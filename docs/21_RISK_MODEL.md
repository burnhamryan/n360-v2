# 21 · The N360 customer risk model

**A customer risk model you can test, tune and run.** It runs on its own (files in, files out) or inside N360, where
Grafana shows it (`n360-risk-model`, linked from Risk). It is built on the research in docs/17. Every setting is in
`code/risk/risk_model.json`; the code holds no thresholds. **Tested on synthetic data only; every weight, anchor and
threshold is PROPOSED.**

## 1. What it answers

> **Will this customer churn or downsell at its next renewal?**

It answers in three layers. Each is explainable on its own, and they are meant to be read together.

| Layer | Output | Use it for |
|---|---|---|
| **Feature risks** | Each of 20 features scored 0 (fine) to 1 (bad), from two anchors | Seeing exactly what is wrong |
| **Scorecard** | **Health** 0–100, a **band** (healthy / watch / at risk), **coverage**, points lost per feature, **hard reds** | The weekly conversation: a stable, explainable number |
| **Probability** | **Chance of churn or downsell at the next renewal**, its push per feature, and **revenue at risk** (chance × the Salesforce renewal amount) | Prioritising by money, and measuring the model against outcomes |

Why renewal: contracted churn and downsell happen at a renewal. Customers whose renewal falls within the horizon (180
days) are the ones the probability is judged on. Customers renewing later are still scored, but marked "later".

## 2. The features (point-in-time)

There are 20 risk features in seven dimensions. Each one is computed as it stood on a given date, from data up to that
date only (`code/risk/features.py`). Rows the nightly run stores are exactly what was known that night; rows rebuilt
later for past dates are close to it, with the limits listed in section 8.

| Dimension (weight) | Features |
|---|---|
| Commercial and contract (25%) | Points behind the term (worst line); forecast at term end (lowest line); renewal forecast category (Salesforce, current only) |
| Usage (20%) | Conversations, the last 4 and 12 weeks against the same span before; week-to-week volatility; days the worst VA was silent (the 30 settled days) |
| Product quality (15%) | Containment (busiest VA); containment change; days the worst VA was flagged as a drop (the 30 settled days) |
| Adoption and depth (15%) | Share of conversations on VAs below V8; target features used; Cortex and Explorer trend; weeks since any configuration changed |
| Relationship (15%) | Days since the last customer contact; days since the last executive meeting (Salesforce) |
| Support (5%) | Open tickets; oldest open ticket (current only) |
| CSM view (5%) | The CSM's rating; open churn risks in the register |

**Context**, kept but not scored: days to renewal, renewal amount, number of billed lines, contract data gaps.

**Risk is per account, and an account has one renewal date:** the end date of its largest running contract line (by
billed volume; a pool counts with its pool's volume). A small add-on line that ends earlier is not the account's
renewal. A customer with no dated line takes the close date of its open Salesforce renewal. Lower-level detail still
feeds the score, collapsed to one reading per account: the worst line for pace and forecast, the worst VA for silent
and drop days, the busiest VA for containment.

**Silent and drop days** are counted over the 30 days that end where the settled days end (3 days before the date),
so a row stored that night and the same date rebuilt later count the same 30 days.

**"Open churn risks" is not reported before the risk register was first used.** An empty register is not an
all-clear.

**"Not reported" is never "fine".** When a source has nothing for a customer, the feature is left out. Its weight moves
to the rest of its dimension, and **coverage** drops. A customer with low coverage has less evidence behind the score.

**Current-only features** (`pit: false`) have no history in N360 to rebuild from: the Salesforce forecast category and
open tickets. They count in today's scorecard, but the probability model never trains on them, so a backtest cannot
leak the future.

## 3. The maths

**Feature risk.** risk = clamp((value − good) / (bad − good), 0, 1). This works in either direction, for example
containment where 60 is good and 30 is bad.

**Health.**
- Each dimension's risk is the weighted mean of its reported features.
- Overall risk is the weighted mean of the dimensions that have any data.
- health = 100 × (1 − risk).
- Points lost per feature add up to 100 − health, always. When a hard red caps health, the points it takes show on
  their own line ("Hard red").

**Hard reds** (Gainsight's "exceptions"). Any one of these caps health at 40, sets the band to "at risk", and puts a
floor under the chance shown, whatever the weights say:

| Hard red | Chance never shown below |
|---|---|
| Renewal lost or omitted in Salesforce | 90% |
| The CSM rated the customer red | 50% |
| An open churn risk in the register | 50% |
| Under half of billed by the term end, within 120 days of renewal | 40% |
| One VA silent for 5 or more expected days | 30% |

A hard red that only applies near a renewal is listed as "could not be checked" when the renewal date is unknown.

**Bands.** Healthy is 75 or more, watch is 50 to 74, at risk is under 50 or any hard red (GitLab's published bands).
Two more: **not enough data** when under 40% of the model's weight has data and there is no hard red, and
**churned** for a customer known to have churned, kept for 12 months and then dropped from the scores.

**Probability.** A logistic model on the same re-weighted risks as the scorecard. "Not reported" is never "fine"
here either: a missing feature takes its dimension's risk, a missing dimension takes the overall risk, and a customer
with nothing reported gets no probability at all.

- **The expert prior** (before any outcomes) says three things, each of which can be checked:
  - the average customer in a renewal window has the portfolio's loss rate (`prior_mean_rate`, 15% proposed:
    **replace it with renewals lost ÷ renewals due from the real record**);
  - a customer at full risk on everything has `prior_max_rate` (80% proposed);
  - features move the log-odds in proportion to their scorecard weights, so the prior ranks customers exactly as
    the scorecard does.
  - **Its level is fixed.** The same readings give the same chance on any day, whoever else is renewing. Until a
    run is adopted, the level is the one the settings give. `train` sets it once, on every stored snapshot of
    customers in their renewal window (at least 200), so that their average chance is the portfolio rate; the level
    then stays with that run until you train and adopt again.
  - It was once re-set every night on the customers then in their renewal window. With 5 to 16 customers there, one
    customer with unchanged readings moved between 12% and 44% as others entered and left the window.
- **Learning comes in stages,** counted in distinct renewals lost (and at least as many kept), never in snapshots:

  | Stage | From | What the data may change |
  |---|---|---|
  | prior | 0 | Nothing |
  | calibrated | 10 lost | The level and one slope. The ranking stays the scorecard's; the chance becomes the data's |
  | dimension | 50 lost | One weight per dimension |
  | feature | 100 lost | One weight per feature |

  - Each learned weight is pulled toward the stage before it. The pull is relative (λ = 4): a weight's prior spread
    is half its prior value, so the data has to argue to halve or double it.
  - The prior's level counts as 20 renewals of evidence, so the first few outcomes move it gently.
  - No weight goes below zero: a risk never lowers the chance of churn. One that tries is held at zero.
  - Features that keep no history keep their prior weight, rescaled by the learned slope.
- Each feature's "push" is its effect on the log-odds against the average customer. The pushes add up exactly,
  for the prior and for a trained model.

**Labels: the unit is the renewal of an account.**
- A snapshot belongs to the customer's next renewal when that is 0 to 180 days away. An unknown renewal date is not
  judged. Renewal dates within 30 days of each other (an edited end date) are one renewal.
- The renewal's outcome is the loss or renewal recorded from the day the customer entered the renewal window (180
  days before the date) to 60 days after it. A loss is logged at the opt-out, which can be months before the term
  ends: an early notice is this renewal's loss.
- **1** = churned or downsold.
- **0** = renewed, and something says so: a renewal on record, or a contract still running after the date.
- **No label: unconfirmed.** Nothing recorded and no contract seen running afterwards. These renewals are counted
  and reported by `train` and the backtest, and never assumed to be kept. With no outcome source connected, every
  renewal would otherwise silently count as kept.
- Never used: snapshots taken once the outcome was recorded (the answer was known), and every snapshot after a churn.
- Every label, lost or kept, becomes usable 60 days after its renewal date. A loss is logged weeks before a kept
  renewal is; learning from each as soon as it is recorded would overstate the loss rate.
- The same loss recorded twice (Salesforce and the register) is one outcome.
- Outcomes come from `n360.v_risk_outcomes`:
  - closed Salesforce renewals: lost = churned; **won at under 90% of the customer's previous won renewal =
    downsold** (`settings.risk_downsell_ratio`, proposed); else renewed. The bar is the previous renewal, not any won
    deal: a small add-on before it would make a cut renewal look like growth, and a multi-year first deal would make
    a normal annual renewal look like a cut. A first renewal has no bar and is never a downsell here;
  - register entries closed as churned or downsold, dated by the day the outcome happened (`risk_register.py close
    --date`, else the day the entry was closed), never by a later edit. An entry closed as "saved" is not a renewal
    outcome;
  - outcomes recorded by hand.

**Backtest** (`code/risk/backtest.py`).
- Walk forward: every 28 days, train only on outcomes usable by then, and score the next period.
- Each renewal is judged 150, 90 and 30 days ahead, so it counts three times at most, not twenty.
- Five methods are compared: the scorecard, the scorecard without the CSM's own view, the expert prior, the model as
  it would have been trained at the time, and the rules alone (reported as a flag: share flagged, precision, recall).
- **Without the CSM's own view:** a CSM often rates a customer red, or opens a churn risk, after the customer has
  said it is leaving. In a backtest that reads as foresight. The scorecard is therefore also judged with the rating
  and the register blanked; if it ranks much better with them, part of that is hindsight.
- The prior in the backtest is the one `train` would have stored at each date: its level set on every in-window
  snapshot up to then.
- Measures:
  - **AUC** with a 95% interval from resampling whole customers, and the interval of trained minus prior;
  - precision and recall for the **top 10%**;
  - **Brier** score, and losses expected against losses seen;
  - a **reliability** table for the model and for the prior;
  - **lead time:** share of losses warned, median days ahead among those warned, whether the warning was still up
    30 days before the loss, and how often the flag was up on renewals that were not lost.

## 4. How it is tested

`code/risk/test_risk_model.py` runs in CI (63 checks), with no database:
- **Unit checks:** anchors; points add up with and without a hard red; missing features re-weight; "not enough
  data"; the prior's rates; "not reported" never reads as "fine"; hard-red floors; the explanation adds up; one
  outcome per renewal; nothing used after an outcome or a churn; events are renewals, not snapshots; the stages;
  weights never below zero; a clean separation still converges; ties in AUC and in a top share; the same readings
  give the same chance whoever else is renewing; an early opt-out is a loss; an unconfirmed renewal is not used;
  renewal dates a few days apart are one renewal.
- **A synthetic history with a known truth** (`sample_data/generate_risk_panel.py`): 220 invented customers over 130
  weeks, with the awkward parts of real records planted on purpose:
  - a churn is logged 30 to 90 days before the term ends (one in five: 120 to 170 days before), and the customer
    goes on producing rows for half a year;
  - a third of churns are recorded twice; one good renewal in twelve is never recorded;
  - two features carry no signal; Phase 2 features are missing for a third of customers and Salesforce activity
    for one in seven.
- **What the walk-forward checks on that history:**
  - the scorecard ranks within 0.04 AUC of the hidden truth itself (0.791 against 0.790);
  - the trained model is never clearly worse than the prior, at 220 customers and at 40;
  - losses expected are within 20% of losses seen;
  - a prior set at a badly wrong rate is corrected by learning;
  - most losses are flagged beforehand, a month or more ahead.
- **On N360's own synthetic data** (`code/test_views.py`): one score per customer; Willow Energy at risk through its
  hard reds; points add up for every customer; a score history; outcomes from Salesforce and the register; the
  model in use is the adopted run; the feature store marks nightly and rebuilt dates; a downsell is judged against
  the previous renewal; a register churn carries the day it happened; one renewal date per account.

**What the synthetic test does not show: that learning sharpens the weights.** On this history the trained model
ranks about as the prior does (AUC 0.776 against 0.782), and with some 80 renewals lost a feature that carries no
signal is not reliably pulled down: across random seeds its weight lands anywhere from zero to twice its prior
value. The ridge keeps every weight near the prior, which is the point at this size. What learning does fix is the
level: a prior set at a badly wrong rate is corrected.

**What the synthetic test cannot show.** The generator still drives every feature from one hidden number, so it says
the plumbing is right and the model does no harm. It does not say the weights are right for real customers. Only
real renewals can.

## 5. Running it

With N360 (as the loader user):

```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" features --from 2026-01-01 --every 7
```
```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" train --report ~/n360-work/risk_backtest.md
```
```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" score --as-of all
```
```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" explain --customer "Willow Energy"
```

What each one does:
- `features --from … --every 7` backfills weekly features, point-in-time, into `n360.risk_features`. Run it once.
  It never replaces a date the nightly run stored (`--overwrite` to force it): the nightly row is the only true
  record of what was known that night.
- `train` trains on stored features and outcomes, runs the backtest, stores the run and says whether it has earned
  adoption. It also says how past renewals were labelled (lost, kept on record, kept because a contract ran on,
  unconfirmed) and what the prior's level was set on. **The run is not in use yet.**
- `adopt --run RUN_ID` puts a stored run into use. `adopt --run prior` goes back to the built-in expert prior.
- `score --as-of all` scores every stored date, which gives the score history.
- `explain` prints one customer, feature by feature.

**Every night:**
- `nightly.sh` runs `risk_model.py nightly` after a load: today's features, then today's scores. Set
  `N360_RISK_MODEL=0` to turn it off.
- **Training is never automatic, and a trained run is never used until adopted.** Retrain on purpose, read the
  backtest and the verdict `train` prints, then `adopt`.

**Record an outcome by hand,** when it is not in Salesforce or the register:

```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" outcome --customer "Lakeside Insurance" --date 2026-10-31 --outcome churned
```

**On its own** (no database; feature and outcome rows as NDJSON, in the same shape `features.py` produces):

```bash
python3 code/risk/risk_model.py --no-db backtest --features f.ndjson --outcomes o.ndjson --report report.md
```

The same `--no-db` flag works for `train`, `score` and `explain`; see the help text at the top of `risk_model.py`.

**Stored in N360** (all backed up with the history tables):

| Table or view | What it holds |
|---|---|
| `risk_features` | The feature store, one value per customer, feature and date; `source` says nightly or rebuilt |
| `risk_outcome` | Outcomes recorded by hand |
| `risk_model_runs` | Every model, with its backtest as JSON |
| `risk_model_adopted` | Which run is in use: the newest row |
| `risk_scores` | Every score |
| `risk_contributions` | The reasons behind each score: per feature value, risk, points and push |
| `v_risk_score`, `v_risk_drivers`, `v_risk_model`, `v_risk_outcomes` | What Grafana reads |

## 6. In Grafana: `n360-risk-model` ("N360 · Risk model")

- **Sentence and tiles:** at risk, revenue at risk, outcomes to learn from, and the backtest AUC.
- **Every customer:** band, health, chance at renewal, revenue at risk, biggest drivers, hard reds and coverage.
- **For a chosen customer:**
  - what costs it health;
  - every feature (value, risk, points, push);
  - points lost by dimension.
- **Health over time.**
- **The backtest:** each method's AUC, precision, recall and Brier score, and the calibration table.
- **Model weights against the expert prior.**

The Risk dashboard (docs/17) keeps its rule-based grid. The model is a second opinion that can be measured; once real
outcomes back it, it can replace or complement the rules.

## 7. Governance: how to change it

1. **Change a weight, anchor, hard red or band** in `risk_model.json`. Bump its `version`. Run the tests, then `train`
   to get a backtest. Compare it with the last run before adopting.
2. **Adopt a trained run only when it has earned it.** `train` prints the verdict:
   - stage "calibrated": its Brier score beats the prior's (the ranking cannot change at this stage);
   - stages "dimension" and "feature": the whole 95% interval of trained minus prior AUC is above zero, and its Brier
     score is lower.
3. **Record outcomes.** Keep Salesforce renewals closed won or lost with the right amount; close register entries
   with an outcome; record anything else by hand. A churned customer then shows as "churned" for 12 months.
4. **Set the prior's rate from the record** before trusting the percentages: renewals lost ÷ renewals due over the
   last two or three years goes in `prior_mean_rate`. Then `train` and `adopt`: that fixes the prior's level on your
   own customers, even while there are too few outcomes to learn anything else.
5. **Back-test before trusting.** On real data, the first useful backtest needs about 180 days of stored features plus
   the outcomes after them. Until then the score is the expert prior: explainable, but unproven.

## 8. Known limits and next steps

- **Few customers, few outcomes.** With a few dozen enterprise customers, the model will sit at "prior", then
  "calibrated", for years. That is the design: the scorecard carries the ranking, and outcomes first fix the level.
- **Rebuilt history is not perfect history.** Contract lines are read as they are today (a renewal adds a line and
  the old one stays, but an edited date rewrites the past); Salesforce activity logged late shows up in the past;
  the "current" engine version is today's. Nightly rows do not have these limits, so the history gets more
  trustworthy every night the model runs.
- **Downsell from Salesforce amounts is a proposal.** It compares a renewal with the customer's previous won renewal
  and assumes both are the same kind of number (both annual, both for the same scope). A first renewal is never a
  downsell here. Check it against a few real renewals.
- **A renewal counted as kept because "a contract ran on" may hide a downsell.** If the main line ended and only a
  small one continued, nothing here sees it. Record downsells in Salesforce or the register.
- **The largest line is chosen by billed volume, whatever its unit.** Two lines measured in different units are
  compared as plain numbers (proposed).
- **A loss announced more than 180 days before the term ends** is not matched to the renewal. The customer's later
  snapshots are dropped (it has churned), so nothing is mislabelled, but the loss is not learned from.
- **Revenue at risk treats a downsell as losing the whole renewal.**
- **Churned status lives in the risk model only.** Other dashboards do not yet filter churned customers out.
- **Volume change is not adjusted for season.** A decline after a peak still scores as risk.
- **Not in the model yet, because the data doesn't exist yet** (docs/17, section 3):
  - abandoned conversations, escalation reasons, CSAT, tool and integration failures (Datadog, Langfuse: docs/20);
  - meeting sentiment;
  - champion change;
  - contract value beyond the Salesforce renewal amount.
- **Settings not yet decided** (all proposed): the prior's 15% and 80%; the 10 / 50 / 100 stages; the hard-red list
  and floors; the 40% coverage floor; the 120 / 60 day outcome window.
