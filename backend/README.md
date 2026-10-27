# pisos-api (Raspberry Pi)

Backend de pisos-cph: estados compartidos, base única de anuncios (API v2), destinos compartidos, motor de rutas, proxy de
fotos, logs, checks y panel privado. Python stdlib + SQLite (WAL), en Docker. Cómo encaja con la web y con Lares: tabla
«Estado real» de `../CLAUDE.md`.

- En la Pi: `/home/docker/pisos-backend`. Base: `./data/pisos.db`. Escucha solo en `127.0.0.1:8787` (API) y `127.0.0.1:8790` (panel).
  Público por Tailscale Funnel: `https://raspberrypi.tail9f1069.ts.net:8443`. Panel: `http://100.72.170.60:9445` (solo tailnet).
- `.env` (solo en la Pi, nunca en git): `API_KEY` (la de la web, pública: solo frena el abuso casual), `CLAVE_LARES`,
  `CLAVE_VIGILANTE`, `CLAVE_COMPROBADOR`, `ADMIN_USERS`, `ALLOWED_ORIGINS`.

## Módulos
- `app.py` — servidor HTTP, endpoints de la web, límites de peticiones, SSE, logs, checks del monitor y panel.
- `api_v2.py` — `/api/v2/…`: permisos por clave de cliente (lista de endpoints arriba del archivo).
- `anuncios.py` — el anuncio: reglas (`evaluar`), estados, historial (`cambio`), duplicados (`alias`).
- `portales.py` — lee la ficha de un anuncio de Boligportal, EDC o Lejebolig a partir de su enlace.
- `puente.py` — une la base única con lo que la web lee hoy (`/api/candidatos`, tabla `estado`). Se borra en el paso D del plan.
- `destinos.py` — destinos compartidos. `precalculo.py` — rutas previas de los destinos y freno del motor. `routing.py` — motor de rutas (GTFS).

## Endpoints
- Web: `GET /api/health`, `/api/estados`, `/api/candidatos`, `/api/zonas`, `/api/filtros`, `/api/destinos`, `/api/ruta?…`,
  `/api/img?u=…`, SSE `/api/eventos`; `POST /api/estado`, `/api/zona`, `/api/filtros`, `/api/destino`, `/api/clientlog`.
- API v2: ver la cabecera de `api_v2.py`.
- Panel (8790): `/`, `/status`, `/flow`, `/checknow`.

## Instalar / actualizar
Desde el PC, con una clave SSH temporal de Jon:
```
PI_KEY=~/.ssh/claude-pisos-1h backend/deploy.sh jon@100.72.170.60            # solo mira
PI_KEY=~/.ssh/claude-pisos-1h backend/deploy.sh jon@100.72.170.60 --aplicar  # tests, copia, build, smoke test, vuelta atrás si falla
```
