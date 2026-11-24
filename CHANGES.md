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

Not done: B15–B21 (forecast history and accuracy, freshness table and alert, alert rules, anomaly log,
anomaly v2, challenger forecast). Not tested: macOS, GitHub Actions itself, Claude Desktop.

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
