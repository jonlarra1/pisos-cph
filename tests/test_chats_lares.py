"""Lector de chats de Boligportal de Lares (lares/chats_boligportal.py), contra el servidor de pruebas y sin internet.
    python3 -m unittest tests.test_chats_lares -v
"""
import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from tests.test_api_v2 import BaseV2, LARES
from tests.test_backend import Servidor, app, pedir

_spec = importlib.util.spec_from_file_location(
    "chats_boligportal", os.path.join(os.path.dirname(__file__), "..", "lares", "chats_boligportal.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

COOKIE = "sessionid=NO-SE-DEBE-VER-123"
MEDIODIA = int(datetime(2026, 11, 8, 12, 0, tzinfo=CB.CPH).timestamp())


def hilo(pid, msg, is_me, body, state="ACTIVE", t=None):
    return {"id": t or int(pid) * 10, "is_owner": False,
            "ad": {"id": int(pid), "state": state, "street_name": f"Calle {pid}", "postal_code": 2720, "city": "Vanløse"},
            "last_message": {"id": msg, "thread_id": t or int(pid) * 10, "is_me": is_me, "body": body,
                             "sent_date": "2026-11-07T18:00:00+01:00"}}


class TestReglas(unittest.TestCase):
    def test_clasifica_por_el_ultimo_mensaje(self):
        self.assertEqual(CB.clasificar(hilo("5670349", 1, True, "Kære udlejer, Vi er tre gode venner")), "sin_respuesta")
        self.assertEqual(CB.clasificar(hilo("5670349", 1, False, "I er velkomne til fremvisning tirsdag")), "visita")
        self.assertEqual(CB.clasificar(hilo("5670349", 1, False, "Tak for din henvendelse")), "respondido")
        self.assertEqual(CB.clasificar(hilo("5670349", 1, False, "Lejemålet er udlejet")), "alquilado")
        self.assertEqual(CB.clasificar(hilo("5670349", 1, True, "Hej", state="RENTED_OUT")), "alquilado")

    def test_hora_pseudoaleatoria_entre_3_y_5_h_y_nunca_de_noche(self):
        saltos = {CB.sortear(MEDIODIA) - MEDIODIA for _ in range(200)}
        self.assertTrue(all(3 * 3600 <= s <= 5 * 3600 for s in saltos))
        self.assertGreater(len(saltos), 100, "cada vez sale una hora distinta")
        self.assertTrue(CB.toca({}, MEDIODIA))
        self.assertFalse(CB.toca({"proximo": MEDIODIA + 60}, MEDIODIA))
        self.assertFalse(CB.toca({}, MEDIODIA - 9 * 3600), "a las 3 de la madrugada no lee")

    def test_la_primera_lectura_no_avisa_y_luego_solo_lo_nuevo_del_casero(self):
        chats = CB.resumir([hilo("5670349", 7, False, "Tak"), hilo("5363124", 8, True, "Hej igen")])
        self.assertEqual(CB.respuestas_nuevas(chats, {}), [])
        self.assertEqual([c["pid"] for c in CB.respuestas_nuevas(chats, {"5670349": 6, "5363124": 1})], ["5670349"])

    def test_salta_conversaciones_sin_anuncio_o_propias(self):
        sin = hilo("5670349", 1, False, "x"); sin["ad"] = {}
        mio = hilo("5363124", 2, False, "x"); mio["is_owner"] = True
        self.assertEqual(CB.resumir([sin, mio]), [])


class TestPasada(BaseV2):
    def setUp(self):
        super().setUp()
        with app.db() as c:
            c.execute("DELETE FROM contacto")
        self.dir = tempfile.TemporaryDirectory(); d = Path(self.dir.name)
        self.rutas = mock.patch.multiple(CB, COOKIE=d / ".boligportal_cookie", ESTADO=d / ".chats_estado.json",
                                         PRIVADO=d / "chats_boligportal.json", BRIDGE=d / "bridge")
        self.rutas.start()
        self.bridge_app = mock.patch.object(app, "BRIDGE", str(d / "bridge")); self.bridge_app.start()
        CB.pisos.API = Servidor.url(); os.environ["PISOS_API_KEY"] = LARES

    def tearDown(self):
        self.rutas.stop(); self.bridge_app.stop(); self.dir.cleanup(); os.environ.pop("PISOS_API_KEY", None)
        super().tearDown()

    def correr(self, paginas, ahora=MEDIODIA):
        out = io.StringIO()
        with mock.patch.object(CB, "pedir_pagina", side_effect=paginas), contextlib.redirect_stdout(out):
            CB.pasada(ahora, pausa=lambda s: None)
        return out.getvalue()

    def buzon(self):
        return json.loads((CB.BRIDGE / "chats_estado.json").read_text())

    def test_sin_cookie_no_lee_nada(self):
        self.assertEqual(self.correr(AssertionError("no debe pedir nada")), "")
        self.assertEqual(self.buzon()["sesion"], "sin_cookie")

    def test_lee_entrega_y_avisa_solo_lo_nuevo(self):
        CB.COOKIE.write_text(COOKIE)
        primera = {"threads": [hilo("5670349", 7, True, "Kære udlejer")], "count": 1}
        self.assertEqual(self.correr([primera]), "", "la primera lectura solo aprende")
        self.assertEqual(pedir("/api/v2/contactos")[2]["contactos"]["5670349"]["marca"], "sin_respuesta")
        self.assertEqual(self.correr([primera], MEDIODIA + 600), "", "no toca hasta la hora sorteada")
        respuesta = {"threads": [hilo("5670349", 9, False, "Fremvisning onsdag kl. 17?")], "count": 1}
        salida = self.correr([respuesta], MEDIODIA + 6 * 3600)
        self.assertIn("RESPUESTA | Calle 5670349, 2720, Vanløse | visita", salida)
        self.assertIn("https://www.boligportal.dk/indbakke/t/56703490", salida)
        self.assertEqual(pedir("/api/v2/contactos")[2]["contactos"]["5670349"]["marca"], "visita")

    def test_el_texto_solo_queda_en_privado_y_la_cookie_en_ningun_sitio(self):
        CB.COOKIE.write_text(COOKIE)
        salida = self.correr([{"threads": [hilo("5670349", 7, False, "Mit nummer er 12345678")], "count": 1}])
        self.assertEqual(stat.S_IMODE(CB.PRIVADO.stat().st_mode), 0o600)
        self.assertIn("12345678", CB.PRIVADO.read_text())
        publico = json.dumps(pedir("/api/v2/contactos")[2]) + (CB.BRIDGE / "chats_estado.json").read_text()
        self.assertNotIn("12345678", publico)
        for sitio in (salida, publico, CB.ESTADO.read_text(), CB.PRIVADO.read_text()):
            self.assertNotIn("NO-SE-DEBE-VER", sitio)

    def test_sesion_caducada_avisa_una_vez_al_dia(self):
        CB.COOKIE.write_text(COOKIE)
        self.assertIn("renovar la cookie", self.correr(CB.Caducada("Boligportal respondió 401")))
        self.assertEqual(self.buzon()["sesion"], "caducada")
        ok, _ = app.check_chats()
        self.assertFalse(ok)
        self.assertEqual(self.correr(CB.Caducada("401"), MEDIODIA + 6 * 3600), "", "el aviso no se repite en el día")

    def test_lo_alquilado_sigue_alquilado_aunque_contestemos(self):
        CB.COOKIE.write_text(COOKIE)
        self.correr([{"threads": [hilo("5670349", 1, False, "Lejemålet er udlejet")], "count": 1}])
        self.correr([{"threads": [hilo("5670349", 2, True, "Ok, tak")], "count": 1}], MEDIODIA + 6 * 3600)
        self.assertEqual(pedir("/api/v2/contactos")[2]["contactos"]["5670349"]["marca"], "alquilado")

    def test_respuesta_sin_conversacion_enlaza_al_anuncio(self):
        CB.COOKIE.write_text(COOKIE)
        sin = hilo("5670349", 1, True, "Hej"); sin["id"] = None; sin["last_message"]["thread_id"] = None
        self.correr([{"threads": [sin], "count": 1}])
        sin["last_message"].update(id=2, is_me=False, body="Tak")
        salida = self.correr([{"threads": [sin], "count": 1}], MEDIODIA + 6 * 3600)
        self.assertNotIn("None", salida)
        self.assertIn(CB.ANUNCIO.format("5670349"), salida)

    def test_check_falla_si_la_tarea_deja_de_leer(self):
        CB.BRIDGE.mkdir(parents=True, exist_ok=True)
        (CB.BRIDGE / "chats_estado.json").write_text(json.dumps({"ts": 1, "sesion": "ok"}))
        ok, txt = app.check_chats()
        self.assertFalse(ok); self.assertIn("¿tarea parada?", txt)

    def test_conversacion_nueva_entra_como_contactado(self):
        CB.COOKIE.write_text(COOKIE)
        nuevo = {"threads": [hilo("5456969", 3, True, "Kære udlejer")], "count": 1}
        with mock.patch.object(CB.pisos, "llamar", wraps=CB.pisos.llamar) as ll:
            self.correr([nuevo])
        self.assertIn(mock.call("POST", "/api/v2/enlaces", {"url": CB.ANUNCIO.format("5456969"), "quien": "lares"}),
                      ll.call_args_list)
        self.assertEqual(self.buzon()["sesion"], "ok")
        if "5456969" in json.loads(CB.ESTADO.read_text()).get("no_anadir", []):   # sin ficha en las pruebas: no se reintenta
            with mock.patch.object(CB.pisos, "llamar", wraps=CB.pisos.llamar) as ll:
                self.correr([nuevo], MEDIODIA + 6 * 3600)
            self.assertNotIn("/api/v2/enlaces", [c.args[1] for c in ll.call_args_list])


class TestAContactado(unittest.TestCase):
    """Con conversación = contactado (sin tocar lo decidido) y qué se reintenta."""
    def llamadas(self, base, falla=None):
        hechas = []
        def llamar(metodo, ruta, cuerpo=None):
            hechas.append((metodo, ruta, cuerpo))
            if ruta == "/api/v2/anuncios": return {"anuncios": base}
            if falla: raise CB.pisos.Error("la API respondió x", falla)
            if ruta == "/api/v2/enlaces": return {"uid": "Boligportal:" + cuerpo["url"].rsplit("-", 1)[1], "estado": "rechazado"}
            return {}
        return hechas, llamar

    def chats(self, *pids):
        return CB.resumir([hilo(p, 1, True, "Kære udlejer") for p in pids])

    def test_propuesto_rechazado_y_nuevo_pasan_a_contactado_y_lo_decidido_no_se_toca(self):
        base = [{"uid": f"Boligportal:{p}", "pid": p, "portal": "Boligportal", "estado": e}
                for p, e in (("1111111", "propuesto"), ("2222222", "rechazado"), ("3333333", "descartado"), ("4444444", "gusta"))]
        hechas, llamar = self.llamadas(base)
        with mock.patch.object(CB.pisos, "llamar", llamar):
            CB.a_contactado(self.chats("1111111", "2222222", "3333333", "4444444", "5555555"), set())
        estados = sorted(r for m, r, c in hechas if r.endswith("/estado"))
        self.assertEqual(estados, [f"/api/v2/anuncios/Boligportal%3A{p}/estado" for p in ("1111111", "2222222", "5555555")])
        self.assertIn(("POST", "/api/v2/enlaces", {"url": CB.ANUNCIO.format("5555555"), "quien": "lares"}), hechas)

    def test_solo_un_400_se_da_por_imposible(self):
        for codigo, guardado in ((400, {"5555555"}), (502, set()), (429, set()), (None, set())):
            no = set(); _, llamar = self.llamadas([], falla=codigo)
            with mock.patch.object(CB.pisos, "llamar", llamar), contextlib.redirect_stderr(io.StringIO()):
                CB.a_contactado(self.chats("5555555"), no)
            self.assertEqual(no, guardado, codigo)

    def test_de_dos_conversaciones_del_mismo_anuncio_vale_la_mas_nueva(self):
        vieja = hilo("5670349", 1, True, "Kære udlejer", t=1); vieja["last_message"]["sent_date"] = "2026-10-01T10:00:00Z"
        nueva = hilo("5670349", 2, False, "Fremvisning?", t=2)
        self.assertEqual([(c["hilo"], c["marca"]) for c in CB.resumir([vieja, nueva])], [(2, "visita")])


if __name__ == "__main__":
    unittest.main()
