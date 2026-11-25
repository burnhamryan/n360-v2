#!/usr/bin/env bash
# Back up the history tables (common.sh HISTORY_TABLES): the ones a nightly export cannot rebuild.
# Writes N360_BACKUP/<UTC timestamp>/: <table>.native.gz (ClickHouse Native format, all columns and types) and
# manifest.json (rows and checksum per table), then points N360_BACKUP/LATEST at it. Disk snapshots (AWS Backup)
# are the first line of defence; this is the portable copy (laptop to AWS, or a fresh server).
set -euo pipefail
# shellcheck source=code/ops/common.sh
. "$(dirname "$0")/common.sh"
: "${N360_BACKUP:?set N360_BACKUP (s3://bucket/backups or a folder)}"
ts=$(date -u +%Y%m%dT%H%M%SZ)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
echo '{' > "$tmp/manifest.json"; first=1
for t in $HISTORY_TABLES; do
  sql "SELECT * FROM n360.$t FORMAT Native" | gzip -c > "$tmp/$t.native.gz"
  rows=$(sql "SELECT count() FROM n360.$t")
  sum=$(sql "SELECT groupBitXor(cityHash64(formatRow('TSV', *))) FROM n360.$t")
  [ $first -eq 1 ] || echo ',' >> "$tmp/manifest.json"; first=0
  printf '  "%s": {"rows": %s, "checksum": "%s"}' "$t" "$rows" "$sum" >> "$tmp/manifest.json"
done
printf '\n}\n' >> "$tmp/manifest.json"
put_dir "$tmp" "${N360_BACKUP%/}/$ts"
put_text "$ts" "${N360_BACKUP%/}/LATEST"
log "backup $ts written to ${N360_BACKUP%/}/$ts ($(wc -w <<< "$HISTORY_TABLES") tables)"
