# 08 · Decisions

## Decided (don't reopen without Ryan)

| Decision | Date |
|---|---|
| Storage ClickHouse, front end Grafana OSS, dashboards as code in Git (Elastic considered and rejected) | 22 Nov 2026 |
| Grafana 13.3.1 OSS (latest stable, released 10 Nov 2026); ClickHouse 25.8 | 22 Nov 2026 |
| Time-series layout: entity × day rows, dims first in sort key, monthly partitions | 22 Nov 2026 |
| NULL = not read, 0 = read and zero | 22 Nov 2026 |
| Per-night history of line totals; daily facts latest only | 22 Nov 2026 |
| Full nightly reload into staging, 8 checks, atomic swap; failed load keeps last night | 22 Nov 2026 |
| Contract measure decided upstream by the pipeline; views only divide | 22 Nov 2026 |
| Line key = sheet customer + channel + start (account + channel + start is not unique) | 22 Nov 2026 |
| Data one day old is acceptable; nightly runs when the VPN connects, once a day | 22 Nov 2026 |
| Local proof of concept on Ryan's Mac, localhost only, before any cloud move | 22 Nov 2026 |
| 11 dashboards with drill-downs; Portfolio → Customer → Contract line → VA | 22 Nov 2026 |
| Straight-line last-30-day forecast stays the forecast of record until a challenger proves better | 22 Nov 2026 |
| Pace colour is decided in a view (`v_line_pace`), not in the dashboard builder; dashboards only map its label to a colour | 24 Nov 2026 |
| Phase 2 tables as built: `cortex_day` = authoring sessions, `explorer_day` = analytics-assistant questions, `playbook_day` added for executions | 24 Nov 2026 |
| Top asks: "several customers" = 2 or more | 24 Nov 2026 |
| Anomaly heatmap watch band z ±2; source freshness amber at 2 days behind, red at 3 | 24 Nov 2026 |
| Pooled-entitlement panel shows for every customer ("No pooled entitlement") | 24 Nov 2026 |
| Alerts not needed yet: all rules provisioned paused, configurable in `build_alerts.py` (`docs/11_ALERTS.md`) | 24 Nov 2026 |
| Anomaly thresholds (v1 and v2) are Ryan's to configure, in `n360.settings` | 24 Nov 2026 |
| Production: self-hosted on AWS (one EC2 server, Docker, open source: ClickHouse, Grafana OSS, Caddy); Grafana sign-in with Google | 25 Nov 2026 |
| Loads run from Ryan's laptop through a Session Manager tunnel for now; server-side loads later (`server_loads`) | 25 Nov 2026 |

## Proposed (build as settings; Ryan approves)

| Item | Proposal |
|---|---|
| Pace colours (values in `n360.settings`) | Green ≤ term + 10 pts; amber > term + 10 or crossing ≤ 60 d; red > 100% or crossing ≤ 30 d; grey not measurable / not implemented / not read |
| Anomaly thresholds | Same weekday 8 weeks; z ≤ −4 / ≥ 4; min median 20; provisional 3 days |
| Alert routing and timing | As in `05_ANOMALY_AND_FORECAST.md`; alerts paused until needed (`11_ALERTS.md`) |
| Challenger adoption bar | Better 30-day MAPE on most lines for 8 straight weeks, plus Ryan's approval |

## Open (company-side questions; don't answer them, design around them)

| Question | Effect on your work |
|---|---|
| Which AWS account and region (customer data residency) | AWS self-hosting decided; region still to approve (`12_AWS.md`, "Before you start") |
| Whether EU / SG data may sit in a US-region store | May need region as a partition or a second database later |
| Who sees which customers in Grafana | Plan folder-level permissions; don't hard-code |
| Alert channel and recipients | Placeholders in contact points |
| Some contract rules are pending or blocked | Rule table already carries status; dashboards show "not live" |
| Billable unit for lines with no contract rule is unconfirmed | Label engaged as the measure, never as "billable" |
| When the existing page retires | Not your concern |
| Whether real N360 data may be sent to Claude (chat via `mcp-grafana`; query results leave the machine) | Chat built and tested on synthetic only (`09_CHAT.md`) |
| Who may chat with the data, and whether per-customer restriction is needed | Grafana OSS cannot limit rows per person; would need ClickHouse row policies + one data source per group |
| Does the pipeline count usage after a line's end date? (synthetic does: Atlas 282.6% all days vs 268.4% within term) | Views follow `reported_measured` as given |
| Pace for an entitlement whose term has ended (synthetic: Lakeside ended 31 Oct at 13.9% of billed shows green "on pace") | `v_line_pace` reports `term_ended`; the colour follows the proposed rule, which says nothing about ended terms |
| Cross-account events: should spikes and drops on the same day be one event or two? | Built as two (by direction); one line to change |
| Challenger model settings (Holt-Winters alpha 0.2, beta 0.01, gamma 0.2, 365-day fit) | First guesses; it does not beat the straight line overall on synthetic data (`10_CHALLENGER_BACKTEST.md`) |
| Who may record anomaly reviews (loader user via `code/review_anomaly.py`; Grafana OSS cannot write) | Fine for one owner; a team needs its own way in |
| Term-end convention: synthetic ends a 12-month line on the last day of the month before its start month (29 Jan start → 31 Dec end; pool ends 28 Jan) | Check against the contract sheet before trusting % of term |
