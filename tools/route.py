"""Fastest public-transport routes from each flat to each destination.

Usage:
  python3 tools/route.py data/pisos.json data/rutas.json <gtfs>/shapes.txt prep_YYYYMMDD.pkl [more days...]

Each prep_*.pkl comes from tools/prep.py (one per weekday).
For every day x arrival time x walking limit it finds the LATEST departure that
still arrives before the chosen time (like Rejseplanen's "arrive by").
Engine: Connection Scan Algorithm on the Rejseplanen GTFS.
"""
import pickle, math, json, sys, bisect, collections, csv, datetime, multiprocessing

PISOS, OUT, SHAPES, PREPS = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
P = json.load(open(PISOS))
DEST = [("gn", "GN · Lautrupbjerg 7, Ballerup", 55.738723, 12.387155),
        ("ns", "Nordic Solar · Strandvejen 102E, Hellerup", 55.733318, 12.575586),
        ("dtu", "DTU · Anker Engelunds Vej 1, Lyngby", 55.7858465, 12.5214443)]
TIMES = ["07:30", "08:00", "08:30", "09:00", "09:30", "10:00"]   # "llegar antes de"
LIMITS = [5, 10, 15, 20, 25, 30]   # max minutes walking or biking per leg (profile setting)
V, DET, CHG = 1.33, 1.25, 90      # walk speed m/s, detour factor, change buffer s
VB, DETB = 20/3.6, 1.3            # bike speed m/s (20 km/h, Jon), detour factor
PARK, BOARD = 0, 0                # no time counted for locking / boarding with the bike
RAILBIKE = {109, 2, 0}            # with a bike: S-tog, regional/local trains and Letbane (free bikes). No bus, no metro.
MODES_OUT = ["foot", "bike"]      # access/egress mode (always with public transport)
STEP = 120                        # departure sampling step (s)
DAYN = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MODE = {109: "S-tog", 1: "Metro", 0: "Letbane", 2: "Tren", 3: "Bus", 700: "Bus", 4: "Ferry"}

def hav(a, b, c, d):
    R = 6371000; p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2-p1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(d-b)/2)**2
    return 2*R*math.asin(math.sqrt(x))
def wt(m): return m*DET/V
def bt(m): return m*DETB/VB
def hm(t): t = int(round(t/60)); return f"{t//60:02d}:{t%60:02d}"
def sec(s): h, m = s.split(":"); return int(h)*3600+int(m)*60

class Day:
    def __init__(self, pkl):
        D = pickle.load(open(pkl, "rb"))
        self.stops, self.conns, self.trips, self.routes, self.seq = D["stops"], D["conns"], D["trips"], D["routes"], D["seq"]
        self.deps = [c[0] for c in self.conns]
        self.rconns = [c for c in self.conns if self.routes[self.trips[c[4]][0]][1] in RAILBIKE]
        self.rdeps = [c[0] for c in self.rconns]
        used = set()
        for c in self.conns: used.update((c[2], c[3]))
        self.SL = [(s, self.stops[s][1], self.stops[s][2]) for s in used]
        self.grid = collections.defaultdict(list)
        for s, la, lo in self.SL: self.grid[(int(la*100), int(lo*60))].append((s, la, lo))
    def near(self, la, lo, r):
        out = []
        for i in range(int(la*100)-2, int(la*100)+3):
            for j in range(int(lo*60)-2, int(lo*60)+3):
                for s, a, b in self.grid.get((i, j), []):
                    d = hav(la, lo, a, b)
                    if d <= r: out.append((s, d))
        return out
    def setup(self, lim):
        self.WALKMAX = lim*60*V/DET; fp = min(self.WALKMAX, 640); self.BIKEMAX = lim*60*VB/DETB
        self.foot = {s: [(n, wt(d)) for n, d in self.near(la, lo, fp) if n != s] for s, la, lo in self.SL}
        self.egress = {k: dict(self.near(la, lo, self.WALKMAX)) for k, _, la, lo in DEST}
        self.begress = {k: dict(self.near(la, lo, self.BIKEMAX)) for k, _, la, lo in DEST}
    def csa(self, la, lo, t0, var="foot", ban=frozenset()):
        """var: foot = walk/transit/walk; park = bike to station, park, transit, walk;
        carry = bike, train with bike (S-tog/regional only), bike."""
        INF = 1e18; ready = {}; arr = {}; ptr = {}; enter = {}
        if var == "foot":
            for s, d in self.near(la, lo, self.WALKMAX): ready[s] = t0+wt(d); arr[s] = ready[s]; ptr[s] = ("walk0", d, "walk")
        else:
            pen = PARK if var == "park" else BOARD
            for s, d in self.near(la, lo, self.BIKEMAX): ready[s] = t0+bt(d)+pen; arr[s] = ready[s]; ptr[s] = ("walk0", d, "bike")
        best = {k: (INF, None) for k, *_ in DEST}
        conns, deps = (self.rconns, self.rdeps) if var == "carry" else (self.conns, self.deps)
        bound = t0+3*3600
        for i in range(bisect.bisect_left(deps, t0), len(conns)):
            dep, ar, u, v, t = conns[i]
            if dep > bound: break
            if t not in enter:
                if ready.get(u, INF) <= dep and (not ban or self.routes[self.trips[t][0]][0] not in ban): enter[t] = conns[i]
                else: continue
            if ar < arr.get(v, INF):
                arr[v] = ar; ptr[v] = ("ride", enter[t], conns[i])
                if ar+CHG < ready.get(v, INF): ready[v] = ar+CHG
                for n, w in self.foot[v]:
                    if ar+w+60 < ready.get(n, INF):
                        ready[n] = ar+w+60
                        if ar+w < arr.get(n, INF): arr[n] = ar+w; ptr[n] = ("foot", v, w)
        eg, f = (self.begress, bt) if var == "carry" else (self.egress, wt)
        for k in eg:
            for s, d in eg[k].items():
                if s in arr and ptr[s][0] != "walk0":
                    x = arr[s]+f(d)
                    if x < best[k][0]: best[k] = (x, s)
        return best, ptr
    def build(self, la, lo, s, ptr, dname, dla, dlo, var="foot"):
        st = self.stops; em = "bike" if var == "carry" else "walk"; ef = bt if var == "carry" else wt
        legs = [{"m": em, "min": max(1, round(ef(hav(st[s][1], st[s][2], dla, dlo))/60)), "to": dname,
                 "path": [[st[s][1], st[s][2]], [dla, dlo]]}]
        for _ in range(60):
            p = ptr[s]
            if p[0] == "walk0":
                m = p[2]; legs.append({"m": m, "min": max(1, round((bt(p[1]) if m == "bike" else wt(p[1]))/60)), "to": st[s][0],
                                       "path": [[la, lo], [st[s][1], st[s][2]]], **({"park": 1} if var == "park" else {})}); break
            if p[0] == "foot":
                v = p[1]
                legs.append({"m": "walk", "min": max(1, round(p[2]/60)), "to": st[s][0], "xfer": 1,
                             "path": [[st[v][1], st[v][2]], [st[s][1], st[s][2]]]}); s = v; continue
            ci, co = p[1], p[2]; t = ci[4]; rid, head, sh = self.trips[t]; name, rt, _ = self.routes[rid]
            sq = [x[0] for x in self.seq[t]]; a = sq.index(ci[2]); b = sq.index(co[3], a)
            legs.append({"m": MODE.get(rt, "Bus"), "line": name, "head": head, "from": st[ci[2]][0], "to": st[co[3]][0],
                         "dep": hm(ci[0]), "arr": hm(co[1]), "n": b-a, "path": [[st[x][1], st[x][2]] for x in sq[a:b+1]], "shape": sh})
            s = ci[2]
        legs.reverse()
        out = []
        for L in legs:  # merge consecutive walks
            if out and L["m"] == "walk" and out[-1]["m"] == "walk" and not out[-1].get("park"):
                out[-1]["min"] += L["min"]; out[-1]["to"] = L["to"]; out[-1]["path"] += L["path"][1:]
            else: out.append(L)
        return out

t_lo, t_hi = sec(TIMES[0])-110*60, sec(TIMES[-1])
def finish(D, p, x):
    t0, e, s, ptr, name, dla, dlo, var = x
    legs = D.build(p["lat"], p["lon"], s, ptr, name, dla, dlo, var)
    rides = [L for L in legs if L["m"] not in ("walk", "bike")]
    if rides and legs[0]["m"] in ("walk", "bike"):  # leave just in time
        pen = (PARK if var == "park" else BOARD if var == "carry" else 0)
        t0 = max(t0, sec(rides[0]["dep"])-legs[0]["min"]*60-pen)
    return {"min": round((e-t0)/60), "sale": hm(t0), "llega": hm(e), "trans": max(0, len(rides)-1), "legs": legs}
def main_line(r):
    rides = [L for L in r["legs"] if L["m"] not in ("walk", "bike")]
    if not rides: return None
    return max(rides, key=lambda L: sec(L["arr"])-sec(L["dep"]))["line"]
def latest(D, p, Tl, vars_, k, ban, name, dla, dlo):
    """Latest departure (STEP grid) arriving by Tl while avoiding lines in ban. Binary search (arrival grows with departure)."""
    grid = list(range(t_lo, Tl+1, STEP)); lo, hi, found = 0, len(grid)-1, None
    def run(t0):
        bestx = None
        for var in vars_:
            b, ptr = D.csa(p["lat"], p["lon"], t0, var, ban)
            e, s = b[k]
            if e < 1e17 and (bestx is None or e < bestx[1]): bestx = (t0, e, s, ptr, name, dla, dlo, var)
        return bestx
    while lo <= hi:
        mid = (lo+hi)//2; x = run(grid[mid])
        if x and x[1] <= Tl: found = x; lo = mid+1
        else: hi = mid-1
    return found
def work(pkl):
    ymd = pkl.rsplit("_", 1)[-1].split(".")[0]
    dn = DAYN[datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])).weekday()]
    D = Day(pkl); res = []
    for lim in LIMITS:
        D.setup(lim)
        for p in P:
            for mo in MODES_OUT:
                vars_ = ["foot"] if mo == "foot" else ["carry"]   # bike: bike + train + bike
                samples = {k: [] for k, *_ in DEST}
                for t0 in range(t_lo, t_hi+1, STEP):
                    for var in vars_:
                        best, ptr = D.csa(p["lat"], p["lon"], t0, var)
                        for k, name, dla, dlo in DEST:
                            e, s = best[k]
                            if e < 1e17: samples[k].append((t0, e, s, ptr, name, dla, dlo, var))
                for T in TIMES:
                    Tl = sec(T)
                    for k, name, dla, dlo in DEST:
                        ok = [x for x in samples[k] if x[1] <= Tl]
                        if not ok: continue
                        best = max(ok, key=lambda x: (x[0], -x[1]))  # latest departure that arrives on time
                        opts = [finish(D, p, best)]
                        ban = set()
                        for _ in range(2):   # up to 2 alternatives avoiding the main line(s) of earlier options
                            ml = main_line(opts[-1])
                            if not ml: break
                            ban.add(ml); alt = latest(D, p, Tl, vars_, k, frozenset(ban), name, dla, dlo)
                            if not alt: break
                            r = finish(D, p, alt)
                            if r["min"] > opts[0]["min"]+25: break
                            if all(json.dumps(r["legs"]) != json.dumps(o["legs"]) for o in opts): opts.append(r)
                        res.append((dn, T, str(lim), mo, p["id"], k, opts))
        print(dn, lim, "done", flush=True)
    return dn, res

if __name__ == "__main__":
    routes, rindex, IX, days = [], {}, {}, []
    with multiprocessing.Pool(min(5, len(PREPS))) as pool:
        for dn, res in pool.map(work, PREPS):
            days.append(dn)
            for dn_, T, lim, mo, fid, k, opts in res:
                ids = []
                for r in opts:
                    key = json.dumps(r, sort_keys=True)
                    if key not in rindex: rindex[key] = len(routes); routes.append(r)
                    ids.append(rindex[key])
                IX.setdefault(mo, {}).setdefault(dn_, {}).setdefault(T, {}).setdefault(lim, {}).setdefault(fid, {})[k] = ids
    # nicer geometry from shapes.txt
    need = {L["shape"] for r in routes for L in r["legs"] if L.get("shape")}
    SH = collections.defaultdict(list)
    for r in csv.reader(open(SHAPES, encoding="utf-8-sig", newline="")):
        if r[0] in need: SH[r[0]].append((int(r[3]), float(r[1]), float(r[2])))
    def clip(pts, a, b):
        def idx(q, start):
            bi, bd = start, 1e18
            for i in range(start, len(pts)):
                d = (pts[i][0]-q[0])**2+((pts[i][1]-q[1])*0.56)**2
                if d < bd: bi, bd = i, d
            return bi, bd
        i, di = idx(a, 0); j, dj = idx(b, i)
        if di > 4e-6 or dj > 4e-6 or j <= i: return None
        return pts[i:j+1]
    paths, pindex = [], {}
    def pid(path):
        th = [path[0]]
        for q in path[1:]:
            if abs(q[0]-th[-1][0])+abs(q[1]-th[-1][1]) > 0.0002: th.append(q)
        if th[-1] != path[-1]: th.append(path[-1])
        th = [[round(a, 5), round(b, 5)] for a, b in th]; k = json.dumps(th)
        if k not in pindex: pindex[k] = len(paths); paths.append(th)
        return pindex[k]
    for r in routes:
        for L in r["legs"]:
            sh = L.pop("shape", None)
            if L["m"] not in ("walk", "bike"):
                pts = [(a, b) for _, a, b in sorted(SH.get(sh, []))]
                c = clip(pts, tuple(L["path"][0]), tuple(L["path"][-1])) if pts else None
                if c: L["path"] = [list(x) for x in c]
            L["p"] = pid(L.pop("path"))
    json.dump({"dest": [{"k": k, "name": n, "ll": [a, b]} for k, n, a, b in DEST], "days": days, "times": TIMES, "lim": LIMITS,
               "week": "26–30 oct 2026", "routes": routes, "paths": paths, "ix": IX},
              open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print("routes", len(routes), "paths", len(paths))
