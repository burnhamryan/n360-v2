# 06 · Backlog

**Status 24 Nov 2026:** B1–B14, B3a done on synthetic data; B22–B23 built on a proposed design (field meanings open in `08`). Next: B15–B21. See `CHANGES.md`.

**Work top to bottom.** Every item is buildable and testable on synthetic data. "Done" means it
meets its acceptance criteria on synthetic data, is listed in `CHANGES.md`, and any new table or
metric is added to `02_DATA_CONTRACT.md` / `03_METRICS.md`.

## Tier 1: make what exists run end to end

| # | Item | Acceptance |
|---|---|---|
| B1 | **First `docker compose up`.** Start the stack, load synthetic rows through the ClickHouse server (not chdb), open Grafana. Fix whatever breaks (ClickHouse plugin install, `$GRAFANA_CH_PASSWORD` interpolation in provisioning, the init script creating the read-only `grafana` user) | Grafana shows the N360 folder; Contracts dashboard renders all 6 panels with synthetic data; Grafana user cannot read `n360_next` |
| B2 | **Multi-night synthetic.** Add `--nights N` to the generator: N consecutive nights, small realistic drift, one night that fails a check on purpose (`--bad-night k`) | Loading 14 nights in order leaves 13 loaded + 1 failed_checks in `runs`; `line_totals_history` has 13 nights; live tables hold the last good night |
| B3 | **CI.** A GitHub Actions workflow: generate synthetic → load with chdb → 8 checks pass → build dashboards → JSON is valid → every panel's SQL executes with macros substituted | Workflow passes; breaking a check or a panel query fails it |
| B3a | **Chat with the data** through Grafana's MCP server (`docs/09_CHAT.md`): Viewer service account, write tools off, chat instructions file | Fresh Claude answers 10 trap questions correctly through Grafana; every write refused |

## Tier 2: the Phase 1 dashboards (specs in `04_DASHBOARDS.md`)

| # | Item | Acceptance |
|---|---|---|
| B4 | **Refactor `build_dashboards.py`** into helpers: header row, variables, data links, Back link, pace thresholds as one constant, panel description template (source · filter · window · definition) | Contracts dashboard regenerates byte-identical in meaning; no threshold or colour literal appears twice |
| B5 | **Views for the dashboards**: `v_line_pace` (pace gap + colour by the proposed rule), `v_customer_attention` (worst line per customer), `v_anomaly_events` (same-day flags across ≥ 3 accounts grouped) | Each view returns the synthetic edge cases correctly (Atlas red, Harbor crossing ≤ 30 days red, pool measured once, Lakeside silent, the cross-account spike grouped) |
| B6 | Portfolio overview `n360-overview` | Matches spec 1; row click drills to Customer with `var-account` set |
| B7 | Customer `n360-customer` | Matches spec 2; not-implemented line greyed; pool panel only when the account has one |
| B8 | Contract line `n360-line` | Matches spec 3; forecast dashed to horizon; billed line; measure explained |
| B9 | VA `n360-va` | Matches spec 4; expected range whiskers; provisional flags orange |
| B10 | Contracts: add pace colours, data links, header, Back link | Matches spec 5 |
| B11 | Forecast and runway `n360-forecast` | Matches spec 6 |
| B12 | Anomalies `n360-anomalies` | Matches spec 7, including the provisional toggle and grouped events |
| B13 | Containment `n360-containment` | Matches spec 8; no cross-channel number anywhere |
| B14 | Pipeline health `n360-pipeline` | Matches spec 11 using `runs` from B2's 14 nights |

## Tier 3: history, accuracy, alerting

| # | Item | Acceptance |
|---|---|---|
| B15 | `forecast_history` table (append-only, one row per line/pool per night) + loader support | 14 synthetic nights → 14 rows per line |
| B16 | Forecast accuracy SQL (MAPE at 30/60/90 days, crossing-date drift) + panels 6.5, 6.6 | Correct on a hand-checked synthetic line |
| B17 | `source_freshness` table + freshness panel + stale alert | Synthetic source made stale shows amber at 2, red at 3 |
| B18 | Alert rules as code (`code/grafana/provisioning/alerting/`), contact points as placeholders | Rules load in Grafana; firing on synthetic failures (bad night, silent VA, stale source); provisional days never fire |
| B19 | `anomaly_log` table + a simple way to record review outcomes (real / expected / data issue) | Outcomes join back to flags; weekly flag-quality query |

## Tier 4: models and Phase 2

| # | Item | Acceptance |
|---|---|---|
| B20 | Anomaly v2 views (containment drop, pace change, measure gap) | Planted synthetic cases detected, no false positives on the rest |
| B21 | Challenger forecast (weekday-aware, Python) + backtest harness writing `forecast_challenger` | Backtest report comparing MAPE vs straight line on synthetic; no change to the forecast of record |
| B22 | Phase 2 schemas + synthetic generators: `va_config_snapshot`, `cortex_day`, `explorer_day`, `ps_ticket` | Designed in `02_DATA_CONTRACT.md`, generated, loaded, checked |
| B23 | Adoption and coverage `n360-adoption`, Requests `n360-requests` | Match specs 9, 10 |

## Not yours (company side; don't attempt)

Pulling real data; the company exporter and `contract_measures.py`; nightly orchestration on the
laptop; cloud hosting; the parity check against the existing page; anything needing real names,
tickets or contracts.
