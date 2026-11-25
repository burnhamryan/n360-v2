#!/usr/bin/env python3
"""Generate a SYNTHETIC customer history with a KNOWN risk process, to test the risk model (code/risk/):
weekly feature snapshots for many invented customers, and their renewal outcomes. Every customer and number is
invented; nothing comes from real data.

    python3 sample_data/generate_risk_panel.py --out /tmp/panel           # features.ndjson, outcomes.ndjson, truth.json

The truth: each customer has a hidden health h (AR(1) over weeks, persistent: half-life about 46 weeks; higher = worse; occasional shocks such as a
champion leaving). Features are noisy readings of h; some carry no signal at all (legacy_va_share, build_change_pct)
and some are missing (Phase 2 features for about a third of customers; Salesforce activity for about one in seven).
At each yearly renewal the customer churns with P = sigmoid(-3.0 + 1.7 h + 0.9 [no contact for 60+ days]), else
downsells with P = sigmoid(-3.2 + 1.0 h), else renews. A good model ranks snapshots by h (written on each row as _h,
which the model never reads: it is there so a test can compare the model with the best possible ranking).

The awkward parts of real records are planted on purpose (--easy leaves them out):
  - a churn is logged at the opt-out, 30 to 90 days BEFORE the term ends; the customer goes on producing rows
  - a churned customer keeps producing rows for half a year, with no contract and collapsing usage
  - about a third of churns are recorded twice (Salesforce, then the register a few days later)
  - about one renewal in twelve that went fine is never recorded at all
"""
import argparse, datetime as dt, json, math, os, random

TRUTH = {'churn': {'intercept': -3.0, 'h': 1.7, 'no_contact_60': 0.9}, 'downsell': {'intercept': -3.2, 'h': 1.0},
         'no_signal_features': ['legacy_va_share', 'build_change_pct']}


def sig(z):
    return 1 / (1 + math.exp(-z))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--customers', type=int, default=220)
    ap.add_argument('--weeks', type=int, default=130)
    ap.add_argument('--start', default='2024-05-06')
    ap.add_argument('--seed', type=int, default=20261125)
    ap.add_argument('--easy', action='store_true', help='no early notice, no rows after churn, no duplicate or missing outcomes')
    a = ap.parse_args()
    R = random.Random(a.seed)
    start = dt.date.fromisoformat(a.start)
    os.makedirs(a.out, exist_ok=True)
    feats, outs = [], []
    for i in range(a.customers):
        acct = f'Synthetic Customer {i + 1:03d}'
        h = R.gauss(0, 1)
        renew_week = R.randrange(20, 72)                       # first renewal, then yearly
        phase2 = R.random() > 0.33
        in_sf = a.easy or R.random() > 0.15                   # Salesforce activity is logged for this customer
        legacy = R.random()                                    # no signal, by design
        amount = round(R.lognormvariate(11.5, 0.8), -2)
        for w in range(a.weeks):
            d = start + dt.timedelta(weeks=w)
            h = 0.985 * h + R.gauss(0, 0.172)                  # persistent: half-life about 46 weeks, sd about 1
            if R.random() < 0.004:
                h += R.uniform(1.0, 2.0)                       # a shock: champion leaves, outage, reorg
            days_to_renewal = (renew_week - w) * 7
            contact = max(1.0, 12 + 28 * max(h, 0) + R.expovariate(1 / 8))
            f = {
                'account': acct, 'as_of': d.isoformat(), '_h': h,
                'pace_gap_pts': 10 + 12 * h + R.gauss(0, 6),
                'forecast_end_pct': 95 - 20 * h + R.gauss(0, 10),
                'renewal_forecast_risk': (1.0 if h > 1.6 else 0.5 if h > 0.4 else 0.0) if 0 <= days_to_renewal <= 120 else None,
                'conv_change_30_pct': -12 * h + R.gauss(0, 8),
                'conv_change_90_pct': -8 * h + R.gauss(0, 6),
                'conv_volatility': max(0.02, 0.2 + 0.08 * h + R.gauss(0, 0.05)),
                'silent_va_days_30': float(poisson(R, max(0.0, 0.6 * h))),
                'containment_pct': 52 - 6 * h + R.gauss(0, 6),
                'containment_change_pts': -3 * h + R.gauss(0, 2.5),
                'drop_days_30': float(poisson(R, max(0.0, 0.8 * h + 0.3))),
                'legacy_va_share': legacy if phase2 else None,
                'features_used': float(min(5, max(0, round(3.5 - 0.9 * h + R.gauss(0, 0.8))))) if phase2 else None,
                'build_change_pct': R.gauss(-10, 30) if phase2 else None,          # no signal, by design
                'weeks_config_unchanged': max(0.0, 6 + 4 * h + R.gauss(0, 3)) if phase2 else None,
                'days_since_contact': contact if in_sf else None,
                'days_since_exec': max(5.0, 80 + 60 * h + R.gauss(0, 40)) if in_sf else None,
                'open_tickets': float(poisson(R, max(0.0, 0.8 + 0.5 * h))),
                'oldest_open_ticket_days': max(0.0, 15 + 12 * h + R.gauss(0, 10)),
                'csm_pulse': 1.0 if h + R.gauss(0, 0.5) > 1.6 else 0.5 if h + R.gauss(0, 0.5) > 0.6 else 0.0,
                'open_churn_risks': 1.0 if h > 1.9 and R.random() < 0.5 else 0.0,
                'days_to_renewal': days_to_renewal if days_to_renewal >= 0 else None,
                'renewal_amount': amount, 'billed_lines': 1, 'data_gaps': 0,
            }
            feats.append({k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.items()})
            if w == renew_week:
                pc = sig(TRUTH['churn']['intercept'] + TRUTH['churn']['h'] * h + TRUTH['churn']['no_contact_60'] * (contact > 60))
                pd = sig(TRUTH['downsell']['intercept'] + TRUTH['downsell']['h'] * h)
                u = R.random()
                outcome = 'churned' if u < pc else 'downsold' if u < pc + (1 - pc) * pd else 'renewed'
                when = d + dt.timedelta(days=1)
                if outcome == 'churned' and not a.easy:
                    when = d - dt.timedelta(days=R.randrange(30, 91))          # logged at the opt-out
                if outcome != 'renewed' or a.easy or R.random() > 0.08:        # some good renewals are never recorded
                    outs.append({'account': acct, 'date': when.isoformat(), 'outcome': outcome, 'source': 'synthetic'})
                if outcome == 'churned':
                    if not a.easy:
                        if R.random() < 0.33:
                            outs.append({'account': acct, 'date': (when + dt.timedelta(days=R.randrange(2, 9))).isoformat(),
                                         'outcome': 'churned', 'source': 'synthetic register'})
                        last = feats[-1]
                        for k in range(1, 27):                                  # the customer is gone but still in the export
                            dd = d + dt.timedelta(weeks=k)
                            if (dd - start).days >= a.weeks * 7:
                                break
                            feats.append(dict(last, as_of=dd.isoformat(), _h=None, days_to_renewal=None, pace_gap_pts=None,
                                              forecast_end_pct=None, renewal_forecast_risk=None,
                                              conv_change_30_pct=-100.0 if k < 9 else None, conv_change_90_pct=-100.0 if k < 25 else None,
                                              conv_volatility=None, silent_va_days_30=30.0 if k < 6 else 0.0, drop_days_30=0.0,
                                              containment_pct=None, containment_change_pts=None,
                                              days_since_contact=None if last['days_since_contact'] is None else min(365.0, last['days_since_contact'] + 7 * k),
                                              days_since_exec=None if last['days_since_exec'] is None else min(365.0, last['days_since_exec'] + 7 * k)))
                    break
                renew_week += 52
    for name, rows in (('features', feats), ('outcomes', outs)):
        with open(os.path.join(a.out, f'{name}.ndjson'), 'w') as f:
            for r in rows:
                f.write(json.dumps(r) + '\n')
    json.dump(TRUTH, open(os.path.join(a.out, 'truth.json'), 'w'), indent=1)
    n = {o: sum(1 for x in outs if x['outcome'] == o) for o in ('churned', 'downsold', 'renewed')}
    print(f'synthetic risk panel: {a.customers} customers, {len(feats)} snapshots, outcomes {n}')


def poisson(R, lam):
    if lam <= 0:
        return 0
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= R.random()
        if p <= l:
            return k
        k += 1


if __name__ == '__main__':
    main()
