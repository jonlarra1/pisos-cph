"""Prueba rápida de un backend en marcha (local o público). Sin escribir nada salvo con --escribir.
    python3 backend/smoke_test.py https://raspberrypi.tail9f1069.ts.net:8443
    python3 backend/smoke_test.py http://127.0.0.1:18787 --escribir      (también cambia un estado de prueba)
Sale con código 0 si todo va bien y 1 si algo falla (deploy.sh lo usa para decidir si vuelve atrás).
"""

import http.client
import json
import ssl
import sys
import time
import urllib.parse

ORIGIN = "https://jonlarra1.github.io"


def conn(u):
    p = urllib.parse.urlparse(u)
    if p.scheme == "https":
        return http.client.HTTPSConnection(p.hostname, p.port or 443, timeout=15, context=ssl.create_default_context())
    return http.client.HTTPConnection(p.hostname, p.port or 80, timeout=15)


def get(base, path, h=None):
    c = conn(base)
    c.request("GET", path, headers={"Origin": ORIGIN, **(h or {})})
    r = c.getresponse()
    b = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, b


def main(base, escribir=False, clave=""):
    fallos, avisos = [], []

    def ok(nombre, cond, det="", grave=True):
        """grave=False: se avisa pero no cuenta como fallo (la web sigue funcionando sin eso)."""
        print(("  ✓ " if cond else ("  ✗ " if grave else "  ⚠ ")) + nombre + (f" · {det}" if det else ""))
        if not cond:
            (fallos if grave else avisos).append(nombre)

    st, _, b = get(base, "/api/health")
    ok("health", st == 200 and b"true" in b, f"HTTP {st}")
    st, h, b = get(base, "/api/estados")
    ok("estados", st == 200 and isinstance(json.loads(b or b"null"), dict), f"HTTP {st}, {len(b)} bytes")
    ok("CORS para la web", h.get("access-control-allow-origin") == ORIGIN)
    tag = h.get("etag")
    if tag:
        st2, _, b2 = get(base, "/api/estados", {"If-None-Match": tag})
        ok("ETag → 304 sin datos", st2 == 304 and not b2, f"HTTP {st2}")
    else:
        ok("ETag (backend nuevo)", False, "sin ETag: ¿backend antiguo?")
    st, _, b = get(base, "/api/candidatos")
    ok("candidatos", st == 200 and "pisos" in json.loads(b), f"HTTP {st}")
    # tiempo real: la conexión tiene que abrir y mandar «: conectado» enseguida. No es grave si falla
    # (p. ej. si Funnel no deja pasar SSE): la web sigue preguntando cada 15 s.
    c = conn(base)
    sse_ok = False
    try:
        c.request("GET", "/api/eventos", headers={"Origin": ORIGIN})
        r = c.getresponse()
        primero = r.fp.readline() + r.fp.readline() if r.status == 200 else b""
        sse_ok = r.status == 200 and b"conectado" in primero
        ok("tiempo real (SSE)", sse_ok, f"HTTP {r.status} {primero[:40]!r}", grave=False)
    except Exception as e:
        ok("tiempo real (SSE)", False, f"{type(e).__name__}: {e}", grave=False)
    if escribir and sse_ok:
        # el piso de prueba alterna nuevo <-> gusta: si se repite el mismo estado no hay cambio y no llega aviso
        try:
            antes = (json.loads(get(base, "/api/estados")[2]).get("9999999") or {}).get("st")
        except Exception:
            antes = None
        t0 = time.time()
        p = conn(base)
        p.request(
            "POST",
            "/api/estado",
            body=json.dumps({"id": "9999999", "st": "gusta" if antes == "nuevo" else "nuevo", "by": ""}),
            headers={"Origin": ORIGIN, "Content-Type": "application/json", "X-Pisos-Key": clave},
        )
        pr = p.getresponse()
        pr.read()
        p.close()
        got = b""
        while b"9999999" not in got and time.time() - t0 < 10:
            got += r.fp.readline()
        ok(
            "aviso al momento tras un cambio",
            b"9999999" in got,
            f"POST {pr.status}, {time.time() - t0:.1f} s",
            grave=False,
        )
    c.close()
    print(
        "RESULTADO:",
        ("todo bien" if not fallos else "FALLA: " + ", ".join(fallos))
        + (f" (avisos: {', '.join(avisos)})" if avisos else ""),
    )
    return 0 if not fallos else 1


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if not a:
        sys.exit(__doc__)
    clave = next((x.split("=", 1)[1] for x in sys.argv if x.startswith("--clave=")), "")
    sys.exit(main(a[0].rstrip("/"), "--escribir" in sys.argv, clave))
