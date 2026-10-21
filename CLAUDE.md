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
  - `PERF` — profiles in «Configuración» (right, under «Líneas»): Jon (bike) and Unai (foot); mode, max minutes per leg (5–30), walk speed 4/5/6 km/h, bike 15/20/25 km/h; saved per browser. `adj()` rescales walk/bike legs to the chosen speed (transit times fixed; ⚠️ if arriving late). `ROWS` — route rows: Nordic Solar→Jon, GN→Unai, DTU→Jon and DTU→Unai. `DIRECTO` — bike-only / walk-only reference times.
  - `EXTRA` — user-added destinations (Configuración → Destinos), saved per browser (localStorage "destinos"); routes: live bike/walk via FOSSGIS OSRM from the browser + Rejseplanen link (no precomputed transit). To make one permanent, add it to DEST in tools/route.py + tools/direct.py and recompute.
  - Only the focused route animates; the others are drawn static and faded.
  - Station dots/labels hide when all lines through them are switched off (`lineOn`, `updateStations`).
  - `HORARIOS` — saved schedule presets shown as "Mis horarios".
- Flat status (`st` in PISOS, `stF` = date of last change; changeable in the card/sheet, overrides saved per browser in localStorage "estados" until a shared backend exists): gusta (blue, we like it), nuevo (orange, new/undecided), contactado (green, landlord contacted), descartado (red; shown DESCARTE_H=48 h on the map with a countdown badge, then hidden and listed in «Descartados»; kept in the DB). Default colour mode = Estado; pin label = monthly cost. Jon tells Claude status changes; update both `data/pisos.json` and `const PISOS` in index.html, then commit + push.
- `data/pisos.json` — flat data (id, zona, barrio, calle, m2, vaer, dorm, alq, aconto, dep, pre, disp, link, lat, lon, ubic = location precision, estado/aviso = warnings).

## Rules
- Forma de trabajar: contar cada paso y cada test en una línea, y actuar como asistente (preguntar antes de cambios grandes o en la Pi), salvo que Jon pida lo contrario.
- Layout: left rail = flat list, or the selected flat's ficha (desktop); right column = Viaje, Ver y filtrar, Líneas, Configuración. Mobile: floating card under the map.
- Keep the UI, barrio grouping, colours, light/dark theme and Spanish UI text. Score was removed from the UI on request (list sorted by monthly cost); no barrio circles on the map.
- Keep OSM attribution visible: "© OpenStreetMap contributors".
- Respect OSM tile and Nominatim usage policies (light personal use, real User-Agent, max 1 request/second for Nominatim).
- Do not invent addresses or coordinates. If an exact address is not found, keep the old point and say so in `ubic`.
- Shared backend: `backend/` (Python stdlib + SQLite in Docker) runs on the Raspberry Pi at `/home/docker/pisos-backend`, bound to 127.0.0.1:8787, public via Tailscale Funnel `https://raspberrypi.tail9f1069.ts.net:8443`. Page reads `/api/estados` on load + every 60 s and POSTs `/api/estado`; offline → localStorage only. Connected to Lares via the shared folder `Vivienda/pisos-bridge` (see `lares/README.md`): Lares writes candidatos.json, backend serves `/api/candidatos` and `/api/zonas` (POST `/api/zona`) and writes config.json. Lares only: never read other Hermes agents/profiles. Only touch `/home/docker/pisos-backend` on the Pi; use targeted checks (grep).
- Monitoring & logs policy (keep for every new feature): backend logs JSON lines in /data/logs (api, events, checks, client); checks every 5 min (funnel_publico, web_github, lares_pasadas, lares_cron, buzon_compartido, api_errores, fotos); private panel http://100.72.170.60:9445 (Jon has MagicDNS off on his devices; tailscale serve --tcp, tailnet only, no identity header → accepted) or https://raspberrypi.tail9f1069.ts.net:9444 (needs MagicDNS; Tailscale-User-Login must start with larrabajojon@). Add a check + log events for anything new.
- Photos: `fotos` (list, floor plan first when `fotoPlano`) per flat, shown through the backend image proxy `/api/img?u=` (allow-listed hosts, disk cache 300 MB).
- Right column «🔎 Búsqueda (Lares)» = search params stored on the Pi (`/api/filtros`, zones) → config.json → Lares. Any change to «Búsqueda» (filters, zones) MUST ask for confirmation with a pop-up whose first line is «⚠️ ESTE CAMBIO ES DE BÚSQUEDA, NO DE VISUALIZACIÓN» (it changes what Lares searches for everyone). «👁 Visualización» = map-only view settings (states shown, «Barrios y municipios» layer, colour by Estado/Coste/Barrios — «Tu prioridad» removed), per browser.
- UI settings per browser in localStorage: `ui` (day, colour mode, barrio filter, lines off, route option, open panels), `perfiles`, `tripTime`, `yo`, `destinos`, `cardPos`.
- Hosting: GitHub Pages (fixed link for flatmates). Commit + push to update.

## Estado (2026-10-21, 21:10) y pendientes
- Hecho y publicado: mapa con 12 pisos, rutas (bici/andando, 3 opciones, 5 días × 6 horas × límites 5–30 min), perfiles Jon/Unai,
  estados compartidos vía Pi, Lares v2 conectado (buzón pisos-bridge), menú «Búsqueda (Lares)» / «Visualización», galería de fotos,
  panel privado :9444, logs + checks. Listado izquierdo: principal = solo 🔵 gusta + 🟢 contactado; «🟠 Propuestos» = nuevos del
  mapa + candidatos de Lares juntos (una sola categoría); «🔴 Descartados» = temporales (48 h, con «recuperar») + definitivos.
- Pendiente:
  1. Fireworks (proveedor del modelo de Lares) suspendido → avisos de Lares al Bot Chat fallan (check `lares_cron` en rojo). Lo arregla Jon.
  2. Tarea IA de Lares que mire los planos y escriba `pisos-bridge/planos.json` (`{"<id>": {"dorm": 3}}`) — cuando Lares tenga modelo.
  3. Pasar al mapa los candidatos que se marquen 🔵 en «Propuestos» (geocodificar + rutas: tools/route.py para esos ids y fusionar
     en data/rutas.json, como se hizo con 5680524/5680537/5680562).
  4. 14 pisos de Lejebolig de nuevos_desde_14oct.md con fecha sin confirmar (Lejebolig no la muestra sin sesión) — no añadidos.
- Puertos ya usados en la Pi (no tocar): Portainer 8000/9000/9443. Nuestro: 127.0.0.1:8787 (API, Funnel :8443), 127.0.0.1:8790 (panel: serve https :9444 por nombre, y serve TCP :9445 por IP http://100.72.170.60:9445 para aparatos sin MagicDNS). Comprobar siempre con `ss -ltnp | grep :PUERTO` antes de usar uno nuevo.
- Acceso a la Pi: clave SSH temporal de 1 h (expiry-time en authorized_keys, comentario «claude-pisos-1h»); pedir una nueva a Jon.
