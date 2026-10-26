"""La herramienta del chat de Lares (lares/pisos.py) contra el servidor de pruebas (sin internet).
    python3 -m unittest tests.test_herramienta_lares -v
"""
import contextlib
import importlib.util
import io
import os
import unittest

from tests.test_api_v2 import BaseV2, BP, EDC, LARES
from tests.test_backend import Servidor, pedir

_spec = importlib.util.spec_from_file_location("pisos_tool", os.path.join(os.path.dirname(__file__), "..", "lares", "pisos.py"))
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)


class TestHerramientaLares(BaseV2):
    def setUp(self):
        super().setUp()
        H.API = Servidor.url()
        os.environ["PISOS_API_KEY"] = LARES

    def tearDown(self):
        super().tearDown()
        os.environ.pop("PISOS_API_KEY", None)

    def ejecutar(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            H.main(list(args))
        return out.getvalue()

    def test_anadir_consultar_y_cambiar_estado(self):
        salida = self.ejecutar("añadir", EDC)
        self.assertIn("Añadido: [propuesto] EDC:11810121", salida)
        self.assertIn("«Propuestos»", salida)
        self.assertIn("Ya estaba", self.ejecutar("añadir", EDC))
        self.assertIn("EDC:11810121", self.ejecutar("consultar"))
        self.assertIn("Hecho: [gusta]", self.ejecutar("estado", EDC, "gusta", "le", "gusta", "a", "Jon", "--por", "jon"))
        ficha = self.ejecutar("consultar", EDC)
        self.assertIn("propuesto → gusta (lares:jon) le gusta a Jon", ficha)
        self.assertEqual(pedir("/api/estados")[2]["11810121"]["st"], "gusta")        # la web lo ve

    def test_errores_claros_sin_tocar_nada(self):
        for args, texto in ((("añadir", "https://evil.com/x"), "no es un enlace"),
                            (("estado", BP, "borrado"), "estado no válido"),
                            (("estado", BP, "gusta", "--por", "pepe"), "--por solo"),
                            (("borrar", BP), "orden no válida")):
            with self.assertRaises(H.Error) as e:
                self.ejecutar(*args)
            self.assertIn(texto, str(e.exception))

    def test_sin_clave(self):
        os.environ.pop("PISOS_API_KEY")
        H.CLAVE_ARCHIVO = H.Path("/no/existe")
        with self.assertRaises(H.Error) as e:
            self.ejecutar("añadir", BP)
        self.assertIn("falta la clave", str(e.exception))

    def test_clave_mala(self):
        os.environ["PISOS_API_KEY"] = "clave-falsa-123"
        with self.assertRaises(H.Error) as e:
            self.ejecutar("añadir", BP)
        self.assertIn("403", str(e.exception))


if __name__ == "__main__":
    unittest.main()
