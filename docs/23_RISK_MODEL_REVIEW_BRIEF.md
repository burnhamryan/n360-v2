# 23 · Review brief: the N360 customer risk model

**For an independent reviewer.** The goal is to find what is wrong, weak or unproven in the risk model before it is
used on real customers. Every result so far is on synthetic data.

## What it is

The model answers: **will this customer churn or downsell at its next renewal?**
- **Customers:** B2B, usage-based contracts for AI virtual agents. There are a few dozen enterprise customers, so
  outcomes will be few.
- **Three layers:**
  - **Feature risks:** 20 features, each scored 0 to 1 between two anchors.
  - **A weighted scorecard:** health 0–100, bands, and hard reds that force "at risk".
  - **A logistic probability:** ridge-penalised toward an expert prior derived from the scorecard weights.
- **Training and testing:** the model trains only at 20 or more events. A walk-forward backtest compares the
  scorecard, the prior, the trained model and the rules.

Full specification: `docs/21_RISK_MODEL.md`. Synthetic backtest: `docs/22_RISK_MODEL_BACKTEST.md`. The research
behind the signals: `docs/17_RISK_RESEARCH.md`.

## Where to look

| File | What |
|---|---|
| `code/risk/model.py` | Feature risk, scorecard, hard reds, prior, IRLS training with ridge toward the prior, explanations, metrics |
| `code/risk/backtest.py` | Labels (renewal window, horizon observed), thinning, walk-forward, lead time |
| `code/risk/features.py` | 20 point-in-time features as SQL against ClickHouse |
| `code/risk/risk_model.json` | Every setting: weights, anchors, hard reds, bands, prior rates, ridge, horizon |
| `code/risk/risk_model.py` | The command line, with the database or `--no-db` |
| `sample_data/generate_risk_panel.py` | The known-truth synthetic history used in tests |
| `code/risk/test_risk_model.py` | 25 checks; no database |

## Run it (Python 3.10+, standard library only)

```bash
python3 code/risk/test_risk_model.py
```
```bash
python3 sample_data/generate_risk_panel.py --out /tmp/panel
```
```bash
python3 code/risk/risk_model.py --no-db backtest --features /tmp/panel/features.ndjson --outcomes /tmp/panel/outcomes.ndjson --report /tmp/report.md
```

The full N360 path, with ClickHouse, a fake Salesforce and every dashboard, is `code/ci.sh chdb` (needs `pip install chdb`).

## Questions we want answered

1. **Correctness.**
   - Is the IRLS / ridge-to-prior implementation right (gradient, Hessian, unpenalised intercept, convergence)?
   - Are AUC (ties), Brier, reliability and lead time computed correctly?
2. **Leakage.**
   - Can the future reach a past snapshot? Check the point-in-time SQL in `features.py`, the labelling and the
     training cut-off in `backtest.py`.
   - Are the `pit: false` features kept out of training?
   - Are contract terms taken "as of today" a meaningful leak?
3. **Problem framing.**
   - Is "churn or downsell at the next renewal, for snapshots within 180 days of renewal" the right target?
   - Should downsell be a separate target, or weighted by amount?
   - Would a time-to-event (survival) model serve better once outcomes exist?
4. **Small data.**
   - With a few dozen customers and maybe a handful of events a year:
     - is the prior plus ridge (λ = 4, toward the prior) sensible?
     - is a training minimum of 20 events too low or too high?
     - is thinning to one snapshot per 28 days right?
   - What would you do instead?
5. **Calibration.**
   - The prior's 5% base and 75% maximum rates are guesses. How should they be set before outcomes exist, and checked
     after?
   - Should hard reds affect the probability, not just the band?
6. **Scorecard design.**
   - Linear anchors, weights inside dimensions, and re-weighting missing features with a coverage measure: sound?
   - Is any feature double-counted? For example, 30- and 90-day volume, or pace gap and forecast at term end.
7. **The synthetic test.**
   - Is the known-truth generator too easy, or too similar to the model's own assumptions? That would make the test
     flatter the model.
   - What harder cases should it plant? For example, non-linear effects, interactions, label noise, a regime change.
8. **What's missing.**
   - Features or methods a reviewer would expect in a SaaS risk model that is not here, given the data that exists.
     docs/17 section 3 lists the data N360 has and lacks.

## What we already know is weak

- **Unproven.** No real outcomes yet, so the probability on real data is the expert prior.
- **Self-similar test.** The synthetic truth was written by the same author as the model, so the test may be gentler
  than reality.
- **Contract terms** are rebuilt from today's contract data for past dates.
- **Current-state data.** The Salesforce forecast category and open tickets have no history, so they are scored
  today but never trained on.
- **The rules alone** (hard reds or the at-risk band) rank worse than the scorecard in the synthetic backtest.
  Expected: they are binary.
