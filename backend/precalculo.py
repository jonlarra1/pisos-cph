"""Rutas de los destinos compartidos: cálculo previo y freno para que el motor de la Pi no se sature.

- Freno: como mucho MAX_ESPERA búsquedas de la web esperando al motor a la vez; las demás reciben «ocupado» al
  momento (la web reintenta) en vez de quedarse cada una con un hilo parado.
- Cola: al añadir un destino, la Pi calcula en segundo plano, de una en una y solo cuando la web no está esperando,
  las rutas de los pisos del mapa para los dos perfiles por defecto (mañana, llegar a las 08:30, hoy y los
  próximos días). Se guardan 24 h en la caché del motor: al abrir el piso ya están. La cola tiene tope (MAX_COLA):
  si se añaden demasiados destinos de golpe, lo que no cabe no se calcula antes (se calcula al abrir el piso).
"""

import collections
import datetime
import threading
import time
import urllib.parse

MAX_ESPERA = 6
MAX_COLA = 400
DIAS = 3
HORA = "08:30"  # la hora por defecto de «Mañana» en la web
PERFILES = (  # los valores por defecto de la web (PERF): Jon en bici, Unai andando
    {"perfil": "bike", "lim": "10", "vw": "5", "vb": "20"},
    {"perfil": "foot", "lim": "10", "vw": "5", "vb": "20"},
)
PAUSA = 0.3  # entre cálculos, para dejar CPU al resto
ESTADO = {"en_cola": 0, "lote": 0, "calculadas": 0, "errores": 0, "sin_sitio": 0, "ocupado": 0, "ultima": ""}


class Freno:
    """Cuenta las búsquedas de la web que esperan o usan el motor."""

    def __init__(self, maximo=MAX_ESPERA):
        self.n, self.maximo, self._lock = 0, maximo, threading.Lock()

    def entrar(self):
        with self._lock:
            if self.n >= self.maximo:
                ESTADO["ocupado"] += 1
                return False
            self.n += 1
            return True

    def salir(self):
        with self._lock:
            self.n -= 1

    def libre(self):
        return self.n == 0


def peticiones(ll, nombre, pisos, hoy=None, dias=DIAS):
    """Consultas /api/ruta (sin «?») para un destino: cada piso × perfil × día, como las pide la web."""
    hoy = hoy or datetime.date.today()
    out = []
    for i in range(dias):
        fecha = (hoy + datetime.timedelta(days=i)).isoformat()
        for lat, lon in pisos:
            for pf in PERFILES:
                q = {"olat": lat, "olon": lon, "dlat": ll[0], "dlon": ll[1], "dname": nombre[:60], "fecha": fecha}
                out.append(urllib.parse.urlencode({**q, "hora": HORA, "modo": "llegar", **pf}))
    return out


class Cola:
    """Trabajos de cálculo previo, de uno en uno. `calcular(qs)` -> (código, respuesta); `pisos()` -> [(lat, lon)]."""

    def __init__(self, calcular, pisos, freno, maximo=MAX_COLA, aviso=None):
        self.calcular, self.pisos, self.freno, self.maximo = calcular, pisos, freno, maximo
        self.q, self._lock = collections.deque(), threading.Lock()
        self.aviso = aviso or (lambda nivel, msg: None)  # (nivel, mensaje) -> log de eventos del backend
        self.tanda = {"t0": 0.0, "ok": 0, "error": 0}

    def destino(self, ll, nombre, hoy=None):
        """Mete en la cola las rutas de un destino nuevo. Devuelve cuántas entraron (las que no caben, fuera)."""
        try:
            pisos = self.pisos()
        except Exception as e:
            self.aviso("warn", f"no se pudo leer la lista de pisos del mapa ({type(e).__name__}): sin rutas previas")
            pisos = []
        nuevas = peticiones(ll, nombre, pisos, hoy)
        with self._lock:
            if not self.q:
                ESTADO["lote"] = 0  # tanda nueva (para la barra de progreso de la web)
                self.tanda = {"t0": time.time(), "ok": 0, "error": 0}
            sitio = max(0, self.maximo - len(self.q))
            self.q.extend(nuevas[:sitio])
            ESTADO["sin_sitio"] += len(nuevas) - min(sitio, len(nuevas))
            ESTADO["en_cola"] = len(self.q)
            ESTADO["lote"] += min(sitio, len(nuevas))
        if len(nuevas) > sitio:
            self.aviso(
                "warn", f"cola de rutas llena: {len(nuevas) - sitio} rutas de «{nombre}» se calcularán al abrir el piso"
            )
        return min(sitio, len(nuevas))

    def uno(self):
        """Calcula un trabajo si hay y la web no espera. Devuelve True si ha hecho algo."""
        if not self.freno.libre():
            return False
        with self._lock:
            if not self.q:
                return False
            qs = self.q.popleft()
            ESTADO["en_cola"] = len(self.q)
        try:
            code, _ = self.calcular(qs)
            ok = code == 200
        except Exception as e:  # el hilo no se cae por un trabajo malo
            ok, code = False, type(e).__name__
        ESTADO["calculadas" if ok else "errores"] += 1
        self.tanda["ok" if ok else "error"] += 1
        if not ok:
            ESTADO["ultima"] = str(code)
        if not self.q:  # tanda terminada: una línea en el log con lo que ha costado
            t = self.tanda
            self.aviso(
                "warn" if t["error"] else "info",
                f"rutas previas listas: {t['ok']} calculadas, {t['error']} con error"
                + (f" (último: {ESTADO['ultima']})" if t["error"] else "")
                + f", en {time.time() - (t['t0'] or time.time()):.0f} s",
            )
        return True

    def trabajar(self):
        while True:
            time.sleep(PAUSA if self.uno() else 2)


def progreso():
    """Para la barra de la web: rutas que faltan de la tanda actual y cuántas tenía."""
    return {"pendientes": ESTADO["en_cola"], "total": ESTADO["lote"] if ESTADO["en_cola"] else 0}


def resumen():
    E = ESTADO
    return (
        f"rutas previas: en cola {E['en_cola']}, calculadas {E['calculadas']}, con error {E['errores']}, "
        f"sin sitio en la cola {E['sin_sitio']} · búsquedas rechazadas por motor ocupado {E['ocupado']}"
    )
