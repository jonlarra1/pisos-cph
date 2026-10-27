# Historial de pisos-cph (diario hasta el 27-oct-2026)

Solo historia: lo que se hizo y cuándo. Lo que el sistema hace HOY está en CLAUDE.md y docs/arquitectura.md.
Las frases de aquí pueden describir cosas que ya no existen.

## Estado (2026-10-22, 23:00) y pendientes
- 26-oct 21:04 INSTALADO en la Pi: backend con API v2 (fase 1; sin claves nuevas en .env: solo acepta la de la web) y Lares v5
  (main partida en pasos; pasada de prueba 41 s, 22 candidatos, 0 errores). 1.er intento del backend → vuelta atrás automática
  porque el Funnel estaba caído (no por el código); 2.º intento con la prueba por túnel SSH (`PISOS_URL=http://127.0.0.1:18788`).
  INCIDENTE CERRADO 26-oct 21:15 (≈22 h): `sudo systemctl restart tailscaled` (entrando por el cable, 192.168.1.110: la Pi
  cuelga de este PC por eth0) y el Funnel volvió. tailscaled llevaba sin reiniciar desde el 14-sep. Funnel caído desde el 25-oct 23:27 UTC (la web no llega a la Pi). La Pi tiene dos salidas: wlan0
  (10.20.15.232, red de DTU, métrica 50 = preferida) y eth0 (192.168.1.110, métrica 100); la WiFi se cae y vuelve («major link
  change», DERP Amsterdam inalcanzable) y el saludo TLS del Funnel se corta. Es red de Jon: NO tocar sin su decisión.
- 26-oct: fase 1 de docs/arquitectura.md HECHA EN LOCAL (sin desplegar): backend/portales.py (leer fichas de Lejebolig/Boligportal/EDC),
  anuncios.py (modelo, estados, permisos, historial), api_v2.py (/api/v2/…, claves por cliente: API_KEY web, CLAVE_LARES,
  CLAVE_VIGILANTE, CLAVE_COMPROBADOR). Dockerfile ya copia los módulos nuevos. Limpieza: quitado LARES_CRON (sin uso desde el 21-oct);
  los scripts de tools/ siguen en uso. Siguiente: desplegar (clave SSH 1 h + crear las claves nuevas en el .env de la Pi) y fase 2 (migración).
- Regla: depósito y prepago siempre por separado cuando el anuncio los separa (ficha, lista y candidatos); la suma solo como «total a la entrada».
- 22-oct noche (Jon fuera 2 h, modo autónomo): añadido 4641637 Teglholmens Østkaj 41 (Sydhavnen) con rutas (GTFS nuevo, 96 % de
  rutas iguales a las anteriores); la web se recarga sola al publicar (ETag de GitHub Pages cada 30 s); CSP + SRI en CDN + safeUrl()
  para enlaces de Lares; 76 tests (tests/); backend LOCAL (sin desplegar): SSE /api/eventos + ETag/304, check_site ampliado
  (pisos sin rutas, CSP, SRI del CDN, app.js), conexiones SQLite y ficheros cerrados. La web ya usa SSE si la Pi lo ofrece (si no, sondeo).
- Hecho y publicado: mapa con 15 pisos, rutas (bici/andando, 3 opciones, 5 días × 6 horas × límites 5–30 min), perfiles Jon/Unai,
  estados compartidos vía Pi, Lares v2 conectado (buzón pisos-bridge), menú «Búsqueda (Lares)» / «Visualización», galería de fotos,
  panel privado :9444, logs + checks. Listado izquierdo: principal = solo 🔵 gusta + 🟢 contactado; «🟠 Propuestos» = nuevos del
  mapa + candidatos de Lares juntos (una sola categoría); «🔴 Descartados» = temporales (48 h, con «recuperar») + definitivos.
- 27-oct 08:00 INSTALADO TODO: backend (destinos compartidos, freno + rutas previas, check «destinos», logs; backup-20261027-075658),
  Lares (sin Lejebolig; pasada de prueba 34 s, Boligportal 158 + EDC 11, 0 errores; SOUL.md y skill piso-watch-cph reescritos con
  pisos.py y la base única — copias SOUL.md.bak-20261027, SKILL.md.bak-20261027, script .v7-20261027-preLejebolig.bak), web publicada,
  migración Lejebolig aplicada (Boligportal 5170785, 5681028, 5484666 con nota; 5 de Lejebolig con «solo en Lejebolig»).
  Mapa: Gyngemose 102 m² → Boligportal 5193904 (punto sin mover, ~250 m: recalcular rutas si se quiere el punto exacto).
  Avisos «solo en Lejebolig»: Vasekær, Rosendal, Gyngemose 6. th y 4. th. Retirados (los descarta el comprobador): Mortonsvej,
  Molestien, Glostrup 104 m².
  27-oct 08:20 BLOQUE DE LARES CERRADO: CSV + listas .md evaluados con los filtros de la web (46 de Boligportal: solo pasan 5484666, ya
  metido, y 5390028 Dantes Plads, importado con nota; Lejebolig y caídos no se importan). Archivado (sin borrar) en
  `Vivienda/archivo-2026-10-26/` (+ LEEME.txt): pisos_cph.csv, pisos_validos/no_validos.md, nuevos_desde_14oct.md, pisos.db vacío y los
  scripts viejos. `piso-check-links` PAUSADA con `hermes -p lares cron pause 9a4dc0bb9868` (se reanuda con `resume`); ACL de jobs.json
  igual. `pisos.py consultar` probado dentro de hermes-gateway (ve los 9 propuestos).
  Pendiente: probar por el CHAT de Lares que el hook (skill-governance-hook, fail_closed) deja usar pisos.py — necesita su modelo (Fireworks);
  pisos.py: el historial enseña la hora en segundos y «propuesto → propuesto» en las notas (arreglar formato).
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
  14. 24-oct (instalado en la Pi y publicado): motor de rutas con opción directa en bici/andando (es la principal si cabe en
     el máximo por tramo; sobran las de transporte más lentas); tramos rectos del motor dibujados por calle con OSRM (solo la ruta
     elegida); ficha completa para los propuestos de Lares con coordenadas (botón «📋 Ver ficha y rutas», rutas con el motor,
     fotos por el proxy, estado desde la ficha); Lares saca amueblado, «se puede compartir», ascensor, balcón, mascotas, lavadora,
     parking (Boligportal: features; EDC: Altan/Elevator + descripción; amueblado también del texto) y relee fichas viejas de la
     caché; filtro «Amueblado» (da igual / sin amueblar / amueblado) en Búsqueda; proxy de fotos acepta billeder.edc.dk/edcmedia/;
     favicon.svg + apple-touch-icon.png.
     Pendiente: propuestos SIN coordenadas (casi todos los de Lejebolig) no tienen ficha (geocodificar con Nominatim, 1 pet./s);
     Chrome en la Pi (noVNC, solo tailnet) para los chats de Boligportal y fotos de Lejebolig con sesión — aprobado por Jon, sin
     empezar. Ojo: el kernel de la Pi ignora mem_limit de Docker («does not support memory limit»): revisar antes de meter Chromium.
  11. Chrome «acceso a red local»: en aparatos con MagicDNS el nombre de la Pi resuelve a 100.72.170.60 y Chrome pide permiso (headless lo deniega).
  10. Nordic Solar = Strandvejen 104B; Jon dice que se entra por el «3B»: preguntar de qué calle es y ajustar el destino.
  4. Lista del 14-oct: añadidos al mapa 5680590 (Frederiksberg, plano 4 dorm.) y 1912011 (Søborg, texto 3 dorm.) el 2026-10-22.
     1917762 = mismo piso que 5680524 (enlace añadido a su ficha). Sin confirmar dormitorios (Lejebolig no da fotos sin JS):
     1914015, 1914094, 1917800. Sin fecha: 5680600, 5390028, 5202436. Datos: scratchpad recup/estado.json.
  8. Hueco de Lares v2: HECHO EN LOCAL (sin desplegar): páginas adaptativas (mín. 2/3 como antes, sigue mientras haya anuncios
     nunca vistos, máx. 6 Lejebolig / 8 Boligportal) y los candidatos se recuerdan 7 días aunque bajen de página (aviso «no visto
     desde…»). tests/test_lares.py. Desplegar junto al punto 6 (mismo fichero, scripts/ de Lares en la Pi).
- Copias de seguridad (regla de Jon, 24-oct): llevar este historial y BORRAR las que sobran (ocupan sitio y pueden ser un
  riesgo, p. ej. un authorized_keys viejo con claves quitadas). Guardar solo la vuelta atrás del último cambio de cada pieza;
  cuando lleve ~1 día bien, borrar las anteriores. Nunca dejar copias de authorized_keys. No tocar copias de otros agentes de Hermes.
  Historial:
  · 24-oct 11:35 — BORRADAS: backend backup-20261023-{002247,005325,135005}; Lares .v1-20261021, .v2-20261023, .v3-20261023-preEDC;
    ~/.ssh/authorized_keys.bak-20261023-{antigravity,edc,token20m}; copia local de los horarios (scratchpad).
  · 24-oct 14:20 — BORRADAS backend backup-20261024-130912 y -132528 (versiones ya superadas).
  · QUEDAN en la Pi: /home/docker/pisos-backend/backup-20261024-141600 (antes de «directa solo dentro del límite»);
    Lares scripts/piso_watch_monitor.py.v4-20261024-preAmueblado.bak. Borrar el 25-oct si todo va bien.
  · 26-oct 21:07 — BORRADAS backend backup-20261024-141600 y backup-20261026-210159 (igual que la de las 21:04) y Lares
    .v4-20261024-preAmueblado.bak. QUEDAN: backend backup-20261026-210404; Lares .v5-20261026-preRefactor.bak. Borrar el 27-oct si va bien.
    Clave SSH temporal quitada (sin copia de authorized_keys).
  · 27-oct 08:05 — BORRADAS backend backup-20261026-{210404,224401,231657}; Lares .v5 y .v6. QUEDAN: backend
    backup-20261027-075658; Lares .v7-20261027-preLejebolig.bak, SOUL.md.bak-20261027, SKILL.md.bak-20261027. Clave SSH quitada (sin copia).
  · PC: ~/.claude/settings.json.bak, .bak-narrator, .bak-20261024-voz (configuración del narrador, 1–2 KB).
