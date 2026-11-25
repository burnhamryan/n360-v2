# 15 · Handoff: review and improve the Grafana dashboards

**For Claude Code working in this repo.** Goal: bring the 11 Grafana dashboards up to the standard of
(a) the **N360 v1.1 page** people use today, so nothing they rely on is lost, and (b) the **N360 v2 redesign
spec**, which sets how the product should read. Written 24 Nov 2026, from the v1.1 page's code and the v2 spec.
No customer data in this file.

## Situation

- Three generations of the same product exist:
  1. **v1.1 page** (live, official): one HTML page built nightly by the company pipeline and published to
     Claude. It's what leadership and CSMs read today.
  2. **v2 redesign spec** (written 21 Nov, approved direction, not built): five dashboards plus one customer page, governed
     metric definitions, strict visual grammar. Written for the HTML page, before Grafana was chosen.
  3. **Grafana (this repo)**: 11 dashboards built on synthetic data, now loaded with 32 real nights on Ryan's Mac.
     Built from `docs/04_DASHBOARDS.md`, which predates the v2 spec reaching this repo.
- **Your job:** review each Grafana dashboard against sections A and B below, write a gap list, and then
  improve `code/grafana/build_dashboards.py` so Grafana delivers v2's intent with nothing from v1 lost.
  Grafana replaces the page only when it passes the gates in section E.

## Rules for this work

- **Develop on synthetic data** (`sample_data/generate_synthetic.py`). Only look at the real data loaded on this
  Mac if this Claude Code session runs under a company-approved account. Never paste real customer names or
  numbers into commits, docs or PR text.
- **Dashboards are code.** Change `build_dashboards.py`, regenerate, run `code/grafana/test_panels.py` and
  `test_build.py`. Never edit the JSON by hand or in the UI.
- **Keep dashboard uids, table names and column names.** Adding views and settings is fine.
- **Views divide; they never decide a measure.** Contract measures, forecasts and rules come from the pipeline.
- **Proposed thresholds stay settings** in `n360.settings`, marked proposed, until Ryan approves.

---

## A. What the v1.1 page shows today (must all have a home in Grafana)

The page has an all-customers block and one block per customer (27 customers, about 70 VAs, about 47 contract
lines), switched by a customer dropdown. Light and dark themes. Every view has a "Method and sources" drawer;
plain words on screen, internals only in the drawer.

### All-customers views

| v1.1 view / panel | What it shows | Grafana home today | Gap |
|---|---|---|---|
| **Overview** tiles + status strip | Counts of lines by contract status; status of each line as a coloured strip | Portfolio overview stats | v1 uses **11 contract statuses** (see C); Grafana uses a 4-colour pace rule |
| Watch list | Lines needing attention | Portfolio "Customers needing attention" | v2 rule for "needs attention" not implemented (see C) |
| **Contract position**: engaged % of billed per line | Bullet chart: each line against 100%, term elapsed marked, forecast | Portfolio "Use against billed" bar gauge | No forecast marker; bar gauge only approximates a bullet chart |
| "Pace against the 100% utilization line" | Same lines, pace view | Contracts | Merge into one bullet panel |
| **Needs a decision this month** | Lines ending soon, over billed, at renewal risk | none | **Missing**: v2 "Decisions due" table |
| **Consumption rollup** | Every contract line, used/billed/term, per customer | Contracts table | OK; add monthly sparkline + % bar per row (v2) |
| Engine by customer | Agentic OS engine per VA, rolled up by customer (V8+ green) | Adoption (Phase 2) | Needs `va_config_snapshot` exported (not yet) |
| Coverage and adoption table | Per customer: V8 share, idle playbooks, features used | Adoption (Phase 2) | Same |
| **Adoption** | Features used per customer (Cortex, Testing, Calibration, Recipes, Explorer, MCP servers, supervisory rules) | Adoption (Phase 2) | Same |
| **Containment** | Per VA, never across channels; "not verified" when a VA never hands off | Containment dashboard | Check the "not verified" state exists |
| **Enhancements & product support** | Jira PS tickets by area and ask, 90 days default | Requests (Phase 2) | Needs `ps_ticket` exported (not yet) |
| **All conversations** | Daily conversations, every VA | Portfolio weekly stacked bars | Add a day/week/month toggle; mark partial periods |
| About (guide, FAQ, change log), click-through tour | Explains the page | none | Grafana text panel or a linked doc; low priority |
| Ask Claude panel | Q&A on the page's data | `chat/` (off with real data) | Stays off until approved |
| Date range picker (consumption dates) | Filters usage views | Grafana time picker | OK |

### Per-customer views (v1.1 block per customer)

| v1.1 view | What it shows | Grafana home today | Gap |
|---|---|---|---|
| Summary / context strip | Customer, VAs with engine chips, CSM, contract end and days left | Customer header | Engine chips need config data; CSM and contract end available now |
| **Coverage & Depth** | Capability matrix: playbooks (V8, green) vs legacy intents (amber) per VA | VA dashboard (Phase 2 rows) | Missing until config exported |
| **Consumption** | Per line: cumulative used vs billed + forecast; conversations over time (day/week/month), VA filter, containment line overlaid | Customer + Contract line | Containment overlay and VA filter missing |
| **Adoption** | Features by VA, playbook executions, idle playbooks | Adoption (Phase 2) | |
| **Authored, not live** (live but idle) | V8 playbooks in production with no executions | none | Phase 2 |
| **Account & Contract** | Lines, terms, billed, rules, SOW notes | Customer contract table + Contract line "How measured" | OK |
| Containment | Per VA, over time | Customer containment panel | OK |
| Enhancements | That customer's tickets | Customer (Phase 2 row) | |
| Contract-rule notes, customer notes | e.g. a pooled line, a rule exception in force | Contract line "How measured" | v2 wants customer notes pinned in the header |

---

## B. The v2 redesign spec, condensed (the target)

### Ten principles (every screen)

1. **One question per page**; the title says it, the first sentence answers it with a number.
2. **Summary before detail**: sentence → at most 4 numbers → one lead chart → one action list → tables. Nothing below repeats what is above.
3. **Every headline number has a reference**: change since a named date, a target, or a share of a whole.
4. **Define once, use everywhere** (metric dictionary). Two panels showing the same metric show the same value.
5. **Chart only where shape carries meaning**; fewer than 5 values = table or sentence.
6. **Colour means status, nowhere else.** One status palette, one selection accent.
7. **Empty is never zero.** Grey, with the reason: not reported yet / not measured / under review.
8. **No cross-contract arithmetic.** A pool is one line.
9. **Plain words on the page**, internals in the description/drawer.
10. **Every view drills down** to the same customer page, scrolled to the item.

### Information architecture

Five dashboards plus one customer page:

| v2 page | Question | Reader |
|---|---|---|
| **Overview** | Where does the book stand, and which customers need attention? | Leadership, < 1 min |
| **Contracts** | What renews when, and which lines are over or under? | Account management, weekly |
| **Adoption** | What do customers use, and what do we move next? | Customer architects, product |
| **Customer health** | How is each customer doing, and what changed in 30 days? | Customer success, by CSM |
| **Requests** | What are customers asking us to build or fix? | Product, CS |
| **One customer** | Everything about one account, in meeting order | Anyone |

Shell: freshness stamp on every page ("Usage to 23 Nov 2026 · built 24 Nov", amber when > 2 days old); customer
picker; CSM filter; date range applies only to usage views (contract status always uses the full term).

### Page specs (row by row)

**Overview**
1. Computed sentence: customers needing attention, lines ending in 30 days, lines at renewal risk.
2. Four tiles, each with change since 30 days ago and a 30-night line: lines over or set to exceed billed · lines below pace · VAs on V8+ · customers using 3+ target features.
3. Contract lines by status, 100% stacked bar (8 cols, click filters Contracts) beside status over the last 30 nights (4 cols).
4. Needs attention: at most 8 rows, customer · issue in ≤ 6 words · date · CSM, soonest first; "View all" opens Customer health.

**Contracts**
1. Sentence: lines ending in 90 days, over billed, set to exceed, at renewal risk. 2. Four tiles, same facts, change vs 30 days.
3. **Bullet chart**: % of billed per line, now and at the forecast, term elapsed marked, 100% line, axis capped at 250% with the true value printed. Sorted by status then %.
4. Decisions due (merged watch list + "needs a decision this month"): customer, line, issue, term end, days left, % used, forecast at end.
5. Every line, searchable, sortable, filterable from row 3 or Overview; monthly sparkline + % bar per row; optional group by term-end quarter.
Drawer per line: cumulative used vs billed with forecast to term end, monthly volume. (Grafana: the Contract line dashboard.)

**Adoption**
1. Sentence: VAs on V8+, customers on 3+ target features, busiest VA still to upgrade, least-used target feature.
2. Tiles: VAs on V8+ · customers using 3+ target features · legacy intents on VAs below V8 · VAs containing under 50%.
3. Engine by customer (100% stacked, sorted by V8 share) beside feature adoption by feature.
4. Coverage and adoption heat table by customer.
5. Upgrade queue (ranked by 30-day volume) beside containment by channel (dot plot per VA within one channel; change as arrow).
6. Idle playbooks table.

**Customer health**
1. CSM filter and sentence. 2. Tiles: lines below pace · VAs with conversations down 10%+ · VAs with containment down 3+ points · open tickets (P1).
3. One row per customer: a small bar per contract line with term tick · conversations sparkline 26 weeks · biggest change in a sentence · target features used · open tickets; expands to VAs.

**Requests**
1. Sentence + tiles: tickets in 90 days · open · customers raising them · asks from 3+ customers.
2. Asks ranked by number of customers (horizontal bars, open and P1 marked). 3. Tickets by area beside opened vs closed per week (26 weeks). 4. Every ticket, filterable.

**One customer**
Header (context strip, sentence, four tiles: contract status, VAs on V8+, target features, open tickets; pinned customer notes) → Needs attention (only if any) → Contracts (per line cumulative + table; conversations over time with day/week/month, VA filter, containment line) → Coverage (playbooks vs intents matrix) → Adoption (features by VA, executions, idle) → Containment (per VA, last 30 vs previous 30) → Requests.

### Visual grammar

| Question shape | Chart | Never |
|---|---|---|
| Each item against its own target | Bullet bar with target tick and forecast ring | Gauges, donuts |
| Level over time | Line, straight segments | Area-only, smoothing |
| Counts per day/week/month | Bars with gaps; partial periods faded and labelled | Lines through partial periods |
| Parts of one whole | 100% stacked bar | Pies with > 3 slices |
| Ranking | Horizontal bars, sorted, labelled at the end | Vertical bars with rotated labels |
| Distribution within a group | Dot plot | Box plots |
| Items × attributes | Table with in-cell bars, chips, sparklines | A chart per attribute |
| 1–4 numbers | Tile or sentence | A chart |

**Numbers:** tables in full (2,971,463); tiles and chart labels compact from 10,000 (2.97M); percentages with no
decimals, one decimal under 10%; dates "9 Nov 2026", "9 Nov" inside charts. Nothing under 11 px.

**States:** not reported yet (grey, tooltip names the missing source) · read, nothing found ("None recorded") ·
not measured (grey chip naming the unit) · source failed tonight (keep last good data, freshness amber) · not
verified (containment, never handed off: grey, never 100%).

**Interaction:** tile or status segment → Contracts filtered; line → its drawer; customer name → customer page;
VA → customer page at that VA. Every state in the URL so Back works. Tables sort, search, filter by status chip,
say when filtered ("12 of 43 lines, filtered to Below pace · clear"), and export CSV with data date and filters.
Every chart has a "show as table" view (Grafana: Inspect → Data).

**Accessibility:** WCAG 2.1 AA in both themes; keyboard path; aria label stating each chart's finding.

---

## C. Conflicts to resolve (flag to Ryan; don't pick silently)

| # | Topic | v2 spec / v1 page | Grafana today | Recommendation |
|---|---|---|---|---|
| 1 | **Status palette** | 11 contract statuses (over billed, will exceed, ahead, on pace, below pace, too early, under review, not measured, billed not recorded, not built, unmapped); colours peach over, lavender will exceed, green on pace/ahead, light blue below pace, grey not readable. **No red** (a renewal-risk line is light blue with a dark outline) | Proposed red/amber/green pace rule | Adopt the v1/v2 statuses and palette: readers already know them. Compute status in a view from `n360.settings` thresholds; needs Ryan's OK |
| 2 | **Forecast rate** | Open decision: last 7 settled days (recommended, skipping the newest 3) vs last 30 | Pipeline forecast = last 30 days | Leave it to the pipeline; show which rule is in force in the panel description. Don't recompute in Grafana |
| 3 | **Page structure** | 5 dashboards + customer page | 11 dashboards (Portfolio, Customer, Contract line, VA, Contracts, Forecast, Anomalies, Containment, Adoption, Requests, Pipeline health) | Map, don't merge blindly (table D). Anomalies, Forecast and Pipeline health are additions v2 lacks; keep them. Add **Customer health**, which Grafana lacks |
| 4 | **Needs attention rule** | A line over billed, set to exceed, ended, or under 50% used with ≤ 120 days left; or a VA with 1,000+ conversations down 25%+ (proposed) | "Worst line by pace gap" | Implement v2's rule as a view with settings, marked proposed |
| 5 | **Headline numbers** | Every tile shows change since 30 days ago + a 30-night line | Bare counts | Use `line_totals_history` / `runs` for the 30-days-ago value; sparkline from the same |
| 6 | **Computed sentence** | First thing on every page | None | A Stat panel can show a string: `SELECT concat(...) AS sentence`, text mode "value", colour none. No plugin needed |
| 7 | **Bullet chart** | Line vs 100%, term tick, forecast ring, cap at 250% | Bar gauge | Grafana has no native bullet chart. Options: bar gauge plus a separate term column; Bar chart with threshold line; or the built-in Canvas/XY panel. Prototype two, show Ryan screenshots |

## D. Proposed mapping (v2 page → Grafana dashboard)

| v2 page | Grafana dashboard(s) | Work |
|---|---|---|
| Overview | `n360-overview` | Rebuild to v2 rows 1–4; statuses per C1; tiles with change |
| Contracts | `n360-contracts` + `n360-line` (as the drawer) | Bullet panel, Decisions due table, sparkline + % bar per row |
| Adoption | `n360-adoption` + `n360-containment` | Phase 2 data not exported yet: build on synthetic, show "Not reported yet" on real data |
| Customer health | **new** `n360-health` | New dashboard; CSM variable; one row per customer |
| Requests | `n360-requests` | Phase 2 data not exported yet |
| One customer | `n360-customer` (+ `n360-va`) | Reorder to v2 sections; pinned notes; containment overlay on conversations |
| (Grafana additions) | `n360-forecast`, `n360-anomalies`, `n360-pipeline` | Keep; apply the visual grammar and states |

## E. Acceptance (from the v2 spec, adapted)

**Machine gates (CI):**
- Nothing lost: every row in section A has a Grafana home, or a written reason it was dropped.
- One number, one value: the same metric on two dashboards returns the same value (`test_views.py` / `test_panels.py`).
- Plain words: no table names, query numbers or ids in panel titles or labels.
- Empty is not zero: no 0 or red where the source was not read (synthetic edge cases cover this).
- Layout: rows 1–3 visible without scrolling at 1440 × 900; no orphan panel in a row.
- Every panel has a description (source · filter · window · definition) and a "show as table" path.

**Human test:** each reader answers their question in 10 seconds without help.

| Dashboard | Reader | Question |
|---|---|---|
| Overview | Leadership | Which three customers need attention first, and why? |
| Contracts | Account management | Which lines go over billed before term end, and when? |
| Adoption | Customer architect | Which VA should be upgraded next, and why that one? |
| Customer health | Customer success | In my book, which customer changed most in 30 days? |
| Requests | Product | What's the most requested ask, and how many customers want it? |
| One customer | Anyone | Is this contract on track, and what is the customer running? |

## F. Suggested order of work

1. Read `build_dashboards.py`, the 11 JSON files and `test_panels.py`; screenshot each dashboard on synthetic data.
2. Write `docs/16_DASHBOARD_GAPS.md`: per dashboard, gaps against A and B, and the C conflicts needing Ryan.
3. Shared building blocks first: freshness stamp, sentence panel, tile-with-change, status palette view, number
   formats, states, drill links with URL state.
4. Overview → Contracts (+ line drawer) → One customer → Customer health (new) → Adoption, Requests (synthetic) →
   apply the grammar to Forecast, Anomalies, Pipeline health.
5. After each dashboard: regenerate, tests green, screenshots, one line in `CHANGES.md`.

## G. What Ryan has decided, for context

- Grafana 13.3.1 OSS, dashboards as code, ClickHouse 25.8. Real data loads nightly on his Mac (32 nights, 12 Oct to 23 Nov).
- Light and dark both stay (v2 decision 4 recommendation; Grafana has both).
- Designs for 7 Grafana dashboards exist on a Claude design canvas (real data, not in this repo). Ryan will share
  screenshots if you need them. Their layout follows `docs/04_DASHBOARDS.md`; v2 supersedes it where they differ.
- Open: forecast rate, V8 green rule, contract value source, metric owners (v2 decisions 1, 2, 3, 5).
