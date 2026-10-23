"""Tests de coherencia de los datos del mapa (data/*.json e index.html). Sin internet, ~1 s.
    python3 -m unittest tests.test_datos.TestRutas -v
"""
import json, os, re, sys, unittest, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def leer(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f: return f.read()
def cargar(f): return json.loads(leer("data", f))
HTML = leer("index.html")
APP = leer("app.js") if os.path.exists(os.path.join(ROOT, "app.js")) else HTML
PISOS, OUT, RUTAS, DIRECTO = cargar("pisos.json"), cargar("descartados.json"), cargar("rutas.json"), cargar("directo.json")
BBOX = (55.55, 55.86, 12.20, 12.72)                 # zona de Copenhague (igual que backend/routing.py)
ESTADOS = {"gusta", "nuevo", "contactado", "descartado"}
PORTALES = {"www.boligportal.dk", "en.lejebolig.dk", "www.lejebolig.dk", "boligportal.dk", "www.edc.dk"}
FOTO_HOSTS = {"image-lambda.boligportal.dk", "s3.eu-central-1.amazonaws.com"}   # los que acepta /api/img


sys.path.insert(0, os.path.join(ROOT, "tools"))
import sync_web


def const(nombre):
    """Valor de `const NOMBRE = ...;` dentro de la web (literal o JSON.parse('...'))."""
    try: return sync_web.valor(APP, nombre)
    except KeyError: raise AssertionError(f"no encuentro const {nombre} en la web")


class TestWebIgualQueData(unittest.TestCase):
    """Lo que se publica (index.html) tiene que ser lo mismo que data/*.json."""
    def test_pisos(self): self.assertEqual(const("PISOS"), PISOS)
    def test_rutas(self): self.assertEqual(const("RUTAS"), RUTAS)
    def test_directo(self): self.assertEqual(const("DIRECTO"), DIRECTO)


class TestFormatoRapido(unittest.TestCase):
    """Los datos grandes van como JSON.parse('...'): la página arranca ~21 % antes (tools/sync_web.py)."""
    def test_grandes_con_json_parse(self):
        for n in ("RUTAS", "DIRECTO", "RAIL", "BARRIOS"):
            with self.subTest(n=n): self.assertRegex(APP, r"(?m)^const %s=JSON\.parse\('" % n)
    def test_pisos_como_lo_lee_el_monitor(self):
        self.assertRegex(APP, r"const PISOS = (\[.*?\]);\n")
    def test_sin_cierre_de_script_dentro(self):
        s = max(re.findall(r"<script>(.*?)</script>", HTML, re.S), key=len)
        self.assertNotIn("</script", s.lower())
    def test_sync_no_cambia_nada(self):
        """index.html ya está sincronizado con data/ (si falla: python3 tools/sync_web.py)."""
        import tempfile, shutil
        d = tempfile.mkdtemp(); p = os.path.join(d, "index.html"); shutil.copy(os.path.join(ROOT, "index.html"), p)
        self.assertEqual(sync_web.sync(p), HTML); shutil.rmtree(d)


class TestPisos(unittest.TestCase):
    def test_ids_unicos_y_no_descartados(self):
        ids = [p["id"] for p in PISOS]
        self.assertEqual(len(ids), len(set(ids)), "ids repetidos")
        links_out = {o.get("link") for o in OUT}
        for p in PISOS:
            with self.subTest(id=p["id"]): self.assertNotIn(p["link"], links_out, "está en el mapa y en descartados")
    def test_campos_y_tipos(self):
        for p in PISOS:
            with self.subTest(id=p["id"]):
                self.assertRegex(p["id"], r"^[0-9]{4,12}$")
                self.assertIn(p["st"], ESTADOS)
                for k in ("alq", "m2", "vaer"): self.assertIsInstance(p[k], (int, float), k)
                self.assertRegex(p["stF"], r"^20\d\d-\d\d-\d\d$")
    def test_coordenadas_en_copenhague(self):
        for p in PISOS:
            with self.subTest(id=p["id"]):
                self.assertTrue(BBOX[0] <= p["lat"] <= BBOX[1] and BBOX[2] <= p["lon"] <= BBOX[3], (p["lat"], p["lon"]))
    def test_deposito_y_prepago_por_separado(self):
        """Regla del proyecto: dep y pre separados; entrada = dep + pre."""
        for p in PISOS:
            with self.subTest(id=p["id"]):
                if p.get("dep") is not None and p.get("pre") is not None and p.get("entrada") is not None:
                    self.assertEqual(p["entrada"], p["dep"] + p["pre"])
    def test_enlaces_https_a_portales(self):
        for p in PISOS:
            with self.subTest(id=p["id"]):
                u = urllib.parse.urlparse(p["link"]); self.assertEqual(u.scheme, "https"); self.assertIn(u.hostname, PORTALES)
    def test_fotos_permitidas_por_el_proxy(self):
        for p in PISOS:
            for f in p.get("fotos") or []:
                with self.subTest(id=p["id"], foto=f[:60]):
                    u = urllib.parse.urlparse(f); self.assertEqual(u.scheme, "https"); self.assertIn(u.hostname, FOTO_HOSTS)


class TestRutas(unittest.TestCase):
    def test_indices_validos(self):
        nr, np_ = len(RUTAS["routes"]), len(RUTAS["paths"])
        for r in RUTAS["routes"]:
            for L in r["legs"]: self.assertLess(L["p"], np_)
        for mo, days in RUTAS["ix"].items():
            for day, times in days.items():
                for t, lims in times.items():
                    for lim, flats in lims.items():
                        for fid, dests in flats.items():
                            for k, j in dests.items():
                                for i in (j if isinstance(j, list) else [j]): self.assertLess(i, nr, (mo, day, t, lim, fid, k))
    def test_cada_piso_tiene_rutas(self):
        """Todos los pisos del mapa tienen ruta a cada destino (al menos con 30 min andando por tramo)."""
        dest = [d["k"] for d in RUTAS["dest"]]
        for mo in ("foot", "bike"):
            for p in PISOS:
                with self.subTest(modo=mo, id=p["id"]):
                    hay = {k for day in RUTAS["days"] for t in RUTAS["times"]
                           for k in RUTAS["ix"].get(mo, {}).get(day, {}).get(t, {}).get("30", RUTAS["ix"].get(mo, {}).get(day, {}).get(t, {}).get(30, {})).get(p["id"], {})}
                    self.assertEqual(set(dest) - hay, set(), "destinos sin ruta")
    def test_cada_piso_tiene_directo(self):
        for mo in ("bike", "foot"):
            for p in PISOS:
                with self.subTest(modo=mo, id=p["id"]):
                    self.assertIn(p["id"], DIRECTO[mo])
                    for k, v in DIRECTO[mo][p["id"]].items(): self.assertTrue(v and v["min"] > 0, k)
    def test_tiempos_razonables(self):
        for r in RUTAS["routes"]:
            self.assertTrue(0 < r["min"] < 240, r["min"])


class TestSeguridadHtml(unittest.TestCase):
    def test_csp(self):
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', HTML)
        self.assertTrue(m, "falta la CSP"); csp = m.group(1)
        for d in ("object-src 'none'", "base-uri 'none'", "form-action 'none'"): self.assertIn(d, csp)
        self.assertIn("raspberrypi.tail9f1069.ts.net:8443", csp.split("connect-src")[1].split(";")[0])
    def test_scripts_externos_con_sri(self):
        for tag in re.findall(r"<script[^>]+src=\"https://[^>]+>", HTML):
            with self.subTest(tag=tag[:80]): self.assertIn('integrity="sha384-', tag); self.assertIn('crossorigin="anonymous"', tag)
    def test_sin_manejadores_inline(self):
        self.assertIsNone(re.search(r"<[a-z]+[^>]*\son[a-z]+=\"", HTML), "atributos onclick= etc. (los bloquearía la CSP)")
    def test_api_y_clave_publicas_esperadas(self):
        self.assertIn('const API="https://raspberrypi.tail9f1069.ts.net:8443"', APP)


if __name__ == "__main__":
    unittest.main()
