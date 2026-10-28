"""Tests del modelo único de anuncios (backend/anuncios.py) y del lector de portales (backend/portales.py).
Sin internet: las fichas son copias reales guardadas en tests/fixtures/.
    python3 -m unittest tests.test_anuncios -v
"""
import os, sqlite3, sys, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import anuncios as A          # noqa: E402
import portales as P          # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
FICHAS = {
    "https://en.lejebolig.dk/lejebolig/1812509": "lejebolig_1812509.html",
    "https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/103m2-4-vaer-id-5481965": "boligportal_5481965.html",
    "https://www.edc.dk/leje/lejlighed/2600-glostrup/grannaalen-1-st-2/11810121/": "edc_11810121.html",
}
FILTROS = {"precio_max": 23000, "dorm_min": 3, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15",
           "compartir": True, "contrato_largo": True, "amueblado": "igual"}
ZONAS = {"Herlev": True, "Brønshøj": True, "Glostrup": True, "Østerbro": False}


def descargar_falso(url, timeout=20):
    f = FICHAS.get(url)
    if f is None: return 404, url, ""
    with open(os.path.join(FIX, f), encoding="utf-8") as fh: return 200, url, fh.read()


def con_nueva():
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    A.crear_tablas(con); return con


def piso(**kw):
    """Anuncio mínimo que pasa todos los filtros."""
    d = {"portal": "Boligportal", "pid": "1", "link": "https://www.boligportal.dk/en/rental-apartments/1", "titulo": "Lejlighed",
         "precio": 16000, "vaer": 4, "dorm": 3, "disp": "2027-01-01", "contrato_meses": 0, "compartible": True, "zona": "Herlev"}
    d.update(kw); return d


class TestPortales(unittest.TestCase):
    def setUp(self): self._orig, P.descargar = P.descargar, descargar_falso
    def tearDown(self): P.descargar = self._orig

    def test_lejebolig_trae_dinero_separado_fecha_y_fotos(self):
        d = P.ficha("https://en.lejebolig.dk/lejebolig/1812509")
        self.assertEqual((d["portal"], d["pid"], d["precio"], d["dep"], d["pre"], d["aconto"]), ("Lejebolig", "1812509", 16600, 49800, 16600, 1200))
        self.assertEqual((d["m2"], d["vaer"], d["disp"], d["contrato_meses"], d["cp"]), (109, 4, "2026-12-01", 0, "2730"))
        self.assertEqual(len(d["fotos"]), 14)

    def test_boligportal_trae_coordenadas_y_rasgos(self):
        d = P.ficha("https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/103m2-4-vaer-id-5481965")
        self.assertEqual((d["precio"], d["dep"], d["pre"], d["compartible"], d["amueblado"]), (16300, 48900, 16300, True, False))
        self.assertAlmostEqual(d["lat"], 55.711, places=2)
        self.assertEqual(d["link"], "https://www.boligportal.dk/en/rental-apartments/5481965")

    def test_edc_trae_dormitorios_reales_y_coordenadas(self):
        d = P.ficha("https://www.edc.dk/leje/lejlighed/2600-glostrup/grannaalen-1-st-2/11810121/")
        self.assertEqual((d["precio"], d["vaer"], d["dorm"], d["dep"], d["pre"], d["aconto"]), (13800, 4, 3, 41400, 13800, 1000))
        self.assertEqual((d["disp"], d["ciudad"], d["cp"]), ("2027-01-01", "Glostrup", "2600"))
        self.assertAlmostEqual(d["lat"], 55.682, places=2)

    def test_ficha_devuelve_siempre_los_mismos_campos(self):
        for u in FICHAS:
            self.assertEqual(tuple(P.ficha(u)), P.CAMPOS)

    def test_debe_fallar_enlaces_que_no_son_anuncios(self):
        for u in ("", "javascript:alert(1)", "http://en.lejebolig.dk/lejebolig/1812509", "https://evil.com/lejebolig/1812509",
                  "https://x@en.lejebolig.dk/lejebolig/1812509", "https://en.lejebolig.dk:8443/lejebolig/1812509",
                  "https://en.lejebolig.dk/lejebolig/1812509.evil.com", "https://www.boligportal.dk/" + "a" * 600):
            with self.assertRaises(P.AnuncioNoValido, msg=u): P.identificar(u)

    def test_debe_fallar_anuncio_retirado(self):
        with self.assertRaises(P.AnuncioNoValido): P.ficha("https://en.lejebolig.dk/lejebolig/9999999")

    def test_estado_404_es_retirado_y_error_de_red_es_desconocido(self):
        self.assertEqual(P.estado("https://en.lejebolig.dk/lejebolig/9999999")[0], "retirado")
        def roto(url, timeout=20): raise OSError("sin red")
        P.descargar = roto
        self.assertEqual(P.estado("https://en.lejebolig.dk/lejebolig/1812509")[0], "desconocido")

    def test_zona_por_texto_y_por_codigo_postal(self):
        self.assertEqual(P.zona_de("Hørkær, Herlev"), "Herlev")
        self.assertEqual(P.zona_de("Gadelandet 24S", "2700"), "Brønshøj")
        self.assertEqual(P.zona_de("H.C. Ørsteds Vej", "1879"), "Frederiksberg")
        self.assertIsNone(P.zona_de("Aarhus C", "8000"))


class TestEvaluar(unittest.TestCase):
    def test_pasa_un_piso_bueno(self):
        self.assertEqual(A.evaluar(piso(), FILTROS, ZONAS)[0], [])

    def test_da_todos_los_motivos_no_solo_el_primero(self):
        motivos, _ = A.evaluar(piso(precio=30000, zona="Østerbro", disp="2026-11-01"), FILTROS, ZONAS)
        self.assertEqual(len(motivos), 3)

    def test_dormitorios_reales_mandan_sobre_vaerelser(self):
        self.assertTrue(A.evaluar(piso(vaer=4, dorm=2), FILTROS, ZONAS)[0])
        self.assertTrue(A.evaluar(piso(vaer=3, dorm=None), FILTROS, ZONAS)[0])

    def test_no_compartir_por_texto(self):
        self.assertIn("no permite compartir", A.evaluar(piso(compartible=None, texto="Bofællesskab ikke muligt"), FILTROS, ZONAS)[0])


class TestEntregar(unittest.TestCase):
    def setUp(self): self.con = con_nueva()

    def test_vigilante_propone_o_rechaza(self):
        self.assertEqual(A.entregar(self.con, piso(), "vigilante", "vigilante", FILTROS, ZONAS)["estado"], "propuesto")
        a = A.entregar(self.con, piso(pid="2", precio=40000), "vigilante", "vigilante", FILTROS, ZONAS)
        self.assertEqual((a["estado"], a["motivo"]), ("rechazado", "precio 40000 > 23000"))

    def test_enlace_de_persona_o_lares_se_propone_aunque_no_pase(self):
        for i, origen in enumerate(("jon", "lares")):
            a = A.entregar(self.con, piso(pid=f"9{i}", precio=40000), origen, origen, FILTROS, ZONAS)
            self.assertEqual(a["estado"], "propuesto")
            self.assertIn("no pasa: precio 40000 > 23000", a["avisos"])

    def test_volver_a_entregar_no_duplica_y_completa_datos(self):
        A.entregar(self.con, piso(dep=48000), "vigilante", "vigilante", FILTROS, ZONAS)
        a = A.entregar(self.con, piso(dep=None, pre=16000), "vigilante", "vigilante", FILTROS, ZONAS)
        self.assertEqual((a["datos"]["dep"], a["datos"]["pre"]), (48000, 16000))
        self.assertEqual(len(A.listar(self.con)), 1)

    def test_si_lo_anade_una_persona_tras_el_vigilante_pasa_a_manual(self):
        A.entregar(self.con, piso(pid="2", precio=40000), "vigilante", "vigilante", FILTROS, ZONAS)   # rechazado
        a = A.entregar(self.con, piso(pid="2", precio=40000), "web", "web:jon", FILTROS, ZONAS)
        self.assertEqual((a["estado"], a["origen"]), ("propuesto", "web"))                        # sale en «Propuestos»
        a = A.entregar(self.con, piso(pid="2", precio=40000), "vigilante", "vigilante", FILTROS, ZONAS)
        self.assertEqual((a["estado"], a["origen"]), ("propuesto", "web"))                        # el vigilante no lo devuelve
    def test_la_decision_de_una_persona_no_la_pisa_el_vigilante(self):
        A.entregar(self.con, piso(), "vigilante", "vigilante", FILTROS, ZONAS)
        A.cambiar_estado(self.con, "Boligportal:1", "gusta", "web", "jon")
        a = A.entregar(self.con, piso(precio=40000), "vigilante", "vigilante", FILTROS, ZONAS)
        self.assertEqual(a["estado"], "gusta")

    def test_si_cambian_los_filtros_un_rechazado_se_reevalua(self):
        A.entregar(self.con, piso(precio=24000), "vigilante", "vigilante", FILTROS, ZONAS)
        a = A.entregar(self.con, piso(precio=24000), "vigilante", "vigilante", {**FILTROS, "precio_max": 25000}, ZONAS)
        self.assertEqual(a["estado"], "propuesto")

    def test_un_manual_sigue_siendo_manual_si_luego_lo_ve_el_vigilante(self):
        A.entregar(self.con, piso(precio=40000), "jon", "jon", FILTROS, ZONAS)
        self.assertEqual(A.entregar(self.con, piso(precio=40000), "vigilante", "vigilante", FILTROS, ZONAS)["estado"], "propuesto")

    def test_debe_fallar_origen_desconocido(self):
        with self.assertRaises(A.NoValido): A.entregar(self.con, piso(), "hacker", "x", FILTROS, ZONAS)


class TestReevaluar(unittest.TestCase):
    """Regla de Jon (28-oct) al cambiar filtros o zonas."""
    def setUp(self): self.con = con_nueva()
    SIN_GLOSTRUP = {**ZONAS, "Glostrup": False}

    def test_filtro_mas_estricto_no_quita_un_propuesto_solo_le_pone_aviso(self):
        A.entregar(self.con, piso(zona="Glostrup"), "vigilante", "vigilante", FILTROS, ZONAS)
        r = A.reevaluar(self.con, FILTROS, self.SIN_GLOSTRUP, "filtros:jon")
        a = A.obtener(self.con, "Boligportal:1")
        self.assertEqual(a["estado"], "propuesto")
        self.assertIn("no pasa: zona excluida: Glostrup", a["avisos"])
        self.assertEqual((r["cambiados"], r["a_propuesto"]), (1, 0))

    def test_el_vigilante_tampoco_lo_quita_al_volver_a_verlo(self):
        A.entregar(self.con, piso(zona="Glostrup"), "vigilante", "vigilante", FILTROS, ZONAS)
        a = A.entregar(self.con, piso(zona="Glostrup"), "vigilante", "vigilante", FILTROS, self.SIN_GLOSTRUP)
        self.assertEqual(a["estado"], "propuesto")

    def test_uno_nuevo_de_una_zona_excluida_se_rechaza(self):
        a = A.entregar(self.con, piso(zona="Glostrup"), "vigilante", "vigilante", FILTROS, self.SIN_GLOSTRUP)
        self.assertEqual((a["estado"], a["motivo"]), ("rechazado", "zona excluida: Glostrup"))

    def test_filtro_mas_flojo_recupera_un_rechazado(self):
        A.entregar(self.con, piso(precio=24000), "vigilante", "vigilante", FILTROS, ZONAS)
        r = A.reevaluar(self.con, {**FILTROS, "precio_max": 25000}, ZONAS, "filtros:jon")
        a = A.obtener(self.con, "Boligportal:1")
        self.assertEqual((a["estado"], r["a_propuesto"]), ("propuesto", 1))
        self.assertEqual(A.historial(self.con, a["uid"])[0]["motivo"], "cambiaron los filtros")

    def test_vuestras_decisiones_no_se_tocan(self):
        A.entregar(self.con, piso(), "vigilante", "vigilante", FILTROS, ZONAS)
        A.cambiar_estado(self.con, "Boligportal:1", "descartado", "web", "jon", "no nos gusta")
        A.reevaluar(self.con, {**FILTROS, "precio_max": 10000}, ZONAS, "filtros:jon")
        self.assertEqual(A.obtener(self.con, "Boligportal:1")["estado"], "descartado")

    def test_rechazados_viejos_no_vuelven(self):
        viejo = time.time() - (A.REEVALUAR_DIAS + 1) * 86400
        A.entregar(self.con, piso(precio=24000), "vigilante", "vigilante", FILTROS, ZONAS, ahora=viejo)
        self.assertEqual(A.reevaluar(self.con, {**FILTROS, "precio_max": 25000}, ZONAS, "x")["revisados"], 0)

    def test_sin_cambios_no_escribe_nada(self):
        A.entregar(self.con, piso(), "vigilante", "vigilante", FILTROS, ZONAS, ahora=1000)
        self.assertEqual(A.reevaluar(self.con, FILTROS, ZONAS, "x", ahora=2000)["cambiados"], 0)
        self.assertEqual(A.obtener(self.con, "Boligportal:1")["actualizado"], 1000)


class TestPermisos(unittest.TestCase):
    def setUp(self):
        self.con = con_nueva()
        A.entregar(self.con, piso(), "vigilante", "vigilante", FILTROS, ZONAS)

    def test_nadie_puede_borrar(self):
        self.assertFalse(any("borr" in op or "delete" in op for ops in A.PERMISOS.values() for op in ops))
        self.assertFalse([f for f in dir(A) if "borrar" in f or "delete" in f])

    def test_scripts_no_cambian_estados(self):
        for cliente in ("vigilante", "comprobador", "panel"):
            with self.assertRaises(A.Prohibido): A.cambiar_estado(self.con, "Boligportal:1", "descartado", cliente, cliente)

    def test_lares_no_puede_tocar_filtros_ni_comprobar(self):
        for op in ("filtros", "comprobar", "entregar"):
            with self.assertRaises(A.Prohibido): A.permitir("lares", op)
        A.permitir("lares", "entregar_enlace")

    def test_nadie_pone_estados_del_sistema_a_mano(self):
        for e in ("rechazado", "retirado"):
            with self.assertRaises(A.Prohibido): A.cambiar_estado(self.con, "Boligportal:1", e, "web", "jon")

    def test_tope_de_cambios_de_lares(self):
        for i in range(A.TOPE_CAMBIOS_HORA["lares"]):
            A.cambiar_estado(self.con, "Boligportal:1", "gusta" if i % 2 else "descartado", "lares", "lares")
        with self.assertRaises(A.Tope): A.cambiar_estado(self.con, "Boligportal:1", "contactado", "lares", "lares")
        A.cambiar_estado(self.con, "Boligportal:1", "contactado", "web", "jon")          # a la web no le afecta

    def test_historial_guarda_quien_y_que(self):
        A.cambiar_estado(self.con, "Boligportal:1", "gusta", "web", "unai", "buen precio")
        h = A.historial(self.con, "Boligportal:1")
        self.assertEqual((h[0]["de"], h[0]["a"], h[0]["quien"], h[0]["motivo"]), ("propuesto", "gusta", "web:unai", "buen precio"))
        self.assertEqual(h[-1]["a"], "propuesto")                                       # el alta también queda


class TestComprobar(unittest.TestCase):
    def setUp(self):
        self.con = con_nueva()
        A.entregar(self.con, piso(), "vigilante", "vigilante", FILTROS, ZONAS)

    def test_retirado_sin_decidir_pasa_a_retirado_y_vuelve_si_reaparece(self):
        self.assertEqual(A.comprobar(self.con, "Boligportal:1", "retirado", "404")["estado"], "retirado")
        self.assertEqual(A.comprobar(self.con, "Boligportal:1", "activo")["estado"], "propuesto")

    def test_retirado_no_pisa_vuestra_decision_solo_avisa(self):
        A.cambiar_estado(self.con, "Boligportal:1", "contactado", "web", "jon")
        a = A.comprobar(self.con, "Boligportal:1", "retirado", "udlejet")
        self.assertEqual(a["estado"], "contactado")
        self.assertIn("el portal dice: udlejet", a["avisos"])

    def test_desconocido_no_cambia_nada(self):
        self.assertEqual(A.comprobar(self.con, "Boligportal:1", "desconocido", "timeout")["estado"], "propuesto")


class TestUnir(unittest.TestCase):
    def test_mismo_piso_en_dos_portales(self):
        con = con_nueva()
        A.entregar(con, piso(), "vigilante", "vigilante", FILTROS, ZONAS)
        A.entregar(con, piso(portal="Lejebolig", pid="7", link="https://en.lejebolig.dk/lejebolig/7"), "vigilante", "vigilante", FILTROS, ZONAS)
        p = A.unir(con, "Boligportal:1", "Lejebolig:7", "jon")
        self.assertEqual(len(p["datos"]["enlaces"]), 2)
        self.assertEqual(A.obtener(con, "Lejebolig:7")["uid"], "Boligportal:1")      # el alias lleva al principal
        self.assertEqual(A.entregar(con, piso(portal="Lejebolig", pid="7"), "vigilante", "vigilante", FILTROS, ZONAS)["uid"], "Boligportal:1")


if __name__ == "__main__":
    unittest.main()
