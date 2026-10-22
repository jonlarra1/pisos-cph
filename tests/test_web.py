"""Tests de la web (index.html) en Chromium headless, función por función. ~15 s.
La Raspberry Pi y los mapas se simulan: no hace falta internet ni la Pi.
    .venv/bin/python -m unittest tests.test_web -v
    .venv/bin/python -m unittest tests.test_web.TestPropuestos -v
Necesita Playwright en .venv (ver README). Si no está, estos tests se saltan.
"""
import functools, glob, http.server, json, os, threading, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

PI = "https://raspberrypi.tail9f1069.ts.net:8443"
FAKE = {   # respuestas simuladas de la Pi
    "/api/estados": {},
    "/api/zonas": {"Herlev": True, "Valby": False},
    "/api/filtros": {"precio_max": 23000, "dorm_min": 3, "vaer_min": 4, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15", "compartir": True, "contrato_largo": True},
    "/api/candidatos": {"actualizado": "2026-10-22T18:00:00+00:00", "pisos": [
        {"id": "7000001", "portal": "Boligportal", "link": "https://www.boligportal.dk/x-7000001", "titulo": "Piso de prueba", "zona": "Herlev",
         "precio": 16000, "aconto": 900, "m2": 100, "vaer": 4, "disp": "2027-01-01", "lat": 55.72, "lon": 12.44, "avisos": [], "st": "nuevo"}]},
}
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082")


def chromium():
    """Chromium de Playwright: el que pida la versión instalada o, si no está, el último descargado."""
    env = os.environ.get("PW_CHROMIUM")
    if env: return env
    c = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")))
    return c[-1] if c else None


class Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


@unittest.skipIf(sync_playwright is None, "Playwright no instalado (.venv/bin/pip install playwright)")
class Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Silencioso, directory=ROOT))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/index.html"
        cls.pw = sync_playwright().start()
        exe = chromium()
        cls.browser = cls.pw.chromium.launch(**({"executable_path": exe} if exe else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.httpd.shutdown(); cls.httpd.server_close()

    INIT = ""                                       # JS que se ejecuta antes de cargar (p. ej. localStorage)
    def setUp(self):
        self.ctx = self.browser.new_context(viewport={"width": 1400, "height": 900})
        if self.INIT: self.ctx.add_init_script(self.INIT)
        self.page = self.ctx.new_page()
        self.errores, self.posts, self.alerts = [], [], []
        self.page.on("pageerror", lambda e: self.errores.append(str(e)))
        self.page.on("dialog", lambda d: (self.alerts.append(d.message), d.dismiss()))
        self.page.route(PI + "/**", self.pi)
        self.page.route("https://tile.openstreetmap.org/**", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG))
        self.page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        self.page.goto(self.url)
        self.esperar("window.map && document.querySelectorAll('.pin').length>0", 15000)

    def tearDown(self):
        self.assertEqual(self.errores, [], "errores de JavaScript en la página")
        self.ctx.close()

    def pi(self, route):
        """Pi simulada: GET -> FAKE, POST /api/estado -> guarda y devuelve los estados."""
        req = route.request; path = req.url.split(PI, 1)[1].split("?")[0]
        if req.method == "OPTIONS": return route.fulfill(status=204, headers=self.cors())
        if req.method == "POST":
            d = json.loads(req.post_data or "{}"); self.posts.append((path, d))
            if path == "/api/estado": FAKE["/api/estados"][d["id"]] = {"st": d["st"], "stF": "2026-10-22", "by": d.get("by", ""), "ts": int(time.time())}
            return route.fulfill(status=200, headers=self.cors(), json=FAKE.get("/api/estados" if path == "/api/estado" else path, {"ok": True}))
        if path == "/api/eventos":                 # por defecto como la Pi actual (sin tiempo real); los tests lo cambian
            sse = getattr(self, "sse", None)
            if sse is None: return route.fulfill(status=404, headers=self.cors(), json={"error": "not found"})
            return route.fulfill(status=200, headers={**self.cors(), "Content-Type": "text/event-stream"}, body=sse)
        if path == "/api/ruta":                     # motor de rutas simulado (o caído si self.ruta_caida)
            if getattr(self, "ruta_caida", False): return route.fulfill(status=503, headers=self.cors(), json={"error": "motor no disponible"})
            return route.fulfill(status=200, headers=self.cors(), json={"opciones": [{"min": 23, "sale": "08:04", "llega": "08:27", "trans": 1, "legs": [
                {"m": "walk", "min": 8, "d": 600, "to": "Enghave Brygge St.", "path": [[55.649, 12.553], [55.653, 12.556]]},
                {"m": "Metro", "line": "M4", "head": "Orientkaj", "from": "Enghave Brygge St.", "to": "København H", "dep": "08:12", "arr": "08:16", "n": 2, "path": [[55.653, 12.556], [55.672, 12.564]]},
                {"m": "walk", "min": 11, "d": 800, "to": "Cris", "path": [[55.672, 12.564], [55.6935, 12.548]]}]}]})
        if path == "/api/img": return route.fulfill(status=200, headers=self.cors(), content_type="image/png", body=PNG)
        if path in FAKE: return route.fulfill(status=200, headers=self.cors(), json=FAKE[path])
        route.fulfill(status=404, headers=self.cors(), json={"error": "not found"})

    def cors(self): return {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type, X-Pisos-Key"}
    def js(self, expr, *a): return self.page.evaluate(expr, *a)
    def esperar(self, expr, timeout=5000):
        """Como wait_for_function, pero sin eval dentro de la página (la CSP lo prohíbe, y así los tests corren con CSP)."""
        fin = time.time() + timeout / 1000
        while time.time() < fin:
            try:
                if self.page.evaluate(f"() => !!({expr})"): return
            except Exception: pass
            self.page.wait_for_timeout(100)
        self.fail(f"no se cumplió en {timeout} ms: {expr}")


class TestCarga(Web):
    def test_un_pin_por_piso(self):
        self.assertEqual(self.js("Object.keys(markers).length"), self.js("PISOS.length"))
    def test_conecta_con_la_pi(self):
        self.esperar("document.getElementById('api-s')?.textContent.includes('✓')", 5000)
    def test_atribucion_osm(self):
        self.assertIn("OpenStreetMap", self.page.inner_text(".leaflet-control-attribution"))


class TestEsc(Web):
    def test_esc(self):
        self.assertEqual(self.js("esc(`<a href=\"x\" onclick='y'>&`)"), "&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;")
    def test_safe_url(self):
        r = self.js("['javascript:alert(1)','data:text/html,x','http://x.dk','https://www.boligportal.dk/a?b=1&c=2',' JaVaScRiPt:alert(1)',null].map(safeUrl)")
        self.assertEqual(r, ["#", "#", "#", "https://www.boligportal.dk/a?b=1&amp;c=2", "#", "#"])


class TestPropuestos(Web):
    def test_salen_en_lista_y_mapa(self):
        self.esperar("CANDS.length===1", 5000)
        self.assertIn("Piso de prueba", self.page.inner_text("#lares"))
        self.assertEqual(self.js("candLayer.getLayers().length"), 1)
    def test_datos_maliciosos_no_se_ejecutan(self):
        self.js("""CANDS=[{id:'1"><img src=x onerror=alert(1)>',link:'javascript:alert(2)',plano:'javascript:alert(3)',zona:'<b>z</b>',
                 titulo:'<img src=x onerror=alert(4)>',m2:'<svg onload=alert(5)>',vaer:'<i>4</i>',precio:1,st:'??',avisos:['<script>alert(6)</script>'],lat:55.7,lon:12.5}];
                 SHOW.nuevo=true; renderCands();""")
        self.page.wait_for_timeout(300)
        r = self.js("(()=>{const e=document.getElementById('lares');return {img:e.querySelectorAll('img,svg,script,b,i').length,href:[...e.querySelectorAll('a')].map(a=>a.getAttribute('href'))}})()")
        self.assertEqual(r, {"img": 0, "href": ["#", "#"]}); self.assertEqual(self.alerts, [])
    def test_me_gusta_envia_a_la_pi(self):
        self.esperar("CANDS.length===1", 5000)
        self.page.click("#lares [data-cst='7000001|gusta']")
        self.page.wait_for_timeout(500)
        self.assertIn(("/api/estado", {"id": "7000001", "st": "gusta", "by": self.js("YO")}), [(p, {k: d[k] for k in ("id", "st", "by")}) for p, d in self.posts])


class TestEstados(Web):
    def test_estado_remoto_cambia_el_pin(self):
        pid = self.js("PISOS.find(p=>p.st!=='contactado').id")
        self.js("id=>applyRemote({[id]:{st:'contactado',stF:'2026-10-22',by:'unai',ts:Math.floor(Date.now()/1000)}})", pid)
        self.assertEqual(self.js("id=>PISOS.find(p=>p.id===id).st", pid), "contactado")
        self.assertIn(self.js("ESTADOS.contactado.c"), self.js("id=>markers[id].getElement().innerHTML", pid))
    def test_aviso_si_lo_cambia_otra_persona(self):
        self.esperar("remoteReady")
        self.js("YO='jon'"); pid = self.js("PISOS.find(p=>p.st!=='gusta').id")
        self.js("id=>applyRemote({[id]:{st:'gusta',stF:'2026-10-22',by:'unai',ts:1}})", pid)
        self.assertIn("Unai marcó", self.page.inner_text("#toasts")); self.assertIn("«Nos gusta»", self.page.inner_text("#toasts"))
    def test_sin_aviso_si_lo_cambias_tu(self):
        self.esperar("remoteReady")
        self.js("YO='jon'"); pid = self.js("PISOS.find(p=>p.st!=='gusta').id")
        self.js("id=>applyRemote({[id]:{st:'gusta',stF:'2026-10-22',by:'jon',ts:1}})", pid)
        self.assertFalse(self.js("!!document.querySelector('#toasts div')"))
    def test_estado_desconocido_se_ignora(self):
        pid = self.js("PISOS[0].id"); antes = self.js("PISOS[0].st")
        self.js("id=>applyRemote({[id]:{st:'hackeado',stF:'x',by:'',ts:1}})", pid)
        self.assertEqual(self.js("PISOS[0].st"), antes)


class TestFicha(Web):
    def test_ficha_con_rutas(self):
        pid = self.js("PISOS[PISOS.length-1].id")
        self.js("id=>{select(id,false); showPiso(id);}", pid); self.page.wait_for_timeout(500)
        txt = self.page.inner_text("body")
        self.assertIn(self.js("id=>PISOS.find(p=>p.id===id).calle.split(' ')[0]", pid), txt)
        self.assertRegex(txt, r"\d+ min")
    def test_fotos_por_el_proxy(self):
        pid = self.js("PISOS.find(p=>p.fotos&&p.fotos.length).id")
        self.js("id=>{select(id,false); showPiso(id);}", pid); self.page.wait_for_timeout(500)
        srcs = self.js("[...document.querySelectorAll('.gal img')].map(i=>i.src)")
        self.assertTrue(srcs and all(s.startswith(self.js("API") + "/api/img?u=") for s in srcs))


class TestTiempoReal(Web):
    def test_version_nueva_recarga_y_mantiene_vista(self):
        self.js("map.setView([55.70,12.50],14,{animate:false})")
        self.js("lastAct=0; VER='version-vieja'")              # simula: página parada y otra versión publicada
        with self.page.expect_event("load", timeout=10000): self.js("checkVersion()")
        self.esperar("window.map && map.getZoom()===14", 10000)
    def test_si_se_usa_la_pagina_avisa_en_vez_de_recargar(self):
        self.js("lastAct=Date.now(); VER='version-vieja'; checkVersion()")
        self.page.wait_for_selector("#nuevaver", timeout=5000)


class TestTiempoRealPi(Web):
    def test_sin_eventos_sigue_por_sondeo(self):
        self.esperar("TREAL.fallos>=1", 5000)
        self.assertFalse(self.js("TREAL.on"))
        self.esperar("document.getElementById('api-s')?.textContent.includes('✓')", 5000)
    def test_evento_de_estado_cambia_el_pin(self):
        pid = self.js("PISOS.find(p=>p.st!=='contactado').id")
        self.sse = "retry: 100000\n: conectado\n\nevent: estados\ndata: " + json.dumps({pid: {"st": "contactado", "stF": "2026-10-22", "by": "unai", "ts": int(time.time())}}) + "\n\n"
        self.js("liveStop(); TREAL.fallos=0; liveStart()")
        self.esperar(f"PISOS.find(p=>p.id==='{pid}').st==='contactado'", 5000)
    def test_propuestos_no_se_repintan_si_no_cambian(self):
        self.esperar("CANDS.length===1", 5000)
        self.js("window._c=CANDS"); self.js("pullCandidatos()"); self.page.wait_for_timeout(500)
        self.assertTrue(self.js("CANDS===window._c"), "se sustituyeron los propuestos aunque no cambiaron")


class TestDestinoAnadido(Web):
    INIT = "localStorage.setItem('destinos', JSON.stringify([{id:'xcris',n:'Cris',addr:'Nørrebrogade',ll:[55.6935,12.548],who:'unai'}]));"
    def abrir(self):
        pid = self.js("PISOS[0].id"); self.js("id=>{select(id,false); showPiso(id);}", pid); return pid
    def test_transporte_de_la_pi(self):
        pid = self.abrir()
        self.esperar(f"RTS('{pid}','xcris-unai').length===1")
        self.page.wait_for_timeout(300)
        txt = self.page.inner_text("#sheet"); self.assertIn("M4", txt); self.assertIn("23 min", txt)
        self.js(f"drawRoute('{pid}','xcris-unai',false)")                 # dibujar con los trazados de la respuesta
    def test_si_la_pi_falla_queda_solo_andando(self):
        self.ruta_caida = True; pid = self.abrir()
        self.esperar("Object.values(TP).some(v=>v===null)")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.js(f"RTS('{pid}','xcris-unai').length"), 0)
        self.assertIn("sin transporte para esta hora", self.page.inner_text("#sheet"))


class TestQuien(Web):
    def test_lista_muestra_quien(self):
        pid = self.js("PISOS[0].id")
        self.js("id=>applyRemote({[id]:{st:'gusta',stF:'2026-10-23',by:'unai',ts:2}})", pid); self.js("renderList()")
        self.assertIn("🔵 Unai", self.page.inner_text("#list"))
    def test_pregunta_quien_eres_y_lo_guarda(self):
        self.js("YO=''"); pid = self.js("PISOS.find(p=>p.st!=='contactado').id")
        self.js("id=>{setEstado(id,'contactado')}", pid)
        self.page.wait_for_selector("#quiensoy"); self.page.click("#quiensoy [data-soy='unai']")
        self.esperar("YO==='unai'"); self.page.wait_for_timeout(300)
        self.assertIn(("/api/estado", pid, "contactado", "unai"), [(p, d["id"], d["st"], d.get("by")) for p, d in self.posts])
    def test_cancelar_no_cambia_nada(self):
        self.js("YO=''"); pid = self.js("PISOS.find(p=>p.st!=='contactado').id"); antes = self.js("id=>PISOS.find(p=>p.id===id).st", pid)
        self.js("id=>{setEstado(id,'contactado')}", pid)
        self.page.wait_for_selector("#quiensoy"); self.page.click("#quiensoy [data-soy='']")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.js("id=>PISOS.find(p=>p.id===id).st", pid), antes); self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()
