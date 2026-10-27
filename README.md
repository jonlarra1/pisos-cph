# Pisos Copenhague

Mapa (Leaflet + OpenStreetMap) de pisos de alquiler de 3 dormitorios en Copenhague y alrededores, líneas de S-tog/metro/Letbane y la ruta más rápida en transporte público, bici o andando desde cada piso a: Nordic Solar (trabajo, Hellerup), GN (Ballerup), DTU (Lyngby) y los destinos que añadáis (compartidos).

Estados compartidos (gusta / propuesto / contactado / descartado), pisos propuestos por Lares (agente en la Raspberry Pi) y una barra para añadir un piso pegando su enlace. Cómo encaja todo: `CLAUDE.md` (tabla «Estado real») y `docs/arquitectura.md`.

## Link para compartir
https://jonlarra1.github.io/pisos-cph/ (GitHub Pages; el link no cambia).
Para actualizarlo: `git add -A && git commit -m "..." && git push` — en 1–2 min se ve la versión nueva.

## Ver el mapa en local
```
python3 -m http.server 8000   # dentro de esta carpeta
```
Abre http://localhost:8000 (no con file://).

## Archivos
- `index.html` — toda la app. Los datos van inline (`PISOS`, `OUT`, `RUTAS`, `RAIL`…), copiados de `data/` con `tools/sync_web.py`.
- `data/` — `pisos.json`, `descartados.json`, `rutas.json`, `directo.json`, `barrios.json`.
- `backend/` — servidor de la Pi (API, base de datos, motor de rutas, panel). Ver `backend/README.md`.
- `lares/` — lo que corre en el perfil de Lares en la Pi. Ver `lares/README.md`.
- `tools/` — recalcular rutas, copiar datos a la web y métricas DORA (`dora.py`).
- `docs/` — `arquitectura.md` (diseño y plan), `historial.md` (diario), `despliegues.jsonl` y `metricas.jsonl` (DORA).

## Tests
```
./tests/run.sh               # backend, datos, Lares, calidad, DORA: ~10 s, sin internet (antes de cada commit)
./tests/run.sh web           # la web en Chromium: ~100 s (Pi y mapas simulados)
./tests/run.sh -k propuestos # solo los tests cuyo nombre contiene esa palabra
```
- `tests/test_backend.py` — API (estados, zonas, filtros, candidatos, destinos, rutas, fotos, límites, CORS), logs y checks del monitor.
- `tests/test_anuncios.py`, `tests/test_api_v2.py` — base única de anuncios: reglas, estados, permisos por clave, historial.
- `tests/test_precalculo.py` — cola de rutas previas y freno del motor.
- `tests/test_herramienta_lares.py` — `lares/pisos.py` contra una API simulada.
- `tests/test_calidad.py` — seguridad, estilo y complejidad del código. `tests/test_dora.py` — métricas.
- `tests/test_datos.py` — `data/*.json` igual que la web, rutas para cada piso, coordenadas, enlaces, CSP y SRI.
- `tests/test_lares.py` — vigilante de Lares con portales simulados: páginas adaptativas, recuerdo de candidatos, filtros.
- `tests/test_web.py` — cada función de la página: pines, propuestos (y datos maliciosos), estados, ficha, fotos, tiempo real.
- La web necesita Playwright: `python3 -m venv .venv && .venv/bin/pip install playwright==1.64.1`
  (usa el Chromium de `~/.cache/ms-playwright`; si no hay, `.venv/bin/playwright install chromium-headless-shell`).

## Recalcular rutas (si cambian los pisos o los horarios)
1. Descarga el GTFS de Rejseplanen: https://www.rejseplanen.info/labs/GTFS.zip y descomprímelo (p. ej. en `/tmp/gtfs`).
2. Prepara un archivo por día laborable (fechas de una semana normal dentro del periodo del GTFS):
   ```
   for d in 20261026 20261027 20261028 20261029 20261030; do python3 tools/prep.py /tmp/gtfs prep_$d.pkl $d; done
   ```
3. Calcula las rutas (tarda unos minutos):
   ```
   python3 tools/route.py data/pisos.json data/rutas.json /tmp/gtfs/shapes.txt prep_2026102*.pkl prep_20261030.pkl
   ```
4. Copia los datos a la web: `python3 tools/sync_web.py` (PISOS, OUT, RUTAS y DIRECTO desde `data/`, en el formato que arranca más rápido) y pasa `./tests/run.sh`.

Modos (siempre con transporte público): 🚶 andando + bus/metro/Letbane/tren (Unai), 🚲 bici a 20 km/h + solo S-tog/tren regional/Lokaltog, con la bici dentro (Jon). Defaults: Nordic Solar 🚲, GN 🚶, DTU 🚶; se cambian en cada ruta.
Tras `route.py`, ejecuta `python3 tools/walkpaths.py data/rutas.json` (tramos a pie/bici por calle; solo una vez) y `python3 tools/fixlate.py data/rutas.json` y `python3 tools/direct.py data/pisos.json data/directo.json` (referencia solo bici / solo andando).

Alternativas: además de la más rápida, hasta 2 rutas que evitan la línea principal de las anteriores (máx. +25 min). Con bici valen S-tog, tren regional/Lokaltog y Letbane.

Cómo se calculan: para cada día, hora de llegada ("llegar antes de") y límite andando por tramo, se busca la salida más tarde que llega a tiempo (Connection Scan Algorithm sobre el GTFS). Transbordos con 1,5 min de margen. Cada ruta tiene además un botón que abre Rejseplanen con origen, destino y hora ya puestos.

Horarios guardados ("Mis horarios"): edita `const HORARIOS=[...]` en `index.html`, por ejemplo
`{n:"Jon → Nordic Solar", dest:"ns", days:["lun","mar","mié","jue","vie"], time:"09:00"}` (la hora tiene que ser una de `RUTAS.times`).
