"""Tests rápidos del backend (backend/app.py), sin internet ni Docker.
Cada clase prueba una función o un endpoint; se pueden lanzar por separado:
    python3 -m unittest tests.test_backend.TestEstado -v
"""
import importlib.util, json, os, sys, tempfile, threading, time, unittest, urllib.request, urllib.error, gzip
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="pisos-test-")
os.environ.update({"DB_PATH": os.path.join(TMP, "pisos.db"), "LOG_DIR": os.path.join(TMP, "logs"),
                   "BRIDGE_DIR": os.path.join(TMP, "bridge"), "API_KEY": "clave-test",
                   "ALLOWED_ORIGINS": "https://jonlarra1.github.io", "GTFS_DIR": os.path.join(TMP, "gtfs")})
os.makedirs(os.environ["BRIDGE_DIR"], exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "backend"))
spec = importlib.util.spec_from_file_location("app", os.path.join(ROOT, "backend", "app.py"))
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
app.init()
app.SSE_PING = 0.2                                   # en tests, las conexiones SSE cerradas se liberan rápido
ORIGIN = "https://jonlarra1.github.io"


def tearDownModule():
    if Servidor.srv: Servidor.srv.shutdown(); Servidor.srv.server_close()


class Servidor:
    """API real en un puerto libre de 127.0.0.1, una vez para todos los tests."""
    srv = None
    @classmethod
    def url(cls):
        if cls.srv is None:
            cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
            threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{cls.srv.server_address[1]}"


def pedir(path, data=None, headers=None, method=None):
    """Devuelve (código, cabeceras, json o bytes)."""
    h = {"Origin": ORIGIN, **(headers or {})}
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(Servidor.url() + path, data=body, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=10); code, hd, raw = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        code, hd, raw = e.code, e.headers, e.read(); e.close()
    try: return code, hd, json.loads(raw)
    except ValueError: return code, hd, raw


def escribir(path, d, clave="clave-test", origin=ORIGIN):
    return pedir(path, d, {"X-Pisos-Key": clave, "Origin": origin} if origin else {"X-Pisos-Key": clave})


class Base(unittest.TestCase):
    def setUp(self):
        app.hits.clear(); app._rl_logged.clear()       # cada test empieza sin límites de peticiones


class TestValidarFiltros(Base):
    OK = {"precio_max": 23000, "dorm_min": 3, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15", "compartir": True, "contrato_largo": False}
    def test_validos(self):
        f = app.validar_filtros(self.OK)
        self.assertEqual(f["vaer_min"], 4); self.assertFalse(f["contrato_largo"])
    def test_rechaza_valores_fuera_de_rango(self):
        for k, v in (("precio_max", 500), ("precio_max", 99999), ("dorm_min", 0), ("dorm_min", 9), ("disp_desde", "mañana"), ("disp_hasta", "2026-01-01")):
            with self.subTest(k=k, v=v): self.assertIsNone(app.validar_filtros({**self.OK, k: v}))
    def test_rechaza_si_falta_un_campo(self):
        d = dict(self.OK); d.pop("compartir"); self.assertIsNone(app.validar_filtros(d))


class TestImgUrl(Base):
    """img_proxy: solo hosts y rutas permitidos (se comprueba antes de descargar nada)."""
    def test_rechaza(self):
        for u in ("http://image-lambda.boligportal.dk/x", "https://evil.com/x.jpg", "https://image-lambda.boligportal.dk:8080/x",
                  "https://user:pw@image-lambda.boligportal.dk/x", "https://image-lambda.boligportal.dk/../../etc/passwd",
                  "https://image-lambda.boligportal.dk/%2e%2e/x", "https://s3.eu-central-1.amazonaws.com/otro-bucket/x.jpg",
                  "file:///etc/passwd", "https://127.0.0.1/x", ""):
            with self.subTest(u=u), self.assertRaises(ValueError): app.img_proxy(u)
    def test_redirecciones_solo_a_portales(self):
        h = app._RedirSoloPortales(); req = urllib.request.Request("https://image-lambda.boligportal.dk/a")
        for u in ("https://evil.com/x", "http://image-lambda.boligportal.dk/x", "https://127.0.0.1/x", "https://image-lambda.boligportal.dk:8443/x"):
            with self.subTest(u=u), self.assertRaises(ValueError): h.redirect_request(req, None, 302, "Found", {}, u)
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", {}, "https://image-lambda.boligportal.dk/b"))
    def test_endpoint_responde_400(self):
        code, _, d = pedir("/api/img?u=" + urllib.request.quote("https://evil.com/a.jpg"))
        self.assertEqual(code, 400)


class TestLimites(Base):
    def test_por_ip(self):
        self.assertFalse(any(app.limited("t:1", 3) for _ in range(3)))
        self.assertTrue(app.limited("t:1", 3))
        self.assertFalse(app.limited("t:2", 3))          # otra IP no se ve afectada
    def test_global(self):
        for i in range(5): app.limited(f"g:{i}", 100, "g:*", 5)
        self.assertTrue(app.limited("g:nueva", 100, "g:*", 5))


class TestEstado(Base):
    def test_get_estados(self):
        code, hd, d = pedir("/api/estados")
        self.assertEqual(code, 200); self.assertIsInstance(d, dict)
        self.assertEqual(hd["Access-Control-Allow-Origin"], ORIGIN); self.assertEqual(hd["Cache-Control"], "no-cache"); self.assertTrue(hd["ETag"])
    def test_post_valido_y_aparece(self):
        code, _, d = escribir("/api/estado", {"id": "4641637", "st": "gusta", "by": "jon"})
        self.assertEqual(code, 200); self.assertEqual(d["4641637"]["st"], "gusta")
        self.assertEqual(pedir("/api/estados")[2]["4641637"]["by"], "jon")
        cfg = json.loads(app._txt(os.path.join(os.environ["BRIDGE_DIR"], "config.json")))   # y Lares lo ve
        self.assertIn("4641637", cfg["con_estado"])
    def test_sin_clave_403(self):
        self.assertEqual(escribir("/api/estado", {"id": "1234", "st": "gusta"}, clave="mala")[0], 403)
    def test_origen_no_permitido_403(self):
        self.assertEqual(escribir("/api/estado", {"id": "1234", "st": "gusta"}, origin="https://evil.com")[0], 403)
    def test_datos_malos_400(self):
        for d in ({"id": "abc", "st": "gusta"}, {"id": "1234", "st": "borrado"}, {"id": "1234", "st": "gusta", "by": "pepe"}, ["x"]):
            with self.subTest(d=d): self.assertEqual(escribir("/api/estado", d)[0], 400)
    def test_json_roto_400(self):
        self.assertEqual(escribir("/api/estado", b"{no es json")[0], 400)
    def test_cuerpo_grande_413(self):
        self.assertEqual(escribir("/api/estado", {"id": "1234", "st": "gusta", "x": "a" * 2000})[0], 413)
    def test_limite_escrituras_429(self):
        codes = [escribir("/api/estado", {"id": "5555", "st": "nuevo"})[0] for _ in range(31)]
        self.assertEqual(codes[-1], 429); self.assertNotIn(429, codes[:30])


class TestZonasYFiltros(Base):
    def test_zona(self):
        code, _, d = escribir("/api/zona", {"zona": "Valby", "permitida": True, "by": "unai"})
        self.assertEqual(code, 200); self.assertTrue(d["Valby"])
    def test_zona_mala(self):
        for d in ({"zona": "<script>", "permitida": True}, {"zona": "Valby", "permitida": "si"}):
            with self.subTest(d=d): self.assertEqual(escribir("/api/zona", d)[0], 400)
    def test_filtros(self):
        code, _, d = escribir("/api/filtros", {**TestValidarFiltros.OK, "precio_max": 21000})
        self.assertEqual(code, 200); self.assertEqual(d["precio_max"], 21000)
        self.assertEqual(pedir("/api/filtros")[2]["precio_max"], 21000)


class TestCandidatos(Base):
    def escribe(self, obj):
        with open(os.path.join(os.environ["BRIDGE_DIR"], "candidatos.json"), "w") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
    def test_mezcla_estado(self):
        self.escribe({"actualizado": "2026-10-22T10:00:00", "pisos": [{"id": "9999001", "precio": 15000}, "basura"]})
        escribir("/api/estado", {"id": "9999001", "st": "descartado", "by": "jon"})
        d = pedir("/api/candidatos")[2]
        self.assertEqual(len(d["pisos"]), 1); self.assertEqual(d["pisos"][0]["st"], "descartado")
    def test_fichero_roto_no_rompe(self):
        self.escribe("{roto")
        self.assertEqual(pedir("/api/candidatos")[2], {"actualizado": None, "pisos": []})


class TestVarios(Base):
    def test_health(self): self.assertEqual(pedir("/api/health")[2], {"ok": True})
    def test_404(self): self.assertEqual(pedir("/api/nada")[0], 404)
    def test_options_cors(self):
        code, hd, _ = pedir("/api/estado", method="OPTIONS")
        self.assertEqual(code, 204); self.assertIn("X-Pisos-Key", hd["Access-Control-Allow-Headers"])
    def test_cors_no_para_otros_origenes(self):
        _, hd, _ = pedir("/api/estados", headers={"Origin": "https://evil.com"})
        self.assertIsNone(hd.get("Access-Control-Allow-Origin"))
    def test_clientlog(self):
        self.assertEqual(pedir("/api/clientlog", {"level": "error", "msg": "prueba", "where": "test"})[0], 200)
        self.assertEqual(app.tail("client", 1)[-1]["msg"], "prueba")
        self.assertEqual(pedir("/api/clientlog", b"x" * 3000)[0], 400)
        self.assertEqual(pedir("/api/clientlog", {"msg": "spam"}, headers={"Origin": "https://evil.com"})[0], 403)
    def test_ruta_sin_motor_503_y_params_400(self):
        app.MOTOR["m"] = None
        self.assertEqual(pedir("/api/ruta?olat=55.7&olon=12.5&dlat=55.7&dlon=12.5&fecha=2026-10-26&hora=09:00")[0], 503)


class TestEtag(Base):
    def test_304_si_no_cambia(self):
        code, hd, _ = pedir("/api/zonas"); tag = hd["ETag"]
        self.assertEqual(code, 200); self.assertEqual(hd["Cache-Control"], "no-cache")
        code2, hd2, body = pedir("/api/zonas", headers={"If-None-Match": tag})
        self.assertEqual((code2, body), (304, b"")); self.assertEqual(hd2["Access-Control-Allow-Origin"], ORIGIN)
    def test_200_si_cambia(self):
        tag = pedir("/api/estados")[1]["ETag"]
        escribir("/api/estado", {"id": "8888001", "st": "contactado", "by": "jon"})
        self.assertEqual(pedir("/api/estados", headers={"If-None-Match": tag})[0], 200)
    def test_post_sin_etag(self):
        self.assertIsNone(escribir("/api/estado", {"id": "8888002", "st": "nuevo"})[1].get("ETag"))


class TestEventos(Base):
    """GET /api/eventos (SSE): los cambios llegan al momento."""
    def tearDown(self):
        fin = time.time() + 3                      # el servidor nota el cierre en el siguiente ping
        while app.SSE["n"] and time.time() < fin: time.sleep(0.05)
        self.assertEqual(app.SSE["n"], 0, "quedan conexiones SSE abiertas")
    def abrir(self):
        import socket
        s = socket.create_connection(("127.0.0.1", int(Servidor.url().rsplit(":", 1)[1])), timeout=5)
        s.sendall(f"GET /api/eventos HTTP/1.1\r\nHost: x\r\nOrigin: {ORIGIN}\r\n\r\n".encode())
        return s
    def leer_hasta(self, s, texto, t=5):
        data, fin = b"", time.time() + t
        while texto.encode() not in data and time.time() < fin:
            try: data += s.recv(4096)
            except TimeoutError: break
        return data.decode()
    def test_aviso_de_estado(self):
        s = self.abrir()
        try:
            self.assertIn("text/event-stream", self.leer_hasta(s, ": conectado"))
            escribir("/api/estado", {"id": "8888003", "st": "gusta", "by": "unai"})
            out = self.leer_hasta(s, "8888003")
            self.assertIn("event: estados", out); self.assertIn('"8888003":{"st":"gusta"', out)
        finally: s.close()
    def test_aviso_de_zonas(self):
        s = self.abrir()
        try:
            self.leer_hasta(s, ": conectado"); escribir("/api/zona", {"zona": "Valby", "permitida": False})
            self.assertIn("event: ajustes", self.leer_hasta(s, "event: ajustes"))
        finally: s.close()
    def test_limite_por_ip(self):
        socks = [self.abrir() for _ in range(app.SSE_IP)]
        try:
            for s in socks: self.assertIn(": conectado", self.leer_hasta(s, ": conectado"))
            extra = self.abrir()
            try: self.assertIn(" 503 ", self.leer_hasta(extra, "\r\n").split("\r\n")[0])
            finally: extra.close()
        finally:
            for s in socks: s.close()
    def test_vigilar_candidatos(self):
        p = os.path.join(os.environ["BRIDGE_DIR"], "candidatos.json")
        with open(p, "w") as f: json.dump({"actualizado": "a", "pisos": []}, f)
        threading.Thread(target=app.vigilar_candidatos, kwargs={"cada": 0.1}, daemon=True).start()
        time.sleep(0.3); n0 = app._ultimo["n"]
        with open(p, "w") as f: json.dump({"actualizado": "b", "pisos": [{"id": "1"}]}, f)
        os.utime(p, (time.time() + 5, time.time() + 5)); time.sleep(0.5)
        self.assertGreater(app._ultimo["n"], n0); self.assertIn('"b"', app._ultimo["data"])


class TestLogs(Base):
    def test_log_y_tail(self):
        app.log("events", level="info", src="test", msg="hola")
        self.assertEqual(app.tail("events", 1)[-1]["msg"], "hola")
    def test_housekeeping_comprime_y_resume(self):
        d = os.path.join(os.environ["LOG_DIR"], "api"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "2026-01-01.jsonl"), "w") as f:
            f.write(json.dumps({"t": 1, "path": "/api/estados", "code": 200, "ms": 4}) + "\n" + json.dumps({"t": 2, "path": "/api/img", "code": 502, "ms": 9}) + "\n")
        app.housekeeping()
        self.assertTrue(os.path.exists(os.path.join(d, "2026-01-01.jsonl.gz")))
        s = json.loads(gzip.open(os.path.join(os.environ["LOG_DIR"], "summary", "2026-01-01.jsonl.gz"), "rt").read())
        self.assertEqual((s["peticiones"], s["errores_5xx"]), (2, 1))
    def test_cambio_de_comprobacion_genera_evento(self):
        app.chk("prueba_x", lambda: (True, "bien")); app.chk("prueba_x", lambda: (False, "mal"))
        ev = [e for e in app.tail("events", 20) if e.get("src") == "prueba_x"]
        self.assertTrue(ev and ev[-1]["msg"].startswith("CAÍDO"))
        app.chk("prueba_x", lambda: 1 / 0)                   # una comprobación que revienta cuenta como fallo
        self.assertFalse(app.CHECKS["prueba_x"]["ok"])


class TestRetirados(Base):
    """retirados.json (Lares) -> descartado por Lares con motivo, una sola vez."""
    def escribir_retirados(self, pisos):
        with open(os.path.join(os.environ["BRIDGE_DIR"], "retirados.json"), "w") as f:
            json.dump({"actualizado": "x", "pisos": pisos}, f)
    def test_descarta_con_motivo_y_avisa(self):
        n0 = app._ultimo["n"]
        self.escribir_retirados({"7700001": {"link": "https://www.boligportal.dk/x-id-7700001", "motivo": "marcado como alquilado («udlejet»)", "desde": "2026-10-23T10:00:00"}})
        self.assertEqual(app.aplicar_retirados(), 1)
        e = app.all_estados()["7700001"]
        self.assertEqual((e["st"], e["by"]), ("descartado", "lares")); self.assertIn("udlejet", e["motivo"])
        self.assertGreater(app._ultimo["n"], n0)                                   # aviso en tiempo real
        self.assertIn("motivo", pedir("/api/estados")[2]["7700001"])
    def test_no_se_repite_si_lo_recuperais(self):
        self.escribir_retirados({"7700002": {"motivo": "x", "desde": "2026-10-23"}}); app.aplicar_retirados()
        escribir("/api/estado", {"id": "7700002", "st": "gusta", "by": "jon"})
        self.assertNotIn("motivo", app.all_estados()["7700002"])                     # al cambiarlo a mano se borra el motivo
        self.assertEqual(app.aplicar_retirados(), 0); self.assertEqual(app.all_estados()["7700002"]["st"], "gusta")
    def test_respeta_un_descarte_vuestro(self):
        escribir("/api/estado", {"id": "7700003", "st": "descartado", "by": "unai"})
        self.escribir_retirados({"7700003": {"motivo": "x", "desde": "2026-10-23"}}); app.aplicar_retirados()
        self.assertEqual(app.all_estados()["7700003"]["by"], "unai")
    def test_fichero_roto_o_ids_raros(self):
        with open(os.path.join(os.environ["BRIDGE_DIR"], "retirados.json"), "w") as f: f.write("{roto")
        self.assertEqual(app.aplicar_retirados(), 0)
        self.escribir_retirados({"abc": {"motivo": "x"}, "7700004": "no es un dict"}); self.assertEqual(app.aplicar_retirados(), 0)
    def test_no_se_puede_escribir_como_lares(self):
        self.assertEqual(escribir("/api/estado", {"id": "7700005", "st": "descartado", "by": "lares"})[0], 400)
    def test_migracion_de_base_antigua(self):
        import sqlite3
        viejo = os.path.join(TMP, "viejo.db")
        with sqlite3.connect(viejo) as c:
            c.execute("CREATE TABLE estado (id TEXT PRIMARY KEY, st TEXT NOT NULL, stF TEXT NOT NULL, by TEXT, ts INTEGER NOT NULL)")
            c.execute("INSERT INTO estado VALUES ('5629725','gusta','2026-10-21','jon',1)")
        db0 = app.DB; app.DB = viejo
        try:
            app.init(); self.assertEqual(app.all_estados()["5629725"]["st"], "gusta")
        finally:
            app.DB = db0


class TestCheckSite(Base):
    """check_site con una web falsa en local; los scripts del «CDN» también se simulan."""
    LIB = b"console.log('leaflet')"
    @classmethod
    def setUpClass(cls):
        import functools, http.server, hashlib, base64
        cls.dir = tempfile.mkdtemp(prefix="pisos-site-")
        h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=cls.dir)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.sri = "sha384-" + base64.b64encode(hashlib.sha384(cls.LIB).digest()).decode()
        cls._get_real = staticmethod(app._get)
    @classmethod
    def tearDownClass(cls): cls.httpd.shutdown(); cls.httpd.server_close(); app._get = cls._get_real
    def setUp(self):
        super().setUp(); app._SITE_CACHE.update(hash=None, sri_t=0, sri_mal=[], sin_rutas=[])
        app.SITE = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        self.cdn = self.LIB
        app._get = lambda url, timeout=15: (200, self.cdn) if url.startswith("https://cdn") else self._get_real(url, timeout)
    def web(self, pisos=("1",), rutas=("1",), csp=True, sri=None, appjs=False):
        js = (f"const PISOS = {json.dumps([{'id': i} for i in pisos])};\nconst OUT = [];\n"
              f"const RUTAS={json.dumps({'ix': {'foot': {'lun': {'09:00': {'30': {i: {} for i in rutas}}}}}})};\n")
        head = ('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'">' if csp else "")
        head += f'<script src="https://cdn.x/leaflet.js" integrity="{sri or self.sri}" crossorigin="anonymous"></script>'
        page = f"<html><head>{head}</head><body>" + ('<script src="app.js"></script>' if appjs else f"<script>\n{js}</script>") + "</body></html>"
        for nombre, txt in (("index.html", page), ("app.js", js if appjs else None)):
            if txt is not None:
                with open(os.path.join(self.dir, nombre), "w") as f: f.write(txt)
    def test_todo_bien(self):
        self.web(); ok, det = app.check_site(); self.assertTrue(ok, det); self.assertIn("todos con rutas", det)
    def test_piso_sin_rutas(self):
        self.web(pisos=("1", "2")); ok, det = app.check_site(); self.assertFalse(ok); self.assertIn("pisos sin rutas: 2", det)
    def test_cdn_cambiado(self):
        self.web(); self.cdn = b"otra cosa"; ok, det = app.check_site(); self.assertFalse(ok); self.assertIn("SRI", det)
    def test_sin_csp(self):
        self.web(csp=False); ok, det = app.check_site(); self.assertFalse(ok); self.assertIn("sin CSP", det)
    def test_con_app_js(self):
        self.web(appjs=True); ok, det = app.check_site(); self.assertTrue(ok, det); self.assertEqual(app.SITE_DATA["pisos"], [{"id": "1"}])
    def test_texto_app_js_dentro_del_codigo_no_cuenta(self):
        """El JS de la página contiene script[src="app.js"] como texto: no debe pedir un app.js que no existe."""
        self.web(); p = os.path.join(self.dir, "index.html")
        with open(p) as f: h = f.read()
        with open(p, "w") as f: f.write(h.replace("const OUT", "document.querySelector('script[src=\"app.js\"]');\nconst OUT"))
        if os.path.exists(os.path.join(self.dir, "app.js")): os.remove(os.path.join(self.dir, "app.js"))
        ok, det = app.check_site(); self.assertTrue(ok, det)
    def test_web_real_del_repo(self):
        """La index.html actual del repo pasa la comprobación (salvo el CDN, simulado aquí)."""
        import shutil; shutil.copy(os.path.join(ROOT, "index.html"), os.path.join(self.dir, "index.html"))
        app._get = lambda url, timeout=15: (200, b"") if url.startswith("https://") else self._get_real(url, timeout)
        ok, det = app.check_site(); self.assertIn("pisos", det); self.assertNotIn("sin rutas", det); self.assertNotIn("sin CSP", det)


class TestPanel(Base):
    """Panel privado (puerto 8790): /status y la página."""
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Admin); threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close()
    def get(self, p, h=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + p, headers=h or {}), timeout=10) as r: return r.status, r.read()
        except urllib.error.HTTPError as e:
            e.close(); return e.code, b""
    def test_status(self):
        code, b = self.get("/status"); d = json.loads(b)
        self.assertEqual(code, 200)
        for k in ("checks", "eventos", "api", "listas", "tiempo_real"): self.assertIn(k, d)
    def test_pagina(self):
        code, b = self.get("/"); self.assertEqual(code, 200); self.assertIn(b"tiempo real", b)
    def test_login_ajeno_403(self):
        app.ADMIN_USERS[:] = ["larrabajojon@"]
        self.assertEqual(self.get("/status", {"Tailscale-User-Login": "otro@gmail.com"})[0], 403)
        self.assertEqual(self.get("/status", {"Tailscale-User-Login": "larrabajojon@gmail.com"})[0], 200)


class TestComprobaciones(Base):
    def test_bridge(self):
        ok, det = app.check_bridge(); self.assertIn("config.json", det)
    def test_api_errores(self):
        app.log("api", m="GET", path="/api/estados", code=500, ms=1)
        ok, det = app.check_api_errors(); self.assertFalse(ok); self.assertIn("errores 5xx", det)
    def test_lares_sin_pasadas(self):
        p = os.path.join(os.environ["BRIDGE_DIR"], "lares_runs.jsonl")
        if os.path.exists(p): os.remove(p)
        self.assertFalse(app.check_lares_runs()[0])
        with open(p, "w") as f: f.write(json.dumps({"t": time.time(), "leidos": {}, "candidatos": 2}) + "\n")
        self.assertTrue(app.check_lares_runs()[0])


class TestRutaDirecta(unittest.TestCase):
    """Motor de rutas: además del transporte, la opción de ir directo en bici / andando (sin GTFS: se simula el CSA)."""
    def motor(self, opciones):
        import routing
        m = object.__new__(routing.Motor); m.dia = lambda ymd: {}
        it = iter(opciones)
        m.csa = lambda *a, **k: next(it, (1e18, None, None, None))
        def legs(la, lo, dest, dname, r, pf):
            return [{"m": "bike", "min": 2, "d": 600, "to": "St."},
                    {"m": "S-tog", "line": r[2], "head": "x", "from": "a", "to": "b", "dep": "08:00", "arr": r[3], "n": 2},
                    {"m": "bike", "min": 2, "d": 600, "to": dname}]
        m.legs = legs
        return m
    PF = {"mode": "bike", "v_walk": 5/3.6, "v_bike": 20/3.6, "lim_min": 10}
    CASA, CERCA, LEJOS = (55.649146, 12.553573), (55.6440, 12.5440), (55.7335, 12.5769)
    def test_cerca_solo_bici_y_sin_rodeos_en_tren(self):
        tren = (8*3600+11*60, 1, "A", "08:09")                        # bici + tren + bici: 13 min
        o = self.motor([tren]).ruta(*self.CASA, self.CERCA, "Cris", "2026-10-26", "08:00", "salir", self.PF)
        self.assertEqual(o[0]["solo"], "bike"); self.assertLessEqual(o[0]["min"], 4)
        self.assertEqual(len(o), 1)                                    # la de tren (más lenta) sobra
    def test_lejos_manda_el_transporte(self):
        tren = (8*3600+26*60, 1, "A", "08:24")
        o = self.motor([tren]).ruta(*self.CASA, self.LEJOS, "NS", "2026-10-26", "08:00", "salir", {**self.PF, "lim_min": 10})
        self.assertFalse(o[0].get("solo")); self.assertTrue(all(not x.get("solo") for x in o))
    def test_sin_transporte_queda_directo(self):
        o = self.motor([]).ruta(*self.CASA, self.LEJOS, "NS", "2026-10-26", "08:30", "llegar", {**self.PF, "mode": "foot"})
        self.assertEqual(len(o), 1); self.assertEqual(o[0]["solo"], "walk"); self.assertEqual(o[0]["llega"], "08:30")
        self.assertEqual(o[0]["legs"][0]["m"], "walk")


if __name__ == "__main__":
    unittest.main()
