# 03 · Metric taxonomy

**Every metric has one name, one definition, one formula.** A new metric is added here first and
approved by Ryan, then built. Panel titles use the plain name; the formula goes in the panel
description.

## Consumption and contract

| Plain name | Definition | Formula | Grain | Source | Caveats |
|---|---|---|---|---|---|
| Conversations | Every conversation a user starts | `sum(conversations)` | VA × day | `va_day` | Sandbox traffic excluded upstream |
| Engaged conversations | Conversations the platform flags as engaged | `sum(engaged)` | VA × day | `va_day` | Default measure where no rule applies; the billable unit for those lines is unconfirmed |
| Used | The contract's measure from line start to data-through | `reported_measured` | Line | `contract_line` | Billable, all conversations, or engaged, by rule. Never summed across measures |
| Billed | The contract's volume for the line | `billed` | Line | `contract_line` | After SOW overrides; NULL for pooled lines |
| % of billed | Used / billed | `100 × used / billed`, 2 dp | Line or pool | `v_line_status`, `v_pool_status` | Must equal the pipeline's figure (checked) |
| % of term | Days elapsed / term days | `100 × least(days since start + 1, term) / term` | Line | `v_line_status` | |
| Pace gap | % of billed − % of term | | Line | view (to add) | Positive = ahead of contract |
| Billable (contract rule) | Contacts meeting the contract's meaningful-engagement test | `sum(billable)` | Line × day | `line_day_billable` | |
| Pooled used | Used across every line in a pool | `sum(reported_measured)` over the pool's lines | Pool | `v_pool_status` | |

## Forecast

| Plain name | Formula | Grain |
|---|---|---|
| Rate per day | last 30 days of used / 30 | Line or pool |
| Forecast to horizon | used + rate × days to horizon; horizon = earlier of contract end and the month end 3 months out | Line or pool |
| Runs out on | today + (billed − used) / rate; none beyond 100 years | Line or pool |
| Crosses billed on | runs-out date when it falls before contract end | Line or pool |
| Forecast error (planned) | mean of `abs(actual − forecast) / actual` (MAPE) for forecasts made 30/60/90 days earlier | Line |

## Containment

| Plain name | Formula | Grain | Caveats |
|---|---|---|---|
| Handoffs | `sum(handoffs)` | VA × day | |
| Containment | `1 − sum(handoffs) / sum(engaged)` | VA × week | **Per VA only. Never combine channels.** VAs with no handoff data are not shown, never 0 |

## Anomaly

| Plain name | Formula |
|---|---|
| Expected | median of the same weekday over the previous 8 weeks |
| Distance from normal (z) | (actual − expected) / spread; spread = IQR / 1.349, floored at 10% of expected and at 5 |
| Went silent / Drop / Spike | 0 conversations / z ≤ −4 / z ≥ 4 (only VAs with expected ≥ 20) |
| Provisional | within 3 days of data-through |

## Pipeline health

Data through (`runs.window_end`), last load status, checks failed, source freshness (last day per source).

## Dimensions

| Dimension | Values | Notes |
|---|---|---|
| Account | ~27 in reality | Stable key in data, display name on screen |
| Contract line | ~47 | |
| VA | ~70 | Shown as "Name (Channel)" |
| Channel | Chat, Email, SMS, Voice, InApp, AMB, RCS, Agent Assist, Apple Messaging for Business… | Open list |
| Region | US, EU, SG | Closed list |
| Measure | engaged · billable (contract rule) · conversations (contract rule) · not measurable · not implemented | Pipeline sets it |
| Rule status | active · pending_probe · blocked | |
| Consumption source | aggregate · conversation_records · contract_rule | |

## Words on screen

| Situation | Show | Never show |
|---|---|---|
| Not read | Not reported yet | 0, a red cell, "n/a" |
| Not measurable under the contract | Not measurable | 0% |
| Contracted, nothing built | Not implemented | Unmapped |
| Recent, may still change | Provisional | An alert |
| Table names, query numbers, internal IDs | Only in the panel description | In a title or label |
