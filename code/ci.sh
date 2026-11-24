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
# refused -> check runs and line_totals_history -> build dashboards and confirm the committed JSON
# matches the generator -> run every panel and variable query.
set -euo pipefail
MODE=${1:?usage: ci.sh chdb|server}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=${CI_WORK:-$(mktemp -d)}
NIGHTS=14 BAD=9
echo "work dir: $WORK"

python3 "$ROOT/sample_data/generate_synthetic.py" --out "$WORK/nights" --nights $NIGHTS --bad-night $BAD

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

# Dashboards: regenerate into a scratch copy and require the committed JSON to match it exactly
cp -r "$ROOT/code/grafana/dashboards" "$WORK/committed"
python3 "$ROOT/code/grafana/build_dashboards.py"
for f in "$ROOT"/code/grafana/dashboards/*.json; do
  python3 -m json.tool "$f" > /dev/null || { echo "FAIL: invalid JSON $f"; exit 1; }
done
diff -r "$WORK/committed" "$ROOT/code/grafana/dashboards" \
  || { echo "FAIL: committed dashboard JSON differs from build_dashboards.py output (run it and commit)"; exit 1; }

python3 "$ROOT/code/grafana/test_panels.py" "${PANEL_CONN[@]}"
echo "CI PASSED ($MODE)"
