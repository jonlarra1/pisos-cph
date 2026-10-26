"""Tests de la API v2 (backend/api_v2.py) por HTTP contra un servidor real en 127.0.0.1, sin internet:
las fichas de los portales salen de tests/fixtures/ y la geocodificación está desactivada.
    python3 -m unittest tests.test_api_v2 -v
"""
import json, unittest, urllib.parse

from tests.test_backend import app, pedir, Base
from tests.test_anuncios import descargar_falso
import api_v2, portales                                      # noqa: E402  (ya en sys.path por test_backend)

LB = "https://en.lejebolig.dk/lejebolig/1812509"
BP = "https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/103m2-4-vaer-id-5481965"
EDC = "https://www.edc.dk/leje/lejlighed/2600-glostrup/grannaalen-1-st-2/11810121/"
WEB, LARES, VIG, COMP = "clave-test", "clave-lares-test", "clave-vigilante-test", "clave-comprobador-test"


def post(path, d, clave, origin=None):
    h = {"X-Pisos-Key": clave}
    if origin: h["Origin"] = origin
    return pedir(path, d, h) if origin else _sin_origin(path, d, h)


def _sin_origin(path, d, h):
    import urllib.request, urllib.error
    from tests.test_backend import Servidor
    req = urllib.request.Request(Servidor.url() + path, data=json.dumps(d).encode(), headers={**h, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=10); return r.status, r.headers, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read(); e.close(); return e.code, e.headers, json.loads(raw or b"{}")


def uid_url(uid): return "/api/v2/anuncios/" + urllib.parse.quote(uid)


class BaseV2(Base):
    def setUp(self):
        super().setUp()
        self._d, self._g = portales.descargar, portales.geocodificar
        portales.descargar = descargar_falso
        portales.geocodificar = lambda q: (55.72, 12.43)            # sin internet
        with app.db() as c:
            c.execute("DELETE FROM cambio"); c.execute("DELETE FROM alias"); c.execute("DELETE FROM anuncio")
            c.execute("UPDATE zonas SET permitida=1 WHERE zona IN ('Herlev','Brønshøj','Glostrup')")

    def tearDown(self):
        portales.descargar, portales.geocodificar = self._d, self._g


class TestAnadirEnlace(BaseV2):
    def test_lares_anade_un_enlace_y_sale_en_la_lista(self):
        code, _, a = post("/api/v2/enlaces", {"url": EDC}, LARES)
        self.assertEqual((code, a["uid"], a["estado"], a["origen"]), (200, "EDC:11810121", "propuesto", "lares"))
        self.assertEqual(a["datos"]["dorm"], 3)
        code, _, lista = pedir("/api/v2/anuncios?estado=propuesto")
        self.assertEqual([x["uid"] for x in lista["anuncios"]], ["EDC:11810121"])
        self.assertNotIn("texto", lista["anuncios"][0]["datos"])                 # la lista no lleva el texto largo

    def test_se_propone_aunque_no_pase_con_el_motivo_como_aviso(self):
        code, _, a = post("/api/v2/enlaces", {"url": LB, "quien": "jon"}, WEB, "https://jonlarra1.github.io")
        self.assertEqual((code, a["estado"]), (200, "propuesto"))                # libre el 1-dic: fuera de fechas
        self.assertTrue(any(x.startswith("no pasa: libre el 2026-12-01") for x in a["avisos"]))
        self.assertEqual((a["lat"], a["precision"]), (55.72, "calle"))           # geocodificado por calle (Lejebolig no da número)

    def test_anadir_dos_veces_no_duplica(self):
        post("/api/v2/enlaces", {"url": BP}, LARES)
        code, _, a = post("/api/v2/enlaces", {"url": BP}, LARES)
        self.assertTrue(a["ya_estaba"])
        self.assertEqual(len(pedir("/api/v2/anuncios")[2]["anuncios"]), 1)

    def test_ficha_completa_con_historial(self):
        post("/api/v2/enlaces", {"url": BP}, LARES)
        code, _, a = pedir(uid_url("Boligportal:5481965"))
        self.assertEqual(code, 200)
        self.assertTrue(a["datos"]["texto"])
        self.assertEqual(a["historial"][0]["quien"], "lares:lares")


class TestEstados(BaseV2):
    def setUp(self):
        super().setUp(); post("/api/v2/enlaces", {"url": BP}, LARES)

    def test_web_y_lares_cambian_estado_con_historial(self):
        code, _, a = post(uid_url("Boligportal:5481965") + "/estado", {"estado": "gusta", "quien": "unai"}, WEB, "https://jonlarra1.github.io")
        self.assertEqual((code, a["estado"]), (200, "gusta"))
        code, _, a = post(uid_url("Boligportal:5481965") + "/estado", {"estado": "contactado", "motivo": "le escribí"}, LARES)
        self.assertEqual(a["estado"], "contactado")
        h = pedir(uid_url("Boligportal:5481965"))[2]["historial"]
        self.assertEqual([x["a"] for x in h[:2]], ["contactado", "gusta"])

    def test_comprobador_no_pisa_vuestra_decision(self):
        post(uid_url("Boligportal:5481965") + "/estado", {"estado": "gusta"}, LARES)
        code, _, a = post(uid_url("Boligportal:5481965") + "/comprobacion", {"resultado": "retirado", "motivo": "udlejet"}, COMP)
        self.assertEqual((code, a["estado"]), (200, "gusta"))
        self.assertIn("el portal dice: udlejet", a["avisos"])


class TestDebeFallar(BaseV2):
    def test_sin_clave_o_clave_mala(self):
        for clave in ("", "otra-clave-cualquiera", WEB[:-1]):
            self.assertEqual(post("/api/v2/enlaces", {"url": BP}, clave)[0], 403, clave)

    def test_clave_de_la_web_sin_su_origen(self):
        self.assertEqual(post("/api/v2/enlaces", {"url": BP}, WEB)[0], 403)
        self.assertEqual(post("/api/v2/enlaces", {"url": BP}, WEB, "https://evil.example")[0], 403)

    def test_clave_de_lares_desde_otra_web(self):
        self.assertEqual(post("/api/v2/enlaces", {"url": BP}, LARES, "https://evil.example")[0], 403)

    def test_cada_cliente_solo_lo_suyo(self):
        post("/api/v2/enlaces", {"url": BP}, LARES)
        u = uid_url("Boligportal:5481965")
        self.assertEqual(post(u + "/estado", {"estado": "descartado"}, VIG)[0], 403)
        self.assertEqual(post(u + "/estado", {"estado": "descartado"}, COMP)[0], 403)
        self.assertEqual(post(u + "/comprobacion", {"resultado": "retirado"}, LARES)[0], 403)
        self.assertEqual(post("/api/v2/enlaces", {"url": BP}, VIG)[0], 403)
        self.assertEqual(post("/api/v2/anuncios", {"datos": {"link": BP, "precio": 1}}, LARES)[0], 403)
        self.assertEqual(post(u + "/estado", {"estado": "retirado"}, LARES)[0], 403)
        self.assertEqual(post(u + "/nota", {"texto": "hola hola"}, VIG)[0], 403)
        self.assertEqual(post(u + "/dormitorios", {"dorm": 3}, COMP)[0], 403)
        self.assertEqual(post(u + "/dormitorios", {"dorm": "3; DROP"}, LARES)[0], 400)
        self.assertEqual(post(u + "/nota", {"texto": ""}, LARES)[0], 400)

    def test_datos_malos(self):
        self.assertEqual(post("/api/v2/enlaces", {"url": "https://evil.com/x"}, LARES)[0], 400)
        self.assertEqual(post("/api/v2/enlaces", {"url": "https://en.lejebolig.dk/lejebolig/9999999"}, LARES)[0], 400)
        self.assertEqual(post("/api/v2/enlaces", ["no", "dict"], LARES)[0], 400)
        self.assertEqual(post("/api/v2/enlaces", {"url": BP, "quien": "<script>"}, LARES)[0], 400)
        self.assertEqual(post(uid_url("Boligportal:5481965") + "/estado", {"estado": "inventado"}, LARES)[0], 400)
        self.assertEqual(post("/api/v2/anuncios/..%2F..%2Fetc/estado", {"estado": "gusta"}, LARES)[0], 404)
        self.assertEqual(pedir("/api/v2/anuncios/Hacker:1")[0], 400)
        self.assertEqual(pedir("/api/v2/anuncios?estado=borrado")[0], 400)

    def test_web_solo_puede_firmar_como_jon_o_unai(self):
        self.assertEqual(post("/api/v2/enlaces", {"url": BP, "quien": "lares"}, WEB, "https://jonlarra1.github.io")[0], 400)

    def test_cuerpo_enorme(self):
        self.assertEqual(post("/api/v2/enlaces", {"url": BP, "x": "a" * 5000}, LARES)[0], 413)

    def test_no_hay_forma_de_borrar(self):
        post("/api/v2/enlaces", {"url": BP}, LARES)
        for metodo in ("DELETE", "PUT", "PATCH"):
            code = pedir(uid_url("Boligportal:5481965"), {}, {"X-Pisos-Key": LARES}, method=metodo)[0]
            self.assertIn(code, (404, 405, 501), metodo)
        self.assertEqual(pedir(uid_url("Boligportal:5481965"))[0], 200)


class TestVigilante(BaseV2):
    def test_entrega_y_queda_en_cola_para_leer_la_ficha(self):
        datos = {"link": BP, "precio": 16300, "vaer": 4, "titulo": "Fed 4-værelses lejlighed", "ciudad": "Brønshøj",
                 "lat": 55.711, "lon": 12.48, "disp": "2027-01-01"}
        code, _, r = post("/api/v2/anuncios", {"datos": datos}, VIG)
        self.assertEqual((code, r["estado"]), (200, "propuesto"))
        self.assertIn("Boligportal:5481965", api_v2._en_cola)

    def test_datos_raros_se_limpian(self):
        datos = {"link": BP, "precio": 16300, "vaer": "4; DROP TABLE", "lat": 99, "lon": 0, "fotos": ["javascript:x", "https://ok/1.jpg"],
                 "titulo": "x" * 999, "disp": "mañana"}
        post("/api/v2/anuncios", {"datos": datos}, VIG)
        a = pedir(uid_url("Boligportal:5481965"))[2]
        self.assertEqual((a["datos"]["vaer"], a["lat"], a["datos"]["disp"]), (None, None, None))
        self.assertEqual(a["datos"]["fotos"], ["https://ok/1.jpg"])
        self.assertEqual(len(a["datos"]["titulo"]), 200)


class TestPuenteConLaWeb(BaseV2):
    """Hasta la fase 3, la web usa /api/candidatos y /api/estado: lo de la base única tiene que verse allí."""

    ORIGEN = "https://jonlarra1.github.io"

    def setUp(self):
        super().setUp()
        with app.db() as c:
            c.execute("DELETE FROM estado WHERE id IN ('5481965', '11810121')")

    def ids_propuestos(self):
        return {str(p["id"]): p for p in pedir("/api/candidatos")[2]["pisos"]}

    def test_lo_que_anade_lares_sale_en_propuestos(self):
        post("/api/v2/enlaces", {"url": EDC}, LARES)
        p = self.ids_propuestos().get("11810121")
        self.assertIsNotNone(p)
        self.assertEqual((p["st"], p["origen"], p["portal"], p["dorm"]), ("nuevo", "lares", "EDC", 3))
        self.assertTrue(p["lat"] and p["link"].startswith("https://www.edc.dk/"))

    def test_lo_del_vigilante_no_sale_por_el_puente(self):
        post("/api/v2/anuncios", {"datos": {"link": BP, "precio": 16300, "vaer": 4, "lat": 55.7, "lon": 12.4}}, VIG)
        self.assertNotIn("5481965", self.ids_propuestos())

    def test_la_web_cambia_el_estado_y_la_base_unica_tambien(self):
        post("/api/v2/enlaces", {"url": BP}, LARES)
        code, _, _ = post("/api/estado", {"id": "5481965", "st": "gusta", "by": "unai"}, WEB, self.ORIGEN)
        self.assertEqual(code, 200)
        a = pedir(uid_url("Boligportal:5481965"))[2]
        self.assertEqual((a["estado"], a["historial"][0]["quien"]), ("gusta", "web:unai"))

    def test_lares_cambia_el_estado_y_la_web_lo_ve(self):
        post("/api/v2/enlaces", {"url": BP}, LARES)
        post(uid_url("Boligportal:5481965") + "/estado", {"estado": "descartado", "motivo": "pedido por Jon"}, LARES)
        e = pedir("/api/estados")[2]["5481965"]
        self.assertEqual((e["st"], e["by"]), ("descartado", "lares"))
        self.assertEqual(self.ids_propuestos()["5481965"]["st"], "descartado")

    def test_un_piso_ya_descartado_en_la_web_no_vuelve_como_propuesto(self):
        post("/api/estado", {"id": "5481965", "st": "descartado", "by": "jon"}, WEB, self.ORIGEN)
        code, _, a = post("/api/v2/enlaces", {"url": BP}, LARES)
        self.assertEqual((code, a["estado"]), (200, "descartado"))


class TestRetiradoPorElVigilante(BaseV2):
    def test_retirados_json_llega_a_la_base_unica_sin_pisar_decisiones(self):
        import json as _json
        post("/api/v2/enlaces", {"url": BP}, LARES)
        post("/api/v2/enlaces", {"url": EDC}, LARES)
        post(uid_url("EDC:11810121") + "/estado", {"estado": "gusta"}, LARES)
        with app.db() as c:
            c.execute("DELETE FROM retirado_aplicado WHERE id IN ('5481965', '11810121')")
        retirados = {"pisos": {"5481965": {"desde": "2026-10-26", "motivo": "udlejet"},
                               "11810121": {"desde": "2026-10-26", "motivo": "404"}}}
        with open(app.os.path.join(app.BRIDGE, "retirados.json"), "w", encoding="utf-8") as f:
            _json.dump(retirados, f)
        app.aplicar_retirados()
        bp, edc = pedir(uid_url("Boligportal:5481965"))[2], pedir(uid_url("EDC:11810121"))[2]
        self.assertEqual(bp["estado"], "retirado")                              # propuesto -> retirado
        self.assertEqual(edc["estado"], "gusta")                                # vuestra decisión no se toca
        self.assertTrue(any("el portal dice" in a for a in edc["avisos"]))


class TestPanel(BaseV2):
    def test_check_anuncios(self):
        post("/api/v2/enlaces", {"url": EDC}, LARES)
        ok, msg = app.check_anuncios()
        self.assertTrue(ok); self.assertIn("1 anuncios (propuesto 1)", msg)

    def test_contadores_de_entregas_y_permisos(self):
        E = api_v2.ESTADO; antes = dict(E)
        datos = {"link": BP, "precio": 16300, "vaer": 4, "lat": 55.711, "lon": 12.48}
        post("/api/v2/anuncios", {"datos": datos}, VIG); post("/api/v2/anuncios", {"datos": datos}, VIG)   # 2.ª vez ya no es nuevo
        post(uid_url("Boligportal:5481965") + "/comprobacion", {"resultado": "retirado"}, LARES)              # 403 de permisos
        self.assertEqual((E["entregas"] - antes["entregas"], E["entregas_nuevas"] - antes["entregas_nuevas"],
                          E["prohibido"] - antes["prohibido"]), (2, 1, 1))
        self.assertIn("acciones no permitidas", app.check_anuncios()[1])


if __name__ == "__main__":
    unittest.main()
