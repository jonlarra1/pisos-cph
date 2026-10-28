"""Migración única a la base única (fase 2 de docs/arquitectura.md, paso B del plan).

Mete en la tabla `anuncio` lo que aún vive fuera de ella, sin perder nada:
- los pisos del mapa (data/pisos.json de la web publicada) → con `datos.mapa = True` y `datos.mapa_id` = su id en el
  mapa (las rutas precalculadas van por ese id);
- los descartados fijos (data/descartados.json) → «descartado» con su motivo;
- los propuestos del vigilante (pisos-bridge/candidatos.json);
- los estados de la web (tabla `estado`) → el estado de cada anuncio, y su historial (tabla `historial`) → `cambio`;
- los estados sin datos en ningún sitio → un anuncio mínimo con su enlace (los datos no se pierden).

Reglas: lo que ya decidisteis en la base única (gusta/contactado/descartado) manda; un propuesto se queda propuesto
aunque hoy no pase un filtro (con el aviso «no pasa: …»), como en anuncios.decidir. Se puede repetir sin duplicar nada.

Uso, dentro del contenedor:  python3 migrar.py            (enseña lo que haría y deshace)
                             python3 migrar.py --aplicar  (lo hace)
Es código de un solo uso: se borra del repositorio cuando esté aplicada (paso D del plan).
"""

import json
import os
import sys
import time

import anuncios as A
import api_v2
import portales as P

DE_WEB = {"nuevo": "propuesto", "gusta": "gusta", "contactado": "contactado", "descartado": "descartado"}
MOTIVO_HISTORIAL = "historial de la web (importado)"


def _num(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _sino(v):
    return {"sí": True, "si": True, "yes": True, "no": False}.get(str(v or "").strip().lower())


def datos_mapa(p):
    """Un piso de data/pisos.json como datos de anuncio."""
    portal, pid = P.identificar(p["link"])
    disp = p.get("disp")
    return {
        "portal": portal,
        "pid": pid,
        "link": P.enlace_canonico(portal, pid, p["link"]),
        "titulo": p.get("calle"),
        "calle": p.get("calle"),
        "planta": p.get("planta") or None,
        "precio": _num(p.get("alq")),
        "aconto": _num(p.get("aconto")),
        "dep": _num(p.get("dep")),
        "pre": _num(p.get("pre")),
        "m2": _num(p.get("m2")),
        "vaer": _num(p.get("vaer")),
        "dorm": None if p.get("dormEst") else _num(p.get("dorm")),  # «Estimado» no es un dato del anuncio
        "disp": "ya" if disp == "Inmediato" else disp,
        "amueblado": _sino(p.get("amueblado")),
        "balcon": _sino(p.get("balcon")),
        "zona": p.get("zona"),
        "lat": p.get("lat"),
        "lon": p.get("lon"),
        "fotos": [f for f in p.get("fotos") or [] if isinstance(f, str)][:15],
        "mapa": True,
        "mapa_id": str(p["id"]),
    }


def datos_descartado(o):
    portal, pid = P.identificar(o["link"])
    t = o.get("t") or ""
    return {
        "portal": portal,
        "pid": pid,
        "link": P.enlace_canonico(portal, pid, o["link"]),
        "titulo": t,
        "zona": P.zona_de(t),
    }


def datos_candidato(c):
    d = api_v2.limpiar_datos(c)
    d["zona"] = c.get("zona") or P.zona_de(" ".join(str(c.get(k) or "") for k in ("titulo", "lugar")))
    return d


def datos_huerfano(pid):
    portal = "Lejebolig" if len(pid) == 7 and pid.startswith("1") else "Boligportal"
    titulo = "(sin datos: solo estaba su estado en la web)"
    return {"portal": portal, "pid": pid, "link": P.enlace_canonico(portal, pid), "titulo": titulo}


def poner_estado(con, a, estado, motivo, quien, F, Z, ahora):
    """Pone el estado que tenía fuera de la base, salvo que la base ya tenga una decisión vuestra."""
    if a["estado"] in A.PERSONA or a["estado"] == estado:
        return False
    if estado == "propuesto":
        estado, motivo, avisos = A.decidir({"estado": "propuesto", "motivo": None}, a["datos"], a["origen"], F, Z)
    else:
        avisos = a["avisos"]
    con.execute(
        "UPDATE anuncio SET estado=?, motivo=?, avisos=?, actualizado=? WHERE uid=?",
        (estado, motivo, json.dumps(avisos, ensure_ascii=False), ahora, a["uid"]),
    )
    A._registrar(con, a["uid"], a["estado"], estado, motivo or "migración a la base única", quien, ahora)
    return True


def importar_historial(con, uid, filas):
    """Filas (st, by, ts) de la tabla `historial` de la web → `cambio` (una vez por anuncio)."""
    if con.execute("SELECT 1 FROM cambio WHERE uid=? AND motivo=?", (uid, MOTIVO_HISTORIAL)).fetchone():
        return 0
    de = None
    for st, by, ts in filas:
        a = DE_WEB.get(st)
        if a:
            con.execute(
                "INSERT INTO cambio (uid, de, a, motivo, quien, ts) VALUES (?,?,?,?,?,?)",
                (uid, de, a, MOTIVO_HISTORIAL, f"web:{by or 'nadie'}", int(ts or 0)),
            )
            de = a
    return len(filas)


def migrar(con, mapa, descartados, candidatos, estados, historial, F, Z, ahora=None):
    """Hace la migración en `con` (quien llama confirma o deshace). Devuelve un resumen."""
    ahora = int(ahora or time.time())
    R = {"mapa": 0, "descartados": 0, "candidatos": 0, "huerfanos": 0, "estados": 0, "historial": 0, "errores": []}
    hechos = {}  # id de la web (mapa_id o pid) -> uid

    def meter(clave, datos, origen, estado, motivo, quien, tipo):
        a = A.entregar(con, datos, origen, "import", F, Z, ahora=ahora)
        if poner_estado(con, a, estado, motivo, quien, F, Z, ahora):
            R["estados"] += 1
        hechos[str(clave)] = a["uid"]
        R[tipo] += 1

    def estado_web(clave, defecto, motivo=None):
        e = estados.get(str(clave)) or {}
        st = DE_WEB.get(e.get("st"))
        return (st, e.get("motivo"), f"web:{e.get('by') or 'nadie'}") if st else (defecto, motivo, "import")

    for p in mapa:
        try:
            meter(p["id"], datos_mapa(p), "import", *estado_web(p["id"], DE_WEB.get(p.get("st"), "propuesto")), "mapa")
        except Exception as e:
            R["errores"].append(f"mapa {p.get('id')}: {e}")
    for o in descartados:
        try:
            d = datos_descartado(o)
            meter(d["pid"], d, "import", *estado_web(d["pid"], "descartado", o.get("r")), "descartados")
        except Exception as e:
            R["errores"].append(f"descartado {o.get('link')}: {e}")
    for c in candidatos:
        try:
            meter(c["id"], datos_candidato(c), "vigilante", *estado_web(c["id"], "propuesto"), "candidatos")
        except Exception as e:
            R["errores"].append(f"candidato {c.get('id')}: {e}")
    for pid in sorted(set(estados) - set(hechos)):
        ya = next((a for a in (A.obtener(con, f"{po}:{pid}") for po in ("Lejebolig", "Boligportal", "EDC")) if a), None)
        try:
            if ya:  # ya estaba en la base única (añadido a mano): solo se cruza el estado
                R["estados"] += poner_estado(con, ya, *estado_web(pid, ya["estado"]), F, Z, ahora)
                hechos[pid] = ya["uid"]
            else:
                meter(pid, datos_huerfano(pid), "import", *estado_web(pid, "descartado"), "huerfanos")
        except Exception as e:
            R["errores"].append(f"estado {pid}: {e}")
    for clave, uid in hechos.items():
        R["historial"] += importar_historial(con, uid, historial.get(clave, []))
    return R


def _leer_entradas(app):
    """(mapa, descartados, candidatos, estados, historial) tal como están ahora en la web publicada y en la Pi."""
    mapa = json.loads(app._get(app.SITE + "data/pisos.json")[1])
    descartados = json.loads(app._get(app.SITE + "data/descartados.json")[1])
    try:
        with open(os.path.join(app.BRIDGE, "candidatos.json"), encoding="utf-8") as f:
            candidatos = json.load(f).get("pisos", [])
    except FileNotFoundError:
        candidatos = []
    historial = {}
    with app.db() as c:
        estados = {
            i: {"st": st, "by": by, "motivo": mot}
            for i, st, by, mot in c.execute("SELECT id, st, by, motivo FROM estado")
        }
        for i, st, by, ts in c.execute("SELECT id, st, by, ts FROM historial ORDER BY ts, n"):
            historial.setdefault(i, []).append((st, by, ts))
    return mapa, descartados, candidatos, estados, historial


def main(aplicar):
    import app  # el backend: misma base, mismos filtros y zonas

    entradas = _leer_entradas(app)
    con = app.sqlite3.connect(app.DB, timeout=10)
    con.row_factory = app.sqlite3.Row
    try:
        with app.lock:
            antes = A.contar(con)
            r = migrar(con, *entradas, app.filtros(), app.zonas_permitidas())
            despues = A.contar(con)
            (con.commit if aplicar else con.rollback)()
    finally:
        con.close()
    print(json.dumps({"aplicado": aplicar, "antes": antes, "despues": despues, **r}, ensure_ascii=False, indent=1))
    if aplicar:
        app.log("events", level="info", src="migracion", msg=json.dumps({**r, "despues": despues}, ensure_ascii=False))


if __name__ == "__main__":
    main("--aplicar" in sys.argv[1:])
