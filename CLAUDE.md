# Pisos Copenhague — project context for Claude Code

## What this is
A single-page Leaflet map (`index.html`) of rental flats (3 bedrooms, shared by 3 people) in Copenhagen and suburbs.
The user is Jon. He works at Nordic Solar (Hellerup). He prefers short, simple, direct answers (non-native English speaker; Spanish is fine too).

Este archivo describe lo que el código hace HOY. La historia (qué se hizo y cuándo) está en `docs/historial.md` y en git.
Regla de Jon (27-oct): documentación, contexto de Lares (`lares/instrucciones-pisos.md`) y comentarios del código cuentan UNA sola
idea, la actual. Al cambiar algo, se reescribe lo que ya no es cierto; no se añaden frases «antes… ahora…».

## Arquitectura
- Diseño al que vamos: `docs/arquitectura.md` (una base de datos, una puerta = la API v2, una entidad = el anuncio con estados,
  los datos no se borran, permisos por clave). **Leerlo antes de tocar datos, backend o Lares.**
- Estado real (auditoría del 27-oct; desde el 1-nov la base ya tiene TODO migrado: mapa, descartados, propuestos y estados): la
  base única (`anuncio`, API v2) existe y funciona, pero solo la usan del todo `pisos.py`
  (Lares en el chat) y la barra «añadir enlace» de la web. El resto sigue por el camino anterior, unido a la base por `backend/puente.py`:

| Pieza | Lee | Escribe |
|---|---|---|
| Web (`index.html`) | `PISOS`/`OUT` incrustados (copia de `data/`), `/api/v2/contactos`, `/api/estados`, `/api/candidatos`, `/api/destinos`, `/api/zonas`, `/api/filtros`, `/api/ruta`, SSE `/api/eventos` | `/api/estado`, `/api/v2/enlaces`, `/api/v2/favoritos`, `/api/destino`, `/api/zona`, `/api/filtros`, `/api/clientlog` |
| Vigilante (`lares/piso_watch_monitor.py`, cron cada 3 h, sin IA) | `pisos-bridge/config.json`, `pisos.json`/`descartados.json` de GitHub Pages, `GET /api/v2/anuncios` (los añadidos a mano), su SQLite `piso-watch/pisos.db` | `pisos-bridge/candidatos.json`, `rechazados.json`, `retirados.json`, `historial.json`, `lares_cron.json`, `lares_runs.jsonl` |
| Comprobador (dentro del vigilante: `comprobar_publicados`) | ≤8 anuncios por pasada (mapa + propuestos que no salen en su portal, también los que solo salen en la copia de EDC) | `retirados.json` → el backend (`aplicar_retirados`) los descarta una vez |
| Lares en el chat (`lares/pisos.py`) | `/api/v2/anuncios`, `/api/v2/resumen`, `chats_boligportal.json` (privado) | `/api/v2/enlaces`, `/estado`, `/nota`, `/dormitorios`, `/favoritos` (clave `CLAVE_LARES`) |
| Chats (`lares/chats_boligportal.py`, tarea `boligportal-chats` cada 30 min, lee cada 3–5 h al azar, nunca 00–08 h) | bandeja de Boligportal (`/api/messaging/`, cookie `.boligportal_cookie` 600), `GET /api/v2/anuncios` | `POST /api/v2/contactos`, `/api/v2/enlaces` + `/estado` contactado (≤3 nuevos por lectura), `chats_boligportal.json` (600, texto), `pisos-bridge/chats_estado.json` |
| Panel (`backend/admin.html`, :9445) | `/status` y `/flow` del backend: contadores de `pisos.json` + `candidatos.json` + tabla `estado` + `historial.json`; solo el check «anuncios» mira la base única | — |
| Backend (`backend/app.py`) | buzón `pisos-bridge`, GitHub Pages (`data/pisos.json` para las rutas previas) | SQLite: `estado`+`historial` (web), `anuncio`+`cambio`+`alias` (v2), `destino`, `zonas`, `ajustes` (filtros), `retirado_aplicado`; `config.json` |

- Sin usar todavía: `POST /api/v2/anuncios` (vigilante) y `POST /api/v2/anuncios/<uid>/comprobacion` (comprobador), y sus claves.
- Reglas duplicadas: el vigilante evalúa con su propia copia (`evaluar`, `zona_de`, `ZONA_ALIAS`, `detalle*`) de lo que hace el
  backend (`anuncios.evaluar`, `portales.py`). Se quita en el paso C del plan.
- El plan para cerrar esto (pasos A–E) está al final de `docs/arquitectura.md`.

## Files
- `index.html` — the whole app (HTML + CSS + JS, Leaflet 1.9.4 + leaflet-polylineoffset from CDN).
  Big inline data constants inside the `<script>` (copied from `data/` by `tools/sync_web.py`):
  - `PISOS` — the flats on the map (`data/pisos.json`). `OUT` — discarded flats with reason (`data/descartados.json`).
  - Basemap = OpenStreetMap standard tiles. Dark theme = CSS filter `--tiles`.
  - `RAIL` — real S-tog, metro (M1–M4), Letbane and Bx geometry + exact station coords (from github.com/Miqell24/copenhagen-bus-map, OSM + Rejseplanen). KEEP THIS.
  - `ST`, `LINES`, `MST`, `MLINES`, `LETB` — station lists per line; used for "nearest station" in each flat card. KEEP.
  - `WORK` — Jon's workplace = Nordic Solar, Strandvejen 104B, Hellerup (entrada por el 3B). KEEP.
  - `RUTAS` — precomputed public-transport routes (GN, Nordic Solar, DTU) per weekday × arrival time × walking limit, from `tools/prep.py` + `tools/route.py` (see README). Deduplicated: `routes`, `paths`, index `ix[mode][day][time][limit][flatId][dest]`, mode = foot (walk + any transit) | bike (bike 20 km/h + S-tog/regional/Lokaltog only, no bus/metro, no lock time).
  - `PERF` — profiles in «Configuración»: Jon (bike) and Unai (foot); mode, max minutes per leg (5–30), walk speed 4/5/6 km/h, bike 15/20/25 km/h; saved per browser. `adj()` rescales walk/bike legs to the chosen speed (transit times fixed; ⚠️ if arriving late). `ROWS` — route rows: Nordic Solar→Jon, GN→Unai, DTU→Jon and DTU→Unai. `DIRECTO` — bike-only / walk-only reference times (`tools/direct.py`).
  - `EXTRA` — shared destinations (Configuración → Destinos), stored on the Pi (`backend/destinos.py`; GET `/api/destinos`,
    POST `/api/destino` {accion: anadir|quitar}; quitar = hidden, never deleted; max 30; SSE «destinos», poll 120 s without SSE).
    Every destination gets rows for BOTH profiles; «Ocultar las rutas de X» in Viaje (per browser, `ui.soloYo`). localStorage
    "destinos" = offline copy (`pend` = not uploaded yet). Their routes come from the Pi's route engine (`/api/ruta`, queue `TPQ`
    of max 2 requests, current row first, retry after 30 s on 429/503); when a destination is added the Pi precomputes them
    (`backend/precalculo.py`) and the web shows «⏳ La Pi está preparando las rutas: X de N».
  - Routes not in `RUTAS` (other times, «Ahora», centro, destinos, propuestos) use the Pi engine, whose walk/bike legs are
    straight lines. Street paths live on the Pi (`backend/calles.py`, table `calle`, never deleted): legs already saved come
    inside `/api/ruta` as `calle`; the rest go to a queue that a thread fills from OSRM (FOSSGIS) at 1 request/s, and the web asks
    `GET /api/calle` (the Pi only asks OSRM for legs its engine gave, max 50 000 rows; unknown leg = 403 and Pi
    down → OSRM directly). Web `liveRoute`: max 2 requests at a time, 250 ms apart, focused route first,
    previous flat's queue dropped; failure → stays «…» and retries after 10, 20, 30 s, then null and a new try after 5 min;
    «no route» stays straight. Only the focused route animates.
  - Station dots/labels hide when all lines through them are switched off (`lineOn`, `updateStations`).
  - `HORARIOS` — saved schedule presets shown as "Mis horarios".
- Flat status (shared on the Pi, table `estado`; `/api/estados` on load, every 15 s without SSE and on focus; POST `/api/estado`;
  offline → localStorage "estados"): gusta (blue), nuevo (orange, shown as «Propuestos»), contactado (green), descartado (red;
  DESCARTE_H=48 h on the map with a countdown, then listed in «Descartados»; never deleted). `st` in `PISOS` is only the starting value.
- `data/pisos.json` — flat data (id, zona, barrio, calle, m2, vaer, dorm, alq, aconto, dep, pre, disp, link, lat, lon, ubic = location precision, estado/aviso = warnings, fotos, fotoPlano).
- `backend/` — `app.py` (server, checks, logs, panel), `api_v2.py` (API v2), `anuncios.py` (model, rules, states, history),
  `portales.py` (reads Boligportal/EDC/Lejebolig/Juli Living listing pages), `puente.py` (v2 ↔ the web's `estado`/`candidatos`), `destinos.py`, `contactos.py`,
  `precalculo.py`, `routing.py` (route engine), `calles.py` (street paths), `admin.html` (panel), `deploy.sh`, `smoke_test.py`, `firewall/`.
- `lares/` — copies of what runs in Lares' profile on the Pi: `piso_watch_monitor.py` (vigilante), `pisos.py` (chat tool), `chats_boligportal.py` (chats),
  `instrucciones-pisos.md` (goes into Lares' SOUL.md and skill `piso-watch-cph`). See `lares/README.md`.
- `.claude/skills/boligportal-mensajes/` — skill para leer (solo leer) la bandeja de Boligportal de la cuenta compartida con
  Claude in Chrome y cruzar los contactados con la base. Sin contraseñas ni cookies fuera del navegador.
- `tools/` — `sync_web.py` (data → index.html), route builders (`prep.py`, `route.py`, `walkpaths.py`, `fixlate.py`, `direct.py`),
  `barrios.py`, `dora.py` (metrics).

## Rules
- Cambios completos (regla de Jon, 27-oct): un cambio está hecho cuando, en el mismo bloque: (1) está desplegado, (2) la
  documentación dice exactamente lo que hace el código, (3) Lares lo sabe (`lares/instrucciones-pisos.md` → su SOUL.md y su skill
  en la Pi) y (4) lo sustituido se retira: el CÓDIGO viejo se borra (git guarda la historia); los DATOS no se borran nunca (se
  archivan o se marcan). Si falta algo, decirlo como pendiente.
- Forma de trabajar: contar cada paso y cada test en una línea, y actuar como asistente (preguntar antes de cambios grandes o en la Pi), salvo que Jon pida lo contrario.
- Layout: left rail = flat list, or the selected flat's ficha (desktop); right column = Viaje, Ver y filtrar, Líneas, Configuración. Mobile: floating card under the map.
  Bottom bar = «añadir un piso por su enlace» (`POST /api/v2/enlaces`; Boligportal, EDC, Lejebolig, Juli Living; always lands as «propuesto», with «no pasa: …» warnings).
- Keep the UI, barrio grouping, colours, light/dark theme and Spanish UI text. List sorted by monthly cost, with Vanløse (Jon's favourite barrio, `esFav`) always first: ⭐ in list/ficha/popup and a gold pin, also in «Propuestos»; no barrio circles on the map.
- Keep OSM attribution visible: "© OpenStreetMap contributors".
- Respect OSM tile and Nominatim usage policies (light personal use, real User-Agent, max 1 request/second for Nominatim).
- Do not invent addresses or coordinates. If an exact address is not found, keep the old point and say so in `ubic`.
- Depósito y prepago siempre por separado cuando el anuncio los separa (ficha, lista y propuestos); la suma solo como «total a la entrada».
- Shared backend: `backend/` (Python stdlib + SQLite in Docker) on the Raspberry Pi at `/home/docker/pisos-backend`, bound to
  127.0.0.1:8787, public via Tailscale Funnel `https://raspberrypi.tail9f1069.ts.net:8443`. The page uses SSE `/api/eventos` when
  available (else polling: estados 15 s, candidatos 60 s, destinos 120 s). «Tiempo real»: every 30 s the page HEADs itself (ETag of
  GitHub Pages); on a new version it reloads alone if idle ≥20 s (max once per 2 min, keeps map view + selected flat via
  sessionStorage «vista»), otherwise shows «🆕 Hay cambios nuevos». Lares: only its profile `lares` and the folder
  `Vivienda/pisos-bridge`; never read other Hermes agents/profiles. On the Pi only touch `/home/docker/pisos-backend`; use targeted checks (grep).
- Monitoring & logs policy (keep for every new feature): backend logs JSON lines in /data/logs/<tipo>/AAAA-MM-DD.jsonl (past days gzipped; retention api/client 14 d, events/checks/summary 183 d; 20 MB/day cap; checks written only on OK<->fail change, last 10 per check in memory + checks_recientes.json; daily summary line); Docker stdout capped 2×5 MB; checks every 5 min (funnel_publico, web_github, lares_pasadas, lares_cron, buzon_compartido, api_errores, fotos, almacenamiento, aislamiento_red, motor_rutas, calles, anuncios, destinos, chats). Check «calles»: trazados guardados, cola y respuestas de OSRM; falla si OSRM lleva >6 h sin responder; eventos src=calles al caerse y al volver. Check «chats»: última lectura de la bandeja (`pisos-bridge/chats_estado.json`: sesión ok|caducada|sin_cookie, conversaciones, error), contactos por marca y ❤️; falla si la sesión caducó o hubo error; eventos src=chats y src=favoritos. Check «destinos»: destinos visibles/quitados, rutas previas (en cola, calculadas, con error, sin sitio) y búsquedas rechazadas por motor ocupado (también evento warn src=rutas, máx. 1/min); eventos src=destinos y src=rutas_previas. Check «anuncios» (API v2): anuncios por estado, cola de fichas, errores del lector, enlaces añadidos/fallidos, entregas del vigilante y acciones no permitidas (403, también evento warn src=permisos). Private panel http://100.72.170.60:9445 (tailscale serve --tcp, tailnet only) or https://raspberrypi.tail9f1069.ts.net:9444 (needs MagicDNS; Tailscale-User-Login must start with larrabajojon@). Add a check + log events for anything new.
- Anuncios retirados: el comprobador del vigilante (`estado_anuncio`) mira ≤8 anuncios por pasada (`MAX_ACTIVO`: pisos del mapa +
  propuestos que ya no salen en las páginas, los menos mirados primero). Retirado SOLO con señal clara: Boligportal redirige con
  `?ad_rented_out_redirect=` o trae `state` ≠ ACTIVE_*; Lejebolig 404/410. Al leer una ficha de Boligportal, esa misma
  redirección marca el anuncio como «alquilado» (rechazado). Error de red = desconocido (nunca retira). Escribe
  `pisos-bridge/retirados.json`; el backend (`aplicar_retirados`) lo pone UNA vez en «descartado» con by="lares" y `motivo` (si lo
  recuperáis, no se repite) y avisa por SSE. Web: aviso «Han retirado el anuncio…», 48 h en rojo y luego Descartados.
- Vanløse = barrio favorito (decisión de Jon, 7-nov; contactos de Tagkronen en Juli Living): web ⭐ + chincheta dorada + primero;
  el vigilante avisa primero «NUEVO | ⭐ VANLØSE | …» y en Vanløse propone lo que falla un filtro con «no pasa: …» (`evaluar_fav`;
  solo rechaza alquilado, habitación, <4 værelser o <3 dormitorios). Juli Living (juliliving.dk) es fuente del vigilante SOLO en
  Vanløse (su JSON `jlet_units`, una petición por pasada; reservada o fuera de su lista = retirada); sus enlaces valen en todas partes.
- Enlace de Boligportal = `https://www.boligportal.dk/lejligheder/bolig/lejlighed-id-<n>` (`portales.enlace_canonico`; redirige
  al enlace largo del anuncio). `/en/rental-apartments/<n>` y `/lejligheder/id-<n>` abren hoy una página de BÚSQUEDA: backend
  (`enlace_bp`) y vigilante (`url_ok`) los reescriben al leer, y al arrancar el backend (`anuncios.arreglar_enlaces`) arregla los
  de la base (guarda el original en `link_viejo`).
- Lejebolig: Lares NO lo busca (decisión de Jon, 27-oct). Sus enlaces solo entran a mano (chat o barra de la web).
- Compartir (decisión de Jon, 1-nov): si en Boligportal el casero no marca «Delevenlig» (`features.shareable` = false), el
  anuncio se RECHAZA («no permite compartir (dato del portal)»), aunque sea el motivo nº 1 de rechazo (84 de 150 anuncios de 4 vær.).
- Chats de Boligportal (decisión de Jon, 8-nov: «Solo leer, con Lares», y a horas pseudoaleatorias para no parecer un
  robot): `lares/chats_boligportal.py` lee la bandeja de la cuenta compartida cada 3–5 h al azar (nunca 00–08 h), ≤3
  páginas con pausas, y se para ante 401/403/429. Marca por anuncio (tabla `contacto`, `backend/contactos.py`):
  respondido 💬 | sin_respuesta ⏳ | visita 📅 | alquilado 🏁. El TEXTO de los mensajes nunca va a la API pública:
  Lares abre ≤5 conversaciones con mensajes nuevos por lectura (`threads/<n>?mark_read=0`, no las marca como leídas) y
  las guarda en la tabla `mensaje` (POST /api/v2/mensajes, solo Lares; no se borra); `GET /api/v2/mensajes?pid=` solo
  responde con la cabecera `X-Pisos-Lectura` = `CLAVE_LECTURA_JON` o `CLAVE_LECTURA_UNAI` (decisión de Jon, 8-nov;
  clave mala → evento warn src=permisos y 429 con >5/min por IP). La web la pide una vez en Configuración («Leer los
  chats», localStorage `claveLectura`) y enseña la conversación en la ficha, debajo de las fotos. El último mensaje
  también queda en el archivo privado del perfil de Lares (600) para `pisos.py chats`. La cookie solo en `.boligportal_cookie` (600) del perfil de Lares; la
  pegan Jon o Unai, Claude no la ve. Conversación de un anuncio que no está en la base → se añade y va a 🟢 contactado;
  los demás estados no se tocan. Nunca escribir a caseros ni archivar. Web: marca en chincheta/lista/ficha, «💬 Abrir
  conversación» (`/indbakke/t/<hilo>`). ❤️ «nos encanta» (tabla `favorito`, `POST /api/v2/favoritos`, web y Lares),
  aparte del estado: ❤️ primero en la lista (luego ⭐ Vanløse), filtro «Solo ❤️» en Visualización; quitarlo no borra.
- Los valores de «Búsqueda» viven en la Pi (`/api/filtros`), no en el código: el 1-nov Jon puso la entrada del 2026-12-01 al
  2027-01-04 (antes 18-dic..15-ene). `DEFAULT_FILTROS` del vigilante solo se usa si no hay config.json.
- Photos: `fotos` (list, floor plan first when `fotoPlano`) per flat, shown through the backend image proxy `/api/img?u=` (allow-listed hosts, disk cache 300 MB).
- Right column «🔎 Búsqueda (Lares)» = search params stored on the Pi (`/api/filtros`, zones) → config.json → Lares. Any change to «Búsqueda» (filters, zones) MUST ask for confirmation with a pop-up whose first line is «⚠️ ESTE CAMBIO ES DE BÚSQUEDA, NO DE VISUALIZACIÓN» (it changes what Lares searches for everyone). «👁 Visualización» = map-only view settings (states shown, «Barrios y municipios» layer, colour by Estado/Coste/Barrios), per browser.
- UI settings per browser in localStorage: `ui` (day, colour mode, barrio filter, lines off, route option, open panels, soloYo, soloCor), `perfiles`, `tripTime`, `yo`, `destinos`, `cardPos`, `claveLectura`.
- Seguridad del backend:
  - Contenedor pisos-api: usuario propio 10002, read_only, cap_drop ALL, no-new-privileges, pids 100, 128 MB; solo monta /data y /bridge
    (buzón con grupo 10002, 2775). No monta la carpeta cron de Lares: Lares copia su estado a pisos-bridge/lares_cron.json.
  - Red fija 172.31.250.0/24 (br-pisos). Cortafuegos `pisos-firewall.service` → /usr/local/sbin/pisos-firewall.sh (copia en backend/firewall/):
    cadenas PISOS-EGRESS (en DOCKER-USER) y PISOS-HOST (en INPUT) — el contenedor sale a internet pero no a LAN, Tailscale, otras redes Docker
    ni a la propia Pi. El check «aislamiento_red» lo verifica cada 5 min.
  - Límite de peticiones con la IP real (último valor de X-Forwarded-For, lo añade tailscaled): escrituras 30/min por IP y 120/min global,
    lecturas 300/min por IP y 2000/min global, fotos 120/min por IP y 600 global, `/api/ruta` 240/min por IP y 600 global, `/api/calle` 120/min por IP y 400 global (el motor lo
    protege además `FRENO`: máx. 6 búsquedas esperando); eventos «limite» en el log.
  - API v2: clave por cliente (web = `API_KEY` pública + Origin, `quien` solo jon/unai/web; `CLAVE_LARES`, `CLAVE_VIGILANTE`,
    `CLAVE_COMPROBADOR` solo en el .env de la Pi). Sin operación de borrar. Claves de lectura de los mensajes:
    `CLAVE_LECTURA_JON` y `CLAVE_LECTURA_UNAI` (≥16 caracteres, solo en el .env; si faltan, la web no enseña textos).
  - /api/img: solo https a image-lambda.boligportal.dk, s3…/lejeboligdata/images/, billeder.edc.dk/edcmedia/ y nrep.estatetool.net/media/bolig/ (Juli Living), sin `..`, userinfo ni puertos raros.
  - Clave de escritura por persona (passkeys/Face ID): aplazado a propósito.
  - Ojo con ACL en la Pi: varias carpetas de Hermes tienen ACL con máscara restrictiva; `setfacl -m` recalcula la máscara y puede reactivar
    permisos de otros usuarios. Comprobar `getfacl` antes y después.
- Hosting: GitHub Pages (fixed link for flatmates). Commit + push to update.

## Métricas DORA (regla de Jon, 26-oct)
- Medir siempre con `python3 tools/dora.py` (usa el python de `.venv` para la parte de calidad): frecuencia de despliegue, cambio → producción,
  tasa de fallos, tasa de retrabajo, recuperación y commits sin desplegar, por pieza (web, backend, lares) + calidad (líneas, duplicado,
  complejidad, bandit, avisos de estilo). Fuentes: git, GitHub Pages (`gh api`) y `docs/despliegues.jsonl`.
- `backend/deploy.sh` apunta cada instalación (ok / vuelta_atras). Lares se instala a mano: después, `python3 tools/dora.py --registrar lares ok "nota"`.
- Al cerrar cada bloque: `python3 tools/dora.py --guardar` (añade la foto a `docs/metricas.jsonl`) y contar a Jon las cifras y qué mejorar.

## Tests (obligatorio antes de cada commit)
- `./tests/run.sh` (backend, datos, Lares, calidad, dora, precálculo; sin internet) antes de cada commit; `./tests/run.sh web`
  (Chromium) si se toca index.html. Por función: `./tests/run.sh -k propuestos`. Cada función nueva lleva su test.
- Los tests de la web corren CON la CSP: no usar `page.wait_for_function`; usar `self.esperar(expr)`.
- `tests/test_calidad.py`: seguridad (sin eval/exec/shell=True, SQL siempre con `?`, sin assert, sin claves en el código, sin borrado
  en la API, bandit sin hallazgos altos), legibilidad (ruff limpio; funciones ≤120 líneas) y eficiencia (complejidad ≤40,
  lecturas de red con tope). ruff/bandit/radon solo en `.venv` local. Deuda conocida: `evaluar` y `do_POST` — no añadir más.
  Estilo: `ruff format` + `ruff check` (ruff.toml, 120 columnas) en backend y Lares, 0 avisos. El Dockerfile y `deploy.sh` deben
  listar todos los módulos que importa `app.py` (hay test).
- Commits pequeños + push, estilo midudev: `type(scope): summary` en inglés, sin Co-Authored-By.

## Pendientes (7-nov)
1. Plan de `docs/arquitectura.md` en DOS instalaciones (decisión de Jon, 28-oct):
   - 1.ª — INSTALADA el 1-nov 19:30 (backup-20261101-192924, base copiada en `data/copias/`):
     · Re-evaluación al cambiar filtros/zonas (`anuncios.decidir` + `reevaluar`). Regla de Jon: aflojar un filtro recupera los
       rechazados de ≤14 días; endurecerlo NO quita propuestos, solo les pone «no pasa: …»; gusta/contactado/descartado no se tocan.
       OJO: solo actúa sobre la base única; los propuestos del vigilante siguen llegando por `candidatos.json` hasta la 2.ª.
     · Migración aplicada (`migrar.py --aplicar`): 106 anuncios en la base (15 del mapa con `datos.mapa`/`mapa_id`, 67 descartados
       fijos, 9 propuestos del vigilante, 5 estados sin datos con su enlace, 38 líneas del historial de la web). Comprobado: los 25
       estados de la web = los de la base; la web sigue viendo lo mismo (22 propuestos).
     · Puente ajustado a lo migrado; `deploy.sh` copia la base en caliente; check `lares_cron` ya no cuenta tareas en pausa.
     · Lares: `SOUL.md` = `instrucciones-pisos.md` (los dos topes). Vigilante arreglado e instalado: un anuncio alquilado se
       rechaza como «alquilado» (antes leía la fecha de otro piso), «Ubegrænset Lejeperiode» = ilimitado (antes «4 meses» del
       «4 Værelser»), y lo que solo sale en la copia de EDC se comprueba. Se borraron de su caché las 44 fichas de propuestos de
       Boligportal y una pasada única las releyó: 41 propuestos falsos (alquilados, «libre el 2026-12-01») salieron.
   - 2.ª: vigilante y comprobador por la API (claves `CLAVE_VIGILANTE` y `CLAVE_COMPROBADOR`, dos, decisión de Jon), web desde
     `/api/v2`, panel desde la base, `/api/filtros` y zonas a la v2, borrar `puente.py`, `/api/candidatos`, `/api/estado(s)`,
     `migrar.py` y los JSON del buzón. Al acabar, reescribir la tabla «Estado real» de arriba.
   - Diagnóstico del 1-nov «no salen pisos nuevos»: el vigilante funciona. Lo nuevo cae sobre todo por zona excluida, «Delevenlig»
     sin marcar (regla de Jon: se rechaza) y fecha. Lares no vuelve a mirar lo rechazado que ya no sale en sus 3 primeras páginas:
     tras ampliar la ventana (1-dic..4-ene), Jon añadió a mano los 3 que pasaban (Boligportal 5456969 Herlev, 5679026 y 5246868
     Sydhavnen). Hoy (19:30) Lares por el chat añadió 5363124 y el backend le rechazó 8 enlaces alquilados: el chat funciona.
   - 7-nov: la Pi estuvo sin internet el 5-nov 10:30–15:00 (el wifi `scandi` no daba IP) y el 6-nov 07:00–18:00 (con IP pero
     «no route to host»: la red del edificio sin salida). No se reinició (sin bajadas de tensión; sí tocó el límite de
     temperatura alguna vez). Sin internet, el vigilante publicó 38 propuestos viejos: eran EDC copias alquiladas cuya ficha
     («alquilado») no contaba como leída, así que se reabrían las mismas 12 cada pasada y 29 se quedaban pendientes con datos del
     1-nov. Arreglado e instalado (7-nov): la ficha alquilada cuenta como leída y una pasada sin ningún portal no toca el buzón.
   - 7-nov (tarde): el Funnel dejó de responder 08:33–~17:00 UTC (saludo TLS cortado, como el 25-oct); el tailnet y el panel sí
     iban. Instalado el barrido diario: una vez al día (o al cambiar filtros/zonas) el vigilante recorre Boligportal entero
     (`-værelser/` de 4 a 6, ~25 páginas, hasta 30 fichas; si falla, repite en la siguiente pasada) y lo que ya está en la base
     no se avisa como «NUEVO». Lares lo sabe (SOUL.md y skill). Jon añadió a mano 12 de 13 pisos antiguos que el vigilante no
     había visto (5522327 estaba reservado). Comprobado en directo el 8-nov: 5670349, 5587412, 5668362 y 5638204 tienen contrato
     indefinido (`rental_period` 0) y fecha dentro de la ventana o sin fecha; no hay ninguno «de 4 meses».
2. `pisos.py consultar`: el historial enseña la hora en segundos y «propuesto → propuesto» en las notas.
3. Avisos de Lares al Bot Chat: la última entrega fallida es del 26-oct (piso-check-links, ya en pausa). El chat de Lares sí
   usa `pisos.py` (1-nov). Comprobar en el próximo aviso de piso nuevo que llega al Bot Chat.
4. Tarea IA de Lares que mire los planos y escriba `pisos-bridge/planos.json` (`{"<id>": {"dorm": 3}}`) — cuando Lares tenga modelo.
5. Pasar al mapa los propuestos que se marquen 🔵 (geocodificar + `tools/route.py` para esos ids y fusionar en `data/rutas.json`).
   Propuestos sin coordenadas no tienen ficha (geocodificar con Nominatim, 1 pet./s).
6. Mover el `<script>` a app.js y quitar 'unsafe-inline' de script-src (check_site ya lo soporta). Partir `app.py`. ResourceWarnings en los tests.
7. Vigilante del Funnel (watchdog): pendiente del OK de Jon. La red de la Pi (wlan0 DTU preferida + eth0 a este PC) es de Jon: no tocar sin su decisión.
8. Nordic Solar = Strandvejen 104B; Jon dice que se entra por el «3B»: preguntar de qué calle es y ajustar el destino.
9. Vanløse / Juli Living (7-nov): instalado. Comprobar en la próxima pasada que `lares_runs.jsonl` trae `leidos.Juli` y sin
   error «juli: …», y en el primer aviso de Vanløse que llega al Bot Chat como «⭐ VANLØSE». Jon: apuntarse al Boligagent y a la
   Interesseliste de Tagkronen (juliliving.dk/tagkronen/) y escribir a la agente de Juli (borrador en el chat del 7-nov).
10. Ideas sin decidir: chat de Boligportal dentro de la web (no hay API, prohíbe iframes); Chrome en la Pi (noVNC, solo tailnet) —
   ojo: el kernel de la Pi ignora mem_limit de Docker. Grafana (Loki leyendo data/logs) cuando Jon lo monte.

## Copias de seguridad y acceso
- Regla de Jon: guardar solo la vuelta atrás del último cambio de cada pieza y borrar las anteriores cuando lleve ~1 día bien;
  apuntar aquí lo que queda. Nunca dejar copias de authorized_keys. No tocar copias de otros agentes de Hermes.
- 1-nov: BORRADAS backend backup-20261027-075658; Lares .v7-20261027-preLejebolig.bak, SOUL.md.bak-20261027, SKILL.md.bak-20261027.
  7-nov: BORRADAS backend backup-20261101-192924 (código, está en git); Lares .v8-20261101-preAlquilado.bak,
  .v9-20261107-preSinConexion.bak y SOUL.md.bak-20261101. QUEDAN en la Pi: `data/copias/pisos-backup-20261101-192924.db` (base
  antes de migrar; `deploy.sh` deja las 3 últimas) y Lares `scripts/piso_watch_monitor.py.v10-20261107-preBarrido.bak`,
  `SOUL.md.bak-20261107`, `skills/productivity/piso-watch-cph/SKILL.md.bak-20261107` → borrar ~9-nov si va bien.
  7-nov (noche, Juli Living + Vanløse, y enlaces de Boligportal): backend `backup-20261107-225853`, `-230904` y `-231325`
  (la de antes del arreglo de enlaces es `-230904`); Lares `scripts/piso_watch_monitor.py.v12-20261107-preEnlaces.bak`, `scripts/piso_watch_monitor.py.v11-20261107-preJuli.bak`,
  `scripts/pisos.py.bak-20261107-preJuli`, `SOUL.md.bak-20261107-preJuli`, `SKILL.md.bak-20261107-preJuli` → borrar ~9-nov si va
  bien (y entonces las de «preBarrido» sobran). Clave SSH quitada al acabar (sin copia).
  8-nov (chats de Boligportal, ❤️ y limpieza del código): backend `backup-20261108-083756` y `-085855`; Lares
  `scripts/pisos.py.bak-20261108-preChats`, `.bak-20261108-preArreglos`, `scripts/chats_boligportal.py.bak-20261108-preArreglos`,
  `SOUL.md.bak-20261108-preChats`, `SKILL.md.bak-20261108-preChats` → borrar ~10-nov si va bien. En git: etiqueta
  `backup-pre-limpieza-20261108` (antes de la limpieza).
  8-nov (conversaciones en la ficha y panel por módulos): backend `backup-20261108-100010` y `-100309`, `.env.bak-20261108-preLectura`
  (el .env antes de las claves de lectura: borrarlo cuando estas funcionen); Lares `*.bak-20261108-preMensajes` (chats_boligportal.py,
  SOUL.md, SKILL.md) y `scripts/piso_watch_monitor.py.v13-20261108-preMensajes.bak` → borrar ~10-nov si va bien.
  8-nov (Lares solo avisa, sin soluciones ni comandos): Lares `SOUL.md.bak-20261108-preSoloAvisar` → borrar ~10-nov si va bien.
  En el PC: ~/.claude/settings.json.bak, .bak-narrator, .bak-20261024-voz (narrador, 1–2 KB).
- Puertos ya usados en la Pi (no tocar): Portainer 8000/9000/9443. Nuestro: 127.0.0.1:8787 (API, Funnel :8443), 127.0.0.1:8790 (panel: serve https :9444 por nombre, y serve TCP :9445 por IP). Comprobar siempre con `ss -ltnp | grep :PUERTO` antes de usar uno nuevo.
- Acceso a la Pi: clave SSH temporal de 1 h (expiry-time en authorized_keys, comentario «claude-pisos-1h»); pedir una nueva a Jon.
- Chrome «acceso a red local»: con MagicDNS el nombre de la Pi resuelve a 100.72.170.60 y Chrome pide permiso (headless lo deniega).
