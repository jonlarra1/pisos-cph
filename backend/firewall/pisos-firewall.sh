#!/bin/sh
# Aísla el contenedor pisos-api (red 172.31.250.0/24): puede salir a internet, pero NO a la LAN,
# a Tailscale, a otras redes de Docker ni a la propia Pi. Idempotente (se puede ejecutar varias veces).
set -e
NET=172.31.250.0/24
IPT=iptables

# salida del contenedor hacia otras redes (pasa por FORWARD -> DOCKER-USER)
$IPT -N PISOS-EGRESS 2>/dev/null || $IPT -F PISOS-EGRESS
$IPT -A PISOS-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for d in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
  $IPT -A PISOS-EGRESS -d "$d" -j DROP
done
$IPT -A PISOS-EGRESS -j RETURN
$IPT -N DOCKER-USER 2>/dev/null || true
$IPT -C DOCKER-USER -s $NET -j PISOS-EGRESS 2>/dev/null || $IPT -I DOCKER-USER 1 -s $NET -j PISOS-EGRESS

# del contenedor a la propia Pi (cualquier IP de la Pi): solo respuestas
$IPT -N PISOS-HOST 2>/dev/null || $IPT -F PISOS-HOST
$IPT -A PISOS-HOST -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
$IPT -A PISOS-HOST -j DROP
$IPT -C INPUT -s $NET -j PISOS-HOST 2>/dev/null || $IPT -I INPUT 1 -s $NET -j PISOS-HOST

echo "pisos-firewall: reglas aplicadas para $NET"
