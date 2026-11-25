# shellcheck shell=bash
# shellcheck disable=SC2034  # variables used by the scripts that source this file
# Shared settings for the ops scripts (sourced, not run). Reads code/.env, then the environment.
#   N360_DROP     where nightly exports land: s3://bucket/exports or a local folder; one subfolder per night,
#                 <data_date>/rows/{*.ndjson,run.json} (the layout generate_synthetic.py --nights writes)
#   N360_BACKUP   where history backups go: s3://bucket/backups or a local folder (optional)
#   N360_WORK     local scratch (default /var/lib/n360/work)
#   CH_URL        ClickHouse HTTP (default http://localhost:8123); loader user n360 / N360_CH_PASSWORD
OPS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$OPS/../.." && pwd)
# code/.env fills in only what the environment does not already set, so `eval "$(code/ops/remote_env.sh)"` (the
# AWS server's credentials, through the tunnel) wins over a laptop's own local .env.
if [ -f "${N360_ENV_FILE:-$ROOT/code/.env}" ]; then
  while IFS='=' read -r k v; do
    [[ $k =~ ^[A-Z_][A-Z0-9_]*$ ]] && [ -z "${!k+x}" ] && export "$k=$v"
  done < "${N360_ENV_FILE:-$ROOT/code/.env}"
fi
CH_URL=${CH_URL:-http://localhost:8123}
CH_USER=${CH_USER:-n360}
CH_PASSWORD=${CH_PASSWORD:-${N360_CH_PASSWORD:-}}
WORK=${N360_WORK:-/var/lib/n360/work}
PYTHON=${N360_PYTHON:-python3}     # runs load.py (needs clickhouse-connect); the server uses python3.12

# History tables: built up night by night (or by people), so a nightly export cannot rebuild them.
HISTORY_TABLES="runs line_totals_history forecast_history source_freshness anomaly_log forecast_challenger"

sql() {   # sql "QUERY" [data-file]: run a query as the loader user; with a file, POST it as the INSERT body
  if [ -n "${2:-}" ]; then
    curl -sfS -u "$CH_USER:$CH_PASSWORD" "$CH_URL/?query=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$1")" \
         --data-binary @"$2"
  else
    curl -sfS -u "$CH_USER:$CH_PASSWORD" "$CH_URL" --data-binary "$1"
  fi
}

is_s3() { case "$1" in s3://*) return 0 ;; *) return 1 ;; esac; }

ls_dirs() {   # ls_dirs LOCATION: names of the subfolders
  if is_s3 "$1"; then aws s3 ls "${1%/}/" | awk '$1 == "PRE" {sub("/$", "", $2); print $2}'
  else find "$1" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null; fi
}

get() {   # get SRC DEST: copy one file (S3 or local)
  if is_s3 "$1"; then aws s3 cp --only-show-errors "$1" "$2"; else cp "$1" "$2"; fi
}

get_dir() {   # get_dir SRC DEST: copy a folder (S3 or local)
  mkdir -p "$2"
  if is_s3 "$1"; then aws s3 cp --only-show-errors --recursive "${1%/}/" "$2/"; else cp -r "${1%/}/." "$2/"; fi
}

put_dir() {   # put_dir SRC DEST: copy a folder up (S3 or local)
  if is_s3 "$2"; then aws s3 cp --only-show-errors --recursive "$1/" "${2%/}/"; else mkdir -p "$2" && cp -r "$1/." "$2/"; fi
}

put_text() {   # put_text TEXT DEST: write a small file (S3 or local)
  if is_s3 "$2"; then printf '%s\n' "$1" | aws s3 cp --only-show-errors - "$2"; else mkdir -p "$(dirname "$2")" && printf '%s\n' "$1" > "$2"; fi
}

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }
