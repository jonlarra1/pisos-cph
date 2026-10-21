# Lares (agente Hermes en la Raspberry Pi) — vigilante de pisos

`piso_watch_monitor.py` se instala en la Pi en `~/docker/hermes/profiles/lares/scripts/` (dentro del contenedor
`hermes-gateway`: `/opt/data/profiles/lares/scripts/`). Lo ejecuta la tarea `piso-watch-cph` de Lares cada 3 h, sin IA.
Copia del script anterior: `piso_watch_monitor.py.v1-20261021.bak` (mismo directorio).

Buzón compartido con el backend: `Vivienda/pisos-bridge/` del vault
(Pi: `~/syncthing_media/jon_Vault/Jon/Vivienda/pisos-bridge`, Lares: `/opt/data/vault/Vivienda/pisos-bridge`).
- `config.json` — lo escribe el backend: filtros, zonas (editables desde la web) y estados.
- `candidatos.json` / `rechazados.json` — los escribe este script.
- `planos.json` — `{"<id>": {"dorm": 3, "nota": "..."}}`: revisión de planos hecha por Lares con IA (pendiente).

Filtros en código: ≥4 værelser, ≤23.000 kr, zona permitida, entrada 2026-12-18..2027-01-15, contrato no corto,
sin «no compartir». Lo que ya está en el mapa/descartados no se propone; lo decidido en la web se oculta y
reaparece si se vuelve a «nuevo».
