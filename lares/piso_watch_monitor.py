#!/usr/bin/env python3
"""piso_watch_monitor.py — vigilante de alquiler en Copenhague para Jon y Unai (v2, 2026-10-21).

Corre en el cron de Lares cada 3 h, sin LLM. Pasos:
 1. Lee config.json del buzón compartido con el mapa (pisos-bridge/): filtros, zonas
    permitidas (editables desde la web) y pisos que ya tienen estado.
 2. Lee el mapa publicado (pisos.json + descartados.json en GitHub Pages) para no repetir.
 3. Descarga los listados de Lejebolig y Boligportal (páginas adaptativas, ver PAGINAS) y, solo para los
    que pasan el primer filtro, abre la ficha (máx. MAX_DETAIL por pasada, con caché).
 4. Aplica los filtros en código y escribe:
      candidatos.json  -> los que pasan (el mapa los muestra como «Propuestos por Lares»)
      rechazados.json  -> últimos rechazados con el motivo exacto
 5. Imprime solo los candidatos NUEVOS (salida vacía = el agente no se despierta).
"""
import json, os, re, sqlite3, tempfile, urllib.request
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
BASE = Path(os.environ.get("PISO_WATCH_BASE", "/opt/data/vault/Vivienda/piso-watch"))      # variables solo para los tests
BRIDGE = Path(os.environ.get("PISOS_BRIDGE", "/opt/data/vault/Vivienda/pisos-bridge"))
DB = BASE / "pisos.db"
MAP_URLS = ["https://jonlarra1.github.io/pisos-cph/data/pisos.json",
            "https://jonlarra1.github.io/pisos-cph/data/descartados.json"]
# Los portales ordenan por más nuevo. Páginas adaptativas: como mínimo MIN_PAGES (lo de siempre) y se sigue
# mientras salgan anuncios que Lares no ha visto nunca, hasta MAX_PAGES. Con pasadas cada 3 h casi siempre
# se para en el mínimo; si un día entran muchos anuncios, no se pierden los que bajan de página.
PAGINAS = {   # portal: (url de la página i, mínimo, máximo)
    "lejebolig": (lambda i: "https://en.lejebolig.dk/lejeboliger/koebenhavn" + (f"?page={i+1}" if i else ""), 2, 6),
    "boligportal": (lambda i: f"https://www.boligportal.dk/lejligheder/k%C3%B8benhavn/?max_monthly_rent=23000&offset={18*i}", 3, 8),
}
RECUERDO_DIAS = 7                    # un candidato que ya no sale en las páginas leídas se sigue proponiendo estos días
MAX_DETAIL = 8                       # fichas abiertas por pasada (ritmo suave)
DEFAULT_FILTROS = {"precio_max": 23000, "vaer_min": 4, "disp_desde": "2026-12-18", "disp_hasta": "2027-01-15"}
BASE.mkdir(parents=True, exist_ok=True)
BRIDGE.mkdir(parents=True, exist_ok=True)
STATS = {}                           # resumen de la pasada para lares_runs.jsonl

# Texto -> zona del mapa. Las claves más largas se prueban primero.
ZONA_ALIAS = {
    "københavn ø": "Østerbro", "kobenhavn o": "Østerbro", "østerbro": "Østerbro", "osterbro": "Østerbro",
    "københavn s": "Amager", "amager": "Amager", "islands brygge": "Islands Brygge", "ørestad": "Ørestad", "orestad": "Ørestad",
    "holmen": "Holmen", "margretheholm": "Holmen", "strandlodsvej": "Amager",
    "københavn n": "Nørrebro", "nørrebro": "Nørrebro", "norrebro": "Nørrebro",
    "københavn nv": "Nordvest", "nordvest": "Nordvest", "bispebjerg": "Bispebjerg",
    "københavn v": "Vesterbro", "vesterbro": "Vesterbro", "københavn sv": "Sydhavnen", "sydhavn": "Sydhavnen",
    "teglholmen": "Sydhavnen", "sluseholmen": "Sydhavnen", "københavn k": "Indre By", "indre by": "Indre By",
    "frederiksberg": "Frederiksberg", "valby": "Valby", "vanløse": "Vanløse", "vanlose": "Vanløse",
    "brønshøj": "Brønshøj", "bronshoj": "Brønshøj", "husum": "Husum", "herlev": "Herlev", "søborg": "Søborg",
    "soborg": "Søborg", "buddinge": "Buddinge", "gladsaxe": "Gladsaxe", "bagsværd": "Bagsværd", "lyngby": "Lyngby",
    "virum": "Virum", "hellerup": "Hellerup", "gentofte": "Gentofte", "charlottenlund": "Charlottenlund",
    "klampenborg": "Klampenborg", "rødovre": "Rødovre", "rodovre": "Rødovre", "ballerup": "Ballerup",
    "skovlunde": "Skovlunde", "måløv": "Måløv", "malov": "Måløv", "glostrup": "Glostrup", "hvidovre": "Hvidovre",
}
BAD_TITLE = re.compile(r"\broom for\b|\broom in\b|\bværelse til\b|student|kollegie|\bshared room\b", re.I)
NO_SHARE = re.compile(r"bofællesskab ikke|ikke bofællesskab|ikke delevenlig|ikke egnet til deling|no shar|"
                      r"not suitable for shar|kun til par|kun par|kun familie|only for couples|only famil|"
                      r"kun virksomhed|only compan|corporate only|expats? only|only expats?", re.I)
YES_SHARE = re.compile(r"delevenlig|bofællesskab mulig|egnet til deling|suitable for shar|sharing (is )?(possible|allowed|ok)", re.I)
SHORT = re.compile(r"(max(imum)?|maks(imalt)?)\.?\s*(1[0-2]|[1-9])\s*(måneder|months|mdr)|tidsbegrænset|limited period|"
                   r"short[- ]term|korttids", re.I)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "da,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.stem, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def num_id(url_or_id):
    m = re.search(r"lejebolig/(\d+)|id-(\d+)|rental-apartments/(\d+)|^(?:lb|bp)?(\d+)$", str(url_or_id))
    return next((g for g in m.groups() if g), None) if m else None


# ------------------------------------------------------------------ listados
def parse_lejebolig(html):
    out = {}
    for m in re.finditer(r'id="lease-(\d+)"(.*?)(?=id="lease-\d+"|</body|$)', html, re.S):
        lid, block = m.group(1), m.group(2)
        title_m = re.search(r"<h2>(.*?)</h2>", block, re.S)
        sub_m = re.search(r'lease-sub-header">\s*<div>(.*?)</div>', block, re.S)
        price_m = re.search(r'class="rent[^"]*"><div>([\d,\.]+)', block) or re.search(r"([0-9][0-9,\.]*)\s*kr", block)
        if not (title_m and price_m):
            continue
        def spec(icon):
            mm = re.search(rf'resultat-stat-{icon}[^>]*>\s*<span>\s*([\d.,]+)\s*</span>', block, re.S)
            return int(float(mm.group(1).replace(",", "."))) if mm else None
        out[lid] = {"portal": "Lejebolig", "id": lid, "link": f"https://en.lejebolig.dk/lejebolig/{lid}",
                    "titulo": unescape(re.sub(r"\s+", " ", title_m.group(1)).strip()),
                    "lugar": unescape(re.sub(r"<[^>]+>", "", sub_m.group(1)).strip()) if sub_m else "",
                    "precio": int(re.sub(r"[,.\-]", "", price_m.group(1))), "m2": spec("areal"), "vaer": spec("vaerelser")}
    return out


def parse_boligportal(html):
    """BoligPortal trae los anuncios en <script id="store" type="application/json">."""
    m = re.search(r'<script id="store" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return {}
    store = json.loads(m.group(1))
    ads = []
    def walk(o):
        if isinstance(o, dict):
            if "monthly_rent" in o and "id" in o and "url" in o:
                ads.append(o); return
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(store)
    out = {}
    num = lambda v: int(float(v)) if isinstance(v, (int, float)) else None
    for a in ads:
        lid = str(a["id"]); loc = a.get("location") or {}
        area, street = a.get("city_area") or a.get("city_level_2") or "", a.get("street_name") or ""
        url = a.get("url") or ""
        out[lid] = {"portal": "Boligportal", "id": lid,
                    "link": ("https://www.boligportal.dk" + url) if url.startswith("/") else url,
                    "titulo": a.get("title") or f"{street}, {area}", "lugar": f"{street} {a.get('street_number') or ''} {a.get('postal_code') or ''} {area}".strip(),
                    "precio": num(a.get("monthly_rent")) or 0, "aconto": num(a.get("monthly_rent_extra_costs")),
                    "dep": num(a.get("deposit")), "pre": num(a.get("prepaid_rent")),
                    "m2": num(a.get("size_m2")), "vaer": num(a.get("rooms")),
                    "disp": (a.get("available_from") or "")[:10] or None, "contrato_meses": num(a.get("rental_period")),
                    "lat": loc.get("lat"), "lon": loc.get("lng") or loc.get("lon"),
                    "plano": next((i.get("url") for i in (a.get("images") or []) if isinstance(i, dict) and i.get("is_floor_plan")), None),
                    "texto": (a.get("description") or "")[:4000]}
    return out


def detalle(item):
    """Abre la ficha para sacar contrato y texto (y fecha si la hay)."""
    h = fetch(item["link"], timeout=15)
    t = unescape(re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S))
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
    d = {"texto": t[:6000]}
    # depósito y prepago por separado (Lejebolig: «an advance rent payment of DKK X and a security deposit of DKK Y» / «Deposit: X»)
    kr = lambda m: int(re.sub(r"\D", "", next(g for g in m.groups() if g))) if m else None
    d["pre"] = kr(re.search(r"Rent paid in advance:\s*([\d,.]+)", t)) or kr(re.search(r"advance rent payment of DKK ([\d,.]+)|upfront rent payment of DKK ([\d,.]+)|[Ff]orudbetalt leje:?\s*(?:DKK|kr\.?)?\s*([\d.,]+)", t))
    # la ficha técnica («Deposit: X») manda; la frase automática a veces trae mal el depósito
    d["dep"] = kr(re.search(r"\bDeposit:\s*([\d,.]+)|[Dd]epositum:?\s*(?:DKK|kr\.?)?\s*([\d.,]+)", t)) or \
        kr(re.search(r"security deposit of DKK ([\d,.]+)", t))
    mp = re.search(r"Rental period\s+(\w+)|Lejeperiode\s+(\w+)", t)
    if mp:
        v = (mp.group(1) or mp.group(2)).lower()
        d["contrato_meses"] = 0 if v in ("unlimited", "ubegrænset") else (int(re.sub(r"\D", "", v)) if re.search(r"\d", v) else None)
    md = re.search(r'"available_from"\s*:\s*"(\d{4}-\d{2}-\d{2})', h) or \
        re.search(r"(?:Available from|Ledig fra|Overtagelse)\s*:?\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})", t)
    if md:
        d["disp"] = md.group(1) if md.lastindex == 1 else f"{md.group(3)}-{int(md.group(2)):02d}-{int(md.group(1)):02d}"
    elif re.search(r"(Available from|Ledig fra)\s*:?\s*(As soon as possible|Snarest|Immediately|Straks)", t, re.I):
        d["disp"] = "ya"
    return d


# ------------------------------------------------------------------ filtros
def zona_de(item):
    hay = f"{item.get('titulo','')} {item.get('lugar','')}".lower()
    for k in sorted(ZONA_ALIAS, key=len, reverse=True):
        if re.search(rf"(?<![a-zæøå]){re.escape(k)}(?![a-zæøå])", hay):
            return ZONA_ALIAS[k]
    return None


def evaluar(item, F, zonas):
    """(ok, motivo_o_None, avisos)"""
    avisos = []
    if BAD_TITLE.search(item.get("titulo", "")):
        return False, "es una habitación / residencia", avisos
    if item["precio"] > F["precio_max"]:
        return False, f"precio {item['precio']} > {F['precio_max']}", avisos
    if item.get("vaer") is not None and item["vaer"] < F["vaer_min"]:
        return False, f"{item['vaer']} værelser (< {F['vaer_min']}: menos de 3 dormitorios)", avisos
    if item.get("vaer") is None:
        avisos.append("værelser sin dato")
    z = zona_de(item); item["zona"] = z
    if z is None:
        return False, "zona no reconocida / fuera de las vigiladas", avisos
    if not zonas.get(z, False):
        return False, f"zona excluida: {z}", avisos
    disp = item.get("disp")
    if disp == "ya":
        return False, "libre ya (no hacia el 1-1-2027)", avisos
    if disp:
        if not (F["disp_desde"] <= disp <= F["disp_hasta"]):
            return False, f"libre el {disp} (fuera de {F['disp_desde']}..{F['disp_hasta']})", avisos
    else:
        avisos.append("fecha de entrada sin confirmar")
    cm = item.get("contrato_meses")
    txt = item.get("texto", "")
    if F.get("contrato_largo", True) and ((cm and 0 < cm <= 12) or SHORT.search(txt)):
        return False, f"contrato corto ({cm or '≤12'} meses)", avisos
    if F.get("compartir", True):
        if NO_SHARE.search(txt):
            return False, "no permite compartir (texto del anuncio)", avisos
        if not YES_SHARE.search(txt):
            avisos.append("compartir sin confirmar")
    pl = item.get("plano_dorm")              # lo escribe Lares (IA) en planos.json tras mirar el plano
    dmin = int(F.get("dorm_min", 3))
    if isinstance(pl, int):
        if pl < dmin:
            return False, f"plano: {pl} dormitorios", avisos
        avisos.append(f"plano revisado: {pl} dormitorios")
    elif item.get("plano"):
        avisos.append("plano pendiente de revisar")
    else:
        avisos.append(f"sin plano: {dmin} dormitorios por confirmar")
    return True, None, avisos


# ---------------------------------------------------------------------- main
def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        cfg = json.loads((BRIDGE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    F = {**DEFAULT_FILTROS, **(cfg.get("filtros") or {})}
    if "dorm_min" in F: F["vaer_min"] = int(F["dorm_min"]) + 1
    zonas = cfg.get("zonas") or {}
    try:   # {"<id>": {"dorm": 3, "nota": "..."}} — revisión de planos hecha por Lares (IA)
        planos = json.loads((BRIDGE / "planos.json").read_text(encoding="utf-8"))
    except Exception:
        planos = {}
    # Lo decidido en la web (gusta/contactado/descartado) se sigue evaluando y escribiendo: el backend
    # lo oculta. Así, si alguien lo vuelve a poner en «nuevo», reaparece al momento.
    decididos = {str(k) for k, v in (cfg.get("con_estado") or {}).items() if (v or {}).get("st") != "nuevo"}
    known = set()
    for u in MAP_URLS:                         # pisos ya en el mapa o en la lista fija de descartados
        try:
            for x in json.loads(fetch(u)):
                i = num_id(x.get("link", ""))
                if i: known.add(i)
                for extra in re.findall(r"https?://\S+", x.get("notas", "") or ""):
                    j = num_id(extra)
                    if j: known.add(j)
        except Exception:
            pass

    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS v2_detalle (id TEXT PRIMARY KEY, data TEXT, ts TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS v2_eval (id TEXT PRIMARY KEY, portal TEXT, link TEXT, ok INTEGER, motivo TEXT, "
                "item TEXT, first_seen TEXT, last_seen TEXT, avisado INTEGER DEFAULT 0)")
    vistos_antes = {r[0] for r in con.execute("SELECT id FROM v2_eval")} | known

    listings, errors, paginas = {}, [], {}
    import time
    for name, parser in (("lejebolig", parse_lejebolig), ("boligportal", parse_boligportal)):
        url_de, minimo, maximo = PAGINAS[name]
        for i in range(maximo):
            try:
                page = parser(fetch(url_de(i)))
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}"); break
            listings.update(page); paginas[name] = i + 1
            time.sleep(2)
            if not page: break                                   # se acabaron los anuncios
            if i + 1 >= minimo and not (set(page) - vistos_antes): break   # todo lo de esta página ya lo conocíamos
    cache = {r[0]: json.loads(r[1]) for r in con.execute("SELECT id, data FROM v2_detalle")}

    budget = MAX_DETAIL
    cands, rech, nuevos = [], [], []
    for lid, it in listings.items():
        if lid in known:
            continue                                      # ya está en el mapa / descartados fijos
        it.update({k: v for k, v in cache.get(lid, {}).items() if it.get(k) in (None, "")})
        if isinstance((planos.get(lid) or {}).get("dorm"), int):
            it["plano_dorm"] = planos[lid]["dorm"]
        ok, motivo, avisos = evaluar(it, F, zonas)
        if ok and lid not in cache and budget > 0:        # pasa lo barato: abrir ficha para lo caro
            try:
                d = detalle(it); budget -= 1
                con.execute("INSERT OR REPLACE INTO v2_detalle VALUES (?,?,?)", (lid, json.dumps(d, ensure_ascii=False), now))
                it.update({k: v for k, v in d.items() if v is not None and (k == "texto" or it.get(k) in (None, ""))})
                ok, motivo, avisos = evaluar(it, F, zonas)
            except Exception:
                avisos.append("ficha no disponible")
        row = con.execute("SELECT first_seen, avisado FROM v2_eval WHERE id=?", (lid,)).fetchone()
        first = row[0] if row else now
        out = {k: it.get(k) for k in ("id", "portal", "link", "titulo", "lugar", "zona", "precio", "aconto", "dep", "pre", "m2", "vaer", "disp", "contrato_meses", "lat", "lon", "plano", "plano_dorm")}
        out.update({"avisos": avisos, "visto": first[:10]})
        con.execute("INSERT INTO v2_eval (id, portal, link, ok, motivo, item, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET ok=excluded.ok, motivo=excluded.motivo, item=excluded.item, last_seen=excluded.last_seen",
                    (lid, it["portal"], it["link"], int(ok), motivo, json.dumps(out, ensure_ascii=False), first, now))
        if ok:
            cands.append(out)
            if lid not in decididos and not (row and row[1]):
                nuevos.append(out)
                con.execute("UPDATE v2_eval SET avisado=1 WHERE id=?", (lid,))
        else:
            rech.append({**out, "motivo": motivo})
    # candidatos de pasadas anteriores que ya no salen en las páginas leídas: se siguen proponiendo unos días
    limite = (datetime.now(timezone.utc).timestamp() - RECUERDO_DIAS * 86400)
    for lid, item, last in con.execute("SELECT id, item, last_seen FROM v2_eval WHERE ok=1"):
        if lid in listings or lid in known: continue
        try:
            if datetime.fromisoformat(last).timestamp() < limite: continue
            it = json.loads(item)
        except Exception:
            continue
        it["avisos"] = [a for a in (it.get("avisos") or []) if not a.startswith("no visto desde")] + [f"no visto desde {last[:10]} (ya no sale en las primeras páginas)"]
        cands.append(it)
    con.commit()
    historial(con, now)
    con.close()

    cands.sort(key=lambda x: (x["visto"], -x["precio"]), reverse=True)
    write_json(BRIDGE / "candidatos.json", {"actualizado": now, "filtros": F, "pisos": cands, "errores": errors})
    write_json(BRIDGE / "rechazados.json", {"actualizado": now, "pisos": rech[:400]})

    leidos = {}
    for it in listings.values(): leidos[it["portal"]] = leidos.get(it["portal"], 0) + 1
    STATS.update(leidos=leidos, paginas=paginas, candidatos=len(cands), nuevos=len(nuevos), rechazados=len(rech), errores=errors,
                 fichas_abiertas=MAX_DETAIL-budget, mapa_conocidos=len(known))
    for n in nuevos:
        print(f"NUEVO | {n['zona']} | {n['precio']} kr/mes | {n['m2'] or '?'} m², {n['vaer'] or '?'} vær. | "
              f"{(n['titulo'] or '')[:45]} | {n['link']}" + (f" | ⚠ {', '.join(n['avisos'][:2])}" if n["avisos"] else ""))
    if errors and not nuevos:
        print(f"[aviso] fuentes con error: {', '.join(errors)}")


def historial(con, now):
    """pisos-bridge/historial.json para el panel y la web: TODOS los rechazados que Lares ha visto (con motivo, para
    poder recuperar un error) y cuántos anuncios nuevos ve cada día. Sin IA y sin avisos: solo un archivo."""
    try:
        rech, dias = [], {}
        for lid, portal, link, ok, motivo, item, first, last in con.execute("SELECT id, portal, link, ok, motivo, item, first_seen, last_seen FROM v2_eval"):
            d = dias.setdefault((first or "")[:10], {"vistos": 0, "candidatos": 0, "rechazados": 0})
            d["vistos"] += 1; d["candidatos" if ok else "rechazados"] += 1
            if not ok:
                it = json.loads(item or "{}")
                rech.append({k: it.get(k) for k in ("id", "portal", "link", "titulo", "zona", "precio", "m2", "vaer", "disp")}
                            | {"id": lid, "portal": it.get("portal") or portal, "link": it.get("link") or link, "motivo": motivo, "visto": (first or "")[:10], "ultimo": (last or "")[:10]})
        rech.sort(key=lambda x: x["ultimo"], reverse=True)
        write_json(BRIDGE / "historial.json", {"actualizado": now, "por_dia": dict(sorted(dias.items())), "rechazados": rech[:3000]})
    except Exception:
        pass


def cron_status():
    """Copia al buzón el estado de las tareas de pisos de Lares (solo piso-*), para el panel.
    Así el backend no necesita leer la carpeta cron de Lares."""
    try:
        jobs = json.loads(Path("/opt/data/profiles/lares/cron/jobs.json").read_text(encoding="utf-8"))
        jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
        keep = ("name", "enabled", "state", "last_status", "last_run_at", "next_run_at", "last_error", "last_delivery_error", "schedule_display")
        out = [{k: j.get(k) for k in keep} for j in jobs if str(j.get("name", "")).startswith("piso")]
        write_json(BRIDGE / "lares_cron.json", {"copiado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "jobs": out})
    except Exception:
        pass


def run_log(**kw):
    """Una línea por pasada en pisos-bridge/lares_runs.jsonl (lo lee el panel de estado)."""
    rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "t": round(datetime.now(timezone.utc).timestamp(), 1), **kw}
    p = BRIDGE / "lares_runs.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-500:] if p.exists() else []
        lines.append(json.dumps(rec, ensure_ascii=False))
        tmp = p.with_suffix(".tmp"); tmp.write_text("\n".join(lines) + "\n", encoding="utf-8"); os.replace(tmp, p)
    except Exception:
        pass


if __name__ == "__main__":
    import time as _t, traceback
    _t0 = _t.time()
    try:
        STATS.clear(); main()
        run_log(ok=True, seg=round(_t.time()-_t0, 1), **STATS)
        cron_status()
    except Exception as e:
        run_log(ok=False, seg=round(_t.time()-_t0, 1), msg=f"{type(e).__name__}: {e}", errores=[traceback.format_exc(limit=2)[-300:]])
        raise
