#!/usr/bin/env bash
# Move the server to another version of the code and re-apply settings. Run on the server (as root):
#   sudo /opt/n360/infra/aws/host/update.sh v1.2.0      # a tag, branch or commit; no argument = same ref, refetched
# or from the laptop:  aws ssm send-command --document-name AWS-RunShellScript --targets Key=tag:Name,Values=n360 \
#                        --parameters 'commands=["/opt/n360/infra/aws/host/update.sh main"]'
# Data, passwords and history are untouched; the containers restart (a minute without dashboards).
set -euo pipefail
CONF=/etc/n360/n360.conf
if [ -n "${1:-}" ]; then sed -i "s|^GIT_REF=.*|GIT_REF=$1|" "$CONF"; fi
exec /opt/n360/infra/aws/host/bootstrap.sh
