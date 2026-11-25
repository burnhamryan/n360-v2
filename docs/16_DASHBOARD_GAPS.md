# 16 · Dashboard review: gaps found, what changed, what Ryan decides

**Review of the 11 Grafana dashboards against the v1.1 page and the v2 redesign spec (`docs/15_DASHBOARD_REVIEW_HANDOFF.md`),
25 Nov 2026, on synthetic data.** The dashboards are rebuilt to the v2 page structure, with the v1.1 contract statuses
and colours. Nothing the v1.1 page shows is lost (table A below). Every status rule lives in a view, with its threshold
in `n360.settings`, marked PROPOSED. The open choices are in section D.

## 1. What was wrong (review findings)

| # | Finding | Where | Why it mattered |
|---|---|---|---|
| 1 | **Colour meant four things.** Red was used for pace, settled anomalies, stopped loads, the billed line and "any above zero". Green was used for "on pace", "used" and "loaded". Customers in the stacked bars got red, orange and yellow by Grafana's default palette | Every dashboard | A reader can't tell a status from a series. v2 principle 6 is broken |
| 2 | **The page's 11 contract statuses were reduced to red/amber/green.** "Too early", "under review", "billed not recorded", "unmapped" and renewal risk had no state | Overview, Contracts, Customer | Leadership and CSMs already read the v1.1 statuses. A grey line had no reason given |
| 3 | **A line that had used 14% of billed with its term ended showed green "on pace"** (Lakeside) | Overview "Customers needing attention" | The pace rule only looked ahead of term, never behind. Renewal risk was invisible |
| 4 | **Headline numbers had no reference** (no "since when", no "of what") | Every stat | v2 principle 3 |
| 5 | **No page answered its question in a sentence.** The Data through and Last night stats took the first row | Every dashboard | v2 principle 1: the first thing on screen should be the answer |
| 6 | **"Needs a decision this month" had no home.** The watch list was the "worst line by pace gap", one per customer | Overview, Contracts | v1.1 view missing. One customer with three problems showed one |
| 7 | **No Customer health page, and no CSM filter** | (none) | v2 page missing |
| 8 | Internal keys on screen: `Orchid Health\|0`, `Orchid Health\|Chat\|15-Mar-2026`, `pending_probe`, ISO dates | Anomalies, Pipeline | v2 principle 9 |
| 9 | A pool's name repeated the customer: "Bluebird Air · Bluebird Air: 3 lines, …" | Overview, Forecast | Noise |
| 10 | The same number came from different places (overview counts from `v_line_pace`, tiles from `runs`) with nothing checking they agree | Overview, Contracts | v2 gate "one number, one value" |
| 11 | Unmapped text cells were painted Grafana's default green (e.g. a ticket's area), and nulls in some stats too | Requests, VA | Green read as "fine" |
| 12 | Partial weeks were drawn as full bars (the newest week looked like a drop) | Overview, Customer | v2 grammar: partial periods faded and labelled |

## 2. What changed

**Every page now reads:** the question (its title), then a sentence that answers it with numbers, then the freshness
stamp ("Usage to 19 Nov 2026 · loaded 25 Nov", amber when more than 2 days old). On the v2 pages the next rows are four
tiles, each with its reference ("+2 since 21 Oct", "of 13 customers") and a 30-night line, then one lead chart, then
one action list, then tables.

**One palette** (`COLOUR` in `build_dashboards.py`). Colour means a status and nothing else. Chart series that are not
statuses (customers, lines, sources) are shades of one neutral blue-grey. Reference marks (billed, 100%) are dark grey
and dashed.

| Status (`n360.v_contract_status`) | Colour | Rule (PROPOSED, `n360.settings`) |
|---|---|---|
| Over billed | peach | used above billed |
| Will exceed | lavender | the stored forecast crosses billed before the term ends |
| Renewal risk | strong blue (no red) | under 50% used with 120 days or fewer left (`renewal_risk_pct`, `renewal_risk_days`) |
| Below pace | light blue | % of billed more than 10 points behind % of term (`status_pace_pts`) |
| Ahead / On pace | green | more than 10 points ahead / within 10 points |
| Too early to tell | light grey | fewer than 30 days into the term (`status_too_early_days`) |
| Under review | grey | the line's contract rule is not live yet (pending or blocked) |
| Billed not recorded | grey | billed blank on the sheet, not in a pool |
| Unmapped | grey | no VA mapped to the line |
| Not built | grey | contracted, nothing implemented |
| Not measured | grey | a contract unit the platform does not count |
| Not reported yet | grey | no use read |

Other states use the same palette. Anomalies: down warm, up cool, provisional faded. Operations: amber means "look at
it" (stale data, a stopped night). Adoption: V8 or later green, older amber (the v1.1 page's colours).

**New views** (`code/schema.sql`):
- `v_contract_status`: the statuses above, renewal risk, the forecast at term end, plain-word issue and date, needs
  attention, decision due.
- `v_status_night`: the same statuses each stored night, for tiles' "since" and the status-over-time chart.
- `v_va_change`: each VA, last 30 days against the 30 before.
- `v_needs_attention`: the v2 rule.
- `v_freshness`: the stamp.
- `v_feature_use`: target features per customer.
- `v_customer_notes`: notes pinned to a customer's header.

**New settings, all PROPOSED:** 14 values in `n360.settings`.

**Pages** (v2 → Grafana; uids unchanged, so every link and bookmark still works):

| v2 page | Grafana | What it is now |
|---|---|---|
| Overview | `n360-overview` (renamed "N360 · Overview") | Sentence → 4 tiles → contract lines by status (100% bar; a segment opens Contracts filtered) beside status over 30 nights → Needs attention (8 rows, soonest first, "View all" → Customer health) → all conversations (day / week / month) |
| Contracts | `n360-contracts` + `n360-line` as the drawer | Sentence → 4 tiles → bullet chart (now, plus the rest of the term at the current rate, 100% line, capped at 250% with the true % printed) → Decisions due → "12 of 19 lines · filtered" → Every line (status chip, % bar, term-end quarter, sortable). Status and Customer filters, in the URL |
| Customer health | **new** `n360-health` | CSM filter → sentence → 4 tiles → one row per customer (most urgent status, lines by status, conversations and change, biggest change in words, target features, open tickets) → 26 weeks of conversations → every VA, flagged first |
| One customer | `n360-customer` | Sentence → 4 tiles (status of the most urgent line, VAs on V8+, target features, open tickets) → pinned notes + needs attention → contract lines (pool lines under the pool) → % of billed over the term + conversations (day / week / month, VA filter, containment drawn only when the chosen VAs share one channel) → VAs (engine, playbooks vs intents, conversations, containment, flags) → features, playbooks → anomalies, requests |
| Adoption | `n360-adoption` | Sentence → 4 tiles → engine by customer (100%) + customers per feature → coverage table → upgrade queue (busiest first) + containment by channel (▲▼) → idle playbooks, configuration changes → authoring and assistant use |
| Requests | `n360-requests` | Sentence → 4 tiles → asks ranked by customers + tickets by area → opened per week (26 weeks) → every ticket (filterable) |
| (Grafana additions, kept) | Forecast, Anomalies, Containment, Pipeline health, VA, Contract line | Sentence and stamp on top; the one palette; plain-word labels |

## A. Nothing lost: every v1.1 view has a home

| v1.1 view | Home now |
|---|---|
| Overview tiles + status strip | Overview: tiles, contract lines by status (all 11 statuses; greys grouped as "No reading yet", each one in the table) |
| Watch list | Overview: Needs attention (v2 rule) |
| Contract position (bullet) | Contracts: bullet chart |
| Pace against 100% | Contracts: same bullet chart (merged, as recommended) |
| Needs a decision this month | Contracts: Decisions due |
| Consumption rollup | Contracts: Every line (% bar per row, term-end quarter). **Monthly sparkline per row: not done**, see section C |
| Engine by customer | Adoption: Engine by customer (needs the configuration export) |
| Coverage and adoption table | Adoption: Coverage and adoption by customer |
| Adoption (features) | Adoption: Customers using each target feature, plus the coverage table. Testing, Calibration and Recipes are not exported yet: shown as a note, not as zeros |
| Containment, "not verified" | Containment, Customer, Adoption: per VA, never across channels; "Not reported yet" where handoffs are not read |
| Enhancements & product support | Requests (needs the ticket export) |
| All conversations | Overview: all conversations, day / week / month, partial periods faded |
| About, tour | **Dropped**: every panel's (i) carries its definition, source, filter and window; the docs are the guide |
| Ask Claude | Stays off with real data until approved (`docs/09_CHAT.md`) |
| Date range picker | Grafana time picker (usage views only; statuses always use the full term) |
| Summary / context strip | Customer: sentence (status, VAs, next term end, CSM) and tiles |
| Coverage & Depth matrix | Customer: VAs table (engine, V8 playbooks vs legacy intents per VA) |
| Consumption (cumulative, conversations, VA filter, containment overlay) | Customer: % of billed over the term; conversations with VA filter and containment line |
| Adoption per customer | Customer: Target features, Playbooks |
| Authored, not live | Customer: Playbooks ("Built, not run"); Adoption: Idle playbooks |
| Account & Contract | Customer: Contract lines; Contract line: How this line is measured |
| Customer notes | Customer: Notes (written from the contract data) |
| Enhancements per customer | Customer: Requests |

## B. v2 gates

| Gate | How it is checked | Result |
|---|---|---|
| Nothing lost | Table A | Every row has a home or a reason |
| One number, one value | `test_panels.py`: panels with the same `n360Metric` must show the same value; `test_views.py`: the newest stored night equals today's statuses | Pass |
| Plain words | `test_build.py`: no table names, keys or underscores in titles; keys mapped to labels in tables | Pass (Pipeline health keeps source ids on purpose: it is the technical page) |
| Empty is not zero | `test_views.py`: unmapped, not built and not measured lines have no use; unread containment stays NULL. Grey with a reason on screen | Pass |
| Layout | `test_build.py`: sentence and stamp in row 1; v2 pages have 4 tiles in row 2 and the lead chart at row 3 (on screen at 1440 × 900); no gap or overlap anywhere | Pass |
| Description and table view | `test_build.py`: every panel's (i) has definition, Source, Filter, Window; every panel has Inspect → Data (CSV) | Pass |
| One palette, no rules in dashboards | `test_build.py`: every colour from `COLOUR`; no thresholds except reference lines | Pass |
| Human test (10 seconds per reader) | Ryan, on real data | **Not done**: needs readers |
| WCAG 2.1 AA | Palette chosen to read on both themes; Grafana picks text contrast on coloured cells | **Not audited**: no aria labels in Grafana OSS panels |

## C. What Grafana OSS can't do (and what was done instead)

| v2 asks for | Done instead |
|---|---|
| A term tick and a forecast ring inside a bullet bar | Two segments: now (status colour) + the rest of the term at the current rate (pale), so the bar ends at the forecast. The label prints "283% · term 100%" |
| A monthly sparkline per row in a table | Not done. Grafana can join a time series into a table only with a fragile two-query transform. Each line's monthly volume is in its drawer (Contract line), and the customer page charts it |
| Left-aligned sentence | Grafana's stat panel centres text; the sentence is a stat (no plugin needed) |
| "12 of 43 lines, filtered to Below pace · clear" inside the table | A one-line "Showing" panel above the table, with a Clear filters link |
| P1 tickets, tickets closed per week | Not in the export: all open tickets are counted, opened per week only; each panel says so |
| Change since 30 days ago for "customers using 3+ features" | Feature use has no stored history yet: the tile says "history not stored yet" |
| Aria labels stating each chart's finding | Not available in Grafana OSS panels; the sentence on top states the finding |

## D. Decisions for Ryan (proposed values are settings; nothing is chosen silently)

| # | Decision | What is built | Where to change |
|---|---|---|---|
| 1 | Adopt the v1.1 statuses and palette (handoff C1) | Built that way: the old red/amber/green pace is no longer on screen (`v_line_pace` still feeds the alerts) | `STATUS`, `COLOUR` in `build_dashboards.py`; rules in `v_contract_status` |
| 2 | Status thresholds: below/ahead ±10 points, too early < 30 days, renewal risk < 50% with ≤ 120 days | Proposed | `n360.settings` |
| 3 | Needs attention: over, will exceed, renewal risk, ended within 90 days, VA with 1,000+ conversations down 25%+ (handoff C4) | Proposed | `n360.settings` |
| 4 | Customer health flags: conversations down 10%+, containment down 3+ points | Proposed | `n360.settings` |
| 5 | Target features list (5 of the v1.1 page's 7: Testing, Calibration, Recipes are not exported) and "3+" | Proposed | `v_feature_use`, `target_features_min` |
| 6 | "Asks from several customers": v2 says 3+, built (and decided 24 Nov) as 2+ | 2+ kept; the tile says so | `asks_several_customers` |
| 7 | Forecast rate (handoff C2): last 30 days (record) vs last 7 settled | Unchanged: the pipeline's rule; the forecast at term end uses the stored rate | Pipeline |
| 8 | Bullet chart form (C7) | One form built (segments); screenshots in `docs/screenshots/` | `contracts()` |
| 9 | Customer health "biggest change": a containment point counted as 3% of conversations | Proposed | `health()` |

## E. Not yet tested

- **Real data.** The dashboards are tested on synthetic data that now plants every status. Two things to watch on
  the 32 real nights. First, `unmapped` relies on `line_va`, so a real line with no VA row will show as unmapped.
  Second, the tiles' history needs `forecast_history`, which builds up night by night.
- **The human 10-second test** and an accessibility audit.
