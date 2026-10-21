"""Bike and walking routes (flat -> destination) on real streets, FOSSGIS OSRM (OSM data).
Usage: python3 tools/direct.py data/pisos.json data/directo.json [cache.json]   (max 1 request/second)"""
import json, sys, time, urllib.request, os
P = json.load(open(sys.argv[1])); OUT = sys.argv[2]; CACHE = sys.argv[3] if len(sys.argv) > 3 else "directcache.json"
DEST = {"gn": (55.738723, 12.387155), "ns": (55.733318, 12.575586), "dtu": (55.7858465, 12.5214443)}
PROF = {"bike": "routed-bike", "foot": "routed-foot"}
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
UA = "pisos-cph/1.0 (personal flat-hunting map; light use)"
def get(prof, a, b):
    key = f"{prof}:{a[1]:.5f},{a[0]:.5f};{b[1]:.5f},{b[0]:.5f}"
    if cache.get(key) is None:
        u = f"https://routing.openstreetmap.de/{PROF[prof]}/route/v1/driving/{a[1]:.5f},{a[0]:.5f};{b[1]:.5f},{b[0]:.5f}?overview=full&geometries=geojson"
        r = None
        for _ in range(3):
            try: r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)); break
            except Exception as e: print("retry", e); time.sleep(3)
        time.sleep(1.1)
        if r and r.get("code") == "Ok":
            x = r["routes"][0]
            pts = [[round(la, 5), round(lo, 5)] for lo, la in x["geometry"]["coordinates"]]
            th = [pts[0]]
            for q in pts[1:]:
                if abs(q[0]-th[-1][0])+abs(q[1]-th[-1][1]) > 0.00015: th.append(q)
            if th[-1] != pts[-1]: th.append(pts[-1])
            cache[key] = {"min": max(1, round(x["duration"]/60)), "d": round(x["distance"]), "path": [list(a)]+th+[list(b)]}
            json.dump(cache, open(CACHE, "w"))
        else:
            return None          # no se guarda el fallo: se reintenta la próxima vez
    r = cache[key]
    if prof == "bike" and r: r = dict(r, min=max(1, round(r["d"]/(20/3.6)/60)))  # Jon: 20 km/h
    return r
out = {m: {} for m in PROF}
for p in P:
    for m in PROF:
        out[m][p["id"]] = {k: get(m, (p["lat"], p["lon"]), d) for k, d in DEST.items()}
    print(p["id"], {m: {k: (v["min"], v["d"]) if v else None for k, v in out[m][p["id"]].items()} for m in PROF}, flush=True)
json.dump(out, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
