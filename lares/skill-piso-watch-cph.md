---
name: piso-watch-cph
description: Use when running or explaining the Copenhagen rental-apartment watch — Boligportal + EDC (+ Juli Living in Vanløse) with the filters set in the web, report ONLY new listings, Vanløse first.
---

# Piso-Watch Copenhague (vigilante de alquiler para Jon y Unai) — rev. 8-nov-2026

Rutina cada 3 h: busca pisos de ALQUILER con los filtros de la web y **solo reporta los NUEVOS** (sin novedades = silencio).

**Implementación:** script sin LLM `/opt/data/profiles/lares/scripts/piso_watch_monitor.py` (cron `piso-watch-cph`, 180 min).
- Fuentes: **Boligportal** (JSON del listado), **EDC** (API JSON por códigos postales) y **Juli Living** (su lista JSON, solo
  Vanløse y solo si esa zona está activa). **Lejebolig ya NO** (decisión de Jon, 27-oct): sus enlaces solo entran a mano.
- **Vanløse es el barrio favorito:** sus avisos salen primero como «NUEVO | ⭐ VANLØSE | …» y allí se avisa aunque falle un filtro
  (con «no pasa: …»); solo se rechaza lo que no sirve para 3 (menos de 4 værelser o de 3 dormitorios, habitaciones) o lo alquilado.
- Filtros y zonas: los de la web («🔎 Búsqueda (Lares)»), que el script lee de `Vivienda/pisos-bridge/config.json`. No hay filtros
  escritos en esta skill a propósito: se quedaban desfasados.
- Escribe en el buzón `Vivienda/pisos-bridge/` (candidatos.json, rechazados.json, retirados.json, lares_runs.jsonl). El buzón es del
  script y del backend: tú no escribes ahí.
- Comprueba si los pisos del mapa y los propuestos siguen publicados (retirado solo con señal clara del portal).
- Cada 3 h lee lo más nuevo; una vez al día (o al cambiar filtros o zonas) recorre Boligportal entero, así que también
  entran anuncios antiguos que cumplen.
- Los avisos «NUEVO» solo traen pisos que no estaban ya en la base: lo añadido a mano (chat o web) no se repite. Sin
  internet («fuentes con error») no toca el buzón.

**Los pisos viven en UNA base de datos** (backend de la Pi, la del mapa https://jonlarra1.github.io/pisos-cph/). Para consultarla o
añadir/anotar pisos usa SOLO `python3 /opt/data/profiles/lares/scripts/pisos.py` (ver SOUL.md, «Pisos de Copenhague»).

Archivados (solo consulta histórica, no usar): `pisos_cph.csv`, `pisos_validos.md`, `pisos_no_validos.md`, la SQLite vieja de
`GestorProyectos/piso-watch/` y los scripts `generar_listas.py`, `marcar_caidos.py`, `check_availability.py`.
