"""Pisos CPH backend: shared flat status (gusta / nuevo / contactado / descartado).
Python stdlib only. SQLite at /data/pisos.db."""
import json, os, re, sqlite3, threading, time, tempfile
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

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS estado (id TEXT PRIMARY KEY, st TEXT NOT NULL, stF TEXT NOT NULL, by TEXT, ts INTEGER NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS historial (n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, st TEXT, stF TEXT, by TEXT, ts INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS zonas (zona TEXT PRIMARY KEY, permitida INTEGER NOT NULL, by TEXT, ts INTEGER)")
        for z, v in ZONAS_DEFAULT.items():
            c.execute("INSERT OR IGNORE INTO zonas (zona, permitida, by, ts) VALUES (?,?,?,?)", (z, v, "", 0))
    write_config()

def all_estados():
    with db() as c:
        return {r[0]: {"st": r[1], "stF": r[2], "by": r[3] or "", "ts": r[4]} for r in c.execute("SELECT id, st, stF, by, ts FROM estado")}

def all_zonas():
    with db() as c:
        return {r[0]: bool(r[1]) for r in c.execute("SELECT zona, permitida FROM zonas ORDER BY zona")}

def write_config():
    """config.json para Lares: filtros, zonas y pisos que ya tienen estado (no volver a proponerlos)."""
    if not os.path.isdir(BRIDGE): return
    cfg = {"actualizado": time.strftime("%Y-%m-%dT%H:%M:%S"), "filtros": FILTROS, "zonas": all_zonas(),
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

def limited(ip):  # max 30 writes per minute per client
    now = time.time()
    with lock:
        q = [t for t in hits.get(ip, []) if now-t < 60]
        if len(q) >= 30: hits[ip] = q; return True
        q.append(now); hits[ip] = q; return False

class H(BaseHTTPRequestHandler):
    server_version = "pisos/1"
    def log_message(self, fmt, *a): print("%s %s" % (self.address_string(), fmt % a), flush=True)
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
        if self.path == "/api/estados": return self.reply(200, all_estados())
        if self.path == "/api/zonas": return self.reply(200, all_zonas())
        if self.path == "/api/candidatos": return self.reply(200, candidatos())
        self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path not in ("/api/estado", "/api/zona"): return self.reply(404, {"error": "not found"})
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
        if self.path == "/api/zona":
            z, ok = str(d.get("zona", "")).strip(), d.get("permitida")
            if not ZONA_RE.match(z) or not isinstance(ok, bool): return self.reply(400, {"error": "bad data"})
            with lock, db() as c:
                c.execute("INSERT INTO zonas (zona, permitida, by, ts) VALUES (?,?,?,?) ON CONFLICT(zona) DO UPDATE SET permitida=excluded.permitida, by=excluded.by, ts=excluded.ts", (z, int(ok), by, int(time.time())))
            write_config(); return self.reply(200, all_zonas())
        pid, st = str(d.get("id", "")), d.get("st")
        stF = time.strftime("%Y-%m-%d")
        if not ID_RE.match(pid) or st not in ESTADOS or by not in WHO: return self.reply(400, {"error": "bad data"})
        ts = int(time.time())
        with lock, db() as c:
            c.execute("INSERT INTO estado (id, st, stF, by, ts) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET st=excluded.st, stF=excluded.stF, by=excluded.by, ts=excluded.ts", (pid, st, stF, by, ts))
            c.execute("INSERT INTO historial (id, st, stF, by, ts) VALUES (?,?,?,?,?)", (pid, st, stF, by, ts))
        write_config(); self.reply(200, all_estados())

if __name__ == "__main__":
    init()
    ThreadingHTTPServer(("0.0.0.0", 8787), H).serve_forever()
