#!/usr/bin/env python3
"""piso_watch_monitor.py — vigilante de alquiler en Copenhague para Jon y Unai (v2, 2026-10-21).

Corre en el cron de Lares cada 3 h, sin LLM. Pasos:
 1. Lee config.json del buzón compartido con el mapa (pisos-bridge/): filtros, zonas
    permitidas (editables desde la web) y pisos que ya tienen estado.
 2. Lee el mapa publicado (pisos.json + descartados.json en GitHub Pages) para no repetir.
 3. Descarga los listados de Boligportal y EDC (páginas adaptativas, ver PAGINAS) y, solo para los
    que pasan el primer filtro, abre la ficha (máx. MAX_DETAIL por pasada, con caché).
 4. Aplica los filtros en código y escribe:
      candidatos.json  -> los que pasan (el mapa los muestra como «Propuestos por Lares»)
      rechazados.json  -> últimos rechazados con el motivo exacto
 5. Imprime solo los candidatos NUEVOS (salida vacía = el agente no se despierta).
"""

import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
BASE = Path(os.environ.get("PISO_WATCH_BASE", "/opt/data/vault/Vivienda/piso-watch"))  # variables solo para los tests
BRIDGE = Path(os.environ.get("PISOS_BRIDGE", "/opt/data/vault/Vivienda/pisos-bridge"))
DB = BASE / "pisos.db"
MAP_URLS = [
    "https://jonlarra1.github.io/pisos-cph/data/pisos.json",
    "https://jonlarra1.github.io/pisos-cph/data/descartados.json",
]
# Los portales ordenan por más nuevo. Páginas adaptativas: como mínimo MIN_PAGES (lo de siempre) y se sigue
# mientras salgan anuncios que Lares no ha visto nunca, hasta MAX_PAGES. Con pasadas cada 3 h casi siempre
# se para en el mínimo; si un día entran muchos anuncios, no se pierden los que bajan de página.
# Lejebolig ya NO se busca (decisión de Jon, 27-oct): sus enlaces solo entran a mano (chat o barra de la web).
PAGINAS = {  # portal: (url de la página i, mínimo, máximo)
    "boligportal": (
        lambda i: f"https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/?max_monthly_rent=23000&offset={18 * i}",
        3,
        8,
    ),
}
# EDC (edc.dk): API JSON de su buscador. Se pide por códigos postales de las zonas activas (más nuevo primero).
# Trae anuncios propios de EDC y copias de Boligportal (esas se guardan con su id de Boligportal, sin duplicar).
EDC_API = "https://www.edc.dk/api/v1/cases/quick-search"
EDC_PAGINAS = (1, 3)  # por grupo de códigos postales: mínimo y máximo de páginas de 100
MAX_DETAIL_EDC = 12  # fichas de EDC por pasada: lo de EDC no trae fecha en el listado, así que
# no se propone hasta abrir su ficha (lo que no cabe espera a la siguiente pasada)
EDC_MAX_URL = 1400  # con URLs muy largas EDC responde 404: se parten los códigos en grupos


def _r(a, b):
    return list(range(a, b + 1))


ZONA_POSTNR = {  # zona del mapa -> códigos postales (los de K, V y Frederiksberg C son rangos)
    "Indre By": _r(1050, 1499),
    "Holmen": _r(1432, 1439),
    "Vesterbro": _r(1500, 1799),
    "Frederiksberg": _r(1800, 2000),
    "Østerbro": [2100, 2150],
    "Nørrebro": [2200],
    "Amager": [2300],
    "Islands Brygge": [2300],
    "Ørestad": [2300],
    "Nordvest": [2400],
    "Bispebjerg": [2400],
    "Sydhavnen": [2450],
    "Valby": [2500],
    "Glostrup": [2600],
    "Rødovre": [2610],
    "Hvidovre": [2650],
    "Brønshøj": [2700],
    "Husum": [2700],
    "Vanløse": [2720],
    "Herlev": [2730],
    "Skovlunde": [2740],
    "Ballerup": [2750],
    "Måløv": [2760],
    "Lyngby": [2800],
    "Gentofte": [2820],
    "Virum": [2830],
    "Søborg": [2860],
    "Buddinge": [2860],
    "Gladsaxe": [2860],
    "Bagsværd": [2880],
    "Hellerup": [2900],
    "Charlottenlund": [2920],
    "Klampenborg": [2930],
}
MAX_ACTIVO = 8  # anuncios comprobados por pasada para ver si siguen publicados
# pisos añadidos por el chat (base única del backend): también se comprueba si siguen publicados
PISOS_API = os.environ.get("PISOS_API", "https://raspberrypi.tail9f1069.ts.net:8443")
ORIGENES_CHAT = ("lares", "jon", "unai", "web", "import")
RETIRADO_DIAS = 14  # cuánto se guarda un retirado en retirados.json (el backend lo aplica una vez)
CUARENTENA_DIAS = 3  # un propuesto retirado sigue en candidatos.json estos días (la web lo enseña 48 h)
RECUERDO_DIAS = 7  # un candidato que ya no sale en las páginas leídas se sigue proponiendo estos días
MAX_DETAIL = 8  # fichas abiertas por pasada (ritmo suave)
DEFAULT_FILTROS = {"precio_max": 23000, "vaer_min": 4, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15"}
BASE.mkdir(parents=True, exist_ok=True)
BRIDGE.mkdir(parents=True, exist_ok=True)
STATS = {}  # resumen de la pasada para lares_runs.jsonl

# Texto -> zona del mapa. Las claves más largas se prueban primero.
ZONA_ALIAS = {
    "københavn ø": "Østerbro",
    "kobenhavn o": "Østerbro",
    "østerbro": "Østerbro",
    "osterbro": "Østerbro",
    "københavn s": "Amager",
    "amager": "Amager",
    "islands brygge": "Islands Brygge",
    "ørestad": "Ørestad",
    "orestad": "Ørestad",
    "holmen": "Holmen",
    "margretheholm": "Holmen",
    "strandlodsvej": "Amager",
    "københavn n": "Nørrebro",
    "nørrebro": "Nørrebro",
    "norrebro": "Nørrebro",
    "københavn nv": "Nordvest",
    "nordvest": "Nordvest",
    "bispebjerg": "Bispebjerg",
    "københavn v": "Vesterbro",
    "vesterbro": "Vesterbro",
    "københavn sv": "Sydhavnen",
    "sydhavn": "Sydhavnen",
    "teglholmen": "Sydhavnen",
    "sluseholmen": "Sydhavnen",
    "københavn k": "Indre By",
    "indre by": "Indre By",
    "frederiksberg": "Frederiksberg",
    "valby": "Valby",
    "vanløse": "Vanløse",
    "vanlose": "Vanløse",
    "brønshøj": "Brønshøj",
    "bronshoj": "Brønshøj",
    "husum": "Husum",
    "herlev": "Herlev",
    "søborg": "Søborg",
    "soborg": "Søborg",
    "buddinge": "Buddinge",
    "gladsaxe": "Gladsaxe",
    "bagsværd": "Bagsværd",
    "lyngby": "Lyngby",
    "virum": "Virum",
    "hellerup": "Hellerup",
    "gentofte": "Gentofte",
    "charlottenlund": "Charlottenlund",
    "klampenborg": "Klampenborg",
    "rødovre": "Rødovre",
    "rodovre": "Rødovre",
    "ballerup": "Ballerup",
    "skovlunde": "Skovlunde",
    "måløv": "Måløv",
    "malov": "Måløv",
    "glostrup": "Glostrup",
    "hvidovre": "Hvidovre",
}
BAD_TITLE = re.compile(r"\broom for\b|\broom in\b|\bværelse til\b|student|kollegie|\bshared room\b", re.I)
NO_SHARE = re.compile(
    r"bofællesskab ikke|ikke bofællesskab|ikke delevenlig|ikke egnet til deling|no shar|"
    r"not suitable for shar|kun til par|kun par|kun familie|only for couples|only famil|"
    r"kun virksomhed|only compan|corporate only|expats? only|only expats?",
    re.I,
)
YES_SHARE = re.compile(
    r"delevenlig|bofællesskab mulig|egnet til deling|suitable for shar|sharing (is )?(possible|allowed|ok)", re.I
)
SHORT = re.compile(
    r"(max(imum)?|maks(imalt)?)\.?\s*(1[0-2]|[1-9])\s*(måneder|months|mdr)|tidsbegrænset|limited period|"
    r"short[- ]term|korttids",
    re.I,
)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "da,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def url_ok(u):
    """Enlaces con «ø» sin codificar (algunos de descartados.json): urllib no los abre tal cual."""
    return urllib.parse.quote(u, safe=":/?=&%#")


def _estado_bp(o, i):
    """Estado («ACTIVE_APPROVED»…) del anuncio i dentro del JSON de la ficha de Boligportal."""
    if isinstance(o, dict):
        if o.get("id") == i and "state" in o:
            return o["state"]
        for v in o.values():
            r = _estado_bp(v, i)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _estado_bp(v, i)
            if r:
                return r
    return None


def estado_anuncio(link):
    """('activo' | 'retirado' | 'desconocido', motivo). Solo señales claras cuentan como retirado:
    Boligportal redirige con ?ad_rented_out_redirect=… («udlejet») o el anuncio trae un estado que no es ACTIVE_*;
    Lejebolig responde 404/410 («no longer available»).
    Error de red o respuesta rara = desconocido: nunca se retira por un fallo."""
    lid = num_id(link)
    req = urllib.request.Request(url_ok(link), headers={"User-Agent": UA, "Accept-Language": "da,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            code, fin, html = r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, fin, html = e.code, e.geturl() or "", ""
        e.close()
    except Exception as e:
        return "desconocido", type(e).__name__
    if code in (404, 410):
        return "retirado", "el portal ya no lo publica"
    if code != 200:
        return "desconocido", f"HTTP {code}"
    if "ad_rented_out_redirect" in fin:
        return "retirado", "marcado como alquilado («udlejet»)"
    if "boligportal" in link:
        m = re.search(r'<script id="store" type="application/json">(.*?)</script>', html, re.S)
        try:
            st = _estado_bp(json.loads(m.group(1)), int(lid)) if (m and lid) else None
        except ValueError:
            st = None
        if st:
            return ("activo", st) if st.startswith("ACTIVE") else ("retirado", f"estado {st} en el portal")
        return "desconocido", "sin estado en la ficha"
    if lid and lid in fin:
        return "activo", ""
    return "desconocido", "redirección inesperada"


def write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.stem, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def num_id(url_or_id):
    m = re.search(
        r"lejebolig/(\d+)|id-(\d+)|rental-apartments/(\d+)|edc\.dk/\S*/(\d{6,})/?(?:[?#]|$)|^(?:lb|bp|edc)?(\d+)$",
        str(url_or_id),
    )
    return next((g for g in m.groups() if g), None) if m else None


# ------------------------------------------------------------------ listados
def parse_boligportal(html):
    """BoligPortal trae los anuncios en <script id="store" type="application/json">."""
    m = re.search(r'<script id="store" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return {}
    store = json.loads(m.group(1))
    ads = []

    def walk(o):
        if isinstance(o, dict):
            if "monthly_rent" in o and "id" in o and "url" in o:
                ads.append(o)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(store)
    out = {}

    def num(v):
        return int(float(v)) if isinstance(v, (int, float)) else None

    for a in ads:
        lid = str(a["id"])
        loc = a.get("location") or {}
        area, street = a.get("city_area") or a.get("city_level_2") or "", a.get("street_name") or ""
        url = a.get("url") or ""
        out[lid] = {
            "portal": "Boligportal",
            "id": lid,
            "link": ("https://www.boligportal.dk" + url) if url.startswith("/") else url,
            "titulo": a.get("title") or f"{street}, {area}",
            "lugar": f"{street} {a.get('street_number') or ''} {a.get('postal_code') or ''} {area}".strip(),
            "precio": num(a.get("monthly_rent")) or 0,
            "aconto": num(a.get("monthly_rent_extra_costs")),
            "dep": num(a.get("deposit")),
            "pre": num(a.get("prepaid_rent")),
            "m2": num(a.get("size_m2")),
            "vaer": num(a.get("rooms")),
            "disp": (a.get("available_from") or "")[:10] or None,
            "contrato_meses": num(a.get("rental_period")),
            "lat": loc.get("lat"),
            "lon": loc.get("lng") or loc.get("lon"),
            "plano": next(
                (i.get("url") for i in (a.get("images") or []) if isinstance(i, dict) and i.get("is_floor_plan")), None
            ),
            "fotos": [i["url"] for i in (a.get("images") or []) if isinstance(i, dict) and i.get("url")][:15],
            **rasgos_bp(a.get("features") or {}),
            "texto": (a.get("description") or "")[:4000],
        }
    return out


def edc_urls(zonas, F):
    """URLs base de EDC (sin pageNr) para las zonas activas, partidas para que ninguna pase de EDC_MAX_URL."""
    codigos = sorted({c for z, on in zonas.items() if on for c in ZONA_POSTNR.get(z, [])})
    base = (
        f"{EDC_API}?c-gruppe=Rent&c-type=0&ejd-typer=31&rum={int(F['vaer_min'])}"
        f"&leje=0-{int(F['precio_max'])}&pageSize=100&sort=26&postnr="
    )
    urls, grupo = [], []
    for c in codigos:
        if grupo and len(base) + len(",".join(map(str, grupo + [c]))) > EDC_MAX_URL:
            urls.append(base + ",".join(map(str, grupo)))
            grupo = []
        grupo.append(c)
    if grupo:
        urls.append(base + ",".join(map(str, grupo)))
    return urls


def parse_edc(text):
    """JSON de la API de EDC -> anuncios. Las copias de Boligportal («ex<id>») salen como Boligportal con su id."""
    out = {}

    def val(o):
        return (
            int(o["value"]) if isinstance(o, dict) and isinstance(o.get("value"), (int, float)) and o["value"] else None
        )

    for a in json.loads(text).get("items") or []:
        if a.get("isRented") or a.get("isSold"):
            continue
        cn, url = str(a.get("caseNumber") or ""), a.get("urlPath") or ""
        geo = a.get("geoCoordinates") or {}
        it = {
            "titulo": f"{a.get('estateTypeName') or 'Lejlighed'}, {a.get('address') or ''}".strip(", "),
            "lugar": f"{a.get('address') or ''} {a.get('zipCode') or ''} {a.get('city') or ''}".strip(),
            "precio": val(a.get("rent")) or 0,
            "m2": val(a.get("livingArea")),
            "vaer": val(a.get("rooms")),
            "lat": geo.get("latitude"),
            "lon": geo.get("longitude"),
        }
        imgs = [
            i
            for i in (a.get("images") or [])
            if isinstance(i, dict) and str(i.get("src", "")).startswith("https://billeder.edc.dk/")
        ]
        if imgs:
            it["fotos"] = [max(imgs, key=lambda i: i.get("width") or 0)["src"]]
        if a.get("isEdcCase") and cn.isdigit() and url.startswith("/leje/"):
            out[cn] = {"portal": "EDC", "id": cn, "link": "https://www.edc.dk" + url, "_edc": True, **it}
        elif "boligportal.dk" in url:
            lid = num_id(url.split("?")[0])
            if lid:
                out[lid] = {"portal": "Boligportal", "id": lid, "link": url.split("?")[0], "_edc": True, **it}
    return out


MESES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "maj": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "nov": 11,
    "dec": 12,
}


def detalle_edc(item):
    """Ficha de EDC: «Ledig fra d. 1. nov. 2026», «Depositum 3 måneders husleje, 45.600 kr.», «Forudbetalt leje …, "
    "15.200 kr.»,
    «Aconto Varmeudgifter 500 kr.», «Lejeperiode Ubegrænset», «Rum/værelser 3/2» (rum / soveværelser)."""
    h = fetch(item["link"], timeout=15)
    t = unescape(re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S))
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
    d = {"texto": t[:6000]}

    def kr(m):
        return int(re.sub(r"\D", "", m.group(1))) if m else None

    d["dep"] = kr(re.search(r"Depositum (?:[^,]{0,40}, )?([\d.]+) kr", t))
    d["pre"] = kr(re.search(r"Forudbetalt leje (?:[^,]{0,40}, )?([\d.]+) kr", t))
    ac = [int(x.replace(".", "")) for x in re.findall(r"Aconto [A-Za-zæøåÆØÅ ]{2,30}? ([\d.]+) kr", t)]
    d["aconto"] = sum(ac) if ac else None
    mp = re.search(r"Lejeperiode (\S+)", t)
    if mp:
        v = mp.group(1).lower()
        d["contrato_meses"] = (
            0 if v.startswith("ubegr") else (int(re.sub(r"\D", "", v)) if re.search(r"\d", v) else None)
        )

    def sj(k):
        return (lambda m: (m.group(1) == "Ja") if m else None)(re.search(k + r" (Ja|Nej)\b", t))

    d["balcon"], d["ascensor"] = sj(r"Altan/terrasse"), sj(r"Elevator")
    d["amueblado"] = amueblado_de(t)
    md_ = re.search(
        r".*Hent boligpræsentation (.*?)(?: Læs hele sagsbeskrivelsen|$)", t
    )  # el último botón: va justo antes del texto
    if md_:
        d["descripcion"] = md_.group(1).strip()[:1500]
    mr = re.search(r"Rum/værelser (\d+)/(\d+)", t)
    if mr:
        d["vaer"], d["dorm"] = int(mr.group(1)), int(mr.group(2))
    md = re.search(r"(?:Ledig fra|Tilgængelig fra)(?: d\.)? (\d{1,2})\. ?([a-zæøå]{3})[a-zæøå]*\.? (\d{4})", t, re.I)
    if md and md.group(2).lower() in MESES:
        d["disp"] = f"{md.group(3)}-{MESES[md.group(2).lower()]:02d}-{int(md.group(1)):02d}"
    elif re.search(r"(?:Ledig fra|Tilgængelig fra)(?: d\.)? (Snarest|Straks|Hurtigst muligt)", t, re.I):
        d["disp"] = "ya"
    return d


def rasgos_bp(f):
    """features de Boligportal -> campos nuestros (None = el anuncio no lo dice)."""

    def b(k):
        return f.get(k) if isinstance(f.get(k), bool) else None

    return {
        "amueblado": b("furnished"),
        "compartible": b("shareable"),
        "ascensor": b("elevator"),
        "balcon": b("balcony"),
        "mascotas": b("pet_friendly"),
        "lavadora": b("washing_machine"),
        "parking": b("parking"),
    }


FURN_NO = re.compile(r"umøbleret|ikke møbleret|unfurnished|not furnished", re.I)
FURN_SI = re.compile(r"(?<!u)møbleret|(?<!un)furnished", re.I)


def amueblado_de(texto):
    """Del texto del anuncio: True / False / None (no lo dice). «umøbleret» manda sobre «møbleret»."""
    if FURN_NO.search(texto or ""):
        return False
    if FURN_SI.search(texto or ""):
        return True
    return None


def detalle(item):
    """Abre la ficha para sacar contrato y texto (y fecha si la hay)."""
    if item.get("portal") == "EDC":
        return detalle_edc(item)
    h = fetch(item["link"], timeout=15)
    t = unescape(re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S))
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
    d = {"texto": t[:6000]}

    # depósito y prepago por separado
    # (Lejebolig: «an advance rent payment of DKK X and a security deposit of DKK Y» / «Deposit: X»)
    def kr(m):
        return int(re.sub(r"\D", "", next(g for g in m.groups() if g))) if m else None

    d["pre"] = kr(re.search(r"Rent paid in advance:\s*([\d,.]+)", t)) or kr(
        re.search(
            r"advance rent payment of DKK ([\d,.]+)|upfront rent payment of DKK ([\d,.]+)|[Ff]orudbetalt "
            r"leje:?\s*(?:DKK|kr\.?)?\s*([\d.,]+)",
            t,
        )
    )
    # la ficha técnica («Deposit: X») manda; la frase automática a veces trae mal el depósito
    d["dep"] = kr(re.search(r"\bDeposit:\s*([\d,.]+)|[Dd]epositum:?\s*(?:DKK|kr\.?)?\s*([\d.,]+)", t)) or kr(
        re.search(r"security deposit of DKK ([\d,.]+)", t)
    )
    mp = re.search(r"Rental period\s+(\w+)|Lejeperiode\s+(\w+)", t)
    if mp:
        v = (mp.group(1) or mp.group(2)).lower()
        d["contrato_meses"] = (
            0 if v in ("unlimited", "ubegrænset") else (int(re.sub(r"\D", "", v)) if re.search(r"\d", v) else None)
        )
    d["amueblado"] = amueblado_de(t)
    md = re.search(r'"available_from"\s*:\s*"(\d{4}-\d{2}-\d{2})', h) or re.search(
        r"(?:Available from|Ledig fra|Overtagelse)\s*:?\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})", t
    )
    if md:
        d["disp"] = md.group(1) if md.lastindex == 1 else f"{md.group(3)}-{int(md.group(2)):02d}-{int(md.group(1)):02d}"
    elif re.search(r"(Available from|Ledig fra)\s*:?\s*(As soon as possible|Snarest|Immediately|Straks)", t, re.I):
        d["disp"] = "ya"
    return d


# ------------------------------------------------------------------ filtros
def zona_de(item):
    hay = f"{item.get('titulo', '')} {item.get('lugar', '')}".lower()
    for k in sorted(ZONA_ALIAS, key=len, reverse=True):
        if re.search(rf"(?<![a-zæøå]){re.escape(k)}(?![a-zæøå])", hay):
            return ZONA_ALIAS[k]
    return None


def evaluar(item, F, zonas):
    """(ok, motivo_o_None, avisos)"""
    avisos = []
    if BAD_TITLE.search(item.get("titulo", "")):
        return False, "es una habitación / residencia", avisos
    if item["precio"] > F["precio_max"]:
        return False, f"precio {item['precio']} > {F['precio_max']}", avisos
    if item.get("vaer") is not None and item["vaer"] < F["vaer_min"]:
        return False, f"{item['vaer']} værelser (< {F['vaer_min']}: menos de 3 dormitorios)", avisos
    if item.get("vaer") is None:
        avisos.append("værelser sin dato")
    z = zona_de(item)
    item["zona"] = z
    if z is None:
        return False, "zona no reconocida / fuera de las vigiladas", avisos
    if not zonas.get(z, False):
        return False, f"zona excluida: {z}", avisos
    disp = item.get("disp")
    if disp == "ya":
        return False, "libre ya (no hacia el 1-1-2027)", avisos
    if disp:
        if not (F["disp_desde"] <= disp <= F["disp_hasta"]):
            return False, f"libre el {disp} (fuera de {F['disp_desde']}..{F['disp_hasta']})", avisos
    else:
        avisos.append("fecha de entrada sin confirmar")
    cm = item.get("contrato_meses")
    txt = item.get("texto", "")
    if F.get("contrato_largo", True) and ((cm and 0 < cm <= 12) or SHORT.search(txt)):
        return False, f"contrato corto ({cm or '≤12'} meses)", avisos
    if F.get("compartir", True):
        if item.get("compartible") is False:
            return False, "no permite compartir (dato del portal)", avisos
        if NO_SHARE.search(txt):
            return False, "no permite compartir (texto del anuncio)", avisos
        if item.get("compartible") is not True and not YES_SHARE.search(txt):
            avisos.append("compartir sin confirmar")
    am, quiere = item.get("amueblado"), F.get("amueblado", "igual")
    if quiere in ("si", "no"):
        if am is not None and am != (quiere == "si"):
            return False, "amueblado" if am else "sin amueblar", avisos
        if am is None:
            avisos.append("amueblado sin confirmar")
    pl = item.get("plano_dorm")  # lo escribe Lares (IA) en planos.json tras mirar el plano
    dmin = int(F.get("dorm_min", 3))
    if isinstance(item.get("dorm"), int) and item["dorm"] < dmin:  # EDC da «Rum/værelser 4/2» en la ficha
        return False, f"ficha: {item['dorm']} dormitorios", avisos
    if isinstance(pl, int):
        if pl < dmin:
            return False, f"plano: {pl} dormitorios", avisos
        avisos.append(f"plano revisado: {pl} dormitorios")
    elif item.get("plano"):
        avisos.append("plano pendiente de revisar")
    else:
        avisos.append(f"sin plano: {dmin} dormitorios por confirmar")
    return True, None, avisos


# ---------------------------------------------------------------------- main
# Campos de cada anuncio que salen hacia el buzón (candidatos.json / rechazados.json)
CAMPOS_SALIDA = (
    "id",
    "portal",
    "link",
    "titulo",
    "lugar",
    "zona",
    "precio",
    "aconto",
    "dep",
    "pre",
    "m2",
    "vaer",
    "disp",
    "contrato_meses",
    "lat",
    "lon",
    "plano",
    "plano_dorm",
    "dorm",
    "fotos",
    "amueblado",
    "compartible",
    "ascensor",
    "balcon",
    "mascotas",
    "lavadora",
    "parking",
    "descripcion",
)


def main():
    """Una pasada del vigilante (sin IA). Cada paso es una función: leer → evaluar → comprobar → escribir."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    F, zonas, planos, decididos = leer_config()
    known, mapa = pisos_conocidos()
    con = abrir_db()
    vistos_antes = {r[0] for r in con.execute("SELECT id FROM v2_eval")} | known
    listings, errors, paginas = leer_portales(vistos_antes, zonas, F)
    ev = evaluar_anuncios(con, listings, known, planos, F, zonas, decididos, now)
    ev.cands += candidatos_recordados(con, listings, known)
    comprobados, nuevos_ret = comprobar_publicados(con, mapa, ev.cands, listings, now)
    ret = retirados_en_cuarentena(con, ev.cands)
    write_json(
        BRIDGE / "retirados.json",
        {"actualizado": now, "pisos": {k: {**v, "desde": v["desde"][:19]} for k, v in ret.items()}},
    )
    STATS.update(comprobados=comprobados, retirados_nuevos=nuevos_ret, retirados=len(ret))
    con.commit()
    historial(con, now)
    con.close()

    ev.cands.sort(key=lambda x: (x["visto"], -x["precio"]), reverse=True)
    write_json(BRIDGE / "candidatos.json", {"actualizado": now, "filtros": F, "pisos": ev.cands, "errores": errors})
    write_json(BRIDGE / "rechazados.json", {"actualizado": now, "pisos": ev.rech[:400]})
    resumen(listings, paginas, errors, ev, known)


def leer_config():
    """Filtros y zonas (config.json, los escribe el backend), planos revisados y pisos ya decididos en la web."""
    try:
        cfg = json.loads((BRIDGE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    F = {**DEFAULT_FILTROS, **(cfg.get("filtros") or {})}
    if "dorm_min" in F:
        F["vaer_min"] = int(F["dorm_min"]) + 1
    zonas = cfg.get("zonas") or {}
    try:  # {"<id>": {"dorm": 3, "nota": "..."}} — revisión de planos hecha por Lares (IA)
        planos = json.loads((BRIDGE / "planos.json").read_text(encoding="utf-8"))
    except Exception:
        planos = {}
    # Lo decidido en la web (gusta/contactado/descartado) se sigue evaluando y escribiendo: el backend
    # lo oculta. Así, si alguien lo vuelve a poner en «nuevo», reaparece al momento.
    decididos = {str(k) for k, v in (cfg.get("con_estado") or {}).items() if (v or {}).get("st") != "nuevo"}
    return F, zonas, planos, decididos


def pisos_conocidos():
    """Ids ya en el mapa o en la lista fija de descartados (y enlaces en sus notas), y los pisos del mapa."""
    known, mapa = set(), []
    for u in MAP_URLS:
        try:
            datos = json.loads(fetch(u))
            if u.endswith("/pisos.json"):
                mapa = [x for x in datos if isinstance(x, dict) and x.get("link")]
            for x in datos:
                i = num_id(x.get("link", ""))
                if i:
                    known.add(i)
                for extra in re.findall(r"https?://\S+", x.get("notas", "") or ""):
                    j = num_id(extra)
                    if j:
                        known.add(j)
        except Exception:
            pass
    return known, mapa


def abrir_db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS v2_detalle (id TEXT PRIMARY KEY, data TEXT, ts TEXT)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS v2_eval (id TEXT PRIMARY KEY, portal TEXT, link TEXT, ok INTEGER, motivo TEXT, "
        "item TEXT, first_seen TEXT, last_seen TEXT, avisado INTEGER DEFAULT 0)"
    )
    return con


def leer_portales(vistos_antes, zonas, F):
    """Páginas de resultados de Boligportal y EDC (Lejebolig no). Páginas adaptativas: mínimo fijo y se sigue
    mientras haya anuncios nunca vistos (hasta el máximo)."""
    listings, errors, paginas = {}, [], {}
    for name, parser in (("boligportal", parse_boligportal),):
        url_de, minimo, maximo = PAGINAS[name]
        for i in range(maximo):
            try:
                page = parser(fetch(url_de(i)))
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}")
                break
            listings.update(page)
            paginas[name] = i + 1
            time.sleep(2)
            if not page:
                break  # se acabaron los anuncios
            if i + 1 >= minimo and not (set(page) - vistos_antes):
                break  # todo lo de esta página ya lo conocíamos
    minimo, maximo = EDC_PAGINAS  # EDC: mismas reglas, por cada grupo de códigos postales
    for base in edc_urls(zonas, F):
        for i in range(maximo):
            try:
                txt = fetch(f"{base}&pageNr={i + 1}")
                page = parse_edc(txt)
                ultima = int(json.loads(txt).get("totalPages") or 1) <= i + 1
            except Exception as e:
                errors.append(f"edc: {type(e).__name__}")
                break
            for lid, it in page.items():
                listings.setdefault(lid, it)  # si Boligportal ya lo trajo, vale su versión
            paginas["edc"] = paginas.get("edc", 0) + 1
            time.sleep(2)
            if ultima or not page:
                break  # no hay más páginas
            if i + 1 >= minimo and not (set(page) - vistos_antes):
                break
    return listings, errors, paginas


class Evaluacion:
    """Resultado de evaluar los anuncios leídos: listas para el buzón y lo gastado del cupo de fichas."""

    def __init__(self):
        self.cands, self.rech, self.nuevos = [], [], []
        self.budget, self.budget_edc, self.pendientes = MAX_DETAIL, MAX_DETAIL_EDC, 0


def evaluar_anuncios(con, listings, known, planos, F, zonas, decididos, now):
    """Evalúa cada anuncio con los filtros; abre la ficha (con cupo) de los que pasan lo barato, y guarda en v2_eval."""
    # fichas leídas antes de que Lares sacara amueblado/balcón/ascensor:
    # se vuelven a abrir (con el mismo cupo por pasada)
    cache = {
        i: d
        for i, d in ((r[0], json.loads(r[1])) for r in con.execute("SELECT id, data FROM v2_detalle"))
        if "amueblado" in d
    }
    ev = Evaluacion()
    orden_ids = sorted(
        listings, key=lambda k: (bool(listings[k].get("_edc")), listings[k]["portal"] != "EDC")
    )  # EDC propios antes que copias
    for lid in orden_ids:
        it = listings[lid]
        if lid in known:
            continue  # ya está en el mapa / descartados fijos
        it.update({k: v for k, v in cache.get(lid, {}).items() if it.get(k) in (None, "")})
        if isinstance((planos.get(lid) or {}).get("dorm"), int):
            it["plano_dorm"] = planos[lid]["dorm"]
        resultado = _evaluar_con_ficha(con, ev, it, lid, lid not in cache, F, zonas, now)
        if resultado is None:
            continue  # de EDC sin ficha: a la siguiente pasada
        _guardar_evaluacion(con, ev, it, lid, *resultado, decididos, now)
    return ev


def _evaluar_con_ficha(con, ev, it, lid, sin_cache, F, zonas, now):
    """(ok, motivo, avisos), o None si es de EDC y no se pudo abrir su ficha (sin ficha no se propone)."""
    ok, motivo, avisos = evaluar(it, F, zonas)
    de_edc = it.get("_edc") and sin_cache
    if ok and de_edc and ev.budget_edc <= 0:
        ev.pendientes += 1
        return None
    if ok and sin_cache and (ev.budget_edc if de_edc else ev.budget) > 0:  # pasa lo barato: abrir ficha para lo caro
        try:
            d = detalle(it)
            if de_edc:
                ev.budget_edc -= 1
            else:
                ev.budget -= 1
            con.execute(
                "INSERT OR REPLACE INTO v2_detalle VALUES (?,?,?)", (lid, json.dumps(d, ensure_ascii=False), now)
            )
            it.update({k: v for k, v in d.items() if v is not None and (k == "texto" or it.get(k) in (None, ""))})
            ok, motivo, avisos = evaluar(it, F, zonas)
        except Exception:
            if de_edc:
                ev.pendientes += 1
                return None
            avisos.append("ficha no disponible")
    return ok, motivo, avisos


def _guardar_evaluacion(con, ev, it, lid, ok, motivo, avisos, decididos, now):
    """Guarda el resultado en v2_eval y lo apunta como candidato (nuevo si nadie lo vio ni decidió) o rechazado."""
    row = con.execute("SELECT first_seen, avisado FROM v2_eval WHERE id=?", (lid,)).fetchone()
    first = row[0] if row else now
    out = {k: it.get(k) for k in CAMPOS_SALIDA}
    out.update({"avisos": avisos, "visto": first[:10]})
    con.execute(
        "INSERT INTO v2_eval (id, portal, link, ok, motivo, item, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET ok=excluded.ok, motivo=excluded.motivo, item=excluded.item, "
        "last_seen=excluded.last_seen",
        (lid, it["portal"], it["link"], int(ok), motivo, json.dumps(out, ensure_ascii=False), first, now),
    )
    if ok:
        ev.cands.append(out)
        if lid not in decididos and not (row and row[1]):
            ev.nuevos.append(out)
            con.execute("UPDATE v2_eval SET avisado=1 WHERE id=?", (lid,))
    else:
        ev.rech.append({**out, "motivo": motivo})


def candidatos_recordados(con, listings, known):
    """Candidatos de pasadas anteriores que ya no salen en las páginas leídas: se siguen proponiendo RECUERDO_DIAS."""
    limite = datetime.now(timezone.utc).timestamp() - RECUERDO_DIAS * 86400
    recordados = []
    for lid, item, last in con.execute("SELECT id, item, last_seen FROM v2_eval WHERE ok=1"):
        if lid in listings or lid in known:
            continue
        try:
            if (
                json.loads(item).get("portal") == "Lejebolig"
            ):  # ya no se busca ahí (27-oct): esos viven en la base única
                continue
            if datetime.fromisoformat(last).timestamp() < limite:
                continue
            it = json.loads(item)
        except Exception:
            continue
        it["avisos"] = [a for a in (it.get("avisos") or []) if not a.startswith("no visto desde")] + [
            f"no visto desde {last[:10]} (ya no sale en las primeras páginas)"
        ]
        recordados.append(it)
    return recordados


def comprobar_publicados(con, mapa, cands, listings, now):
    """¿Siguen publicados? Pisos del mapa + propuestos que ya no salen en las páginas leídas (los que salen, sí lo
    están). Como mucho MAX_ACTIVO por pasada, los que hace más tiempo que no se miran primero.
    Devuelve (comprobados, retirados nuevos)."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS v2_activo (id TEXT PRIMARY KEY, link TEXT, estado TEXT, motivo TEXT, desde "
        "TEXT, ultimo TEXT)"
    )
    revisar = {num_id(p["link"]): p["link"] for p in mapa if num_id(p["link"])}
    revisar.update({c["id"]: c["link"] for c in cands if c["id"] not in listings})
    revisar.update({i: link for i, link in anuncios_del_chat().items() if i not in listings and i not in revisar})
    prev = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, estado, ultimo FROM v2_activo")}
    orden = sorted(
        (i for i in revisar if (prev.get(i) or ("", ""))[0] != "retirado"), key=lambda i: (prev.get(i) or ("", ""))[1]
    )[:MAX_ACTIVO]
    nuevos_ret = 0
    for i in orden:
        est, mot = estado_anuncio(revisar[i])
        time.sleep(2)
        if est == "desconocido":
            con.execute(
                "INSERT INTO v2_activo (id, link, estado, motivo, desde, ultimo) VALUES (?,?,?,?,?,?) ON "
                "CONFLICT(id) DO UPDATE SET ultimo=excluded.ultimo",
                (i, revisar[i], "desconocido", mot, now, now),
            )
            continue
        nuevos_ret += est == "retirado"
        con.execute(
            "INSERT INTO v2_activo (id, link, estado, motivo, desde, ultimo) VALUES (?,?,?,?,?,?) ON CONFLICT(id) "
            "DO UPDATE SET "
            "estado=excluded.estado, motivo=excluded.motivo, ultimo=excluded.ultimo, "
            "desde=CASE WHEN v2_activo.estado=excluded.estado THEN v2_activo.desde ELSE excluded.desde END",
            (i, revisar[i], est, mot, now, now),
        )
    return len(orden), nuevos_ret


def anuncios_del_chat():
    """{id: enlace} de los pisos añadidos a mano (chat de Lares, web) que siguen abiertos en la base única.
    Lectura pública de la API; si no responde, la pasada sigue sin ellos."""
    try:
        lista = json.loads(fetch(PISOS_API + "/api/v2/anuncios?estado=propuesto,gusta,contactado"))["anuncios"]
    except Exception:
        return {}
    out = {}
    for a in lista:
        link = (a.get("datos") or {}).get("link") or ""
        if a.get("origen") in ORIGENES_CHAT and num_id(link):
            out[num_id(link)] = link
    return out


def _dias_desde(d):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(d)).total_seconds() / 86400


def retirados_en_cuarentena(con, cands):
    """Retirados de los últimos RETIRADO_DIAS. A los candidatos retirados les pone el aviso 🚫 (la web los enseña
    48 h en Descartados) y pasados CUARENTENA_DIAS los quita de la lista. Devuelve {id: retirado}."""
    ret = {
        r[0]: {"link": r[1], "motivo": r[2], "desde": r[3]}
        for r in con.execute("SELECT id, link, motivo, desde FROM v2_activo WHERE estado='retirado'")
        if _dias_desde(r[3]) <= RETIRADO_DIAS
    }
    for c in list(cands):
        r = ret.get(c["id"])
        if not r:
            continue
        if _dias_desde(r["desde"]) > CUARENTENA_DIAS:
            cands.remove(c)
            continue
        c["retirado"] = r["desde"][:10]
        c["avisos"] = [a for a in (c.get("avisos") or []) if not a.startswith(("no visto desde", "🚫"))] + [
            f"🚫 anuncio retirado del portal el {r['desde'][:10]}: {r['motivo']}"
        ]
    return ret


def resumen(listings, paginas, errors, ev, known):
    """Estadísticas de la pasada (van a lares_runs.jsonl) y las líneas «NUEVO |…» que lee Lares para avisar."""
    leidos = {}
    for it in listings.values():
        leidos[it["portal"]] = leidos.get(it["portal"], 0) + 1
    STATS.update(
        leidos=leidos,
        paginas=paginas,
        candidatos=len(ev.cands),
        nuevos=len(ev.nuevos),
        rechazados=len(ev.rech),
        errores=errors,
        fichas_abiertas=MAX_DETAIL - ev.budget,
        fichas_edc=MAX_DETAIL_EDC - ev.budget_edc,
        edc_pendientes=ev.pendientes,
        mapa_conocidos=len(known),
    )
    for n in ev.nuevos:
        print(
            f"NUEVO | {n['zona']} | {n['precio']} kr/mes | {n['m2'] or '?'} m², {n['vaer'] or '?'} vær. | "
            f"{(n['titulo'] or '')[:45]} | {n['link']}" + (f" | ⚠ {', '.join(n['avisos'][:2])}" if n["avisos"] else "")
        )
    if errors and not ev.nuevos:
        print(f"[aviso] fuentes con error: {', '.join(errors)}")


def historial(con, now):
    """pisos-bridge/historial.json para el panel y la web: TODOS los rechazados que Lares ha visto (con motivo, para
    poder recuperar un error) y cuántos anuncios nuevos ve cada día. Sin IA y sin avisos: solo un archivo."""
    try:
        rech, dias = [], {}
        for lid, portal, link, ok, motivo, item, first, last in con.execute(
            "SELECT id, portal, link, ok, motivo, item, first_seen, last_seen FROM v2_eval"
        ):
            d = dias.setdefault((first or "")[:10], {"vistos": 0, "candidatos": 0, "rechazados": 0})
            d["vistos"] += 1
            d["candidatos" if ok else "rechazados"] += 1
            if not ok:
                it = json.loads(item or "{}")
                rech.append(
                    {k: it.get(k) for k in ("id", "portal", "link", "titulo", "zona", "precio", "m2", "vaer", "disp")}
                    | {
                        "id": lid,
                        "portal": it.get("portal") or portal,
                        "link": it.get("link") or link,
                        "motivo": motivo,
                        "visto": (first or "")[:10],
                        "ultimo": (last or "")[:10],
                    }
                )
        rech.sort(key=lambda x: x["ultimo"], reverse=True)
        write_json(
            BRIDGE / "historial.json",
            {"actualizado": now, "por_dia": dict(sorted(dias.items())), "rechazados": rech[:3000]},
        )
    except Exception:
        pass


def cron_status():
    """Copia al buzón el estado de las tareas de pisos de Lares (solo piso-*), para el panel.
    Así el backend no necesita leer la carpeta cron de Lares."""
    try:
        jobs = json.loads(Path("/opt/data/profiles/lares/cron/jobs.json").read_text(encoding="utf-8"))
        jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
        keep = (
            "name",
            "enabled",
            "state",
            "last_status",
            "last_run_at",
            "next_run_at",
            "last_error",
            "last_delivery_error",
            "schedule_display",
        )
        out = [{k: j.get(k) for k in keep} for j in jobs if str(j.get("name", "")).startswith("piso")]
        write_json(
            BRIDGE / "lares_cron.json",
            {"copiado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "jobs": out},
        )
    except Exception:
        pass


def run_log(**kw):
    """Una línea por pasada en pisos-bridge/lares_runs.jsonl (lo lee el panel de estado)."""
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "t": round(datetime.now(timezone.utc).timestamp(), 1),
        **kw,
    }
    p = BRIDGE / "lares_runs.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-500:] if p.exists() else []
        lines.append(json.dumps(rec, ensure_ascii=False))
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


if __name__ == "__main__":
    import time as _t
    import traceback

    _t0 = _t.time()
    try:
        STATS.clear()
        main()
        run_log(ok=True, seg=round(_t.time() - _t0, 1), **STATS)
        cron_status()
    except Exception as e:
        run_log(
            ok=False,
            seg=round(_t.time() - _t0, 1),
            msg=f"{type(e).__name__}: {e}",
            errores=[traceback.format_exc(limit=2)[-300:]],
        )
        raise
