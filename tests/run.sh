#!/usr/bin/env bash
# Tests rápidos de pisos-cph.
#   ./tests/run.sh               backend + datos + Lares (~4 s, sin internet)
#   ./tests/run.sh web           además la web en Chromium (~30 s)
#   ./tests/run.sh -k estado     solo los tests cuyo nombre contiene «estado» (en todos los ficheros)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
mods=(tests.test_backend tests.test_datos tests.test_lares tests.test_anuncios tests.test_api_v2 tests.test_calidad tests.test_dora tests.test_herramienta_lares tests.test_precalculo tests.test_migrar)
args=()
for a in "$@"; do
  if [ "$a" = "web" ]; then mods+=(tests.test_web); else args+=("$a"); fi
done
# con -k se buscan en todos los ficheros, también los de la web
if [[ " ${args[*]-} " == *" -k "* ]]; then mods=(tests.test_backend tests.test_datos tests.test_lares tests.test_anuncios tests.test_api_v2 tests.test_calidad tests.test_dora tests.test_herramienta_lares tests.test_precalculo tests.test_migrar tests.test_web); fi
exec "$PY" -m unittest "${mods[@]}" ${args[@]+"${args[@]}"}
