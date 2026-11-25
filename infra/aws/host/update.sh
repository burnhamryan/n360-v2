#!/usr/bin/env bash
# Re-apply the settings and code on the server, e.g. after `terraform apply`. Run on the server (as root):
#   sudo /opt/n360/infra/aws/host/update.sh             # the settings and git_ref from Terraform (Parameter Store)
#   sudo /opt/n360/infra/aws/host/update.sh v1.2.0      # run another tag, branch or commit until the next update
# or from the laptop:  aws ssm send-command --document-name AWS-RunShellScript --instance-ids <id> \
#                        --parameters 'commands=["/opt/n360/infra/aws/host/update.sh"]'
# Data, passwords and history are untouched; the containers restart (about a minute without dashboards).
set -euo pipefail
# shellcheck source=/dev/null
. /etc/n360/n360.conf
cfg=$(aws ssm get-parameter --region "$AWS_REGION" --name "/n360/$N360_NAME/config" --query Parameter.Value --output text 2>/dev/null || true)
ref=${1:-$(printf '%s\n' "$cfg" | sed -n "s/^GIT_REF='\(.*\)'$/\1/p")}
ref=${ref:-${GIT_REF:?no GIT_REF}}
# fetch first: the new version's bootstrap.sh is the one that runs
git -C /opt/n360 fetch -q --tags origin "$ref"
git -C /opt/n360 checkout -q --detach FETCH_HEAD
if [ -n "${1:-}" ]; then export N360_GIT_REF=$1; fi
exec /opt/n360/infra/aws/host/bootstrap.sh
