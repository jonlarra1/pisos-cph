## Pisos de Copenhague (búsqueda de piso de Jon y Unai)

Hay UNA sola base de datos de pisos: la del backend en la Pi, la misma que usa el mapa
(https://jonlarra1.github.io/pisos-cph/). Solo se usa con esta herramienta:

    python3 /opt/data/profiles/lares/scripts/pisos.py <orden>

### Qué hacer
- **Lejebolig ya NO se busca** (a Jon no le gusta): no busques ni propongas pisos de Lejebolig por tu cuenta. Si te
  pasan un enlace de Lejebolig, se añade igual, pero sugiere buscar el mismo piso en Boligportal.
- **Jon o Unai te pasan un enlace** de Boligportal, EDC, Juli Living (o Lejebolig) → `pisos.py añadir <enlace> --por jon`
  (o `--por unai`). Contesta con lo que diga la herramienta: si se ha añadido o ya estaba, y los avisos
  (p. ej. «no pasa: …»). Sale en el mapa en «Propuestos». Varios enlaces → una orden por enlace.
- **Jon y Unai también añaden enlaces desde la web** (barra de abajo del mapa, «➕ Añadir»): van a la misma base.
  Si te dicen «lo he añadido en la web», no lo vuelvas a añadir: compruébalo con `pisos.py consultar <enlace>`.
- **Te preguntan qué pisos hay** → `pisos.py consultar`; por un piso concreto → `pisos.py consultar <enlace>`.
- **Has comprobado algo de un piso** (ficha revisada, «es la gemela de…», trampa del anuncio, zona límite) →
  `pisos.py nota <enlace> <texto>`. Sale en la ficha del mapa y queda en el historial.
- **Has contado los dormitorios reales en el plano** → `pisos.py dormitorios <enlace> <n>`. El piso se vuelve a
  evaluar con ese dato (si no llega al mínimo, sale el aviso «no pasa»).
- **Te piden marcar un piso** → `pisos.py estado <enlace> gusta|contactado|descartado|propuesto [motivo] --por jon|unai`.
  Solo cuando te lo pidan expresamente, nunca por tu cuenta.

### Vanløse es el barrio favorito
- A Jon y Unai les encanta **Vanløse**: es la prioridad. Los avisos del script traen primero los de Vanløse, marcados
  «⭐ VANLØSE». Cuéntalos los primeros y destácalos.
- En Vanløse el script avisa aunque el piso falle un filtro (precio, fecha, contrato, compartir…): llega con el aviso
  «no pasa: …». Díselo tal cual, para que decidan ellos. Solo se rechaza lo que no sirve para 3 (menos de 4 værelser
  o de 3 dormitorios, habitaciones) y lo ya alquilado.
- El script también mira **Juli Living** (juliliving.dk, el edificio Tagkronen en Vanløse Torv), solo en Vanløse. Sus
  pisos vuelan («se alquilan en menos de 48 h»): si sale uno, avisa en cuanto lo veas.

### Chats de Boligportal (cuenta compartida de Unai)
- El script `chats_boligportal.py` (tarea `boligportal-chats`, sin IA) lee la bandeja de Boligportal a horas al azar,
  cada 3–5 h y nunca de 00 a 08 h. SOLO LEE. La web enseña en cada piso: 💬 han contestado, ⏳ sin respuesta (con los
  días), 📅 hablan de una visita, 🏁 el casero dice que está alquilado, y el botón «💬 Abrir conversación».
- Si el script te despierta con «RESPUESTA | calle | marca | fecha | enlace»: avisa a Jon y Unai enseguida (las
  visitas 📅 primero, y Vanløse ⭐ antes que nada) con ese enlace. Para leer el mensaje: `pisos.py chats [calle o número]`.
- «[aviso] La sesión de Boligportal ha caducado»: dile a Jon que hay que renovar la cookie (lo hace él o Unai). Tú
  nunca la pides, la lees ni la escribes.
- Las conversaciones de pisos que no estaban en la base entran solas en 🟢 contactado; los demás estados no se tocan.
- Nunca escribas a un casero, ni contestes, ni archives conversaciones: no hay forma y no se hace.
- ❤️ «nos encanta» lo marcan Jon y Unai en la web; no lo cambies tú salvo que te lo pidan.

### Criterios de búsqueda
- Los filtros con números (precio máximo, dormitorios, fechas, zonas, amueblado) se cambian SOLO en la web, en
  «🔎 Búsqueda (Lares)». Tú los ves en `Vivienda/pisos-bridge/config.json` (solo leer).
- `/opt/data/lares_vivienda_criterios.md` queda para lo que el código no puede comprobar (gemelas, trampas,
  zonas límite…). No copies ahí los filtros con números: se quedarían desfasados.

### Destinos (sitios a los que vais)
- Los destinos (trabajo, casa de amigos, gimnasio…) están en la web, en «Configuración → Destinos», compartidos entre
  Jon y Unai y con rutas para los dos. No los copies a ningún archivo; si te piden añadir uno, diles que lo hagan ahí.

### Prohibido
- Crear, editar o leer archivos de pisos: `pisos_cph.csv`, `pisos_validos.md`, `pisos_no_validos.md`, ni ningún CSV,
  Markdown o JSON de pisos. Están archivados en `Vivienda/archivo-2026-10-26/` (solo consulta histórica); lo que
  escribas ahí NO llega al mapa. Tampoco uses `generar_listas.py`, `marcar_caidos.py` ni `check_availability.py`.
- Escribir en la carpeta `Vivienda/pisos-bridge` (es del script vigilante y del backend).
- Abrir la base de datos directamente o llamar a la API con otra cosa que no sea `pisos.py`.
- Enseñar, copiar o leer la clave (`.pisos_api_key`) o la cookie de Boligportal (`.boligportal_cookie`).
- Enseñar el texto de los chats a nadie que no sea Jon o Unai en su chat privado.
- Borrar pisos: no existe borrar. Si algo no interesa, se marca «descartado».

### Si algo falla
- Si `pisos.py` da `ERROR: …`, díselo a Jon tal cual y para. No busques otro camino (ni archivos ni scripts).
- «la API respondió 429»: has hecho demasiados cambios: como mucho 20 por hora (estados, notas y dormitorios) y 60 peticiones por minuto; espera.

### Lo que NO tienes que hacer tú
- Buscar pisos nuevos en los portales y comprobar si siguen publicados (también los que añades por el chat):
  lo hace el script automático sin IA `piso_watch_monitor.py`, cada 3 h. La tarea `piso-check-links` ya no existe.
  No gastes tokens en eso. Cada 3 h mira lo más nuevo, y una vez al día (o cuando cambian los filtros) recorre
  Boligportal entero con los filtros, así que también entran anuncios antiguos que cumplen.
- Los avisos «NUEVO» del script solo traen pisos que no estaban ya en la base: lo añadido a mano (chat o web) no se
  vuelve a avisar. Para saber qué pisos hay de verdad, usa siempre `pisos.py consultar`.
- Si el script avisa «fuentes con error» (la Pi sin internet), la lista de propuestos se queda como estaba: no
  hace falta hacer nada, solo decírselo a Jon si pregunta.
