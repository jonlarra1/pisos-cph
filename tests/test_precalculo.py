"""Rutas de los destinos: freno del motor y cola de cálculo previo (backend/precalculo.py)."""
import datetime, os, sys, threading, unittest, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import precalculo as PC  # noqa: E402

PISOS = [(55.70, 12.40), (55.65, 12.55)]


class TestFreno(unittest.TestCase):
    def test_tope_de_espera(self):
        f = PC.Freno(2)
        self.assertTrue(f.entrar()); self.assertTrue(f.entrar()); self.assertFalse(f.entrar())   # la 3.ª: «ocupado»
        f.salir(); self.assertTrue(f.entrar())
    def test_libre(self):
        f = PC.Freno(); self.assertTrue(f.libre()); f.entrar(); self.assertFalse(f.libre())


class TestPeticiones(unittest.TestCase):
    def test_como_las_pide_la_web(self):
        qs = PC.peticiones([55.6935, 12.548], "Gimnasio", PISOS, hoy=datetime.date(2026, 10, 27))
        self.assertEqual(len(qs), PC.DIAS * len(PISOS) * len(PC.PERFILES))
        q = dict(urllib.parse.parse_qsl(qs[0]))
        self.assertEqual((q["fecha"], q["hora"], q["modo"], q["perfil"], q["lim"]), ("2026-10-27", "08:30", "llegar", "bike", "10"))
        self.assertEqual((q["olat"], q["dlat"]), ("55.7", "55.6935"))                    # del piso al destino (ida)


class TestCola(unittest.TestCase):
    def setUp(self):
        self.hechas, self.freno = [], PC.Freno()
        self.cola = PC.Cola(lambda qs: (self.hechas.append(qs), (200, {}))[1], lambda: PISOS, self.freno, maximo=10)
    def test_tope_de_la_cola(self):
        self.assertEqual(self.cola.destino([55.69, 12.55], "A"), 10)                    # 12 rutas, caben 10
        self.assertEqual(self.cola.destino([55.68, 12.56], "B"), 0)                     # llena: no entra nada más
        self.assertGreaterEqual(PC.ESTADO["sin_sitio"], 2 + 12)
    def test_progreso_de_la_tanda(self):
        self.cola.destino([55.69, 12.55], "A")                                           # 10 en la tanda
        self.cola.uno(); self.cola.uno()
        self.assertEqual(PC.progreso(), {"pendientes": 8, "total": 10})
        while self.cola.uno(): pass
        self.assertEqual(PC.progreso(), {"pendientes": 0, "total": 0})
    def test_espera_si_la_web_esta_pidiendo(self):
        self.cola.destino([55.69, 12.55], "A"); self.freno.entrar()
        self.assertFalse(self.cola.uno()); self.assertEqual(self.hechas, [])
        self.freno.salir(); self.assertTrue(self.cola.uno()); self.assertEqual(len(self.hechas), 1)
    def test_un_error_no_para_la_cola(self):
        cola = PC.Cola(lambda qs: 1 / 0, lambda: PISOS, self.freno)
        cola.destino([55.69, 12.55], "A"); antes = PC.ESTADO["errores"]
        self.assertTrue(cola.uno()); self.assertEqual(PC.ESTADO["errores"], antes + 1)
    def test_avisos_al_log(self):
        avisos = []
        cola = PC.Cola(lambda qs: (200, {}), lambda: PISOS, self.freno, maximo=10, aviso=lambda n, m: avisos.append((n, m)))
        cola.destino([55.69, 12.55], "Gimnasio")                                          # 12 rutas, caben 10
        self.assertIn("cola de rutas llena", avisos[-1][1])
        while cola.uno(): pass
        self.assertEqual(avisos[-1][0], "info"); self.assertIn("10 calculadas, 0 con error", avisos[-1][1])
    def test_sin_pisos_no_falla(self):
        cola = PC.Cola(lambda qs: (200, {}), lambda: 1 / 0, self.freno)
        self.assertEqual(cola.destino([55.69, 12.55], "A"), 0)
    def test_un_solo_hilo_a_la_vez(self):
        self.cola.destino([55.69, 12.55], "A")
        hilos = [threading.Thread(target=self.cola.uno) for _ in range(5)]
        [h.start() for h in hilos]; [h.join() for h in hilos]
        self.assertEqual(len(self.hechas), len(set(self.hechas)))                       # ninguna repetida


if __name__ == "__main__":
    unittest.main()
