"""Modelo de datos y reglas de pisos-cph (ver docs/arquitectura.md). Solo stdlib + sqlite3.

Una entidad: el ANUNCIO, identificado por «portal:id» (p. ej. «Lejebolig:1812509»). No hay listas, solo estados.
Nada se borra: cada cambio queda en la tabla `cambio` (quién, cuándo, de qué a qué y por qué).

Todas las funciones reciben una conexión sqlite3 abierta; quien llama decide la transacción (`with con:`).
"""

import json
import re
import time

# ------------------------------------------------------------------ estados y permisos
SISTEMA = ("rechazado", "propuesto", "retirado")  # los pone el sistema
PERSONA = ("gusta", "contactado", "descartado")  # los decidís Jon y Unai (o Lares si se lo pedís)
ESTADOS = SISTEMA + PERSONA

# Qué puede hacer cada cliente de la API. No existe ninguna operación de borrar.
PERMISOS = {
    "web": {"leer", "entregar_enlace", "cambiar_estado", "filtros"},
    "lares": {"leer", "entregar_enlace", "cambiar_estado"},
    "vigilante": {"leer", "entregar"},
    "comprobador": {"leer", "comprobar"},
    "panel": {"leer"},
}
# Estados que cada cliente puede poner a mano. «propuesto» = devolver a «sin decidir» (deshacer una decisión).
PUEDE_PONER = {"web": set(PERSONA) | {"propuesto"}, "lares": set(PERSONA) | {"propuesto"}}
TOPE_CAMBIOS_HORA = {"lares": 20, "web": 300}  # cambios de estado por hora y cliente

ORIGENES = ("vigilante", "lares", "jon", "unai", "web", "import")


class Prohibido(Exception):
    """El cliente no tiene permiso para esa operación (→ 403)."""


class NoValido(ValueError):
    """Datos o petición no válidos (→ 400)."""


class Tope(Exception):
    """El cliente ha superado su tope de cambios (→ 429)."""


def permitir(cliente, operacion):
    if operacion not in PERMISOS.get(cliente, ()):
        raise Prohibido(f"«{cliente}» no puede «{operacion}»")


# ------------------------------------------------------------------ esquema
def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS anuncio (
            uid TEXT PRIMARY KEY,               -- «Portal:id»
            portal TEXT NOT NULL, pid TEXT NOT NULL,
            datos TEXT NOT NULL,                -- JSON con los campos de portales.CAMPOS (y los que se añadan a mano)
            estado TEXT NOT NULL, motivo TEXT, avisos TEXT NOT NULL DEFAULT '[]',
            zona TEXT, lat REAL, lon REAL, precision TEXT,   -- precision: exacta | calle | aproximada
            origen TEXT NOT NULL, creado INTEGER NOT NULL, actualizado INTEGER NOT NULL,
            comprobado INTEGER, activo TEXT     -- última comprobación: activo | retirado | desconocido
        );
        CREATE INDEX IF NOT EXISTS anuncio_estado ON anuncio(estado);
        CREATE TABLE IF NOT EXISTS alias (     -- el mismo piso en otro portal apunta al anuncio principal
            uid TEXT PRIMARY KEY, principal TEXT NOT NULL REFERENCES anuncio(uid)
        );
        CREATE TABLE IF NOT EXISTS cambio (
            n INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, de TEXT, a TEXT NOT NULL,
            motivo TEXT, quien TEXT NOT NULL, ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS cambio_uid ON cambio(uid);
        CREATE INDEX IF NOT EXISTS cambio_quien_ts ON cambio(quien, ts);
    """)


# ------------------------------------------------------------------ filtros
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


def evaluar(d, filtros, zonas):
    """(motivos, avisos): motivos = por qué NO pasa (vacío = pasa); avisos = lo que falta confirmar.
    Se devuelven todos los motivos, no solo el primero: si el anuncio lo añade una persona, los ve todos."""
    F, motivos, avisos = filtros, [], []
    texto = d.get("texto") or ""
    if BAD_TITLE.search(d.get("titulo") or ""):
        motivos.append("es una habitación / residencia")
    if d.get("precio") and d["precio"] > F["precio_max"]:
        motivos.append(f"precio {d['precio']} > {F['precio_max']}")
    dmin = int(F.get("dorm_min", 3))
    if isinstance(d.get("dorm"), int):
        if d["dorm"] < dmin:
            motivos.append(f"{d['dorm']} dormitorios (< {dmin})")
    elif isinstance(d.get("vaer"), int) and d["vaer"] < dmin + 1:
        motivos.append(f"{d['vaer']} værelser (< {dmin + 1}: menos de {dmin} dormitorios)")
    elif d.get("vaer") is None:
        avisos.append("værelser sin dato")
    z = d.get("zona")
    if z is None:
        motivos.append("zona no reconocida")
    elif not zonas.get(z, False):
        motivos.append(f"zona excluida: {z}")
    disp = d.get("disp")
    if disp == "ya":
        motivos.append("libre ya (no hacia la fecha buscada)")
    elif disp and not (F["disp_desde"] <= disp <= F["disp_hasta"]):
        motivos.append(f"libre el {disp} (fuera de {F['disp_desde']}..{F['disp_hasta']})")
    elif not disp:
        avisos.append("fecha de entrada sin confirmar")
    cm = d.get("contrato_meses")
    if F.get("contrato_largo", True) and ((cm and 0 < cm <= 12) or SHORT.search(texto)):
        motivos.append(f"contrato corto ({cm or '≤12'} meses)")
    if F.get("compartir", True):
        if d.get("compartible") is False or NO_SHARE.search(texto):
            motivos.append("no permite compartir")
        elif d.get("compartible") is not True and not YES_SHARE.search(texto):
            avisos.append("compartir sin confirmar")
    quiere, am = F.get("amueblado", "igual"), d.get("amueblado")
    if quiere in ("si", "no"):
        if am is not None and am != (quiere == "si"):
            motivos.append("amueblado" if am else "sin amueblar")
        elif am is None:
            avisos.append("amueblado sin confirmar")
    if not isinstance(d.get("dorm"), int):
        avisos.append(f"{dmin} dormitorios por confirmar (plano)")
    return motivos, avisos


# ------------------------------------------------------------------ lectura
def _fila(r):
    if r is None:
        return None
    a = dict(r)
    a["datos"] = json.loads(a["datos"])
    a["avisos"] = json.loads(a["avisos"])
    return a


def resolver(con, uid):
    """uid principal (sigue los alias)."""
    r = con.execute("SELECT principal FROM alias WHERE uid=?", (uid,)).fetchone()
    return r[0] if r else uid


def obtener(con, uid):
    return _fila(con.execute("SELECT * FROM anuncio WHERE uid=?", (resolver(con, uid),)).fetchone())


def listar(con, estados=None, desde=None, limite=2000):
    """Anuncios (los más recientes primero). estados: lista; desde: ts (solo los actualizados después)."""
    q, args = "SELECT * FROM anuncio WHERE 1=1", []
    if estados:
        q += f" AND estado IN ({','.join('?' * len(estados))})"
        args += list(estados)
    if desde:
        q += " AND actualizado > ?"
        args.append(int(desde))
    q += " ORDER BY actualizado DESC LIMIT ?"
    args.append(int(limite))
    return [_fila(r) for r in con.execute(q, args)]


def historial(con, uid, limite=200):
    uid = resolver(con, uid)
    return [
        dict(r)
        for r in con.execute(
            "SELECT de, a, motivo, quien, ts FROM cambio WHERE uid=? ORDER BY n DESC LIMIT ?", (uid, limite)
        )
    ]


def contar(con):
    """{estado: número} para el panel."""
    return {e: n for e, n in con.execute("SELECT estado, COUNT(*) FROM anuncio GROUP BY estado")}


# ------------------------------------------------------------------ escritura
def _registrar(con, uid, de, a, motivo, quien, ahora):
    con.execute(
        "INSERT INTO cambio (uid, de, a, motivo, quien, ts) VALUES (?,?,?,?,?,?)", (uid, de, a, motivo, quien, ahora)
    )


def _estado_sistema(motivos, origen):
    """Estado que decide el sistema. Si lo añade una persona o Lares, se propone aunque no pase (los motivos van de "
    "aviso)."""
    if not motivos or origen != "vigilante":
        return "propuesto", None
    return "rechazado", "; ".join(motivos)


def entregar(con, datos, origen, quien, filtros, zonas, ubicacion=None, ahora=None):
    """Guarda (o actualiza) un anuncio leído de un portal. Devuelve el anuncio.
    - Nuevo: se evalúa con los filtros → propuesto / rechazado.
    - Ya existente: se refrescan sus datos; si estaba en un estado del sistema se vuelve a evaluar
      (p. ej. cambiaron los filtros); si lo decidisteis vosotros, la decisión NO se toca.
    ubicacion: (lat, lon, precision) si se conoce."""
    if origen not in ORIGENES:
        raise NoValido("origen no válido")
    if not datos.get("portal") or not datos.get("pid"):
        raise NoValido("faltan portal o id")
    ahora = int(ahora or time.time())
    uid = resolver(con, f"{datos['portal']}:{datos['pid']}")
    previo = obtener(con, uid)
    if previo:  # los datos nuevos completan a los viejos, no borran lo que ya se sabía
        datos = {**previo["datos"], **{k: v for k, v in datos.items() if v not in (None, "", [])}}
    motivos, avisos = evaluar(datos, filtros, zonas)
    # un anuncio que añadió una persona o Lares sigue siendo «manual» aunque luego lo vea el vigilante
    origen_efectivo = previo["origen"] if previo and previo["origen"] != "vigilante" else origen
    if origen_efectivo != "vigilante" and motivos:
        avisos = [f"no pasa: {m}" for m in motivos] + avisos
    lat, lon, prec = ubicacion or (datos.get("lat"), datos.get("lon"), "exacta" if datos.get("lat") else None)
    if previo and previo["estado"] in PERSONA:
        estado, motivo = previo["estado"], previo["motivo"]
    else:
        estado, motivo = _estado_sistema(motivos, origen_efectivo)
    if previo:
        lat, lon, prec = (lat, lon, prec) if lat is not None else (previo["lat"], previo["lon"], previo["precision"])
        con.execute(
            "UPDATE anuncio SET datos=?, estado=?, motivo=?, avisos=?, zona=?, lat=?, lon=?, precision=?, "
            "actualizado=? "
            "WHERE uid=?",
            (
                json.dumps(datos, ensure_ascii=False),
                estado,
                motivo,
                json.dumps(avisos, ensure_ascii=False),
                datos.get("zona"),
                lat,
                lon,
                prec,
                ahora,
                uid,
            ),
        )
        if estado != previo["estado"]:
            _registrar(con, uid, previo["estado"], estado, motivo, quien, ahora)
    else:
        con.execute(
            "INSERT INTO anuncio (uid, portal, pid, datos, estado, motivo, avisos, zona, lat, lon, precision, origen, "
            "creado, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uid,
                datos["portal"],
                str(datos["pid"]),
                json.dumps(datos, ensure_ascii=False),
                estado,
                motivo,
                json.dumps(avisos, ensure_ascii=False),
                datos.get("zona"),
                lat,
                lon,
                prec,
                origen,
                ahora,
                ahora,
            ),
        )
        _registrar(con, uid, None, estado, motivo or f"añadido por {quien}", quien, ahora)
    return {**obtener(con, uid), "nuevo": not previo}


def cambiar_estado(con, uid, nuevo, cliente, quien, motivo=None, ahora=None):
    """Una persona (o Lares por encargo) cambia el estado de UN anuncio."""
    permitir(cliente, "cambiar_estado")
    if nuevo not in ESTADOS:
        raise NoValido("estado no válido")
    if nuevo not in PUEDE_PONER.get(cliente, ()):
        raise Prohibido(f"«{cliente}» no puede poner «{nuevo}»")
    ahora = int(ahora or time.time())
    tope = TOPE_CAMBIOS_HORA.get(cliente)
    if (
        tope
        and con.execute(
            "SELECT COUNT(*) FROM cambio WHERE quien LIKE ? AND ts > ?", (f"{cliente}%", ahora - 3600)
        ).fetchone()[0]
        >= tope
    ):
        raise Tope(f"«{cliente}» ya ha hecho {tope} cambios en la última hora")
    a = obtener(con, uid)
    if a is None:
        raise NoValido("anuncio no encontrado")
    if a["estado"] == nuevo:
        return a
    motivo = (motivo or "")[:300] or None
    con.execute("UPDATE anuncio SET estado=?, motivo=?, actualizado=? WHERE uid=?", (nuevo, motivo, ahora, a["uid"]))
    _registrar(con, a["uid"], a["estado"], nuevo, motivo, f"{cliente}:{quien}" if quien != cliente else cliente, ahora)
    return obtener(con, a["uid"])


def comprobar(con, uid, resultado, motivo="", ahora=None):
    """Resultado del comprobador (¿sigue publicado?). Nunca pisa una decisión vuestra: si el piso está en gusta /
    contactado / descartado y el portal lo retiró, solo se añade un aviso."""
    if resultado not in ("activo", "retirado", "desconocido"):
        raise NoValido("resultado no válido")
    ahora = int(ahora or time.time())
    a = obtener(con, uid)
    if a is None:
        raise NoValido("anuncio no encontrado")
    avisos = [x for x in a["avisos"] if not x.startswith("el portal dice:")]
    estado = a["estado"]
    if resultado == "retirado":
        if estado in PERSONA:
            avisos.insert(0, f"el portal dice: {motivo or 'retirado'}")
        else:
            estado = "retirado"
    elif resultado == "activo" and estado == "retirado":
        estado = "propuesto"  # vuelve a estar publicado: se propone de nuevo
    con.execute(
        "UPDATE anuncio SET estado=?, avisos=?, comprobado=?, activo=?, actualizado=? WHERE uid=?",
        (
            estado,
            json.dumps(avisos, ensure_ascii=False),
            ahora,
            resultado,
            ahora if estado != a["estado"] or avisos != a["avisos"] else a["actualizado"],
            a["uid"],
        ),
    )
    if estado != a["estado"]:
        _registrar(con, a["uid"], a["estado"], estado, motivo or resultado, "comprobador", ahora)
    return obtener(con, a["uid"])


def unir(con, uid_principal, uid_alias, quien, ahora=None):
    """El mismo piso en dos portales: el alias pasa a apuntar al principal (sus enlaces se guardan en `enlaces`)."""
    ahora = int(ahora or time.time())
    p, x = obtener(con, uid_principal), obtener(con, uid_alias)
    if not p or not x or p["uid"] == x["uid"]:
        raise NoValido("anuncios no válidos para unir")
    enlaces = list(dict.fromkeys((p["datos"].get("enlaces") or [p["datos"].get("link")]) + [x["datos"].get("link")]))
    p["datos"]["enlaces"] = [e for e in enlaces if e]
    con.execute(
        "UPDATE anuncio SET datos=?, actualizado=? WHERE uid=?",
        (json.dumps(p["datos"], ensure_ascii=False), ahora, p["uid"]),
    )
    con.execute("INSERT OR REPLACE INTO alias (uid, principal) VALUES (?,?)", (x["uid"], p["uid"]))
    con.execute(
        "UPDATE anuncio SET estado='retirado', motivo=?, actualizado=? WHERE uid=?",
        (f"duplicado de {p['uid']}", ahora, x["uid"]),
    )
    _registrar(con, x["uid"], x["estado"], "retirado", f"duplicado de {p['uid']}", quien, ahora)
    return obtener(con, p["uid"])
