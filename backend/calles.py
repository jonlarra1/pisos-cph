"""Trazados por calle de los tramos a pie y en bici, guardados en la base para siempre (tabla `calle`).

El motor de rutas de la Pi no tiene calles: da cada tramo a pie / en bici como una recta. El trazado real lo da OSRM
(servidor gratuito de FOSSGIS, routing.openstreetmap.de), que a veces se cae durante horas. Por eso:
- cada trazado que se consigue se guarda (también «no hay ruta», solo con el código NoRoute de OSRM), y no se borra;
- solo se piden a OSRM tramos que ha dado el motor (`_CONOCIDOS`): no es un proxy abierto. Tope: MAX_FILAS;
- GET /api/calle lo da desde la base o lo pide a OSRM al momento (como mucho 1 petición por segundo, `PAUSA`; la web
  pasa antes que el hilo de relleno). Tramo desconocido = 403 y la web va directa a OSRM;
- cada ruta del motor apunta sus tramos rectos en una cola (`encolar`) y un hilo (`rellenar`) los pide poco a poco;
  si OSRM no responde, espera `ESPERA_CAIDO` y vuelve a probar (también sin cola, para saber cuándo vuelve).
"""

import collections
import json
import threading
import time
import urllib.error
import urllib.request

URL = "https://routing.openstreetmap.de/routed-{perfil}/route/v1/driving/{a1},{a0};{b1},{b0}?overview=full&geometries=geojson"
AGENTE = "pisos-cph/1.0 (mapa personal de pisos; https://jonlarra1.github.io/pisos-cph/)"
MODOS = ("foot", "bike")
TIMEOUT = 10
MAX_BYTES = 2_000_000  # respuesta de OSRM
MAX_PUNTOS = 5000  # puntos de un trazado
MAX_KM = 40  # tramo más largo que se pide
MAX_FILAS = 50_000  # tope de la tabla (≈ 10 KB por trazado → 500 MB como mucho)
PAUSA = 1.0  # segundos entre peticiones a OSRM (FOSSGIS: uso ligero)
ESPERA_CAIDO = 120  # el hilo de relleno, si OSRM no responde
CAIDO_H = 6  # el check falla si OSRM lleva más de esto sin responder
MAX_COLA = 3000
MAX_CONOCIDOS = 50_000
PRUEBA = ("foot", [55.70700, 12.52300], [55.71050, 12.51800])  # tramo corto para saber si OSRM ha vuelto

ESTADO = {
    "pedidas": 0,
    "ok": 0,
    "sin_ruta": 0,
    "fallos": 0,
    "servidas": 0,
    "de_base": 0,
    "desconocidos": 0,
    "ultimo_ok": 0,
    "caido_desde": 0,
    "ultimo_error": "",
}
_LOCK = threading.Lock()  # una petición a OSRM a la vez
_EST = threading.Lock()  # ESTADO
_ULTIMA = {"t": 0.0}
_ESPERAN = {"n": 0}  # peticiones de la web esperando a OSRM (el hilo de relleno les cede el turno)
_COLA = collections.OrderedDict()  # clave -> (modo, a, b)
_CONOCIDOS = collections.OrderedDict()  # claves de tramos que ha dado el motor
_COLA_LOCK = threading.Lock()
LOG = {"fn": lambda level, msg: None}  # app.py pone aquí su log (src=calles)


def crear_tabla(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS calle (k TEXT PRIMARY KEY, modo TEXT NOT NULL, d INTEGER, path TEXT, ts INTEGER)"
    )


def clave(modo, a, b):
    """La misma que usa la web (liveRoute): coordenadas con 5 decimales."""
    return f"{modo}|{a[0]:.5f},{a[1]:.5f}|{b[0]:.5f},{b[1]:.5f}"


def leer(con, k):
    """{d, path} si está guardado, "sin_ruta" si OSRM dijo que no hay, None si no se ha pedido."""
    r = con.execute("SELECT d, path FROM calle WHERE k=?", (k,)).fetchone()
    if not r:
        return None
    return {"d": r[0], "path": json.loads(r[1])} if r[1] else "sin_ruta"


def guardar(con, k, modo, res):
    """Guarda el trazado (o «no hay ruta»); False si la tabla ya llegó a MAX_FILAS."""
    if con.execute("SELECT COUNT(*) FROM calle").fetchone()[0] >= MAX_FILAS:
        return False
    path = json.dumps(res["path"], separators=(",", ":")) if res else None
    con.execute(
        "INSERT OR REPLACE INTO calle (k, modo, d, path, ts) VALUES (?,?,?,?,?)",
        (k, modo, res["d"] if res else None, path, int(time.time())),
    )
    return True


def _cerca(a, b):
    """Distancia aproximada en km (sin trigonometría fina: basta para poner un tope)."""
    return ((a[0] - b[0]) * 111.2) ** 2 + ((a[1] - b[1]) * 62.8) ** 2 <= MAX_KM**2


def valido(modo, a, b, bbox):
    """Comprueba modo, coordenadas dentro de la zona del motor y distancia; ValueError si no vale."""
    if modo not in MODOS:
        raise ValueError("modo")
    la0, la1, lo0, lo1 = bbox  # como routing.BBOX: (lat mín, lat máx, lon mín, lon máx)
    for p in (a, b):
        if not (la0 <= p[0] <= la1 and lo0 <= p[1] <= lo1):
            raise ValueError("fuera de la zona")
    if not _cerca(a, b):
        raise ValueError("tramo demasiado largo")


def _anotar(**inc):
    with _EST:
        for k, v in inc.items():
            ESTADO[k] += v


def _anotar_espera(n):
    with _EST:
        _ESPERAN["n"] += n


def _anotar_fallo(msg):
    with _EST:
        ESTADO["fallos"] += 1
        ESTADO["ultimo_error"] = msg[:120]
        nuevo = not ESTADO["caido_desde"]
        if nuevo:
            ESTADO["caido_desde"] = time.time()
    if nuevo:
        LOG["fn"]("warn", f"OSRM no responde: {msg[:120]}")


def _anotar_ok():
    with _EST:
        desde, ESTADO["caido_desde"], ESTADO["ultimo_ok"] = ESTADO["caido_desde"], 0, time.time()
    if desde:
        LOG["fn"]("info", f"OSRM responde otra vez (caído {(time.time() - desde) / 60:.0f} min)")


def pedir_osrm(modo, a, b, abrir=urllib.request.urlopen, espera=None):
    """("ok", {d, path}) | ("sin_ruta", None) | ("invalida", None) | ("fallo", motivo). Respeta PAUSA.
    espera: segundos que se aguanta a que acabe otra petición (None = lo que haga falta)."""
    if not _LOCK.acquire(timeout=-1 if espera is None else espera):
        return "fallo", "ocupado"
    try:
        falta = _ULTIMA["t"] + PAUSA - time.time()
        if falta > 0:
            time.sleep(falta)
        _ULTIMA["t"] = time.time()
        _anotar(pedidas=1)
        url = URL.format(perfil=modo, a0=a[0], a1=a[1], b0=b[0], b1=b[1])
        try:
            with abrir(urllib.request.Request(url, headers={"User-Agent": AGENTE}), timeout=TIMEOUT) as r:
                cuerpo, code = r.read(MAX_BYTES + 1), 200
        except urllib.error.HTTPError as e:
            if e.code != 400:  # 400 = la consulta no vale (NoRoute, InvalidQuery…): OSRM sí ha contestado
                _anotar_fallo(f"HTTP {e.code}")
                return "fallo", f"HTTP {e.code}"
            cuerpo, code = e.read(MAX_BYTES + 1), 400
        except (OSError, ValueError) as e:  # red, DNS, timeout, conexión rechazada
            _anotar_fallo(f"{type(e).__name__}: {e}")
            return "fallo", type(e).__name__
    finally:
        _LOCK.release()
    return _leer_respuesta(cuerpo, code)


def _leer_respuesta(cuerpo, code=200):
    try:
        if len(cuerpo) > MAX_BYTES:
            raise ValueError("respuesta demasiado grande")
        j = json.loads(cuerpo)
        cod = j.get("code")
        if cod != "Ok":
            _anotar_ok()
            if cod == "NoRoute":
                _anotar(sin_ruta=1)
                return "sin_ruta", None
            return "invalida", None  # InvalidQuery, TooBig…: no se guarda
        x = j["routes"][0]
        path = [[round(float(la), 5), round(float(lo), 5)] for lo, la in x["geometry"]["coordinates"][:MAX_PUNTOS]]
        if len(path) < 2:
            raise ValueError("trazado vacío")
        d = round(float(x["distance"]))
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
        if code == 400:  # 400 con cuerpo raro: ha contestado, pero no se guarda
            _anotar_ok()
            return "invalida", None
        _anotar_fallo(f"respuesta rara: {e}")
        return "fallo", "respuesta rara"
    _anotar_ok()
    _anotar(ok=1)
    return "ok", {"d": d, "path": path}


def conocer(k):
    with _COLA_LOCK:
        _CONOCIDOS[k] = 1
        _CONOCIDOS.move_to_end(k)
        while len(_CONOCIDOS) > MAX_CONOCIDOS:
            _CONOCIDOS.popitem(last=False)


def trazado(db, modo, a, b, abrir=urllib.request.urlopen):
    """Para GET /api/calle: (200, {d, path}) | (404, …) sin ruta | (403, …) tramo que no ha dado el motor |
    (503, …) OSRM no responde (y queda en la cola)."""
    k = clave(modo, a, b)
    _anotar(servidas=1)
    with db() as c:
        hit = leer(c, k)
    if hit is not None:
        _anotar(de_base=1)
        return (404, {"error": "no hay ruta"}) if hit == "sin_ruta" else (200, hit)
    if k not in _CONOCIDOS:
        _anotar(desconocidos=1)
        return 403, {"error": "tramo desconocido: pídelo a OSRM directamente"}
    if ESTADO["caido_desde"] and time.time() - _ULTIMA["t"] < 30:  # acaba de fallar: no esperar otra vez
        encolar(modo, a, b, primero=True)
        return 503, {"error": "OSRM no responde"}
    _anotar_espera(1)
    try:
        estado, res = pedir_osrm(modo, a, b, abrir, espera=5)
    finally:
        _anotar_espera(-1)
    if estado == "fallo":
        encolar(modo, a, b, primero=True)
        return 503, {"error": "OSRM no responde" if res != "ocupado" else "OSRM ocupado, prueba en unos segundos"}
    if estado == "ok" or estado == "sin_ruta":
        with db() as c:
            guardar(c, k, modo, res)
    return (200, res) if res else (404, {"error": "no hay ruta"})


def encolar(modo, a, b, primero=False):
    k = clave(modo, a, b)
    conocer(k)
    with _COLA_LOCK:
        if k not in _COLA and len(_COLA) < MAX_COLA:
            _COLA[k] = (modo, list(a), list(b))
        if primero and k in _COLA:
            _COLA.move_to_end(k, last=False)


def tramos_rectos(opciones):
    """Tramos a pie / en bici de dos puntos (los del motor) de una respuesta de /api/ruta."""
    for o in opciones or []:
        for leg in o.get("legs", []):
            p = leg.get("path")
            if leg.get("m") in ("walk", "bike") and isinstance(p, list) and len(p) == 2:
                yield ("bike" if leg["m"] == "bike" else "foot"), leg


def con_calles(db, opciones, solo_encolar=False):
    """Copia de las opciones con `calle` (trazado guardado) en cada tramo recto; los que faltan van a la cola.
    solo_encolar (cálculo previo, nadie mira la respuesta): no copia nada, solo apunta lo que falta."""
    if not opciones:
        return opciones
    out = opciones if solo_encolar else [{**o, "legs": [dict(leg) for leg in o.get("legs", [])]} for o in opciones]
    with db() as c:
        for modo, leg in tramos_rectos(out):
            k = clave(modo, *leg["path"])
            conocer(k)
            hit = leer(c, k)
            if isinstance(hit, dict) and not solo_encolar:
                leg["calle"] = hit["path"]
            elif hit is None:
                encolar(modo, *leg["path"])
    return out


def _turno():
    """El hilo de relleno espera mientras haya peticiones de la web esperando a OSRM."""
    for _ in range(100):
        if not _ESPERAN["n"]:
            return
        time.sleep(0.2)


def rellenar(db, abrir=urllib.request.urlopen, parar=None):
    """Hilo: pide a OSRM los tramos de la cola, uno por segundo; si OSRM no responde, espera ESPERA_CAIDO. Sin cola y
    con OSRM caído, prueba un tramo corto (PRUEBA) cada ESPERA_CAIDO para saber cuándo vuelve."""
    while not (parar and parar()):
        with _COLA_LOCK:
            item = _COLA.popitem(last=False) if _COLA else None
        if not item:
            if ESTADO["caido_desde"] and time.time() - _ULTIMA["t"] >= ESPERA_CAIDO:
                pedir_osrm(*PRUEBA, abrir=abrir)
            time.sleep(5)
            continue
        k, (modo, a, b) = item
        with db() as c:
            if leer(c, k) is not None:
                continue
        _turno()
        estado, res = pedir_osrm(modo, a, b, abrir)
        if estado == "fallo":
            with _COLA_LOCK:
                _COLA[k] = (modo, a, b)  # al final de la cola
            time.sleep(ESPERA_CAIDO)
            continue
        if estado in ("ok", "sin_ruta"):
            with db() as c:
                guardar(c, k, modo, res)


def resumen(con):
    """(ok, texto) para el check «calles»."""
    n = dict(con.execute("SELECT path IS NOT NULL, COUNT(*) FROM calle GROUP BY 1").fetchall())
    E = dict(ESTADO)
    caido_h = (time.time() - E["caido_desde"]) / 3600 if E["caido_desde"] else 0
    lleno = sum(n.values()) >= MAX_FILAS
    ok = caido_h <= CAIDO_H and not lleno
    return ok, (
        f"guardados {n.get(1, 0)} trazados (sin ruta {n.get(0, 0)}) · en cola {len(_COLA)}"
        + f" · servidos {E['servidas']} (de la base {E['de_base']}, desconocidos {E['desconocidos']})"
        + f" · a OSRM {E['pedidas']}: ok {E['ok']}, sin ruta {E['sin_ruta']}, fallos {E['fallos']}"
        + (f" · OSRM sin responder desde hace {caido_h:.1f} h ({E['ultimo_error']})" if E["caido_desde"] else "")
        + (f" · tabla llena ({MAX_FILAS} filas): no se guardan más" if lleno else "")
    )
