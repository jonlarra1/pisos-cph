# Arquitectura de pisos-cph (diseño de referencia)

Acordado con Jon el 2026-10-26. Este documento manda: si el código o el `CLAUDE.md` dicen otra cosa, se corrige el código
o se actualiza aquí primero, con Jon.

## Por qué

Hasta ahora los datos de pisos vivían en cinco sitios con cinco formatos (CSV de Lares, `.md` del vault, JSON del buzón,
SQLite del vigilante y la lista `PISOS` dentro de `index.html`). Nada mandaba sobre nada: un enlace que Jon le pasaba a
Lares por el chat acababa en el CSV y nunca llegaba al mapa (análisis del 26-oct: de 93 enlaces de `pisos_validos.md`,
42 ya estaban descartados en el mapa, 32 no los había visto nunca el vigilante y 4 eran propuestos sin coordenadas).

## Principios

1. **Una sola fuente de verdad:** la base de datos SQLite del backend (`pisos-api`, `/data/pisos.db`).
2. **Una sola puerta:** la API. Nadie lee ni escribe la base directamente (ni Lares, ni la web, ni scripts, ni Claude).
3. **Una sola entidad:** el *anuncio*. No hay listas separadas, solo estados.
4. **Nada se borra:** solo cambia de estado, con historial. Todo se puede deshacer.
5. **Las reglas en un solo sitio:** filtros, zonas, duplicados y «no volver a proponer lo descartado» viven en el backend.
   Los scripts *encuentran* o *comprueban*; el backend *decide*.
6. **Las decisiones de Jon y Unai mandan:** ningún proceso automático las pisa (como mucho añade un aviso).
7. **Lo que no es IA no usa IA:** comprobar anuncios y buscar nuevos lo hacen scripts. Lares (IA) solo actúa cuando
   se le habla.

## El anuncio

Campos: portal + id (clave), enlaces (un mismo piso puede estar en varios portales → un solo anuncio con varios enlaces),
precio, aconto, depósito, prepago (siempre por separado), m², værelser, dormitorios (y de dónde sale: plano, texto,
estimado), fecha de entrada, compartir, contrato, amueblado, balcón, mascotas, planta, dirección, ubicación + precisión
(exacta / calle / aproximada), fotos, plano, estado, motivo, avisos, origen (vigilante, chat de Lares, Jon, Unai, import),
fechas (visto por primera vez, última comprobación).

Estados:

```
            ┌──────────── lo decide el sistema ───────────┐   ┌──────── lo decidís Jon y Unai ────────┐
encontrado ─► rechazado (no pasa filtros, con motivo)       │   │                                        │
            └► propuesto ─────────────────────────────────────►  gusta ─► contactado                    │
                                                             │   descartado (48 h en el mapa → archivo) │
cualquiera ─► retirado (el portal lo quitó)                  └────────────────────────────────────────┘
```

- Un enlace que añade una persona o Lares por el chat se **propone aunque no pase algún filtro**, con el motivo como aviso.
- `retirado` sobre un anuncio decidido (gusta, contactado) no cambia la decisión: añade el aviso «el portal dice …».

Historial: cada cambio guarda quién, cuándo, de qué estado a cuál y el motivo. De ahí salen los contadores y gráficos
del panel.

## Quién hace qué

| Pieza | Qué es | Qué hace |
|---|---|---|
| Backend (`pisos-api`) | contenedor en la Pi | base de datos, reglas, lectura de fichas de los portales, geocodificación, API |
| Web | GitHub Pages | mapa; carga los anuncios de la API (copia estática solo de respaldo) |
| Vigilante | script sin IA (tarea de Lares) | recorre los portales y entrega a la API lo que encuentra |
| Comprobador | script sin IA | cada ~6 h revisa propuestos, gusta y contactados: ¿sigue activo? |
| Lares (chat) | agente con IA | añadir un enlace, consultar, cambiar el estado de un piso concreto, cuando Jon se lo pide |
| Panel | privado, solo tailnet | solo lectura: estado, contadores, gráficos, historial |

## Permisos (una clave por cliente)

| Clave | Puede | No puede |
|---|---|---|
| comprobador | marcar activo / retirado | cambiar decisiones de Jon/Unai |
| vigilante | entregar anuncios, leer filtros y zonas | cambiar estados ya decididos |
| lares | añadir enlace, consultar, cambiar estado de **un** anuncio | filtros, zonas, cambios en masa |
| web (Jon, Unai) | todo lo anterior + filtros y zonas (con confirmación de búsqueda) | — |
| todas | — | **borrar** (no existe la operación) |

Topes por clave (p. ej. Lares ≤ 20 cambios de estado por hora); si se superan, se corta y el panel lo marca.
Copia de seguridad diaria de la base (se guardan las 7 últimas).

Red: Lares llega a la API por una red interna de Docker con su propia clave; la API no puede llegar a Lares
(el cortafuegos de salida de `pisos-api` no cambia).

## Lo que desaparece

- `pisos_cph.csv`, `pisos_validos.md`, `pisos_no_validos.md`, `nuevos_desde_14oct.md` y los scripts sueltos de
  `Vivienda/` (se importa su contenido una vez antes).
- Del buzón `pisos-bridge`: `candidatos.json`, `rechazados.json`, `retirados.json`, `historial.json`, `config.json`.
- `PISOS` y `OUT` dentro de `index.html` (pasan a la API; la copia estática se genera sola).
- La base propia del vigilante como fuente de verdad (le queda solo una caché interna de fichas leídas).
- La tarea `piso-check-links` de Lares (la sustituye el comprobador, sin IA ni tokens).

Quedan en ficheros solo cosas que no son datos de pisos: logs (JSON por líneas), copias de seguridad de la base y la
copia estática de la web. Si Jon quiere verlo en Obsidian: una nota de solo lectura generada desde la base.

## Fases

1. **Backend** (local): modelo de datos, API con permisos y topes, historial, lectura de fichas de los 3 portales,
   geocodificación. Con pruebas «debe funcionar / debe fallar».
2. **Migración:** importar pisos del mapa, descartados, propuestos, rechazados y el CSV de Lares (duplicados unidos).
3. **Web:** cargar de la API, botón «Añadir enlace», copia estática de respaldo.
4. **Lares:** vigilante y comprobador por la API; herramienta del chat (`pisos añadir/consultar/estado`); instrucciones
   de Lares actualizadas. Requiere clave SSH temporal.
5. **Pruebas** de funcionamiento y de abuso; retirar lo viejo; panel con contadores e historial desde la base.
