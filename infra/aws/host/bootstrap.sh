#!/usr/bin/env bash
# Set up (or update) the N360 server on Amazon Linux 2023. Run as root. Safe to run again: data and passwords live
# on the data volume and are kept; only the code version and settings change. Terraform's user_data writes
# /etc/n360/n360.conf and runs this at first boot; update.sh runs it again later.
#
# /etc/n360/n360.conf (shell variables):
#   N360_NAME          name tag and Parameter Store prefix (/n360/<name>/...)       e.g. n360
#   AWS_REGION         region                                                       e.g. us-east-1
#   REPO_URL, GIT_REF  the code to run                                              e.g. https://github.com/burnhamryan/n360-v2, main
#   DATA_VOLUME_ID     EBS volume for /data (ClickHouse, Grafana, Caddy, passwords) e.g. vol-0abc...
#   BUCKET             S3 bucket: exports/ (nightly drop) and backups/
#   N360_DOMAIN        public hostname for Grafana over HTTPS; empty = no public access (tunnel only)
#   GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS, GOOGLE_HOSTED_DOMAIN   Google sign-in (secret: Parameter Store)
#   SERVER_LOADS       true = the server loads new exports from S3 every hour; false = loads run from the laptop
set -euo pipefail
CONF=${N360_CONF:-/etc/n360/n360.conf}
# shellcheck source=/dev/null
. "$CONF"
: "${N360_NAME:?}" "${AWS_REGION:?}" "${REPO_URL:?}" "${GIT_REF:?}" "${DATA_VOLUME_ID:?}" "${BUCKET:?}"
COMPOSE_VERSION=2.40.3
declare -A COMPOSE_SHA256=(
  [x86_64]=dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372
  [aarch64]=d26373b19e89160546d15407516cc59f453030d9bc5b43ba7faf16f7b4980137 )
CODE=/opt/n360; DATA=/data; ENVF=$DATA/n360/.env; P=/n360/$N360_NAME
export AWS_DEFAULT_REGION=$AWS_REGION
log() { echo "[n360-bootstrap] $*"; }

# 1 packages; Docker Compose plugin pinned and checksum-verified
dnf install -y -q docker git xfsprogs python3.12 python3.12-pip >/dev/null   # AL2023's python3 is 3.9: too old
arch=$(uname -m)
if ! docker compose version 2>/dev/null | grep -q "v$COMPOSE_VERSION"; then
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL -o /tmp/docker-compose "https://github.com/docker/compose/releases/download/v$COMPOSE_VERSION/docker-compose-linux-$arch"
  echo "${COMPOSE_SHA256[$arch]}  /tmp/docker-compose" | sha256sum -c - >/dev/null
  install -m 0755 /tmp/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
fi
python3.12 -m pip install -q clickhouse-connect==1.13.0

# 2 data volume on /data (formatted once; mounted by UUID; survives instance replacement)
dev=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID/-/}
for _ in $(seq 60); do [ -e "$dev" ] && break; sleep 5; done
[ -e "$dev" ] || { log "data volume $DATA_VOLUME_ID not attached"; exit 1; }
blkid "$dev" >/dev/null 2>&1 || { log "formatting $DATA_VOLUME_ID"; mkfs.xfs -q "$dev"; }
uuid=$(blkid -s UUID -o value "$dev")
mkdir -p "$DATA"
grep -q "$uuid" /etc/fstab || echo "UUID=$uuid $DATA xfs defaults,nofail 0 2" >> /etc/fstab
mountpoint -q "$DATA" || mount "$DATA"
mkdir -p "$DATA/n360/work" "$DATA/docker"

# 3 Docker keeps everything (images, volumes) on the data volume
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<JSON
{"data-root": "$DATA/docker", "log-driver": "json-file", "log-opts": {"max-size": "20m", "max-file": "5"}}
JSON
systemctl enable -q docker && systemctl restart docker

# 4 code at GIT_REF
if [ ! -d "$CODE/.git" ]; then git clone -q "$REPO_URL" "$CODE"; fi
git -C "$CODE" fetch -q --tags origin "$GIT_REF"
git -C "$CODE" checkout -q --detach FETCH_HEAD
log "code at $GIT_REF ($(git -C "$CODE" rev-parse --short HEAD))"

# 5 settings and passwords: generated once, kept on the data volume, copied to Parameter Store for the laptop
param() { aws ssm get-parameter --name "$P/$1" --with-decryption --query Parameter.Value --output text 2>/dev/null || true; }
put() { aws ssm put-parameter --name "$P/$1" --type "$2" --value "$3" --overwrite >/dev/null; }
if [ ! -f "$ENVF" ]; then
  umask 077
  { echo "N360_CH_PASSWORD=$(openssl rand -hex 20)"
    echo "GRAFANA_CH_PASSWORD=$(openssl rand -hex 20)"
    echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 20)"; } > "$ENVF"
  log "passwords generated"
fi
secret=$(param google_client_secret)
google=false; [ -n "${GOOGLE_CLIENT_ID:-}" ] && [ -n "$secret" ] && [ -n "${N360_DOMAIN:-}" ] && google=true
plugin=$(grep '^GRAFANA_CH_PLUGIN_VERSION=' "$CODE/code/.env.example" | cut -d= -f2)
umask 077
{ grep -E '^(N360_CH_PASSWORD|GRAFANA_CH_PASSWORD|GRAFANA_ADMIN_PASSWORD)=' "$ENVF"
  echo "GRAFANA_CH_PLUGIN_VERSION=$plugin"
  echo "N360_DOMAIN=${N360_DOMAIN:-}"
  if [ -n "${N360_DOMAIN:-}" ]; then echo "GRAFANA_ROOT_URL=https://$N360_DOMAIN/"; echo "GRAFANA_COOKIE_SECURE=true"; fi
  echo "GOOGLE_AUTH_ENABLED=$google"
  echo "GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}"
  echo "GOOGLE_CLIENT_SECRET=$secret"
  echo "GOOGLE_ALLOWED_DOMAINS=${GOOGLE_ALLOWED_DOMAINS:-}"
  echo "GOOGLE_HOSTED_DOMAIN=${GOOGLE_HOSTED_DOMAIN:-}"
  echo "N360_DROP=s3://$BUCKET/exports"
  echo "N360_BACKUP=s3://$BUCKET/backups"
  echo "N360_WORK=$DATA/n360/work"
  echo "N360_PYTHON=python3.12"; } > "$ENVF.new"
mv "$ENVF.new" "$ENVF"
ln -sfn "$ENVF" "$CODE/code/.env"
# shellcheck source=/dev/null
. "$ENVF"
put loader_password SecureString "$N360_CH_PASSWORD"
put grafana_admin_password SecureString "$GRAFANA_ADMIN_PASSWORD"
put backup_location String "s3://$BUCKET/backups"
put drop_location String "s3://$BUCKET/exports"
[ -n "${GOOGLE_CLIENT_ID:-}" ] && [ "$google" = false ] && log "Google sign-in NOT on: set $P/google_client_secret and N360_DOMAIN, then run update.sh"

# 6 systemd: the stack always; daily backup always; hourly loads only if SERVER_LOADS=true
install -m 0644 "$CODE"/infra/aws/host/systemd/n360-* /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q n360-stack.service n360-backup.timer
systemctl restart n360-stack.service
systemctl start n360-backup.timer
if [ "${SERVER_LOADS:-false}" = true ]; then systemctl enable -q --now n360-nightly.timer; log "server loads ON (hourly)"
else systemctl disable -q --now n360-nightly.timer 2>/dev/null || true; log "server loads OFF (loads run from the laptop)"; fi

# 7 tables exist before the first load (so backup, restore and the dashboards work on an empty server)
for _ in $(seq 60); do curl -sf http://localhost:8123/ping >/dev/null && break; sleep 2; done
python3.12 "$CODE/code/load.py" --schema-only --user n360 --password "$N360_CH_PASSWORD"
log "ready: $([ -n "${N360_DOMAIN:-}" ] && echo "https://$N360_DOMAIN (Google sign-in: $google)" || echo "no public access; use code/ops/tunnel.sh")"
