#!/usr/bin/env bash
# Load every new nightly export, oldest first. Safe to run every hour: the company exporter runs whenever its
# VPN connects, so this picks an export up the first time it is seen.
#   new     = its run_id is not in n360.runs (a fixed re-export of a night has a new run_id and is loaded)
#   skipped = a night older than the one already live (loading it would roll the dashboards back)
# Each export goes through load.py (stage, 8 checks, swap). Exit codes: 0 nothing failed (loaded, refused by a
# check, or nothing new); 2 a load errored (stops here, in order; the next run retries it).
# After at least one load, backs up the history tables if N360_BACKUP is set. Settings: see common.sh.
# With N360_SALESFORCE=1 it first pulls Salesforce (code/salesforce/sf_pull.py, credentials SF_* in the environment
# or .env) and loads it (load_salesforce.py), so tonight's risk snapshot sees it. A failed pull is logged and the
# previous Salesforce copy stays live; the nightly loads still run.
#   --reload-latest  also reload the newest night already loaded (after a restore onto a new server, whose live
#                    tables are still empty: the restored n360.runs makes every export look known)
set -euo pipefail
# shellcheck source=code/ops/common.sh
. "$(dirname "$0")/common.sh"
: "${N360_DROP:?set N360_DROP (s3://bucket/exports or a folder)}"
mkdir -p "$WORK"
if command -v flock >/dev/null; then                       # Linux
  exec 9>"$WORK/.nightly.lock"
  flock -n 9 || { log "another nightly run is in progress; nothing to do"; exit 0; }
else                                                       # macOS has no flock: a lock directory instead
  if ! mkdir "$WORK/.nightly.lock.d" 2>/dev/null; then
    log "another nightly run is in progress (or a stale lock: rmdir $WORK/.nightly.lock.d); nothing to do"; exit 0
  fi
  trap 'rmdir "$WORK/.nightly.lock.d" 2>/dev/null' EXIT
fi

if [ "${N360_SALESFORCE:-0}" = 1 ]; then
  sfdir="$WORK/salesforce"; rm -rf "$sfdir"
  if "$PYTHON" "$ROOT/code/salesforce/sf_pull.py" --out "$sfdir"; then
    set +e
    "$PYTHON" "$ROOT/code/salesforce/load_salesforce.py" --rows "$sfdir" --url "$CH_URL" --user "$CH_USER" --password "$CH_PASSWORD"
    rc=$?; set -e
    if [ "$rc" -eq 0 ]; then
      log "Salesforce: LOADED"
    else
      log "Salesforce: not loaded (exit $rc); the previous copy stays live"
    fi
  else
    log "Salesforce: pull failed; the previous copy stays live"
  fi
  rm -rf "$sfdir"
fi

reload=${1:-}
known=$(sql "SELECT toString(run_id) FROM n360.runs" 2>/dev/null || true)          # empty on a new server
newest=$(sql "SELECT toString(max(data_date)) FROM n360.runs WHERE status = 'loaded'" 2>/dev/null || true)
if [ "$reload" = --reload-latest ] && [ -n "$newest" ]; then
  reload_run=$(sql "SELECT toString(argMax(run_id, (loaded_at, data_date))) FROM n360.runs WHERE status = 'loaded'")
  known=$(grep -vx "$reload_run" <<< "$known" || true)
  log "reloading the newest loaded night ($newest, run $reload_run)"
fi
loaded=0
for night in $(ls_dirs "$N360_DROP" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort); do
  tmp="$WORK/$night"; rm -rf "$tmp"; mkdir -p "$tmp/rows"
  if ! get "${N360_DROP%/}/$night/rows/run.json" "$tmp/rows/run.json" 2>/dev/null; then
    log "$night: no run.json yet (export still landing?); skipped"; rm -rf "$tmp"; continue
  fi
  run_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$tmp/rows/run.json")
  if grep -qx "$run_id" <<< "$known"; then rm -rf "$tmp"; continue; fi
  if [ -n "$newest" ] && [ "$newest" != "1970-01-01" ] && [[ "$night" < "$newest" ]]; then
    log "$night: run $run_id is older than the live night $newest; not loaded (would roll the dashboards back)"
    rm -rf "$tmp"; continue
  fi
  get_dir "${N360_DROP%/}/$night/rows" "$tmp/rows"
  log "$night: loading run $run_id"
  set +e
  "$PYTHON" "$ROOT/code/load.py" --rows "$tmp/rows" --url "$CH_URL" --user "$CH_USER" --password "$CH_PASSWORD"
  rc=$?
  set -e
  rm -rf "$tmp"
  case $rc in
    0) log "$night: LOADED"; loaded=$((loaded + 1)); newest=$night ;;
    3) log "$night: REFUSED by a data check (logged in n360.runs; the previous night stays live)" ;;
    *) log "$night: ERROR (exit $rc); stopping here, the next run retries"; exit 2 ;;
  esac
done
[ "$loaded" -eq 0 ] && log "nothing new to load"
# risk model (docs/21): tonight's features and scores, unless N360_RISK_MODEL=0. Training stays a deliberate step.
if [ "$loaded" -gt 0 ] && [ "${N360_RISK_MODEL:-1}" = 1 ]; then
  "$PYTHON" "$ROOT/code/risk/risk_model.py" --url "$CH_URL" --user "$CH_USER" --password "$CH_PASSWORD" nightly \
    || log "risk model: scoring failed (the dashboards keep the previous scores)"
fi
if [ "$loaded" -gt 0 ] && [ -n "${N360_BACKUP:-}" ]; then "$OPS/backup.sh"; fi
exit 0
