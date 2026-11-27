#!/usr/bin/env bash
# The whole test path on synthetic data. GitHub Actions runs this (.github/workflows/ci.yml);
# run it locally the same way. Any failure exits non-zero.
#
#   code/ci.sh chdb                  # embedded engine, no server needed
#   code/ci.sh server                # a ClickHouse server at $CH_URL (default http://localhost:8123),
#                                    # loader user $CH_USER/$CH_PASSWORD; creates a read-only
#                                    # 'grafana' user like init/grafana_user.sh and tests panels as it
#
# Steps: generate 14 nights (night 9 broken on purpose) -> load in order, expecting exactly night 9
# refused -> check runs and line_totals_history -> test the dashboard views -> build dashboards and confirm the committed JSON
# matches the generator -> run every panel and variable query -> test the dashboard building blocks.
set -euo pipefail
MODE=${1:?usage: ci.sh chdb|server}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=${CI_WORK:-$(mktemp -d)}
NIGHTS=14 BAD=9
echo "work dir: $WORK"

python3 "$ROOT/sample_data/generate_synthetic.py" --out "$WORK/nights" --nights $NIGHTS --bad-night $BAD --forecast-backfill 150

if [ "$MODE" = chdb ]; then
  CONN=(--chdb "$WORK/chdb")
  PANEL_CONN=("${CONN[@]}")
  sql() { python3 - "$WORK/chdb" "$1" <<'PY'
import sys
from chdb import session
print(session.Session(sys.argv[1]).query(sys.argv[2], 'TSV').bytes().decode().strip())
PY
  }
else
  URL=${CH_URL:-http://localhost:8123}
  CONN=(--url "$URL" --user "${CH_USER:-default}" --password "${CH_PASSWORD:-}")
  sql() { curl -sfS -u "${CH_USER:-default}:${CH_PASSWORD:-}" "$URL" --data-binary "$1"; }
  sql "DROP DATABASE IF EXISTS n360" && sql "DROP DATABASE IF EXISTS n360_next"
  GRAFANA_CH_PASSWORD=${GRAFANA_CH_PASSWORD:-ci-grafana}
  # the same SQL as init/grafana_user.sh (the one definition of Grafana's user)
  sed -n '/<<SQL$/,/^SQL$/p' "$ROOT/code/init/grafana_user.sh" | sed '1d;$d' |
    sed "s/\${GRAFANA_CH_PASSWORD}/$GRAFANA_CH_PASSWORD/" | tr '\n' ' ' | tr ';' '\n' |
    while read -r st; do [ -n "$st" ] && sql "$st"; done
  PANEL_CONN=(--url "$URL" --user grafana --password "$GRAFANA_CH_PASSWORD")
fi

# Salesforce (25 Nov 2026): synthetic records served by a fake Salesforce API, read by the real reader (OAuth client
# credentials, SOQL, paging), loaded before the nights so each night's risk snapshot sees it
python3 "$ROOT/sample_data/generate_salesforce.py" --out "$WORK/sf_records.json" > /dev/null
SF_PORT=${SF_PORT:-18443}
python3 "$ROOT/code/salesforce/fake_salesforce.py" --records "$WORK/sf_records.json" --port "$SF_PORT" &
SF_PID=$!; trap 'kill $SF_PID 2>/dev/null || true' EXIT
python3 -c "import json, sys; c = json.load(open(sys.argv[1])); c['account_overrides']['Juniper Bank N.A.'] = 'Juniper Bank'; json.dump(c, open(sys.argv[2], 'w'))" \
  "$ROOT/code/salesforce/salesforce.json" "$WORK/salesforce.json"        # a Salesforce name that differs from N360's
for _ in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$SF_PORT/" && break; sleep 0.5; done
SF_ENV=(env NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 SF_LOGIN_URL="http://127.0.0.1:$SF_PORT" SF_CLIENT_ID=fake-client-id)
"${SF_ENV[@]}" SF_CLIENT_SECRET=fake-client-secret python3 "$ROOT/code/salesforce/sf_pull.py" --out "$WORK/sf" --config "$WORK/salesforce.json"
python3 "$ROOT/code/salesforce/load_salesforce.py" --rows "$WORK/sf" "${CONN[@]}"
if "${SF_ENV[@]}" SF_CLIENT_SECRET=wrong python3 "$ROOT/code/salesforce/sf_pull.py" --out "$WORK/sf_bad" > "$WORK/sf_bad.log" 2>&1; then
  echo "FAIL: a wrong Salesforce secret was accepted"; exit 1
fi
[ ! -e "$WORK/sf_bad/sf_account.ndjson" ] || { echo "FAIL: a failed pull wrote files"; exit 1; }
echo "OK   Salesforce: pulled through the API (paged), loaded; a wrong secret is refused and writes nothing"

i=0
for dir in $(ls -d "$WORK"/nights/*/ | sort); do
  i=$((i + 1)); want=0; [ $i -eq $BAD ] && want=3
  set +e; python3 "$ROOT/code/load.py" --rows "$dir/rows" "${CONN[@]}" > "$WORK/load_$i.log" 2>&1; got=$?; set -e
  echo "night $i $(basename "$dir"): exit $got (want $want)"
  if [ $got -ne $want ]; then cat "$WORK/load_$i.log"; echo "FAIL: night $i"; exit 1; fi
  # alert rules (B18) in known states: provisional-only silence, the broken night, the 13 Nov event, Harbor crossing
  case $i in 7|9|11|12) python3 "$ROOT/code/grafana/test_alerts.py" "${PANEL_CONN[@]}" --scenario night$i ;; esac
done

loaded=$(sql "SELECT countIf(status = 'loaded') FROM n360.runs")
failed=$(sql "SELECT countIf(status = 'failed_checks') FROM n360.runs")
hist=$(sql "SELECT count(DISTINCT data_date) FROM n360.line_totals_history")
live=$(sql "SELECT toString(max(day)) FROM n360.va_day")
want_live=$(basename "$(ls -d "$WORK"/nights/*/ | sort | tail -1)")
want_live=$(python3 -c "import datetime as d; print(d.date.fromisoformat('$want_live') - d.timedelta(1))")
echo "runs loaded=$loaded failed_checks=$failed history_nights=$hist live_last_day=$live"
[ "$loaded" -eq $((NIGHTS - 1)) ] && [ "$failed" -eq 1 ] && [ "$hist" -eq $((NIGHTS - 1)) ] && [ "$live" = "$want_live" ] \
  || { echo "FAIL: runs/history do not match"; exit 1; }

# B5 views: the synthetic edge cases (Atlas red, Harbor crossing red, pool once, Lakeside silent, the grouped spike)
# anomaly reviews (B19), as the loader user; a second review of the same flag replaces the first
R=(python3 "$ROOT/code/review_anomaly.py" "${CONN[@]}" --by ci)
"${R[@]}" --va "Lakeside Email" --day 2026-11-11 --outcome real --note "inbox outage"
"${R[@]}" --va "Lakeside Email" --day 2026-11-12 --outcome real
"${R[@]}" --va "Lakeside Email" --day 2026-11-12 --outcome "data issue" --note "re-read: export gap"
"${R[@]}" --va "Bluebird Web" --day 2026-11-13 --outcome expected --note "industry-wide spike"
# risk register and CSM ratings (risk dashboard), as the loader user: an open churn risk, an overdue one, an updated
# one, a closed one, two ratings
K=(python3 "$ROOT/code/risk_register.py" "${CONN[@]}" --by ci)
"${K[@]}" add --id R-willow --customer "Willow Energy" --reason "Adoption / value" --impact churn --owner csm12 --due 2026-12-10 --note "[SYNTHETIC] usage falling"
"${K[@]}" add --id R-orchid --customer "Orchid Health" --reason "Quality / bugs" --impact contraction --owner csm10 --due 2026-11-20 --note "[SYNTHETIC] containment fell"
"${K[@]}" add --id R-kestrel --customer "Kestrel Travel" --reason "Budget / price" --impact downsell --owner csm9
"${K[@]}" update --id R-kestrel --due 2026-12-20 --note "[SYNTHETIC] procurement review"
"${K[@]}" add --id R-atlas --customer "Atlas Streaming" --reason "Budget / price" --impact downsell --owner csm4
"${K[@]}" close --id R-atlas --outcome saved --note "[SYNTHETIC] renewed at full volume"
# a churn closed in the register late: the outcome carries the day it happened (--date), not the day it was typed in
"${K[@]}" add --id R-lakeside --customer "Lakeside Insurance" --reason "Budget / price" --impact churn --owner csm8
"${K[@]}" close --id R-lakeside --outcome churned --date 2026-09-15 --note "[SYNTHETIC] gave notice in September"
"${K[@]}" pulse --customer "Harbor Telecom" --level amber --note "[SYNTHETIC] budget review in Q1"
"${K[@]}" pulse --customer "Northwind Outfitters" --level green
# risk model (docs/21): unit and synthetic-history tests on files, then the N360 path: weekly features back to May,
# train (stays at the expert prior: too few outcomes) and adopt that run, score every stored date
python3 "$ROOT/code/risk/test_risk_model.py"
M=(python3 "$ROOT/code/risk/risk_model.py" "${CONN[@]}")
"${M[@]}" features --from 2026-05-01 --every 7 > /dev/null
"${M[@]}" train --adopt > /dev/null
"${M[@]}" score --as-of all > /dev/null
# a rebuild must not replace what the nightly run stored: store the newest day as nightly, rebuild over it, count
"${M[@]}" features > /dev/null
"${M[@]}" features --from 2026-05-01 --every 7 | grep -q "kept as the nightly run stored them" \
  || { echo "FAIL: a rebuild replaced features the nightly run stored"; exit 1; }
"${M[@]}" score > /dev/null
python3 "$ROOT/code/test_views.py" "${PANEL_CONN[@]}"
# the readiness report runs on everything loaded, and prints no customer name
python3 "$ROOT/code/readiness.py" "${CONN[@]}" > "$WORK/readiness.txt"
grep -q "^TO DO, in order" "$WORK/readiness.txt" || { cat "$WORK/readiness.txt"; echo "FAIL: readiness report incomplete"; exit 1; }
if grep -qE "Willow|Harbor|Lakeside|Northwind|Juniper|Maple|Kestrel|Atlas|Orchid|Summit|Pinecrest|Bluebird|Cedar|Globex" "$WORK/readiness.txt"; then
  echo "FAIL: the readiness report printed a customer name"; exit 1
fi
echo "OK   readiness report: $(tail -1 "$WORK/readiness.txt"); no customer names"

# Challenger forecast backtest (B21): writes n360.forecast_challenger; the forecast of record must not change
before=$(sql "SELECT groupBitXor(cityHash64(formatRow('TSV', *))) FROM n360.forecast")
python3 "$ROOT/code/forecast/challenger.py" "${CONN[@]}" --report "$WORK/challenger.md" | tail -1
after=$(sql "SELECT groupBitXor(cityHash64(formatRow('TSV', *))) FROM n360.forecast")
[ "$before" = "$after" ] || { echo "FAIL: the challenger changed n360.forecast"; exit 1; }
[ "$(sql "SELECT count() > 0 FROM n360.v_challenger_backtest")" = 1 ] || { echo "FAIL: no challenger backtest rows"; exit 1; }
echo "OK   challenger backtest written; forecast of record unchanged"

# Dashboards and alert rules: regenerate into a scratch copy and require the committed JSON to match exactly
cp -r "$ROOT/code/grafana/dashboards" "$WORK/committed"
cp -r "$ROOT/code/grafana/provisioning/alerting" "$WORK/committed_alerting"
python3 "$ROOT/code/grafana/build_dashboards.py"
python3 "$ROOT/code/grafana/build_alerts.py"
python3 "$ROOT/code/grafana/build_ops.py"
diff -r "$WORK/committed_alerting" "$ROOT/code/grafana/provisioning/alerting" \
  || { echo "FAIL: committed alert rules differ from build_alerts.py output (run it and commit)"; exit 1; }
for f in "$ROOT"/code/grafana/dashboards/*.json; do
  python3 -m json.tool "$f" > /dev/null || { echo "FAIL: invalid JSON $f"; exit 1; }
done
diff -r "$WORK/committed" "$ROOT/code/grafana/dashboards" \
  || { echo "FAIL: committed dashboard JSON differs from build_dashboards.py / build_ops.py output (run them and commit)"; exit 1; }

# The Operations dashboard reads two tables the company pipeline's local runner writes (never loaded from exports).
# A few [SYNTHETIC] rows, so every one of its panels is tested: one night per loaded run, one refused, a job each state.
sql "INSERT INTO n360.ops_night (night, claude_result, claude_published, code_result, export_run_id, export_counts, updated)
     SELECT data_date, if(status = 'loaded', 'published v1', 'stopped: [SYNTHETIC] a check failed'), if(status = 'loaded', 1, NULL),
            'not run', toString(run_id), '[SYNTHETIC] va_day 1', now64(3) FROM n360.runs"
sql "INSERT INTO n360.ops_job (job_id, action, state, started, finished, exit_code, last_line, updated) VALUES
     ('syn-1', 'load', 'done', now() - 7200, now() - 7000, 0, '[SYNTHETIC] LOADED', now64(3)),
     ('syn-2', 'export', 'failed', now() - 3600, now() - 3500, 2, '[SYNTHETIC] export failed', now64(3)),
     ('syn-3', 'pull', 'running', now() - 60, NULL, NULL, '[SYNTHETIC] pulling', now64(3))"

python3 "$ROOT/code/grafana/test_panels.py" "${PANEL_CONN[@]}"
python3 "$ROOT/code/grafana/test_alerts.py" "${PANEL_CONN[@]}" --scenario final
# build_dashboards.py helpers: settings in one place, description shape, sample header/links/colours queries
python3 "$ROOT/code/grafana/test_build.py" "${PANEL_CONN[@]}"
# Stale sources (B17): amber at 2 days behind, red at 3 (embedded engine only: separate scratch databases)
if [ "$MODE" = chdb ]; then
  for st in "containment_days=2 stale2 amber" "consumption_rules=3 stale3 red"; do
    set -- $st
    python3 "$ROOT/sample_data/generate_synthetic.py" --out "$WORK/$2/rows" --stale "$1" > /dev/null
    python3 "$ROOT/code/load.py" --rows "$WORK/$2/rows" --chdb "$WORK/$2/db" > /dev/null
    python3 "$ROOT/code/grafana/test_alerts.py" --chdb "$WORK/$2/db" --scenario "$2"
    got=$(python3 -c "import sys; from chdb import session; print(session.Session(sys.argv[1]).query(\"SELECT status FROM n360.v_source_freshness WHERE source_id = '\" + sys.argv[2] + \"'\", 'TSV').bytes().decode().strip())" "$WORK/$2/db" "${1%%=*}")
    [ "$got" = "$3" ] || { echo "FAIL: ${1%%=*} stale shows '$got', want '$3'"; exit 1; }
    echo "OK   ${1%%=*} ${1##*=} days behind shows $got"
  done
fi
echo "CI PASSED ($MODE)"
