# Lares (agente Hermes en la Raspberry Pi) — pisos

Copias de lo que corre en el perfil `lares` de la Pi (`~/docker/hermes/profiles/lares`; dentro del contenedor
`hermes-gateway`: `/opt/data/profiles/lares`). Se instalan a mano con una clave SSH temporal.

- `piso_watch_monitor.py` → `scripts/`. El vigilante: lo ejecuta la tarea `piso-watch-cph` cada 3 h, sin IA. Solo despierta
  a Lares (salida no vacía) cuando hay candidatos nuevos.
- `pisos.py` → `scripts/`. La herramienta de Lares en el chat: añadir enlaces, consultar, cambiar estados, notas y dormitorios.
  Usa la API v2 con `CLAVE_LARES` (archivo `.pisos_api_key` en la carpeta del perfil). Es su ÚNICA forma de tocar pisos.
- `instrucciones-pisos.md` → se copia en su `SOUL.md` y en la skill `piso-watch-cph/SKILL.md`. Es el contexto del agente:
  si cambia algo que le afecte, se actualiza aquí y se reinstala en el mismo bloque.

## Buzón compartido con el backend
`Vivienda/pisos-bridge/` del vault (Pi: `~/syncthing_media/jon_Vault/Jon/Vivienda/pisos-bridge`, Lares: `/opt/data/vault/Vivienda/pisos-bridge`).
- `config.json` — lo escribe el backend: filtros, zonas (editables desde la web) y estados.
- Lo escribe el vigilante: `candidatos.json` (los que pasan), `rechazados.json` (con motivo), `retirados.json` (comprobador),
  `historial.json` (todos los rechazados + anuncios por día, para el panel), `lares_cron.json` y `lares_runs.jsonl` (estado de las
  tareas y una línea por pasada, para el panel).
- `planos.json` — `{"<id>": {"dorm": 3, "nota": "..."}}`: revisión de planos por Lares con IA (pendiente).
Cuando el vigilante entregue por la API (paso C de `docs/arquitectura.md`), `candidatos.json`, `rechazados.json` y `retirados.json` desaparecen.

## Qué hace el vigilante
Fuentes: Boligportal (listado, 3–8 páginas adaptativas) y EDC (edc.dk). Lejebolig NO se busca (decisión de Jon, 27-oct): sus
enlaces solo entran a mano. EDC se lee por su API JSON (`/api/v1/cases/quick-search`, sin clave) con los códigos postales de las
zonas activas, ≥ værelser y ≤ precio, más nuevo primero, 1–3 páginas de 100 por grupo de códigos. Trae anuncios propios de EDC y
copias de Boligportal (se guardan con su id de Boligportal). El listado de EDC no trae fecha: lo de EDC NO se propone hasta abrir su
ficha (cupo `MAX_DETAIL_EDC` = 12 por pasada; lo que no cabe espera, `edc_pendientes` en lares_runs.jsonl). Fichas de Boligportal:
`MAX_DETAIL` = 8 por pasada, con caché.

Filtros: los de «Búsqueda (Lares)» en la web (`config.json`; por defecto ≤23.000 kr, ≥4 værelser, entrada 2026-12-18..2027-01-15),
zona permitida, contrato no corto, sin «no compartir». Lo que ya está en el mapa o en descartados no se propone; lo decidido en la
web lo oculta el backend y reaparece si se vuelve a «propuesto». Un candidato que deja de salir en las páginas se sigue proponiendo
7 días. Comprobador: ≤8 anuncios por pasada; si un portal dice que se alquiló, va a `retirados.json`.

## Retirado en la Pi (27-oct-2026)
- Archivados (datos, sin borrar) en `Vivienda/archivo-2026-10-26/`: `pisos_cph.csv`, `pisos_validos.md`, `pisos_no_validos.md`,
  `nuevos_desde_14oct.md` y los scripts viejos.
- Tarea `piso-check-links` en pausa (`hermes -p lares cron pause 9a4dc0bb9868`): la sustituye el comprobador.
