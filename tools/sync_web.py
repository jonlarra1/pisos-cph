"""Copia los datos de data/ a index.html (en el formato que la página arranca más rápido).
Uso: python3 tools/sync_web.py          (después: ./tests/run.sh)

- PISOS y OUT   <- data/pisos.json y data/descartados.json, como literal (pequeños; el monitor de la Pi los lee así).
- RUTAS, DIRECTO <- data/rutas.json y data/directo.json, como JSON.parse('...').
- RAIL, BARRIOS  -> se quedan con su valor actual de index.html, pero también como JSON.parse('...').
Los navegadores leen un texto JSON bastante más rápido que el mismo objeto escrito en JavaScript
(medido: la página arranca ~21 % antes, también en móvil).
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LITERAL = {"PISOS": "pisos.json", "OUT": "descartados.json"}
PARSE = {"RUTAS": "rutas.json", "DIRECTO": "directo.json", "RAIL": None, "BARRIOS": None}


def js_str(txt):
    """Texto JSON -> literal de JavaScript entre comillas simples (seguro dentro de <script>)."""
    return "'" + txt.replace("\\", "\\\\").replace("'", "\\'").replace("</", "<\\/") + "'"


def valor(html, nombre):
    """Valor (objeto Python) de `const NOMBRE = ...;` en cualquiera de los dos formatos."""
    m = re.search(r"^const %s ?= ?(.*?);[ \t]*$" % nombre, html, re.M)
    if not m: raise KeyError(nombre)
    v = m.group(1)
    p = re.fullmatch(r"JSON\.parse\('(.*)'\)", v, re.S)
    if p: v = re.sub(r"\\(.)", lambda x: x.group(1), p.group(1))   # solo hay \\ \' y \/
    return json.loads(v)


INDEX = os.path.join(ROOT, "index.html")


def sync(path=INDEX):
    with open(path, encoding="utf-8") as f: html = f.read()
    def poner(nombre, linea):
        nonlocal html
        pat = re.compile(r"^const %s ?= ?.*;[ \t]*$" % nombre, re.M)
        if len(pat.findall(html)) != 1: raise SystemExit(f"index.html: no hay exactamente una línea const {nombre}")
        html = pat.sub(lambda _: linea, html)
    for nombre, fich in LITERAL.items():
        with open(os.path.join(ROOT, "data", fich), encoding="utf-8") as f: obj = json.load(f)
        poner(nombre, f"const {nombre} = {json.dumps(obj, ensure_ascii=False)};")       # siempre en una línea
    for nombre, fich in PARSE.items():
        if fich:
            with open(os.path.join(ROOT, "data", fich), encoding="utf-8") as f: obj = json.load(f)
        else:
            obj = valor(html, nombre)
        poner(nombre, f"const {nombre}=JSON.parse({js_str(json.dumps(obj, ensure_ascii=False, separators=(',', ':')))});")
    with open(path, "w", encoding="utf-8") as f: f.write(html)
    return html


if __name__ == "__main__":
    h = sync(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "index.html"))
    print(f"index.html actualizado ({len(h)//1024} KB): PISOS, OUT, RUTAS, DIRECTO desde data/; RAIL y BARRIOS en formato rápido")
