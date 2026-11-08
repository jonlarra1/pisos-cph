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
- Conversación de un anuncio que no está en la base, o que está «propuesto» o «rechazado»: queda en «contactado»
  (≤3 por lectura). Gusta, contactado, descartado y retirado no se tocan.
- Buzón: `pisos-bridge/chats_estado.json` (sesión, conversaciones, error; sin textos) para el check «chats» del panel.
- Salida (despierta a Lares solo si hay algo): «RESPUESTA | …» por cada mensaje nuevo del casero y, como mucho una vez
  al día, «[aviso] … renovar la cookie».
"""

import http.client
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
ANUNCIO = "https://www.boligportal.dk/lejligheder/bolig/lejlighed-id-{}"
UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
CPH = ZoneInfo("Europe/Copenhagen")
ESPERA_MIN, ESPERA_MAX = 3 * 3600, 5 * 3600
NOCHE = range(0, 8)  # horas sin leer
MAX_PAGINAS = 3
MAX_NUEVOS = 3  # anuncios añadidos a la base por lectura (Lares tiene un tope de cambios por hora)
MAX_BYTES = 2_000_000
A_CONTACTADO = (None, "propuesto", "rechazado")  # estado en la base (None = no está) que pasa a «contactado»
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
    """Lo de cada conversación con anuncio: lo público (para la API) y lo privado (texto, para el perfil).
    Si un anuncio tiene varias (p. ej. una archivada), vale la del mensaje más reciente."""
    out, vistos = [], set()
    hilos = sorted(hilos, key=lambda h: str((h.get("last_message") or {}).get("sent_date") or ""), reverse=True)
    for h in hilos:
        ad, ult = h.get("ad") or {}, h.get("last_message") or {}
        pid, hilo = str(ad.get("id") or ""), ult.get("thread_id") or h.get("id")
        if not re.fullmatch(r"\d{4,10}", pid) or h.get("is_owner") or pid in vistos:
            continue
        vistos.add(pid)
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


def a_contactado(chats, no_anadir):
    """Con conversación = contactado: añade lo que no está en la base y pasa a «contactado» lo propuesto o rechazado.
    Solo un 400 (anuncio no válido o ya no publicado) va a `no_anadir`; un fallo pasajero se reintenta."""
    base = {
        a.get("pid"): a
        for a in pisos.llamar("GET", "/api/v2/anuncios").get("anuncios") or []
        if a.get("portal", "Boligportal") == "Boligportal"
    }
    pendientes = [
        c
        for c in chats
        if c["marca"] != "alquilado"
        and c["pid"] not in no_anadir
        and (base.get(c["pid"]) or {}).get("estado") in A_CONTACTADO
    ]
    hechos = []
    for c in pendientes[:MAX_NUEVOS]:
        try:
            a = base.get(c["pid"]) or pisos.llamar(
                "POST", "/api/v2/enlaces", {"url": ANUNCIO.format(c["pid"]), "quien": "lares"}
            )
            if a.get("estado") in A_CONTACTADO:
                pisos.llamar("POST", pisos.ruta_uid(a["uid"]) + "/estado", {"estado": "contactado", "quien": "lares"})
            hechos.append(c["pid"])
        except pisos.Error as e:
            if e.codigo == 400:
                no_anadir.add(c["pid"])
            print(f"[aviso] no se pudo pasar {c['pid']} a contactado: {e}", file=sys.stderr)
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
        alquilados = set(estado.get("alquilados") or [])  # lo que el casero dio por alquilado lo sigue estando
        for c in chats:
            if c["pid"] in alquilados:
                c["marca"] = "alquilado"
        estado["alquilados"] = sorted(alquilados | {c["pid"] for c in chats if c["marca"] == "alquilado"})
        info["conversaciones"] = len(chats)
        nuevas = respuestas_nuevas(chats, estado.get("vistos") or {})
        info["cambiados"] = entregar(chats)
        no_anadir = set(estado.get("no_anadir") or [])
        info["anadidos"] = a_contactado(chats, no_anadir)
        estado["no_anadir"] = sorted(no_anadir)
        estado["vistos"] = {c["pid"]: c["msg"] for c in chats}
        escribir_privado(PRIVADO, {"leido": ahora, "chats": chats})
        for c in nuevas:
            enlace = pisos.HILO.format(c["hilo"]) if c["hilo"] else ANUNCIO.format(c["pid"])
            print(f"RESPUESTA | {c['calle'] or c['pid']} | {c['marca']} | {c['fecha'][:16]} | {enlace}")
    except Caducada as e:
        info["sesion"] = "caducada"
        aviso_caducada(estado, ahora, e)
    except (RuntimeError, pisos.Error, OSError, http.client.HTTPException, ValueError) as e:  # OSError: red y disco
        info["error"] = str(e)[:200] or type(e).__name__
    escribir_privado(ESTADO, estado)
    guardar_estado_buzon(info)
    return 0


if __name__ == "__main__":
    sys.exit(pasada())
