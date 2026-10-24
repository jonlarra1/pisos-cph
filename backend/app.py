"""Pisos CPH backend: shared flat status (gusta / nuevo / contactado / descartado).
Python stdlib only. SQLite at /data/pisos.db."""
import base64, contextlib, json, os, re, sqlite3, threading, time, tempfile, ssl, socket, http.client, urllib.request, urllib.parse, collections, hashlib, gzip, glob, shutil, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("DB_PATH", "/data/pisos.db")
ORIGINS = set(os.environ.get("ALLOWED_ORIGINS", "https://jonlarra1.github.io,http://localhost:8000").split(","))
KEY = os.environ.get("API_KEY", "")
ESTADOS = {"gusta", "nuevo", "contactado", "descartado"}
WHO = {"", "jon", "unai"}
ID_RE = re.compile(r"^[0-9]{4,12}$")
BRIDGE = os.environ.get("BRIDGE_DIR", "/bridge")      # carpeta compartida con Lares (vault/Vivienda/pisos-bridge)
# Filtros de Jon (rev. 2026-10-21). Lares los lee de config.json.
FILTROS = {"precio_max": 23000, "dorm_min": 3, "vaer_min": 4, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15",
           "compartir": True, "contrato_largo": True, "amueblado": "igual"}
ZONAS_DEFAULT = {  # zona: permitida
    "Hellerup": 1, "Gentofte": 1, "Charlottenlund": 1, "Klampenborg": 1, "Lyngby": 1, "Virum": 1, "Søborg": 1, "Buddinge": 1,
    "Gladsaxe": 1, "Bagsværd": 1, "Herlev": 1, "Husum": 1, "Brønshøj": 1, "Vanløse": 1, "Rødovre": 1, "Ballerup": 1,
    "Skovlunde": 1, "Måløv": 1, "Glostrup": 1, "Hvidovre": 1, "Frederiksberg": 1, "Nørrebro": 1, "Nordvest": 1,
    "Bispebjerg": 1, "Vesterbro": 1, "Sydhavnen": 1, "Indre By": 1,
    "Østerbro": 0, "Amager": 0, "Islands Brygge": 0, "Ørestad": 0, "Holmen": 0, "Valby": 0}
ZONA_RE = re.compile(r"^[A-Za-zÆØÅæøåÉé .\-]{2,30}$")
lock = threading.Lock()
hits = {}
STARTED = time.time()
LOGDIR = os.environ.get("LOG_DIR", "/data/logs")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "raspberrypi.tail9f1069.ts.net"); PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "8443"))
SITE = os.environ.get("SITE_URL", "https://jonlarra1.github.io/pisos-cph/")
LARES_CRON = os.environ.get("LARES_CRON", "/lares_cron/jobs.json")
ADMIN_USERS = [u for u in os.environ.get("ADMIN_USERS", "").split(",") if u]
SITE_DATA = {}                               # PISOS / OUT de la web publicada (lo rellena check_site)
CHECKS = {}                                  # último resultado de cada comprobación
CHECK_HIST = collections.defaultdict(lambda: collections.deque(maxlen=10))   # 10 últimas de cada una
IMG_DIR = os.path.join(os.path.dirname(DB) or "/data", "imgcache")
IMG_HOSTS = {"image-lambda.boligportal.dk": "https://www.boligportal.dk/", "s3.eu-central-1.amazonaws.com": None,
             "billeder.edc.dk": "https://www.edc.dk/"}
IMG_MAX_MB, IMG_CACHE_MB = 8, 300
IMG_STATS = {"hit": 0, "fetch": 0, "error": 0}
os.makedirs(IMG_DIR, exist_ok=True)
RECENT = collections.deque(maxlen=300)       # últimas peticiones (también en api.jsonl)
os.makedirs(LOGDIR, exist_ok=True)
_loglock = threading.Lock()

# ------------------------------------------------------------------ logs
# logs/<tipo>/AAAA-MM-DD.jsonl (hoy) y .jsonl.gz (días pasados, comprimidos por housekeeping()).
#   api, client  -> detalle de peticiones / errores de la página: 14 días
#   events       -> caídas, recuperaciones, cambios de estado/filtros/zonas, accesos denegados: 183 días
#   checks       -> SOLO cuando una comprobación cambia (OK <-> fallo): 183 días
#   summary      -> una línea por día con el resumen: 183 días
# Las 10 últimas comprobaciones de cada tipo van en memoria y en logs/checks_recientes.json.
RETENTION = {"api": 14, "client": 14, "events": 183, "checks": 183, "summary": 183}
DAY_CAP = 20_000_000                          # tope por tipo y día (bytes)
_capped = set()

def _today(): return time.strftime("%Y-%m-%d")

def _txt(path):
    """Lee un fichero de texto entero y lo cierra."""
    with open(path, encoding="utf-8") as f: return f.read()

def _put(path, txt):
    with open(path, "w", encoding="utf-8") as f: f.write(txt)

def log(kind, **kw):
    """Una línea JSON por evento en logs/<kind>/<hoy>.jsonl."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "t": round(time.time(), 3), **kw}
    d = os.path.join(LOGDIR, kind); path = os.path.join(d, _today() + ".jsonl")
    with _loglock:
        try:
            os.makedirs(d, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > DAY_CAP:
                if (kind, _today()) not in _capped:
                    _capped.add((kind, _today()))
                    with open(os.path.join(LOGDIR, "events", _today() + ".jsonl"), "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": rec["ts"], "t": rec["t"], "level": "warn", "src": "logs",
                                            "msg": f"tope diario de {DAY_CAP//1_000_000} MB alcanzado en «{kind}»: se deja de guardar el detalle hasta mañana"}) + "\n")
            else:
                with open(path, "a", encoding="utf-8") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            print("log error", e, flush=True)
    if kind == "api": RECENT.append(rec)
    return rec

def _read(path, maxbytes=400_000):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f: data = f.read()
    else:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size-maxbytes)); data = f.read().decode("utf-8", "replace")
            if size > maxbytes: data = data.split("\n", 1)[-1]
    return [x for x in data.splitlines() if x.strip()]

def tail(kind, n=50):
    """Últimas n líneas del tipo, mirando hoy y, si hace falta, días anteriores."""
    out = []
    for path in sorted(glob.glob(os.path.join(LOGDIR, kind, "*.jsonl*")), key=os.path.getmtime, reverse=True)[:3]:   # el más reciente primero
        try:
            out = [json.loads(x) for x in _read(path)] + out
        except Exception:
            continue
        if len(out) >= n: break
    return out[-n:]

def migrate_old_logs():
    """Una vez: api.jsonl, checks.jsonl… del formato anterior pasan a logs/<tipo>/legacy-<fecha>.jsonl.gz."""
    for f in glob.glob(os.path.join(LOGDIR, "*.jsonl")) + glob.glob(os.path.join(LOGDIR, "*.jsonl.1")):
        kind = os.path.basename(f).split(".")[0]
        d = os.path.join(LOGDIR, kind); os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, f"legacy-{time.strftime('%Y%m%d')}-{os.path.basename(f).replace('.', '_')}.jsonl.gz")
        with open(f, "rb") as src, gzip.open(dst, "wb") as out: shutil.copyfileobj(src, out)
        os.remove(f)

def daily_summary(day):
    """Resumen de un día (a partir del detalle de api/client y de los eventos)."""
    def lines(kind):
        for ext in (".jsonl", ".jsonl.gz"):
            p = os.path.join(LOGDIR, kind, day + ext)
            if os.path.exists(p):
                try: return [json.loads(x) for x in _read(p, 50_000_000)]
                except Exception: return []
        return []
    api, cli, ev = lines("api"), lines("client"), lines("events")
    by = collections.Counter(r.get("path", "?") for r in api)
    ms = [r.get("ms", 0) for r in api]
    lares = 0
    try:
        for x in _txt(os.path.join(BRIDGE, "lares_runs.jsonl")).splitlines():
            if x.strip() and json.loads(x).get("ts", "").startswith(day): lares += 1
    except Exception: pass
    return {"dia": day, "peticiones": len(api), "por_ruta": dict(by.most_common(10)),
            "errores_4xx": sum(1 for r in api if 400 <= r.get("code", 0) < 500), "errores_5xx": sum(1 for r in api if r.get("code", 0) >= 500),
            "ms_medio": round(sum(ms)/len(ms)) if ms else 0, "errores_pagina": len(cli),
            "caidas": sum(1 for e in ev if str(e.get("msg", "")).startswith("CAÍDO")), "cambios_estado": sum(1 for e in ev if e.get("src") == "estado"),
            "pasadas_lares": lares}

def housekeeping():
    """Comprime los días pasados, escribe su resumen y borra lo que supera la retención."""
    today = _today(); now = time.time()
    for kind, days in RETENTION.items():
        d = os.path.join(LOGDIR, kind)
        if not os.path.isdir(d): continue
        for f in glob.glob(os.path.join(d, "*.jsonl")):
            name = os.path.basename(f)[:-6]
            if name >= today or name.startswith("legacy"): continue
            if kind == "api":                       # antes de comprimir el detalle de ayer, guardar su resumen
                sp = os.path.join(LOGDIR, "summary"); os.makedirs(sp, exist_ok=True)
                if not glob.glob(os.path.join(sp, name + ".jsonl*")):
                    with open(os.path.join(sp, name + ".jsonl"), "w", encoding="utf-8") as o: o.write(json.dumps(daily_summary(name), ensure_ascii=False) + "\n")
            with open(f, "rb") as src, gzip.open(f + ".gz", "wb") as out: shutil.copyfileobj(src, out)
            os.remove(f)
        for f in glob.glob(os.path.join(d, "*.jsonl.gz")):
            if now - os.path.getmtime(f) > days * 86400: os.remove(f)

def housekeeper():
    while True:
        try: housekeeping()
        except Exception as e: log("events", level="error", src="logs", msg=f"housekeeping: {type(e).__name__}: {e}")
        time.sleep(3600)

@contextlib.contextmanager
def db():
    """Conexión a SQLite: confirma (o deshace si hay error) y SIEMPRE la cierra al salir del `with`."""
    c = sqlite3.connect(DB, timeout=10)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        with c: yield c
    finally:
        c.close()

def init():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS estado (id TEXT PRIMARY KEY, st TEXT NOT NULL, stF TEXT NOT NULL, by TEXT, ts INTEGER NOT NULL)")
        if "motivo" not in {r[1] for r in c.execute("PRAGMA table_info(estado)")}:     # migración: por qué (p. ej. anuncio retirado)
            c.execute("ALTER TABLE estado ADD COLUMN motivo TEXT")
        c.execute("CREATE TABLE IF NOT EXISTS retirado_aplicado (id TEXT PRIMARY KEY, desde TEXT, ts INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS historial (n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, st TEXT, stF TEXT, by TEXT, ts INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS zonas (zona TEXT PRIMARY KEY, permitida INTEGER NOT NULL, by TEXT, ts INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS ajustes (k TEXT PRIMARY KEY, v TEXT NOT NULL, by TEXT, ts INTEGER)")
        for z, v in ZONAS_DEFAULT.items():
            c.execute("INSERT OR IGNORE INTO zonas (zona, permitida, by, ts) VALUES (?,?,?,?)", (z, v, "", 0))
    write_config()

def all_estados():
    with db() as c:
        return {r[0]: {"st": r[1], "stF": r[2], "by": r[3] or "", "ts": r[4], **({"motivo": r[5]} if r[5] else {})}
                for r in c.execute("SELECT id, st, stF, by, ts, motivo FROM estado")}

def filtros():
    """Filtros de búsqueda de Lares (editables desde la web). Por defecto FILTROS."""
    with db() as c:
        r = c.execute("SELECT v FROM ajustes WHERE k='filtros'").fetchone()
    f = dict(FILTROS)
    if r:
        try: f.update(json.loads(r[0]))
        except Exception: pass
    return f

def validar_filtros(d):
    f = {}
    try:
        f["precio_max"] = int(d["precio_max"]); assert 8000 <= f["precio_max"] <= 40000
        f["dorm_min"] = int(d["dorm_min"]); assert 1 <= f["dorm_min"] <= 5
        f["vaer_min"] = f["dorm_min"] + 1
        for k in ("disp_desde", "disp_hasta"):
            v = str(d[k]); assert re.match(r"^20\d\d-\d\d-\d\d$", v); f[k] = v
        assert f["disp_desde"] <= f["disp_hasta"]
        f["compartir"] = bool(d["compartir"]); f["contrato_largo"] = bool(d["contrato_largo"])
        f["amueblado"] = str(d.get("amueblado", "igual")); assert f["amueblado"] in ("igual", "si", "no")   # opcional (webs viejas)
    except Exception:
        return None
    return f

def all_zonas():
    with db() as c:
        return {r[0]: bool(r[1]) for r in c.execute("SELECT zona, permitida FROM zonas ORDER BY zona")}

def write_config():
    """config.json para Lares: filtros, zonas y pisos que ya tienen estado (no volver a proponerlos)."""
    if not os.path.isdir(BRIDGE):
        log("events", level="error", src="bridge", msg="carpeta compartida no montada"); return
    cfg = {"actualizado": time.strftime("%Y-%m-%dT%H:%M:%S"), "filtros": filtros(), "zonas": all_zonas(),
           "con_estado": all_estados()}
    fd, tmp = tempfile.mkstemp(dir=BRIDGE, prefix=".config", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644); os.replace(tmp, os.path.join(BRIDGE, "config.json"))

def candidatos():
    """Propuestos por Lares con su estado (la web separa: sin decidir / gustan / descartados)."""
    try:
        with open(os.path.join(BRIDGE, "candidatos.json"), encoding="utf-8") as f: c = json.load(f)
    except Exception:
        return {"actualizado": None, "pisos": []}
    est = all_estados(); pisos = c.get("pisos", []) if isinstance(c, dict) else []
    out = []
    for p in pisos[:300]:
        if not isinstance(p, dict): continue
        e = est.get(str(p.get("id")), {})
        out.append({**p, "st": e.get("st", "nuevo"), "stF": e.get("stF"), "by": e.get("by", ""), "ts": e.get("ts"), **({"motivo": e["motivo"]} if e.get("motivo") else {})})
    return {"actualizado": c.get("actualizado") if isinstance(c, dict) else None, "pisos": out}

class _RedirSoloPortales(urllib.request.HTTPRedirectHandler):
    """El proxy de fotos solo sigue redirecciones a los mismos hosts permitidos (nada de saltar a otra web o a la red local)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        pu = urllib.parse.urlparse(newurl)
        if pu.scheme != "https" or pu.hostname not in IMG_HOSTS or pu.username or pu.port not in (None, 443):
            raise ValueError(f"redirección a host no permitido ({pu.hostname})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
_IMG_OPENER = urllib.request.build_opener(_RedirSoloPortales)

def img_proxy(u):
    """Foto de un anuncio desde los portales (con su Referer), cacheada en disco. Devuelve (bytes, content-type)."""
    pu = urllib.parse.urlparse(u)
    if pu.scheme != "https" or pu.hostname not in IMG_HOSTS: raise ValueError("host no permitido")
    raw = urllib.parse.unquote(pu.path)
    if ".." in raw or "\\" in raw or pu.username or pu.password or pu.port not in (None, 443): raise ValueError("ruta no permitida")
    if pu.hostname.startswith("s3.") and not raw.startswith("/lejeboligdata/images/"): raise ValueError("ruta no permitida")
    if pu.hostname == "billeder.edc.dk" and not raw.startswith("/edcmedia/"): raise ValueError("ruta no permitida")
    key = hashlib.sha1(u.encode()).hexdigest(); path = os.path.join(IMG_DIR, key)
    if os.path.exists(path):
        IMG_STATS["hit"] += 1
        with open(path, "rb") as f: data = f.read()
        ctype = _txt(path + ".t") if os.path.exists(path + ".t") else "image/jpeg"
        return data, ctype, "cache"
    h = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) Chrome/131.0", "Accept": "image/avif,image/webp,image/*"}
    if IMG_HOSTS[pu.hostname]: h["Referer"] = IMG_HOSTS[pu.hostname]
    with _IMG_OPENER.open(urllib.request.Request(u, headers=h), timeout=20) as r:
        ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image/"): raise ValueError(f"no es imagen ({ctype})")
        data = r.read(IMG_MAX_MB * 1024 * 1024 + 1)
    if len(data) > IMG_MAX_MB * 1024 * 1024: raise ValueError("imagen demasiado grande")
    with open(path + ".tmp", "wb") as f: f.write(data)
    os.replace(path + ".tmp", path); _put(path + ".t", ctype)
    IMG_STATS["fetch"] += 1
    # limpiar caché si pasa del límite (borra las más antiguas)
    files = [os.path.join(IMG_DIR, x) for x in os.listdir(IMG_DIR) if not x.endswith((".t", ".tmp"))]
    total = sum(os.path.getsize(x) for x in files)
    if total > IMG_CACHE_MB * 1024 * 1024:
        for x in sorted(files, key=os.path.getmtime)[: len(files)//4]:
            for y in (x, x + ".t"):
                try: os.remove(y)
                except OSError: pass
        log("events", level="info", src="img", msg=f"caché de fotos limpiada ({total//1048576} MB)")
    return data, ctype, "portal:" + ("boligportal" if "boligportal" in pu.hostname else "edc" if "edc" in pu.hostname else "lejebolig")

_rl_logged = {}
def limited(key, cap=30, glob_key=None, glob_cap=None):
    """Ventana de 60 s. `key` = límite por IP; `glob_key` = límite global (todas las IPs juntas)."""
    now = time.time()
    with lock:
        if len(hits) > 5000:                                   # que la tabla no crezca sin fin con muchas IPs
            for k in [k for k, v in hits.items() if not v or now - v[-1] > 60]: hits.pop(k, None)
            if len(hits) > 5000: hits.clear()
        for k, c in ((key, cap), (glob_key, glob_cap)):
            if not k: continue
            q = [t for t in hits.get(k, []) if now-t < 60]
            if len(q) >= c:
                hits[k] = q
                if now - _rl_logged.get(k, 0) > 60:          # un aviso por minuto y límite, no uno por petición
                    _rl_logged[k] = now
                    threading.Thread(target=log, args=("events",), kwargs={"level": "warn", "src": "limite",
                                     "msg": f"límite de peticiones alcanzado: {k} ({c}/min)"}, daemon=True).start()
                return True
        for k in (key, glob_key):
            if k: hits.setdefault(k, []).append(now); hits[k] = [t for t in hits[k] if now-t < 60]
    return False

# ------------------------------------------------------------------ tiempo real (Server-Sent Events)
# La web abre GET /api/eventos y la Pi le avisa al momento de cada cambio (en vez de preguntar cada pocos segundos):
#   event: estados    data: {todos los estados}          (alguien cambió un estado)
#   event: candidatos data: {"actualizado": ...}          (Lares escribió candidatos.json)
#   event: ajustes    data: {}                            (zonas o filtros de búsqueda)
# Cada hilo de conexión cuenta en pids_limit del contenedor (100): como mucho SSE_MAX en total y SSE_IP por IP.
SSE_MAX, SSE_IP, SSE_PING, SSE_VIDA = 20, 4, 25, 1800
SSE = {"n": 0, "por_ip": collections.Counter(), "total": 0, "rechazadas": 0}
_cambio = threading.Condition()
_ultimo = {"n": 0, "tipo": "", "data": "{}"}       # último aviso: cada conexión compara el número para no perderse ninguno

def avisar(tipo, data):
    with _cambio:
        _ultimo.update(n=_ultimo["n"] + 1, tipo=tipo, data=json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        _cambio.notify_all()

def aplicar_retirados():
    """retirados.json (lo escribe Lares): cada anuncio retirado del portal pasa UNA vez a «descartado» por Lares,
    con el motivo (la web lo enseña 48 h en rojo y luego en Descartados). Si alguien lo recupera, no se vuelve a descartar."""
    try:
        with open(os.path.join(BRIDGE, "retirados.json"), encoding="utf-8") as f: d = json.load(f)
        pisos = d.get("pisos") or {}
    except Exception:
        return 0
    n, ts = 0, int(time.time())
    with lock, db() as c:
        hechos = {r[0] for r in c.execute("SELECT id FROM retirado_aplicado")}
        for pid, r in pisos.items():
            pid = str(pid)
            if pid in hechos or not ID_RE.match(pid) or not isinstance(r, dict): continue
            desde = str(r.get("desde", ""))[:10]
            motivo = f"Anuncio retirado del portal ({desde}): {str(r.get('motivo', ''))[:120]}"
            c.execute("INSERT INTO retirado_aplicado (id, desde, ts) VALUES (?,?,?)", (pid, desde, ts))
            cur = c.execute("SELECT st FROM estado WHERE id=?", (pid,)).fetchone()
            if cur and cur[0] == "descartado": continue                   # ya lo habíais descartado vosotros
            c.execute("INSERT INTO estado (id, st, stF, by, ts, motivo) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                      "st=excluded.st, stF=excluded.stF, by=excluded.by, ts=excluded.ts, motivo=excluded.motivo",
                      (pid, "descartado", time.strftime("%Y-%m-%d"), "lares", ts, motivo))
            c.execute("INSERT INTO historial (id, st, stF, by, ts) VALUES (?,?,?,?,?)", (pid, "descartado", time.strftime("%Y-%m-%d"), "lares", ts))
            log("events", level="warn", src="lares", msg=f"{pid}: {motivo} -> descartado (48 h en el mapa)")
            n += 1
    if n:
        write_config(); avisar("estados", all_estados()); avisar("candidatos", {"retirados": n})
    return n

def vigilar_candidatos(cada=10):
    """Avisa cuando Lares reescribe candidatos.json, y aplica retirados.json (anuncios que ya no están en el portal)."""
    mt = lambda n: os.path.getmtime(os.path.join(BRIDGE, n)) if os.path.exists(os.path.join(BRIDGE, n)) else 0
    antes, antes_r = mt("candidatos.json"), -1
    while True:
        try:
            if mt("retirados.json") != antes_r:
                antes_r = mt("retirados.json"); aplicar_retirados()
            m = mt("candidatos.json")
            if m != antes:
                antes = m; c = candidatos()
                avisar("candidatos", {"actualizado": c.get("actualizado")})
                log("events", level="info", src="lares", msg=f"candidatos actualizados: {len(c['pisos'])} pisos")
        except Exception as e:
            log("events", level="warn", src="lares", msg=f"vigilar candidatos: {type(e).__name__}: {e}"[:200])
        time.sleep(cada)

def etag(b): return '"' + hashlib.sha1(b).hexdigest()[:20] + '"'

class H(BaseHTTPRequestHandler):
    server_version = "pisos/1"
    def client_ip(self):
        """IP real del cliente. Por Funnel/serve, tailscaled AÑADE la IP al final de X-Forwarded-For,
        así que solo vale el último valor (los anteriores los puede inventar quien hace la petición)."""
        xff = self.headers.get("X-Forwarded-For", "")
        return xff.split(",")[-1].strip() if xff else self.client_address[0]
    def log_message(self, fmt, *a): pass
    def handle_one_request(self):
        self._t0 = time.time(); self._code = None; self._note = ""
        try:
            super().handle_one_request()
        except Exception as e:
            log("events", level="error", src="api", msg=f"{type(e).__name__}: {e}")
        if getattr(self, "command", None) and self._code is not None and self.path != "/api/health":
            log("api", m=self.command, path=self.path.split("?")[0], code=self._code, ms=round((time.time()-self._t0)*1000), note=self._note,
                origin=self.headers.get("Origin", "") if self.headers else "", via="funnel" if self.headers and self.headers.get("Tailscale-Funnel-Request") else "")
    def send_response(self, code, message=None):
        self._code = code; super().send_response(code, message)
    def cors(self):
        o = self.headers.get("Origin", "")
        if o in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", o); self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Pisos-Key")
    def reply(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        tag = etag(b) if code == 200 and self.command == "GET" else None
        if tag and self.headers.get("If-None-Match") == tag:      # la web ya lo tiene: «sin cambios», sin cuerpo
            self.send_response(304); self.cors(); self.send_header("ETag", tag); self.send_header("Cache-Control", "no-cache")
            self.end_headers(); self._note = (self._note + " 304").strip(); return
        self.send_response(code); self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
        if tag: self.send_header("ETag", tag)
        self.send_header("Cache-Control", "no-cache" if tag else "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers(); self.wfile.write(b)
    def eventos(self):
        """GET /api/eventos: conexión abierta (SSE) por la que llegan los cambios al momento."""
        ip = self.client_ip()
        with lock:
            if SSE["n"] >= SSE_MAX or SSE["por_ip"][ip] >= SSE_IP:
                SSE["rechazadas"] += 1; lleno = True
            else:
                SSE["n"] += 1; SSE["por_ip"][ip] += 1; SSE["total"] += 1; lleno = False
        if lleno: return self.reply(503, {"error": "demasiadas conexiones en tiempo real"})
        t0 = time.time()
        try:
            self.send_response(200); self.cors()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8"); self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no"); self.send_header("Connection", "close"); self.end_headers()
            self.close_connection = True
            with _cambio: visto = _ultimo["n"]
            self.wfile.write(b"retry: 5000\n: conectado\n\n"); self.wfile.flush()
            while time.time() - t0 < SSE_VIDA:
                with _cambio:
                    _cambio.wait_for(lambda: _ultimo["n"] != visto, timeout=SSE_PING)
                    nuevo = dict(_ultimo) if _ultimo["n"] != visto else None
                if nuevo:
                    visto = nuevo["n"]; msg = f"event: {nuevo['tipo']}\ndata: {nuevo['data']}\n\n"
                else:
                    msg = ": ping\n\n"                      # mantiene viva la conexión (y detecta si el cliente se fue)
                self.wfile.write(msg.encode()); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass                                            # el navegador cerró la pestaña: normal
        finally:
            with lock:
                SSE["n"] -= 1; SSE["por_ip"][ip] -= 1
                if SSE["por_ip"][ip] <= 0: del SSE["por_ip"][ip]
            self._note = f"sse {round(time.time()-t0)} s"
    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        if self.path == "/api/health": return self.reply(200, {"ok": True})
        if limited("get:" + self.client_ip(), 300, "get:*", 2000): return self.reply(429, {"error": "too many requests"})
        if self.path.startswith("/api/img?"):
            ip = self.client_ip()
            if limited("img:"+ip, 120, "img:*", 600): return self.reply(429, {"error": "too many requests"})
            u = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("u", [""])[0]
            try:
                data, ctype, self._note = img_proxy(u)
            except ValueError as e:      # petición no válida (host/ruta no permitidos, no es imagen)
                log("events", level="warn", src="img", msg=f"rechazada: {str(e)[:120]} · {u[:90]}")
                return self.reply(400, {"error": str(e)[:80]})
            except Exception as e:       # el portal no responde
                IMG_STATS["error"] += 1; log("events", level="warn", src="img", msg=f"{type(e).__name__}: {str(e)[:120]} · {u[:90]}")
                return self.reply(502, {"error": "imagen no disponible"})
            self.send_response(200); self.cors(); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers()
            self.wfile.write(data); return
        if self.path == "/api/eventos": return self.eventos()
        if self.path == "/api/estados": return self.reply(200, all_estados())
        if self.path == "/api/zonas": return self.reply(200, all_zonas())
        if self.path == "/api/filtros": return self.reply(200, filtros())
        if self.path == "/api/candidatos": return self.reply(200, candidatos())
        if self.path.startswith("/api/ruta?"):
            if limited("ruta:" + self.client_ip(), 60, "ruta:*", 200): return self.reply(429, {"error": "demasiadas búsquedas, espera un minuto"})
            code, obj = ruta(urllib.parse.urlparse(self.path).query)
            self._note = f"{obj.get('ms','')} ms" + (" caché" if obj.get("cache") else "") if code == 200 else str(obj.get("error", ""))[:60]
            return self.reply(code, obj)
        self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path == "/api/clientlog":    # errores de la página de los usuarios
            n = int(self.headers.get("Content-Length") or 0)
            ip = self.client_ip()
            o = self.headers.get("Origin", "")
            if o and o not in ORIGINS: return self.reply(403, {"error": "origin"})
            if n <= 0 or n > 2048: return self.reply(400, {"error": "bad size"})
            if limited("log:"+ip): return self.reply(429, {"error": "too many requests"})
            try: d = json.loads(self.rfile.read(n))
            except Exception: return self.reply(400, {"error": "bad json"})
            log("client", level=str(d.get("level", "error"))[:10], msg=str(d.get("msg", ""))[:500], where=str(d.get("where", ""))[:200])
            return self.reply(200, {"ok": True})
        if self.path not in ("/api/estado", "/api/zona", "/api/filtros"): return self.reply(404, {"error": "not found"})
        if KEY and self.headers.get("X-Pisos-Key") != KEY: return self.reply(403, {"error": "forbidden"})
        o = self.headers.get("Origin", "")
        if o and o not in ORIGINS: return self.reply(403, {"error": "origin"})
        ip = self.client_ip()
        if limited("w:"+ip, 30, "w:*", 120): return self.reply(429, {"error": "too many requests"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 1024: return self.reply(413, {"error": "bad size"})
        try: d = json.loads(self.rfile.read(n))
        except Exception: return self.reply(400, {"error": "bad json"})
        if not isinstance(d, dict): return self.reply(400, {"error": "bad json"})
        by = str(d.get("by", "")).lower()
        if by not in WHO: return self.reply(400, {"error": "bad data"})
        if self.path == "/api/filtros":
            f = validar_filtros(d)
            if f is None: return self.reply(400, {"error": "bad data"})
            with lock, db() as c:
                c.execute("INSERT INTO ajustes (k, v, by, ts) VALUES ('filtros',?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, by=excluded.by, ts=excluded.ts",
                          (json.dumps(f), by, int(time.time())))
            write_config(); log("events", level="info", src="filtros", msg=json.dumps(f, ensure_ascii=False), by=by)
            avisar("ajustes", {}); return self.reply(200, filtros())
        if self.path == "/api/zona":
            z, ok = str(d.get("zona", "")).strip(), d.get("permitida")
            if not ZONA_RE.match(z) or not isinstance(ok, bool): return self.reply(400, {"error": "bad data"})
            with lock, db() as c:
                c.execute("INSERT INTO zonas (zona, permitida, by, ts) VALUES (?,?,?,?) ON CONFLICT(zona) DO UPDATE SET permitida=excluded.permitida, by=excluded.by, ts=excluded.ts", (z, int(ok), by, int(time.time())))
            write_config(); log("events", level="info", src="zonas", msg=f"{z} -> {'permitida' if ok else 'excluida'}", by=by)
            avisar("ajustes", {}); return self.reply(200, all_zonas())
        pid, st = str(d.get("id", "")), d.get("st")
        stF = time.strftime("%Y-%m-%d")
        if not ID_RE.match(pid) or st not in ESTADOS or by not in WHO: return self.reply(400, {"error": "bad data"})
        ts = int(time.time())
        with lock, db() as c:
            c.execute("INSERT INTO estado (id, st, stF, by, ts, motivo) VALUES (?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET st=excluded.st, stF=excluded.stF, by=excluded.by, ts=excluded.ts, motivo=NULL", (pid, st, stF, by, ts))
            c.execute("INSERT INTO historial (id, st, stF, by, ts) VALUES (?,?,?,?,?)", (pid, st, stF, by, ts))
        write_config(); log("events", level="info", src="estado", msg=f"{pid} -> {st}", by=by)
        est = all_estados(); avisar("estados", est); self.reply(200, est)

# ------------------------------------------------------------------ comprobaciones
# ------------------------------------------------------------------ motor de rutas (horarios GTFS de Rejseplanen)
GTFS_DIR = os.environ.get("GTFS_DIR", os.path.join(os.path.dirname(DB) or "/data", "gtfs"))
GTFS_URL = os.environ.get("GTFS_URL", "https://www.rejseplanen.info/labs/GTFS.zip")
GTFS_EVERY = 7 * 86400                       # descarga semanal
MOTOR = {"m": None, "error": "", "cargado": 0, "ultima_ms": None, "busquedas": 0}
RUTA_CACHE = collections.OrderedDict()       # resultados recientes (10 min)
RUTA_LOCK = threading.Semaphore(1)           # una búsqueda a la vez (CPU de la Pi)
COMPACTO = os.path.join(GTFS_DIR, "compacto.pkl")

def gtfs_refresh(force=False):
    """Descarga el GTFS si tiene más de 7 días y prepara el archivo compacto en un proceso aparte
    (así el pico de memoria de la preparación no se suma al del servidor). Luego recarga el motor."""
    os.makedirs(GTFS_DIR, exist_ok=True)
    zp = os.path.join(GTFS_DIR, "GTFS.zip")
    viejo = not os.path.exists(COMPACTO) or time.time() - os.path.getmtime(COMPACTO) > GTFS_EVERY
    if force or viejo:
        t0 = time.time()
        try:
            req = urllib.request.Request(GTFS_URL, headers={"User-Agent": "pisos-cph/1.0 (personal flat-hunting map)"})
            with urllib.request.urlopen(req, timeout=120) as r, open(zp + ".tmp", "wb") as f:
                n = 0
                while True:
                    b = r.read(1 << 20)
                    if not b: break
                    n += len(b)
                    if n > 300_000_000: raise ValueError("GTFS demasiado grande")
                    f.write(b)
            os.replace(zp + ".tmp", zp)
            import subprocess, sys
            out = subprocess.run([sys.executable, "-I", "-c",
                                  "import sys,json; sys.path.insert(0, sys.argv[1]); import routing; print(json.dumps(routing.preparar(sys.argv[2], sys.argv[3])))",
                                  os.path.dirname(os.path.abspath(__file__)), zp, COMPACTO], capture_output=True, text=True, timeout=900)
            if out.returncode: raise RuntimeError(out.stderr.strip()[-300:])
            os.remove(zp)                           # el zip ya no hace falta (56 MB)
            log("events", level="info", src="rutas", msg=f"horarios actualizados en {time.time()-t0:.0f} s: {out.stdout.strip()[:200]}")
        except Exception as e:
            MOTOR["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            log("events", level="error", src="rutas", msg="no se pudo actualizar el GTFS: " + MOTOR["error"])
            try: os.remove(zp + ".tmp")
            except OSError: pass
    if os.path.exists(COMPACTO) and (MOTOR["m"] is None or os.path.getmtime(COMPACTO) > MOTOR["cargado"]):
        import routing
        MOTOR["m"] = None                           # suelta el anterior antes de cargar el nuevo (memoria)
        MOTOR["m"] = routing.Motor(COMPACTO); MOTOR["cargado"] = os.path.getmtime(COMPACTO); MOTOR["error"] = ""
        RUTA_CACHE.clear()

def gtfs_worker():
    while True:
        try: gtfs_refresh()
        except Exception as e: log("events", level="error", src="rutas", msg=f"{type(e).__name__}: {e}"[:300])
        time.sleep(6 * 3600)

def _f(q, k, lo, hi):
    v = float(q[k][0])
    if not (lo <= v <= hi) or v != v: raise ValueError(k)
    return v

def ruta(qs):
    """GET /api/ruta?olat&olon&dlat&dlon&dname&fecha=YYYY-MM-DD&hora=HH:MM&modo=llegar|salir&perfil=foot|bike&lim&vw&vb"""
    import routing
    q = urllib.parse.parse_qs(qs, max_num_fields=20)
    M = MOTOR["m"]
    if M is None: return 503, {"error": "motor de rutas no disponible", "detalle": MOTOR["error"]}
    B = routing.BBOX
    try:
        olat, olon = _f(q, "olat", B[0], B[1]), _f(q, "olon", B[2], B[3])
        dlat, dlon = _f(q, "dlat", B[0], B[1]), _f(q, "dlon", B[2], B[3])
        fecha = q["fecha"][0]; hora = q["hora"][0]; modo = q.get("modo", ["llegar"])[0]; perfil = q.get("perfil", ["foot"])[0]
        if not re.fullmatch(r"20\d\d-[01]\d-[0-3]\d", fecha) or not re.fullmatch(r"[0-2]\d:[0-5]\d", hora) or int(hora[:2]) > 23: raise ValueError("fecha/hora")
        datetime.date.fromisoformat(fecha)
        if modo not in ("llegar", "salir") or perfil not in ("foot", "bike"): raise ValueError("modo/perfil")
        lim = _f(q, "lim", 3, 30) if "lim" in q else 10
        vw = _f(q, "vw", 3, 7) if "vw" in q else 5; vb = _f(q, "vb", 8, 35) if "vb" in q else 20
        dname = re.sub(r"[^\w .,·\-/]", "", q.get("dname", ["Destino"])[0], flags=re.U)[:60] or "Destino"
    except (KeyError, ValueError, IndexError) as e:
        return 400, {"error": f"parámetros no válidos ({e})"}
    ymd = fecha.replace("-", "")
    if not (M.desde <= ymd <= M.hasta): return 400, {"error": f"fuera del periodo de horarios ({M.desde}–{M.hasta})"}
    key = (round(olat, 4), round(olon, 4), round(dlat, 4), round(dlon, 4), fecha, hora, modo, perfil, lim, vw, vb)
    hit = RUTA_CACHE.get(key)
    if hit and time.time() - hit[0] < 600: return 200, {**hit[1], "cache": True}
    if not RUTA_LOCK.acquire(timeout=10): return 503, {"error": "motor ocupado, prueba en unos segundos"}
    try:
        t0 = time.time()
        pf = {"mode": perfil, "v_walk": vw/3.6, "v_bike": vb/3.6, "lim_min": lim}
        opts = M.ruta(olat, olon, (dlat, dlon), dname, fecha, hora, modo, pf)
        ms = round((time.time()-t0)*1000)
    finally:
        RUTA_LOCK.release()
    MOTOR["ultima_ms"] = ms; MOTOR["busquedas"] += 1
    res = {"opciones": opts, "ms": ms, "horarios": {"creado": M.creado, "hasta": M.hasta}, "fuente": "GTFS Rejseplanen (horario, sin retrasos)"}
    RUTA_CACHE[key] = (time.time(), res)
    while len(RUTA_CACHE) > 300: RUTA_CACHE.popitem(last=False)
    return 200, res

def check_rutas():
    M = MOTOR["m"]
    if M is None: return False, "motor sin cargar" + (f" · {MOTOR['error']}" if MOTOR["error"] else "")
    edad = (time.time() - MOTOR["cargado"]) / 86400
    hoy = time.strftime("%Y%m%d"); ok = edad < 10 and M.desde <= hoy <= M.hasta and not MOTOR["error"]
    return ok, (f"horarios de hace {edad:.1f} días (válidos hasta {M.hasta}) · {MOTOR['busquedas']} búsquedas desde el arranque"
                + (f" · última {MOTOR['ultima_ms']} ms" if MOTOR["ultima_ms"] is not None else "") + (f" · {MOTOR['error']}" if MOTOR["error"] else ""))

def listas():
    """Contadores por lista para el panel + anuncios nuevos por día (historial.json de Lares)."""
    est = all_estados(); n = collections.Counter()
    for p in SITE_DATA.get("pisos", []):
        n["mapa_" + (est.get(str(p.get("id")), {}).get("st") or p.get("st") or "nuevo")] += 1
    for c in candidatos().get("pisos", []):
        n["lares_" + (c.get("st") or "nuevo")] += 1
    try:
        with open(os.path.join(BRIDGE, "historial.json"), encoding="utf-8") as f: h = json.load(f)
    except Exception:
        h = {}
    dias = h.get("por_dia") or {}
    hoy = time.strftime("%Y-%m-%d")
    serie = [{"dia": d, **v} for d, v in sorted(dias.items()) if d][-30:]
    completos = [x["vistos"] for x in serie if x["dia"] != hoy][-14:]
    return {"gusta": n["mapa_gusta"] + n["lares_gusta"], "contactado": n["mapa_contactado"] + n["lares_contactado"],
            "propuestos": n["mapa_nuevo"] + n["lares_nuevo"], "descartados": n["mapa_descartado"] + n["lares_descartado"],
            "descartados_fijos": len(SITE_DATA.get("out", [])), "rechazados_lares": len(h.get("rechazados") or []),
            "en_mapa": len(SITE_DATA.get("pisos", [])), "historial": bool(h), "por_dia": serie,
            "media_diaria": round(sum(completos) / len(completos), 1) if completos else None, "dias_media": len(completos)}

def chk(name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
        res = {"ok": bool(ok), "detail": detail}
    except Exception as e:
        res = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    res.update({"name": name, "ms": round((time.time()-t0)*1000), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    prev = CHECKS.get(name); CHECKS[name] = res
    res["t"] = round(time.time(), 3); CHECK_HIST[name].append(res)
    try:   # 10 últimas de cada comprobación, para no perderlas si se reinicia el contenedor
        tmp = os.path.join(LOGDIR, ".checks_recientes.tmp")
        with open(tmp, "w", encoding="utf-8") as f: json.dump({k: list(v) for k, v in CHECK_HIST.items()}, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(LOGDIR, "checks_recientes.json"))
    except Exception as e: print("checks_recientes", e, flush=True)
    if prev is None or prev["ok"] != res["ok"]:
        log("checks", **res)
        log("events", level="info" if res["ok"] else "error", src=name, msg=("RECUPERADO: " if res["ok"] else "CAÍDO: ") + str(res["detail"])[:300])

def check_funnel():
    """Como un usuario de fuera: IP pública del Funnel por DNS público y GET /api/health."""
    q = urllib.request.Request(f"https://cloudflare-dns.com/dns-query?name={PUBLIC_HOST}&type=A", headers={"accept": "application/dns-json"})
    ips = [a["data"] for a in json.load(urllib.request.urlopen(q, timeout=10)).get("Answer", []) if a.get("type") == 1]
    if not ips: return False, "DNS público sin IPs para el Funnel"
    ctx = ssl.create_default_context()
    with socket.create_connection((ips[0], PUBLIC_PORT), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=PUBLIC_HOST) as ss:
            ss.sendall(f"GET /api/health HTTP/1.1\r\nHost: {PUBLIC_HOST}\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while True:
                chunk = ss.recv(4096)
                if not chunk: break
                data += chunk
    head = data.split(b"\r\n", 1)[0].decode(errors="replace")
    return (" 200 " in head and b'"ok": true' in data), f"{head} vía {ips[0]}"

_SITE_CACHE = {"hash": None, "sin_rutas": [], "sri_t": 0, "sri_mal": []}

def _get(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pisos-monitor"}), timeout=timeout) as r:
        return r.status, r.read()

def check_site():
    """La web publicada: responde, trae los datos, cada piso tiene rutas, tiene CSP y los scripts del CDN
    siguen coincidiendo con su huella SRI (si no, el navegador los bloquea y el mapa no carga)."""
    st, raw = _get(SITE + f"?chk={int(time.time())}"); body = raw.decode("utf-8", "replace")
    js = body
    if re.search(r'<script[^>]*\ssrc="app\.js"', body):    # JavaScript en su propio fichero (la etiqueta, no el texto)
        st2, raw2 = _get(SITE + f"app.js?chk={int(time.time())}"); js = raw2.decode("utf-8", "replace")
        if st2 != 200: return False, f"app.js: HTTP {st2}"
    problemas = []
    for k, name in (("pisos", "PISOS"), ("out", "OUT")):      # listas del mapa para el contador del panel
        m = re.search(r"const %s = (\[.*?\]);\n" % name, js, re.S)
        if m:
            try: SITE_DATA[k] = json.loads(m.group(1))
            except ValueError: problemas.append(f"{name} no es JSON válido")
        else: problemas.append(f"falta {name}")
    h = hashlib.sha1(js.encode()).hexdigest()
    if h != _SITE_CACHE["hash"]:                              # solo se analizan las rutas cuando cambia la web
        _SITE_CACHE["hash"] = h; _SITE_CACHE["sin_rutas"] = []
        m = re.search(r"^const RUTAS ?= ?(.*?);[ \t]*$", js, re.M)
        if not m: problemas.append("falta RUTAS")
        else:
            try:
                v = m.group(1); p = re.fullmatch(r"JSON\.parse\('(.*)'\)", v, re.S)     # formato rápido (tools/sync_web.py)
                if p: v = re.sub(r"\\(.)", lambda x: x.group(1), p.group(1))
                ix = json.loads(v)["ix"]
                con = {fid for mo in ix.values() for d in mo.values() for t in d.values() for l in t.values() for fid in l}
                _SITE_CACHE["sin_rutas"] = [str(p.get("id")) for p in SITE_DATA.get("pisos", []) if str(p.get("id")) not in con]
            except (ValueError, KeyError, AttributeError) as e: problemas.append(f"RUTAS ilegible ({type(e).__name__})")
    if _SITE_CACHE["sin_rutas"]: problemas.append("pisos sin rutas: " + ", ".join(_SITE_CACHE["sin_rutas"][:5]))
    if 'http-equiv="Content-Security-Policy"' not in body: problemas.append("sin CSP")
    if time.time() - _SITE_CACHE["sri_t"] > 3600:             # scripts del CDN: como mucho una vez por hora
        _SITE_CACHE["sri_t"] = time.time(); mal = []
        for src, integ in re.findall(r'<script src="(https://[^"]+)" integrity="(sha384-[^"]+)"', body):
            try:
                _, b = _get(src)
                if "sha384-" + base64.b64encode(hashlib.sha384(b).digest()).decode() != integ: mal.append(src.rsplit("/", 1)[-1])
            except Exception as e: mal.append(f"{src.rsplit('/', 1)[-1]} ({type(e).__name__})")
        _SITE_CACHE["sri_mal"] = mal
    if _SITE_CACHE["sri_mal"]: problemas.append("el CDN cambió (SRI no coincide, el mapa no cargaría): " + ", ".join(_SITE_CACHE["sri_mal"]))
    ok = st == 200 and not problemas
    det = f"HTTP {st}, {len(raw)//1024} KB, {len(SITE_DATA.get('pisos', []))} pisos" + (" · " + " · ".join(problemas) if problemas else " · todos con rutas, CSP y SRI bien")
    return ok, det

def check_lares_runs():
    path = os.path.join(BRIDGE, "lares_runs.jsonl")
    try:
        last = json.loads(_txt(path).strip().splitlines()[-1])
    except FileNotFoundError:
        return False, "Lares aún no ha registrado ninguna pasada (lares_runs.jsonl no existe)"
    age = (time.time() - last.get("t", 0)) / 3600
    errs = last.get("errores") or []
    ok = age < 3.7 and not errs and last.get("ok", True)
    return ok, f"última pasada hace {age:.1f} h · {last.get('leidos', {})} anuncios · {last.get('candidatos', 0)} candidatos" + (f" · errores: {errs}" if errs else "")

def check_lares_cron():
    """Estado de las tareas de pisos de Lares, que Lares copia al buzón en cada pasada (lares_cron.json)."""
    d = json.loads(_txt(os.path.join(BRIDGE, "lares_cron.json")))
    jobs = d.get("jobs", [])
    out, ok = [], True
    for j in jobs:
        if not str(j.get("name", "")).startswith("piso"): continue
        st, derr = j.get("last_status"), j.get("last_delivery_error")
        bad = (st not in ("ok", None)) or not j.get("enabled", True)
        ok = ok and not bad
        out.append(f"{j.get('name')}: {st} (última {str(j.get('last_run_at'))[:16]}, próxima {str(j.get('next_run_at'))[:16]})" +
                   (f" — entrega: {str(derr)[:160]}" if derr and bad else ""))
    return ok, " | ".join(out) or "sin tareas de pisos"

def check_bridge():
    res = []
    ok = True
    for fn, max_h in (("config.json", None), ("candidatos.json", 3.7)):
        p = os.path.join(BRIDGE, fn)
        if not os.path.exists(p): ok = False; res.append(f"{fn}: no existe"); continue
        age = (time.time() - os.path.getmtime(p)) / 3600
        if max_h and age > max_h: ok = False
        res.append(f"{fn}: hace {age:.1f} h")
    return ok, " · ".join(res)

def check_api_errors():
    recent = [r for r in tail("api", 300) if time.time() - r.get("t", 0) < 3600]
    # un 502 de /api/img es el portal negando una foto, no un fallo nuestro: lo vigila check_img
    errs = [r for r in recent if r.get("code", 200) >= 500 and not (r.get("path") == "/api/img" and r.get("code") == 502)]
    cli = [r for r in tail("client", 100) if time.time() - r.get("t", 0) < 3600]
    return (not errs), f"{len(recent)} peticiones en la última hora · {len(errs)} errores 5xx · {len(cli)} errores de la página"

def check_img():
    files = [x for x in os.listdir(IMG_DIR) if not x.endswith((".t", ".tmp"))]
    mb = sum(os.path.getsize(os.path.join(IMG_DIR, x)) for x in files) / 1048576
    tot = sum(IMG_STATS.values()); err = IMG_STATS["error"]
    return (tot == 0 or err / tot < 0.3), f"{len(files)} fotos en caché ({mb:.0f} MB) · desde el arranque: {IMG_STATS['hit']} de caché, {IMG_STATS['fetch']} descargadas, {err} errores"

# destinos internos que el contenedor NO debe alcanzar (cortafuegos pisos-firewall en la Pi)
ISOLATION_TARGETS = [t for t in os.environ.get("ISOLATION_TARGETS", "172.31.250.1:9443,172.31.250.1:22,10.20.15.232:22,100.72.170.60:22,172.17.0.1:9000").split(",") if t]
def check_isolation():
    """Intenta conectar desde el contenedor a servicios internos: si alguno responde, el aislamiento ha fallado."""
    abiertos = []
    for t in ISOLATION_TARGETS:
        host, port = t.rsplit(":", 1)
        try:
            with socket.create_connection((host, int(port)), timeout=2): abiertos.append(t)
        except OSError:
            pass
    if abiertos: return False, f"¡el contenedor PUEDE llegar a {', '.join(abiertos)}! (¿cortafuegos desactivado?)"
    return True, f"bloqueado: {len(ISOLATION_TARGETS)} destinos internos (Portainer, SSH, LAN, Tailscale) inalcanzables desde el contenedor"

def check_storage():
    def du(p):
        tot = 0
        for root, _, files in os.walk(p):
            for f in files:
                try: tot += os.path.getsize(os.path.join(root, f))
                except OSError: pass
        return tot / 1048576
    logs_mb, img_mb = du(LOGDIR), du(IMG_DIR)
    st = os.statvfs(os.path.dirname(DB) or "/data"); free = st.f_bavail / st.f_blocks * 100; free_gb = st.f_bavail * st.f_frsize / 1e9
    ok = logs_mb < 100 and free > 10
    return ok, f"logs {logs_mb:.1f} MB · caché de fotos {img_mb:.0f} MB · disco libre {free_gb:.0f} GB ({free:.0f} %)"

def monitor():
    while True:
        for name, fn in (("funnel_publico", check_funnel), ("web_github", check_site), ("lares_pasadas", check_lares_runs),
                         ("lares_cron", check_lares_cron), ("buzon_compartido", check_bridge), ("api_errores", check_api_errors), ("fotos", check_img), ("almacenamiento", check_storage), ("aislamiento_red", check_isolation), ("motor_rutas", check_rutas)):
            chk(name, fn)
        time.sleep(300)

# ------------------------------------------------------------------ panel privado (solo tailnet)
class Admin(BaseHTTPRequestHandler):
    server_version = "pisos-admin/1"
    def log_message(self, fmt, *a): pass
    def allowed(self):
        """El puerto 8790 solo es accesible desde la propia Pi (127.0.0.1) o desde la tailnet vía
        `tailscale serve`. Con serve web (nombre, :9444) llega Tailscale-User-Login y se comprueba;
        con serve TCP (IP, :9445, para aparatos sin MagicDNS) no llega cabecera y se acepta."""
        who = self.headers.get("Tailscale-User-Login", "")
        if not who: return True
        return (not ADMIN_USERS) or any(who.startswith(u) for u in ADMIN_USERS)
    def send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if not self.allowed():
            log("events", level="warn", src="admin", msg=f"acceso denegado: {self.headers.get('Tailscale-User-Login','(sin login)')}")
            return self.send(403, "forbidden", "text/plain")
        if self.path.startswith("/status"):
            st = {"ahora": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "uptime_h": round((time.time()-STARTED)/3600, 2),
                  "checks": CHECKS, "eventos": tail("events", 60)[::-1], "api": tail("api", 80)[::-1], "cliente": tail("client", 30)[::-1],
                  "lares": [json.loads(x) for x in (_txt(os.path.join(BRIDGE, "lares_runs.jsonl")).splitlines()[-15:] if os.path.exists(os.path.join(BRIDGE, "lares_runs.jsonl")) else []) if x.strip()][::-1],
                  "listas": listas(),
                  "tiempo_real": {"abiertas": SSE["n"], "max": SSE_MAX, "desde_arranque": SSE["total"], "rechazadas": SSE["rechazadas"]},
                  "usuario": self.headers.get("Tailscale-User-Login", "") or "acceso por IP de Tailscale"}
            return self.send(200, json.dumps(st, ensure_ascii=False), "application/json; charset=utf-8")
        if self.path in ("/", "/index.html"):
            return self.send(200, open(os.path.join(os.path.dirname(__file__), "admin.html"), encoding="utf-8").read(), "text/html; charset=utf-8")
        if self.path.startswith("/flow"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            since = float(q.get("since", ["0"])[0] or 0)
            items = [{"k": "api", **r} for r in tail("api", 300) if r.get("t", 0) > since]
            items += [{"k": "check", **r} for h in CHECK_HIST.values() for r in h if r.get("t", 0) > since]
            lp = os.path.join(BRIDGE, "lares_runs.jsonl")
            if os.path.exists(lp):
                for x in _txt(lp).splitlines()[-20:]:
                    try:
                        r = json.loads(x)
                        if r.get("t", 0) > since: items.append({"k": "lares", **r})
                    except Exception: pass
            items.sort(key=lambda r: r.get("t", 0))
            return self.send(200, json.dumps({"now": time.time(), "checks": CHECKS, "items": items[-400:]}, ensure_ascii=False), "application/json; charset=utf-8")
        if self.path == "/checknow":
            threading.Thread(target=lambda: [chk(n, f) for n, f in (("funnel_publico", check_funnel), ("web_github", check_site), ("lares_pasadas", check_lares_runs), ("lares_cron", check_lares_cron), ("buzon_compartido", check_bridge), ("api_errores", check_api_errors), ("fotos", check_img), ("almacenamiento", check_storage), ("aislamiento_red", check_isolation), ("motor_rutas", check_rutas))], daemon=True).start()
            return self.send(200, '{"ok":true}', "application/json")
        self.send(404, "not found", "text/plain")

if __name__ == "__main__":
    migrate_old_logs()
    try:
        for k, v in json.loads(_txt(os.path.join(LOGDIR, "checks_recientes.json"))).items():
            CHECK_HIST[k].extend(v[-10:]); CHECKS[k] = v[-1]
    except Exception: pass
    init()
    log("events", level="info", src="api", msg="arranque del backend")
    threading.Thread(target=housekeeper, daemon=True).start()
    threading.Thread(target=gtfs_worker, daemon=True).start()
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=vigilar_candidatos, daemon=True).start()
    threading.Thread(target=lambda: ThreadingHTTPServer(("0.0.0.0", 8790), Admin).serve_forever(), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8787), H).serve_forever()
