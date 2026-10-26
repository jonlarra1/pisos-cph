"""Calidad del código del backend y de Lares: seguridad, eficiencia y legibilidad.
Las comprobaciones básicas son de la librería estándar (ast). ruff, bandit y radon solo corren si están en .venv
(pip install ruff bandit radon dentro de .venv; no se instalan en la Pi).
    python3 -m unittest tests.test_calidad -v
"""
import ast, json, pathlib, shutil, subprocess, unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CODIGO = sorted([*RAIZ.glob("backend/*.py"), *RAIZ.glob("lares/*.py")])
VENV = RAIZ / ".venv" / "bin"

# Deuda conocida (26-oct): funciones largas que ya existían. No añadir más; si se arregla una, quitarla de aquí.
COMPLEJAS_CONOCIDAS = {"main", "evaluar", "do_POST"}
MAX_COMPLEJIDAD = 40          # radon: por encima es grado F
MAX_LINEAS_FUNCION = 120


def arboles():
    for f in CODIGO:
        yield f, ast.parse(f.read_text(encoding="utf-8"), str(f))


def llamada(n):
    """Nombre de la función llamada: 'eval', 'os.system', 'subprocess.run'…"""
    f = n.func
    if isinstance(f, ast.Name): return f.id
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else "?"
        return f"{base}.{f.attr}"
    return ""


class TestSeguridad(unittest.TestCase):
    def test_sin_eval_exec_ni_shell(self):
        malas = []
        for f, t in arboles():
            for n in ast.walk(t):
                if not isinstance(n, ast.Call): continue
                nombre = llamada(n)
                if nombre in ("eval", "exec", "os.system", "os.popen", "pickle.loads", "yaml.load"):
                    malas.append(f"{f.name}:{n.lineno} {nombre}")
                if any(k.arg == "shell" and getattr(k.value, "value", False) is True for k in n.keywords):
                    malas.append(f"{f.name}:{n.lineno} shell=True")
        self.assertEqual(malas, [])

    def test_sql_siempre_con_parametros(self):
        """execute() nunca recibe SQL montado con f-string, % o +: los datos van como parámetros (?)."""
        malas = []
        for f, t in arboles():
            for n in ast.walk(t):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("execute", "executemany") and n.args:
                    sql = n.args[0]
                    if isinstance(sql, (ast.JoinedStr, ast.BinOp)):
                        malas.append(f"{f.name}:{n.lineno}")
        self.assertEqual(malas, [])

    def test_sin_assert_para_validar(self):
        """assert desaparece con python -O: no se usa en el código (solo en tests)."""
        malas = [f"{f.name}:{n.lineno}" for f, t in arboles() for n in ast.walk(t) if isinstance(n, ast.Assert)]
        self.assertEqual(malas, [])

    def test_sin_claves_en_el_codigo(self):
        """Las claves llegan por variables de entorno; ninguna variable CLAVE/KEY/TOKEN con un valor fijo largo."""
        malas = []
        for f, t in arboles():
            for n in ast.walk(t):
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                    for d in n.targets:
                        nombre = getattr(d, "id", "").upper()
                        if any(p in nombre for p in ("CLAVE", "KEY", "TOKEN", "SECRET", "PASSWORD")) and len(n.value.value) >= 16:
                            malas.append(f"{f.name}:{n.lineno} {nombre}")
        self.assertEqual(malas, [])

    def test_no_hay_ruta_de_borrado_en_la_api(self):
        """La API no tiene DELETE ni borra anuncios (docs/arquitectura.md: nada se borra)."""
        api = (RAIZ / "backend" / "api_v2.py").read_text(encoding="utf-8") + (RAIZ / "backend" / "anuncios.py").read_text(encoding="utf-8")
        self.assertNotIn("DELETE FROM anuncio", api)
        self.assertNotIn("do_DELETE", (RAIZ / "backend" / "app.py").read_text(encoding="utf-8"))

    @unittest.skipUnless((VENV / "bandit").exists(), "bandit no está en .venv")
    def test_bandit_sin_hallazgos_altos(self):
        r = subprocess.run([str(VENV / "bandit"), "-r", "backend", "lares", "-q", "-lll", "-f", "json"],
                           cwd=RAIZ, capture_output=True, text=True, check=False)
        altos = [f"{x['filename']}:{x['line_number']} {x['test_id']}" for x in json.loads(r.stdout)["results"]]
        self.assertEqual(altos, [])


class TestLegibilidad(unittest.TestCase):
    @unittest.skipUnless((VENV / "ruff").exists() or shutil.which("ruff"), "ruff no está instalado")
    def test_ruff_limpio(self):
        ruff = str(VENV / "ruff") if (VENV / "ruff").exists() else "ruff"
        r = subprocess.run([ruff, "check", "--select", "F,E9,B", "--output-format", "concise", "backend", "lares", "tests"],
                           cwd=RAIZ, capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_funciones_no_gigantes(self):
        largas = []
        for f, t in arboles():
            for n in ast.walk(t):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name not in COMPLEJAS_CONOCIDAS:
                    lineas = n.end_lineno - n.lineno + 1
                    if lineas > MAX_LINEAS_FUNCION: largas.append(f"{f.name}:{n.name} ({lineas} líneas)")
        self.assertEqual(largas, [])

    def test_modulos_nuevos_documentados(self):
        for nombre in ("portales.py", "anuncios.py", "api_v2.py", "routing.py"):
            t = ast.parse((RAIZ / "backend" / nombre).read_text(encoding="utf-8"))
            self.assertTrue(ast.get_docstring(t), nombre)


class TestEficiencia(unittest.TestCase):
    @unittest.skipUnless((VENV / "radon").exists(), "radon no está en .venv")
    def test_complejidad(self):
        r = subprocess.run([str(VENV / "radon"), "cc", "-j", "backend", "lares"], cwd=RAIZ, capture_output=True, text=True, check=False)
        malas = [f"{f}:{b['name']} ({b['complexity']})" for f, bloques in json.loads(r.stdout).items() for b in bloques
                 if b["complexity"] > MAX_COMPLEJIDAD and b["name"] not in COMPLEJAS_CONOCIDAS]
        self.assertEqual(malas, [])

    def test_sin_lecturas_sin_limite_de_red(self):
        """Lo que se descarga de fuera se lee con tope (r.read(N)) en el código nuevo: un portal no puede llenar la memoria."""
        src = (RAIZ / "backend" / "portales.py").read_text(encoding="utf-8")
        self.assertNotIn("r.read()", src)


if __name__ == "__main__":
    unittest.main()
