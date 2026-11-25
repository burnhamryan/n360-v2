#!/usr/bin/env bash
# Restore the history tables from a backup (backup.sh): restore.sh [TIMESTAMP|latest] [--force]
# Use on a new server: start the stack, run `python3 code/load.py --schema-only ...`, then this, then the nightly
# load (which skips every night already in the restored n360.runs). Refuses a table that already has rows unless
# --force (which empties it first). Checks every table's rows and checksum against the manifest.
set -euo pipefail
# shellcheck source=code/ops/common.sh
. "$(dirname "$0")/common.sh"
: "${N360_BACKUP:?set N360_BACKUP (s3://bucket/backups or a folder)}"
which=${1:-latest}; force=${2:-}
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
if [ "$which" = latest ]; then get "${N360_BACKUP%/}/LATEST" "$tmp/LATEST"; which=$(tr -d '[:space:]' < "$tmp/LATEST"); fi
get_dir "${N360_BACKUP%/}/$which" "$tmp/b"
for t in $HISTORY_TABLES; do
  have=$(sql "SELECT count() FROM n360.$t")
  if [ "$have" != 0 ]; then
    if [ "$force" = --force ]; then sql "TRUNCATE TABLE n360.$t"; else
      log "n360.$t already has $have rows; nothing restored (use --force to replace)"; exit 1; fi
  fi
  gunzip -c "$tmp/b/$t.native.gz" > "$tmp/$t.native"
  sql "INSERT INTO n360.$t FORMAT Native" "$tmp/$t.native"
done
fail=0
for t in $HISTORY_TABLES; do
  want=$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1]))[sys.argv[2]]; print(m["rows"], m["checksum"])' "$tmp/b/manifest.json" "$t")
  got="$(sql "SELECT count() FROM n360.$t") $(sql "SELECT groupBitXor(cityHash64(formatRow('TSV', *))) FROM n360.$t")"
  if [ "$want" = "$got" ]; then log "n360.$t restored: $got"; else log "n360.$t MISMATCH: want $want, got $got"; fail=1; fi
done
if [ $fail -ne 0 ]; then log "restore $which FAILED checks"; exit 1; fi
log "restore $which complete"
