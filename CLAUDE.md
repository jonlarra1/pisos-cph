# Pisos Copenhague — project context for Claude Code

## What this is
A single-page Leaflet map (`index.html`) of rental flats (3 bedrooms, shared by 3 people) in Copenhagen and suburbs.
The user is Jon. He works at Nordic Solar (Hellerup). He prefers short, simple, direct answers (non-native English speaker; Spanish is fine too).

## Files
- `index.html` — the whole app (HTML + CSS + JS, Leaflet 1.9.4 + leaflet-polylineoffset from CDN).
  Big inline data constants inside the `<script>`:
  - `PISOS` — the active flats (also copied to `data/pisos.json`).
  - `OUT` — discarded flats with reason (also in `data/descartados.json`).
  - Basemap = OpenStreetMap standard tiles (CARTO now needs an API key). Dark theme = CSS filter `--tiles`.
  - `RAIL` — real S-tog, metro (M1–M4), Letbane and Bx geometry + exact station coords (from github.com/Miqell24/copenhagen-bus-map, OSM + Rejseplanen). KEEP THIS.
  - `ST`, `LINES`, `MST`, `MLINES`, `LETB` — station lists per line; used for "nearest station" in each flat card. KEEP.
  - `WORK` — Jon's workplace = Nordic Solar, Strandvejen 102E, Hellerup. KEEP.
  - `RUTAS` — precomputed public-transport routes (GN, Nordic Solar, DTU) per weekday × arrival time × walking limit, from `tools/prep.py` + `tools/route.py` (see README). Deduplicated: `routes`, `paths`, index `ix[mode][day][time][limit][flatId][dest]`, mode = foot (walk + any transit) | bike (bike 20 km/h + S-tog/regional/Lokaltog only, no bus/metro, no lock time).
  - `PERF` — profiles shown in «Configuración» (right, under «Líneas»): Jon (bike, default 10 min) and Unai (foot, default 10 min); mode + max minutes per leg (5–30), saved per browser. `ROWS` — route rows: Nordic Solar→Jon, GN→Unai, DTU→Jon and DTU→Unai. `DIRECTO` — bike-only / walk-only reference times.
  - `EXTRA` — user-added destinations (Configuración → Destinos), saved per browser (localStorage "destinos"); routes: live bike/walk via FOSSGIS OSRM from the browser + Rejseplanen link (no precomputed transit). To make one permanent, add it to DEST in tools/route.py + tools/direct.py and recompute.
  - Only the focused route animates; the others are drawn static and faded.
  - Station dots/labels hide when all lines through them are switched off (`lineOn`, `updateStations`).
  - `HORARIOS` — saved schedule presets shown as "Mis horarios".
- Flat status (`st` in PISOS, `stF` = date of last change): gusta (blue, we like it), nuevo (orange, new/undecided), contactado (green, landlord contacted), descartado (red; shown DESCARTE_DIAS=7 days, then auto-moved to «Descartados»). Default colour mode = Estado; pin label = monthly cost. Jon tells Claude status changes; update both `data/pisos.json` and `const PISOS` in index.html, then commit + push.
- `data/pisos.json` — flat data (id, zona, barrio, calle, m2, vaer, dorm, alq, aconto, dep, pre, disp, link, lat, lon, ubic = location precision, estado/aviso = warnings).

## Rules
- Keep the UI, barrio grouping, colours, light/dark theme and Spanish UI text. Score was removed from the UI on request (list sorted by monthly cost); no barrio circles on the map.
- Keep OSM attribution visible: "© OpenStreetMap contributors".
- Respect OSM tile and Nominatim usage policies (light personal use, real User-Agent, max 1 request/second for Nominatim).
- Do not invent addresses or coordinates. If an exact address is not found, keep the old point and say so in `ubic`.
- Hosting: GitHub Pages (fixed link for flatmates). Commit + push to update.
