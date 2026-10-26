"""Puente entre la base única (anuncios.py, API v2) y la web actual, que todavía usa /api/candidatos y /api/estado.

- candidatos(): los anuncios añadidos a mano (chat de Lares, web) salen en «Propuestos» como un candidato más.
- desde_web(): si la web cambia el estado de un piso que está en la base única, se cambia también allí (con historial).
- a_web(): si Lares (o la web por la API v2) cambia un estado, la web lo ve en su tabla de estados.
- al_anadir(): un enlace que ya teníais decidido en la web (p. ej. descartado) no vuelve como «propuesto».

Se quita en la fase 3 completa de docs/arquitectura.md, cuando la web lea directamente de /api/v2.
"""

import time

import anuncios as A

MANUALES = ("lares", "jon", "unai", "web", "import")  # orígenes que no son el vigilante
V2_A_WEB = {"propuesto": "nuevo", "gusta": "gusta", "contactado": "contactado", "descartado": "descartado"}
WEB_A_V2 = {v: k for k, v in V2_A_WEB.items()}
CAMPOS_CANDIDATO = (
    "portal", "link", "titulo", "precio", "aconto", "dep", "pre", "m2", "vaer", "dorm", "disp", "contrato_meses",
    "amueblado", "compartible", "ascensor", "balcon", "mascotas", "plano",
)  # fmt: skip


def candidatos(con, ya, limite=200):
    """Anuncios manuales en el formato de candidatos.json, sin los ids que ya están en `ya` (set, se amplía)."""
    out = []
    for a in A.listar(con, estados=list(V2_A_WEB), limite=limite):
        pid = str(a["pid"])
        if a["origen"] not in MANUALES or pid in ya:
            continue
        d = a["datos"]
        sitio = " ".join(str(x) for x in (d.get("cp"), d.get("ciudad")) if x)
        avisos = list(a["avisos"])
        if a["precision"] == "calle":
            avisos.append("ubicación aproximada (sin número de portal)")
        if d.get("dorm_fuente"):
            avisos.append(f"{d.get('dorm')} dormitorios según {d['dorm_fuente']}")
        avisos += [f"📝 {n['texto']} ({n['quien'].split(':')[-1]})" for n in (d.get("notas") or [])[-5:]]
        out.append(
            {
                "id": pid,
                **{k: d.get(k) for k in CAMPOS_CANDIDATO},
                "lugar": ", ".join(x for x in (d.get("calle"), sitio) if x) or None,
                "zona": a["zona"],
                "lat": a["lat"],
                "lon": a["lon"],
                "fotos": d.get("fotos") or [],
                "avisos": avisos,
                "visto": time.strftime("%Y-%m-%d", time.gmtime(a["creado"])),
                "origen": a["origen"],
            }
        )
        ya.add(pid)
    return out


def desde_web(con, pid, st, by):
    """La web puso `st` (nuevo/gusta/contactado/descartado) al piso `pid`. Devuelve los uid cambiados."""
    nuevo = WEB_A_V2.get(st)
    cambiados = []
    for (uid,) in con.execute("SELECT uid FROM anuncio WHERE pid=?", (str(pid),)).fetchall():
        a = A.cambiar_estado(con, uid, nuevo, "web", by or "web", "desde la web")
        cambiados.append(a["uid"])
    return cambiados


def a_web(a, cliente, quien):
    """(id, st, by) para la tabla de estados de la web, o None si ese estado no existe en la web."""
    st = V2_A_WEB.get(a["estado"])
    if st is None:
        return None
    by = "lares" if cliente == "lares" else (quien if quien in ("jon", "unai") else "")
    return str(a["pid"]), st, by


def al_anadir(con, a, estado_web):
    """Al añadir un enlace: si la web ya tenía una decisión sobre ese piso, la base única la respeta."""
    nuevo = WEB_A_V2.get((estado_web or {}).get("st"))
    if nuevo in A.PERSONA and a["estado"] != nuevo:
        return A.cambiar_estado(con, a["uid"], nuevo, "web", (estado_web.get("by") or "web"), "ya decidido en la web")
    return a


def retirado(con, pid, motivo):
    """El vigilante vio que el portal retiró el piso `pid`: se apunta en la base única (sin pisar decisiones)."""
    uids = [r[0] for r in con.execute("SELECT uid FROM anuncio WHERE pid=?", (str(pid),)).fetchall()]
    return [A.comprobar(con, uid, "retirado", motivo)["uid"] for uid in uids]
