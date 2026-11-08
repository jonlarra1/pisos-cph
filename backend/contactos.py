"""Contactos con caseros (de los chats de Boligportal) y la marca ❤️ «nos encanta». Solo stdlib + sqlite3.

- contacto: lo escribe Lares (clave CLAVE_LARES) tras leer la bandeja de la cuenta compartida. Por anuncio de
  Boligportal: la marca (respondido | sin_respuesta | visita | alquilado), si el último mensaje es nuestro, su fecha
  y el número de la conversación (para el botón «Abrir conversación»).
  NO se guarda el texto de los mensajes: la API se lee sin clave.
- favorito: ❤️ por piso (id de la web), compartido entre Jon y Unai. Quitarla no borra nada: la deja inactiva.
"""

import re
import time

MARCAS = ("respondido", "sin_respuesta", "visita", "alquilado")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")  # ids de la web (Boligportal, Lejebolig, EDC, «jl…» de Juli)
PID_RE = re.compile(r"^\d{4,10}$")  # anuncio de Boligportal
FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]{0,20})?$")
MAX_CONTACTOS = 500


class NoValido(ValueError):
    pass


def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS contacto (
            pid TEXT PRIMARY KEY,               -- anuncio de Boligportal
            hilo INTEGER, marca TEXT NOT NULL, es_mio INTEGER NOT NULL, fecha TEXT, actualizado INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS favorito (
            id TEXT PRIMARY KEY,                -- id del piso en la web
            activo INTEGER NOT NULL, by TEXT NOT NULL, ts INTEGER NOT NULL
        );
    """)


def guardar_contactos(con, lista, ahora=None):
    """Sustituye el contacto de cada anuncio de la lista (lo que no viene se queda como estaba). Devuelve cuántos
    cambiaron. Valida todo: lo que no encaja se rechaza entero (NoValido). Un anuncio repetido: vale el primero."""
    if not isinstance(lista, list) or len(lista) > MAX_CONTACTOS:
        raise NoValido("contactos: lista no válida")
    ahora, filas = ahora or int(time.time()), []
    for c in lista:
        if not isinstance(c, dict):
            raise NoValido("contacto no válido")
        pid, marca, fecha, hilo = str(c.get("pid") or ""), c.get("marca"), str(c.get("fecha") or ""), c.get("hilo")
        if not PID_RE.match(pid) or marca not in MARCAS or not isinstance(c.get("es_mio"), bool):
            raise NoValido(f"contacto no válido ({pid[:12]})")
        if fecha and not FECHA_RE.match(fecha):
            raise NoValido(f"fecha no válida ({pid})")
        if hilo is not None and not (isinstance(hilo, int) and not isinstance(hilo, bool) and 0 < hilo < 10**12):
            raise NoValido(f"conversación no válida ({pid})")
        filas.append((pid, hilo, marca, int(c["es_mio"]), fecha[:25] or None))
    n, hechos = 0, set()
    for pid, hilo, marca, es_mio, fecha in filas:
        if pid in hechos:
            continue
        hechos.add(pid)
        previo = con.execute("SELECT hilo, marca, es_mio, fecha FROM contacto WHERE pid=?", (pid,)).fetchone()
        if previo and tuple(previo) == (hilo, marca, es_mio, fecha):
            continue
        con.execute(
            "INSERT INTO contacto (pid, hilo, marca, es_mio, fecha, actualizado) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(pid) DO UPDATE SET hilo=excluded.hilo, marca=excluded.marca, es_mio=excluded.es_mio, "
            "fecha=excluded.fecha, actualizado=excluded.actualizado",
            (pid, hilo, marca, es_mio, fecha, ahora),
        )
        n += 1
    return n


def poner_favorito(con, id_, activo, quien, ahora=None):
    if not isinstance(id_, str) or not ID_RE.match(id_) or not isinstance(activo, bool):
        raise NoValido("favorito no válido")
    con.execute(
        "INSERT INTO favorito (id, activo, by, ts) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "activo=excluded.activo, by=excluded.by, ts=excluded.ts",
        (id_, int(activo), quien, ahora or int(time.time())),
    )


def listar(con):
    """Lo que lee la web: contactos por anuncio de Boligportal y los ❤️ activos."""
    return {
        "contactos": {
            r[0]: {"hilo": r[1], "marca": r[2], "es_mio": bool(r[3]), "fecha": r[4], "actualizado": r[5]}
            for r in con.execute("SELECT pid, hilo, marca, es_mio, fecha, actualizado FROM contacto")
        },
        "favoritos": {
            r[0]: {"by": r[1], "ts": r[2]} for r in con.execute("SELECT id, by, ts FROM favorito WHERE activo=1")
        },
    }


def resumen(con):
    """Para el check «chats»: contactos por marca, ❤️ activos y la última actualización."""
    marcas = dict(con.execute("SELECT marca, COUNT(*) FROM contacto GROUP BY marca").fetchall())
    ultimo = con.execute("SELECT MAX(actualizado) FROM contacto").fetchone()[0]
    fav = con.execute("SELECT COUNT(*) FROM favorito WHERE activo=1").fetchone()[0]
    return {"marcas": marcas, "ultimo": ultimo, "favoritos": fav}
