# Task for Claude Code

Do these steps in order. After each step, tell me in 1–2 lines what you did.

## 1. Run it locally
Serve the folder with `python3 -m http.server 8000` and check the page loads at http://localhost:8000 with no console errors.
(Do not open it with file:// — OSM tile servers may refuse requests without a Referer.)

## 2. Real OpenStreetMap basemap
- Add a tile layer under everything else. Light theme: CARTO Positron (`https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png`). Dark theme: CARTO Dark Matter (`.../dark_all/...`). Switch the layer when the theme changes (there is already a `restyle()` function and a theme MutationObserver).
- Attribution: "© OpenStreetMap contributors © CARTO". Turn `attributionControl` on.
- Remove the embedded basemap layers and their data: `BASE`, `URBAN`, `LAKES`, `PARKS`, `ROADS`, the municipality labels (`muniLayer`, `muniLbl`) and the bus-street layer (`streetLayer` / `RAIL.roads`). Keep `RAIL.g`, `RAIL.c`, `RAIL.st`, `RAIL.letb`.
- Remove `preferCanvas:true` if it causes problems with tiles; otherwise keep it.
- Raise `maxZoom` to 18.

## 3. Exact location of each flat
For each flat in `PISOS` whose `ubic` is not exact:
1. Open its `link` (lejebolig.dk / boligportal.dk) and look for the full address with house number. Lejebolig often shows street + number in the page title or map block.
2. Geocode the address. Try in this order:
   - Danish address API (DAR via Dataforsyningen, or DanskAdresseAPI) if reachable.
   - Nominatim: `https://nominatim.openstreetmap.org/search?format=json&countrycodes=dk&q=<street number, postcode>` with a real User-Agent, 1 request/second.
3. Update `lat`, `lon` and set `ubic` to "exacta (número X)" or "calle" if only the street was found.
4. Show me a table: flat, old position, new position, distance moved (m), precision.

## 4. Check the links again
Open every `link` in `PISOS`. If a page returns 404, says "Udlejet", "Reserveret" or similar, move that flat from `PISOS` to `OUT` with the reason and today's date. Show me what changed.

## 5. Recalculate
Nearest S-tog, metro and Letbane station and distance to work are computed in JS from lat/lon, so they update by themselves. Just reload and confirm the cards show the new values.

## 6. (Optional, only if I say yes) Share it
Put it on GitHub Pages so my flatmates can open it with a link.
