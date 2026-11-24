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
| ClickHouse schema: 13 tables, 7 views (incl. anomaly detection; pace, customer attention, cross-account events) | `code/schema.sql` | Done, tested |
| View tests (synthetic edge cases) | `code/test_views.py` | Run in CI on both engines (B5) |
| Data checks run before every load (8) | `code/checks.sql` | Done, all pass on synthetic |
| Loader: stage → check → atomic swap | `code/load.py` | Done (server or embedded `chdb`) |
| Local stack: ClickHouse 25.8 + Grafana 13.3.1, localhost only | `code/docker-compose.yml`, `code/init/`, `code/grafana/provisioning/` | Started and tested in Linux Docker 24 Nov 2026 (B1); not yet on the Mac |
| Dashboard generator (1 of 11 dashboards: Contracts) | `code/grafana/build_dashboards.py` | Done; renders in Grafana with plugin 4.23.5. Refactored into building blocks (B4): header, variables, data links, Back link, pace and colour settings in one place, description template |
| Building-block test | `code/grafana/test_build.py` | Settings written once; description shape; sample header, links and colours run and render |
| Panel query test | `code/grafana/test_panels.py` | Runs every panel and variable query with macros expanded |
| Synthetic data, one night or N nights | `sample_data/generate_synthetic.py` (`--nights`, `--bad-night`) | Done; 14 nights → 13 loaded, 1 refused |
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
