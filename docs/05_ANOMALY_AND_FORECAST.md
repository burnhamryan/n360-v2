# 05 · Anomaly detection and forecasting

## Anomaly detection v1 (built: view `v_va_day_anomaly` in `schema.sql`)

Each VA's daily conversations are compared with the same weekday over the previous 8 weeks.

| Part | Rule | Why |
|---|---|---|
| Baseline | Median of the same weekday, previous 8 weeks | Weekly shape: Mondays compare with Mondays |
| Spread | IQR / 1.349, floored at 10% of the median and at 5 | Robust to the spikes it looks for; floors stop quiet VAs alerting on noise |
| Scored | Baseline median ≥ 20 a day, 8 weeks of history | Below that, normal swings look anomalous |
| Went silent | Read, 0 conversations | Most urgent |
| Drop / spike | z ≤ −4 / z ≥ 4 | ~1 day in 300 for a normal VA |
| Provisional | Within 3 days of data-through | Recent days still settle; most real "drops" on the newest days were settling, not real |
| Not read | Never scored | NULL ≠ 0 |

On 30 days of real data this flagged about 3 settled drops or silences a week across ~60 active
VAs: a workable alert volume. Spikes (~9 a week) go on dashboards, not alerts. Thresholds are
first guesses; they are tuned monthly from review outcomes.

**Observed patterns the design must handle:**
- Several customers spiking on the same day is one event (e.g. an industry-wide disruption).
  Group same-day flags across ≥ 3 accounts into one item. Built: `v_anomaly_events` (settled days only;
  spikes grouped apart from drops and silences).
- The newest 0–2 days produce most false drops (data still settling). Provisional days never alert.

## Anomaly detection v2 (built 24 Nov 2026, B20: `v_anomaly_v2`; thresholds in `n360.settings`; tested on planted synthetic cases)

| Signal | Rule |
|---|---|
| Containment drop | Weekly containment per VA > 10 points below its 8-week median, on ≥ 200 engaged that week |
| Pace change | A line's crossing date moves > 14 days earlier night over night (needs `forecast_history`) |
| Measure gap | Contract measure / engaged flag ratio moves > 20 points from its usual level |
| Freshness | A source's last day > 2 days behind the run |
| Cross-account event | ≥ 3 accounts flagged the same settled day → one grouped item |

## Forecasting (forecast of record: built upstream, stored in `forecast`)

Straight line at the last-30-day rate of the contract measure, per line and per pool, computed
once in the company pipeline after contract rules are applied. **Never recomputed in Grafana
SQL.** `generate_synthetic.py::forecast_row` reproduces it exactly, so you can study it.

| Output | Definition |
|---|---|
| Rate per day | last 30 days / 30 |
| Forecast to horizon | used + rate × days to horizon (earlier of contract end and month end 3 months out) |
| Runs out on | today + (billed − used) / rate (none if > 100 years) |
| Crosses billed on | runs-out date if before contract end |
| Flags | Early term (< 60 days history) · Step change (last-30 rate vs term average differs > 50%) · Already above billed · Projected to exceed billed by a date |

## What to add (backlog items F1–F4) — built 24 Nov 2026 on synthetic

F1 `forecast_history` (B15, copied by `load.py` each loaded night), F2 accuracy (B16, `v_forecast_accuracy`,
`v_forecast_drift`), F3 challenger backtest (B21, `code/forecast/challenger.py`, report in `10_CHALLENGER_BACKTEST.md`).
F4 ranges is not built (the challenger gives no interval yet).


1. **`forecast_history`**: store every night's forecast per line and pool. Without it nothing can be checked.
2. **Accuracy**: for forecasts made 30, 60, 90 days ago, compare predicted cumulative use with actual; MAPE per line and overall; how far crossing dates moved.
3. **Challenger model**: weekday-aware seasonal model (e.g. Holt-Winters on daily data, in Python in the pipeline, never in Grafana), backtested from 1 Jan 2025. It replaces the straight line only if its 30-day MAPE beats it on most lines for 8 straight weeks **and Ryan agrees**.
4. **Ranges**: the challenger gives an 80% interval; show "crosses around 4 Feb (28 Jan to 15 Feb)" instead of one date that moves nightly.

Forecasts never use engaged where a rule applies, never sum lines outside a confirmed pool, and
say "Not reported yet" for lines that are not measurable or not implemented.

## Alerting (built 24 Nov 2026, B18: `code/grafana/build_alerts.py` → `provisioning/alerting/`; contact points are placeholders)

| Alert | Condition | To | When |
|---|---|---|---|
| Load failed | latest `runs` status failed_checks, or loader exit 2 | Day-to-day owner | Immediately |
| Data stale | no loaded run with data through yesterday by 10:00 ET | Owner | Daily |
| Source stale | any source > 2 days behind | Owner | Daily digest |
| VA silent / dropped | settled flag in the anomaly view | Owner → account CSM | Daily digest 09:00 ET |
| Cross-account event | ≥ 3 accounts same settled day | Owner | Daily digest |
| Over billed | line/pool newly > 100% | CS leadership + CSM | Monday digest |
| Crossing soon | crossing newly ≤ 30 days, or moved > 14 days earlier | CS leadership + CSM | Monday digest |

Every message: account, VA or line, value vs baseline, data-through date, panel link. Provisional
days never alert. Contact points (channel names, emails) are placeholders for Ryan to fill.
