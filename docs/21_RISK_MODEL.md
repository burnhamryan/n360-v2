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
date only (`code/risk/features.py`). The model can therefore be backtested honestly on N360's own history.

| Dimension (weight) | Features |
|---|---|
| Commercial and contract (25%) | Points behind the term (worst line); forecast at term end (lowest line); renewal forecast category (Salesforce, current only) |
| Usage (20%) | Conversations, 30 and 90 days against the period before; week-to-week volatility; silent VA-days |
| Product quality (15%) | Containment (busiest VA); containment change; drop days |
| Adoption and depth (15%) | Share of conversations on VAs below V8; target features used; customer authoring and assistant trend; weeks since any configuration changed |
| Relationship (15%) | Days since the last customer contact; days since the last executive meeting (Salesforce) |
| Support (5%) | Open tickets; oldest open ticket (current only) |
| CSM view (5%) | The CSM's rating; open churn risks in the register |

**Context**, kept but not scored: days to renewal, renewal amount, number of billed lines, contract data gaps.

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
- Points lost per feature add up to 100 − health.

**Hard reds** (Gainsight's "exceptions"). Any one of these caps health at 40 and sets the band to "at risk", whatever
the weights say:
- renewal lost or omitted in Salesforce;
- the CSM rated the customer red;
- an open churn risk in the register;
- a VA silent for 5 or more expected days;
- under half of billed by the term end, within 120 days of renewal.

**Bands.** Healthy is 75 or more, watch is 50 to 74, at risk is under 50 or any hard red (GitLab's published bands).

**Probability.** A logistic regression on the 0–1 feature risks.
- **Before any outcomes,** it uses an **expert prior** made from the scorecard weights:
  - intercept = logit(5%);
  - coefficients = each feature's overall weight × (logit(75%) − logit(5%)).
  - So a customer at no risk anywhere scores 5%, and one at full risk everywhere scores 75%. The prior ranks drivers
    exactly as the scorecard does.
- **With outcomes,** training is Newton/IRLS with a ridge penalty that pulls each coefficient **toward the prior, not
  toward zero** (λ = 4).
  - With few outcomes the prior dominates; as outcomes accumulate, the data takes over.
  - A feature the data says nothing about is shrunk.
  - It trains only once there are at least 20 churns or downsells.
- Missing features take the training mean, so they push neither way.
- Each feature's "push" is its coefficient × (its risk − the training mean): its effect on the log-odds against an
  average customer.

**Labels.**
- 1 if the customer churned or downsold within 180 days after the snapshot date. Snapshots whose 180 days aren't over
  yet are left unlabelled, never counted as "no".
- Training uses one snapshot per customer every 28 days, because weekly snapshots of one customer are not independent
  evidence.
- Outcomes come from `n360.v_risk_outcomes`:
  - closed Salesforce renewals (won = renewed, lost = churned);
  - closed register entries (churned, downsold, saved);
  - outcomes recorded by hand.

**Backtest** (`code/risk/backtest.py`).
- Walk forward: every 28 days, train only on outcomes known by then, and score the next period.
- Four methods are compared:
  - the scorecard;
  - the expert prior;
  - the trained model;
  - the rules alone (hard reds or the at-risk band).
- Measures:
  - **AUC** (ranking; 0.5 is chance);
  - precision and recall for the **top 10%** flagged;
  - **Brier** score (calibration of the probability);
  - a **reliability** table (predicted against observed);
  - **lead time** (how far ahead churns were flagged).

## 4. How it is tested

`code/risk/test_risk_model.py` runs in CI (25 checks), with no database:
- **Unit checks:**
  - anchors in both directions;
  - points add up to 100 − health;
  - missing features re-weight and lower coverage;
  - hard reds and their windows;
  - nothing reported gives no health, never 100;
  - the prior hits its base and maximum rates and ranks drivers like the scorecard;
  - too few outcomes returns the prior;
  - labels are never assigned before the horizon is observed;
  - training is deterministic.
- **A synthetic history with a known truth** (`sample_data/generate_risk_panel.py`):
  - 220 invented customers over 130 weeks;
  - a hidden health that drifts and sometimes jumps (a champion leaves, an outage);
  - features are noisy readings of it, one carries no signal at all, and Phase 2 features are missing for a third of
    customers;
  - churn at renewal depends on the hidden health and on lost contact.
- **What the walk-forward checks on that history:**
  - scorecard, prior and trained model rank at AUC ≥ 0.72 (about 0.77 in practice);
  - the trained model is better calibrated than the prior;
  - the top 10% flagged churn at more than twice the base rate;
  - most churns were flagged a month or more ahead;
  - the no-signal feature is shrunk below its prior weight;
  - the strong true signals keep positive weights;
  - current-only features are never trained on.
- **On N360's own synthetic data** (`code/test_views.py`):
  - one score per customer;
  - Willow Energy at risk through its hard reds;
  - points add up per customer;
  - a 30-week score history;
  - outcomes read from Salesforce and the register.

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
- `train` trains on stored features and outcomes, runs the backtest, and stores the model.
- `score --as-of all` scores every stored date, which gives the score history.
- `explain` prints one customer, feature by feature.

**Every night:**
- `nightly.sh` runs `risk_model.py nightly` after a load: today's features, then today's scores. Set
  `N360_RISK_MODEL=0` to turn it off.
- **Training is never automatic.** Retrain on purpose, read the backtest, and only then use the new model.

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
| `risk_features` | The feature store, one value per customer, feature and date |
| `risk_outcome` | Outcomes recorded by hand |
| `risk_model_runs` | Every model, with its backtest as JSON |
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
   to get a backtest. Compare it with the last run before using the new settings.
2. **Adopt a trained model only when** its backtest beats the expert prior on AUC and Brier, on real outcomes, over at
   least two retrainings. The same bar style as the forecast challenger (docs/08).
3. **Record outcomes.** The model can only learn from churns, downsells and renewals it knows about. Closing register
   entries with an outcome, and keeping Salesforce renewals closed won or lost, is what makes it better.
4. **Back-test before trusting.** On real data, the first useful backtest needs about 180 days of stored features plus
   the outcomes after them. Until then the score is the expert prior: explainable, but unproven.

## 8. Known limits and next steps

- **Few customers, few outcomes.** With a few dozen enterprise customers, years may pass before there are 20 churns.
  The prior and the scorecard carry the model until then. Downsells count as events, which helps.
- **Contract terms are taken as they are today** when rebuilding past features. A renegotiated term changes history
  slightly.
- **Not in the model yet, because the data doesn't exist yet** (docs/17, section 3):
  - abandoned conversations, escalation reasons, CSAT, tool and integration failures (Datadog, Langfuse: docs/20);
  - meeting sentiment;
  - champion change;
  - contract value beyond the Salesforce renewal amount.
- **Possible upgrades once outcomes accumulate:**
  - a survival (time-to-churn) model;
  - per-segment weights;
  - interaction with days to renewal.
- **Settings not yet decided:**
  - the 5% base rate and 75% maximum of the prior;
  - the 180-day horizon;
  - the hard-red list.
