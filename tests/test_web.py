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
        self.errores, self.posts, self.alerts, self.rutas_q = [], [], [], []
        self.page.on("pageerror", lambda e: self.errores.append(str(e)))
        self.page.on("dialog", lambda d: (self.alerts.append(d.message), d.dismiss()))
        self.page.route(PI + "/**", self.pi)
        self.page.route("https://tile.openstreetmap.org/**", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG))
        self.page.route("https://routing.openstreetmap.de/**", lambda r: r.fulfill(status=200, json={"code": "Ok", "routes": [
            {"distance": 4000, "duration": 900, "geometry": {"coordinates": [[12.585, 55.679], [12.57, 55.665], [12.553, 55.649]]}}]}))   # bici/andando por calle
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
            import urllib.parse as up
            self.rutas_q.append({k: v[0] for k, v in up.parse_qs(req.url.split("?", 1)[1]).items()})
            if getattr(self, "ruta_caida", False): return route.fulfill(status=503, headers=self.cors(), json={"error": "motor no disponible"})
            if getattr(self, "ruta_retenida", None) is not None: return self.ruta_retenida.append(route)   # el test la suelta luego
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
    def test_ficha_del_propuesto(self):
        self.esperar("CANDS.length===1", 5000)
        self.js("""Object.assign(CANDS[0],{fotos:['https://image-lambda.boligportal.dk/abc'],amueblado:false,compartible:true,ascensor:true,
                   dep:48000,pre:16000,descripcion:'Bo ved vandet'}); renderCands();""")
        self.page.click("#lares .item[data-id='7000001']")
        self.esperar("document.getElementById('sheet').classList.contains('open')&&sheet.dataset.id==='7000001'")
        self.esperar("rutas_ok=Object.values(TP).some(v=>Array.isArray(v))")
        self.page.wait_for_timeout(300)
        t = self.page.inner_text("#sheet")
        for x in ("Piso de prueba", "Sin amueblar", "Ascensor", "48.000", "16.000", "Bo ved vandet", "23 min", "M4"): self.assertIn(x, t)
        self.assertTrue(any(q["olat"] == "55.72" for q in self.rutas_q))             # rutas desde el propuesto, con el motor
        self.assertIn("/api/img?u=", self.js("document.querySelector('#sheet .gal img').src"))
        self.js("YO='jon'"); self.page.click("#sheet [data-cst='7000001|gusta']")    # el estado se cambia desde la ficha
        self.esperar("CANDS[0].st==='gusta'")
        self.assertIn(("/api/estado", "7000001"), [(p, d.get("id")) for p, d in self.posts])
    def test_propuesto_sin_ubicacion_se_ubica_por_la_calle(self):
        self.esperar("CANDS.length===1", 5000)
        q = []
        self.page.route("https://nominatim.openstreetmap.org/**", lambda r: (q.append(r.request.url), r.fulfill(status=200, headers=self.cors(), json=[{"lat": "55.7712", "lon": "12.5040"}]))[1])
        self.js("Object.assign(CANDS[0],{lat:null,lon:null,portal:'Lejebolig',titulo:'Lejlighed på Karen Schaks Vej i Kongens Lyngby',lugar:'Apartment in Kongens Lyngby'}); GEO={}; renderCands();")
        self.assertEqual(self.js("document.querySelectorAll('#lares .item[data-id=\"7000001\"]').length"), 1)   # sale en la lista igual
        self.page.click("#lares .item[data-id='7000001']")
        self.esperar("CANDS[0]._geo&&document.querySelector('#sheet').innerText.includes('Karen Schaks Vej')", 8000)
        self.assertIn("Karen+Schaks+Vej", q[0]); self.assertIn("aproximada", self.page.inner_text("#sheet"))
        self.esperar("Object.values(TP).some(v=>Array.isArray(v))")                  # y ya hay rutas (orientativas)
    def test_ficha_con_datos_maliciosos(self):
        self.esperar("CANDS.length===1", 5000)
        self.js("""CANDS=[{id:'9',link:'javascript:alert(2)',zona:'<b>z</b>',titulo:'<img src=x onerror=alert(4)>',m2:'<svg onload=alert(5)>',
                 vaer:'<i>4</i>',precio:'<b>1</b>',dep:'<i>x</i>',st:'nuevo',avisos:['<script>alert(6)</script>'],descripcion:'<img src=y onerror=alert(7)>',
                 fotos:['javascript:alert(8)'],lat:55.7,lon:12.5}]; renderCands(); openCand('9');""")
        self.page.wait_for_timeout(300)
        n = self.js("(()=>{const e=document.querySelector('#sheet .sh-head,#sheet .sh-body')?.parentElement||sheet; return e.querySelectorAll('.sh-body b img,.sh-body svg,.sh-body script,.sh-head img,.sh-head svg,.note img,.kv img').length})()")
        self.assertEqual(n, 0)
    def test_datos_maliciosos_no_se_ejecutan(self):
        self.js("""CANDS=[{id:'1"><img src=x onerror=alert(1)>',link:'javascript:alert(2)',plano:'javascript:alert(3)',zona:'<b>z</b>',
                 titulo:'<img src=x onerror=alert(4)>',m2:'<svg onload=alert(5)>',vaer:'<i>4</i>',precio:1,st:'??',avisos:['<script>alert(6)</script>'],lat:55.7,lon:12.5},
                 {id:'2',link:'javascript:alert(2)',zona:'<b>z</b>',titulo:'<img src=x onerror=alert(4)>',m2:'<svg onload=alert(5)>',vaer:'<i>4</i>',
                  precio:'<b>1</b>',portal:'<i>p</i>',disp:'<b>d</b>',st:'??',avisos:['<script>alert(6)</script>'],lat:55.7,lon:12.5}];
                 SHOW.nuevo=true; renderCands();""")
        self.page.wait_for_timeout(300)
        r = self.js("(()=>{const e=document.getElementById('lares');return {img:e.querySelectorAll('img,svg,script,b,i').length,items:[...e.querySelectorAll('.item')].filter(x=>!PISOS.some(p=>p.id===x.dataset.id)).length}})()")
        self.assertEqual(r, {"img": 0, "items": 1}); self.assertEqual(self.alerts, [])     # el del id con HTML ni se pinta
    def test_me_gusta_envia_a_la_pi(self):
        self.esperar("CANDS.length===1", 5000)
        self.js("YO='jon'")                              # ya ha dicho quién es (si no, sale «¿Quién eres?»)
        self.page.click("#lares .item[data-id='7000001']")                         # se abre la ficha, como los del mapa
        self.esperar("sheet.dataset.id==='7000001'"); self.page.click("#sheet [data-cst='7000001|gusta']")
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


class TestVisualizacion(Web):
    def etiquetas(self): return self.js("barLbl.getLayers().map(l=>l.options.icon.options.html.replace(/<[^>]+>/g,''))")
    def setUp(self):
        super().setUp(); self.js("document.getElementById('d-ver').open=true")   # los botones están en «Visualización»
    def test_ocultar_estados_quita_etiquetas_de_grupos_vacios(self):
        todas = self.etiquetas(); self.assertTrue(todas)
        for k in ("gusta", "nuevo", "contactado", "descartado"): self.page.click(f"#showst [data-show='{k}']")
        self.assertEqual(self.etiquetas(), [])                                   # sin pisos visibles, sin etiquetas
        self.page.click("#showst [data-show='gusta']")
        con_gusta = self.js("BORD.filter(b=>BAR[b].some(p=>p.st==='gusta'&&!expired(p))).length")
        self.assertEqual(len(self.etiquetas()), con_gusta)
    def test_al_recargar_con_estados_ocultos(self):
        for k in ("gusta", "nuevo", "contactado", "descartado"): self.page.click(f"#showst [data-show='{k}']")
        self.page.reload(); self.esperar("window.map&&typeof barLbl!=='undefined'&&document.querySelectorAll('.pin').length>=0")
        self.assertEqual(self.etiquetas(), [])


class TestRetirados(Web):
    """Anuncio retirado del portal: Lares lo descarta; aviso, motivo y cuarentena de 48 h."""
    def test_piso_del_mapa_retirado(self):
        self.esperar("remoteReady"); pid = self.js("PISOS.find(p=>p.st==='gusta').id")
        self.js("id=>applyRemote({[id]:{st:'descartado',stF:'2026-10-23',by:'lares',ts:Math.floor(Date.now()/1000),motivo:'Anuncio retirado del portal (2026-10-23): marcado como alquilado'}})", pid)
        self.assertIn("Han retirado el anuncio", self.page.inner_text("#toasts"))
        self.js("document.querySelector('#out details').open=true")
        out = self.page.inner_text("#out"); self.assertIn("Anuncio retirado del portal", out); self.assertIn("recuperar", out)
        self.assertIn("Lares", self.js("id=>{showPiso(id); return document.getElementById('sheet').innerText}", pid))
    def test_propuesto_retirado_en_cuarentena(self):
        self.esperar("CANDS.length===1")
        FAKE_C = dict(FAKE["/api/candidatos"]["pisos"][0], st="descartado", by="lares", ts=int(time.time()), motivo="Anuncio retirado del portal (2026-10-23): el portal ya no lo publica")
        self.js("c=>{candSig=''; CANDS=[{...c, st:'nuevo'}];}", FAKE_C)               # estado anterior: sin decidir
        self.page.route(PI + "/api/candidatos", lambda r: r.fulfill(status=200, headers=self.cors(), json={"actualizado": "x", "pisos": [FAKE_C]}))
        self.js("pullCandidatos()"); self.esperar("CANDS[0]&&CANDS[0].st==='descartado'")
        self.assertIn("Han retirado el anuncio propuesto", self.page.inner_text("#toasts"))
        self.js("document.querySelector('#out details').open=true")
        out = self.page.inner_text("#out"); self.assertIn("Piso de prueba", out); self.assertIn("el portal ya no lo publica", out)
        self.assertEqual(self.js("candLayer.getLayers().length"), 0)                 # ya no sale como punto naranja
    def test_propuesto_caducado_desaparece(self):
        self.esperar("CANDS.length===1")
        viejo = dict(FAKE["/api/candidatos"]["pisos"][0], st="descartado", by="lares", ts=int(time.time()) - 49 * 3600)
        self.js("c=>{CANDS=[c]; renderOut();}", viejo)
        self.assertNotIn("Piso de prueba", self.page.inner_text("#out"))


class TestDestinoAnadido(Web):
    INIT = "localStorage.setItem('destinos', JSON.stringify([{id:'xcris',n:'Cris',addr:'Nørrebrogade',ll:[55.6935,12.548],who:'unai'}]));"
    def abrir(self):
        self.js("tripDay='lun'")                                       # laborable: las filas fijas usan las precalculadas
        pid = self.js("PISOS[0].id"); self.js("id=>{select(id,false); showPiso(id);}", pid); return pid
    def test_transporte_de_la_pi(self):
        pid = self.abrir()
        self.esperar(f"RTS('{pid}','xcris-unai').length===1")
        self.page.wait_for_timeout(300)
        txt = self.page.inner_text("#sheet"); self.assertIn("M4", txt); self.assertIn("23 min", txt)
        self.js(f"drawRoute('{pid}','xcris-unai',false)")                 # dibujar con los trazados de la respuesta
    def test_tramos_rectos_del_motor_se_dibujan_por_calle(self):
        pid = self.abrir(); self.esperar(f"RTS('{pid}','xcris-unai').length===1")
        self.js(f"drawRoute('{pid}','xcris-unai',false)")
        self.esperar("Object.values(LIVE).some(v=>v&&v.path)")                       # OSRM (simulado) ha contestado
        self.js(f"drawRoute('{pid}','xcris-unai',false)")
        tramos = self.js("routeLayer.getLayers().filter(l=>l.options&&/walk|bike/.test(l.options.className||'')).map(l=>l.getLatLngs().length)")
        self.assertTrue(tramos); self.assertTrue(all(n > 2 for n in tramos), tramos)    # ningún tramo en línea recta (2 puntos)
    def test_barra_de_progreso(self):
        self.ruta_retenida = []; pid = self.abrir()
        self.esperar("document.querySelector('#sheet .tp-bar')")
        self.assertIn("0 de 1 ruta (0 %)", self.page.inner_text("#sheet .tp-bar"))
        r = self.ruta_retenida.pop(); self.ruta_retenida = None; self.pi(r)          # la Pi contesta
        self.esperar(f"RTS('{pid}','xcris-unai').length===1 && !document.querySelector('#sheet .tp-bar')")
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


class TestMomentos(Web):
    """Mañana = precalculado; vuelta del trabajo, noche y madrugada = motor de la Pi con el sentido correcto."""
    def momento(self, k, dia="vie"):
        self.js(f"tripDay='{dia}'"); self.page.click(f"#mom [data-mom='{k}']"); self.page.wait_for_timeout(200)
        pid = self.js("PISOS[0].id"); self.js("id=>{select(id,false); showPiso(id);}", pid); return pid
    def test_manana_no_pregunta_a_la_pi(self):
        self.momento("manana", "mar"); self.page.wait_for_timeout(800)
        self.assertEqual(self.rutas_q, []); self.assertIn("Nordic Solar", self.page.inner_text("#sheet"))
    def test_noche_al_centro(self):
        pid = self.momento("noche")
        self.esperar("document.querySelector('#sheet') && /Kongens Nytorv/.test(document.querySelector('#sheet').innerText)")
        self.esperar(f"RTS('{pid}','centro-jon').length===1")
        q = self.rutas_q[0]; self.assertEqual(q["modo"], "llegar"); self.assertAlmostEqual(float(q["dlat"]), 55.678939, 4)
        self.assertEqual(q["hora"], "22:00")
    def test_madrugada_vuelve_desde_el_centro_al_dia_siguiente(self):
        pid = self.momento("madrugada", "vie"); self.esperar(f"RTS('{pid}','centro-unai').length===1")
        q = self.rutas_q[0]; self.assertEqual(q["modo"], "salir"); self.assertAlmostEqual(float(q["olat"]), 55.678939, 4)
        import datetime; self.assertEqual(datetime.date.fromisoformat(q["fecha"]).weekday(), 5)   # viernes noche -> sábado
        self.assertIn("noche del viernes", self.page.inner_text("#sheet").lower())
    def test_vuelta_del_trabajo_sale_de_nordic_solar(self):
        pid = self.momento("tarde", "mar"); self.esperar(f"RTS('{pid}','ns-jon').length===1")
        q = next(x for x in self.rutas_q if abs(float(x["olat"]) - 55.7335) < 0.01)
        self.assertEqual((q["modo"], q["hora"]), ("salir", "17:00"))
    def test_avisa_de_la_espera_de_noche(self):
        self.js("MOMENTO='madrugada'; tripTime='03:00'")
        self.assertEqual(self.js("espera({sale:'04:54'})"), 114); self.assertIn("1 h 54 min", self.js("esperaTxt({sale:'04:54'})"))
        self.assertEqual(self.js("esperaTxt({sale:'03:05'})"), "")                      # 5 min: normal
        self.js("tripTime='23:30'"); self.assertEqual(self.js("espera({sale:'00:10'})"), 40)   # pasa de medianoche
        self.js("MOMENTO='manana'"); self.assertEqual(self.js("espera({sale:'09:00'})"), 0)    # «llegar antes de»: no aplica
    def test_de_noche_con_espera_sale_solo_en_bici(self):
        self.js("tripTime='02:00'"); pid = self.momento("madrugada")          # la ruta simulada sale 08:04 -> mucha espera
        self.esperar(f"RTS('{pid}','centro-jon').length===1")
        self.esperar("/solo en bici \\d+ min/.test(document.querySelector('#sheet .rt').innerText)")
        self.assertIn("sin transporte: 12 min", self.page.inner_text("#rt-steps"))          # 4 km a 20 km/h
    def test_fin_de_semana_por_la_manana_usa_el_motor(self):
        pid = self.momento("manana", "sáb"); self.esperar(f"RTS('{pid}','ns-jon').length===1")
    def test_etiqueta_centro_solo_de_noche(self):
        self.page.click("#mom [data-mom='noche']"); self.assertIn("Centro", self.js("extraLayer.getLayers().map(l=>l.getElement()?.innerText||'').join()"))
        self.page.click("#mom [data-mom='manana']"); self.assertNotIn("Centro", self.js("extraLayer.getLayers().map(l=>l.getElement()?.innerText||'').join()"))
    def test_lista_de_barrios_no_lanza_peticiones(self):
        self.page.click("#mom [data-mom='noche']"); self.js("renderList()"); self.page.wait_for_timeout(800)
        self.assertEqual(self.rutas_q, [])
    def test_se_recuerda_al_recargar(self):
        self.page.click("#mom [data-mom='madrugada']"); self.page.reload(); self.esperar("window.map&&MOMENTO==='madrugada'")
        self.assertEqual(self.js("tripTime"), "02:00"); self.assertTrue(self.js("!!rowOf(routeDest)"))


if __name__ == "__main__":
    unittest.main()
