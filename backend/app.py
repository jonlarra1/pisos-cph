"""Pisos CPH backend: shared flat status (gusta / nuevo / contactado / descartado).
Python stdlib only. SQLite at /data/pisos.db."""
import json, os, re, sqlite3, threading, time, tempfile, ssl, socket, http.client, urllib.request, urllib.parse, collections, hashlib
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
           "compartir": True, "contrato_largo": True}
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
CHECKS = {}                                  # último resultado de cada comprobación
IMG_DIR = os.path.join(os.path.dirname(DB) or "/data", "imgcache")
IMG_HOSTS = {"image-lambda.boligportal.dk": "https://www.boligportal.dk/", "s3.eu-central-1.amazonaws.com": None}
IMG_MAX_MB, IMG_CACHE_MB = 8, 300
IMG_STATS = {"hit": 0, "fetch": 0, "error": 0}
os.makedirs(IMG_DIR, exist_ok=True)
RECENT = collections.deque(maxlen=300)       # últimas peticiones (también en api.jsonl)
os.makedirs(LOGDIR, exist_ok=True)
_loglock = threading.Lock()

def log(kind, **kw):
    """Una línea JSON por evento en /data/logs/<kind>.jsonl (rota a 5 MB)."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "t": round(time.time(), 3), **kw}
    path = os.path.join(LOGDIR, f"{kind}.jsonl")
    with _loglock:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 5_000_000:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            print("log error", e, flush=True)
    if kind == "api": RECENT.append(rec)
    return rec

def tail(kind, n=50):
    path = os.path.join(LOGDIR, f"{kind}.jsonl")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size-200_000)); lines = f.read().decode("utf-8", "replace").splitlines()[1:]
        return [json.loads(x) for x in lines[-n:] if x.strip()]
    except Exception:
        return []

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS estado (id TEXT PRIMARY KEY, st TEXT NOT NULL, stF TEXT NOT NULL, by TEXT, ts INTEGER NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS historial (n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, st TEXT, stF TEXT, by TEXT, ts INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS zonas (zona TEXT PRIMARY KEY, permitida INTEGER NOT NULL, by TEXT, ts INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS ajustes (k TEXT PRIMARY KEY, v TEXT NOT NULL, by TEXT, ts INTEGER)")
        for z, v in ZONAS_DEFAULT.items():
            c.execute("INSERT OR IGNORE INTO zonas (zona, permitida, by, ts) VALUES (?,?,?,?)", (z, v, "", 0))
    write_config()

def all_estados():
    with db() as c:
        return {r[0]: {"st": r[1], "stF": r[2], "by": r[3] or "", "ts": r[4]} for r in c.execute("SELECT id, st, stF, by, ts FROM estado")}

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
        out.append({**p, "st": e.get("st", "nuevo"), "stF": e.get("stF"), "by": e.get("by", ""), "ts": e.get("ts")})
    return {"actualizado": c.get("actualizado") if isinstance(c, dict) else None, "pisos": out}

def img_proxy(u):
    """Foto de un anuncio desde los portales (con su Referer), cacheada en disco. Devuelve (bytes, content-type)."""
    pu = urllib.parse.urlparse(u)
    if pu.scheme != "https" or pu.hostname not in IMG_HOSTS: raise ValueError("host no permitido")
    if pu.hostname.startswith("s3.") and not pu.path.startswith("/lejeboligdata/"): raise ValueError("ruta no permitida")
    key = hashlib.sha1(u.encode()).hexdigest(); path = os.path.join(IMG_DIR, key)
    if os.path.exists(path):
        IMG_STATS["hit"] += 1
        with open(path, "rb") as f: data = f.read()
        ctype = open(path + ".t").read() if os.path.exists(path + ".t") else "image/jpeg"
        return data, ctype, "cache"
    h = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) Chrome/131.0", "Accept": "image/avif,image/webp,image/*"}
    if IMG_HOSTS[pu.hostname]: h["Referer"] = IMG_HOSTS[pu.hostname]
    r = urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=20)
    ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not ctype.startswith("image/"): raise ValueError(f"no es imagen ({ctype})")
    data = r.read(IMG_MAX_MB * 1024 * 1024 + 1)
    if len(data) > IMG_MAX_MB * 1024 * 1024: raise ValueError("imagen demasiado grande")
    with open(path + ".tmp", "wb") as f: f.write(data)
    os.replace(path + ".tmp", path); open(path + ".t", "w").write(ctype)
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
    return data, ctype, "portal:" + ("boligportal" if "boligportal" in pu.hostname else "lejebolig")

def limited(ip, cap=30):  # max `cap` requests per minute per client/key
    now = time.time()
    with lock:
        q = [t for t in hits.get(ip, []) if now-t < 60]
        if len(q) >= cap: hits[ip] = q; return True
        q.append(now); hits[ip] = q; return False

class H(BaseHTTPRequestHandler):
    server_version = "pisos/1"
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
        self.send_response(code); self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        if self.path == "/api/health": return self.reply(200, {"ok": True})
        if self.path.startswith("/api/img?"):
            ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
            if limited("img:"+ip, 120): return self.reply(429, {"error": "too many requests"})
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
        if self.path == "/api/estados": return self.reply(200, all_estados())
        if self.path == "/api/zonas": return self.reply(200, all_zonas())
        if self.path == "/api/filtros": return self.reply(200, filtros())
        if self.path == "/api/candidatos": return self.reply(200, candidatos())
        self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path == "/api/clientlog":    # errores de la página de los usuarios
            n = int(self.headers.get("Content-Length") or 0)
            ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
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
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        if limited(ip): return self.reply(429, {"error": "too many requests"})
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
            return self.reply(200, filtros())
        if self.path == "/api/zona":
            z, ok = str(d.get("zona", "")).strip(), d.get("permitida")
            if not ZONA_RE.match(z) or not isinstance(ok, bool): return self.reply(400, {"error": "bad data"})
            with lock, db() as c:
                c.execute("INSERT INTO zonas (zona, permitida, by, ts) VALUES (?,?,?,?) ON CONFLICT(zona) DO UPDATE SET permitida=excluded.permitida, by=excluded.by, ts=excluded.ts", (z, int(ok), by, int(time.time())))
            write_config(); log("events", level="info", src="zonas", msg=f"{z} -> {'permitida' if ok else 'excluida'}", by=by); return self.reply(200, all_zonas())
        pid, st = str(d.get("id", "")), d.get("st")
        stF = time.strftime("%Y-%m-%d")
        if not ID_RE.match(pid) or st not in ESTADOS or by not in WHO: return self.reply(400, {"error": "bad data"})
        ts = int(time.time())
        with lock, db() as c:
            c.execute("INSERT INTO estado (id, st, stF, by, ts) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET st=excluded.st, stF=excluded.stF, by=excluded.by, ts=excluded.ts", (pid, st, stF, by, ts))
            c.execute("INSERT INTO historial (id, st, stF, by, ts) VALUES (?,?,?,?,?)", (pid, st, stF, by, ts))
        write_config(); log("events", level="info", src="estado", msg=f"{pid} -> {st}", by=by); self.reply(200, all_estados())

# ------------------------------------------------------------------ comprobaciones
def chk(name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
        res = {"ok": bool(ok), "detail": detail}
    except Exception as e:
        res = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    res.update({"name": name, "ms": round((time.time()-t0)*1000), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    prev = CHECKS.get(name); CHECKS[name] = res
    log("checks", **res)
    if prev is None or prev["ok"] != res["ok"]:
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

def check_site():
    r = urllib.request.urlopen(urllib.request.Request(SITE + f"?chk={int(time.time())}", headers={"User-Agent": "pisos-monitor"}), timeout=15)
    body = r.read().decode("utf-8", "replace")
    return (r.status == 200 and "const RUTAS" in body and "const PISOS" in body), f"HTTP {r.status}, {len(body)//1024} KB"

def check_lares_runs():
    path = os.path.join(BRIDGE, "lares_runs.jsonl")
    try:
        last = json.loads(open(path, encoding="utf-8").read().strip().splitlines()[-1])
    except FileNotFoundError:
        return False, "Lares aún no ha registrado ninguna pasada (lares_runs.jsonl no existe)"
    age = (time.time() - last.get("t", 0)) / 3600
    errs = last.get("errores") or []
    ok = age < 3.7 and not errs and last.get("ok", True)
    return ok, f"última pasada hace {age:.1f} h · {last.get('leidos', {})} anuncios · {last.get('candidatos', 0)} candidatos" + (f" · errores: {errs}" if errs else "")

def check_lares_cron():
    jobs = json.load(open(LARES_CRON, encoding="utf-8"))
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
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
    errs = [r for r in recent if r.get("code", 200) >= 500]
    cli = [r for r in tail("client", 100) if time.time() - r.get("t", 0) < 3600]
    return (not errs), f"{len(recent)} peticiones en la última hora · {len(errs)} errores 5xx · {len(cli)} errores de la página"

def check_img():
    files = [x for x in os.listdir(IMG_DIR) if not x.endswith((".t", ".tmp"))]
    mb = sum(os.path.getsize(os.path.join(IMG_DIR, x)) for x in files) / 1048576
    tot = sum(IMG_STATS.values()); err = IMG_STATS["error"]
    return (tot == 0 or err / tot < 0.3), f"{len(files)} fotos en caché ({mb:.0f} MB) · desde el arranque: {IMG_STATS['hit']} de caché, {IMG_STATS['fetch']} descargadas, {err} errores"

def monitor():
    while True:
        for name, fn in (("funnel_publico", check_funnel), ("web_github", check_site), ("lares_pasadas", check_lares_runs),
                         ("lares_cron", check_lares_cron), ("buzon_compartido", check_bridge), ("api_errores", check_api_errors), ("fotos", check_img)):
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
                  "lares": [json.loads(x) for x in (open(os.path.join(BRIDGE, "lares_runs.jsonl"), encoding="utf-8").read().splitlines()[-15:] if os.path.exists(os.path.join(BRIDGE, "lares_runs.jsonl")) else []) if x.strip()][::-1],
                  "usuario": self.headers.get("Tailscale-User-Login", "") or "acceso por IP de Tailscale"}
            return self.send(200, json.dumps(st, ensure_ascii=False), "application/json; charset=utf-8")
        if self.path in ("/", "/index.html"):
            return self.send(200, open(os.path.join(os.path.dirname(__file__), "admin.html"), encoding="utf-8").read(), "text/html; charset=utf-8")
        if self.path.startswith("/flow"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            since = float(q.get("since", ["0"])[0] or 0)
            items = [{"k": "api", **r} for r in tail("api", 300) if r.get("t", 0) > since]
            items += [{"k": "check", **r} for r in tail("checks", 60) if r.get("t", 0) > since]
            lp = os.path.join(BRIDGE, "lares_runs.jsonl")
            if os.path.exists(lp):
                for x in open(lp, encoding="utf-8").read().splitlines()[-20:]:
                    try:
                        r = json.loads(x)
                        if r.get("t", 0) > since: items.append({"k": "lares", **r})
                    except Exception: pass
            items.sort(key=lambda r: r.get("t", 0))
            return self.send(200, json.dumps({"now": time.time(), "checks": CHECKS, "items": items[-400:]}, ensure_ascii=False), "application/json; charset=utf-8")
        if self.path == "/checknow":
            threading.Thread(target=lambda: [chk(n, f) for n, f in (("funnel_publico", check_funnel), ("web_github", check_site), ("lares_pasadas", check_lares_runs), ("lares_cron", check_lares_cron), ("buzon_compartido", check_bridge), ("api_errores", check_api_errors), ("fotos", check_img))], daemon=True).start()
            return self.send(200, '{"ok":true}', "application/json")
        self.send(404, "not found", "text/plain")

if __name__ == "__main__":
    init()
    log("events", level="info", src="api", msg="arranque del backend")
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=lambda: ThreadingHTTPServer(("0.0.0.0", 8790), Admin).serve_forever(), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8787), H).serve_forever()
