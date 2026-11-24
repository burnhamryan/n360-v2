#!/usr/bin/env bash
# Create the Grafana service account that the chat MCP server (mcp-grafana) uses, with the Viewer
# role, and print a token for it once. Grafana cannot provision service accounts from files, so this
# runs once per Grafana instance. Re-running it adds a new token to the same account.
#
#   GRAFANA_URL=http://localhost:3000 GRAFANA_ADMIN_PASSWORD=... code/grafana/mcp_service_account.sh
#
# What the token can do: whatever a Viewer can (read dashboards, run queries through the N360
# ClickHouse data source). It cannot edit dashboards, data sources or alerts. The data itself is
# protected one level lower: the data source logs in as ClickHouse user 'grafana' (readonly = 1).
# Treat the token as a password: put it in the MCP client config, never in Git.
set -euo pipefail
URL=${GRAFANA_URL:-http://localhost:3000}
AUTH="admin:${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD}"
NAME=${SA_NAME:-n360-chat}

api() { curl -sfS -u "$AUTH" -H 'Content-Type: application/json' "$@"; }

id=$(api "$URL/api/serviceaccounts/search?query=$NAME" |
     python3 -c "import sys,json; a=[s for s in json.load(sys.stdin)['serviceAccounts'] if s['name']=='$NAME']; print(a[0]['id'] if a else '')")
if [ -z "$id" ]; then
  id=$(api -X POST "$URL/api/serviceaccounts" -d "{\"name\":\"$NAME\",\"role\":\"Viewer\",\"isDisabled\":false}" |
       python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  echo "created service account $NAME (id $id, role Viewer)" >&2
else
  echo "service account $NAME exists (id $id)" >&2
fi
api -X POST "$URL/api/serviceaccounts/$id/tokens" -d "{\"name\":\"$NAME-$(date -u +%Y%m%d%H%M%S)\"}" |
  python3 -c "import sys,json; print(json.load(sys.stdin)['key'])"
