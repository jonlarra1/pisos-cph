"""Contactos de los chats de Boligportal y ❤️ (backend/contactos.py y sus rutas en la API v2), sin internet.
    python3 -m unittest tests.test_contactos -v
"""
import sqlite3
import unittest

from tests.test_api_v2 import BaseV2, LARES, WEB, post
from tests.test_backend import app, pedir
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

    def test_favorito_no_se_borra(self):
        C.poner_favorito(self.con, "jl1644", True, "jon")
        self.assertIn("jl1644", C.listar(self.con)["favoritos"])
        C.poner_favorito(self.con, "jl1644", False, "unai")
        self.assertNotIn("jl1644", C.listar(self.con)["favoritos"])
        self.assertEqual(self.con.execute("SELECT activo, by FROM favorito").fetchall(), [(0, "unai")])
        for mal in (("<b>", True), ("1", "si"), (5, True)):
            with self.assertRaises(C.NoValido): C.poner_favorito(self.con, mal[0], mal[1], "jon")


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
