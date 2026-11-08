"""Contactos con caseros (de los chats de Boligportal) y la marca ❤️ «nos encanta». Solo stdlib + sqlite3.

- contacto: lo escribe Lares (clave CLAVE_LARES) tras leer la bandeja de la cuenta compartida. Por anuncio de
  Boligportal: la marca (respondido | sin_respuesta | visita | alquilado), si el último mensaje es nuestro, su fecha
  y el número de la conversación (para el botón «Abrir conversación»). Sin texto: GET /api/v2/contactos es público.
- favorito: ❤️ por piso (id de la web), compartido entre Jon y Unai. Quitarla no borra nada: la deja inactiva.
- mensaje: el texto de cada mensaje de las conversaciones (lo escribe Lares). Es PRIVADO: la API solo lo da con la clave
  de lectura de Jon o de Unai (api_v2.lector_de). No se borra nunca.
"""

import re
import time

MARCAS = ("respondido", "sin_respuesta", "visita", "alquilado")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")  # ids de la web (Boligportal, Lejebolig, EDC, «jl…» de Juli)
PID_RE = re.compile(r"^\d{4,10}$")  # anuncio de Boligportal
FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]{0,20})?$")
MAX_CONTACTOS = 500
MAX_MENSAJES = 1000  # por entrega
MAX_TEXTO = 5000


class NoValido(ValueError):
    pass


def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS contacto (
            pid TEXT PRIMARY KEY,               -- anuncio de Boligportal
            hilo INTEGER, marca TEXT NOT NULL, es_mio INTEGER NOT NULL, fecha TEXT, actualizado INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mensaje (
            id INTEGER PRIMARY KEY,             -- id del mensaje en Boligportal
            hilo INTEGER NOT NULL, pid TEXT NOT NULL, es_mio INTEGER NOT NULL, fecha TEXT NOT NULL,
            texto TEXT NOT NULL, adjuntos INTEGER NOT NULL DEFAULT 0, guardado INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS mensaje_pid ON mensaje (pid, fecha);
        CREATE TABLE IF NOT EXISTS favorito (
            id TEXT PRIMARY KEY,                -- id del piso en la web
            activo INTEGER NOT NULL, by TEXT NOT NULL, ts INTEGER NOT NULL
        );
    """)


def _fila(c):
    """Un contacto validado como fila (pid, hilo, marca, es_mio, fecha), o NoValido."""
    if not isinstance(c, dict):
        raise NoValido("contacto no válido")
    pid, marca, fecha, hilo = str(c.get("pid") or ""), c.get("marca"), str(c.get("fecha") or ""), c.get("hilo")
    if not PID_RE.match(pid) or marca not in MARCAS or not isinstance(c.get("es_mio"), bool):
        raise NoValido(f"contacto no válido ({pid[:12]})")
    if fecha and not FECHA_RE.match(fecha):
        raise NoValido(f"fecha no válida ({pid})")
    if hilo is not None and not _entero(hilo):
        raise NoValido(f"conversación no válida ({pid})")
    return pid, hilo, marca, int(c["es_mio"]), fecha[:25] or None


def guardar_contactos(con, lista, ahora=None):
    """Sustituye el contacto de cada anuncio de la lista (lo que no viene se queda como estaba). Devuelve cuántos
    cambiaron. Valida todo: lo que no encaja se rechaza entero (NoValido). Un anuncio repetido: vale el primero."""
    if not isinstance(lista, list) or len(lista) > MAX_CONTACTOS:
        raise NoValido("contactos: lista no válida")
    filas = {}
    for fila in map(_fila, lista):
        filas.setdefault(fila[0], fila)
    ahora, n = ahora or int(time.time()), 0
    for pid, *datos in filas.values():
        previo = con.execute("SELECT hilo, marca, es_mio, fecha FROM contacto WHERE pid=?", (pid,)).fetchone()
        if previo and list(previo) == datos:
            continue
        con.execute(
            "INSERT INTO contacto (pid, hilo, marca, es_mio, fecha, actualizado) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(pid) DO UPDATE SET hilo=excluded.hilo, marca=excluded.marca, es_mio=excluded.es_mio, "
            "fecha=excluded.fecha, actualizado=excluded.actualizado",
            (pid, *datos, ahora),
        )
        n += 1
    return n


def _entero(v, hasta=10**12):
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < hasta


def _mensaje(m):
    """Un mensaje validado como fila (id, hilo, pid, es_mio, fecha, texto, adjuntos), o NoValido."""
    if not isinstance(m, dict) or not _entero(m.get("id")) or not _entero(m.get("hilo")):
        raise NoValido("mensaje no válido")
    pid, fecha, texto, adj = str(m.get("pid") or ""), str(m.get("fecha") or ""), m.get("texto"), m.get("adjuntos", 0)
    if not PID_RE.match(pid) or not FECHA_RE.match(fecha) or not isinstance(m.get("es_mio"), bool):
        raise NoValido(f"mensaje no válido ({m['id']})")
    if not isinstance(texto, str) or len(texto) > MAX_TEXTO or not (isinstance(adj, int) and 0 <= adj <= 50):
        raise NoValido(f"texto o adjuntos no válidos ({m['id']})")
    return m["id"], m["hilo"], pid, int(m["es_mio"]), fecha[:25], texto, adj


def guardar_mensajes(con, lista, ahora=None):
    """Guarda los mensajes (por su id; si ya estaba, se actualiza). Devuelve cuántos son nuevos."""
    if not isinstance(lista, list) or len(lista) > MAX_MENSAJES:
        raise NoValido("mensajes: lista no válida")
    filas, ahora = [_mensaje(m) for m in lista], ahora or int(time.time())
    antes = con.execute("SELECT COUNT(*) FROM mensaje").fetchone()[0]
    con.executemany(
        "INSERT INTO mensaje (id, hilo, pid, es_mio, fecha, texto, adjuntos, guardado) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET texto=excluded.texto, adjuntos=excluded.adjuntos",
        [(*f, ahora) for f in filas],
    )
    return con.execute("SELECT COUNT(*) FROM mensaje").fetchone()[0] - antes


def mensajes_de(con, pid, limite=300):
    """La conversación de un anuncio de Boligportal, de la más antigua a la más reciente."""
    filas = con.execute(
        "SELECT id, hilo, es_mio, fecha, texto, adjuntos FROM mensaje WHERE pid=? ORDER BY fecha DESC, id DESC LIMIT ?",
        (pid, limite),
    ).fetchall()
    return [
        {"id": r[0], "hilo": r[1], "es_mio": bool(r[2]), "fecha": r[3], "texto": r[4], "adjuntos": r[5]}
        for r in reversed(filas)
    ]


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
    msj = con.execute("SELECT COUNT(*), COUNT(DISTINCT pid) FROM mensaje").fetchone()
    return {"marcas": marcas, "ultimo": ultimo, "favoritos": fav, "mensajes": msj[0], "conversaciones": msj[1]}
