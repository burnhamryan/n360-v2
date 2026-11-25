#!/usr/bin/env bash
# Start or stop the N360 containers (called by n360-stack.service). Adds Caddy (HTTPS) when N360_DOMAIN is set.
set -euo pipefail
cd "${N360_CODE:-/opt/n360}/code"
domain=$(grep '^N360_DOMAIN=' .env | cut -d= -f2 || true)   # no line = no domain
args=(-p n360 -f docker-compose.yml -f docker-compose.prod.yml)
[ -n "$domain" ] && args+=(--profile public)
for extra in ${N360_COMPOSE_EXTRA:-}; do args+=(-f "$extra"); done     # optional extra compose files (local overrides)
case "${1:-up}" in
  up) docker compose "${args[@]}" up -d --remove-orphans
      # no domain = no public access: make sure an earlier Caddy is gone (compose leaves inactive profiles alone)
      if [ -z "$domain" ]; then docker rm -f n360-caddy >/dev/null 2>&1 || true; fi ;;
  down) docker compose "${args[@]}" down ;;
  *) echo "usage: stack.sh up|down"; exit 1 ;;
esac
