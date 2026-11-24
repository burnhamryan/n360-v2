# 04 · Dashboards

**All eleven built 24 Nov 2026 on synthetic data (Phase 2 tables are a proposed design). Differences from
the specs are noted under each heading.** Eleven dashboards in Grafana 13.3.1 OSS, linked so a reader starts at the portfolio and clicks
down to one customer, one contract line, one VA.** Each spec is what `build_dashboards.py` must
generate. One is built (Contracts); ten are yours to build.

Designs for 7 of them (Portfolio, Customer, Contract line, VA, Forecast, Anomalies, Pipeline
health) exist at the company with real data and are **awaiting Ryan's approval**. They are not in
this pack because they contain real customer data; their layout is described below in enough
detail to build from. Build against the spec. Ryan will reconcile with the approved designs.

## Drill-down map

```
                         Portfolio overview ─ ─ ─ ─► Pipeline health (linked from every header)
        ┌──────────┬──────────┬────┴─────┬───────────┐
    Contracts   Forecast   Customer   Anomalies   Containment
        │          │        │    │        │           │
        └──────────┴──► Contract line ──► VA ◄────────┴───┘
                                          │
                              Phase 2: Adoption and coverage, Requests
```

## Conventions (every dashboard)

| Item | Rule |
|---|---|
| Folder, uid | Folder "N360"; uid `n360-<name>`, never changed (links depend on it) |
| Header | "Data through" stat, last load status, link back to Portfolio overview |
| Variables | `account`, `line`, `va` as needed, from ClickHouse; time range default 90 days |
| Drill-down | Data links on table rows and series: `/d/<uid>?var-<name>=${__data.fields.<column>}&${__url_time_range}`. Every target has a "Back to" link |
| Colour | Green, amber, red only for a stated meaning. Empty = grey "Not reported yet", never red |
| Units | Thousands separators; % to 1 dp; dates "4 Dec 2026" |
| Descriptions | Every panel: source, filter, window, definition (from `03_METRICS.md`) |
| Editing | Provisioned read-only; changes by pull request only |
| Layout | Fixed grid positions (Grafana 13.3 defaults to auto grid; N360 sets positions so builds match designs) |
| Theme | Grafana dark |

## Pace colours (PROPOSED, awaiting Ryan)

| Colour | Rule |
|---|---|
| Green | % of billed ≤ % of term + 10 points |
| Amber | % of billed more than 10 points ahead of % of term, or crossing billed within 60 days |
| Red | Above 100% of billed, or crossing billed within 30 days |
| Grey | Not measurable, not implemented, or not read |

The view decides the colour (decided 24 Nov 2026): `n360.v_line_pace` holds the four thresholds at its top, the
only place they exist. `build_dashboards.py` maps the view's `pace` label to a display colour (`PACE_LABEL`).

## 1. Portfolio overview · `n360-overview` (BUILT 24 Nov 2026, B6)
Question: which customers need attention this week, and why? Audience: leadership.

Built as specified, with these differences for Ryan to accept or change: 1.6 sorts red first, then
amber, then by pace gap (pure pace-gap order would put a red line crossing in 28 days below an amber
line); the pace chip reads Red / Amber / Green with the view's reason in a "Why" column beside it.
1.6 lists customers with red or amber pace, or a settled drop or silence. 1.8 is a table with a bar
per row (capped at 100%, true % beside it) and % of term in its own column: Grafana OSS bar gauges
cannot draw a tick at % of term. 1.7 counts complete weeks only, and a week with an unread day is a gap.

| # | Panel | Visual | Data | Drills to |
|---|---|---|---|---|
| 1.1 | Data through | Stat | latest loaded `runs.window_end` | Pipeline health |
| 1.2 | Customers | Stat (sub: lines, pools) | `account` | |
| 1.3 | Lines above billed | Stat, red when > 0 | `v_line_status` pct > 100 (+ pools) | Contracts |
| 1.4 | Crossing billed in 90 days | Stat | `forecast.cross_date` | Forecast |
| 1.5 | Drops or silences, 7 settled days | Stat | anomaly view, not provisional | Anomalies |
| 1.6 | Customers needing attention | Table: customer, worst line, measure, % billed, % term, pace chip, crosses billed, anomalies 7d; sorted by pace gap | views + forecast | Customer (row) |
| 1.7 | Conversations per week, top customers | Stacked bars, top 5 + Other | `va_day` | Customer |
| 1.8 | Use against billed, by line | Bar gauge, bar capped at 100%, true % as text, white tick at % of term | views | Contract line |

## 2. Customer · `n360-customer` (variable `account`) (BUILT, B7)
Differences: the pooled-entitlement panel shows for every customer and says "No pooled entitlement" when there is none (Grafana OSS cannot hide a panel by data). "Total used against billed" is shown only when the customer's measured lines share one measure and one term (rules 2, 5), otherwise "Not added up". "Main line" = the line or pool with the most use.

Header stats: contract lines (note not-implemented), worst line % billed with term, total used vs billed (excluding not-implemented), main line's 30-day rate with step-change note, anomalies 7d (count provisional). Panels: contract lines table (channel, measure, used, billed, % billed chip, % term, forecast to horizon, runs out; not-implemented row greyed with "Not reported yet"); pooled entitlement (when present); used per week per line (lines); containment per VA weekly (one line per VA, label "Name (Channel)"); VAs table (VA, channel, region, conversations 30d, containment 30d, anomalies 30d); recent anomalies (provisional labelled); requests (Phase 2). Drills: line → Contract line, VA → VA.

## 3. Contract line · `n360-line` (variable `line`, `account` carried) (BUILT, B8)
Differences: no "today" marker on the cumulative chart (the forecast starts at the data-through day instead). VA share is a share of conversations, not of the contract measure.

Stats: used (with last 30), billed, % billed, % term (n of N days), forecast to horizon (as % of billed, rate/day). Panels: cumulative used vs billed (actual solid, forecast dashed to horizon, billed as dashed red flat line, today marker); monthly measure vs engaged flag (paired bars); "How this line is measured" (measure, rule label, plain counting rule, funnel conversations → engaged → billable, reconciliation note, source); VAs on this line with share; % of billed night by night (from `line_totals_history`; show a note until 7 nights exist).

## 4. VA · `n360-va` (variable `va`) (BUILT, B9)
Differences: the expected range is drawn as a shaded band (expected ± 4 spreads) rather than one whisker per day.

Stats: status (Normal / Went silent / …), conversations 30d, typical weekday (expected), containment 30d, flags 30d (settled vs provisional). Panels: daily conversations against expected range (actual line; expected as a point with a whisker of ±4 spreads on scored days; flag markers red settled / orange provisional); flags table; reader and configuration (consumption source, line, region, handoff data read or not; Phase 2 config fields as "Not reported yet").

## 5. Contracts · `n360-contracts` (BUILT; B10 done)
Data through, lines above billed, contract lines table, pools, daily use per line, containment per VA weekly, with pace colours, data links, header and Back link. Fixed: lines above billed now counts the pool; not measurable lines are not plotted; "Contract unit" says "Sheet terms" where no SOW overrides; thousands separators, dates as "4 Dec 2026", clean series names.

## 6. Forecast and runway · `n360-forecast` (BUILT, B11)
Differences: the runway's contract-end tick is a change of shade (dark after the term end). Forecast accuracy is a note until `forecast_history` exists (B15).

Stats: crossing in 30 / 60 / 90 days, already above billed, step changes. Panels: runway timeline (one lane per line, bar from today to runs-out date, tick at contract end; colours: red crosses ≤30 days, blue crosses before term end, grey runs out after term end); runway table (line, used, billed, rate, runs out, term ends, note; flag when the term end has already passed); step changes table (last-30 vs term %, "rising"/"falling"/"stopped"); forecast accuracy (needs `forecast_history`; show a note until 30 nights).

## 7. Anomalies · `n360-anomalies` (BUILT, B12)
Differences: the heatmap is a state timeline (one lane per flagged VA); not-scored days are blank. Review column is "Not reported yet" until the anomaly log (B19). Cross-account events are split by direction (spikes vs drops/silences).

Controls: kind (drops and silences / spikes), include provisional (No default / Yes). Stats: drops, silences, spikes (7 settled days), provisional count. Panels: heatmap VA × day (30 days, VAs flagged in the window; colour by z: red ≤ −4 or silent, dark red −4..−2, neutral, dark blue 2..4, blue ≥ 4, near-black not scored; provisional columns faded); anomaly feed (day, customer, VA, actual, expected, z, kind, status, review) with VA drill; same-day events across customers (grouped); settled flags per week (stacked: drops+silences, spikes).

## 8. Containment · `n360-containment` (BUILT, B13)
Per VA, last 4 weeks vs previous 4 (table with change); weekly containment per VA (series); VAs without handoff data listed as "Not reported yet". Never one number across channels.

## 9. Adoption and coverage · `n360-adoption` (Phase 2; BUILT on a proposed design, B22/B23)
Tables `va_config_snapshot`, `playbook_day` (added: executions need a per-playbook table), `cortex_day` (read as authoring sessions), `explorer_day` (analytics-assistant questions): meanings are open questions in `08`.

Engine version per VA (green = V8 or later); V8 playbooks vs legacy intents; playbook executions 31d with built-but-idle marked; MCP servers live, supervisory rules on; authoring sessions and analytics-assistant questions (customer users only); configuration changes since last week.

## 10. Requests · `n360-requests` (Phase 2; BUILT on a proposed design, B22/B23)
"Several customers" = 2 or more (proposed, in `v_top_asks`).

Tickets by product area; top asks (several customers wanting the same thing); tickets by customer; "Not tagged yet" list (tagged by hand, never automatically).

## 11. Pipeline health · `n360-pipeline` (BUILT, B14)
Differences: "ran, not loaded" (orange) cannot show: a run that errors before logging (loader exit 2) writes nothing to `runs`, so it shows as "no run". Hover shows the status, not the failed checks (they are in "Why nights stopped").

Stats: last night result, data through, nights loaded of last 14, checks passed on last load, stale sources. Panels: runs, last 14 nights (one block per night: loaded green, stopped red, ran-not-loaded orange, no run grey; hover shows reason); why nights stopped (table); source freshness (last day, days behind: amber 2, red 3); contract measure mix (lines per measure; rules not live).
