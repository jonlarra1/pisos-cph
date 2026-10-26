import csv,sys,pickle,collections
F=sys.argv[1]; OUT=sys.argv[2]
DATE=sys.argv[3] if len(sys.argv)>3 else "20261027"
import datetime; WD=datetime.date(int(DATE[:4]),int(DATE[4:6]),int(DATE[6:])).strftime("%A").lower()
BB=(55.55,55.86,12.20,12.72)
T0,T1=5*3600+30*60, 11*3600
def rd(n): return csv.DictReader(open(f"{F}/{n}",encoding="utf-8-sig",newline=""))
svc=set()
for r in rd("calendar.txt"):
    if r[WD]=="1" and r["start_date"]<=DATE<=r["end_date"]: svc.add(r["service_id"])
for r in rd("calendar_dates.txt"):
    if r["date"]==DATE:
        (svc.add if r["exception_type"]=="1" else svc.discard)(r["service_id"])
routes={r["route_id"]:(r["route_short_name"],int(r["route_type"]),r["agency_id"]) for r in rd("routes.txt")}
trips={}
for r in rd("trips.txt"):
    if r["service_id"] in svc: trips[r["trip_id"]]=(r["route_id"],r["trip_headsign"],r["shape_id"])
stops={}
for r in rd("stops.txt"):
    la,lo=float(r["stop_lat"]),float(r["stop_lon"])
    if BB[0]<=la<=BB[1] and BB[2]<=lo<=BB[3]: stops[r["stop_id"]]=(r["stop_name"],la,lo,r["parent_station"])
def ts(x): h,m,s=x.split(":"); return int(h)*3600+int(m)*60+int(s)
seq=collections.defaultdict(list)
f=open(f"{F}/stop_times.txt",encoding="utf-8-sig",newline=""); rr=csv.reader(f); next(rr)
for t,a,d,s,q,pu,do,_ in rr:
    if t in trips and s in stops: seq[t].append((int(q),s,ts(a),ts(d),pu,do))
conns=[]
for t,L in seq.items():
    L.sort()
    for x,y in zip(L,L[1:], strict=False):
        if x[4]=="1" or y[5]=="1": continue
        if T0<=x[3]<=T1: conns.append((x[3],y[2],x[1],y[1],t))
conns.sort()
used={t for *_,t in conns}
types=collections.Counter(routes[trips[t][0]][1] for t in used)
print("svc",len(svc),"trips",len(used),"stops",len(stops),"conns",len(conns),"types",types)
pickle.dump({"stops":stops,"conns":conns,"trips":{t:trips[t] for t in used},"routes":routes,
             "seq":{t:[(s,a,d) for _,s,a,d,_,_ in seq[t]] for t in used}},open(OUT,"wb"))
