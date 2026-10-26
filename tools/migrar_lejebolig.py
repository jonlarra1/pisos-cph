"""Migración de Lejebolig a Boligportal (decisión de Jon, 27-oct): Lares deja de buscar en Lejebolig.

Mete en la base única (API v2 de la Pi), como añadidos por Jon:
- los anuncios de Boligportal que son el mismo piso que un propuesto de Lejebolig, con una nota de dónde venían;
- los propuestos de Lejebolig sin pareja en Boligportal, con la nota «solo en Lejebolig» (así no se pierden cuando
  Lares deja de recordarlos).
El de Teglholmens Østkaj (Lejebolig 1910801) no se mete: es el 4641637 que ya está en el mapa.

Uso: python3 tools/migrar_lejebolig.py            (solo enseña lo que haría)
     python3 tools/migrar_lejebolig.py --aplicar  (lo hace; ~16 peticiones, con pausa)
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.environ.get("PISOS_API", "https://raspberrypi.tail9f1069.ts.net:8443")
LB = "https://en.lejebolig.dk/lejebolig/"
BP = "https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/"
HOY = "27-10-2026"
PAREJAS = (  # (enlace de Boligportal, nota)
    (BP + "131m2-4-vaer-id-5170785", f"Antes en Lejebolig ({LB}1608732): mismo piso, 131 m², 21.995 kr. Migrado el {HOY}."),
    (BP + "92m2-4-vaer-id-5681028", f"Antes en Lejebolig ({LB}1917597): Glostrup, 92 m², 14.950 kr. Migrado el {HOY}."),
    (
        BP + "95m2-4-vaer-id-5484666",
        f"Probablemente el de Lejebolig {LB}1911591 (Hørkær, 15.700 kr; allí ponía 97 m²). Migrado el {HOY}: revisadlo.",
    ),
)
SOLO_LEJEBOLIG = ("1812509", "1837797", "1914884", "1698735", "1608727")
NOTA_SOLO = f"Solo en Lejebolig: no está en Boligportal (revisado el {HOY})."


def clave_web():
    """La clave de escritura de la web (la misma que lleva index.html)."""
    m = re.search(r'API_KEY="([^"]+)"', open(os.path.join(ROOT, "index.html"), encoding="utf-8").read())
    if not m:
        sys.exit("no encuentro API_KEY en index.html")
    return m.group(1)


def post(ruta, cuerpo, clave):
    req = urllib.request.Request(
        API + ruta,
        data=json.dumps(cuerpo).encode(),
        headers={"Content-Type": "application/json", "X-Pisos-Key": clave, "Origin": "https://jonlarra1.github.io"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def trabajos():
    return [(url, nota) for url, nota in PAREJAS] + [(LB + pid, NOTA_SOLO) for pid in SOLO_LEJEBOLIG]


def main(aplicar):
    clave = clave_web() if aplicar else ""
    for url, nota in trabajos():
        if not aplicar:
            print(f"añadiría {url}\n   nota: {nota}")
            continue
        code, a = post("/api/v2/enlaces", {"url": url, "quien": "jon"}, clave)
        if code != 200:
            print(f"ERROR {code} {url}: {a.get('error')}")
            continue
        time.sleep(1.5)
        code2, _ = post(f"/api/v2/anuncios/{urllib.parse.quote(a['uid'])}/nota", {"texto": nota, "quien": "jon"}, clave)
        print(f"{'ya estaba' if a.get('ya_estaba') else 'añadido'} {a['uid']} [{a['estado']}] · nota {code2}")
        time.sleep(1.5)


if __name__ == "__main__":
    main("--aplicar" in sys.argv[1:])
