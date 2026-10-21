# pisos-api (Raspberry Pi)

Backend for the shared flat status (gusta / nuevo / contactado / descartado). Python stdlib + SQLite, in Docker.

- Lives on the Pi at `/home/docker/pisos-backend`. DB: `./data/pisos.db` (tables `estado`, `historial`).
- Listens on `127.0.0.1:8787` only. Public via Tailscale Funnel: `https://raspberrypi.tail9f1069.ts.net:8443`.
- `API_KEY` (in `.env` on the Pi, not in git) is sent by the page as `X-Pisos-Key`. It only limits casual abuse (the page is public).

Endpoints: `GET /api/health`, `GET /api/estados`, `POST /api/estado {id, st, by}`.

Deploy / update:
```
docker compose up -d --build
sudo tailscale funnel --bg --https=8443 http://127.0.0.1:8787
```
