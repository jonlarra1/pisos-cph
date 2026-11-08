"""Trazados por calle (backend/calles.py y GET /api/calle), sin internet: OSRM simulado.
    python3 -m unittest tests.test_calles -v
"""
import io
import json
import sqlite3
import unittest
import urllib.error
from contextlib import contextmanager

from tests.test_backend import app, pedir
import calles as K                                           # noqa: E402  (ya en sys.path por test_backend)

A, B = [55.70700, 12.52300], [55.71050, 12.51800]
OK = {"code": "Ok", "routes": [{"distance": 612.4, "geometry": {"coordinates": [[12.523, 55.707], [12.52, 55.709], [12.518, 55.7105]]}}]}


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def abridor(*respuestas):
    """urlopen simulado: cada llamada devuelve (o lanza) lo siguiente de `respuestas`."""
    pedidas = []
    def abrir(req, timeout=None):
        pedidas.append(req.full_url); r = respuestas[min(len(pedidas), len(respuestas)) - 1]
        if isinstance(r, Exception): raise r
        return Resp(json.dumps(r).encode())
    abrir.pedidas = pedidas
    return abrir


def http(code, cuerpo=b"{}"):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(cuerpo))


def limpiar():
    K._COLA.clear(); K._CONOCIDOS.clear(); K._ULTIMA["t"] = 0
    for k in K.ESTADO: K.ESTADO[k] = "" if k == "ultimo_error" else 0


class Base(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); K.crear_tabla(self.con)
        self.viejo = (K.PAUSA, K.LOG["fn"]); K.PAUSA = 0; limpiar()
        self.avisos = []; K.LOG["fn"] = lambda level, msg: self.avisos.append((level, msg))
        for p in (B, [55.72, 12.53]): K.conocer(K.clave("foot", A, p)); K.conocer(K.clave("bike", A, p))

    def tearDown(self):
        K.PAUSA, K.LOG["fn"] = self.viejo; limpiar(); self.con.close()

    @contextmanager
    def db(self):
        with self.con:
            yield self.con


class TestTrazado(Base):
    def test_pide_una_vez_y_luego_sale_de_la_base(self):
        ab = abridor(OK)
        code, r = K.trazado(self.db, "foot", A, B, ab)
        self.assertEqual((code, r["d"], r["path"][0], len(r["path"])), (200, 612, [55.707, 12.523], 3))
        self.assertIn("routed-foot", ab.pedidas[0]); self.assertIn("12.523,55.707;12.518,55.7105", ab.pedidas[0])
        self.assertEqual(K.trazado(self.db, "foot", A, B, abridor(Exception("no debe llamarse")))[0], 200)
        self.assertEqual((K.ESTADO["pedidas"], K.ESTADO["de_base"]), (1, 1))

    def test_tramo_que_no_es_del_motor_no_va_a_osrm(self):
        ab = abridor(OK)
        self.assertEqual(K.trazado(self.db, "foot", A, [55.75, 12.4], ab)[0], 403); self.assertEqual(ab.pedidas, [])

    def test_otro_400_no_se_guarda(self):
        for cuerpo in (b'{"code":"InvalidQuery"}', b"<html>"):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(K.trazado(self.db, "foot", A, B, abridor(http(400, cuerpo)))[0], 404)
                self.assertIsNone(K.leer(self.con, K.clave("foot", A, B))); self.assertEqual(K.ESTADO["caido_desde"], 0)

    def test_tope_de_filas(self):
        viejo, K.MAX_FILAS = K.MAX_FILAS, 1
        try:
            self.assertTrue(K.guardar(self.con, "k1", "foot", {"d": 1, "path": [A, B]}))
            self.assertFalse(K.guardar(self.con, "k2", "foot", {"d": 1, "path": [A, B]}))
            ok, txt = K.resumen(self.con); self.assertFalse(ok); self.assertIn("tabla llena", txt)
        finally:
            K.MAX_FILAS = viejo

    def test_sin_ruta_se_guarda_y_no_se_repite(self):
        ab = abridor(http(400, b'{"code":"NoRoute"}'))
        self.assertEqual(K.trazado(self.db, "bike", A, B, ab)[0], 404)
        self.assertEqual(K.trazado(self.db, "bike", A, B, ab)[0], 404); self.assertEqual(len(ab.pedidas), 1)

    def test_osrm_caido_da_503_va_a_la_cola_y_avisa_una_vez(self):
        for err in (OSError("Connection refused"), http(503), http(429)):
            with self.subTest(err=str(err)):
                K._COLA.clear(); K.ESTADO["caido_desde"] = 0; K._ULTIMA["t"] = 0
                self.assertEqual(K.trazado(self.db, "foot", A, B, abridor(err))[0], 503)
                self.assertEqual(len(K._COLA), 1)
        self.assertEqual(K.leer(self.con, K.clave("foot", A, B)), None)            # el fallo no se guarda
        self.assertEqual(sum(1 for lv, _ in self.avisos if lv == "warn"), 3)
        K._ULTIMA["t"] = 0
        self.assertEqual(K.trazado(self.db, "foot", A, B, abridor(OK))[0], 200)
        self.assertEqual(K.ESTADO["caido_desde"], 0)
        self.assertTrue(any(lv == "info" and "otra vez" in m for lv, m in self.avisos))

    def test_recien_caido_no_espera_otra_vez(self):
        K.trazado(self.db, "foot", A, B, abridor(OSError("x")))
        ab = abridor(OK)
        self.assertEqual(K.trazado(self.db, "foot", A, [55.72, 12.53], ab)[0], 503); self.assertEqual(ab.pedidas, [])
        self.assertEqual(next(iter(K._COLA)), K.clave("foot", A, [55.72, 12.53]))       # lo de la web, primero

    def test_respuestas_raras_no_rompen(self):
        for mala in ({"code": "Ok", "routes": [{"distance": 1, "geometry": {"coordinates": [[1, 2]]}}]}, {"code": "Ok", "routes": "x"}, [1]):
            with self.subTest(mala=str(mala)[:30]):
                K.ESTADO["caido_desde"] = 0; K._ULTIMA["t"] = 0
                self.assertEqual(K.trazado(self.db, "foot", A, B, abridor(mala))[0], 503)

    def test_valido(self):
        bbox = (55.55, 55.86, 12.20, 12.72)
        K.valido("bike", A, B, bbox)
        for modo, a, b in (("car", A, B), ("foot", [40.4, -3.7], B), ("foot", [55.56, 12.21], [55.85, 12.71])):
            with self.subTest(modo=modo, a=a), self.assertRaises(ValueError): K.valido(modo, a, b, bbox)


class TestRutasDelMotor(Base):
    OPC = [{"legs": [{"m": "walk", "path": [A, [55.71, 12.52]]}, {"m": "Bus", "path": [[55.71, 12.52], [55.73, 12.5]]},
                     {"m": "bike", "path": [[55.73, 12.5], B]}, {"m": "walk", "path": [A, [55.70, 12.52], B]}]}]

    def test_pone_lo_guardado_y_encola_lo_que_falta(self):
        K.guardar(self.con, K.clave("foot", A, [55.71, 12.52]), "foot", {"d": 300, "path": [A, [55.708, 12.521], [55.71, 12.52]]})
        out = K.con_calles(self.db, self.OPC)
        legs = out[0]["legs"]
        self.assertEqual(len(legs[0]["calle"]), 3); self.assertNotIn("calle", legs[1]); self.assertNotIn("calle", legs[2])
        self.assertNotIn("calle", self.OPC[0]["legs"][0], "no toca la respuesta guardada en la caché")
        self.assertEqual(list(K._COLA), [K.clave("bike", [55.73, 12.5], B)])

    def test_el_hilo_rellena_la_cola(self):
        K.encolar("bike", [55.73, 12.5], B); K.encolar("foot", A, B)
        vueltas = iter([False, False, True])
        K.rellenar(self.db, abridor(OK, http(400, b'{"code":"NoRoute"}')), parar=lambda: next(vueltas))
        self.assertIsInstance(K.leer(self.con, K.clave("bike", [55.73, 12.5], B)), dict)
        self.assertEqual(K.leer(self.con, K.clave("foot", A, B)), "sin_ruta"); self.assertEqual(len(K._COLA), 0)

    def test_si_osrm_falla_el_hilo_lo_deja_en_la_cola(self):
        K.ESPERA_CAIDO, viejo = 0, K.ESPERA_CAIDO
        try:
            K.encolar("foot", A, B); vueltas = iter([False, True])
            K.rellenar(self.db, abridor(OSError("x")), parar=lambda: next(vueltas))
            self.assertEqual(len(K._COLA), 1)
        finally:
            K.ESPERA_CAIDO = viejo

    def test_sin_cola_y_caido_prueba_si_ha_vuelto(self):
        K.ESTADO["caido_desde"] = 1; vueltas = iter([False, True]); ab = abridor(OK)
        K.rellenar(self.db, ab, parar=lambda: next(vueltas))
        self.assertEqual(len(ab.pedidas), 1); self.assertEqual(K.ESTADO["caido_desde"], 0)

    def test_previo_solo_encola_sin_copiar(self):
        out = K.con_calles(self.db, self.OPC, solo_encolar=True)
        self.assertIs(out, self.OPC); self.assertEqual(len(K._COLA), 2)

    def test_resumen(self):
        K.guardar(self.con, "k1", "foot", {"d": 1, "path": [A, B]}); K.guardar(self.con, "k2", "foot", None)
        ok, txt = K.resumen(self.con)
        self.assertTrue(ok); self.assertIn("guardados 1 trazados (sin ruta 1)", txt)
        K.ESTADO["caido_desde"] = 1
        self.assertFalse(K.resumen(self.con)[0])


class TestRuta(unittest.TestCase):
    def test_parametros_malos_400(self):
        for q in ("modo=car&a=55.7,12.5&b=55.71,12.51", "modo=foot&a=55.7&b=55.71,12.51", "modo=foot&a=40,3&b=55.71,12.51", "modo=foot"):
            with self.subTest(q=q): self.assertEqual(pedir("/api/calle?" + q)[0], 400)

    def test_tramo_desconocido_403(self):
        self.assertEqual(pedir("/api/calle?modo=foot&a=55.7001,12.5001&b=55.7101,12.5101")[0], 403)

    def test_lo_guardado_sale_sin_internet(self):
        with app.db() as c:
            K.guardar(c, K.clave("bike", A, B), "bike", {"d": 700, "path": [A, [55.709, 12.52], B]})
        code, _, r = pedir(f"/api/calle?modo=bike&a={A[0]},{A[1]}&b={B[0]},{B[1]}")
        self.assertEqual((code, r["d"], len(r["path"])), (200, 700, 3))

    def test_hay_check(self):
        self.assertIn("calles", [n for n, _ in app.CHECKS_LISTA])
        self.assertIn("guardados", app.check_calles()[1])


if __name__ == "__main__":
    unittest.main()
