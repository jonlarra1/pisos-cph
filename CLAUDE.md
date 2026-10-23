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
  - `WORK` — Jon's workplace = Nordic Solar, Strandvejen 104B, Hellerup (entrada por el 3B). KEEP.
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
- Shared backend: `backend/` (Python stdlib + SQLite in Docker) runs on the Raspberry Pi at `/home/docker/pisos-backend`, bound to 127.0.0.1:8787, public via Tailscale Funnel `https://raspberrypi.tail9f1069.ts.net:8443`. Page reads `/api/estados` on load + every 15 s (candidatos every 60 s, both also when the tab gets focus) and POSTs `/api/estado`; offline → localStorage only. «Tiempo real»: every 30 s the page HEADs itself (ETag of GitHub Pages); on a new version it reloads alone if idle ≥20 s (max once per 2 min, keeps map view + selected flat via sessionStorage «vista»), otherwise shows a «🆕 Hay cambios nuevos» banner with «Actualizar». Connected to Lares via the shared folder `Vivienda/pisos-bridge` (see `lares/README.md`): Lares writes candidatos.json, backend serves `/api/candidatos` and `/api/zonas` (POST `/api/zona`) and writes config.json. Lares only: never read other Hermes agents/profiles. Only touch `/home/docker/pisos-backend` on the Pi; use targeted checks (grep).
- Monitoring & logs policy (keep for every new feature): backend logs JSON lines in /data/logs/<tipo>/AAAA-MM-DD.jsonl (past days gzipped; retention api/client 14 d, events/checks/summary 183 d; 20 MB/day cap; checks written only on OK<->fail change, last 10 per check in memory + checks_recientes.json; daily summary line); Docker stdout capped 2×5 MB; checks every 5 min (funnel_publico, web_github, lares_pasadas, lares_cron, buzon_compartido, api_errores, fotos); private panel http://100.72.170.60:9445 (Jon has MagicDNS off on his devices; tailscale serve --tcp, tailnet only, no identity header → accepted) or https://raspberrypi.tail9f1069.ts.net:9444 (needs MagicDNS; Tailscale-User-Login must start with larrabajojon@). Add a check + log events for anything new.
- Anuncios retirados (2026-10-23): Lares (`estado_anuncio`) comprueba ≤8 anuncios por pasada (pisos del mapa + propuestos que ya no
  salen en las páginas). Retirado SOLO con señal clara: Boligportal redirige con `?ad_rented_out_redirect=` («udlejet») o el anuncio
  trae `state` ≠ ACTIVE_*; Lejebolig 404/410. Error de red = desconocido (nunca retira). Escribe `pisos-bridge/retirados.json`; el
  backend (`aplicar_retirados`) lo pone UNA vez en «descartado» con by="lares" y `motivo` (si lo recuperáis, no se repite) y avisa
  por SSE. Web: aviso «Han retirado el anuncio…», 48 h en rojo y luego Descartados; propuestos retirados en «Temporales» 48 h.
- Photos: `fotos` (list, floor plan first when `fotoPlano`) per flat, shown through the backend image proxy `/api/img?u=` (allow-listed hosts, disk cache 300 MB).
- Right column «🔎 Búsqueda (Lares)» = search params stored on the Pi (`/api/filtros`, zones) → config.json → Lares. Any change to «Búsqueda» (filters, zones) MUST ask for confirmation with a pop-up whose first line is «⚠️ ESTE CAMBIO ES DE BÚSQUEDA, NO DE VISUALIZACIÓN» (it changes what Lares searches for everyone). «👁 Visualización» = map-only view settings (states shown, «Barrios y municipios» layer, colour by Estado/Coste/Barrios — «Tu prioridad» removed), per browser.
- UI settings per browser in localStorage: `ui` (day, colour mode, barrio filter, lines off, route option, open panels), `perfiles`, `tripTime`, `yo`, `destinos`, `cardPos`.
- Seguridad del backend (2026-10-21):
  - Contenedor pisos-api: usuario propio 10002, read_only, cap_drop ALL, no-new-privileges, pids 100, 128 MB; solo monta /data y /bridge
    (buzón con grupo 10002, 2775). Ya NO monta la carpeta cron de Lares: Lares copia su estado a pisos-bridge/lares_cron.json.
  - Red fija 172.31.250.0/24 (br-pisos). Cortafuegos `pisos-firewall.service` → /usr/local/sbin/pisos-firewall.sh (copia en backend/firewall/):
    cadenas PISOS-EGRESS (en DOCKER-USER) y PISOS-HOST (en INPUT) — el contenedor sale a internet pero no a LAN, Tailscale, otras redes Docker
    ni a la propia Pi. Comprobación «aislamiento_red» del monitor lo verifica cada 5 min.
  - Límite de peticiones con la IP real (último valor de X-Forwarded-For, lo añade tailscaled): escrituras 30/min por IP y 120/min global,
    lecturas 300/min por IP y 2000/min global, fotos 120/min por IP y 600 global; eventos «limite» en el log.
  - /api/img: solo https a image-lambda.boligportal.dk y s3…/lejeboligdata/images/, sin `..`, userinfo ni puertos raros.
  - Clave de escritura por persona (passkeys/Face ID): aplazado a propósito.
  - Ojo con ACL en la Pi: varias carpetas de Hermes tienen ACL con máscara restrictiva; `setfacl -m` recalcula la máscara y puede reactivar
    permisos de otros usuarios (pasó con lares/cron/jobs.json, revertido). Comprobar `getfacl` antes y después.
- Hosting: GitHub Pages (fixed link for flatmates). Commit + push to update.

## Tests (obligatorio antes de cada commit)
- `./tests/run.sh` (backend + datos, ~3 s, sin internet) antes de cada commit; `./tests/run.sh web` (Chromium, ~40 s) si se toca index.html.
  Por función: `./tests/run.sh -k propuestos`. Cada función nueva lleva su test (tests/test_backend.py, test_datos.py, test_web.py).
- Los tests de la web corren CON la CSP: no usar `page.wait_for_function` (usa eval); usar `self.esperar(expr)`.
- Commits pequeños + push, estilo midudev: `type(scope): summary` en inglés, sin Co-Authored-By.

## Estado (2026-10-22, 23:00) y pendientes
- Regla: depósito y prepago siempre por separado cuando el anuncio los separa (ficha, lista y candidatos); la suma solo como «total a la entrada».
- 22-oct noche (Jon fuera 2 h, modo autónomo): añadido 4641637 Teglholmens Østkaj 41 (Sydhavnen) con rutas (GTFS nuevo, 96 % de
  rutas iguales a las anteriores); la web se recarga sola al publicar (ETag de GitHub Pages cada 30 s); CSP + SRI en CDN + safeUrl()
  para enlaces de Lares; 76 tests (tests/); backend LOCAL (sin desplegar): SSE /api/eventos + ETag/304, check_site ampliado
  (pisos sin rutas, CSP, SRI del CDN, app.js), conexiones SQLite y ficheros cerrados. La web ya usa SSE si la Pi lo ofrece (si no, sondeo).
- Hecho y publicado: mapa con 15 pisos, rutas (bici/andando, 3 opciones, 5 días × 6 horas × límites 5–30 min), perfiles Jon/Unai,
  estados compartidos vía Pi, Lares v2 conectado (buzón pisos-bridge), menú «Búsqueda (Lares)» / «Visualización», galería de fotos,
  panel privado :9444, logs + checks. Listado izquierdo: principal = solo 🔵 gusta + 🟢 contactado; «🟠 Propuestos» = nuevos del
  mapa + candidatos de Lares juntos (una sola categoría); «🔴 Descartados» = temporales (48 h, con «recuperar») + definitivos.
- Pendiente:
  1. Fireworks (proveedor del modelo de Lares) suspendido → avisos de Lares al Bot Chat fallan (check `lares_cron` en rojo). Lo arregla Jon.
  2. Tarea IA de Lares que mire los planos y escriba `pisos-bridge/planos.json` (`{"<id>": {"dorm": 3}}`) — cuando Lares tenga modelo.
  3. Pasar al mapa los candidatos que se marquen 🔵 en «Propuestos» (geocodificar + rutas: tools/route.py para esos ids y fusionar
     en data/rutas.json, como se hizo con 5680524/5680537/5680562).
  5. Futuro: monitorizar con Grafana (Loki/Promtail leyendo data/logs, ya en JSON por líneas) cuando Jon lo monte en el homelab.
  6. Instalar en la Pi el piso_watch_monitor.py con depósito y prepago separados (campos `dep`/`pre`; local listo y probado
     el 2026-10-21; hace falta clave SSH temporal). Hasta entonces los candidatos de Lares no traen el desglose.
  7. Motor de rutas en vivo: /api/ruta hecho y probado en local (commit 0a1c981). Falta: desplegar en la Pi (mem_limit 512m,
     clave SSH), web (botón «Ahora», hora libre, 7 días, llegar/salir, rutas en TP para destinos añadidos), pruebas desde fuera.
  9. Desplegar en la Pi a la vez: Lares (dep/pre, historial.json), panel «Pisos» (contadores + gráfico), fix 502 de fotos,
     backend nuevo (SSE /api/eventos + ETag, check_site ampliado, vigilar_candidatos). Probado en Docker local con python:3.12-slim y las
     mismas restricciones (70 tests + smoke_test: 304, SSE, aviso de Lares, tope 20 conexiones, 27 MB, 26 procesos).
     Instalar: `backend/deploy.sh USUARIO@100.72.170.60` (solo mira) y luego `--aplicar` (tests, copia de seguridad, compose up --build,
     smoke_test contra el Funnel y vuelta atrás automática si falla). `--con-compose` sube también compose.yml (mem_limit 512m: preguntar a Jon).
     Después (fase 2): mover el <script> a app.js y quitar 'unsafe-inline' de script-src (check_site ya lo soporta).
  12. INSTALADO en la Pi el 23-oct (13:50–14:00): backend con retirados (motivo, retirado_aplicado) y Lares nuevo (copia previa:
     scripts/piso_watch_monitor.py.v2-20261023.bak). 1.ª pasada 12:20 UTC: 190 anuncios, 6/8 páginas, 5 candidatos (antes 0),
     8 comprobados, 1 retirado (1870169, ya descartado por Jon: se respetó). Copias del backend: /home/docker/pisos-backend/backup-*.
     Seguridad SSH (23-oct): borradas de authorized_keys token20m-ssh-token_2125 y antigravity-setup (copias .bak-20261023-*).
  13. EDC como 3.ª fuente de Lares: INSTALADO en la Pi el 23-oct 18:29 (copia previa: scripts/piso_watch_monitor.py.v3-20261023-preEDC.bak;
     ver lares/README.md). 1.ª pasada 16:31 UTC: 68 s, sin errores, 16 EDC + 289 Boligportal leídos, 12 fichas EDC, 14 candidatos
     (7 de EDC, 3 en Sydhavnen), 143 de EDC esperando ficha (se vacían en unas pasadas). Clave SSH temporal borrada
     (authorized_keys.bak-20261023-edc).
     Idea de Jon: tener el chat de Boligportal con cada casero dentro de la plataforma (Boligportal no tiene API y prohíbe iframes:
     X-Frame-Options SAMEORIGIN). Pendiente de decidir: extensión de Chrome / leer los avisos por email (Gmail) / navegador en la Pi.
  11. Chrome «acceso a red local»: en aparatos con MagicDNS el nombre de la Pi resuelve a 100.72.170.60 y Chrome pide permiso (headless lo deniega).
  10. Nordic Solar = Strandvejen 104B; Jon dice que se entra por el «3B»: preguntar de qué calle es y ajustar el destino.
  4. Lista del 14-oct: añadidos al mapa 5680590 (Frederiksberg, plano 4 dorm.) y 1912011 (Søborg, texto 3 dorm.) el 2026-10-22.
     1917762 = mismo piso que 5680524 (enlace añadido a su ficha). Sin confirmar dormitorios (Lejebolig no da fotos sin JS):
     1914015, 1914094, 1917800. Sin fecha: 5680600, 5390028, 5202436. Datos: scratchpad recup/estado.json.
  8. Hueco de Lares v2: HECHO EN LOCAL (sin desplegar): páginas adaptativas (mín. 2/3 como antes, sigue mientras haya anuncios
     nunca vistos, máx. 6 Lejebolig / 8 Boligportal) y los candidatos se recuerdan 7 días aunque bajen de página (aviso «no visto
     desde…»). tests/test_lares.py. Desplegar junto al punto 6 (mismo fichero, scripts/ de Lares en la Pi).
- Puertos ya usados en la Pi (no tocar): Portainer 8000/9000/9443. Nuestro: 127.0.0.1:8787 (API, Funnel :8443), 127.0.0.1:8790 (panel: serve https :9444 por nombre, y serve TCP :9445 por IP http://100.72.170.60:9445 para aparatos sin MagicDNS). Comprobar siempre con `ss -ltnp | grep :PUERTO` antes de usar uno nuevo.
- Acceso a la Pi: clave SSH temporal de 1 h (expiry-time en authorized_keys, comentario «claude-pisos-1h»); pedir una nueva a Jon.
