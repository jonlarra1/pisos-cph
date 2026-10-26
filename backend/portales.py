"""Lectura de anuncios de los portales (Lejebolig, Boligportal, EDC) para pisos-api. Solo stdlib.

    ficha(url)              -> dict con los campos normalizados del anuncio (ver CAMPOS)
    estado(url)             -> ("activo" | "retirado" | "desconocido", motivo)
    zona_de(texto)          -> zona del mapa («Søborg», «Sydhavnen»…) o None
    geocodificar(direccion) -> (lat, lon) o None   (Nominatim, máx. 1 petición/s)

Las descargas pasan por `descargar`, que los tests sustituyen por fichas guardadas.
Nunca se inventan datos: lo que el anuncio no dice queda en None.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
UA_NOMINATIM = "pisos-cph/1.0 (personal flat-hunting map; light use)"
MAX_BYTES = 3_000_000  # una ficha normal pesa 40–400 KB

# Campos que devuelve ficha(). None = el anuncio no lo dice.
CAMPOS = (
    "portal",
    "pid",
    "link",
    "titulo",
    "calle",
    "cp",
    "ciudad",
    "planta",
    "precio",
    "aconto",
    "dep",
    "pre",
    "m2",
    "vaer",
    "dorm",
    "disp",
    "contrato_meses",
    "compartible",
    "amueblado",
    "balcon",
    "ascensor",
    "mascotas",
    "lat",
    "lon",
    "fotos",
    "plano",
    "texto",
)

MESES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "maj": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "okt": 10,
    "nov": 11,
    "dec": 12,
}


class AnuncioNoValido(ValueError):
    """El enlace no es de un portal soportado o la ficha no se puede leer."""


# ------------------------------------------------------------------ enlaces
_PATRONES = (
    ("Lejebolig", re.compile(r"^https://(?:en\.|www\.)?lejebolig\.dk/lejebolig/(\d{4,10})(?:[/?#]|$)")),
    (
        "Boligportal",
        re.compile(r"^https://(?:www\.)?boligportal\.dk/\S*?(?:id-|rental-apartments/)(\d{4,10})(?:[/?#]|$)"),
    ),
    ("EDC", re.compile(r"^https://(?:www\.)?edc\.dk/leje/\S*?/(\d{6,10})/?(?:[?#]|$)")),
)


def identificar(url):
    """(portal, id) de un enlace de anuncio, o AnuncioNoValido. Solo https y solo los 3 portales."""
    u = (url or "").strip()
    host = urllib.parse.urlsplit(u).netloc
    if len(u) > 500 or not host or "@" in host or ":" in host:  # sin usuario@ ni puertos raros
        raise AnuncioNoValido("enlace no válido")
    for portal, pat in _PATRONES:
        m = pat.match(u)
        if m:
            return portal, m.group(1)
    raise AnuncioNoValido("solo enlaces de anuncios de Lejebolig, Boligportal o EDC")


def enlace_canonico(portal, pid, url=""):
    if portal == "Lejebolig":
        return f"https://en.lejebolig.dk/lejebolig/{pid}"
    if portal == "Boligportal":
        return f"https://www.boligportal.dk/en/rental-apartments/{pid}"
    return url.split("?")[0].split("#")[0]  # EDC: el id va dentro de una ruta larga


# ------------------------------------------------------------------ descarga
def descargar(url, timeout=20):
    """(código HTTP, url final, html). Sustituible en los tests."""
    if not url.startswith("https://"):  # segunda barrera (identificar() ya lo exige): nada de file:// ni http://
        raise ValueError("solo https")
    req = urllib.request.Request(
        urllib.parse.quote(url, safe=":/?=&%#"), headers={"User-Agent": UA, "Accept-Language": "da,en;q=0.8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        e.close()
        return e.code, e.geturl() or url, ""


def _texto(html):
    sin = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", sin))).strip()


def _kr(s):
    return int(re.sub(r"\D", "", s)) if s and re.search(r"\d", s) else None


def _fecha(dia, mes, anio):
    m = MESES.get(mes[:3].lower())
    return f"{anio}-{m:02d}-{int(dia):02d}" if m else None


# ------------------------------------------------------------------ Lejebolig
def _lejebolig(pid, html):
    t = _texto(html)
    d = {"titulo": None, "calle": None, "ciudad": None, "planta": None}
    m = re.search(r"(\d+) room apartment of (\d+) m2 (.+?), ([^,]+?) - (.+?) Rent ([\d,]+) DKK", t) or re.search(
        r"m2 apartment on (.+?) (.+?), ([^,]+?) - (.+?) Rent ([\d,]+) DKK", t
    )
    if m and m.re.pattern.startswith("(\\d+) room"):
        d.update(
            vaer=int(m.group(1)),
            m2=int(m.group(2)),
            calle=m.group(3),
            ciudad=m.group(4),
            planta=m.group(5),
            precio=_kr(m.group(6)),
        )
    elif m:
        d.update(calle=m.group(2), ciudad=m.group(3), planta=m.group(4), precio=_kr(m.group(5)))
    d.setdefault("precio", _kr((re.search(r"\bRent ([\d,]+) DKK", t) or [None, None])[1]))
    d.setdefault("m2", _kr((re.search(r"\bSize (\d+) m²", t) or [None, None])[1]))
    d.setdefault("vaer", _kr((re.search(r"\bRooms (\d+)", t) or [None, None])[1]))
    d["titulo"] = f"{d['calle']}, {d['ciudad']}" if d["calle"] else None
    d["cp"] = (re.search(r"Property for rent in (\d{4}) ", t) or [None, None])[1]
    d["pre"] = _kr((re.search(r"Rent paid in advance: ([\d,]+)", t) or [None, None])[1])
    d["dep"] = _kr((re.search(r"\bDeposit: ([\d,]+)", t) or [None, None])[1])
    d["aconto"] = _kr((re.search(r"Payment on account: ([\d,]+)", t) or [None, None])[1])
    v = re.search(r"Vacant: (?:(\d{1,2})\. ([A-Za-z]+) (\d{4})|(Immediately|As soon as possible))", t)
    d["disp"] = (_fecha(v.group(1), v.group(2), v.group(3)) if v.group(1) else "ya") if v else None
    p = re.search(r"Rental period (\w+)", t)
    d["contrato_meses"] = None if not p else (0 if p.group(1).lower() == "unlimited" else _kr(p.group(1)))
    d["mascotas"] = True if "Pets allowed" in t else (False if "Pets not allowed" in t else None)
    d["balcon"] = True if "Balcony/terrasse" in t else None
    d["ascensor"] = True if re.search(r"\bLift\b", t) else None
    carpeta = str(-(-int(pid) // 100) * 100)  # Lejebolig guarda las fotos en lease/<id redondeado a 100>/
    d["fotos"] = list(
        dict.fromkeys(
            re.findall(
                r"https://s3\.eu-central-1\.amazonaws\.com/lejeboligdata/images/lease/%s/[0-9a-f-]+_large\.jpg"
                % carpeta,
                html,
            )
        )
    )[:15]
    i = t.find("Contact owner")
    d["texto"] = t[i + 14 : i + 4000] if i >= 0 else t[:4000]
    return d


# ------------------------------------------------------------------ Boligportal
def _buscar(o, pred):
    if isinstance(o, dict):
        if pred(o):
            return o
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = _buscar(v, pred)
            if r is not None:
                return r
    return None


def _boligportal(pid, html):
    m = re.search(r'<script id="store" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise AnuncioNoValido("Boligportal: ficha sin datos")
    a = _buscar(json.loads(m.group(1)), lambda o: str(o.get("id")) == pid and "monthly_rent" in o)
    if a is None:
        raise AnuncioNoValido("Boligportal: anuncio no encontrado en la ficha")

    def num(v):
        return int(float(v)) if isinstance(v, (int, float)) else None

    f = a.get("features") or {}

    def b(k):
        return f.get(k) if isinstance(f.get(k), bool) else None

    imgs = [i for i in (a.get("images") or []) if isinstance(i, dict) and i.get("url")]
    loc = a.get("location") or {}
    calle = " ".join(x for x in (a.get("street_name"), a.get("street_number")) if x) or None
    return {
        "titulo": a.get("title"),
        "calle": calle,
        "cp": str(a.get("postal_code") or "") or None,
        "ciudad": a.get("city_area") or a.get("city_level_2"),
        "planta": a.get("floor"),
        "precio": num(a.get("monthly_rent")),
        "aconto": num(a.get("monthly_rent_extra_costs")),
        "dep": num(a.get("deposit")),
        "pre": num(a.get("prepaid_rent")),
        "m2": num(a.get("size_m2")),
        "vaer": num(a.get("rooms")),
        "disp": (a.get("available_from") or "")[:10] or None,
        "contrato_meses": num(a.get("rental_period")),
        "compartible": b("shareable"),
        "amueblado": b("furnished"),
        "balcon": b("balcony"),
        "ascensor": b("elevator"),
        "mascotas": b("pet_friendly"),
        "lat": loc.get("lat"),
        "lon": loc.get("lng") or loc.get("lon"),
        "fotos": [i["url"] for i in imgs][:15],
        "plano": next((i["url"] for i in imgs if i.get("is_floor_plan")), None),
        "texto": (a.get("description") or "")[:4000],
    }


# ------------------------------------------------------------------ EDC
def _edc(pid, html, url):
    t = _texto(html)
    tit = re.search(r"<title>[^|<]*\| ([^|<]+)", html)
    lugar = re.search(r"/leje/[^/]+/(\d{4})-([a-z0-9æøå-]+)/", url)
    d = {
        "calle": unescape(tit.group(1)).strip() if tit else None,
        "cp": lugar.group(1) if lugar else None,
        "ciudad": lugar.group(2).replace("-", " ").title() if lugar else None,
        "planta": None,
    }
    d["titulo"] = f"{d['calle']}, {d['ciudad']}" if d["calle"] else None
    d["precio"] = _kr((re.search(r"\b([\d.]+) kr\./md\.", t) or [None, None])[1])
    d["m2"] = _kr((re.search(r"Boligareal (\d+) m²", t) or [None, None])[1])
    r = re.search(r"Rum/værelser (\d+)/(\d+)", t)
    d["vaer"], d["dorm"] = (int(r.group(1)), int(r.group(2))) if r else (None, None)
    d["dep"] = _kr((re.search(r"Depositum (?:[^,]{0,40}, )?([\d.]+) kr", t) or [None, None])[1])
    d["pre"] = _kr((re.search(r"Forudbetalt leje (?:[^,]{0,40}, )?([\d.]+) kr", t) or [None, None])[1])
    ac = [_kr(x) for x in re.findall(r"Aconto [A-Za-zæøåÆØÅ ]{2,30}? ([\d.]+) kr", t)]
    d["aconto"] = sum(ac) if ac else None
    p = re.search(r"Lejeperiode (\S+)", t)
    d["contrato_meses"] = None if not p else (0 if p.group(1).lower().startswith("ubegr") else _kr(p.group(1)))
    v = re.search(
        r"(?:Ledig fra|Tilgængelig fra)(?: d\.)? (?:(\d{1,2})\. ?([a-zæøå]{3})[a-zæøå]*\.? (\d{4})|(Snarest|Straks))",
        t,
        re.I,
    )
    d["disp"] = (_fecha(v.group(1), v.group(2), v.group(3)) if v.group(1) else "ya") if v else None

    def sj(k):
        return (lambda m: (m.group(1) == "Ja") if m else None)(re.search(k + r" (Ja|Nej)\b", t))

    d["balcon"], d["ascensor"] = sj(r"Altan/terrasse"), sj(r"Elevator")
    g = re.search(r'"geoCoordinates":\{"latitude":([\d.]+),"longitude":([\d.]+)\}', html)  # la primera es la del piso
    d["lat"], d["lon"] = (round(float(g.group(1)), 6), round(float(g.group(2)), 6)) if g else (None, None)
    d["fotos"] = list(dict.fromkeys(re.findall(r"https://billeder\.edc\.dk/edcmedia/[^\"'\s?]+\.jpg", html)))[:15]
    i = t.find("Hent boligpræsentation")
    d["texto"] = t[i : i + 4000] if i >= 0 else t[:4000]
    return d


# ------------------------------------------------------------------ texto común
_FURN_NO = re.compile(r"umøbleret|ikke møbleret|unfurnished|not furnished", re.I)
_FURN_SI = re.compile(r"(?<!u)møbleret|(?<!un)furnished", re.I)


def amueblado_de(texto):
    """True / False / None. «umøbleret» manda sobre «møbleret»."""
    if _FURN_NO.search(texto or ""):
        return False
    if _FURN_SI.search(texto or ""):
        return True
    return None


def ficha(url):
    """Abre la ficha y devuelve el anuncio con todos los CAMPOS (None = no lo dice). AnuncioNoValido si no se puede."""
    portal, pid = identificar(url)
    code, final, html = descargar(url)
    if code in (404, 410) or "ad_rented_out_redirect" in final:
        raise AnuncioNoValido("el anuncio ya no está publicado")
    if code != 200 or not html:
        raise AnuncioNoValido(f"{portal} respondió HTTP {code}")
    lector = {"Lejebolig": _lejebolig, "Boligportal": _boligportal}.get(portal)
    d = lector(pid, html) if lector else _edc(pid, html, url)
    if not d.get("precio"):
        raise AnuncioNoValido(f"{portal}: no se encuentra el precio en la ficha")
    if d.get("amueblado") is None:
        d["amueblado"] = amueblado_de(d.get("texto"))
    d.update(portal=portal, pid=pid, link=enlace_canonico(portal, pid, url))
    return {k: d.get(k) for k in CAMPOS}


def estado(url):
    """('activo' | 'retirado' | 'desconocido', motivo). Solo una señal clara retira: 404/410, «udlejet» de Boligportal
    o un estado que no es ACTIVE_*. Un fallo de red es «desconocido»: nunca se retira por un error nuestro."""
    portal, pid = identificar(url)
    try:
        code, final, html = descargar(url)
    except Exception as e:
        return "desconocido", type(e).__name__
    if code in (404, 410):
        return "retirado", "el portal ya no lo publica"
    if code != 200:
        return "desconocido", f"HTTP {code}"
    if "ad_rented_out_redirect" in final:
        return "retirado", "marcado como alquilado («udlejet»)"
    if portal == "Boligportal":
        m = re.search(r'<script id="store" type="application/json">(.*?)</script>', html, re.S)
        try:
            a = _buscar(json.loads(m.group(1)), lambda o: str(o.get("id")) == pid and "state" in o) if m else None
        except ValueError:
            a = None
        if a:
            return (
                ("activo", "")
                if str(a["state"]).startswith("ACTIVE")
                else ("retirado", f"estado {a['state']} en el portal")
            )
        return "desconocido", "sin estado en la ficha"
    return ("activo", "") if pid in final else ("desconocido", "redirección inesperada")


# ------------------------------------------------------------------ zonas
ZONA_ALIAS = {
    "københavn ø": "Østerbro",
    "østerbro": "Østerbro",
    "københavn s": "Amager",
    "amager": "Amager",
    "islands brygge": "Islands Brygge",
    "ørestad": "Ørestad",
    "holmen": "Holmen",
    "margretheholm": "Holmen",
    "københavn n": "Nørrebro",
    "nørrebro": "Nørrebro",
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
    "brønshøj": "Brønshøj",
    "husum": "Husum",
    "herlev": "Herlev",
    "søborg": "Søborg",
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
    "ballerup": "Ballerup",
    "skovlunde": "Skovlunde",
    "måløv": "Måløv",
    "glostrup": "Glostrup",
    "hvidovre": "Hvidovre",
}
ZONA_CP = {
    "2450": "Sydhavnen",
    "2400": "Nordvest",
    "2860": "Søborg",
    "2730": "Herlev",
    "2700": "Brønshøj",
    "2720": "Vanløse",
    "2610": "Rødovre",
    "2800": "Lyngby",
    "2830": "Virum",
    "2900": "Hellerup",
    "2820": "Gentofte",
    "2200": "Nørrebro",
    "2100": "Østerbro",
    "2300": "Amager",
    "2500": "Valby",
    "2600": "Glostrup",
    "2650": "Hvidovre",
    "2750": "Ballerup",
    "2740": "Skovlunde",
    "2760": "Måløv",
    "2880": "Bagsværd",
    "2920": "Charlottenlund",
    "2930": "Klampenborg",
}
_SIN_TILDES = str.maketrans("øåæ", "oaa")


def zona_de(texto, cp=None):
    """Zona del mapa a partir del texto (título, calle, ciudad) y, si no, del código postal."""
    hay = (texto or "").lower()
    for k in sorted(ZONA_ALIAS, key=len, reverse=True):
        for v in {k, k.translate(_SIN_TILDES)}:
            if re.search(rf"(?<![a-zæøå]){re.escape(v)}(?![a-zæøå])", hay):
                return ZONA_ALIAS[k]
    cp = str(cp or "")
    if cp.isdigit() and 1800 <= int(cp) <= 2000:
        return "Frederiksberg"
    if cp.isdigit() and 1050 <= int(cp) <= 1799:
        return "Indre By" if int(cp) < 1500 else "Vesterbro"
    return ZONA_CP.get(cp)


# ------------------------------------------------------------------ geocodificación
_GEO_LOCK = threading.Lock()
_geo_ultima = [0.0]
BBOX = (55.55, 55.86, 12.20, 12.72)  # zona de Copenhague: fuera de aquí no es nuestro piso


def geocodificar(direccion):
    """(lat, lon) con Nominatim o None. Una petición por segundo como mucho (política de uso de OSM)."""
    q = (direccion or "").strip()
    if len(q) < 4:
        return None
    with _GEO_LOCK:
        espera = 1.1 - (time.time() - _geo_ultima[0])
        if espera > 0:
            time.sleep(espera)
        _geo_ultima[0] = time.time()
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"format": "json", "limit": 1, "countrycodes": "dk", "q": q}
        )
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA_NOMINATIM}), timeout=15
            ) as r:
                res = json.loads(r.read(200_000))
        except Exception:
            return None
    if not res:
        return None
    la, lo = float(res[0]["lat"]), float(res[0]["lon"])
    return (round(la, 6), round(lo, 6)) if BBOX[0] <= la <= BBOX[1] and BBOX[2] <= lo <= BBOX[3] else None
