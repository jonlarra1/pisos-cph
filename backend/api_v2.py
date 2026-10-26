"""API v2 de pisos-cph: la única puerta a los anuncios (ver docs/arquitectura.md).

    GET  /api/v2/anuncios[?estado=a,b&desde=ts]   lista (sin el texto largo)      público (como el mapa)
    GET  /api/v2/anuncios/<uid>                   un anuncio + su historial        público
    GET  /api/v2/resumen                          contadores por estado            público
    POST /api/v2/enlaces        {url, quien?}     añadir un anuncio por su enlace  web, lares
    POST /api/v2/anuncios       {datos}           entregar lo que encuentra        vigilante
    POST /api/v2/anuncios/<uid>/estado  {estado, quien?, motivo?}                 web, lares
    POST /api/v2/anuncios/<uid>/comprobacion {resultado, motivo?}                 comprobador

Clientes: «web» = la clave pública de la página (API_KEY) + Origin permitido; «lares», «vigilante» y «comprobador» =
claves secretas propias (CLAVE_LARES, CLAVE_VIGILANTE, CLAVE_COMPROBADOR), solo en la Pi. No hay operación de borrar.

app.py llama a `atender(...)` y le pasa lo que necesita en `Contexto` (base de datos, filtros, logs, avisos, límites).
"""
import hmac, os, queue, re, time, urllib.parse
from dataclasses import dataclass
from typing import Callable

import anuncios as A
import portales as P

MAX_CUERPO = {"/api/v2/anuncios": 64_000}               # el vigilante manda el texto del anuncio; lo demás es pequeño
MAX_CUERPO_DEFECTO = 2_000
UID_RE = re.compile(r"^(Lejebolig|Boligportal|EDC):\d{4,10}$")
QUIEN_RE = re.compile(r"^[a-z]{2,12}$")
ESCRITURAS_MIN = {"web": 30, "lares": 60, "vigilante": 600, "comprobador": 600}   # por minuto
LECTOR_PAUSA = 2.0                                        # s entre fichas de la cola (ritmo suave con los portales)
LECTOR_MAX_COLA = 300


@dataclass
class Contexto:
    db: Callable            # context manager -> conexión sqlite3
    filtros: Callable       # () -> dict
    zonas: Callable         # () -> {zona: bool}
    log: Callable           # (tipo, **campos)
    avisar: Callable        # (tipo, data) -> aviso por SSE a las páginas abiertas
    limitado: Callable      # (clave, tope, clave_global, tope_global) -> bool
    origenes: set           # Origin permitidos para la web


CLAVES = {}                 # clave -> cliente; se carga en configurar()
ESTADO = {"lector_ok": 0, "lector_error": 0, "lector_ultimo_error": "", "enlaces_ok": 0, "enlaces_error": 0,
          "entregas": 0, "entregas_nuevas": 0, "prohibido": 0}
_cola = queue.Queue(maxsize=LECTOR_MAX_COLA)
_en_cola = set()


def configurar(env=os.environ):
    """Lee las claves de los clientes. Las que estén vacías no se aceptan."""
    CLAVES.clear()
    for var, cliente in (("API_KEY", "web"), ("CLAVE_LARES", "lares"), ("CLAVE_VIGILANTE", "vigilante"),
                         ("CLAVE_COMPROBADOR", "comprobador")):
        k = env.get(var, "")
        if len(k) >= 8: CLAVES[k] = cliente


def cliente_de(clave, origin, origenes):
    """Cliente según la clave (comparación en tiempo constante). La web además tiene que venir de un Origin permitido;
    las claves de servidor (Lares, scripts) no deben llegar desde un navegador de otra web."""
    for k, c in CLAVES.items():
        if clave and hmac.compare_digest(k.encode(), clave.encode()):
            if c == "web" and origin not in origenes: return None
            if c != "web" and origin and origin not in origenes: return None
            return c
    return None


# ------------------------------------------------------------------ validación
def _int(v, lo=0, hi=10_000_000):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else None


def _txt(v, n):
    return str(v)[:n] if isinstance(v, (str, int)) and str(v).strip() else None


def _bool(v):
    return v if isinstance(v, bool) else None


def limpiar_datos(d):
    """Solo los CAMPOS conocidos, con su tipo y tamaño. Lo que no encaja se descarta (None)."""
    if not isinstance(d, dict): raise A.NoValido("datos no válidos")
    portal, pid = P.identificar(d.get("link", ""))          # el enlace manda: portal e id salen de él
    disp = d.get("disp")
    fotos = [u for u in (d.get("fotos") or [])[:15] if isinstance(u, str) and u.startswith("https://") and len(u) < 400]
    lat, lon = d.get("lat"), d.get("lon")
    dentro = all(isinstance(x, (int, float)) for x in (lat, lon)) and P.BBOX[0] <= lat <= P.BBOX[1] and P.BBOX[2] <= lon <= P.BBOX[3]
    plano = d.get("plano") if isinstance(d.get("plano"), str) and d["plano"].startswith("https://") else None
    return {"portal": portal, "pid": pid, "link": P.enlace_canonico(portal, pid, d["link"]),
            "titulo": _txt(d.get("titulo"), 200), "calle": _txt(d.get("calle"), 120), "cp": _txt(d.get("cp"), 4),
            "ciudad": _txt(d.get("ciudad"), 60), "planta": _txt(d.get("planta"), 30),
            "precio": _int(d.get("precio"), 1, 200_000), "aconto": _int(d.get("aconto"), 0, 20_000),
            "dep": _int(d.get("dep"), 0, 500_000), "pre": _int(d.get("pre"), 0, 500_000), "m2": _int(d.get("m2"), 1, 1000),
            "vaer": _int(d.get("vaer"), 1, 20), "dorm": _int(d.get("dorm"), 0, 20),
            "disp": disp if isinstance(disp, str) and (disp == "ya" or re.fullmatch(r"20\d\d-[01]\d-[0-3]\d", disp)) else None,
            "contrato_meses": _int(d.get("contrato_meses"), 0, 240), "compartible": _bool(d.get("compartible")),
            "amueblado": _bool(d.get("amueblado")), "balcon": _bool(d.get("balcon")), "ascensor": _bool(d.get("ascensor")),
            "mascotas": _bool(d.get("mascotas")), "lat": lat if dentro else None, "lon": lon if dentro else None,
            "fotos": fotos, "plano": plano, "texto": _txt(d.get("texto"), 4000)}


def _con_zona(d):
    d["zona"] = P.zona_de(" ".join(str(d.get(k) or "") for k in ("titulo", "calle", "ciudad")), d.get("cp"))
    return d


def _ubicacion(d):
    """(lat, lon, precisión). Coordenadas del portal = exacta; si no, Nominatim con calle (+ cp y ciudad)."""
    if d.get("lat") is not None: return d["lat"], d["lon"], "exacta"
    calle = re.sub(r",? *(st\.|\d+\.).*$", "", d.get("calle") or "").strip()      # «Grannålen 1, st. 2» -> «Grannålen 1»
    if not calle: return None
    g = P.geocodificar(", ".join(x for x in (calle, d.get("cp"), d.get("ciudad")) if x))
    if not g: return None
    return g[0], g[1], "exacta" if re.search(r"\d", calle) else "calle"


# ------------------------------------------------------------------ cola de fichas (segundo plano)
def encolar_ficha(uid, link):
    if uid in _en_cola: return
    try: _cola.put_nowait((uid, link)); _en_cola.add(uid)
    except queue.Full: pass                                 # se volverá a pedir en la siguiente pasada del vigilante


def lector(ctx):
    """Hilo: lee las fichas pendientes, una cada LECTOR_PAUSA s, y completa el anuncio."""
    while True:
        uid, link = _cola.get()
        try:
            d = _con_zona(P.ficha(link)); d["ficha_leida"] = True
            ubic = _ubicacion(d)
            with ctx.db() as con:
                a = A.obtener(con, uid)
                A.entregar(con, d, a["origen"] if a else "vigilante", "lector", ctx.filtros(), ctx.zonas(), ubic)
            ESTADO["lector_ok"] += 1
        except Exception as e:
            ESTADO["lector_error"] += 1; ESTADO["lector_ultimo_error"] = f"{uid}: {type(e).__name__}: {str(e)[:120]}"
            ctx.log("events", level="warn", src="lector", msg=ESTADO["lector_ultimo_error"])
        finally:
            _en_cola.discard(uid)
        time.sleep(LECTOR_PAUSA)


def cola():
    return _cola.qsize()


# ------------------------------------------------------------------ rutas
def _para_lista(a):
    """En las listas no va el texto largo (pesa) — está en GET /api/v2/anuncios/<uid>."""
    a = dict(a); a["datos"] = {k: v for k, v in a["datos"].items() if k != "texto"}
    return a


def leer(ctx, path):
    """GET. Devuelve (código, objeto)."""
    u = urllib.parse.urlparse(path)
    q = urllib.parse.parse_qs(u.query, max_num_fields=5)
    if u.path == "/api/v2/anuncios":
        estados = [e for e in (q.get("estado", [""])[0].split(",")) if e]
        if any(e not in A.ESTADOS for e in estados): return 400, {"error": "estado no válido"}
        desde = q.get("desde", ["0"])[0]
        if not desde.isdigit(): return 400, {"error": "desde no válido"}
        with ctx.db() as con:
            return 200, {"ahora": int(time.time()), "anuncios": [_para_lista(a) for a in A.listar(con, estados or None, int(desde))]}
    if u.path == "/api/v2/resumen":
        with ctx.db() as con: return 200, {"estados": A.contar(con), "cola_fichas": cola()}
    m = re.fullmatch(r"/api/v2/anuncios/([^/]+)", u.path)
    if m:
        uid = urllib.parse.unquote(m.group(1))
        if not UID_RE.match(uid): return 400, {"error": "uid no válido"}
        with ctx.db() as con:
            a = A.obtener(con, uid)
            return (200, {**a, "historial": A.historial(con, uid)}) if a else (404, {"error": "no encontrado"})
    return 404, {"error": "not found"}


def escribir(ctx, path, cliente, cuerpo):
    """POST de un cliente ya identificado. Devuelve (código, objeto). Las excepciones de anuncios.* se traducen aquí."""
    try:
        if not isinstance(cuerpo, dict): return 400, {"error": "json no válido"}
        if ctx.limitado(f"v2:{cliente}", ESCRITURAS_MIN.get(cliente, 30), "v2:*", 1500):
            return 429, {"error": "demasiadas peticiones"}
        quien = str(cuerpo.get("quien") or cliente).lower()
        if not QUIEN_RE.match(quien) or (cliente == "web" and quien not in ("jon", "unai", "web")):
            return 400, {"error": "quien no válido"}
        if path == "/api/v2/enlaces":
            A.permitir(cliente, "entregar_enlace")
            return _anadir_enlace(ctx, cuerpo.get("url"), cliente, quien)
        if path == "/api/v2/anuncios":
            A.permitir(cliente, "entregar")
            d = _con_zona(limpiar_datos(cuerpo.get("datos")))
            if not d["precio"]: return 400, {"error": "falta el precio"}
            ubic = (d["lat"], d["lon"], "exacta") if d["lat"] is not None else None
            with ctx.db() as con:
                a = A.entregar(con, d, "vigilante", "vigilante", ctx.filtros(), ctx.zonas(), ubic)
            ESTADO["entregas"] += 1
            if a["nuevo"]:                          # solo los nuevos: el vigilante reenvía cientos en cada pasada
                ESTADO["entregas_nuevas"] += 1
                ctx.log("events", level="info", src="vigilante", msg=f"{a['uid']} nuevo -> {a['estado']}")
            if a["estado"] == "propuesto" and not a["datos"].get("ficha_leida"): encolar_ficha(a["uid"], a["datos"]["link"])
            return 200, {"uid": a["uid"], "estado": a["estado"], "motivo": a["motivo"]}
        m = re.fullmatch(r"/api/v2/anuncios/([^/]+)/(estado|comprobacion)", path)
        if not m or not UID_RE.match(urllib.parse.unquote(m.group(1))): return 404, {"error": "not found"}
        uid = urllib.parse.unquote(m.group(1))
        with ctx.db() as con:
            if m.group(2) == "estado":
                a = A.cambiar_estado(con, uid, cuerpo.get("estado"), cliente, quien, _txt(cuerpo.get("motivo"), 300))
            else:
                A.permitir(cliente, "comprobar")
                a = A.comprobar(con, uid, cuerpo.get("resultado"), _txt(cuerpo.get("motivo"), 200) or "")
        ctx.log("events", level="info", src="anuncios", msg=f"{uid} -> {a['estado']}", by=f"{cliente}:{quien}")
        ctx.avisar("anuncios", {"uid": uid, "estado": a["estado"]})
        return 200, _para_lista(a)
    except A.Prohibido as e:                       # clave buena pero acción no permitida: se registra (posible abuso)
        ESTADO["prohibido"] += 1
        ctx.log("events", level="warn", src="permisos", msg=f"{cliente}: {e}", path=path[:120]); return 403, {"error": str(e)}
    except A.Tope as e:
        ctx.log("events", level="warn", src="anuncios", msg=f"tope: {e}"); return 429, {"error": str(e)}
    except (A.NoValido, P.AnuncioNoValido) as e: return 400, {"error": str(e)}


def _anadir_enlace(ctx, url, cliente, quien):
    """Lee la ficha AHORA (1–2 s) para que quien lo añade vea el resultado al momento."""
    portal, pid = P.identificar(url)
    with ctx.db() as con:
        previo = A.obtener(con, f"{portal}:{pid}")
    try:
        d = _con_zona(P.ficha(url))
    except P.AnuncioNoValido:
        ESTADO["enlaces_error"] += 1; raise
    except Exception as e:
        ESTADO["enlaces_error"] += 1
        ctx.log("events", level="warn", src="enlaces", msg=f"{url[:100]}: {type(e).__name__}")
        return 502, {"error": "el portal no responde, prueba en un rato"}
    d["ficha_leida"] = True
    origen = quien if quien in A.ORIGENES else cliente
    with ctx.db() as con:
        a = A.entregar(con, d, origen, f"{cliente}:{quien}", ctx.filtros(), ctx.zonas(), _ubicacion(d))
    ESTADO["enlaces_ok"] += 1
    ctx.log("events", level="info", src="enlaces", msg=f"{a['uid']} {'ya estaba' if previo else 'añadido'} -> {a['estado']}",
            by=f"{cliente}:{quien}")
    ctx.avisar("anuncios", {"uid": a["uid"], "estado": a["estado"]})
    return 200, {**_para_lista(a), "ya_estaba": bool(previo)}
