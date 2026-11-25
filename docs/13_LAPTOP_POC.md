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

http://localhost:3000 → N360 → **Portfolio overview**. Then **Pipeline health** shows every night loaded or refused.

Optional, once enough nights are in: `python3 code/forecast/challenger.py --user n360 --password "$N360_CH_PASSWORD"`
(challenger backtest; changes nothing else).

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
- Stop: `docker compose stop` (data kept). Delete everything: `docker compose down -v`.
- Alerts are off (`docs/11_ALERTS.md`). Anomaly thresholds: `n360.settings` in `code/schema.sql`.
- This setup has been tested end to end on Linux Docker with synthetic data, not yet on a Mac or with real exports.
  If something differs from the export format, `load.py` stops with the table and column; send me the message
  (not the data).
