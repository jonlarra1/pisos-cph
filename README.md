# Pisos Copenhague

Mapa (Leaflet + OpenStreetMap) de pisos de alquiler de 3 dormitorios en Copenhague y alrededores, con puntuación, líneas de S-tog/metro/Letbane y la ruta más rápida en transporte público desde cada piso a: Nordic Solar (trabajo, Hellerup), GN (Ballerup) y DTU (Lyngby).

## Link para compartir
https://jonlarra1.github.io/pisos-cph/ (GitHub Pages; el link no cambia).
Para actualizarlo: `git add -A && git commit -m "..." && git push` — en 1–2 min se ve la versión nueva.

## Ver el mapa en local
```
python3 -m http.server 8000   # dentro de esta carpeta
```
Abre http://localhost:8000 (no con file://).

## Archivos
- `index.html` — toda la app. Los datos van inline (`PISOS`, `OUT`, `RUTAS`, `RAIL`…).
- `data/pisos.json`, `data/descartados.json`, `data/rutas.json` — copias de esos datos.
- `tools/` — scripts para recalcular las rutas.

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
4. Sustituye la línea `const RUTAS=...;` de `index.html` por el contenido de `data/rutas.json`.

Cómo se calculan: para cada día, hora de llegada ("llegar antes de") y límite andando por tramo, se busca la salida más tarde que llega a tiempo (Connection Scan Algorithm sobre el GTFS). Transbordos con 1,5 min de margen. Cada ruta tiene además un botón que abre Rejseplanen con origen, destino y hora ya puestos.

Horarios guardados ("Mis horarios"): edita `const HORARIOS=[...]` en `index.html`, por ejemplo
`{n:"Jon → Nordic Solar", dest:"ns", days:["lun","mar","mié","jue","vie"], time:"09:00"}` (la hora tiene que ser una de `RUTAS.times`).
