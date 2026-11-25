#!/usr/bin/env bash
# Open private tunnels from your laptop to the N360 server on AWS, through AWS Session Manager (no open ports,
# no SSH keys). Leave it running; Ctrl-C closes both.
#   code/ops/tunnel.sh        # ClickHouse -> localhost:18123 (loads), Grafana -> localhost:13000 (admin)
# Then, in another terminal:  eval "$(code/ops/remote_env.sh)"  and run code/ops/nightly.sh or code/load.py.
# Needs the AWS CLI v2 and the Session Manager plugin, and AWS credentials allowed to start sessions on the
# instance. N360_NAME = the instance's Name tag (default n360); AWS_PROFILE / AWS_REGION as usual.
set -euo pipefail
NAME=${N360_NAME:-n360}
CH_PORT=${CH_LOCAL_PORT:-18123}; GF_PORT=${GRAFANA_LOCAL_PORT:-13000}
id=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=running" \
       --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ -z "$id" ] || [ "$id" = None ]; then echo "no running instance tagged Name=$NAME"; exit 1; fi
fwd() { aws ssm start-session --target "$id" --document-name AWS-StartPortForwardingSession \
          --parameters "portNumber=$1,localPortNumber=$2" > /dev/null; }
fwd 8123 "$CH_PORT" & p1=$!
fwd 3000 "$GF_PORT" & p2=$!
trap 'kill $p1 $p2 2>/dev/null' EXIT INT TERM
echo "tunnels to $id open: ClickHouse localhost:$CH_PORT, Grafana localhost:$GF_PORT (Ctrl-C to close)"
wait
