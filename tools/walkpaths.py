"""Replace straight walking legs in data/rutas.json with real footpaths (FOSSGIS OSRM foot profile, OSM data).
Also sets walking distance (m) and minutes per leg and recomputes leave time / total minutes.
Usage: python3 tools/walkpaths.py data/rutas.json [cache.json]   (max 1 request/second)"""
import json, sys, time, urllib.request, os
F = sys.argv[1]; CACHE = sys.argv[2] if len(sys.argv) > 2 else "walkcache.json"
R = json.load(open(F)); cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
UA = "pisos-cph/1.0 (personal flat-hunting map; light use)"
def foot(pts):
    key = ";".join(f"{lo:.5f},{la:.5f}" for la, lo in pts)
    if key not in cache:
        u = f"https://routing.openstreetmap.de/routed-foot/route/v1/driving/{key}?overview=full&geometries=geojson"
        for i in range(3):
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)); break
            except Exception as e:
                print("retry", e); time.sleep(3)
        time.sleep(1.1)
        if r.get("code") != "Ok": cache[key] = None
        else:
            x = r["routes"][0]; g = [[round(la, 5), round(lo, 5)] for lo, la in x["geometry"]["coordinates"]]
            cache[key] = {"g": g, "d": round(x["distance"]), "t": x["duration"]}
        json.dump(cache, open(CACHE, "w"))
    return cache[key]
def sec(s): h, m = s.split(":"); return int(h)*3600+int(m)*60
def hm(t): t = int(round(t/60)); return f"{t//60:02d}:{t%60:02d}"
newp = {}
for r in R["routes"]:
    for L in r["legs"]:
        if L["m"] != "walk": continue
        if L["p"] not in newp:
            res = foot(R["paths"][L["p"]])
            if res:
                g = res["g"]; a, b = R["paths"][L["p"]][0], R["paths"][L["p"]][-1]
                g = [a]+g+[b]            # keep exact start/end (flat, stop, destination)
                R["paths"].append(g); newp[L["p"]] = (len(R["paths"])-1, res["d"], res["t"])
            else: newp[L["p"]] = None
        n = newp[L["p"]]
        if n: L["p"], L["d"], L["min"] = n[0], n[1], max(1, round(n[2]/60))
    rides = [L for L in r["legs"] if L["m"] != "walk"]
    if rides:
        if r["legs"][0]["m"] == "walk": r["sale"] = hm(sec(rides[0]["dep"])-r["legs"][0]["min"]*60)
        else: r["sale"] = rides[0]["dep"]
        r["llega"] = hm(sec(rides[-1]["arr"])+(r["legs"][-1]["min"]*60 if r["legs"][-1]["m"] == "walk" else 0))
        r["min"] = round((sec(r["llega"])-sec(r["sale"]))/60)
    else:
        r["min"] = r["legs"][0]["min"]
# drop unused paths and reindex
used = sorted({L["p"] for r in R["routes"] for L in r["legs"]}); remap = {o: i for i, o in enumerate(used)}
R["paths"] = [R["paths"][o] for o in used]
for r in R["routes"]:
    for L in r["legs"]: L["p"] = remap[L["p"]]
json.dump(R, open(F, "w"), ensure_ascii=False, separators=(",", ":"))
print("ok", len(newp), "walk legs routed;", sum(1 for v in newp.values() if v is None), "failed")
