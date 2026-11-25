# CHANGES · 25 Nov 2026 · Risk model fixes after two reviews

Fixes to the reworked risk model (`code/risk/`), each with a test. Synthetic data only.

**Numbers that were wrong**
- **The chance at renewal no longer depends on who else is renewing.** The prior's level was re-set every night on
  the customers then in their renewal window. On a 27-customer history, one customer with unchanged readings moved
  between 12% and 44%. The level is now fixed: the settings give it until a run is adopted, and `train` sets it once
  on the whole stored record (at least 200 snapshots), so it stays with the adopted run.
- **An early opt-out is a loss.** A churn logged more than 120 days before the term ended was labelled "kept". The
  outcome window now opens when the customer enters the renewal window (180 days out).
- **"Nothing recorded" is no longer "renewed".** A renewal counts as kept only on record, or when a contract ran on
  after the date. Otherwise it is unconfirmed: counted, reported by `train` and the backtest, never used.

**Risk is per account (decided)**
- An account has one renewal date: its largest running line's end date. A small add-on ending first is not the
  renewal. Renewal dates within 30 days of each other are one renewal.

**Outcomes**
- A downsell is judged against the customer's previous won **renewal**, not any won deal.
- A register outcome is dated by the day it happened (`risk_register.py close --date`, else the day it was closed).
  A risk closed as "saved" is not a renewal outcome.

**Smaller**
- Silent and drop days are counted over the same 30 settled days whether stored that night or rebuilt.
- "Open churn risks" is not reported before the register was first used.
- The explanation's pushes add up exactly for a trained model too.
- A feature given weight 0 no longer breaks training; a step that cannot improve the fit is not taken.
- The backtest also judges the scorecard without the CSM's own view, and says how past renewals were labelled.
- `horizon_days` is gone from `risk_model.json`; the dashboard reads `eligible_days_to_renewal` (same value).

**Tests that claimed too much.** Three checks held on one random seed only (the original code fails them on other
seeds). The claim that a no-signal feature is pulled down is withdrawn: with some 80 renewals lost it is not,
reliably. docs/21 section 4 says what the synthetic test does and does not show.

**After pulling:** `load.py --schema-only`, then `features --from <first night> --every 7 --overwrite`,
`train`, `adopt --run <id>`, `score --as-of all` (docs/21 section 5). Tested: 63 model checks; `code/ci.sh chdb`.

# CHANGES · 25 Nov 2026 · ClickHouse memory caps

ClickHouse was killed on a Mac (`Exited (137)`) when the Risk model dashboard ran its queries: in Docker Desktop it
allowed itself 90% of the VM, leaving too little for Grafana and the VM. Now (`code/clickhouse/`, mounted by
`docker-compose.yml`):
- the server uses at most 60% of the machine's memory;
- one query uses at most 2 GB, and big GROUP BY and ORDER BY spill to disk after 1 GB.

A query that is too big now fails with "memory limit exceeded"; the server stays up. Tested: an oversized GROUP BY
as the read-only Grafana user spilled to disk and finished. Pick it up with `docker compose up -d` from `code/`.

# CHANGES · 26 Nov 2026 · Customer risk model

**A customer risk model you can test, tune and run, on its own or inside N360** (`code/risk/`, `docs/21_RISK_MODEL.md`).
It gives each customer:
- **health** (0–100), with the points each feature costs;
- a **band**, with hard reds that force "at risk";
- the **chance of churn or downsell at the next renewal**;
- **revenue at risk**.

It has 20 features in 7 dimensions, each computed as it stood on any past date. The probability is a logistic
regression that starts as an expert prior built from the scorecard and learns from outcomes as they arrive. A
walk-forward backtest compares it with the scorecard and the rules. The new dashboard is `n360-risk-model`.
Tested on synthetic data with a known truth: AUC about 0.77 out of time; the trained model is better calibrated than
the prior; churn was flagged a month or more ahead.

| Piece | What |
|---|---|
| `code/risk/model.py` | Feature risks, scorecard, hard reds, the expert prior, ridge-to-prior logistic regression (pure Python), explanations, metrics (AUC, Brier, precision/recall at 10%, reliability) |
| `code/risk/backtest.py` | Labels (renewal window, horizon observed), walk-forward backtest, lead time |
| `code/risk/features.py` | 20 point-in-time features from N360's ClickHouse (usage, contract, quality, adoption, Salesforce, tickets, register, CSM rating) |
| `code/risk/risk_model.py` | `features`, `train`, `score`, `backtest`, `explain`, `outcome`, `nightly`; with N360 or `--no-db` on files |
| `code/risk/risk_model.json` | Every setting (PROPOSED): dimensions, weights, anchors, hard reds, bands, prior, ridge, horizon, backtest |
| `code/schema.sql` | `risk_features`, `risk_outcome`, `risk_model_runs`, `risk_scores`, `risk_contributions`; views `v_risk_outcomes`, `v_risk_model`, `v_risk_score`, `v_risk_drivers` |
| `sample_data/generate_risk_panel.py` | A synthetic 220-customer, 130-week history with a known risk process |
| Tests, CI | `code/risk/test_risk_model.py` (25 checks). The N360 path in CI: features, train, score history, view checks. Nightly scoring in `nightly.sh` (training stays manual). Model tables backed up |

---

# CHANGES · 25 Nov 2026 · Risk dashboard and Salesforce

**New Risk dashboard (`n360-risk`), and N360 now reads Salesforce.** The dashboard shows which customers are at risk,
why, and who is acting, for CSMs and customer architects. It has:
- 12 explainable signals per customer, with forced reds;
- a risk register and a CSM rating;
- a renewal runway;
- a per-VA architect view;
- nightly risk history.

Salesforce is read-only: accounts, renewals, contracts and activity. Design and research: `docs/17_RISK_RESEARCH.md`.
Salesforce: `docs/18_SALESFORCE.md`. Tested on synthetic data and a fake Salesforce API only.

| Piece | What |
|---|---|
| `code/salesforce/` | `sf_pull.py`: reads Salesforce over its REST API, standard library only, using an existing token or OAuth client credentials, with SOQL paging. `salesforce.json` sets what to read. `load_salesforce.py` stages, checks and swaps. `fake_salesforce.py` serves the API for tests |
| `code/schema.sql` | `sf_*` tables and `sf_runs`. `risk_register`, `risk_pulse`, `risk_history`. Views `v_sf_account`, `v_sf_unlinked`, `v_risk_signal`, `v_risk_customer`, `v_risk_register`, `v_risk_pulse`. 16 PROPOSED risk settings |
| `code/risk_register.py` | Add, update and close risks; record a CSM rating. The reason codes are a short fixed list |
| `code/load.py`, `code/ops/nightly.sh` | Risk history every night. `N360_SALESFORCE=1` pulls Salesforce before the nights. History tables backed up |
| `sample_data/generate_salesforce.py` | Synthetic Salesforce records that plant every signal: lost renewal, Omitted forecast, no contact, a differing account name, an unlinked prospect |
| CI | Covers the Salesforce path end to end: pulled through the fake API, paged, with a wrong secret refused. Also the register, every planted signal level, and risk history on every night |

---

# CHANGES · 25 Nov 2026 · Dashboards rebuilt to the v2 redesign

**The 11 dashboards are reviewed and rebuilt to the v2 redesign. The changes:**
- **The v1.1 page's contract statuses and colours** replace red/amber/green, from one palette with no red.
- **Every page starts with a sentence** that answers its question, then a freshness stamp.
- **A new Customer health page.**
- **Nothing the v1.1 page shows is lost.**

Review and decisions: `docs/16_DASHBOARD_GAPS.md`. Brief: `docs/15_DASHBOARD_REVIEW_HANDOFF.md`. Uids are
unchanged, so links and bookmarks keep working. Tested on synthetic data only.

| Piece | What |
|---|---|
| `code/schema.sql` | New views: `v_contract_status` (11 statuses + renewal risk), `v_status_night`, `v_va_change`, `v_needs_attention`, `v_freshness`, `v_feature_use`, `v_customer_notes`. 14 new PROPOSED settings in `n360.settings` |
| `code/grafana/build_dashboards.py` | One palette (`COLOUR`), one status map (`STATUS`). New building blocks: sentence, freshness stamp, tile with change and 30-night line, status columns, reference line. Overview, Contracts, Customer, Adoption and Requests rebuilt; Customer health (`n360-health`) new; the sentence and stamp added to Contract line, VA, Forecast, Anomalies, Containment and Pipeline health |
| `sample_data/generate_synthetic.py` | Two invented customers plant every status: Maple Clinics (too early, billed not recorded, unmapped, under review) and Willow Energy (renewal risk; a busy VA down a third) |
| Gates | `test_build.py`: one palette, no rules in dashboards, plain-word titles, page shape (rows 1–3 on screen at 1440 × 900), no layout gaps, every status has a word and a colour. `test_panels.py`: one number, one value (`n360Metric`), All on multi-value filters. `test_views.py`: every planted status, renewal risk, empty never zero, needs attention, newest night = today |

**What was wrong before** (docs/16 section 1, in short):
- Red, green and default palette colours meant several different things.
- A line at 14% of billed with its term over showed green.
- No "needs a decision" list and no Customer health page.
- Internal keys appeared on screen.
- Headline numbers had no reference.
- Partial weeks were drawn as full bars.

---

# CHANGES · 25 Nov 2026 · AWS, self-hosted

**N360 can now run on AWS as one private server with the same open-source stack that CI tests**, created by
Terraform, with Grafana behind HTTPS and Google sign-in, and loads from your laptop through a private tunnel.
Runbook: `docs/12_AWS.md`. **Not yet run in a real AWS account.**

Also in this branch: **alerts off by default** (all 7 rules provisioned paused, `docs/11_ALERTS.md`). That commit
landed after PR #2 was merged, so `main` still had the rules active.

| Piece | What |
|---|---|
| `infra/aws/terraform` | VPC (or an existing subnet), EC2 Amazon Linux 2023, separate encrypted data volume, Elastic IP when public, S3 (exports, backups), IAM (server role; an operator policy for you), AWS Backup daily snapshots, settings in Parameter Store |
| `infra/aws/host` | First boot and updates (`bootstrap.sh`, `update.sh`), the stack (`stack.sh`), systemd: stack, daily backup, hourly server loads (off until `server_loads = true`) |
| `code/docker-compose.prod.yml` | Restart and log limits, Grafana Google sign-in (OSS `auth.google`, allowed domain, PKCE, Viewer by default), Caddy for HTTPS (Let's Encrypt) |
| `code/ops` | `nightly.sh` (new exports from S3 or a folder), `backup.sh` / `restore.sh` (history tables), `tunnel.sh`, `remote_env.sh`, `grafana_admin.sh`, `test_ops.sh` |
| `code/load.py` | `--schema-only` (new server, before a restore) |
| CI | Two new jobs: ops (end to end against ClickHouse + S3 via moto) and infra (Terraform fmt/validate, shellcheck, compose config) |

**Bugs found and fixed while testing:**
- Row checksums used `cityHash64(*)`, which is NULL for any row with a NULL column, so the B21 CI check
  "forecast of record unchanged" compared NULL with NULL and could not fail. Fixed everywhere.
- Inline security-group rules would have left 80/443 open after switching to tunnel-only; rules are now separate resources.
- Amazon Linux 2023's `python3` is 3.9, too old for clickhouse-connect; the server uses Python 3.12.
- A required `N360_DOMAIN` in the Caddy service broke tunnel-only mode; `stack.sh` exited silently without an
  `N360_DOMAIN` line; Caddy rejected an empty email; AWS rejects apostrophes in security-group descriptions.

| Check | Result |
|---|---|
| Ops end to end (14 nights, backup, restore onto a new server, reload, new night, older re-export) | Pass (ClickHouse 25.8 + moto S3) |
| HTTPS via Caddy; Google sign-in redirect (client ID, redirect URI, hosted domain, PKCE); password form hidden | Pass (local, dummy Google client) |
| Tunnel-only mode (no domain): Caddy removed, 443 closed | Pass |
| Amazon Linux 2023: packages, Compose 2.40.3 checksum, Python 3.12 + clickhouse-connect | Pass (container) |
| Terraform 1.17.2 / aws 6.74.0: validate; apply against moto (31 resources); public to tunnel-only removes the IP and both rules | Pass |
| Not tested | A real AWS account (first boot, EBS attach, Let's Encrypt, Session Manager, AWS Backup); a real Google sign-in |

---

# CHANGES · 24 Nov 2026 · B15–B21 (history, accuracy, freshness, alerts, review log, anomaly v2, challenger)

**The backlog is done: every item B1–B23 is built and tested on synthetic data.** What is left is company-side
work and the open questions in `08`.

| Item | What was built | Tested |
|---|---|---|
| Settings | `n360.settings`: every proposed threshold in one view (pace, anomaly, freshness, engine, asks, v2); all views read it | View tests unchanged |
| B15 | `forecast_history`, copied by `load.py` from the forecast of record after each swap (no pipeline change); optional backfill file; generator `--forecast-backfill DAYS` | One row per line/pool per loaded night (13 of 14 nights; the refused night has none) |
| B16 | `v_forecast_accuracy` (MAPE at 30/60/90 days: the stored forecast evaluated, never refitted), `v_forecast_change`, `v_forecast_drift`; Forecast panels 6.5, 6.6 | Harbor Chat MAPE recomputed by hand in Python matches the view |
| B17 | `source_freshness` (per night); Pipeline "Days behind, night by night"; generator `--stale SOURCE=DAYS` | 2 days behind → amber, 3 → red |
| B18 | 7 alert rules as code (`build_alerts.py` → `provisioning/alerting/`): load failed, data stale, source stale, VA silent/dropped, cross-account event, newly over billed, crossing soon; placeholder contact points routed by audience | Rule queries checked after nights 7, 9, 11, 12 and final, plus two stale cases. A provisional-only silence does not fire. In Grafana 13.3.1 all 7 load healthy; Lakeside, Data stale and (after a bad night) Load failed fire |
| B19 | `anomaly_log` (the nightly load never touches it), `code/review_anomaly.py`, `v_anomaly_review` (newest review wins), `v_flag_quality`; Anomalies "Review" and "Flag quality per week" | Outcomes join back to flags; week of 9 Nov: 3 reviewed, 1 real, 1 expected, 1 data issue |
| B20 | `v_containment_week`, `v_measure_gap_week`, `v_anomaly_v2` (plus pace change and freshness); Anomalies "Other signals" | New synthetic customer Orchid Health carries the planted cases: exactly 3 signals, none elsewhere |
| B21 | `code/forecast/challenger.py`: weekday Holt-Winters against the straight line at weekly cutoffs; writes `forecast_challenger` and `docs/10_CHALLENGER_BACKTEST.md`; Forecast panel | CI fails if `n360.forecast` changes (checksum). Result: the challenger is worse overall (90 days: 3.9% vs 1.3% MAPE) but better on most lines at 30 days for 6 straight weeks; not adopted |

**Bugs found on the way (fixed):**
- `runs.loaded_at` has one-second precision, so loads in the same second tied and "the latest run" could be an older night (now broken by `data_date`). Found by server-mode CI.
- Two reviews of one flag in the same second tied the same way (`anomaly_log.reviewed_at` is now milliseconds).
- Alias collisions and a correlated join that ClickHouse refuses.

**Synthetic data:** added **Orchid Health** (anomaly v2 cases). Existing customers' numbers are unchanged in multi-night
mode; customer count is now 11.

**New open questions in `08`:** alert recipients and timing (placeholders now); anomaly v2 thresholds; challenger
settings; who may record reviews.

| Check | Result |
|---|---|
| `ci.sh chdb`, `ci.sh server` | Pass: loads, all view tests (B5, B15, B16, B19, B20, Phase 2), alert scenarios, stale scenarios, challenger checksum, dashboards and alert rules regenerate identically, every panel query as Grafana's read-only user |
| Grafana 13.3.1 | Forecast, Anomalies, Pipeline render (screenshots updated); alert rules provisioned and evaluated |

---

# CHANGES · 24 Nov 2026 · all 11 dashboards (B7–B14, B10, Phase 2 B22–B23)

**Every dashboard in `04` is built, rendered in Grafana 13.3.1 on synthetic data, and every drill-down link was
clicked.** Repository: `burnhamryan/n360-v2` (`main` = the pack through B6; this work on `claude/all-dashboards`).
Phase 2 (Adoption, Requests) runs on a **proposed** table design; its field meanings are open questions in `08`.

| Dashboard | uid | Highlights on synthetic data |
|---|---|---|
| Portfolio overview | `n360-overview` | (B6) unchanged |
| Contracts | `n360-contracts` | B10: header, Back link, pace chips, links; the 4 known problems fixed (pool counted → 3 above billed; Harbor Voice no longer plotted; "Sheet terms" for no SOW; formats) |
| Customer | `n360-customer` | Header stats, lines with pace, pooled entitlement (pool row + its lines "At the pool"), weekly use, containment, VAs, recent anomalies, requests |
| Contract line | `n360-line` | Used / billed / % / term / forecast stats; how it is measured (rule, funnel, page match, source, billed basis); cumulative used vs billed with dashed forecast and red billed line; month bars; VAs; night by night (after 7 nights) |
| VA | `n360-va` | Status (Went silent (provisional) for Lakeside), daily against the expected band, red settled / orange provisional flags, reader and configuration |
| Forecast and runway | `n360-forecast` | Crossing in 30/60/90, runway lanes (Harbor red to 17 Dec, Kestrel blue, grey to term end then dark), runway table with flags and "term already ended", step changes, accuracy note (B15) |
| Anomalies | `n360-anomalies` | Kind and include-provisional controls, heatmap lanes, feed, cross-account events, weekly flags |
| Containment | `n360-containment` | 4 weeks vs previous 4 per VA, weekly per VA, VAs without handoff data |
| Pipeline health | `n360-pipeline` | 14 nights (night 9 red), checks passed n of 8, freshness, measure mix, rules not live |
| Adoption and coverage | `n360-adoption` | Engine per VA (V7 flagged), playbooks vs intents, idle playbook, MCP / supervisory, authoring and assistant use, config changes |
| Requests | `n360-requests` | By area (incl. "Not tagged yet"), top asks (3 and 2 customers), by customer, untagged list |

**Rules kept in one place (views):** pace, runway and step change in `v_line_pace`; the anomaly flag threshold
(4) now a named constant in `v_va_day_anomaly`, which also exposes the expected range; heatmap bands in
`v_anomaly_heat`; freshness in `v_source_freshness`; "V8 or later" in `v_va_config_latest`; "several customers"
in `v_top_asks`. Dashboards only map labels to colours (`test_build.py` fails on any threshold in a dashboard).

**Phase 2 (proposed):** tables `va_config_snapshot`, `playbook_day` (added), `cortex_day`, `explorer_day`,
`ps_ticket`; the generator plants V7 VAs, two config changes, an idle playbook, shared asks and untagged
tickets, using its own random streams (core synthetic numbers unchanged). `load.py` loads these only when the
export has the file: a night without them loads exactly as before (tested).

**Fixed on the way (found in screenshots):** Grafana's default colour scale (red above 80) on stats; green
"Not reported yet" / "At the pool" cells; a runway chart broken by Grafana's own gap-filling; "All" customers
leaving out Cedar Logistics (no contract line); `join_use_nulls` in a view on ClickHouse 25.8 (earlier);
a compiled `__pycache__` file tracked since the pack as received (untracked).

| Check | Result |
|---|---|
| `ci.sh chdb`, `ci.sh server` (14 nights, night 9 bad) | Pass: loads, view tests (incl. 4 Phase 2 cases), dashboards regenerate identically, all 98 panel queries (11 dashboards) as Grafana's read-only user |
| Grafana 13.3.1, all 11 dashboards | Render, no panel errors (screenshots on the PR) |
| Drill-downs clicked | Overview → Customer / Contract line; Customer → line / VA; line → VA; Contracts → Customer / line; Forecast → line; Anomalies, Adoption → VA; Back links keep customer and time range |
| Night without Phase 2 files | LOADED; Phase 2 tables empty, dashboards say why |

GitHub Actions: both CI jobs (chdb, ClickHouse 25.8) pass on GitHub for `main` and this branch.

Not done: B15–B21 (forecast history and accuracy, freshness table and alert, alert rules, anomaly log,
anomaly v2, challenger forecast). Not tested: macOS / Docker Desktop, Claude Desktop.

New open questions in `08`: Phase 2 field meanings; "several customers" = 2; z ±2 watch band and freshness
2/3 days as proposed; pooled panel always shown.

---

# CHANGES · 24 Nov 2026 · amber test case, and backlog B6 (Portfolio overview)

## End-to-end test (fresh start, as on the Mac)

Database and Grafana volumes wiped, then the README steps: `docker compose up -d` → generate one night →
`load.py` as the loader user → Grafana. Results:

| Step | Result |
|---|---|
| Stack start | ClickHouse and Grafana up in about 8 s; data source health "Data source is working" |
| Generate + load | 8 × PASS, LOADED, data through 19 Nov 2026 |
| Grafana | Folder N360 with `n360-overview` and `n360-contracts`; both render with no panel errors |
| Every panel query, as Grafana's read-only user | All pass (2 dashboards, 15 panels, 1 variable) |
| View checks, as Grafana's read-only user | All pass |
| Locks | Saving a provisioned dashboard refused ("Cannot save provisioned dashboard"); `DROP VIEW` as `grafana` refused |
| CI, both engines (14 nights, night 9 bad) | `ci.sh chdb` and `ci.sh server` pass |

Still **not** tested: macOS / Docker Desktop, GitHub Actions itself, Claude Desktop with the chat config.
This container also needs a test-only override (lower `nofile`, plugin mounted because containers have
no DNS here). Neither is in the pack.

## Amber test case

New synthetic customer **Kestrel Travel** (one Chat line, billed 125,000): 85.5% of billed at 72.3% of term
(13.2 points ahead), crossing billed in 45 days → **amber** ("more than 10 points ahead of term"), with
margin on both sides (not near 10 points, not near the 30-day red line). `test_views.py` checks it.

In multi-night mode (CI) every other customer's numbers are unchanged: daily rows, totals, forecasts,
anomaly flags and the 13 Nov event, compared before and after. In single-night mode (README quick start)
the shared random stream shifts, so other customers move slightly (e.g. Bluebird pool 122.0% vs 122.3%).
The tests check rules, not exact figures. Customer count is now 10.

## B6: Portfolio overview `n360-overview`

| # | Panel | Data | Drills to |
|---|---|---|---|
| 1.1 | Data through | `runs` | Pipeline health |
| — | Last night (header) | `runs` | |
| 1.2 | Customers · Contract lines · Pools | `account`, `contract_line`, `pool` | |
| 1.3 | Lines above billed (red when > 0) | `v_line_pace`: lines and pools, pool counted once → **3** | Contracts |
| 1.4 | Crossing billed in 90 days | `v_line_pace` (stored forecast) → 2 (Harbor, Kestrel) | Forecast |
| 1.5 | Drops or silences, 7 settled days | `v_customer_attention` → 4 (Lakeside) | Anomalies |
| 1.6 | Customers needing attention | `v_customer_attention`: worst line, measure, %, pace chip, why, crosses billed, drops 7d | Customer (row) |
| 1.7 | Conversations per week, top 5 + Other | `va_day`, complete weeks only | Customer (bar) |
| 1.8 | Use against billed, by line | `v_line_pace`: bar capped at 100%, true %, % of term | Contract line (pool → Customer) |

Every link was clicked in Chromium and lands on the right uid with the right variable and time range. The targets
(`n360-customer`, `n360-line`, `n360-forecast`, `n360-anomalies`, `n360-pipeline`) are B7–B11, so they
show "Dashboard not found" until built.

Differences from the spec (also in `04`): 1.6 sorted red, amber, then pace gap; chip reads Red / Amber / Green
with a "Why" column; 1.8 is a table with bars because Grafana OSS bar gauges can't draw the % of term
tick; 1.6 lists only customers needing attention (red/amber, or a settled drop/silence).

Fixed on the way, found by screenshots: double URL-encoding in links (Grafana encodes the values itself);
bar-gauge labels swallowed the link column; titles truncated at narrow widths.

| File | Change |
|---|---|
| `sample_data/generate_synthetic.py` | Kestrel Travel (amber) |
| `code/schema.sql` | `v_line_pace.label` (e.g. "Chat (Juniper Marketing)"); `v_customer_attention.worst_label`, `pace_rank` |
| `code/grafana/build_dashboards.py` | `overview()`; helpers `date_sql`, `count_col`, `width`, `hidden`, `panel_link`, `path_link`, `red_when_any`; `UID`, `CROSS_WINDOW_DAYS`, `TOP_CUSTOMERS`; pace chip words |
| `code/grafana/dashboards/n360-overview.json` (new) | Generated |
| `code/test_views.py` | Amber case; pool check by rule, not exact figure |
| `docs/04_DASHBOARDS.md`, `README.md` | Spec 1 status and differences; status row |

---

# CHANGES · 24 Nov 2026 · backlog B5 (views for the dashboards)

**Three new views in `schema.sql`. The pace colour is decided in `v_line_pace`, as you decided, and the four
proposed thresholds exist only at the top of that view.** `build_dashboards.py` no longer holds any
threshold. It only maps the view's `pace` label to a colour and a word (`PACE_LABEL`).

| View | One row per | Gives |
|---|---|---|
| `v_line_pace` | Entitlement: each line not in a pool, and each pool once | % of billed, % of term, pace gap, crossing date (from the stored forecast), `pace` (red / amber / green / not measurable / not implemented / not read), `pace_reason` in words, `term_ended` |
| `v_customer_attention` | Customer | Worst entitlement (red, then amber, then green; ties by pace gap), counts of red and amber entitlements, drops and silences and spikes in the 7 newest settled days, provisional flags |
| `v_anomaly_events` | Settled day and direction flagged on ≥ 3 accounts | Number of accounts and VAs, the accounts, the kinds |

Acceptance on the 14-night synthetic data (`code/test_views.py`, run in CI on both engines; on the
server as Grafana's read-only user):

| Case | Result |
|---|---|
| Atlas Streaming | Red: 282.6% of billed |
| Harbor Telecom Chat | Red: 91.0% of billed, crosses billed 17 Dec 2026, in 28 days |
| Bluebird Air pool | One row for the pool, none for its 3 lines. Red at 122.3% |
| Harbor Voice / Pinecrest Voice | "not measurable" / "not implemented", no colour |
| Lakeside Insurance | 4 settled silent days in the last 7 settled days, plus 3 provisional ones counted apart. Quiet weekends are below the 20-a-day floor, so not scored |
| 13 Nov spike | One event: spikes on 3 accounts (Bluebird, Harbor, Northwind), 4 VAs |
| Cedar Logistics (no contract lines) | Empty (not read), not a colour |
| Planted fault: red crossing window 30 → 20 days | CI fails on Harbor Telecom |

**Version difference caught by CI.** In `v_customer_attention`, the `join_use_nulls` setting inside the view
works on the embedded engine (ClickHouse 26.11) but not on the pinned server 25.8, where a customer with
no lines got `''` instead of empty. I replaced it with an explicit check that works on both.

Also checked in Grafana: a sample table with the pace chip renders. Red "Over or crossing soon", green
"On pace", grey "Not measurable" / "Not implemented", and empty values grey. The Contracts dashboard is
unchanged (B10 adds colour).

**Gaps and questions**
1. **No amber case in the synthetic data.** The amber branch (more than 10 points ahead of term, or
   crossing within 60 days) is in the view but not tested. Adding one amber line to the generator
   changes the synthetic numbers that the chat trap test and the earlier results use, so I haven't. Say if you want it.
2. **Ended terms** (in `08`): Lakeside ended 31 Oct at 13.9% of billed and shows green "on pace". The
   proposed rule doesn't cover ended terms. The view reports `term_ended` so a dashboard can say so.
3. **Spikes and drops on the same day** (in `08`): I group them as two events, by direction. One line to change.
4. **% of term for pools** repeats `v_line_status`'s formula, because pools carry dates, not `term_days`.
   It's the same formula written twice (against rule 4), flagged in a comment. The Bluebird pool reads
   80.8% of term and its lines 87.5%, because of the open term-end question.
5. "7 settled days" = the 7 newest days that are no longer provisional (data-through −9 to −3).

| File | Change |
|---|---|
| `code/schema.sql` | `v_line_pace`, `v_customer_attention`, `v_anomaly_events` appended. Existing views unchanged |
| `code/test_views.py` (new) | Acceptance cases above |
| `code/ci.sh` | Runs `test_views.py` after the loads |
| `code/grafana/build_dashboards.py` | `PACE` thresholds removed. `PACE_LABEL`, `pace_chip()`; `coloured()` takes view labels, not thresholds |
| `code/grafana/test_build.py` | Sample uses `v_line_pace`. New check: no dashboard sets thresholds |
| `chat/n360_chat_instructions.md` | Points to the three views (trap test not re-run, see below) |
| `docs/02`, `03`, `04`, `05`, `08`, `README.md` | Views listed. Pace decision recorded as decided 24 Nov 2026. 2 new open questions |

Not re-run: the 10-question chat test (the instructions only gained pointers to the new views).

---

# CHANGES · 24 Nov 2026 · backlog B4 (dashboard generator refactor)

**`build_dashboards.py` is now built from shared building blocks; the Contracts dashboard is unchanged
in meaning.** Every query, layout, unit, variable and setting in `n360-contracts.json` is identical to
before (compared field by field). Only the panel descriptions changed shape (below).

| Building block | What it gives B6–B10 |
|---|---|
| `PACE` | The 4 proposed pace thresholds (10 pts ahead, above 100%, crossing ≤ 60 d, ≤ 30 d), in one place, marked proposed |
| `COLOUR`, `RUN_STATUS` | Display colours and run-status words, in one place. Grey for empty |
| `describe()` | Every panel description: definition, then **Source**, **Filter**, **Window** (rule 5) |
| `query_var()`, `var_account()` | Dashboard variables. `line` and `va` are added when their dashboards are specified in detail (B7–B9) |
| `header()`, `data_through()`, `last_load()` | Top row: Data through (links to Pipeline health), Last night (Loaded / Stopped: data checks failed) |
| `drill()`, `with_links()` | Data links in the `04` form `/d/<uid>?var-<name>=${__data.fields.<column>}&${__url_time_range}` |
| `back_link()` | "Back to Portfolio overview", keeps the time range |
| `coloured()`, `pct_billed_colours()`, `pace_gap_colours()`, `label_colours()`, `empty_grey()` | Threshold colours from `PACE`/`COLOUR`; empty cells grey "Not reported yet" |
| `field_config()`, `override()`, `pct()`, `dashboard()` | The repeated JSON, once |

The Contracts dashboard does not use the header, links or colours yet: adding them changes what it
shows, which is B10.

**Panel descriptions.** Same facts, new shape. Example, "Lines above billed": the definition text is
unchanged, followed by `Source: n360.v_line_status · Filter: Customer · Window: each line from its start
date to the data-through day`. Where the old text had a source sentence, it moved into **Source**; I
added the view names (e.g. `n360.v_line_status`) and the filter and window as the queries apply them.
The "Pooled lines are counted once, as their pool" sentence on "Lines above billed" is kept as is,
though it is wrong (item 1 under "Found, not fixed" below; fix in B10).

**One bug found in the new helpers and fixed before use:** Grafana paints an empty cell in the base
threshold colour, so an unread pace gap showed "Not reported yet" in green. `coloured()` now always
adds a grey mapping for empty.

| Check | Result |
|---|---|
| Contracts JSON, old vs new, descriptions aside | Identical (and identical when serialised) |
| `test_build.py`: rebuild a sample with `PACE`/`COLOUR` replaced by marker values | Every colour and threshold in the output is a marker. Planting a literal `100` or `'red'` in a helper fails it |
| `test_build.py`: description shape | Every panel has Source, Filter, Window |
| Sample dashboard (header, pace columns, data link, Back link) in Grafana 13.3.1 | Renders, no errors; Back link → `/d/n360-overview?from=…&to=…`; clicking a customer opens `/d/n360-customer?var-account=Atlas%20Streaming&from=…`; empty pace gap grey |
| Contracts in Grafana after the refactor | 6 panels, no errors, no empty panels |
| `ci.sh chdb`, `ci.sh server` (now also run `test_build.py`) | Both pass |

Not tested: the "Stopped: data checks failed" colour on screen (the query and mapping are tested; the
last night in the test data was loaded). Target dashboards don't exist yet, so data links lead to
"Dashboard not found" until B6–B9.

Open question added to `08_DECISIONS.md`: **where the pace rule lives.** `04` asks for the thresholds as
constants in the builder (done); rule 4 and B5 put the colour in a view. Red depends on the
forecast, so it can only be worked out in SQL. My recommendation: the view decides (B5), and the
builder only maps its label to a colour. Until you decide, the numbers sit in `PACE` only.

| File | Change |
|---|---|
| `code/grafana/build_dashboards.py` | Rewritten into building blocks; same Contracts output |
| `code/grafana/dashboards/n360-contracts.json` | Regenerated: descriptions only |
| `code/grafana/test_build.py` (new) | Tests above |
| `code/ci.sh` | Runs `test_build.py` after `test_panels.py` |
| `README.md`, `docs/08_DECISIONS.md`, `.gitignore` | Status row; pace-rule question; `__pycache__/` |

---

# CHANGES · 24 Nov 2026 · backlog B1, B2, B3, B3a

All tested on synthetic data in a Linux container (Docker 29.9, ClickHouse 25.8.33.6, Grafana 13.3.1,
ClickHouse plugin 4.23.5, Python 3.13). **Not tested on macOS / Docker Desktop.** Paths match the pack.

## Two fixes you need before the first start on the Mac

| # | Problem | Effect if not fixed | Fix |
|---|---|---|---|
| 1 | `init/grafana_user.sh` gave Grafana's user `readonly = 1`. The ClickHouse plugin sets `max_execution_time` on every query, which `readonly = 1` refuses | Data source health check fails; **every panel errors** | `readonly = 2 CONST`: still no writes or DDL (tested), and a query cannot switch `readonly` off (tested) |
| 2 | `GF_INSTALL_PLUGINS` is deprecated in Grafana 13; plugin version unpinned | Warning now, breakage later; plugin version drifts | `GF_PLUGINS_PREINSTALL_SYNC` with `GRAFANA_CH_PLUGIN_VERSION` from `.env` (4.23.5, latest on 24 Nov 2026). A failed install now stops start-up instead of leaving blank panels |

Also check: the zip stored every file as owner-only (`600`, folders `700`). Grafana in Docker runs as
another user and could not read the provisioning folder (no N360 folder, no dashboard). A git checkout
gives normal `644`; if you unzip the pack directly, run `chmod -R go+rX` on it.

## What changed

| File | Change | Item |
|---|---|---|
| `code/docker-compose.yml` | Plugin preinstall as above | B1 |
| `code/.env.example` | `GRAFANA_CH_PLUGIN_VERSION=4.23.5` | B1 |
| `code/init/grafana_user.sh` | `readonly = 2 CONST`, with the reason in a comment | B1 |
| `code/grafana/test_panels.py` (new) | Runs every panel and variable query in the dashboard JSON with Grafana macros expanded; fails on any error or empty panel | B1, B3 |
| `sample_data/generate_synthetic.py` | `--nights N`, `--bad-night k`. Multi-night: per-VA random streams (a day reads the same every night), planted events on fixed dates, newest 2 days read 3% and 10% low until they settle. Bad night: Northwind's line total is off by 1, so `line_measured_reconciles` refuses it. **Single-night output unchanged** (compared file by file, run_id aside) | B2 |
| `code/ci.sh` (new), `.github/workflows/ci.yml` (new) | Generate 14 nights → load in order (exactly night 9 refused) → check `runs` and history → regenerate dashboards and require the committed JSON to match → run every panel query. Two jobs: chdb, and ClickHouse server 25.8 as user `grafana` (created from `init/grafana_user.sh`, so it is defined once) | B3 |
| `chat/n360_chat_instructions.md` (new) | Instructions for the Claude people chat with: dashboards first, then views; the 12 rules from `03`/`07` | B3a |
| `chat/mcp_config.example.json` (new) | `mcp-grafana@2.2.0`, write tools off, 16-tool allowlist | B3a |
| `code/grafana/mcp_service_account.sh` (new) | Creates the Viewer service account and prints a token | B3a |
| `docs/09_CHAT.md` (new) | Setup, the three locks, the trap-question test | B3a |
| `docs/06_BACKLOG.md`, `docs/08_DECISIONS.md`, `README.md` | B3a added; 4 open questions; status table | |
| `.gitignore` (new) | Generated rows, `code/.env` | |

New dependencies: none in the pipeline. Chat needs `uv` on the machine running Claude.

## How it was tested

| Check | Result |
|---|---|
| Pack as received: generate, load (chdb), build | 8 × PASS, LOADED; rebuilt dashboard JSON identical to the shipped one |
| B1: `docker compose up`, load through the server | 8 × PASS; Grafana shows folder N360, Contracts renders all 6 panels in Chromium (no errors, no empty panels) |
| B1: Grafana user | Reads `n360`; refused on `n360_next`, INSERT, DROP, CREATE, `SETTINGS readonly = 0` |
| B1: UI edits | Saving the provisioned dashboard refused |
| B2: 14 nights, night 9 bad | 13 loaded + 1 failed_checks; `line_totals_history` 13 nights; after the bad night live tables stayed on night 8; settled days identical across nights |
| B2: edge cases on the final night | Atlas 282.6%; Harbor Chat crosses 17 Dec (≤ 30 d); Lakeside silent 9 days (last 3 provisional); 13 Nov spike on 3 accounts; no false drops from settling |
| B3: `ci.sh chdb` and `ci.sh server` (against a fresh ClickHouse 25.8 volume) | Both pass |
| B3: planted faults | Broken panel SQL, a check failing on a good night, hand-edited dashboard JSON: each fails CI |
| B3a: chat | 10 trap questions answered correctly by a fresh Claude through Grafana (table in `09_CHAT.md`); a write via `query_sql` refused |

**Not tested:** the workflow on GitHub itself (no repository attached); macOS / Docker Desktop; the
chat config in Claude Desktop (tested with a stdio client against the same binary and flags).
In this container ClickHouse needed a lower `nofile` limit and the plugin had to be mounted (no DNS
from containers). Both were done in a test-only override file, not in the pack.

## Found, not fixed (for B4 / B10, or your decision)

Contracts dashboard as rendered:
1. **"Lines above billed" shows 2, but 3 entitlements are over** (Atlas, Pinecrest Chat, and the
   Bluebird pool at 122.3%). Its description says pools are "counted once, as their pool"; the query
   only reads `v_line_status`.
2. **Harbor Voice (not measurable) is plotted** in "Daily use per contract line". `line_day` carries
   engaged as `measured` for it. Rule: not measurable shows no usage.
3. "Contract unit" shows "Not reported yet" for lines with no SOW override: an empty string is a real
   "none", not "not read".
4. Formatting against rule 14: no thousands separators; dates as `2026-10-31 00:00:00`; pool % with
   no unit or decimal; series names prefixed "used " / "containment ".

Open questions (also in `08_DECISIONS.md`):
5. **Usage after a line's end date.** Synthetic `reported_measured` counts every day to data-through.
   Atlas: 282.6% (all days) vs 268.4% (within term); Lakeside also. Does the real pipeline stop at the end date?
6. **Term-end convention.** Synthetic ends a 12-month line on the last day of the month before its
   start month (29 Jan start → 31 Dec end), while the pool ends 28 Jan. Does the contract sheet work that way?
7. **Chat with real data** sends query results to Anthropic; that is a company decision.
8. **Who may chat, and with which customers.** Grafana OSS can't restrict rows per person; this would
   need ClickHouse row policies.

Version notes: chdb 4.5.1 embeds ClickHouse 26.11, newer than the pinned server 25.8, so CI runs both.
ClickHouse 25.8 image resolved to 25.8.33.6. Grafana plugin 4.23.5 and mcp-grafana 2.2.0 were the
latest on 24 Nov 2026.
