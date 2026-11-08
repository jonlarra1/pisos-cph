#!/usr/bin/env python3
"""Lector de los chats de Boligportal (cuenta compartida de Unai). SOLO LEE: nunca escribe a caseros ni archiva.

Lo lanza la tarea `boligportal-chats` de Lares cada 30 min, sin IA, pero solo lee cuando toca: tras cada lectura se
sortea la siguiente entre 3 y 5 h después (y nunca de 00 a 08 h, hora de Copenhague), para no leer a horas fijas.

- Cookie: archivo `.boligportal_cookie` (permisos 600) en la carpeta del perfil. La pegan Jon o Unai; este programa
  nunca la imprime ni la guarda en otro sitio.
- Lee GET /api/messaging/ (lo mismo que pide la web de Boligportal al abrir la bandeja): ≤3 páginas de 20, con pausas.
  401/403 = sesión caducada; 429 u otra cosa rara = error. En los dos casos para y no reintenta hasta la próxima hora.
- Texto completo de los mensajes: SOLO en `chats_boligportal.json` (600) del perfil, para `pisos.py chats`.
- A la API v2 (POST /api/v2/contactos, clave de Lares) solo va: anuncio, conversación, marca, si el último es
  nuestro y su fecha.
- Conversación de un anuncio que no está en la base: lo añade por su enlace y lo pone en «contactado» (≤3 por lectura).
- Buzón: `pisos-bridge/chats_estado.json` (sesión, conversaciones, error; sin textos) para el check «chats» del panel.
- Salida (despierta a Lares solo si hay algo): «RESPUESTA | …» por cada mensaje nuevo del casero y, como mucho una vez
  al día, «[aviso] … renovar la cookie».
"""

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pisos  # noqa: E402  (la misma carpeta scripts/ del perfil)

PERFIL = Path(__file__).resolve().parent.parent
COOKIE = PERFIL / ".boligportal_cookie"
ESTADO = PERFIL / ".chats_estado.json"  # próxima lectura, último aviso y lo ya visto
PRIVADO = PERFIL / "chats_boligportal.json"  # texto de los mensajes: solo aquí
BRIDGE = Path(os.environ.get("PISOS_BRIDGE", "/opt/data/vault/Vivienda/pisos-bridge"))
BANDEJA = "https://www.boligportal.dk/api/messaging/?offset={}&limit=20&search=&show_archived=true"
HILO = "https://www.boligportal.dk/indbakke/t/{}"
ANUNCIO = "https://www.boligportal.dk/lejligheder/bolig/lejlighed-id-{}"
UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
CPH = ZoneInfo("Europe/Copenhagen")
ESPERA_MIN, ESPERA_MAX = 3 * 3600, 5 * 3600
NOCHE = range(0, 8)  # horas sin leer
MAX_PAGINAS = 3
MAX_NUEVOS = 3  # anuncios añadidos a la base por lectura (Lares tiene un tope de cambios por hora)
MAX_BYTES = 2_000_000
AVISO_CADA = 24 * 3600
VISITA_RE = re.compile(r"fremvis|besøg|besigtig|viewing|visit", re.I)
ALQUILADO_RE = re.compile(r"lejemålet er udlejet|samtalen er deaktiveret|er blevet udlejet", re.I)
AZAR = secrets.SystemRandom()


class Caducada(Exception):
    pass


def leer_json(ruta, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return defecto


def escribir_privado(ruta, datos):
    """Escribe con permisos 600 desde el principio (nunca queda legible para otros)."""
    tmp = ruta.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)


def toca(estado, ahora):
    """¿Hay que leer ya? Respeta la hora sorteada y la noche."""
    return ahora >= estado.get("proximo", 0) and datetime.fromtimestamp(ahora, CPH).hour not in NOCHE


def sortear(ahora):
    return int(ahora + AZAR.uniform(ESPERA_MIN, ESPERA_MAX))


def pedir_pagina(cookie, offset):
    req = urllib.request.Request(
        BANDEJA.format(offset),
        headers={
            "User-Agent": UA,
            "Cookie": cookie,
            "Accept": "application/json",
            "Accept-Language": "da,en;q=0.8",
            "Referer": "https://www.boligportal.dk/indbakke/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 (https fijo)
            cuerpo = r.read(MAX_BYTES)
            if "json" not in r.headers.get("Content-Type", ""):
                raise Caducada("la bandeja devolvió una página, no datos (¿sesión cerrada?)")
            return json.loads(cuerpo)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Caducada(f"Boligportal respondió {e.code}") from None
        raise RuntimeError(f"Boligportal respondió {e.code}") from None


def leer_bandeja(cookie, pausa=time.sleep):
    hilos, offset = [], 0
    for _ in range(MAX_PAGINAS):
        pag = pedir_pagina(cookie, offset)
        nuevos = pag.get("threads") or []
        hilos += nuevos
        offset += len(nuevos)
        if not nuevos or offset >= (pag.get("count") or 0):
            break
        pausa(AZAR.uniform(2, 6))
    return hilos


def clasificar(h):
    """Marca de una conversación según su último mensaje (y el estado del anuncio)."""
    ad, ult = h.get("ad") or {}, h.get("last_message") or {}
    texto = ult.get("body") or ""
    if ALQUILADO_RE.search(texto) or (ad.get("state") and not str(ad["state"]).startswith("ACTIVE")):
        return "alquilado"
    if ult.get("is_me"):
        return "sin_respuesta"
    return "visita" if VISITA_RE.search(texto) else "respondido"


def resumir(hilos):
    """Lo de cada conversación con anuncio: lo público (para la API) y lo privado (texto, para el perfil)."""
    out = []
    for h in hilos:
        ad, ult = h.get("ad") or {}, h.get("last_message") or {}
        pid, hilo = str(ad.get("id") or ""), ult.get("thread_id") or h.get("id")
        if not re.fullmatch(r"\d{4,10}", pid) or h.get("is_owner"):
            continue
        calle = ", ".join(str(x) for x in (ad.get("street_name"), ad.get("postal_code"), ad.get("city")) if x)
        out.append(
            {
                "pid": pid,
                "hilo": hilo if isinstance(hilo, int) else None,
                "marca": clasificar(h),
                "es_mio": bool(ult.get("is_me")),
                "fecha": str(ult.get("sent_date") or "")[:25],
                "msg": ult.get("id"),
                "texto": (ult.get("body") or "")[:4000],
                "calle": calle[:120],
            }
        )
    return out


def respuestas_nuevas(chats, vistos):
    """Mensajes del casero que no estaban la vez anterior (la primera lectura no avisa: solo aprende)."""
    if not vistos:
        return []
    return [c for c in chats if not c["es_mio"] and c["msg"] and vistos.get(c["pid"]) != c["msg"]]


def entregar(chats):
    publico = [{k: c[k] for k in ("pid", "hilo", "marca", "es_mio", "fecha")} for c in chats]
    return pisos.llamar("POST", "/api/v2/contactos", {"contactos": publico}).get("cambiados", 0)


def anadir_nuevos(chats, no_anadir):
    """Conversaciones de anuncios que no están en la base: se añaden y quedan en «contactado» (sin tocar los demás).
    Lo que el backend no puede añadir (p. ej. ya no está publicado) va a `no_anadir` y no se vuelve a intentar."""
    en_base = {a.get("pid") for a in pisos.llamar("GET", "/api/v2/anuncios").get("anuncios") or []}
    hechos = []
    for c in [c for c in chats if c["pid"] not in en_base | no_anadir and c["marca"] != "alquilado"][:MAX_NUEVOS]:
        try:
            a = pisos.llamar("POST", "/api/v2/enlaces", {"url": ANUNCIO.format(c["pid"]), "quien": "lares"})
            if a.get("estado") == "propuesto":
                pisos.llamar("POST", pisos.ruta_uid(a["uid"]) + "/estado", {"estado": "contactado", "quien": "lares"})
            hechos.append(c["pid"])
        except pisos.Error as e:
            no_anadir.add(c["pid"])
            print(f"[aviso] no se pudo añadir {c['pid']}: {e}", file=sys.stderr)
    return hechos


def guardar_estado_buzon(datos):
    try:
        BRIDGE.mkdir(parents=True, exist_ok=True)
        (BRIDGE / "chats_estado.json").write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def aviso_caducada(estado, ahora, motivo):
    if ahora - estado.get("aviso", 0) >= AVISO_CADA:
        estado["aviso"] = ahora
        print(f"[aviso] La sesión de Boligportal ha caducado ({motivo}): Jon o Unai tienen que renovar la cookie.")


def pasada(ahora=None, pausa=time.sleep):
    ahora = int(ahora or time.time())
    estado = leer_json(ESTADO, {})
    if not COOKIE.exists():
        guardar_estado_buzon({"ts": ahora, "sesion": "sin_cookie"})
        return 0
    if not toca(estado, ahora):
        return 0
    estado["proximo"] = sortear(ahora)
    info = {"ts": ahora, "proximo": estado["proximo"], "sesion": "ok"}
    try:
        chats = resumir(leer_bandeja(COOKIE.read_text(encoding="utf-8").strip(), pausa))
        info["conversaciones"] = len(chats)
        nuevas = respuestas_nuevas(chats, estado.get("vistos") or {})
        info["cambiados"] = entregar(chats)
        no_anadir = set(estado.get("no_anadir") or [])
        info["anadidos"] = anadir_nuevos(chats, no_anadir)
        estado["no_anadir"] = sorted(no_anadir)
        estado["vistos"] = {c["pid"]: c["msg"] for c in chats}
        escribir_privado(PRIVADO, {"leido": ahora, "chats": chats})
        for c in nuevas:
            print(f"RESPUESTA | {c['calle'] or c['pid']} | {c['marca']} | {c['fecha'][:16]} | {HILO.format(c['hilo'])}")
    except Caducada as e:
        info["sesion"] = "caducada"
        aviso_caducada(estado, ahora, e)
    except (RuntimeError, pisos.Error, urllib.error.URLError, TimeoutError, ValueError) as e:
        info["error"] = str(e)[:200] or type(e).__name__
    escribir_privado(ESTADO, estado)
    guardar_estado_buzon(info)
    return 0


if __name__ == "__main__":
    sys.exit(pasada())
