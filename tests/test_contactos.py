"""Contactos de los chats de Boligportal y ❤️ (backend/contactos.py y sus rutas en la API v2), sin internet.
    python3 -m unittest tests.test_contactos -v
"""
import json
import sqlite3
import unittest

from tests.test_api_v2 import BaseV2, LARES, WEB, post
from tests.test_backend import app, pedir
import api_v2                                                # noqa: E402
import contactos as C                                        # noqa: E402  (ya en sys.path por test_backend)

WEB_ORIGIN = "https://jonlarra1.github.io"


def contacto(pid="5670349", **kw):
    return {"pid": pid, "hilo": 37733843, "marca": "visita", "es_mio": False, "fecha": "2026-11-03T10:00:00Z", **kw}


class TestModelo(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); C.crear_tablas(self.con)

    def test_guarda_y_solo_cuenta_lo_que_cambia(self):
        self.assertEqual(C.guardar_contactos(self.con, [contacto(), contacto("5363124", marca="sin_respuesta", es_mio=True)]), 2)
        self.assertEqual(C.guardar_contactos(self.con, [contacto()]), 0)                 # igual: no cuenta
        self.assertEqual(C.guardar_contactos(self.con, [contacto(marca="respondido")]), 1)
        l = C.listar(self.con)["contactos"]
        self.assertEqual((l["5670349"]["marca"], l["5670349"]["hilo"], l["5363124"]["es_mio"]), ("respondido", 37733843, True))

    def test_rechaza_lo_que_no_encaja_y_no_guarda_texto(self):
        for mal in ([contacto(pid="abc")], [contacto(marca="otra")], [contacto(es_mio="no")], [contacto(fecha="ayer")],
                    [contacto(hilo="1; DROP")], "x", [contacto()] * 501):
            with self.subTest(mal=str(mal)[:40]), self.assertRaises(C.NoValido): C.guardar_contactos(self.con, mal)
        C.guardar_contactos(self.con, [contacto(texto="Hej Unai, mit nummer er 12345678")])
        self.assertNotIn("texto", C.listar(self.con)["contactos"]["5670349"])
        self.assertNotIn("12345678", str(self.con.execute("SELECT * FROM contacto").fetchall()))

    def test_anuncio_repetido_en_el_lote_vale_el_primero(self):
        self.assertEqual(C.guardar_contactos(self.con, [contacto(marca="visita"), contacto(marca="sin_respuesta")]), 1)
        self.assertEqual(C.listar(self.con)["contactos"]["5670349"]["marca"], "visita")
        self.assertEqual(C.guardar_contactos(self.con, [contacto(marca="visita"), contacto(marca="sin_respuesta")]), 0)

    def test_favorito_no_se_borra(self):
        C.poner_favorito(self.con, "jl1644", True, "jon")
        self.assertIn("jl1644", C.listar(self.con)["favoritos"])
        C.poner_favorito(self.con, "jl1644", False, "unai")
        self.assertNotIn("jl1644", C.listar(self.con)["favoritos"])
        self.assertEqual(self.con.execute("SELECT activo, by FROM favorito").fetchall(), [(0, "unai")])
        for mal in (("<b>", True), ("1", "si"), (5, True)):
            with self.assertRaises(C.NoValido): C.poner_favorito(self.con, mal[0], mal[1], "jon")


def mensaje(i, **kw):
    return {"id": i, "hilo": 37733843, "pid": "5670349", "es_mio": i % 2 == 0, "fecha": f"2026-11-0{i}T10:00:00Z",
            "texto": f"mensaje {i}", "adjuntos": 0, **kw}


class TestMensajesModelo(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); C.crear_tablas(self.con)

    def test_guarda_sin_repetir_y_en_orden(self):
        self.assertEqual(C.guardar_mensajes(self.con, [mensaje(2), mensaje(1)]), 2)
        self.assertEqual(C.guardar_mensajes(self.con, [mensaje(2), mensaje(3)]), 1)
        self.assertEqual([m["texto"] for m in C.mensajes_de(self.con, "5670349")], ["mensaje 1", "mensaje 2", "mensaje 3"])
        self.assertEqual(C.mensajes_de(self.con, "1111111"), [])

    def test_rechaza(self):
        for mal in ([mensaje(1, texto=5)], [mensaje(1, texto="x" * 5001)], [mensaje(1, pid="a")], [mensaje(1, fecha="")],
                    [mensaje(1, es_mio="si")], [mensaje(1, id=True)], [mensaje(1, adjuntos=99)], "x"):
            with self.subTest(mal=str(mal)[:30]), self.assertRaises(C.NoValido): C.guardar_mensajes(self.con, mal)


LECTURA = "clave-de-lectura-de-jon-para-tests"


class TestMensajesRutas(BaseV2):
    def setUp(self):
        super().setUp()
        api_v2.LECTORES[LECTURA] = "jon"
        with app.db() as c:
            c.execute("DELETE FROM mensaje")

    def tearDown(self):
        api_v2.LECTORES.pop(LECTURA, None); super().tearDown()

    def test_solo_con_clave_de_lectura(self):
        self.assertEqual(post("/api/v2/mensajes", {"mensajes": [mensaje(1)]}, LARES)[2], {"nuevos": 1})
        self.assertEqual(pedir("/api/v2/mensajes?pid=5670349")[0], 401)
        self.assertEqual(pedir("/api/v2/mensajes?pid=5670349", headers={"X-Pisos-Lectura": "otra-clave-cualquiera-x"})[0], 401)
        code, _, r = pedir("/api/v2/mensajes?pid=5670349", headers={"X-Pisos-Lectura": LECTURA})
        self.assertEqual((code, r["mensajes"][0]["texto"]), (200, "mensaje 1"))
        self.assertEqual(pedir("/api/v2/mensajes?pid=x", headers={"X-Pisos-Lectura": LECTURA})[0], 400)
        self.assertNotIn("mensaje 1", json.dumps(pedir("/api/v2/contactos")[2]), "lo público no lleva textos")

    def test_la_web_no_escribe_mensajes_y_cors_permite_la_cabecera(self):
        self.assertEqual(post("/api/v2/mensajes", {"mensajes": [mensaje(1)], "quien": "jon"}, WEB, WEB_ORIGIN)[0], 403)
        _, h, _ = pedir("/api/v2/mensajes?pid=5670349", method="OPTIONS")
        self.assertIn("X-Pisos-Lectura", h.get("Access-Control-Allow-Headers", ""))

    def test_clave_mala_repetida_se_corta(self):
        codes = [pedir("/api/v2/mensajes?pid=5670349", headers={"X-Pisos-Lectura": "mala-mala-mala-mala"})[0] for _ in range(7)]
        self.assertIn(429, codes)


class TestRutas(BaseV2):
    def setUp(self):
        super().setUp()
        with app.db() as c:
            c.execute("DELETE FROM contacto"); c.execute("DELETE FROM favorito")

    def test_lares_entrega_contactos_y_la_web_los_lee(self):
        code, _, r = post("/api/v2/contactos", {"contactos": [contacto()]}, LARES)
        self.assertEqual((code, r["cambiados"]), (200, 1))
        code, _, r = pedir("/api/v2/contactos")
        self.assertEqual((code, r["contactos"]["5670349"]["marca"]), (200, "visita"))

    def test_la_web_no_puede_escribir_contactos(self):
        code, _, _ = post("/api/v2/contactos", {"contactos": [contacto()], "quien": "jon"}, WEB, WEB_ORIGIN)
        self.assertEqual(code, 403)
        self.assertEqual(pedir("/api/v2/contactos")[2]["contactos"], {})

    def test_favorito_desde_la_web(self):
        code, _, r = post("/api/v2/favoritos", {"id": "5670349", "activo": True, "quien": "unai"}, WEB, WEB_ORIGIN)
        self.assertEqual((code, r["activo"]), (200, True))
        self.assertEqual(pedir("/api/v2/contactos")[2]["favoritos"]["5670349"]["by"], "unai")
        self.assertEqual(post("/api/v2/favoritos", {"id": "<x>", "activo": True, "quien": "jon"}, WEB, WEB_ORIGIN)[0], 400)

    def test_check_chats(self):
        post("/api/v2/contactos", {"contactos": [contacto()]}, LARES)
        ok, txt = app.check_chats()
        self.assertIn("visita 1", txt)


if __name__ == "__main__":
    unittest.main()
