# Lares (agente Hermes en la Raspberry Pi) — vigilante de pisos

`piso_watch_monitor.py` se instala en la Pi en `~/docker/hermes/profiles/lares/scripts/` (dentro del contenedor
`hermes-gateway`: `/opt/data/profiles/lares/scripts/`). Lo ejecuta la tarea `piso-watch-cph` de Lares cada 3 h, sin IA.
Copia del script anterior: `piso_watch_monitor.py.v1-20261021.bak` (mismo directorio).

Buzón compartido con el backend: `Vivienda/pisos-bridge/` del vault
(Pi: `~/syncthing_media/jon_Vault/Jon/Vivienda/pisos-bridge`, Lares: `/opt/data/vault/Vivienda/pisos-bridge`).
- `config.json` — lo escribe el backend: filtros, zonas (editables desde la web) y estados.
- `candidatos.json` / `rechazados.json` — los escribe este script.
- `planos.json` — `{"<id>": {"dorm": 3, "nota": "..."}}`: revisión de planos hecha por Lares con IA (pendiente).

Fuentes: Boligportal y EDC (edc.dk, desde el 23-oct). Lejebolig ya NO se busca (decisión de Jon, 27-oct): sus enlaces solo entran a mano. EDC se lee por su API JSON
(`/api/v1/cases/quick-search`, sin clave) con los códigos postales de las zonas activas (`ZONA_POSTNR`), ≥ værelser y
≤ precio, más nuevo primero, máx. 3 páginas de 100 por grupo de códigos (4 grupos con todas las zonas). Trae anuncios propios
de EDC y copias de Boligportal (se guardan con su id de Boligportal; si Boligportal ya lo trajo, vale esa versión).
El listado de EDC no trae fecha: lo de EDC NO se propone hasta abrir su ficha (cupo propio `MAX_DETAIL_EDC` = 12 por pasada,
primero los propios de EDC); lo que no cabe espera (`edc_pendientes` en lares_runs.jsonl). La ficha de EDC da fecha,
depósito y prepago por separado, aconto, lejeperiode y «Rum/værelser 4/3» (dormitorios → se rechaza si < dorm_min).

Filtros en código: ≥4 værelser, ≤23.000 kr, zona permitida, entrada 2026-12-18..2027-01-15, contrato no corto,
sin «no compartir». Lo que ya está en el mapa/descartados no se propone; lo decidido en la web se oculta y
reaparece si se vuelve a «nuevo».
