# 17 · Risk dashboard: market research and proposed design

**For a risk dashboard used by CSMs and customer architects.** This document covers:
- what the market's risk tooling does;
- which signals matter for an AI-agent business on usage-based contracts;
- which of those N360 already has, can derive, or would need as new data;
- a proposed design to build in Grafana.

Researched 25 Nov 2026. Nothing is built yet: section 6 lists the decisions to make first.

**How it was researched.** Two research passes used web search across vendor help centres, vendor docs and
practitioner sources. The session's network policy blocked full page fetches. So findings come from search
results quoting those pages, and the cited URLs are the pages quoted. Thresholds marked *published* are attributed
to their source. **Every other threshold here is a proposal to calibrate on our own data, not a market standard.**

---

## 1. What the market does (in one page)

| Tool | Score model | What a reader sees | How risk becomes action |
|---|---|---|---|
| **Gainsight** | Scorecard → groups → measures, weighted; empty measures' weight redistributed. **Exceptions** force the overall colour ("hard red", e.g. a churn notice). CSM-scored and rule-scored measures mixed ([docs](https://support.gainsight.com/gainsight_nxt/05Scorecards/02Admin_Guides/Exceptions_in_Scorecards)) | Score with ▲▼ against last week; 18 weeks of weekly history per measure. Renewal Center: forecast call per renewal, a waterfall from renewals due to churn, downsell and upsell, and churn reasons with $ impact | **CTA** (call to action) of type Risk, with: a reason, priority, owner, due date, a playbook of tasks, and a close outcome (Success / No Action / Invalid). Rules open and close CTAs ([docs](https://support.gainsight.com/Gainsight_NXT/04Cockpit_and_Playbooks/03Admin_Guides/Configure_CTA_Types_Reasons_Priority_Snooze)) |
| **Staircase AI** (Gainsight) | Sentiment on every message, rolled up per account. A "relationship" = 3+ exchanges in 90 days. **Churn risk when fewer than 2 of our people talk to 2 of theirs** ([docs](https://support.gainsight.com/gainsight_nxt/Staircase_AI/User_Guide/Explore_Staircase_AI_in_Gainsight_CS)) | "Revenue at risk" = accounts with red sentiment; "Champion left", "Usage down 32%" alerts | Alerts into Gainsight |
| **Totango / Catalyst** | **Green needs every good criterion; one poor criterion makes it red**; amber otherwise. Profiles per segment ([docs](https://support.totango.com/hc/en-us/articles/360030899812-Configure-profiles-for-account-health)). A parent account turns red if red children hold a set share of contract value | Red/amber/green that explains itself: the failing criterion is the reason | SuccessPlays on triggers such as "sharp health decline" or "90 days to renewal" |
| **ChurnZero** | Factors worth points that sum to 100, each with its own time window; composite scores ([docs](https://support.churnzero.com/hc/en-us/articles/360003535071-Build-Your-ChurnScores)) | Command Center: today's tasks, alerts, renewals calendar | Plays and Journeys from segments; Slack and email alerts |
| **Vitally** | Sub-scores 0–10, weighted; Healthy ≥ 6.7, Poor < 3.4; hourly ([docs](https://docs.vitally.io/en/articles/9901284-health-scores)). "Trend matters more than level" | **Risk indicators**: a flag next to the account name, set by playbooks | Playbook creates a project when health drops |
| **Planhat** | Usage + recency of contact to start; weights learned from churn history (marketing claim) | Churn analytics by reason, tier and year | **Churn records** with multi-pick reasons and ARR churned ([docs](https://help.planhat.com/en/articles/9587088-logging-and-tracking-reasons-for-churn)) |
| **HubSpot CS** | Points → At-risk / Neutral / Healthy | % of accounts per status; **"most recent score changes"** on the record ([docs](https://knowledge.hubspot.com/customer-success/access-the-customer-success-health-score-card-on-records)) | Workspace tasks |
| **ClientSuccess** | **Pulse** = the CSM's own rating, which decays over time unless refreshed ([docs](https://help.clientsuccess.com/en/articles/12833866-mastering-the-pulse-score)) | SuccessScore per client | — |
| **GitLab on Gainsight** (public handbook) | Green 75–100 / Yellow 50–74 / Red 0–49. License use weighs more as renewal nears. "Will churn" is kept outside the colour scale ([handbook](https://handbook.gitlab.com/handbook/customer-success/customer-health-scoring/)) | Red accounts updated weekly, green ones monthly | Renewal CTA at about 4 months before renewal. A non-engagement playbook after more than 60 days with no call or meeting |

**The patterns worth copying:**
1. **Explain, don't just score.** Every leading tool shows *which* measure turned an account red (Gainsight's measure grid,
   HubSpot's "recent score changes", Totango's failing criterion). An unexplained number is the most common complaint.
2. **Hard reds.** Some conditions override any weighting (Gainsight Exceptions, Totango's "one poor criterion makes it red").
3. **Trend beside level.** Show ▲▼ against last week, plus a history.
   - This needs **weekly snapshots**.
   - Snapshots are also the only way to check a score later. Back-test against how accounts looked about 90 days
     before each churn ([guidance](https://hackernoon.com/your-customer-health-score-is-probably-astrology-heres-how-to-test-it)).
4. **Renewal proximity amplifies, it doesn't count alone.** Typical windows: a call at T-6 months, commit at T-3 months,
   plays at 90–120 days.
5. **Risk is a register, not just a colour.** Each risk has:
   - a short reason-code list (6–12; Gainsight warns against a pre-filled default reason);
   - an impact type (churn / downsell / contraction);
   - an owner, due date, status, and an outcome when it closes.
6. **Keep commit and consumption apart**, and keep churn risk and expansion apart (Planhat; Forbes on usage-based pricing).
7. **The CSM's judgement is a signal.** A "pulse" rating that expires if not refreshed (ClientSuccess, GitLab).

**Common reason codes**, compiled from Gainsight, Planhat, GitLab and practitioner guides:
- Adoption / value not realised
- Product / functionality gap
- Bugs / performance
- Support experience
- Champion or sponsor change
- Budget / price
- Competitor
- Company change (M&A, layoffs)
- Implementation / onboarding

**Impact type is recorded separately:** churn / downsell / volume contraction.

## 2. What is specific to an AI-agent vendor

- **"Containment" is the weak metric; the market moved to judged resolution.**
  - Ada counts a resolution only if it is relevant, accurate, safe *and* contained.
  - Intercom Fin reports resolution and drops "constrained" conversations (where rules forbade an answer) from the
    denominator.
  - Zendesk splits "Contained" from "Verified" resolutions, and **only Verified ones count toward billing**.
  - Sources: [Ada](https://docs.ada.cx/docs/generative/measure-success/understand-and-improve-your-ai-agent-s-automated-resolution-rate),
    [Intercom](https://www.intercom.com/help/en/articles/15599377-update-to-fin-performance-metrics),
    [Zendesk](https://support.zendesk.com/hc/en-us/articles/10677925692698-Announcing-changes-to-AI-agent-reporting).
  - **This matters to N360.** Our containment, 1 − handoffs / engaged, counts an *abandoned* conversation as contained.
- **Abandoned / dropped conversations are a separate outcome.**
  - Copilot Studio counts sessions that time out after 60 minutes with no resolution.
  - LivePerson calls a conversation "bot-contained" only with no transfer, no bot failure and no customer drop-off.
  - Sources: [Copilot Studio](https://learn.microsoft.com/power-virtual-agents/analytics-summary),
    [LivePerson](https://developers.liveperson.com/bot-analytics-bot-performance.html).
- **Escalations caused by configuration versus by failure.** Intercom and Decagon separate escalations the
  configuration demanded from escalations where the bot failed. Heavy escalation rules cap automation, and so they
  cap consumption.
- **Fallback / no-match rates and content gaps** are the classic "the bot doesn't know" signal.
  - Lex marks a conversation failed after a fallback.
  - Intercom and Zendesk group unresolved topics with no content behind them.
  - Sources: [Lex](https://docs.aws.amazon.com/lexv2/latest/dg/performance-dashboard.html),
    [Intercom](https://intercom.help/fin4all/en/articles/10672146-analyze-unresolved-questions).
- **Informational-only bots plateau.** Fin puts the ceiling at about 40–50% resolution; going higher needs
  actions or integrations ([fin.ai](https://fin.ai/learn/ai-resolution-rate)). For N360, that makes a VA with no
  MCP servers or actions a capped VA.
- **Old engines lose capabilities and visibility.**
  - Lex V1 support ended in September 2025, and migration did not carry everything over.
  - Kore.ai's unhandled-utterance analytics need version 9.3 or later.
  - For N360, that makes legacy-engine VAs both a risk and a blind spot.
- **Reopen / repeat contact** within 24–72 hours is called the best check of real resolution
  ([fin.ai](https://fin.ai/learn/ai-resolution-rate)).
- **Consumption risk.**
  - The standard construct is pacing: consumed % against term-elapsed %, with forecast at term end.
  - **No published standard threshold for "commit at risk" was found.** Burning faster than forecast means expansion;
    stopping means churn ([Flexprice](https://flexprice.io/questions/tools-track-customer-health-consumption-billing)).
  - Where volume doesn't roll over, customers burn it late on low-value use, which masks the true run-rate
    ([Kinde](https://www.kinde.com/learn/billing/usage-based/committed-consumption-annual-commit-drawdown/)).
- **Market context.** Gartner forecasts more than 40% of agentic-AI projects cancelled by the end of 2027, mainly for
  unclear business value ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027)).
  So showing value (cost per resolution, resolution rate against the agreed target) is itself risk work.

## 3. The signal catalogue, mapped to N360

**Lens:** CSM = commercial and relationship; CA = customer architect (technical and quality).

**Have (core):** in the nightly export today; nothing more needed.

| # | Signal | Lens | Measure | Proposed risk rule (setting) |
|---|---|---|---|---|
| 1 | Under-consumption / renewal risk | CSM | % of billed vs % of term; forecast at term end (`v_contract_status`) | Renewal risk (< 50% used, ≤ 120 days left); below pace (> 10 points behind) |
| 2 | Over-consumption | CSM | Over billed / will exceed (`v_contract_status`) | Over billed, or crosses billed before the term ends. **Expansion, not churn**: keep it apart |
| 3 | Days to renewal | CSM | Term end (`contract_line.end_date`) | Amplifier: windows T-180 / T-120 / T-30 |
| 4 | Volume trend | both | Conversations, 30 days against the 30 before (`v_va_change`) | Down 25%+ on 1,000+ conversations |
| 5 | VA silent or dropping | CA | Settled drops and silences (`v_va_day_anomaly`) | Any settled silence; 2+ drops in 7 days |
| 6 | Containment trend | CA | 1 − handoffs / engaged, per VA (`v_va_change`, `v_containment_week`) | Down 3+ points; weekly 10+ below its 8-week median |
| 7 | Forecast instability | CSM | Crossing date moved 14+ days in a night (`v_forecast_change`) | As now (anomaly v2) |
| 8 | Measure gap | CSM | Contract measure ÷ engaged drifts 20+ points (`v_measure_gap_week`) | As now: a billing-definition risk |
| 9 | Concentration | CA | Share of the customer's conversations from its biggest VA or channel (derived from `va_day`) | One VA ≥ 80% (proposed; no published threshold) |
| 10 | Contract hygiene | CSM | Billed not recorded, unmapped, rule pending, not built (`v_contract_status`) | Any. A data risk to fix before renewal |

**Have, once the Phase 2 export ships:** the tables and views are built and tested on synthetic data only.

| # | Signal | Lens | Measure | Proposed risk rule |
|---|---|---|---|---|
| 11 | Legacy engine | CA | VA older than V8 (`v_va_config_latest`) | Any busy VA below V8, ranked by volume |
| 12 | Capped automation | CA | No MCP servers live (no actions or integrations) | Any VA with 0 MCP servers live and containment < 50% |
| 13 | Stale configuration | CA | Weeks since the last configuration change (derived from `va_config_snapshot`) | No change in 8+ weeks (proposed; no published threshold) |
| 14 | Idle build | CA | Playbooks built but not run in 31 days | Any |
| 15 | Customer builder disengaged | both | Authoring sessions and analytics-assistant questions by customer users, 30 days vs before | Down 50%, or none in 30 days |
| 16 | Open support load | both | Open tickets, oldest open ticket age (`ps_ticket`) | Oldest open > 30 days; 3+ open |
| 17 | Feature breadth | CSM | Target features used (`v_feature_use`) | Under 3 of 5 |

**Need (new data), in rough order of value against effort:**

| # | Signal | Lens | What's needed | Likely source |
|---|---|---|---|---|
| 18 | **Risk register** (reason, impact, owner, due, status, outcome) | CSM | A new append-only table, written by a small CLI like `review_anomaly.py`; no export change | CSMs (in N360) |
| 19 | **CSM pulse** (green/amber/red + note, expires after 30 days) | CSM | Same mechanism as 18 | CSMs |
| 20 | **Weekly risk snapshots** (for trend arrows and back-testing) | both | A new history table, filled by `load.py` like `forecast_history`; no export change | N360 itself |
| 21 | **Contract value** (ARR or $ per line) for "revenue at risk" | CSM | One column on `contract_line` (open decision 3 in docs/08) | Entitlement sheet / billing |
| 22 | **Last meaningful contact**, executive touch | CSM | Last meeting / last EBR (executive business review) date per account | Rocketlane, calendar, Gong |
| 23 | **Abandoned / dropped conversations** per VA-day | CA | Count of sessions ending with neither resolution nor handoff | Platform conversation records |
| 24 | **Escalation reason** (configured vs failure) and fallback / no-match counts | CA | Counts per VA-day | Platform logs |
| 25 | **Ticket priority and closed date** | both | Two columns on `ps_ticket` | Jira |
| 26 | Bot CSAT, repeat contact within 72 hours | CA | Per VA-day | Platform (if collected) |
| 27 | MCP / tool call success rate and latency | CA | Per VA-day | Platform logs |
| 28 | Champion / sponsor named and last seen; customer admin logins (a denominator for 15) | CSM | Per account | CRM / Rocketlane; platform auth |

## 4. Proposed risk model (recommendation)

**Explainable rules first, no weighted 0–100 score yet.** A weighted score needs history and outcomes to set and
check its weights, and N360 has neither yet. So:

- **Each signal is per customer (or per VA), with a level and a reason in words**, e.g. "Harbor Chat: containment
  down 4 points". Levels are risk / watch / fine; *not reported* is grey and never counts as fine.
- **Customer risk = the worst signal (Totango's rule), with hard reds** (Gainsight's exceptions):
  - renewal risk inside 120 days;
  - a VA silent on settled days;
  - an open "will churn" register entry;
  - a CSM pulse of red.
- **Two lenses, two sub-levels:** commercial and relationship (CSM) and technical (CA). One customer can be fine
  commercially and at risk technically. That is the case where the CA should act before the CSM sees it in the
  numbers.
- **Renewal proximity sorts, it doesn't colour.** Sort order: risk level, then days to renewal.
- **Weekly snapshots from day one**, so trend arrows work and, after 6–12 months of real outcomes, a weighted score
  can be fitted and checked against the rules.
- Expansion (over billed, will exceed) is shown, but on its own scale, not as churn risk.

## 5. Proposed dashboard: `n360-risk` "Risk"

**Question: which customers are at risk, why, and who is acting?**

1. **Sentence + stamp.** For example: "6 customers at risk · 3 renewing within 120 days · 4 new risks this week · 2 with
   no owner".
2. **Tiles (each with "since last week"):**
   - customers at risk;
   - at-risk renewals within 120 days (count, and billed volume, or contract value once signal 21 exists);
   - new risks this week;
   - open register items with no owner or past due.
3. **Lead: risk matrix.** One row per customer, columns = signals grouped by lens, each cell a coloured chip with the
   reason on hover. This is Gainsight's scorecard grid, in a Grafana table. Rows are sorted by risk, then days to
   renewal, with a trend arrow against last week's snapshot.
4. **Action list: risk register.** Open items with reason, impact, owner, due, age, status. Overdue items come first.
5. **Renewal runway.** Customers by days to renewal (T-180 / T-120 / T-30 lanes), coloured by risk.
6. **Architect view.** One row per VA:
   - engine;
   - containment level and trend;
   - volume trend;
   - silent / drop flags;
   - MCP servers;
   - weeks since the last configuration change;
   - idle playbooks.
   Sorted by volume, so the busiest problem comes first.
7. **Risk over time.** Customers at risk per week by lens (from the snapshots).
8. **Closed risks.** Outcomes and reasons over 90 days: what worked, and the reasons behind churn.

A **CSM filter** (as on Customer health) and a **lens filter** (CSM / CA / both). Every customer and VA drills to its
page.

## 6. Decisions before building

1. **Model:** explainable rules with hard reds now; a weighted score later, once back-tested (recommended). Or a
   weighted score now.
2. **Register and pulse in N360?** A CLI writes them (like anomaly reviews). The alternative is to read them from
   wherever CSMs already log risk (Rocketlane, CRM) once a feed exists. Grafana OSS can't write data from a panel.
3. **Reason codes:** a short list to agree. Proposed:
   - Adoption / value
   - Product gap
   - Quality / bugs
   - Support
   - Champion or sponsor change
   - Budget / price
   - Competitor
   - Company change
   - Implementation

   **Impact:** churn / downsell / contraction.
4. **Which new data to ask for first** (section 3). Recommended order:
   - 20 and 18–19: no export change;
   - 21 (contract value);
   - 23–24 (abandoned and escalation reason from the platform);
   - 25 (ticket priority and closed date);
   - 22 (last contact).
5. **Thresholds** in sections 3 and 4 are proposals and would go in `n360.settings` like the others.
6. **Real data:** risk views would be built and tested on synthetic data (planted cases for every signal), as before.
   They would then be checked on the real nights.
