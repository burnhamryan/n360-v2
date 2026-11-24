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
python3 "$ROOT/code/test_views.py" "${PANEL_CONN[@]}"

# Challenger forecast backtest (B21): writes n360.forecast_challenger; the forecast of record must not change
before=$(sql "SELECT groupBitXor(cityHash64(*)) FROM n360.forecast")
python3 "$ROOT/code/forecast/challenger.py" "${CONN[@]}" --report "$WORK/challenger.md" | tail -1
after=$(sql "SELECT groupBitXor(cityHash64(*)) FROM n360.forecast")
[ "$before" = "$after" ] || { echo "FAIL: the challenger changed n360.forecast"; exit 1; }
[ "$(sql "SELECT count() > 0 FROM n360.v_challenger_backtest")" = 1 ] || { echo "FAIL: no challenger backtest rows"; exit 1; }
echo "OK   challenger backtest written; forecast of record unchanged"

# Dashboards and alert rules: regenerate into a scratch copy and require the committed JSON to match exactly
cp -r "$ROOT/code/grafana/dashboards" "$WORK/committed"
cp -r "$ROOT/code/grafana/provisioning/alerting" "$WORK/committed_alerting"
python3 "$ROOT/code/grafana/build_dashboards.py"
python3 "$ROOT/code/grafana/build_alerts.py"
diff -r "$WORK/committed_alerting" "$ROOT/code/grafana/provisioning/alerting" \
  || { echo "FAIL: committed alert rules differ from build_alerts.py output (run it and commit)"; exit 1; }
for f in "$ROOT"/code/grafana/dashboards/*.json; do
  python3 -m json.tool "$f" > /dev/null || { echo "FAIL: invalid JSON $f"; exit 1; }
done
diff -r "$WORK/committed" "$ROOT/code/grafana/dashboards" \
  || { echo "FAIL: committed dashboard JSON differs from build_dashboards.py output (run it and commit)"; exit 1; }

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
