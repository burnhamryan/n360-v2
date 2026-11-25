# 12 · Running N360 on AWS (self-hosted, open source)

**One small private server running the same Docker stack that is tested in CI: ClickHouse 25.8 (Apache 2.0),
Grafana 13.3.1 OSS (AGPL), Caddy 2.10.2 (Apache 2.0).** Grafana is public over HTTPS with Google sign-in, or
private (tunnel only). ClickHouse is never public. **Loads run from your laptop for now**, through a private
tunnel; the server can take them over later with one setting.

```
 your laptop                          AWS (your account)
 ───────────                          ────────────────────────────────────────────────────────────────
 exports ─► code/ops/nightly.sh ──┐   EC2 (Amazon Linux 2023, no inbound except 443/80 when public)
            or code/load.py       │     ├─ ClickHouse  127.0.0.1:8123  ◄── tunnel (Session Manager)
 code/ops/tunnel.sh ══════════════┘     ├─ Grafana     127.0.0.1:3000  ◄── tunnel (admin)
                                        └─ Caddy :443 ── Google sign-in ── people's browsers
                                      EBS data volume (/data: databases, passwords, certificates), snapshot daily
                                      S3 bucket: exports/ (for server loads later), backups/ (history tables)
                                      Parameter Store /n360/<name>/: settings, generated passwords, Google secret
```

Everything is code: `infra/aws/terraform` (AWS resources), `infra/aws/host` (server setup), `code/ops` (loads,
backups, tunnel). Nothing is clicked together in the console except the Google OAuth client and DNS.

## What it costs (estimate, us-east-1 on-demand; check AWS pricing for your region)

| Item | Monthly |
|---|---|
| EC2 t3.large (2 vCPU, 8 GB) | about $60 (about $38 with a 1-year Savings Plan) |
| EBS: 20 GB root + 50 GB data, gp3 | about $6 |
| Public IPv4 address | about $4 |
| Snapshots, S3, Parameter Store, Session Manager | a few dollars at N360's size |
| **Total** | **about $70** |

No NAT gateway and no load balancer, on purpose: the server reaches the internet through an internet gateway and
accepts nothing inbound except HTTPS when you want it public.

## Before you start

| Need | Notes |
|---|---|
| Company approval for customer data in this AWS account and region | Open in `08` (EU / SG data residency) |
| An AWS account you control, admin rights to run Terraform | Day-to-day you only need the `n360-operator` policy |
| Terraform ≥ 1.6 (tested 1.17.2), AWS CLI v2 + the Session Manager plugin on your laptop | `brew install terraform awscli session-manager-plugin` |
| A hostname for Grafana, e.g. `n360.yourcompany.com`, and access to its DNS | Only for public access |
| A Google OAuth client (below) | Only for Google sign-in |

## 1. Google sign-in (Grafana OSS: free, open source)

In Google Cloud Console (a project your company owns):
1. **OAuth consent screen**: user type **Internal** (only your Google Workspace accounts can sign in).
2. **Credentials → Create OAuth client ID → Web application.**
   Authorized redirect URI: `https://n360.yourcompany.com/login/google`
3. Keep the **client ID** (goes in Terraform) and **client secret** (goes in Parameter Store, step 3).

Grafana restricts sign-in to `google_allowed_domains` as well. Everyone who signs in is a **Viewer** (read-only;
dashboards are provisioned and cannot be edited in the UI anyway). Promote yourself once:
`code/ops/grafana_admin.sh role you@yourcompany.com Admin` (through the tunnel, below).

## 2. Create the server

```bash
cd infra/aws/terraform
cp terraform.tfvars.example terraform.tfvars     # region, domain, google_client_id, allowed domain
terraform init
terraform apply
```

It creates about 30 resources (network, server, data volume, bucket, roles, snapshot plan, settings parameter) and
prints `next_steps`. Keep the Terraform state safe (`backend.tf.example` puts it in S3).

## 3. After apply

```bash
# a) let your own IAM user/role use the tunnel, parameters and bucket
aws iam attach-user-policy --user-name YOU --policy-arn "$(terraform output -raw operator_policy_arn)"
# b) DNS: an A record for your domain -> terraform output public_ip
# c) the Google client secret (never in Terraform or Git)
aws ssm put-parameter --name /n360/n360/google_client_secret --type SecureString --value 'GOCSPX-...'
# d) apply it on the server (first boot may have finished before the secret existed)
aws ssm send-command --document-name AWS-RunShellScript --instance-ids "$(terraform output -raw instance_id)" \
  --parameters 'commands=["/opt/n360/infra/aws/host/update.sh"]'
```

First boot takes about 5 minutes (log on the server: `/var/log/n360-bootstrap.log`). It installs Docker, mounts the
data volume, generates every password (kept on the data volume and copied to Parameter Store, never in Terraform),
starts the stack, creates the empty tables, and gets the HTTPS certificate once DNS points at it.

## 4. Load from your laptop (the current way of working)

```bash
code/ops/tunnel.sh                       # terminal 1: leave running (ClickHouse :18123, Grafana :13000)
eval "$(code/ops/remote_env.sh)"         # terminal 2: point the scripts at the server
N360_DROP=~/n360/exports code/ops/nightly.sh     # every new export in that folder, oldest first
#   or one export:  python3 code/load.py --rows ~/n360/exports/2026-11-25/rows --url "$CH_URL" --user n360 --password "$CH_PASSWORD"
code/ops/backup.sh                       # optional: history backup to S3 now (the server also does it daily)
```

`nightly.sh` loads each export once (by `run_id`), never loads a night older than the live one, and stops at the
first error. The 8 data checks run exactly as before; a refused night leaves the previous good night on screen.

**Later, to let the server load by itself:** have the exporter copy each night to
`s3://<bucket>/exports/<data_date>/rows/`, set `server_loads = true`, `terraform apply`, run `update.sh`. The
server then checks every hour. You can still load from the laptop; the same `run_id` is never loaded twice.

## 5. Moving your laptop's history to AWS

The history tables (`runs`, `line_totals_history`, `forecast_history`, `source_freshness`, `anomaly_log`, `risk_register`, `risk_pulse`, `risk_history`, `sf_runs`,
`forecast_challenger`) cannot be rebuilt from exports. To bring them over from a laptop install:

```bash
N360_BACKUP=s3://<bucket>/backups code/ops/backup.sh            # on the laptop, against the LOCAL ClickHouse
eval "$(code/ops/remote_env.sh)"; code/ops/restore.sh latest     # through the tunnel, into the EMPTY server
code/ops/nightly.sh --reload-latest                              # with N360_DROP set: live tables back
```

`restore.sh` refuses a table that already has rows and checks every table's row count and checksum.

## Day to day

| Task | How |
|---|---|
| Open Grafana | `https://<domain>` (Google), or `http://localhost:13000` through the tunnel |
| Make someone an Editor/Admin | `code/ops/grafana_admin.sh role person@company.com Editor` (they sign in once first) |
| Upgrade to a new version of this repo | set `git_ref` (a tag), `terraform apply`, run `update.sh` (or `update.sh v1.2.0` to try one) |
| Change domain, Google settings, server loads | change the variable, `terraform apply`, run `update.sh` |
| Turn alerts on | `docs/11_ALERTS.md`, then `update.sh` |
| Server shell | `aws ssm start-session --target <instance_id>` (no SSH, no keys) |
| See the setup log / services | `sudo tail /var/log/n360-bootstrap.log`; `systemctl status n360-stack n360-backup.timer` |

## Backups and recovery

| Layer | What | Where | Restore |
|---|---|---|---|
| 1 | Daily snapshot of the whole data volume (AWS Backup, `snapshot_retention_days`, default 35) | AWS Backup vault | Restore the snapshot to a volume, swap it in (or attach to a new server) |
| 2 | Daily copy of the history tables (`backup.sh`, 09:30 UTC, and after server loads) | `s3://<bucket>/backups/`, 365 days | `restore.sh` onto any N360 (AWS or laptop) |
| 3 | Exports | `s3://<bucket>/exports/` (90 days) or your laptop | `nightly.sh` |

The data volume has `prevent_destroy` in Terraform: `terraform destroy` stops before deleting it. To tear down for
good, back up first, then remove that line.

## Security notes

- No SSH and no open admin ports: Session Manager (IAM-authenticated, logged) for shell and tunnels.
- ClickHouse and Grafana listen on 127.0.0.1 inside the server; only Caddy (443, 80 for the certificate challenge
  and redirect) is reachable, and only when a domain is set. `https_allowed_cidrs` can narrow it to office/VPN ranges.
- Grafana's database user is read-only (`readonly = 2`), as on the laptop; chat via `mcp-grafana` stays read-only too.
- Encrypted EBS volumes and S3 (bucket private, TLS only, versioned). IMDSv2 only.
- Passwords are generated on the server, stored on the encrypted data volume and in Parameter Store SecureStrings;
  Terraform state holds none of them.

## What has been tested, and what has not

Tested (synthetic data): the Docker stack with the production layer, HTTPS through Caddy, Google sign-in up to
Google's redirect (client ID, redirect URI, hosted domain, PKCE) with the password form hidden; tunnel-only mode;
`nightly.sh` / `backup.sh` / `restore.sh` end to end against ClickHouse and S3 (moto); server packages, Compose
download and checksum, Python 3.12 + clickhouse-connect on Amazon Linux 2023; Terraform validate and a full apply
against moto (31 resources; switching to tunnel-only closes 80/443). CI runs the ops test and the infra checks.

**Not tested: a real AWS account** (first boot on EC2, EBS attach, Let's Encrypt, Session Manager tunnels, AWS
Backup) and a real Google sign-in. Expect the first `terraform apply` to be the real test; the bootstrap log says
where it stopped, and `update.sh` re-runs it safely.
