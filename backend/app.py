"""Pisos CPH backend: shared flat status (gusta / nuevo / contactado / descartado).
Python stdlib only. SQLite at /data/pisos.db."""
import json, os, re, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("DB_PATH", "/data/pisos.db")
ORIGINS = set(os.environ.get("ALLOWED_ORIGINS", "https://jonlarra1.github.io,http://localhost:8000").split(","))
KEY = os.environ.get("API_KEY", "")
ESTADOS = {"gusta", "nuevo", "contactado", "descartado"}
WHO = {"", "jon", "unai"}
ID_RE = re.compile(r"^[0-9]{4,12}$")
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

def all_estados():
    with db() as c:
        return {r[0]: {"st": r[1], "stF": r[2], "by": r[3] or "", "ts": r[4]} for r in c.execute("SELECT id, st, stF, by, ts FROM estado")}

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
        self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path != "/api/estado": return self.reply(404, {"error": "not found"})
        if KEY and self.headers.get("X-Pisos-Key") != KEY: return self.reply(403, {"error": "forbidden"})
        o = self.headers.get("Origin", "")
        if o and o not in ORIGINS: return self.reply(403, {"error": "origin"})
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        if limited(ip): return self.reply(429, {"error": "too many requests"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 1024: return self.reply(413, {"error": "bad size"})
        try: d = json.loads(self.rfile.read(n))
        except Exception: return self.reply(400, {"error": "bad json"})
        pid, st, by = str(d.get("id", "")), d.get("st"), str(d.get("by", "")).lower()
        stF = time.strftime("%Y-%m-%d")
        if not ID_RE.match(pid) or st not in ESTADOS or by not in WHO: return self.reply(400, {"error": "bad data"})
        ts = int(time.time())
        with lock, db() as c:
            c.execute("INSERT INTO estado (id, st, stF, by, ts) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET st=excluded.st, stF=excluded.stF, by=excluded.by, ts=excluded.ts", (pid, st, stF, by, ts))
            c.execute("INSERT INTO historial (id, st, stF, by, ts) VALUES (?,?,?,?,?)", (pid, st, stF, by, ts))
        self.reply(200, all_estados())

if __name__ == "__main__":
    init()
    ThreadingHTTPServer(("0.0.0.0", 8787), H).serve_forever()
