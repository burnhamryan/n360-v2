# 13 · Tonight: N360 on your Mac with real data

Everything stays on the laptop: ClickHouse and Grafana listen on `localhost` only. About 30 minutes.

## 0. You need

- **Docker Desktop**, running (Settings → Resources: 4 GB memory or more).
- **Python 3.12**: `brew install python@3.12` (or the macOS installer from python.org). macOS's own `python3` is
  3.9, too old: pip then finds only clickhouse-connect 0.x and fails on `==1.13.0`.
- **git**, and internet access for the first start (Docker images and the Grafana plugin download once).
- The company pipeline's export folder: one folder per night, `data/<D>/rows/*.ndjson` + `run.json`
  (written by `export_rows.py`). Below it is `~/n360-pipeline/data`; use your real path.

## 1. Get the code and set it up (once)

```bash
git clone https://github.com/burnhamryan/n360-v2 ~/n360 && cd ~/n360
python3.12 -m venv .venv && source .venv/bin/activate
pip install clickhouse-connect==1.13.0
cd code && cp .env.example .env
```

Put three new passwords in `code/.env` (any long random strings; `openssl rand -hex 16` makes one):
`N360_CH_PASSWORD` (loader), `GRAFANA_CH_PASSWORD` (Grafana's read-only user), `GRAFANA_ADMIN_PASSWORD`.
Never commit `.env`.

## 2. Start ClickHouse and Grafana

```bash
docker compose up -d            # from ~/n360/code; first start takes a few minutes
docker compose ps               # both "running"
```

Open http://localhost:3000, sign in as `admin` with `GRAFANA_ADMIN_PASSWORD`. The **N360** folder has 11 dashboards,
empty until the first load.

## 3. Load one night first

```bash
cd ~/n360 && source .venv/bin/activate && set -a && . code/.env && set +a
python3 code/load.py --rows ~/n360-pipeline/data/<latest night>/rows --user n360 --password "$N360_CH_PASSWORD"
```

You should see 8 × `PASS`, then `LOADED`. If a check fails the night is **refused** (`REFUSED`, exit 3) and the
output shows which rows broke it; that is the checks doing their job, so read it before going on.

## 4. Load every night

```bash
N360_DROP=~/n360-pipeline/data N360_WORK=~/n360-work code/ops/nightly.sh
```

It loads each night folder once, oldest first (the history tables build up night by night), skips folders without
`run.json`, never loads a night older than the one already live, and stops at the first error. Run it again any
time: it only loads what is new. Each night takes a few seconds.

**Order note:** if you already loaded the latest night in step 3, older nights are skipped (they would roll the
dashboards back). To load the full history, either skip step 3, or start clean first:
`docker compose down -v && docker compose up -d` (deletes the local databases).

## 5. Look

http://localhost:3000 → N360 → **Overview**. Then **Pipeline health** shows every night loaded or refused.

Optional, once enough nights are in: `python3 code/forecast/challenger.py --user n360 --password "$N360_CH_PASSWORD"`
(challenger backtest; changes nothing else).

## 6. Get the risk model running on real data

Do these in order, from `~/Documents/Github/n360` with `.env` loaded
(`source .venv/bin/activate && set -a && . code/.env && set +a`).

**1. See what is missing.** The report prints counts, dates and shares only, never a customer name or amount. It
does print the labels your Salesforce uses (opportunity types, forecast categories, task subtypes). Read it before
pasting it anywhere.

```bash
python3 code/readiness.py --user n360 --password "$N360_CH_PASSWORD"
```

**2. Pull Salesforce** (docs/18). Any Salesforce access token works: set `SF_INSTANCE_URL` and `SF_ACCESS_TOKEN`
however you get them. Salesforce data changes slowly, so pulling by hand every week or two is enough; leave
`N360_SALESFORCE` unset so the nightly run does not try with an expired token. One way to get a token is your own
login through the Salesforce CLI (`brew install sf` once):

```bash
sf org login web --alias company
```
```bash
export SF_INSTANCE_URL=$(sf org display --target-org company --json | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['instanceUrl'])")
export SF_ACCESS_TOKEN=$(sf org display --target-org company --json | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['accessToken'])")
```
```bash
python3 code/salesforce/sf_pull.py --out ~/n360-work/salesforce
```
```bash
python3 code/salesforce/load_salesforce.py --rows ~/n360-work/salesforce --user n360 --password "$N360_CH_PASSWORD"
```

The pull reads, read-only: accounts, opportunities closing in the last two years or later, contracts, and tasks and
events from the last two years. It changes nothing in Salesforce.

**3. Link the names.** Run the report again. If customers are not linked to a Salesforce account, see which:

```bash
docker exec n360-clickhouse clickhouse-client --user n360 --password "$N360_CH_PASSWORD" --query "SELECT sf_name FROM n360.v_sf_unlinked"
```

Add each pair to `account_overrides` in `code/salesforce/salesforce.json` (Salesforce name → N360 customer), then
pull and load again. If the report says there are no renewal opportunities, or no activity counts as contact, fix
the matching setting in the same file (the report says which).

**4. Rebuild the risk features** as far back as the usage history allows. The report says where it starts; begin
168 days after that (the 12-week volume change needs two 12-week spans):

```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" features --from 2025-07-01 --every 7 --overwrite
```

Past renewals are found from Salesforce renewal opportunities when the contract line has gone from the export.

**5. Set the expected loss rate.** In `code/risk/risk_model.json`, set `prior_mean_rate` to renewals lost ÷ renewals
due over the last two or three years, and bump `version`.

**6. Train, adopt, score.**

```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" train
```

`train` prints a run id, how past renewals were labelled, and a verdict. With few outcomes it stays at the expert
prior; adopting it still fixes the chance at renewal on your own customers.

```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" adopt --run RUN_ID
```
```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" score --as-of all
```

**7. Keep it fed.** Pull Salesforce again every week or two (step 2). Between pulls, "days since the last contact"
misses calls logged since, so it can read up to a pull's age too high. For unattended nightly pulls, ask the admin
for an integration user (docs/19) and set `N360_SALESFORCE=1` in `code/.env`.
Ask CSMs to rate their customers and use the risk register (docs/17): both count in the score.

## Every night after

```bash
cd ~/n360 && source .venv/bin/activate
N360_DROP=~/n360-pipeline/data N360_WORK=~/n360-work code/ops/nightly.sh
```

## Keep in mind

- **Real data stays local.** Do not switch on the Claude chat (`docs/09_CHAT.md`) with real data until the company
  approves sending query results to Anthropic (`08_DECISIONS.md`).
- **Back up the history** (it cannot be rebuilt from exports), e.g. weekly:
  `N360_BACKUP=~/n360-backups code/ops/backup.sh`. The same backup moves the history to AWS later
  (`docs/12_AWS.md`, "Moving your laptop's history to AWS").
- **Memory.** Give Docker Desktop 8 GB (Settings → Resources). ClickHouse is capped at 60% of that
  (`code/clickhouse/`), so a query that is too big fails with "memory limit exceeded" instead of the server being
  killed (`Exited (137)`). If ClickHouse shows `Exited`, run `docker compose up -d` from `code/`.
- Stop: `docker compose stop` (data kept). Delete everything: `docker compose down -v`.
- Alerts are off (`docs/11_ALERTS.md`). Anomaly thresholds: `n360.settings` in `code/schema.sql`.
- This setup has been tested end to end on Linux Docker with synthetic data, not yet on a Mac or with real exports.
  If something differs from the export format, `load.py` stops with the table and column; send me the message
  (not the data).
