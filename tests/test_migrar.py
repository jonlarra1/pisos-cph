"""backend/migrar.py: la migración única a la base única (paso B). Sin red."""
import os, sqlite3, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import anuncios as A          # noqa: E402
import migrar as M            # noqa: E402

F = {"precio_max": 23000, "dorm_min": 3, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15",
     "contrato_largo": True, "compartir": True, "amueblado": "igual"}
Z = {"Herlev": True, "Glostrup": False, "Valby": True}
BP = "https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/"
MAPA = [
    {"id": "1913485", "zona": "Herlev", "calle": "Vasekær 99 m²", "m2": 99, "vaer": 4, "dorm": "3", "dormEst": "Estimado",
     "alq": 15200, "disp": "2027-01-01", "link": "https://en.lejebolig.dk/lejebolig/1913485", "lat": 55.71, "lon": 12.43,
     "st": "gusta", "fotos": []},
    {"id": "1917125", "zona": "Valby", "calle": "Gyngemose", "vaer": 4, "dorm": "3", "alq": 20000, "disp": "Inmediato",
     "link": BP + "102m2-4-vaer-id-5193904", "lat": 55.7, "lon": 12.4, "st": "nuevo"},
]
DESC = [{"t": "Sydhavn · Grøntudsevej", "r": "Quitado por Jon", "link": "https://en.lejebolig.dk/lejebolig/1913588"}]
CANDS = [{"id": "11810121", "portal": "EDC", "zona": "Glostrup", "titulo": "Grannålen 1", "precio": 14000, "vaer": 4,
          "disp": "2027-01-01", "link": "https://www.edc.dk/leje/lejlighed/2600-glostrup/grannaalen-1-st-2/11810121/"}]


def base():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    A.crear_tablas(con)
    return con


def correr(con, estados=None, historial=None):
    return M.migrar(con, MAPA, DESC, CANDS, estados or {}, historial or {}, F, Z, ahora=1_800_000_000)


class TestMigrar(unittest.TestCase):
    def test_mete_todo_sin_errores(self):
        con = base(); r = correr(con)
        self.assertEqual((r["mapa"], r["descartados"], r["candidatos"], r["errores"]), (2, 1, 1, []))
        self.assertEqual(len(A.listar(con)), 4)

    def test_pisos_del_mapa_llevan_su_id_del_mapa(self):
        con = base(); correr(con)
        a = A.obtener(con, "Boligportal:5193904")
        self.assertEqual((a["datos"]["mapa"], a["datos"]["mapa_id"], a["datos"]["disp"]), (True, "1917125", "ya"))
        self.assertIsNone(A.obtener(con, "Lejebolig:1913485")["datos"]["dorm"])  # «Estimado» no es dato del anuncio

    def test_estados_del_mapa_y_de_la_web(self):
        con = base(); correr(con, {"1917125": {"st": "contactado", "by": "unai"}})
        self.assertEqual(A.obtener(con, "Lejebolig:1913485")["estado"], "gusta")             # el de PISOS
        self.assertEqual(A.obtener(con, "Boligportal:5193904")["estado"], "contactado")      # la web manda
        self.assertEqual(A.historial(con, "Boligportal:5193904")[0]["quien"], "web:unai")

    def test_descartado_fijo_con_su_motivo(self):
        con = base(); correr(con)
        a = A.obtener(con, "Lejebolig:1913588")
        self.assertEqual((a["estado"], a["motivo"]), ("descartado", "Quitado por Jon"))

    def test_propuesto_que_hoy_no_pasa_sigue_propuesto_con_aviso(self):
        con = base(); correr(con)
        a = A.obtener(con, "EDC:11810121")
        self.assertEqual(a["estado"], "propuesto")
        self.assertIn("no pasa: zona excluida: Glostrup", a["avisos"])

    def test_la_decision_que_ya_esta_en_la_base_manda(self):
        con = base()
        A.entregar(con, M.datos_candidato(CANDS[0]), "vigilante", "x", F, {**Z, "Glostrup": True})
        A.cambiar_estado(con, "EDC:11810121", "descartado", "web", "jon")
        correr(con, {"11810121": {"st": "gusta", "by": "unai"}})
        self.assertEqual(A.obtener(con, "EDC:11810121")["estado"], "descartado")

    def test_estado_sin_datos_no_se_pierde(self):
        con = base(); r = correr(con, {"1608780": {"st": "descartado", "by": "jon"}})
        a = A.obtener(con, "Lejebolig:1608780")
        self.assertEqual((r["huerfanos"], a["estado"], a["datos"]["link"]), (1, "descartado", "https://en.lejebolig.dk/lejebolig/1608780"))

    def test_estado_de_un_anuncio_que_ya_estaba_no_le_pisa_los_datos(self):
        con = base()
        A.entregar(con, {"portal": "Boligportal", "pid": "5681028", "link": BP + "x-id-5681028", "titulo": "Nørrelund"},
                   "jon", "jon", F, Z)
        r = correr(con, {"5681028": {"st": "descartado", "by": "jon"}})
        a = A.obtener(con, "Boligportal:5681028")
        self.assertEqual((r["huerfanos"], a["estado"], a["datos"]["titulo"]), (0, "descartado", "Nørrelund"))

    def test_historial_de_la_web_se_importa_una_vez(self):
        con = base()
        h = {"1913485": [("nuevo", "jon", 100), ("gusta", "unai", 200)]}
        correr(con, historial=h); correr(con, historial=h)
        filas = [x for x in A.historial(con, "Lejebolig:1913485") if x["motivo"] == M.MOTIVO_HISTORIAL]
        self.assertEqual([(x["de"], x["a"], x["quien"]) for x in filas],
                         [("propuesto", "gusta", "web:unai"), (None, "propuesto", "web:jon")])

    def test_repetir_no_duplica_ni_cambia(self):
        con = base(); correr(con); antes = A.contar(con)
        r = correr(con)
        self.assertEqual((A.contar(con), r["estados"], len(A.listar(con))), (antes, 0, 4))

    def test_un_enlace_malo_es_un_error_no_una_caida(self):
        con = base()
        r = M.migrar(con, [{"id": "1", "link": "https://evil.example/1"}], [], [], {}, {}, F, Z)
        self.assertEqual(len(r["errores"]), 1)


if __name__ == "__main__":
    unittest.main()
