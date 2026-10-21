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
  - `RUTAS` — precomputed public-transport routes (GN, Nordic Solar, DTU) per weekday × arrival time × walking limit, from `tools/prep.py` + `tools/route.py` (see README). Deduplicated: `routes`, `paths`, index `ix[day][time][limit][flatId][dest]`.
  - `HORARIOS` — saved schedule presets shown as "Mis horarios".
- `data/pisos.json` — flat data (id, zona, barrio, calle, m2, vaer, dorm, alq, aconto, dep, pre, disp, link, lat, lon, ubic = location precision, estado/aviso = warnings).

## Rules
- Keep the UI, scoring, barrio grouping, colours, light/dark theme and Spanish UI text as they are.
- Keep OSM attribution visible: "© OpenStreetMap contributors".
- Respect OSM tile and Nominatim usage policies (light personal use, real User-Agent, max 1 request/second for Nominatim).
- Do not invent addresses or coordinates. If an exact address is not found, keep the old point and say so in `ubic`.
- Hosting: GitHub Pages (fixed link for flatmates). Commit + push to update.
