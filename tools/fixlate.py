"""Run after walkpaths.py. Usage: python3 tools/fixlate.py data/rutas.json"""
import json, sys
F = sys.argv[1]
# After real street distances some routes may arrive after the chosen time.
# Replace them with the latest earlier-slot route of the same flat/dest that still arrives on time.
R = json.load(open(F)); T = R["times"]; late = fixed = 0
for mo, D in R["ix"].items():
    for d, TT in D.items():
        for ti, t in enumerate(T):
            for lim, FF in TT.get(t, {}).items():
                for fid, X in FF.items():
                    for k, i in list(X.items()):
                        if R["routes"][i]["llega"] <= t: continue
                        late += 1; alt = None
                        for t2 in reversed(T[:ti]):
                            j = TT.get(t2, {}).get(lim, {}).get(fid, {}).get(k)
                            if j is not None and R["routes"][j]["llega"] <= t: alt = j; break
                        if alt is not None: X[k] = alt; fixed += 1
                        else: del X[k]
json.dump(R, open(F, "w"), ensure_ascii=False, separators=(",", ":"))
print("late", late, "fixed with earlier slot", fixed, "removed", late-fixed)
