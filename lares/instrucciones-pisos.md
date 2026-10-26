## Pisos de Copenhague (búsqueda de piso de Jon y Unai)

Hay UNA sola base de datos de pisos: la del backend en la Pi, la misma que usa el mapa
(https://jonlarra1.github.io/pisos-cph/). Solo se usa con esta herramienta:

    python3 /opt/data/profiles/lares/scripts/pisos.py <orden>

### Qué hacer
- **Lejebolig ya NO se busca** (a Jon no le gusta): no busques ni propongas pisos de Lejebolig por tu cuenta. Si te
  pasan un enlace de Lejebolig, se añade igual, pero sugiere buscar el mismo piso en Boligportal.
- **Jon o Unai te pasan un enlace** de Boligportal, EDC (o Lejebolig) → `pisos.py añadir <enlace> --por jon`
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
- Enseñar o copiar la clave (`.pisos_api_key`).
- Borrar pisos: no existe borrar. Si algo no interesa, se marca «descartado».

### Si algo falla
- Si `pisos.py` da `ERROR: …`, díselo a Jon tal cual y para. No busques otro camino (ni archivos ni scripts).
- «la API respondió 429»: has hecho demasiados cambios seguidos (máximo 20 por hora); espera.

### Lo que NO tienes que hacer tú
- Buscar pisos nuevos en los portales y comprobar si siguen publicados (también los que añades por el chat):
  lo hace el script automático sin IA `piso_watch_monitor.py`, cada 3 h. La tarea `piso-check-links` ya no existe.
  No gastes tokens en eso.
