# N360 analytics — handoff pack

**Read this file first, then `docs/07_WORKING_RULES.md`, then the backlog.**

You (a Claude instance) are continuing the build of N360's analytics layer for Ryan Burnham.
This pack is everything needed to keep building **without any real company data**: the design,
the generic code, and a synthetic data generator that reproduces every edge case the real data has.

## What N360 is, in one paragraph

N360 tracks, per enterprise customer, how much of each signed contract is being used, how fast,
when it will run out, and whether anything unusual is happening. A nightly pipeline (at Ryan's
company, not in this pack) pulls usage from production systems, applies each contract's billing
rules, and writes the results. This project moves the *output* side from a hand-built web page to
**ClickHouse (storage, time-series) + Grafana 13.3.1 OSS (dashboards defined as code)**, with
anomaly detection and forecasting built in.

## Boundary: what you must not do

- **No real data, ever.** Use `sample_data/generate_synthetic.py`. If a task seems to need real
  customer names, numbers, hostnames, tickets or documents, stop and write it up as a question
  for Ryan instead.
- **No access to company systems.** You don't connect to any company database, tracker, drive or
  chat. Ryan carries your work back and applies it himself.
- **Don't invent business facts.** Contract terms, rule definitions and thresholds marked
  *proposed* are Ryan's to decide. Build the mechanism with the value as a setting, and list the
  open question.

## What exists (all in this pack, all tested on synthetic data on 24 Nov 2026)

| Piece | File | State |
|---|---|---|
| ClickHouse schema: 13 tables + 5 Phase 2 tables (proposed), 13 views | `code/schema.sql` | Done, tested on synthetic |
| View tests (synthetic edge cases) | `code/test_views.py` | Run in CI on both engines (B5) |
| Data checks run before every load (8) | `code/checks.sql` | Done, all pass on synthetic |
| Loader: stage → check → atomic swap | `code/load.py` | Done (server or embedded `chdb`); Phase 2 files loaded only when present |
| Local stack: ClickHouse 25.8 + Grafana 13.3.1, localhost only | `code/docker-compose.yml`, `code/init/`, `code/grafana/provisioning/` | Started and tested in Linux Docker 24 Nov 2026 (B1); not yet on the Mac |
| Dashboard generator: **all 11 dashboards** | `code/grafana/build_dashboards.py` → `code/grafana/dashboards/*.json` | Built and rendered in Grafana 13.3.1 with plugin 4.23.5 on synthetic data; every drill-down link clicked. Phase 2 (Adoption, Requests) on a proposed design |
| Building-block test | `code/grafana/test_build.py` | Settings written once; description shape; sample header, links and colours run and render |
| Panel query test | `code/grafana/test_panels.py` | Runs every panel and variable query with macros expanded |
| Synthetic data, one night or N nights | `sample_data/generate_synthetic.py` (`--nights`, `--bad-night`, `--forecast-backfill`, `--stale`) | Done; 14 nights → 13 loaded, 1 refused |
| Forecast history and accuracy (B15–B16) | `forecast_history`, `v_forecast_accuracy` | Built; Forecast panels 6.5, 6.6 |
| Alert rules as code (B18) | `code/grafana/build_alerts.py` → `provisioning/alerting/`; `test_alerts.py` | Built and tested; **paused** (not needed yet). Configure and switch on per `docs/11_ALERTS.md` |
| Anomaly review log (B19) | `code/review_anomaly.py`, `anomaly_log` | Built; Anomalies feed and weekly flag quality |
| Anomaly detection v2 (B20) | `v_anomaly_v2` | Built; planted cases caught, no false positives |
| AWS, self-hosted (open source) | `infra/aws/terraform`, `infra/aws/host`, `code/docker-compose.prod.yml`, `code/ops/` | Built; tested on synthetic data and against moto; **not yet run in a real AWS account**. Runbook: `docs/12_AWS.md` |
| Ops: nightly load, backup, restore, tunnel | `code/ops/*.sh` | Tested end to end (ClickHouse + S3); in CI |
| Challenger forecast (B21) | `code/forecast/challenger.py`, `docs/10_CHALLENGER_BACKTEST.md` | Backtest only; the forecast of record is unchanged |
| CI | `.github/workflows/ci.yml`, `code/ci.sh` | Steps pass locally (chdb and server 25.8); not yet run on GitHub |
| Chat with the data | `docs/09_CHAT.md`, `chat/`, `code/grafana/mcp_service_account.sh` | Tested on synthetic through Grafana; real data is an open question |

## The docs

| File | What it covers |
|---|---|
| `docs/01_ARCHITECTURE.md` | Flow, layers, why ClickHouse and Grafana, load and swap, how real data enters at the company |
| `docs/02_DATA_CONTRACT.md` | Every table and column, keys, NULL rules, row format, the 8 checks |
| `docs/03_METRICS.md` | Every metric: definition, formula, grain; words allowed on screen |
| `docs/04_DASHBOARDS.md` | All 11 dashboards, panel by panel, drill-downs, conventions, design notes |
| `docs/05_ANOMALY_AND_FORECAST.md` | Methods, thresholds (some proposed), what's planned |
| `docs/06_BACKLOG.md` | What to build next, in order, with acceptance criteria |
| `docs/07_WORKING_RULES.md` | How Ryan wants work done. Non-negotiable |
| `docs/08_DECISIONS.md` | Decided, proposed, and open questions |
| `docs/11_ALERTS.md` | Alert rules: off for now; how to switch on, set recipients and timing |
| `docs/10_CHALLENGER_BACKTEST.md` | Challenger vs straight-line backtest (synthetic), regenerated by `code/forecast/challenger.py` |
| `docs/16_DASHBOARD_GAPS.md` | Dashboard review: what was wrong, the v2 page structure, the contract statuses and colours, decisions for Ryan |
| `docs/15_DASHBOARD_REVIEW_HANDOFF.md` | The review brief: v1.1 page and v2 spec, condensed |
| `docs/13_LAPTOP_POC.md` | **Start here for real data on your Mac**: set up, load every night, look |
| `docs/12_AWS.md` | Running N360 on AWS: create, load from the laptop, Google sign-in, backups, upgrades |
| `docs/09_CHAT.md` | Chatting with the data through Grafana's MCP server: setup, locks, test |

## First 15 minutes

```bash
pip install chdb clickhouse-connect
python3 sample_data/generate_synthetic.py --out sample_data/rows
python3 code/load.py --rows sample_data/rows --chdb /tmp/n360    # expect 8 × PASS, then LOADED
python3 code/grafana/build_dashboards.py                          # writes code/grafana/dashboards/*.json
```

Then with Docker:

```bash
cd code && cp .env.example .env && docker compose up -d
python3 load.py --rows ../sample_data/rows --user n360 --password <N360_CH_PASSWORD from .env>
# open http://localhost:3000 → folder N360
```

## How your work gets back to Ryan

Deliver changes as files with the **same paths** as this pack, plus a short `CHANGES.md` (what
changed, why, how you tested it on synthetic data, any open question). Keep table and column
names unchanged unless the backlog says otherwise: the company pipeline writes these exact names,
and a rename there is Ryan's decision. Ryan reviews, copies the files into the company repository,
and runs them against real data himself.
