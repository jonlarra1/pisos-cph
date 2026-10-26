"""Destinos compartidos (web: Configuración → Destinos): casa de amigos, gimnasio, clase…

Antes vivían en el navegador de cada uno; ahora están en la base de la Pi y Jon y Unai ven los mismos,
con rutas para los dos perfiles. Nada se borra: «quitar» lo oculta y queda apuntado quién y cuándo.
"""

import re
import time

MAX = 30  # destinos visibles a la vez
CAJA = (55.35, 56.05, 11.85, 12.9)  # lat mín/máx, lon mín/máx: el mismo recuadro que el mapa (maxBounds)
ID_RE = re.compile(r"^x[a-z0-9]{4,16}$")  # la web los crea como "x" + fecha en base 36
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f<>]")
CERCA = 0.0005  # ≈ 50 m: mismo nombre y casi el mismo sitio = el mismo destino
ESTADO = {"anadidos": 0, "quitados": 0, "repetidos": 0, "rechazados": 0}


class Lleno(Exception):
    """Ya hay MAX destinos visibles."""


def crear_tabla(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS destino (id TEXT PRIMARY KEY, n TEXT NOT NULL, addr TEXT, lat REAL NOT NULL, "
        "lon REAL NOT NULL, by TEXT, ts INTEGER NOT NULL, oculto INTEGER NOT NULL DEFAULT 0, oculto_by TEXT, "
        "oculto_ts INTEGER)"
    )


def listar(c):
    filas = c.execute("SELECT id, n, addr, lat, lon, by FROM destino WHERE oculto=0 ORDER BY ts, id")
    return [{"id": i, "n": n, "addr": a or "", "ll": [lat, lon], "by": by or ""} for i, n, a, lat, lon, by in filas]


def _texto(v, maximo, minimo=0):
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())
    if not minimo <= len(v) <= maximo or CONTROL_RE.search(v):
        return None
    return v


def _coordenadas(ll):
    if not isinstance(ll, list) or len(ll) != 2:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in ll):
        return None
    lat, lon = float(ll[0]), float(ll[1])
    if not (CAJA[0] <= lat <= CAJA[1] and CAJA[2] <= lon <= CAJA[3]):
        return None
    return [round(lat, 6), round(lon, 6)]


def validar(d):
    """(acción, datos) de una petición de la web, o None si no vale."""
    accion, did = d.get("accion"), d.get("id")
    if not isinstance(did, str) or not ID_RE.match(did):
        return None
    if accion == "quitar":
        return "quitar", {"id": did}
    if accion != "anadir":
        return None
    n, addr, ll = _texto(d.get("n"), 30, 1), _texto(d.get("addr", ""), 90), _coordenadas(d.get("ll"))
    if n is None or addr is None or ll is None:
        return None
    return "anadir", {"id": did, "n": n, "addr": addr, "ll": ll}


def _repetido(c, x):
    for (i,) in c.execute(
        "SELECT id FROM destino WHERE oculto=0 AND lower(n)=lower(?) AND abs(lat-?)<? AND abs(lon-?)<?",
        (x["n"], x["ll"][0], CERCA, x["ll"][1], CERCA),
    ):
        return i
    return None


def aplicar(c, accion, x, by, ahora=None):
    """Añade u oculta un destino. Devuelve (cambiado, lista visible). Repetir la misma petición no cambia nada."""
    ahora = int(ahora or time.time())
    if accion == "quitar":
        n = c.execute(
            "UPDATE destino SET oculto=1, oculto_by=?, oculto_ts=? WHERE id=? AND oculto=0", (by, ahora, x["id"])
        ).rowcount
        ESTADO["quitados"] += n
        return bool(n), listar(c)
    existe = c.execute("SELECT 1 FROM destino WHERE id=?", (x["id"],)).fetchone()
    if existe or _repetido(c, x):  # ya estaba (o alguien lo quitó: no se resucita al subir copias viejas)
        ESTADO["repetidos"] += 1
        return False, listar(c)
    if c.execute("SELECT count(*) FROM destino WHERE oculto=0").fetchone()[0] >= MAX:
        raise Lleno
    c.execute(
        "INSERT INTO destino (id, n, addr, lat, lon, by, ts) VALUES (?,?,?,?,?,?,?)",
        (x["id"], x["n"], x["addr"], x["ll"][0], x["ll"][1], by, ahora),
    )
    ESTADO["anadidos"] += 1
    return True, listar(c)


def resumen(c):
    """Para el check del monitor: (ok, texto)."""
    vis = c.execute("SELECT count(*) FROM destino WHERE oculto=0").fetchone()[0]
    ocu = c.execute("SELECT count(*) FROM destino WHERE oculto=1").fetchone()[0]
    E = ESTADO
    return vis <= MAX, (
        f"{vis} destinos compartidos ({ocu} quitados) · desde el arranque: añadidos {E['anadidos']}, quitados "
        f"{E['quitados']}, repetidos {E['repetidos']}, peticiones rechazadas {E['rechazados']}"
    )
