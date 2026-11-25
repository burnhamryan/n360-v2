#!/usr/bin/env bash
# Print shell exports that point the ops scripts at the AWS server through tunnel.sh. Credentials come from AWS
# Systems Manager Parameter Store (/n360/<name>/...), where the server's first boot stored them; nothing is saved
# on the laptop.    eval "$(code/ops/remote_env.sh)"
set -euo pipefail
NAME=${N360_NAME:-n360}; P=/n360/$NAME
param() { aws ssm get-parameter --name "$P/$1" --with-decryption --query Parameter.Value --output text; }
printf 'export CH_URL=%q\n' "http://localhost:${CH_LOCAL_PORT:-18123}"
printf 'export CH_USER=n360\n'
printf 'export CH_PASSWORD=%q N360_CH_PASSWORD=%q\n' "$(param loader_password)" "$(param loader_password)"
printf 'export GRAFANA_URL=%q\n' "http://localhost:${GRAFANA_LOCAL_PORT:-13000}"
printf 'export GRAFANA_ADMIN_PASSWORD=%q\n' "$(param grafana_admin_password)"
printf 'export N360_BACKUP=%q\n' "$(param backup_location)"
echo "# N360 on AWS ($NAME): loads and backups now go to the server through the tunnel" >&2
