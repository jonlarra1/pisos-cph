"""Herramienta de Lares para los pisos de Copenhague: la ÚNICA forma de añadir, consultar o cambiar pisos.
Habla con la API del backend (base única); no escribe ningún archivo.

    python3 pisos.py añadir <enlace> [--por jon|unai]
    python3 pisos.py consultar                    resumen + propuestos y los que os gustan
    python3 pisos.py consultar <enlace>           ficha de un piso con su historial
    python3 pisos.py estado <enlace> <gusta|contactado|descartado|propuesto> [motivo…] [--por jon|unai]

Enlaces válidos: anuncios de Lejebolig, Boligportal o EDC (https). La clave va en el archivo .pisos_api_key
junto a la carpeta scripts del perfil (o en la variable PISOS_API_KEY); nunca se escribe en la salida.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("PISOS_API", "https://raspberrypi.tail9f1069.ts.net:8443")
MAPA = "https://jonlarra1.github.io/pisos-cph/"
CLAVE_ARCHIVO = Path(__file__).resolve().parent.parent / ".pisos_api_key"
ESTADOS = ("gusta", "contactado", "descartado", "propuesto")
PATRONES = (  # los mismos que backend/portales.py
    ("Lejebolig", re.compile(r"^https://(?:en\.|www\.)?lejebolig\.dk/lejebolig/(\d{4,10})(?:[/?#]|$)")),
    (
        "Boligportal",
        re.compile(r"^https://(?:www\.)?boligportal\.dk/\S*?(?:id-|rental-apartments/)(\d{4,10})(?:[/?#]|$)"),
    ),
    ("EDC", re.compile(r"^https://(?:www\.)?edc\.dk/leje/\S*?/(\d{6,10})/?(?:[?#]|$)")),
)


class Error(Exception):
    pass


def clave():
    k = os.environ.get("PISOS_API_KEY") or (CLAVE_ARCHIVO.read_text().strip() if CLAVE_ARCHIVO.exists() else "")
    if not k:
        raise Error("falta la clave de la API (.pisos_api_key): avisa a Jon")
    return k


def uid_de(enlace):
    if re.fullmatch(r"(Lejebolig|Boligportal|EDC):\d{4,10}", enlace):
        return enlace
    for portal, pat in PATRONES:
        m = pat.match(enlace.strip())
        if m:
            return f"{portal}:{m.group(1)}"
    raise Error("no es un enlace de anuncio de Lejebolig, Boligportal o EDC")


def llamar(metodo, ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cab = {"Content-Type": "application/json", "User-Agent": "lares-pisos/1.0"}
    if metodo == "POST":
        cab["X-Pisos-Key"] = clave()
    req = urllib.request.Request(API + ruta, data=datos, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", "")
        except ValueError:
            msg = ""
        raise Error(f"la API respondió {e.code}: {msg or e.reason}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise Error(f"no se puede llegar a la API ({type(e).__name__}); prueba en un rato") from None


def ruta_uid(uid):
    return "/api/v2/anuncios/" + urllib.parse.quote(uid)


def linea(a):
    d = a.get("datos", {})
    sitio = d.get("calle") or d.get("ciudad") or a.get("zona") or "?"
    total = (d.get("precio") or 0) + (d.get("aconto") or 0)
    dorm = d.get("dorm") or (d["vaer"] - 1 if d.get("vaer") else "?")
    zona = a.get("zona") or "?"
    return f"[{a['estado']}] {a['uid']} · {sitio} ({zona}) · {total} kr/mes · {dorm} dorm. · {d.get('link')}"


def anadir(enlace, por):
    uid_de(enlace)  # comprueba el enlace antes de llamar
    a = llamar("POST", "/api/v2/enlaces", {"url": enlace.strip(), "quien": por or "lares"})
    print(("Ya estaba: " if a.get("ya_estaba") else "Añadido: ") + linea(a))
    for av in a.get("avisos") or []:
        print(f"  aviso: {av}")
    print(f"Sale en el mapa, en «Propuestos»: {MAPA}")


def consultar(enlace=None):
    if enlace:
        a = llamar("GET", ruta_uid(uid_de(enlace)))
        print(linea(a))
        for av in a.get("avisos") or []:
            print(f"  aviso: {av}")
        for h in (a.get("historial") or [])[:8]:
            print(
                f"  {h.get('ts')}: {h.get('de') or '—'} → {h['a']} ({h.get('quien')}) {h.get('motivo') or ''}".rstrip()
            )
        return
    r = llamar("GET", "/api/v2/resumen")
    print("Resumen:", ", ".join(f"{k} {v}" for k, v in sorted((r.get("estados") or {}).items())) or "vacío")
    lista = llamar("GET", "/api/v2/anuncios?estado=gusta,contactado,propuesto")
    for a in (lista.get("anuncios") or [])[:30]:
        print(linea(a))


def estado(enlace, nuevo, motivo, por):
    if nuevo not in ESTADOS:
        raise Error(f"estado no válido; usa uno de: {', '.join(ESTADOS)}")
    cuerpo = {"estado": nuevo, "quien": por or "lares", **({"motivo": motivo} if motivo else {})}
    a = llamar("POST", ruta_uid(uid_de(enlace)) + "/estado", cuerpo)
    print("Hecho: " + linea(a))


def main(args):
    por = None
    if "--por" in args:
        i = args.index("--por")
        por = (args[i + 1 : i + 2] or [""])[0].lower()
        del args[i : i + 2]
        if por not in ("jon", "unai"):
            raise Error("--por solo puede ser jon o unai")
    if not args or args[0] in ("-h", "--help", "ayuda"):
        print(__doc__)
        return
    orden, resto = args[0].lower(), args[1:]
    if orden in ("añadir", "anadir") and len(resto) == 1:
        anadir(resto[0], por)
    elif orden == "consultar" and len(resto) <= 1:
        consultar(resto[0] if resto else None)
    elif orden == "estado" and len(resto) >= 2:
        estado(resto[0], resto[1].lower(), " ".join(resto[2:])[:300], por)
    else:
        raise Error("orden no válida; mira: python3 pisos.py ayuda")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Error as e:
        print(f"ERROR: {e}")
        sys.exit(1)
