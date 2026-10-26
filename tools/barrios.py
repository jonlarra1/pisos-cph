"""Build data/barrios.json: Copenhagen's 10 official bydele (kk.dk WFS) + neighbouring municipalities (OSM via Nominatim).
Usage: python3 tools/barrios.py <bydel_kk.json> <kommuner.json> data/barrios.json"""
import json, sys
BY, KOM, OUT = sys.argv[1:4]
def dp(pts, tol):
    if len(pts) < 3: return pts
    a, b = pts[0], pts[-1]; dmax, idx = 0, 0
    for i in range(1, len(pts)-1):
        p = pts[i]; dx, dy = b[0]-a[0], b[1]-a[1]
        d = abs(dy*p[0]-dx*p[1]+b[0]*a[1]-b[1]*a[0])/((dx*dx+dy*dy)**.5 or 1e-12) if (dx or dy) else ((p[0]-a[0])**2+(p[1]-a[1])**2)**.5
        if d > dmax: dmax, idx = d, i
    if dmax <= tol: return [a, b]
    return dp(pts[:idx+1], tol)[:-1]+dp(pts[idx:], tol)
def polys(g):
    return [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
def area_c(ring):
    A = cx = cy = 0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]+ring[:1], strict=False):
        f = x1*y2-x2*y1; A += f; cx += (x1+x2)*f; cy += (y1+y2)*f
    return A/2, (cx/(3*A) if A else ring[0][0], cy/(3*A) if A else ring[0][1])
def feat(name, kind, g):
    out = []; best = (0, None)
    for poly in polys(g):
        rings = []
        for ring in poly:
            r = dp([[round(x, 5), round(y, 5)] for x, y in ring], 0.00015)
            if len(r) >= 4: rings.append([[y, x] for x, y in r])   # Leaflet order lat,lon
        if rings:
            out.append(rings); A, c = area_c([[p[1], p[0]] for p in rings[0]])
            if abs(A) > best[0]: best = (abs(A), [round(c[1], 5), round(c[0], 5)])
    return {"n": name, "k": kind, "c": best[1], "p": out}
F = [feat(f["properties"]["navn"], "bydel", f["geometry"]) for f in json.load(open(BY))["features"]]
F += [feat(x["name"].replace(" Kommune", ""), "kommune", x["geojson"]) for x in json.load(open(KOM))]
json.dump(F, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
print(len(F), "areas")
