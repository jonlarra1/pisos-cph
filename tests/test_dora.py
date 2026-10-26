"""Lógica de tools/dora.py con datos inventados (sin git, sin red).
    python3 -m unittest tests.test_dora -v
"""
import datetime as dt
import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("dora", pathlib.Path(__file__).resolve().parent.parent / "tools" / "dora.py")
dora = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dora)

T0 = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)


def h(n):
    return T0 + dt.timedelta(hours=n)


def commit(sha, hora, asunto, pieza="backend"):
    return {"sha": sha, "ts": h(hora), "asunto": asunto, "piezas": {pieza}}


def dep(hora, resultado="ok", pieza="backend"):
    return {"ts": h(hora), "pieza": pieza, "resultado": resultado}


class TestDora(unittest.TestCase):
    def setUp(self):
        self.commits = [commit("a", 0, "feat: x"), commit("b", 1, "feat: y"), commit("c", 5, "fix: y"),
                        commit("d", 30, "feat: z"), commit("e", 40, "docs", pieza="web")]

    def test_reparto_y_pendientes(self):
        deps, pend = dora.repartir(self.commits, [dep(2), dep(6)], "backend")
        self.assertEqual([[c["sha"] for c in d["commits"]] for d in deps], [["a", "b"], ["c"]])
        self.assertEqual([c["sha"] for c in pend], ["d"])

    def test_metricas(self):
        deps, pend = dora.repartir(self.commits, [dep(2), dep(6)], "backend")
        m = dora.medir("backend", deps, pend, h(0), h(48))
        self.assertEqual(m["despliegues"], 2)
        self.assertEqual(m["lead_mediana_h"], 1.0)                 # 2 h, 1 h, 1 h
        self.assertEqual(m["tasa_retrabajo"], 0.5)                 # el 2.º solo trae un fix
        self.assertEqual(m["tasa_fallos"], 0.5)                    # el 1.º necesitó ese arreglo antes de 48 h
        self.assertEqual(m["recuperacion_mediana_h"], 4.0)
        self.assertEqual(m["sin_desplegar"], 1)

    def test_vuelta_atras_es_fallo(self):
        deps, pend = dora.repartir(self.commits, [dep(2, "vuelta_atras"), dep(3)], "backend")
        m = dora.medir("backend", deps, pend, h(0), h(48))
        self.assertEqual((m["despliegues"], m["tasa_fallos"], m["recuperacion_mediana_h"]), (1, 0.5, 1.0))

    def test_publicacion_cancelada_por_otro_push_no_es_fallo(self):
        b = lambda st, minuto: {"status": st, "created_at": f"2026-10-22T20:{minuto:02d}:00Z"}   # noqa: E731
        self.assertTrue(dora._sustituido(b("errored", 0), [b("built", 1)]))
        self.assertFalse(dora._sustituido(b("errored", 0), [b("built", 30)]))       # tardó: fallo de verdad
        self.assertFalse(dora._sustituido(b("errored", 0), [b("errored", 1)]))

    def test_registrar_rechaza_valores_raros(self):
        with self.assertRaises(SystemExit):
            dora.registrar("otra", "ok", "")
        with self.assertRaises(SystemExit):
            dora.registrar("backend", "borrado", "")


if __name__ == "__main__":
    unittest.main()
