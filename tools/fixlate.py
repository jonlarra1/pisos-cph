"""Run after walkpaths.py. Usage: python3 tools/fixlate.py data/rutas.json"""
import json, sys
F = sys.argv[1]
# After real street distances some routes may arrive after the chosen time.
# Replace them with the latest earlier-slot route of the same flat/dest that still arrives on time.
R = json.load(open(F)); T = R["times"]; late = fixed = 0
ok = lambda i, t: R["routes"][i]["llega"] <= t
for _mo, D in R["ix"].items():
    for _d, TT in D.items():
        for ti, t in enumerate(T):
            for lim, FF in TT.get(t, {}).items():
                for fid, X in FF.items():
                    for k, ids in list(X.items()):
                        good = [i for i in ids if ok(i, t)]
                        if len(good) == len(ids): continue
                        late += 1
                        if not good:   # fall back to the latest earlier slot that arrives on time
                            for t2 in reversed(T[:ti]):
                                good = [i for i in TT.get(t2, {}).get(lim, {}).get(fid, {}).get(k, []) if ok(i, t)]
                                if good: break
                        if good: X[k] = good; fixed += 1
                        else: del X[k]
json.dump(R, open(F, "w"), ensure_ascii=False, separators=(",", ":"))
print("slots with late options", late, "fixed", fixed, "removed", late-fixed)
