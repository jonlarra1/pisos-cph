#!/usr/bin/env bash
# Instala el backend del repo en la Raspberry Pi, con copia de seguridad y vuelta atrás automática.
#
#   backend/deploy.sh USUARIO@100.72.170.60              solo MIRA: qué ficheros cambian (no toca nada)
#   backend/deploy.sh USUARIO@100.72.170.60 --aplicar    instala
#   ... --aplicar --con-compose                          además sustituye compose.yml (p. ej. mem_limit 512m)
#
# Pasos con --aplicar:
#   1. tests locales (./tests/run.sh) — si fallan, no se instala nada
#   2. copia de seguridad en la Pi: /home/docker/pisos-backend/backup-AAAAMMDD-HHMMSS/
#   3. copia app.py, routing.py, admin.html, Dockerfile (y compose.yml con --con-compose)
#   4. docker compose up -d --build y espera a /api/health
#   5. smoke_test.py contra la URL pública (Funnel); si falla -> restaura la copia y reconstruye
# Solo toca /home/docker/pisos-backend (regla del proyecto). Necesita la clave SSH temporal: PI_KEY=/ruta/clave backend/deploy.sh ...
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:?uso: backend/deploy.sh USUARIO@HOST [--aplicar] [--con-compose]}"; shift || true
APLICAR=0; COMPOSE=0
for a in "$@"; do case "$a" in --aplicar) APLICAR=1;; --con-compose) COMPOSE=1;; *) echo "opción desconocida: $a"; exit 2;; esac; done
DIR=/home/docker/pisos-backend
URL=https://raspberrypi.tail9f1069.ts.net:8443
FILES=(app.py routing.py portales.py anuncios.py api_v2.py admin.html Dockerfile)
[ "$COMPOSE" = 1 ] && FILES+=(compose.yml)
KEYOPT=(); [ -n "${PI_KEY:-}" ] && KEYOPT=(-i "$PI_KEY" -o IdentitiesOnly=yes)   # PI_KEY=ruta de la clave temporal
SSH=(ssh "${KEYOPT[@]}" -o BatchMode=yes -o ConnectTimeout=10 "$HOST")

echo "== Diferencias repo -> Pi ($DIR)"
for f in "${FILES[@]}" compose.yml; do
  local_sum=$(sha256sum "backend/$f" | cut -c1-12)
  remote_sum=$("${SSH[@]}" "sha256sum $DIR/$f 2>/dev/null | cut -c1-12" || true)
  if [ "$local_sum" = "$remote_sum" ]; then echo "  igual    $f"; else echo "  CAMBIA   $f (repo $local_sum, Pi ${remote_sum:-no existe})"; fi
done
echo "== compose.yml (Pi -> repo):"
"${SSH[@]}" "cat $DIR/compose.yml" | diff -u - backend/compose.yml | sed 's/^/  /' || true
[ "$APLICAR" = 1 ] || { echo; echo "Solo mirado. Para instalar: backend/deploy.sh $HOST --aplicar"; exit 0; }

echo "== 1. Tests locales"
./tests/run.sh

B="backup-$(date +%Y%m%d-%H%M%S)"
echo "== 2. Copia de seguridad en la Pi: $DIR/$B"
"${SSH[@]}" "cd $DIR && mkdir $B && for f in ${FILES[*]} compose.yml; do if [ -e \$f ]; then cp -a \$f $B/; fi; done && ls $B"

volver() {
  echo "!! Algo falló: restaurando $B y reconstruyendo"
  "${SSH[@]}" "cd $DIR && cp -a $B/. . && docker compose up -d --build" || echo "!! LA VUELTA ATRÁS TAMBIÉN FALLÓ: revisar a mano en la Pi"
  python3 tools/dora.py --registrar backend vuelta_atras "$B"      # métricas DORA: cuenta como fallo
  exit 1
}

echo "== 3. Copiando: ${FILES[*]}"
for f in "${FILES[@]}"; do scp -q "${KEYOPT[@]}" -o BatchMode=yes "backend/$f" "$HOST:$DIR/$f" || volver; done

echo "== 4. Reconstruyendo el contenedor"
"${SSH[@]}" "cd $DIR && docker compose up -d --build" || volver
for i in $(seq 1 30); do
  "${SSH[@]}" "curl -s -m 3 http://127.0.0.1:8787/api/health" 2>/dev/null | grep -q true && break
  [ "$i" = 30 ] && volver; sleep 2
done

echo "== 5. Prueba desde fuera ($URL)"
python3 backend/smoke_test.py "$URL" || volver
python3 tools/dora.py --registrar backend ok "$B"                  # métricas DORA (docs/despliegues.jsonl)
echo "== Instalado. Copia anterior en $DIR/$B (borrarla cuando todo lleve un día bien)."
