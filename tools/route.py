"""Fastest public-transport routes from each flat to each destination.

Usage:
  python3 tools/route.py data/pisos.json data/rutas.json <gtfs>/shapes.txt prep_YYYYMMDD.pkl [more days...]

Each prep_*.pkl comes from tools/prep.py (one per weekday).
For every day x arrival time x walking limit it finds the LATEST departure that
still arrives before the chosen time (like Rejseplanen's "arrive by").
Engine: Connection Scan Algorithm on the Rejseplanen GTFS.
"""
import pickle, math, json, sys, bisect, collections, csv, datetime

PISOS, OUT, SHAPES, PREPS = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
P = json.load(open(PISOS))
DEST = [("gn", "GN · Lautrupbjerg 7, Ballerup", 55.738723, 12.387155),
        ("ns", "Nordic Solar · Strandvejen 102E, Hellerup", 55.733318, 12.575586),
        ("dtu", "DTU · Anker Engelunds Vej 1, Lyngby", 55.7858465, 12.5214443)]
TIMES = ["07:30", "08:00", "08:30", "09:00", "09:30", "10:00"]   # "llegar antes de"
LIMITS = [5, 10, 15, 20]          # max minutes walking per walking leg
V, DET, CHG = 1.33, 1.25, 90      # walk speed m/s, detour factor, change buffer s
STEP = 120                        # departure sampling step (s)
DAYN = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MODE = {109: "S-tog", 1: "Metro", 0: "Letbane", 2: "Tren", 3: "Bus", 700: "Bus", 4: "Ferry"}

def hav(a, b, c, d):
    R = 6371000; p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2-p1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(d-b)/2)**2
    return 2*R*math.asin(math.sqrt(x))
def wt(m): return m*DET/V
def hm(t): t = int(round(t/60)); return f"{t//60:02d}:{t%60:02d}"
def sec(s): h, m = s.split(":"); return int(h)*3600+int(m)*60

class Day:
    def __init__(self, pkl):
        D = pickle.load(open(pkl, "rb"))
        self.stops, self.conns, self.trips, self.routes, self.seq = D["stops"], D["conns"], D["trips"], D["routes"], D["seq"]
        self.deps = [c[0] for c in self.conns]
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
        self.WALKMAX = lim*60*V/DET; fp = min(self.WALKMAX, 640)
        self.foot = {s: [(n, wt(d)) for n, d in self.near(la, lo, fp) if n != s] for s, la, lo in self.SL}
        self.egress = {k: dict(self.near(la, lo, self.WALKMAX)) for k, _, la, lo in DEST}
    def csa(self, la, lo, t0):
        INF = 1e18; ready = {}; arr = {}; ptr = {}; enter = {}
        for s, d in self.near(la, lo, self.WALKMAX):
            ready[s] = t0+wt(d); arr[s] = ready[s]; ptr[s] = ("walk0", d)
        best = {}
        for k, _, a, b in DEST:
            d = hav(la, lo, a, b); best[k] = (t0+wt(d) if d <= self.WALKMAX else INF, None)
        conns = self.conns; bound = t0+3*3600
        for i in range(bisect.bisect_left(self.deps, t0), len(conns)):
            dep, ar, u, v, t = conns[i]
            if dep > bound: break
            if t not in enter:
                if ready.get(u, INF) <= dep: enter[t] = i
                else: continue
            if ar < arr.get(v, INF):
                arr[v] = ar; ptr[v] = ("ride", enter[t], i)
                if ar+CHG < ready.get(v, INF): ready[v] = ar+CHG
                for n, w in self.foot[v]:
                    if ar+w+60 < ready.get(n, INF):
                        ready[n] = ar+w+60
                        if ar+w < arr.get(n, INF): arr[n] = ar+w; ptr[n] = ("foot", v, w)
        for k in self.egress:
            for s, d in self.egress[k].items():
                if s in arr:
                    x = arr[s]+wt(d)
                    if x < best[k][0]: best[k] = (x, s)
        return best, ptr
    def build(self, la, lo, s, ptr, dname, dla, dlo):
        st = self.stops
        if s is None:
            return [{"m": "walk", "min": max(1, round(wt(hav(la, lo, dla, dlo))/60)), "to": dname, "path": [[la, lo], [dla, dlo]]}]
        legs = [{"m": "walk", "min": max(1, round(wt(hav(st[s][1], st[s][2], dla, dlo))/60)), "to": dname,
                 "path": [[st[s][1], st[s][2]], [dla, dlo]]}]
        for _ in range(60):
            p = ptr[s]
            if p[0] == "walk0":
                legs.append({"m": "walk", "min": max(1, round(wt(p[1])/60)), "to": st[s][0], "path": [[la, lo], [st[s][1], st[s][2]]]}); break
            if p[0] == "foot":
                v = p[1]
                legs.append({"m": "walk", "min": max(1, round(p[2]/60)), "to": st[s][0], "xfer": 1,
                             "path": [[st[v][1], st[v][2]], [st[s][1], st[s][2]]]}); s = v; continue
            ci, co = self.conns[p[1]], self.conns[p[2]]; t = ci[4]; rid, head, sh = self.trips[t]; name, rt, _ = self.routes[rid]
            sq = [x[0] for x in self.seq[t]]; a = sq.index(ci[2]); b = sq.index(co[3], a)
            legs.append({"m": MODE.get(rt, "Bus"), "line": name, "head": head, "from": st[ci[2]][0], "to": st[co[3]][0],
                         "dep": hm(ci[0]), "arr": hm(co[1]), "n": b-a, "path": [[st[x][1], st[x][2]] for x in sq[a:b+1]], "shape": sh})
            s = ci[2]
        legs.reverse()
        out = []
        for L in legs:  # merge consecutive walks
            if out and L["m"] == "walk" and out[-1]["m"] == "walk":
                out[-1]["min"] += L["min"]; out[-1]["to"] = L["to"]; out[-1]["path"] += L["path"][1:]
            else: out.append(L)
        return out

routes, rindex = [], {}          # unique routes (deduplicated across day/time/limit)
IX = {}                          # IX[day][time][limit][flat][dest] = route index
days = []
t_lo, t_hi = sec(TIMES[0])-110*60, sec(TIMES[-1])
for pkl in PREPS:
    ymd = pkl.rsplit("_", 1)[-1].split(".")[0]
    dn = DAYN[datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])).weekday()]
    days.append(dn); D = Day(pkl); IX[dn] = {T: {} for T in TIMES}
    for lim in LIMITS:
        D.setup(lim)
        for p in P:
            # scan departures; keep (t0, arrival, legs-builder) per dest
            samples = {k: [] for k, *_ in DEST}
            for t0 in range(t_lo, t_hi+1, STEP):
                best, ptr = D.csa(p["lat"], p["lon"], t0)
                for k, name, dla, dlo in DEST:
                    e, s = best[k]
                    if e < 1e17: samples[k].append((t0, e, s, ptr, name, dla, dlo))
            for T in TIMES:
                Tl = sec(T); row = IX[dn][T].setdefault(str(lim), {}).setdefault(p["id"], {})
                for k in samples:
                    ok = [x for x in samples[k] if x[1] <= Tl]
                    if not ok: continue
                    t0, e, s, ptr, name, dla, dlo = max(ok, key=lambda x: (x[0], -x[1]))  # latest departure
                    legs = D.build(p["lat"], p["lon"], s, ptr, name, dla, dlo)
                    rides = [L for L in legs if L["m"] != "walk"]
                    if rides and legs[0]["m"] == "walk":  # leave just in time for the first vehicle
                        t0 = max(t0, sec(rides[0]["dep"])-legs[0]["min"]*60)
                    r = {"min": round((e-t0)/60), "sale": hm(t0), "llega": hm(e), "trans": max(0, len(rides)-1), "legs": legs}
                    key = json.dumps(r, sort_keys=True)
                    if key not in rindex: rindex[key] = len(routes); routes.append(r)
                    row[k] = rindex[key]
        print(dn, lim, "routes so far:", len(routes), flush=True)

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
        if L["m"] != "walk":
            pts = [(a, b) for _, a, b in sorted(SH.get(sh, []))]
            c = clip(pts, tuple(L["path"][0]), tuple(L["path"][-1])) if pts else None
            if c: L["path"] = [list(x) for x in c]
        L["p"] = pid(L.pop("path"))
json.dump({"dest": [{"k": k, "name": n, "ll": [a, b]} for k, n, a, b in DEST], "days": days, "times": TIMES, "lim": LIMITS,
           "week": "26–30 oct 2026", "routes": routes, "paths": paths, "ix": IX},
          open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
print("routes", len(routes), "paths", len(paths))
