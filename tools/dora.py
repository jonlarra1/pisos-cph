"""Métricas DORA de pisos-cph (y una foto de la calidad del código).

Fuentes:
  - git: cada commit, su hora y qué pieza toca (web, backend, lares).
  - GitHub Pages (gh api): cada publicación de la web, con su commit y si falló.
  - docs/despliegues.jsonl: instalaciones en la Pi (las escribe backend/deploy.sh; Lares con --registrar).

Métricas, por pieza:
  Ritmo        frecuencia de despliegue · tiempo de cambio a producción (lead time) · tiempo de recuperación
  Estabilidad  tasa de fallos (vuelta atrás, build fallido o arreglo urgente en 48 h) · tasa de retrabajo
               (despliegues que solo traen fix/revert)

Uso:
  python3 tools/dora.py                     informe de los últimos 30 días
  python3 tools/dora.py --dias 7
  python3 tools/dora.py --guardar           además añade la foto a docs/metricas.jsonl (para ver la evolución)
  python3 tools/dora.py --registrar lares ok "nota"    apunta un despliegue hecho a mano
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import statistics
import subprocess

RAIZ = pathlib.Path(__file__).resolve().parent.parent
REGISTRO = RAIZ / "docs" / "despliegues.jsonl"
HISTORICO = RAIZ / "docs" / "metricas.jsonl"
REPO_GH = "jonlarra1/pisos-cph"

PIEZAS = {
    "web": ("index.html", "app.js", "data/", "favicon", "apple-touch-icon"),
    "backend": ("backend/",),
    "lares": ("lares/",),
}
VENTANA_FALLO = dt.timedelta(hours=48)       # un arreglo antes de esto cuenta como fallo del despliegue anterior


# ------------------------------------------------------------------ utilidades
def ahora():
    return dt.datetime.now(dt.timezone.utc)


def fecha(texto):
    return dt.datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def git(*args):
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True, check=True).stdout


def horas(delta):
    return delta.total_seconds() / 3600


def texto_horas(h):
    if h is None:
        return "—"
    if h < 1:
        return f"{h * 60:.0f} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} días"


def es_arreglo(asunto):
    return asunto.startswith(("fix", "revert", "Revert"))


# ------------------------------------------------------------------ datos
def leer_commits():
    """Lista de commits (antiguo → nuevo): hash, hora, asunto y piezas que toca."""
    salida = git("log", "--reverse", "--format=@@%H|%cI|%s", "--name-only")
    commits = []
    for bloque in salida.split("@@")[1:]:
        cabecera, *ficheros = bloque.strip().split("\n")
        sha, hora, asunto = cabecera.split("|", 2)
        piezas = {p for p, prefijos in PIEZAS.items() for f in ficheros if f.startswith(prefijos)}
        commits.append({"sha": sha, "ts": fecha(hora), "asunto": asunto, "piezas": piezas})
    return commits


def despliegues_web(commits):
    """Publicaciones de GitHub Pages que cambian la web. Sin gh o sin red → lista vacía."""
    try:
        salida = subprocess.run(["gh", "api", "--paginate", f"repos/{REPO_GH}/pages/builds?per_page=100"],
                                capture_output=True, text=True, check=True, timeout=60).stdout
        builds = json.loads(salida.replace("][", ","))
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    hora_de = {c["sha"]: c["ts"] for c in commits}
    resultado = []
    for b in sorted(builds, key=lambda b: b["created_at"]):
        if b["commit"] not in hora_de:
            continue                                   # commit que ya no está en la historia local
        resultado.append({"ts": fecha(b["updated_at"]), "pieza": "web", "commit": b["commit"],
                          "resultado": "ok" if b["status"] == "built" else "fallo"})
    return resultado


def despliegues_pi():
    if not REGISTRO.exists():
        return []
    filas = [json.loads(linea) for linea in REGISTRO.read_text(encoding="utf-8").splitlines() if linea.strip()]
    return [{**f, "ts": fecha(f["ts"])} for f in filas]


def repartir(commits, despliegues, pieza):
    """A cada despliegue de la pieza le asigna los commits de esa pieza que llegaron con él.
    Web: por posición en git (lo que hay hasta su commit). Pi: por hora (hechos antes de instalar)."""
    propios = [c for c in commits if pieza in c["piezas"]]
    deps = sorted((d for d in despliegues if d["pieza"] == pieza), key=lambda d: d["ts"])
    if pieza == "web":
        orden = {c["sha"]: i for i, c in enumerate(commits)}
        limite = lambda d: lambda c: orden[c["sha"]] <= orden[d["commit"]]      # noqa: E731
    else:
        limite = lambda d: lambda c: c["ts"] <= d["ts"]                          # noqa: E731
    pendientes = list(propios)
    for d in deps:
        if d["resultado"] != "ok":
            d["commits"] = []
            continue
        cabe = limite(d)
        d["commits"] = [c for c in pendientes if cabe(c)]
        pendientes = [c for c in pendientes if not cabe(c)]
    deps = [d for d in deps if d["commits"] or d["resultado"] != "ok"]           # publicaciones sin cambios de la pieza: fuera
    return deps, pendientes


# ------------------------------------------------------------------ métricas
def medir(pieza, deps, pendientes, desde, hasta):
    deps = [d for d in deps if desde <= d["ts"] <= hasta]
    ok = [d for d in deps if d["resultado"] == "ok"]
    dias = max((hasta - desde).days, 1)

    lead = [horas(d["ts"] - c["ts"]) for d in ok for c in d["commits"]]

    retrabajo = [d for d in ok if all(es_arreglo(c["asunto"]) for c in d["commits"])]
    fallidos, recuperacion = [], []
    for i, d in enumerate(deps):
        siguiente = next((x for x in deps[i + 1:] if x["resultado"] == "ok"), None)
        arreglado_pronto = siguiente in retrabajo and siguiente["ts"] - d["ts"] <= VENTANA_FALLO
        if d["resultado"] != "ok" or arreglado_pronto:
            fallidos.append(d)
            if siguiente:
                recuperacion.append(horas(siguiente["ts"] - d["ts"]))

    return {
        "pieza": pieza,
        "despliegues": len(ok),
        "por_dia": round(len(ok) / dias, 2),
        "lead_mediana_h": round(statistics.median(lead), 2) if lead else None,
        "lead_p90_h": round(statistics.quantiles(lead, n=10)[-1], 2) if len(lead) >= 2 else None,
        "tasa_fallos": round(len(fallidos) / len(deps), 3) if deps else None,
        "tasa_retrabajo": round(len(retrabajo) / len(ok), 3) if ok else None,
        "recuperacion_mediana_h": round(statistics.median(recuperacion), 2) if recuperacion else None,
        "sin_desplegar": len(pendientes),
        "sin_desplegar_mas_viejo_h": round(horas(hasta - pendientes[0]["ts"]), 1) if pendientes else None,
    }


def nivel(m):
    """Nivel DORA aproximado (umbrales del informe State of DevOps 2023)."""
    notas = []
    if m["por_dia"] >= 1:
        notas.append("frecuencia: élite")
    elif m["por_dia"] >= 1 / 7:
        notas.append("frecuencia: alto")
    else:
        notas.append("frecuencia: medio/bajo")
    lead = m["lead_mediana_h"]
    if lead is not None:
        notas.append("lead: élite" if lead < 24 else "lead: alto" if lead < 168 else "lead: medio/bajo")
    if m["tasa_fallos"] is not None:
        t = m["tasa_fallos"]
        notas.append("fallos: élite" if t <= 0.05 else "fallos: alto" if t <= 0.10 else "fallos: medio" if t <= 0.15 else "fallos: bajo")
    return ", ".join(notas)


# ------------------------------------------------------------------ calidad (deterministas)
def calidad():
    """Tamaño, duplicado y, si están en .venv, complejidad (radon) y seguridad (bandit)."""
    codigo = sorted([*RAIZ.glob("backend/*.py"), *RAIZ.glob("lares/*.py")])
    lineas = {f: [x.strip() for x in f.read_text(encoding="utf-8").splitlines()] for f in codigo}
    total = sum(1 for ls in lineas.values() for x in ls if x and not x.startswith("#"))

    # código duplicado: bloques de 6 líneas con contenido que aparecen más de una vez
    ventanas = collections.Counter()
    for ls in lineas.values():
        utiles = [x for x in ls if x and not x.startswith("#")]
        for i in range(len(utiles) - 5):
            ventanas[hashlib.sha1("\n".join(utiles[i:i + 6]).encode(), usedforsecurity=False).digest()] += 1
    repetidas = sum(n for n in ventanas.values() if n > 1)
    foto = {"lineas_python": total, "duplicado_pct": round(100 * repetidas / max(sum(ventanas.values()), 1), 1)}

    venv = RAIZ / ".venv" / "bin"
    if (venv / "radon").exists():
        cc = json.loads(subprocess.run([str(venv / "radon"), "cc", "-j", "backend", "lares"],
                                       cwd=RAIZ, capture_output=True, text=True, check=False).stdout)
        valores = [b["complexity"] for bloques in cc.values() for b in bloques]
        foto["complejidad_media"] = round(statistics.mean(valores), 1)
        foto["funciones_complejidad_mayor_20"] = sum(v > 20 for v in valores)
    if (venv / "bandit").exists():
        b = json.loads(subprocess.run([str(venv / "bandit"), "-r", "backend", "lares", "-q", "-f", "json"],
                                      cwd=RAIZ, capture_output=True, text=True, check=False).stdout)
        foto["bandit"] = dict(collections.Counter(x["issue_severity"].lower() for x in b["results"]))
    if (venv / "ruff").exists():
        r = subprocess.run([str(venv / "ruff"), "check", "--select", "E,W,F,B", "--statistics", "--output-format", "concise",
                            "backend", "lares"], cwd=RAIZ, capture_output=True, text=True, check=False).stdout
        foto["avisos_estilo"] = sum(int(linea.split()[0]) for linea in r.splitlines() if linea[:1].isdigit())
    return foto


# ------------------------------------------------------------------ salida
def informe(dias):
    hasta = ahora()
    desde = hasta - dt.timedelta(days=dias)
    commits = leer_commits()
    todos = despliegues_web(commits) + despliegues_pi()
    filas = []
    for pieza in PIEZAS:
        deps, pendientes = repartir(commits, todos, pieza)
        filas.append(medir(pieza, deps, pendientes, desde, hasta))
    return {"ts": hasta.isoformat(timespec="seconds"), "dias": dias, "piezas": filas, "calidad": calidad(),
            "web_sin_datos": not any(d["pieza"] == "web" for d in todos)}


def imprimir(r):
    print(f"Métricas DORA · últimos {r['dias']} días · {r['ts']}")
    if r["web_sin_datos"]:
        print("  (sin datos de GitHub Pages: ¿gh sin sesión o sin red?)")
    for m in r["piezas"]:
        print(f"\n[{m['pieza']}]  {nivel(m)}")
        print(f"  despliegues            {m['despliegues']}  ({m['por_dia']} al día)")
        print(f"  cambio → producción    mediana {texto_horas(m['lead_mediana_h'])}, p90 {texto_horas(m['lead_p90_h'])}")
        tf, tr = m["tasa_fallos"], m["tasa_retrabajo"]
        print(f"  tasa de fallos         {'—' if tf is None else f'{tf:.0%}'}")
        print(f"  tasa de retrabajo      {'—' if tr is None else f'{tr:.0%}'}")
        print(f"  recuperación           {texto_horas(m['recuperacion_mediana_h'])}")
        print(f"  sin desplegar          {m['sin_desplegar']} commits (el más viejo hace {texto_horas(m['sin_desplegar_mas_viejo_h'])})")
    print("\n[calidad del código: backend + lares]")
    for k, v in r["calidad"].items():
        print(f"  {k:30} {v}")


def registrar(pieza, resultado, nota):
    if pieza not in PIEZAS or resultado not in ("ok", "vuelta_atras", "fallo"):
        raise SystemExit("uso: --registrar {web|backend|lares} {ok|vuelta_atras|fallo} [nota]")
    fila = {"ts": ahora().isoformat(timespec="seconds").replace("+00:00", "Z"), "pieza": pieza,
            "commit": git("rev-parse", "HEAD").strip(), "resultado": resultado, "nota": nota}
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fila, ensure_ascii=False) + "\n")
    print("registrado:", fila)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--guardar", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--registrar", nargs="+", metavar="ARG")
    a = p.parse_args()
    if a.registrar:
        registrar(a.registrar[0], a.registrar[1] if len(a.registrar) > 1 else "ok", " ".join(a.registrar[2:]))
        return
    r = informe(a.dias)
    print(json.dumps(r, ensure_ascii=False, indent=2)) if a.json else imprimir(r)
    if a.guardar:
        with HISTORICO.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
