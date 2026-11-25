#!/usr/bin/env bash
# shellcheck disable=SC2015,SC1091  # a && ok || fail is intended: ok never fails
# End-to-end test of the ops scripts against a real ClickHouse server and S3 (or an S3-compatible server such as
# MinIO: set AWS_ENDPOINT_URL). DESTROYS the n360 databases on that server: use a test server only.
#
#   N360_TEST_BUCKET=s3://n360-test CH_URL=http://localhost:8123 N360_CH_PASSWORD=... code/ops/test_ops.sh
#
# 1 14 synthetic nights land in the drop folder; nightly.sh loads them in order (night 9 refused); a second run
#   loads nothing. 2 backup.sh. 3 a "new server": databases dropped, schema only, restore.sh, every history table
#   matches its checksum; nightly.sh --reload-latest brings the live tables back. 4 a new night is loaded; a
#   re-export of an older night is not (it would roll the dashboards back).
set -euo pipefail
OPS=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$OPS/../.." && pwd)
: "${N360_TEST_BUCKET:?set N360_TEST_BUCKET (s3://bucket, a test bucket)}"
export N360_DROP="${N360_TEST_BUCKET%/}/exports" N360_BACKUP="${N360_TEST_BUCKET%/}/backups"
export N360_WORK=${N360_WORK:-$(mktemp -d)}
. "$OPS/common.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
ok() { echo "OK   $*"; }
fail() { echo "FAIL $*"; exit 1; }

sql "DROP DATABASE IF EXISTS n360" && sql "DROP DATABASE IF EXISTS n360_next"
aws s3 rm --quiet --recursive "$N360_TEST_BUCKET/" >/dev/null 2>&1 || true
python3 "$ROOT/sample_data/generate_synthetic.py" --out "$T/n14" --nights 14 --bad-night 9 > /dev/null
python3 "$ROOT/sample_data/generate_synthetic.py" --out "$T/n15" --nights 15 --last-day 2026-11-20 > /dev/null   # one more night
aws s3 cp --quiet --recursive "$T/n14/" "$N360_DROP/"

# 1 nightly loads
"$OPS/nightly.sh" | tail -3
[ "$(sql "SELECT countIf(status = 'loaded') || '/' || countIf(status = 'failed_checks') FROM n360.runs")" = "13/1" ] \
  && ok "nightly: 13 nights loaded, night 9 refused" || fail "nightly: runs $(sql "SELECT status, count() FROM n360.runs GROUP BY status")"
out=$("$OPS/nightly.sh"); grep -q "nothing new to load" <<< "$out" && ok "second run: nothing new" || fail "second run: $out"
live=$(sql "SELECT toString(max(day)) FROM n360.va_day")
[ "$live" = 2026-11-19 ] && ok "live data through 19 Nov" || fail "live $live"

# 2 backup
"$OPS/backup.sh"
latest=$(aws s3 cp "$N360_BACKUP/LATEST" - | tr -d '[:space:]')
[ -n "$latest" ] && ok "backup $latest" || fail "no LATEST"
before=""; for t in $HISTORY_TABLES; do before="$before $t:$(sql "SELECT count() FROM n360.$t")/$(sql "SELECT groupBitXor(cityHash64(formatRow('TSV', *))) FROM n360.$t")"; done

# 3 a new server: schema only, restore, reload the newest night
sql "DROP DATABASE n360" && sql "DROP DATABASE n360_next"
python3 "$ROOT/code/load.py" --schema-only --url "$CH_URL" --user "$CH_USER" --password "$CH_PASSWORD"
"$OPS/restore.sh" latest
after=""; for t in $HISTORY_TABLES; do after="$after $t:$(sql "SELECT count() FROM n360.$t")/$(sql "SELECT groupBitXor(cityHash64(formatRow('TSV', *))) FROM n360.$t")"; done
[ "$before" = "$after" ] && ok "restore: every history table identical (rows and checksum)" || fail "restore: $before vs $after"
"$OPS/restore.sh" latest > /dev/null 2>&1 && fail "restore over existing rows should refuse" || ok "restore refuses to overwrite without --force"
out=$("$OPS/nightly.sh"); grep -q "nothing new to load" <<< "$out" && ok "after restore: no night reloaded by accident" || fail "$out"
"$OPS/nightly.sh" --reload-latest | tail -2
[ "$(sql "SELECT toString(max(day)) FROM n360.va_day")" = 2026-11-19 ] && ok "--reload-latest: live tables back (through 19 Nov)" || fail "reload"

# 4 a new night, and an older re-export
aws s3 cp --quiet --recursive "$T/n15/2026-11-21/" "$N360_DROP/2026-11-21/"
aws s3 rm --quiet --recursive "$N360_DROP/2026-11-10/" && aws s3 cp --quiet --recursive "$T/n15/2026-11-10/" "$N360_DROP/2026-11-10/"
out=$("$OPS/nightly.sh"); echo "$out" | tail -3
grep -q "2026-11-21: LOADED" <<< "$out" && ok "new night 21 Nov loaded" || fail "new night"
grep -q "2026-11-10: run .* older than the live night" <<< "$out" && ok "older re-export not loaded" || fail "older re-export"
[ "$(sql "SELECT toString(max(day)) FROM n360.va_day")" = 2026-11-20 ] && ok "live data through 20 Nov" || fail "live"
echo "OPS TESTS PASSED"
