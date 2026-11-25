#!/usr/bin/env bash
# Manage Grafana users once Google sign-in is on (everyone who signs in starts as a Viewer).
#   code/ops/grafana_admin.sh list
#   code/ops/grafana_admin.sh role you@company.com Admin       # Admin | Editor | Viewer (Admin also = server admin)
# Talks to GRAFANA_URL as admin / GRAFANA_ADMIN_PASSWORD (eval "$(code/ops/remote_env.sh)" with tunnel.sh open,
# or a local Grafana with code/.env). The person must have signed in with Google once first.
set -euo pipefail
OPS=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=code/ops/common.sh
. "$OPS/common.sh"
G=${GRAFANA_URL:-http://localhost:3000}; A="admin:${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD}"
api() { curl -sfS -u "$A" -H 'Content-Type: application/json' "$@"; }
case "${1:-}" in
  list)
    api "$G/api/org/users" | python3 -c 'import json,sys
for u in json.load(sys.stdin): print(u["email"].ljust(40), u["role"].ljust(7), "last seen", u.get("lastSeenAtAge", ""))' ;;
  role)
    email=${2:?email}; role=${3:?Admin, Editor or Viewer}
    case "$role" in Admin|Editor|Viewer) ;; *) echo "role must be Admin, Editor or Viewer"; exit 1 ;; esac
    id=$(api "$G/api/users/lookup?loginOrEmail=$email" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null) \
      || { echo "$email has not signed in yet (they need to sign in with Google once)"; exit 1; }
    api -X PATCH "$G/api/org/users/$id" -d "{\"role\": \"$role\"}" > /dev/null
    api -X PUT "$G/api/admin/users/$id/permissions" -d "{\"isGrafanaAdmin\": $([ "$role" = Admin ] && echo true || echo false)}" > /dev/null
    echo "$email is now $role" ;;
  *) sed -n '2,6p' "$0"; exit 1 ;;
esac
