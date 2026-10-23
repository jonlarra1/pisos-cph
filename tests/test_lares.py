"""Tests del vigilante de Lares (lares/piso_watch_monitor.py) sin internet: los portales se simulan.
    python3 -m unittest tests.test_lares -v
"""
import contextlib, importlib.util, io, json, os, sqlite3, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cargar_modulo():
    tmp = tempfile.mkdtemp(prefix="lares-test-")
    os.environ["PISO_WATCH_BASE"] = os.path.join(tmp, "watch"); os.environ["PISOS_BRIDGE"] = os.path.join(tmp, "bridge")
    spec = importlib.util.spec_from_file_location("pwm", os.path.join(ROOT, "lares", "piso_watch_monitor.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    (m.BRIDGE / "config.json").write_text(json.dumps({"filtros": {"precio_max": 23000, "dorm_min": 3, "disp_desde": "2026-12-18",
                                                                  "disp_hasta": "2027-01-15", "compartir": True, "contrato_largo": True},
                                                      "zonas": {"Herlev": True, "Valby": False}}), encoding="utf-8")
    return m


def anuncio(i, **kw):
    """Anuncio que pasa los filtros salvo que se cambie algo."""
    return {"portal": "Boligportal", "id": str(i), "link": f"https://www.boligportal.dk/x-id-{i}", "titulo": "4 værelses lejlighed",
            "lugar": "Hyrdindestien 9 2730 Herlev", "precio": 16000, "m2": 100, "vaer": 4, "disp": "2027-01-01",
            "contrato_meses": 0, "texto": "Delevenlig", **kw}


def caso_edc(i, **kw):
    """Un anuncio tal como lo da la API de EDC. i < 0 = copia de Boligportal con id -i."""
    v = lambda x, u="": {"value": x, "unitCode": u}
    a = {"caseNumber": str(i), "isEdcCase": True, "isRented": False, "estateTypeName": "Lejlighed", "address": "Hyrdindestien 9",
         "zipCode": "2730", "city": "Herlev", "rent": v(16000.0), "livingArea": v(100.0), "rooms": v(4.0),
         "urlPath": f"/leje/lejlighed/2730-herlev/hyrdindestien-9/{i}/", "geoCoordinates": {"latitude": 55.72, "longitude": 12.44}}
    if i < 0:
        a.update(caseNumber=f"ex{-i}", isEdcCase=False, urlPath=f"https://www.boligportal.dk/lejligheder/herlev/100m2-4-vaer-id-{-i}?EDC=true&utm_source=edc")
    return {**a, **kw}


class Pasada:
    """Simula los portales: páginas[portal] = lista de páginas (cada una, lista de ids)."""
    def __init__(self, m, paginas, estados=None, mapa=None):
        self.m, self.paginas, self.pedidas = m, paginas, []
        self.estados, self.mapa, self.comprobados = estados or {}, mapa or [], []   # id -> ("retirado", motivo); pisos.json
    def fetch(self, url, timeout=20):
        if "github.io" in url: return json.dumps(self.mapa) if url.endswith("/pisos.json") else "[]"
        self.pedidas.append(url)
        if url.startswith(self.m.EDC_API):                      # EDC: JSON de su API, páginas de paginas["edc"]
            pags = self.paginas.get("edc", []); n = int(url.rsplit("pageNr=", 1)[1]) - 1
            return json.dumps({"totalPages": len(pags), "items": [caso_edc(x) for x in (pags[n] if n < len(pags) else [])]})
        return url
    def parser(self, portal):
        def p(url):
            i = self.m.PAGINAS[portal][0]
            n = next(k for k in range(20) if i(k) == url)
            pags = self.paginas.get(portal, [])
            return {str(x): anuncio(x) for x in (pags[n] if n < len(pags) else [])}
        return p
    def estado(self, link):
        i = self.m.num_id(link); self.comprobados.append(i); return self.estados.get(i, ("activo", "ACTIVE_APPROVED"))
    def __call__(self):
        with mock.patch.object(self.m, "fetch", self.fetch), \
             mock.patch.object(self.m, "parse_boligportal", self.parser("boligportal")), \
             mock.patch.object(self.m, "parse_lejebolig", self.parser("lejebolig")), \
             mock.patch.object(self.m, "detalle", lambda it: {}), mock.patch("time.sleep"), \
             mock.patch.object(self.m, "estado_anuncio", self.estado), \
             contextlib.redirect_stdout(io.StringIO()) as self.salida:          # las líneas «NUEVO |…» que lee Lares
            self.m.STATS.clear(); self.m.main()
        return json.loads((self.m.BRIDGE / "candidatos.json").read_text(encoding="utf-8"))


class TestPaginas(unittest.TestCase):
    def setUp(self): self.m = cargar_modulo()
    def cuenta(self, pas, portal): return sum(1 for u in pas.pedidas if portal in u and not u.startswith(self.m.EDC_API))

    def test_lee_el_minimo_aunque_no_haya_nada_nuevo(self):
        pas = Pasada(self.m, {"boligportal": [[1], [2], [3], [4]], "lejebolig": [[10], [11], [12]]}); pas()
        pas2 = Pasada(self.m, {"boligportal": [[1], [2], [3], [4]], "lejebolig": [[10], [11], [12]]}); pas2()
        self.assertEqual(self.cuenta(pas2, "boligportal"), 3); self.assertEqual(self.cuenta(pas2, "lejebolig"), 2)
    def test_sigue_mientras_haya_nuevos(self):
        pas = Pasada(self.m, {"boligportal": [[i] for i in range(1, 7)]}); pas()      # 6 páginas, todas nuevas
        self.assertEqual(self.cuenta(pas, "boligportal"), 7)                            # 6 con anuncios + 1 vacía
    def test_no_pasa_del_maximo(self):
        pas = Pasada(self.m, {"boligportal": [[i] for i in range(1, 30)]}); pas()
        self.assertEqual(self.cuenta(pas, "boligportal"), self.m.PAGINAS["boligportal"][2])
    def test_se_para_al_llegar_a_lo_conocido(self):
        Pasada(self.m, {"boligportal": [[1], [2], [3], [4], [5]]})()                    # primera pasada: ve 1..5
        pas = Pasada(self.m, {"boligportal": [[100], [101], [102], [1], [2], [3]]}); pas()   # entran 3 nuevos arriba
        self.assertEqual(self.cuenta(pas, "boligportal"), 4)                            # 3 nuevas + 1 conocida -> para


class TestRecuerdo(unittest.TestCase):
    def setUp(self): self.m = cargar_modulo()
    def test_candidato_que_baja_de_pagina_sigue_propuesto(self):
        Pasada(self.m, {"boligportal": [[1], [2], [3]]})()
        d = Pasada(self.m, {"boligportal": [[7], [8], [9]]})()                         # 1, 2 y 3 ya no salen
        ids = {c["id"] for c in d["pisos"]}
        self.assertTrue({"1", "2", "3", "7", "8", "9"} <= ids)
        viejo = next(c for c in d["pisos"] if c["id"] == "1")
        self.assertTrue(any(a.startswith("no visto desde") for a in viejo["avisos"]))
    def test_se_olvida_tras_recuerdo_dias(self):
        Pasada(self.m, {"boligportal": [[1], [2], [3]]})()
        hace = (datetime.now(timezone.utc) - timedelta(days=self.m.RECUERDO_DIAS + 1)).isoformat(timespec="seconds")
        with sqlite3.connect(self.m.DB) as con: con.execute("UPDATE v2_eval SET last_seen=? WHERE id='1'", (hace,))
        d = Pasada(self.m, {"boligportal": [[7], [8], [9]]})()
        self.assertNotIn("1", {c["id"] for c in d["pisos"]}); self.assertIn("2", {c["id"] for c in d["pisos"]})
    def test_aviso_no_se_repite(self):
        Pasada(self.m, {"boligportal": [[1], [2], [3]]})()
        Pasada(self.m, {"boligportal": [[7], [8], [9]]})()
        d = Pasada(self.m, {"boligportal": [[7], [8], [9]]})()
        viejo = next(c for c in d["pisos"] if c["id"] == "1")
        self.assertEqual(sum(a.startswith("no visto desde") for a in viejo["avisos"]), 1)


class Respuesta:
    """Respuesta HTTP simulada para estado_anuncio."""
    def __init__(self, code=200, url="", html=""): self.status, self._url, self._html = code, url, html.encode()
    def geturl(self): return self._url
    def read(self): return self._html
    def __enter__(self): return self
    def __exit__(self, *a): pass


class TestEstadoAnuncio(unittest.TestCase):
    """Reconoce las respuestas reales de los portales (vistas el 23-oct-2026)."""
    def setUp(self): self.m = cargar_modulo()
    def con(self, resp):
        def abrir(req, timeout=20):
            if isinstance(resp, Exception): raise resp
            return resp
        with mock.patch("urllib.request.urlopen", abrir): return self.m.estado_anuncio(self.link)
    BP = "https://www.boligportal.dk/lejligheder/københavn/93m2-4-vaer-id-5432947"
    def test_boligportal_alquilado(self):
        self.link = self.BP
        est, mot = self.con(Respuesta(200, "https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/3-v%C3%A6relser/?ad_rented_out_redirect=RENTED_OUT_APPROVED"))
        self.assertEqual(est, "retirado"); self.assertIn("udlejet", mot)
    def test_boligportal_activo_y_otro_estado(self):
        self.link = self.BP
        store = lambda st: '<script id="store" type="application/json">' + json.dumps({"ad": {"id": 5432947, "state": st}, "otros": [{"id": 1, "state": "X"}]}) + "</script>"
        self.assertEqual(self.con(Respuesta(200, self.BP, store("ACTIVE_APPROVED")))[0], "activo")
        self.assertEqual(self.con(Respuesta(200, self.BP, store("INACTIVE")))[0], "retirado")
        self.assertEqual(self.con(Respuesta(200, self.BP, "<html>sin datos</html>"))[0], "desconocido")
    def test_lejebolig(self):
        import urllib.error
        self.link = "https://en.lejebolig.dk/lejebolig/1912232"
        self.assertEqual(self.con(urllib.error.HTTPError(self.link, 404, "Not Found", {}, None))[0], "retirado")
        self.assertEqual(self.con(Respuesta(200, "https://en.lejebolig.dk/lejebolig/1912232/roedovre-port"))[0], "activo")
        self.assertEqual(self.con(urllib.error.HTTPError(self.link, 503, "Busy", {}, None))[0], "desconocido")
    def test_fallo_de_red_no_retira(self):
        self.link = self.BP; self.assertEqual(self.con(TimeoutError())[0], "desconocido")
    def test_url_con_o_danesa(self):
        self.assertNotIn("ø", self.m.url_ok(self.BP))


class TestRetirados(unittest.TestCase):
    def setUp(self): self.m = cargar_modulo()
    def retirados(self): return json.loads((self.m.BRIDGE / "retirados.json").read_text(encoding="utf-8"))["pisos"]
    def test_piso_del_mapa_retirado_va_a_retirados_json(self):
        mapa = [{"id": "5432947", "link": "https://www.boligportal.dk/x-id-5432947"}]
        Pasada(self.m, {}, estados={"5432947": ("retirado", "marcado como alquilado («udlejet»)")}, mapa=mapa)()
        r = self.retirados(); self.assertIn("5432947", r); self.assertIn("udlejet", r["5432947"]["motivo"])
    def test_propuesto_retirado_queda_en_cuarentena_y_luego_sale(self):
        Pasada(self.m, {"boligportal": [[1], [2], [3]]})()
        d = Pasada(self.m, {"boligportal": [[7], [8], [9]]}, estados={"1": ("retirado", "el portal ya no lo publica")})()
        c = next(x for x in d["pisos"] if x["id"] == "1")
        self.assertTrue(c["retirado"]); self.assertTrue(any(a.startswith("🚫") for a in c["avisos"]))
        hace = (datetime.now(timezone.utc) - timedelta(days=self.m.CUARENTENA_DIAS + 1)).isoformat(timespec="seconds")
        with sqlite3.connect(self.m.DB) as con: con.execute("UPDATE v2_activo SET desde=? WHERE id='1'", (hace,))
        d = Pasada(self.m, {"boligportal": [[7], [8], [9]]})()
        self.assertNotIn("1", {x["id"] for x in d["pisos"]})
    def test_como_mucho_max_activo_por_pasada_y_no_repite_retirados(self):
        mapa = [{"id": str(i), "link": f"https://www.boligportal.dk/x-id-{i}"} for i in range(100, 120)]
        p = Pasada(self.m, {}, estados={"100": ("retirado", "x")}, mapa=mapa); p()
        self.assertEqual(len(p.comprobados), self.m.MAX_ACTIVO)
        p2 = Pasada(self.m, {}, mapa=mapa); p2()
        self.assertNotIn("100", p2.comprobados)                                  # ya retirado: no se vuelve a pedir
        self.assertTrue(set(p2.comprobados).isdisjoint(set(p.comprobados) - {"100"}) or len(mapa) < 2 * self.m.MAX_ACTIVO)


class TestEDC(unittest.TestCase):
    FICHA = ("<html><body><h1>Hyrdindestien 9</h1><div>Ledig fra d. 1. jan. 2027</div><dl><dt>Rum/værelser</dt><dd>4/3</dd>"
             "<dt>Lejeperiode</dt><dd>Ubegrænset</dd><dt>Depositum</dt><dd>3 måneders husleje, 48.000 kr.</dd>"
             "<dt>Forudbetalt leje</dt><dd>1 måneds husleje, 16.000 kr.</dd><dt>Aconto Varmeudgifter</dt><dd>500 kr.</dd>"
             "<dt>Aconto Vandudgift</dt><dd>300 kr.</dd></dl><p>Boligen er delevenlig.</p></body></html>")
    def setUp(self): self.m = cargar_modulo()
    def test_parse_edc_propios_copias_y_alquilados(self):
        out = self.m.parse_edc(json.dumps({"items": [caso_edc(11810045), caso_edc(-5663068), caso_edc(11810046, isRented=True)]}))
        self.assertEqual(set(out), {"11810045", "5663068"})
        self.assertEqual(out["11810045"]["portal"], "EDC"); self.assertEqual(out["11810045"]["precio"], 16000)
        self.assertEqual(out["11810045"]["vaer"], 4); self.assertTrue(out["11810045"]["link"].startswith("https://www.edc.dk/leje/"))
        self.assertEqual(out["5663068"]["portal"], "Boligportal"); self.assertNotIn("utm_", out["5663068"]["link"])
    def test_ficha_edc(self):
        with mock.patch.object(self.m, "fetch", lambda u, timeout=20: self.FICHA):
            d = self.m.detalle({"portal": "EDC", "link": "https://www.edc.dk/leje/x/11810045/"})
        self.assertEqual((d["dep"], d["pre"], d["aconto"], d["contrato_meses"], d["vaer"], d["dorm"], d["disp"]),
                         (48000, 16000, 800, 0, 4, 3, "2027-01-01"))
    def test_num_id_edc(self):
        self.assertEqual(self.m.num_id("https://www.edc.dk/leje/lejlighed/2450-koebenhavn-sv/a-c-meyers-vaenge-134-1-tv/11810045/"), "11810045")
    def test_urls_solo_zonas_activas_y_cortas(self):
        F = {"vaer_min": 4, "precio_max": 23000}
        u = self.m.edc_urls({"Herlev": True, "Valby": False}, F)
        self.assertEqual(len(u), 1); self.assertTrue(u[0].endswith("postnr=2730")); self.assertIn("rum=4", u[0]); self.assertIn("leje=0-23000", u[0])
        todas = self.m.edc_urls({z: True for z in self.m.ZONA_POSTNR}, F)
        self.assertTrue(all(len(x) <= self.m.EDC_MAX_URL for x in todas))
        cod = {c for x in todas for c in x.split("postnr=")[1].split(",")}
        self.assertEqual(cod, {str(c) for z in self.m.ZONA_POSTNR.values() for c in z})
        self.assertEqual(self.m.edc_urls({}, F), [])
    def test_dormitorios_de_la_ficha(self):
        F = TestFiltros.F
        ok, mot, _ = self.m.evaluar(anuncio(1, dorm=2), F, TestFiltros.Z); self.assertFalse(ok); self.assertIn("ficha: 2 dormitorios", mot)
        self.assertTrue(self.m.evaluar(anuncio(1, dorm=3), F, TestFiltros.Z)[0])
    def test_pasada_con_edc(self):
        pas = Pasada(self.m, {"boligportal": [[7]], "edc": [[11810045, -7, -8]]}); c = pas()
        por_id = {p["id"]: p for p in c["pisos"]}
        self.assertEqual(por_id["11810045"]["portal"], "EDC")
        self.assertEqual(por_id["7"]["link"], "https://www.boligportal.dk/x-id-7")       # la versión de Boligportal manda
        self.assertIn("8", por_id)                                                       # copia que Boligportal no trajo
        self.assertFalse([e for e in c["errores"] if e.startswith("edc")])
    def test_edc_sin_ficha_no_se_propone_y_espera(self):
        self.m.MAX_DETAIL_EDC = 1
        c = Pasada(self.m, {"edc": [[11810001, 11810002, -9]]})()
        self.assertEqual([p["id"] for p in c["pisos"]], ["11810001"])                  # 1 ficha por pasada: los otros esperan
        self.assertEqual(self.m.STATS["edc_pendientes"], 2)
        c = Pasada(self.m, {"edc": [[11810001, 11810002, -9]]})()
        self.assertEqual(sorted(p["id"] for p in c["pisos"]), ["11810001", "11810002"])  # el propio de EDC antes que la copia
    def test_edc_para_al_llegar_a_lo_conocido(self):
        self.m.EDC_PAGINAS = (1, 3)
        Pasada(self.m, {"edc": [[11810001]] * 3})()
        pas = Pasada(self.m, {"edc": [[11810001]] * 3}); pas()
        self.assertEqual(sum(1 for u in pas.pedidas if u.startswith(self.m.EDC_API)), 1)


class TestFiltros(unittest.TestCase):
    F = {"precio_max": 23000, "vaer_min": 4, "dorm_min": 3, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15", "compartir": True, "contrato_largo": True}
    Z = {"Herlev": True, "Valby": False}
    def setUp(self): self.m = cargar_modulo()
    def ev(self, **kw): return self.m.evaluar(anuncio(1, **kw), self.F, self.Z)
    def test_pasa(self): self.assertTrue(self.ev()[0])
    def test_rechazos(self):
        for kw, motivo in (({"precio": 30000}, "precio"), ({"vaer": 3}, "værelser"), ({"lugar": "Valby"}, "zona excluida"),
                           ({"lugar": "Odense"}, "zona no reconocida"), ({"disp": "2026-11-01"}, "libre el"), ({"disp": "ya"}, "libre ya"),
                           ({"contrato_meses": 6}, "contrato corto"), ({"texto": "Ikke delevenlig"}, "no permite compartir"),
                           ({"titulo": "Room for rent"}, "habitación")):
            with self.subTest(kw=kw):
                ok, mot, _ = self.ev(**kw); self.assertFalse(ok); self.assertIn(motivo, mot)
    def test_avisos(self):
        _, _, av = self.ev(disp=None, texto="")
        self.assertIn("fecha de entrada sin confirmar", av); self.assertIn("compartir sin confirmar", av)


if __name__ == "__main__":
    unittest.main()
