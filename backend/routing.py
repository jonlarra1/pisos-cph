"""Motor de rutas en transporte público para pisos-api (solo stdlib).

- preparar(zip_gtfs, salida): lee el GTFS de Rejseplanen (sin descomprimir al disco), se queda con la zona de
  Copenhague y guarda un archivo compacto (pickle) con paradas, líneas, viajes y calendario.
- Motor(compacto): carga ese archivo; para cada fecha construye las conexiones del día (caché de 3 fechas) y
  responde búsquedas «llegar antes de» / «salir a las» con perfiles andando o bici (bici solo con S-tog, tren, Letbane).
Algoritmo: Connection Scan Algorithm (CSA), igual que tools/route.py.
"""
import array, bisect, collections, csv, datetime, io, json, math, os, pickle, threading, time, zipfile

BBOX = (55.55, 55.86, 12.20, 12.72)            # zona de Copenhague
MODE = {109: "S-tog", 1: "Metro", 0: "Letbane", 2: "Tren", 3: "Bus", 700: "Bus", 4: "Ferry"}
RAILBIKE = {109, 2, 0}                         # con bici: S-tog, tren regional/local y Letbane
DET_W, DET_B = 1.25, 1.30                      # factor de rodeo andando / en bici
CHG = 90                                       # margen de transbordo (s)
FOOTPATH_M = 400                               # transbordos a pie entre paradas (m)


def hav(a, b, c, d):
    R = 6371000; p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2-p1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(d-b)/2)**2
    return 2*R*math.asin(math.sqrt(x))


def hm(t):
    t = int(round(t/60)); return f"{(t//60) % 24:02d}:{t % 60:02d}"


# ------------------------------------------------------------------ preparación (semanal)
def preparar(zip_path, out_path):
    t0 = time.time()
    z = zipfile.ZipFile(zip_path)
    def rd(name):
        return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline=""))
    stops, sidx = [], {}
    for r in rd("stops.txt"):
        la, lo = float(r["stop_lat"]), float(r["stop_lon"])
        if BBOX[0] <= la <= BBOX[1] and BBOX[2] <= lo <= BBOX[3]:
            sidx[r["stop_id"]] = len(stops); stops.append((r["stop_name"], round(la, 6), round(lo, 6)))
    routes = {r["route_id"]: (r["route_short_name"], int(r["route_type"])) for r in rd("routes.txt")}
    cal = {r["service_id"]: (tuple(int(r[d]) for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")),
                             r["start_date"], r["end_date"]) for r in rd("calendar.txt")}
    exc = collections.defaultdict(dict)
    for r in rd("calendar_dates.txt"):
        exc[r["service_id"]][r["date"]] = r["exception_type"]
    tinfo = {}
    for r in rd("trips.txt"):
        tinfo[r["trip_id"]] = (r["route_id"], r["trip_headsign"], r["service_id"])
    # stop_times: agrupar por viaje solo las paradas dentro de la zona
    trips = []
    def cerrar(tid, L):
        if len(L) < 2: return
        L.sort(); rid, head, svc = tinfo[tid]
        name, rtype = routes.get(rid, ("?", 3))
        trips.append((name, rtype, head, svc, array.array("I", [x[1] for x in L]), array.array("i", [x[2] for x in L]),
                      array.array("i", [x[3] for x in L]), bytes(x[4] for x in L)))
    f = io.TextIOWrapper(z.open("stop_times.txt"), encoding="utf-8-sig", newline="")
    rr = csv.reader(f); hdr = next(rr); ix = {h: i for i, h in enumerate(hdr)}
    it, ia, idp, isd, iq = ix["trip_id"], ix["arrival_time"], ix["departure_time"], ix["stop_id"], ix["stop_sequence"]
    ipu, ido = ix.get("pickup_type"), ix.get("drop_off_type")
    def ts(x):
        h, m, s = x.split(":"); return int(h)*3600+int(m)*60+int(s)
    cur, L, hechos = None, [], set()
    for row in rr:
        tid = row[it]
        if tid != cur:
            if cur is not None and cur not in hechos: cerrar(cur, L); hechos.add(cur)
            cur, L = tid, []
        s = sidx.get(row[isd])
        if s is None or tid not in tinfo: continue
        flags = (1 if ipu is not None and row[ipu] == "1" else 0) | (2 if ido is not None and row[ido] == "1" else 0)
        L.append((int(row[iq]), s, ts(row[ia]), ts(row[idp]), flags))
    if cur is not None and cur not in hechos: cerrar(cur, L)
    del tinfo
    dates = sorted(d for _, a, b in cal.values() for d in (a, b))
    data = {"version": 1, "creado": time.strftime("%Y-%m-%dT%H:%M:%S"), "stops": stops, "trips": trips, "cal": cal,
            "exc": dict(exc), "desde": dates[0] if dates else "", "hasta": dates[-1] if dates else ""}
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fo: pickle.dump(data, fo, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, out_path)
    return {"paradas": len(stops), "viajes": len(trips), "seg": round(time.time()-t0, 1), "desde": data["desde"], "hasta": data["hasta"]}


# ------------------------------------------------------------------ motor
class Motor:
    def __init__(self, path):
        with open(path, "rb") as f: d = pickle.load(f)
        self.creado, self.desde, self.hasta = d["creado"], d["desde"], d["hasta"]
        self.stops, self.trips, self.cal, self.exc = d["stops"], d["trips"], d["cal"], d["exc"]
        self.grid = collections.defaultdict(list)
        for i, (_, la, lo) in enumerate(self.stops):
            self.grid[(int(la*100), int(lo*60))].append(i)
        self.foot = [[(n, dm) for n, dm in self.near(la, lo, FOOTPATH_M) if n != i] for i, (_, la, lo) in enumerate(self.stops)]
        self._days, self._lock = collections.OrderedDict(), threading.Lock()

    def near(self, la, lo, r):
        out = []
        for i in range(int(la*100)-2, int(la*100)+3):
            for j in range(int(lo*60)-2, int(lo*60)+3):
                for s in self.grid.get((i, j), ()):
                    d = hav(la, lo, self.stops[s][1], self.stops[s][2])
                    if d <= r: out.append((s, d))
        return out

    def activos(self, ymd):
        dt = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])); wd = dt.weekday(); on = set()
        for sid, (days, a, b) in self.cal.items():
            if a <= ymd <= b and days[wd]: on.add(sid)
        for sid, ex in self.exc.items():
            e = ex.get(ymd)
            if e == "1": on.add(sid)
            elif e == "2": on.discard(sid)
        return on

    def dia(self, ymd):
        """Conexiones del día (incluye los viajes del día anterior que pasan de medianoche). Caché de 3 días."""
        with self._lock:
            if ymd in self._days:
                self._days.move_to_end(ymd); return self._days[ymd]
        prev = (datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])) - datetime.timedelta(days=1)).strftime("%Y%m%d")
        conns = []
        for shift, svc in ((0, self.activos(ymd)), (-86400, self.activos(prev))):
            for ti, (name, rtype, head, s, st, arr, dep, fl) in enumerate(self.trips):
                if s not in svc: continue
                if shift and dep[-1] < 86400: continue
                for k in range(len(st)-1):
                    d0 = dep[k] + shift
                    if d0 < 0 or (fl[k] & 1) or (fl[k+1] & 2): continue
                    conns.append((d0, arr[k+1]+shift, st[k], st[k+1], ti, k))
        conns.sort()
        rail = [c for c in conns if self.trips[c[4]][1] in RAILBIKE]
        day = {"all": (conns, [c[0] for c in conns]), "rail": (rail, [c[0] for c in rail])}
        with self._lock:
            self._days[ymd] = day
            while len(self._days) > 3: self._days.popitem(last=False)
        return day

    def csa(self, day, la, lo, dest, t0, pf, ban=frozenset(), bound=None):
        """Llegada más temprana saliendo a t0. pf: dict(mode, v_walk, v_bike, lim_min). Devuelve (llegada, s_final, ptr, var)."""
        INF = 1e18; vw, vb = pf["v_walk"], pf["v_bike"]
        wt = lambda m: m*DET_W/vw; bt = lambda m: m*DET_B/vb
        walkmax = pf["lim_min"]*60*vw/DET_W; bikemax = pf["lim_min"]*60*vb/DET_B
        variants = ["foot"] if pf["mode"] == "foot" else ["carry"]
        best = (INF, None, None, None)
        for var in variants:
            conns, deps = day["rail" if var == "carry" else "all"]
            ready, arr, ptr, enter = {}, {}, {}, {}
            acc, accf = (bikemax, bt) if var == "carry" else (walkmax, wt)
            for s, d in self.near(la, lo, acc):
                ready[s] = t0 + accf(d); arr[s] = ready[s]; ptr[s] = ("ini", d)
            egress = self.near(dest[0], dest[1], acc)
            lim_t = bound if bound is not None else t0 + 3*3600
            trips, foot, rget, aget = self.trips, self.foot, ready.get, arr.get
            for i in range(bisect.bisect_left(deps, t0), len(conns)):
                c = conns[i]; dep, ar, u, v, t, k = c
                if dep > lim_t: break
                if t not in enter:
                    if rget(u, INF) <= dep and (not ban or trips[t][0] not in ban): enter[t] = c
                    else: continue
                if ar < aget(v, INF):
                    arr[v] = ar; ptr[v] = ("ride", enter[t], c)
                    if ar+CHG < rget(v, INF): ready[v] = ar+CHG
                    for n, dm in foot[v]:
                        w = wt(dm)
                        if ar+w+60 < rget(n, INF):
                            ready[n] = ar+w+60
                            if ar+w < aget(n, INF): arr[n] = ar+w; ptr[n] = ("foot", v, dm)
            for s, d in egress:
                if s in arr and ptr[s][0] != "ini":
                    x = arr[s] + accf(d)
                    if x < best[0]: best = (x, s, ptr, var, d)
        return best

    def legs(self, la, lo, dest, dname, res, pf):
        e, s, ptr, var, dfin = res
        st = self.stops; vw, vb = pf["v_walk"], pf["v_bike"]
        mv = "bike" if var == "carry" else "walk"; spd = vb if var == "carry" else vw; det = DET_B if var == "carry" else DET_W
        mins = lambda d, sp=spd, de=det: max(1, round(d*de/sp/60))
        out = [{"m": mv, "min": mins(dfin), "d": round(dfin*det), "to": dname, "path": [[st[s][1], st[s][2]], list(dest)]}]
        for _ in range(80):
            p = ptr[s]
            if p[0] == "ini":
                out.append({"m": mv, "min": mins(p[1]), "d": round(p[1]*det), "to": st[s][0], "path": [[la, lo], [st[s][1], st[s][2]]]}); break
            if p[0] == "foot":
                v = p[1]; out.append({"m": "walk", "min": mins(p[2], vw, DET_W), "d": round(p[2]*DET_W), "to": st[s][0], "xfer": 1,
                                      "path": [[st[v][1], st[v][2]], [st[s][1], st[s][2]]]}); s = v; continue
            ci, co = p[1], p[2]; tr = self.trips[ci[4]]
            k0, k1 = ci[5], co[5]+1
            out.append({"m": MODE.get(tr[1], "Bus"), "line": tr[0], "head": tr[2], "from": st[ci[2]][0], "to": st[co[3]][0],
                        "dep": hm(ci[0]), "arr": hm(co[1]), "n": k1-k0,
                        "path": [[st[x][1], st[x][2]] for x in tr[4][k0:k1+1]]})
            s = ci[2]
        out.reverse()
        return out

    def ruta(self, la, lo, dest, dname, fecha, hora, modo, pf, alternativas=3):
        """fecha 'YYYY-MM-DD', hora 'HH:MM', modo 'llegar' | 'salir'. Devuelve lista de opciones (la más rápida primero)."""
        ymd = fecha.replace("-", ""); day = self.dia(ymd)
        h, m = hora.split(":"); T = int(h)*3600+int(m)*60
        def una(ban):
            if modo == "salir":
                r = self.csa(day, la, lo, dest, T, pf, ban); return (T, r) if r[1] is not None else None
            lo_i, hi_i, found = 0, 90, None                     # «llegar antes de»: salida más tarde (pasos de 2 min, 3 h)
            while lo_i <= hi_i:
                mid = (lo_i+hi_i)//2; t0 = T - (90-mid)*120
                r = self.csa(day, la, lo, dest, t0, pf, ban, bound=T)
                if r[1] is not None and r[0] <= T: found = (t0, r); lo_i = mid+1
                else: hi_i = mid-1
            return found
        opts, ban = [], set()
        for _ in range(alternativas):
            x = una(frozenset(ban))
            if not x: break
            t0, r = x; L = self.legs(la, lo, dest, dname, r, pf)
            rides = [l for l in L if l["m"] not in ("walk", "bike")]
            if not rides: break
            sale = (int(rides[0]["dep"][:2])*3600+int(rides[0]["dep"][3:])*60) - L[0]["min"]*60 if L[0]["m"] in ("walk", "bike") else t0
            if modo == "salir": sale = max(sale, T)
            o = {"min": round((r[0]-sale)/60), "sale": hm(sale), "llega": hm(r[0]), "trans": len(rides)-1, "legs": L}
            if not opts or o["min"] <= opts[0]["min"]+25:
                if all(json.dumps(o["legs"]) != json.dumps(p["legs"]) for p in opts): opts.append(o)
            main = max(rides, key=lambda l: (int(l["arr"][:2])*60+int(l["arr"][3:])) - (int(l["dep"][:2])*60+int(l["dep"][3:])))
            ban.add(main["line"])
        # sin transporte: ir directo en bici / andando. Se ofrece si cabe en el máximo por tramo o si tarda como mucho
        # 5 min más que la mejor con transporte (antes una ruta de 5 min en bici salía como «bici + tren + bici»).
        # Si se ofrece, sobran las opciones con transporte que no son más rápidas que ir directo.
        d = self.directo(la, lo, dest, dname, pf)
        mejor = min((o["min"] for o in opts), default=None)
        if mejor is None or d["min"] <= pf["lim_min"] or d["min"] <= mejor + 5:
            sale = T - d["min"]*60 if modo == "llegar" else T
            opts = [o for o in opts if o["min"] < d["min"]]
            opts.append({**d, "sale": hm(sale), "llega": hm(sale + d["min"]*60)})
        opts.sort(key=lambda o: o["min"])
        return opts

    def directo(self, la, lo, dest, dname, pf):
        """Ruta sin transporte (línea recta × factor de rodeo, como los tramos de acceso)."""
        bici = pf["mode"] != "foot"
        dist = hav(la, lo, dest[0], dest[1]); det = DET_B if bici else DET_W; v = pf["v_bike"] if bici else pf["v_walk"]
        mins = max(1, round(dist*det/v/60))
        return {"min": mins, "trans": 0, "solo": "bike" if bici else "walk",
                "legs": [{"m": "bike" if bici else "walk", "min": mins, "d": round(dist*det), "to": dname, "path": [[la, lo], list(dest)]}]}
