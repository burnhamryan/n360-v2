"""Labels and the out-of-time backtest for the N360 risk model. Pure functions, standard library only.

The unit is the RENEWAL, not the snapshot. A snapshot (a customer on a date) belongs to the customer's next renewal
when that renewal is 0 to eligible_days_to_renewal days away; a snapshot with no known renewal date belongs to none
and is never labelled. The renewal's outcome is the churn, downsell or renewal recorded around its date
(label.outcome_days_before / _after: a loss is usually logged at the opt-out, weeks before the term ends). Then:

  label 1   the renewal was lost (churned or downsold)
  label 0   it was renewed, or nothing was recorded and outcome_days_after have passed since the renewal date
  no label  outcome_days_after have not yet passed since the renewal date. This holds for losses too: a loss is
            logged weeks before a kept renewal is, so learning from each as soon as it is recorded would fill the
            training set with losses first and overstate the rate
  dropped   snapshots taken on or after the day the outcome was recorded (the answer was already known), and every
            snapshot of a customer after it churned

Backtest: walk forward in time. At each retraining date T, train on snapshots whose renewal outcome was known by T,
then score the snapshots of the next period. No snapshot is scored by a model that saw its own outcome. Each renewal
is judged at fixed lead times (backtest.lead_days before the renewal), so a renewal counts a few times, not twenty;
intervals come from resampling whole customers. Compared: the scorecard, the expert prior, the model as it would
have been trained at the time, and the rules (hard reds or the "at risk" band).
"""
import datetime as dt
import random

from model import (auc, brier, flag_stats, mask_pit, predict, prior, probability, reliability, scorecard, top_share, train)


def _d(s):
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def by_account(outcomes):
    out = {}
    for o in outcomes:
        out.setdefault(o['account'], []).append(o)
    return out


def churn_dates(outcomes):
    """The first day each customer is known to have churned."""
    out = {}
    for o in outcomes:
        if o['outcome'] == 'churned' and (o['account'] not in out or _d(o['date']) < out[o['account']]):
            out[o['account']] = _d(o['date'])
    return out


def renewal_date(row, cfg):
    """The renewal this snapshot is judged on: its date when it is 0 to eligible_days_to_renewal days away, else None.
    An unknown renewal date is None: unknown is not "in the window"."""
    d = row.get('days_to_renewal')
    if d is None or not 0 <= d <= cfg['eligible_days_to_renewal']:
        return None
    return _d(row['as_of']) + dt.timedelta(round(d))


def eligible(row, cfg):
    return renewal_date(row, cfg) is not None


def outcome_of(account, renewal, oba, cfg):
    """The outcome recorded for this renewal: the earliest loss in its window if there is one (the same churn often
    appears twice, in Salesforce and in the register), else the earliest renewal, else None."""
    lab = cfg['label']
    lo, hi = renewal - dt.timedelta(lab['outcome_days_before']), renewal + dt.timedelta(lab['outcome_days_after'])
    near = sorted((o for o in oba.get(account, []) if lo <= _d(o['date']) <= hi), key=lambda o: _d(o['date']))
    lost = [o for o in near if o['outcome'] in cfg['label_events']]
    return (lost or near or [None])[0]


def labelled(rows, outcomes, cfg, observed_until):
    """One dict per labelled snapshot: row, y, group (the renewal), lead (days to the renewal), known (the day the
    label became known), event_date (the day a loss was recorded, else None)."""
    oba, gone = by_account(outcomes), churn_dates(outcomes)
    events, after = set(cfg['label_events']), dt.timedelta(cfg['label']['outcome_days_after'])
    out = []
    for r in rows:
        d, rd = _d(r['as_of']), renewal_date(r, cfg)
        if rd is None or (r['account'] in gone and d >= gone[r['account']]):
            continue
        o = outcome_of(r['account'], rd, oba, cfg)
        if o is not None:
            if d >= _d(o['date']):
                continue                                    # taken after the outcome was recorded
            y = int(o['outcome'] in events)
        else:
            y = 0                                           # nothing recorded: counted as renewed once the grace has passed
        # Every label becomes usable on the same day relative to its renewal. A loss is logged weeks before a kept
        # renewal is, so using each as soon as it is recorded would fill the training set with losses first.
        known = rd + after
        if known > observed_until:
            continue
        out.append({'row': r, 'y': y, 'group': (r['account'], rd.isoformat()), 'lead': (rd - d).days, 'known': known,
                    'event_date': _d(o['date']) if o is not None and y else None})
    return out


def thin(lab, days):
    """One snapshot per renewal every `days` (oldest first): consecutive weekly snapshots are nearly the same
    observation. What is left is still weighted as one renewal in training."""
    last, out = {}, []
    for x in sorted(lab, key=lambda x: (x['group'], str(x['row']['as_of']))):
        d = _d(x['row']['as_of'])
        if x['group'] not in last or (d - last[x['group']]).days >= days:
            out.append(x); last[x['group']] = d
    return out


def fit(lab, cfg, prior_rows=None, stage=None):
    """Train on labelled snapshots (thinned, weighted per renewal)."""
    tr = thin(lab, cfg['backtest']['train_sample_days'])
    return train([x['row'] for x in tr], [x['y'] for x in tr], [x['group'] for x in tr], cfg, prior_rows, stage)


def run(rows, outcomes, cfg, observed_until=None):
    """Walk-forward backtest. Returns metrics per method, calibration and lead times."""
    rows = sorted(rows, key=lambda r: (str(r['as_of']), r['account']))
    if not rows:
        return {'note': 'no snapshots'}
    observed_until = _d(observed_until) if observed_until else max(_d(r['as_of']) for r in rows)
    bt = cfg['backtest']
    lab = labelled(rows, outcomes, cfg, observed_until)
    window = [(r, _d(r['as_of'])) for r in rows if eligible(r, cfg)]
    first = _d(rows[0]['as_of'])
    t = first + dt.timedelta(bt['min_train_days'])
    step = dt.timedelta(bt['train_every_days'])
    preds, models = [], []
    while t <= observed_until:
        te = [x for x in lab if t <= _d(x['row']['as_of']) < t + step]
        if te:
            # the level of the prior: set on the customers in their renewal window in the period just before T
            recent = [mask_pit(r, cfg) for r, d in window if t - step <= d < t]
            pr = prior(cfg, recent)
            m = fit([x for x in lab if x['known'] <= t], cfg, recent)
            models.append({'trained_at': t.isoformat(), 'kind': m['kind'], 'stage': m['stage'], 'events': m.get('events', 0),
                           'renewals': m.get('renewals', 0), 'n': m.get('n', 0)})
            for x in te:
                pit = mask_pit(x['row'], cfg)
                sc = scorecard(pit, cfg)
                if sc['health'] is None:
                    continue
                preds.append(dict(x, health=sc['health'], prior=probability(pr, pit, cfg), model=probability(m, pit, cfg),
                                  rule=int(sc['band'] == 'at risk'), stage=m['stage']))
        t += step
    if not preds:
        return {'note': 'not enough history: no period has both a training set and labelled snapshots to score',
                'snapshots': len(rows), 'labelled': len(lab)}
    ev = at_leads(preds, bt['lead_days'], bt['lead_tolerance_days'])
    ys = [p['y'] for p in ev]
    S = {'scorecard (100 - health)': lambda p: 100 - p['health'], 'expert prior (probability)': lambda p: p['prior'],
         'trained model (probability)': lambda p: p['model'], 'rules (at-risk band)': lambda p: p['rule']}
    groups = {p['group'] for p in preds}
    res = {'snapshots_scored': len(preds), 'renewals': len(groups), 'events': len({p['group'] for p in preds if p['y']}),
           'judged': len(ev), 'lead_days': bt['lead_days'], 'period': [str(preds[0]['row']['as_of']), str(preds[-1]['row']['as_of'])],
           'retrainings': models, 'methods': {}}
    res['base_rate'] = res['events'] / res['renewals']
    ci = intervals(ev, S, bt['bootstrap'])
    for name, f in S.items():
        s = [f(p) for p in ev]
        x = {'auc': auc(s, ys), 'auc_low': ci[name][0], 'auc_high': ci[name][1]}
        if name.startswith('rules'):
            x.update(flag_stats(s, ys))
        else:
            x.update({f'top_{k}': v for k, v in top_share(s, ys, bt['top_share']).items()})
        if 'probability' in name:
            x.update(brier=brier(s, ys), expected_events=sum(s), observed_events=sum(ys))
        res['methods'][name] = x
    res['trained_minus_prior_auc'] = ci['_diff']
    # the model against the prior only where the model really had learned something
    tr = [p for p in ev if p['stage'] != 'prior']
    if tr:
        yt = [p['y'] for p in tr]
        res['trained_periods'] = {'judged': len(tr), 'events': sum(yt),
                                  'auc_prior': auc([p['prior'] for p in tr], yt), 'auc_model': auc([p['model'] for p in tr], yt),
                                  'brier_prior': brier([p['prior'] for p in tr], yt), 'brier_model': brier([p['model'] for p in tr], yt)}
    res['by_lead'] = {str(L): {'judged': len(sub), 'auc_scorecard': auc([100 - p['health'] for p in sub], [p['y'] for p in sub]),
                               'auc_model': auc([p['model'] for p in sub], [p['y'] for p in sub])}
                      for L in bt['lead_days'] for sub in [[p for p in ev if p['at_lead'] == L]]}
    res['reliability'] = reliability([p['model'] for p in ev], ys, 5)
    res['reliability_prior'] = reliability([p['prior'] for p in ev], ys, 5)
    res['lead_time_days'] = lead_times(preds, cfg)
    return res


def at_leads(preds, leads, tol):
    """Each renewal judged at fixed lead times: for each lead, the snapshot closest to it (within `tol` days)."""
    by = {}
    for p in preds:
        by.setdefault(p['group'], []).append(p)
    out = []
    for g in sorted(by):
        for L in leads:
            best = min(by[g], key=lambda p: abs(p['lead'] - L))
            if abs(best['lead'] - L) <= tol:
                out.append(dict(best, at_lead=L))
    return out


def intervals(ev, S, draws, seed=20261126):
    """95% intervals for each method's AUC, and for trained minus prior, by resampling whole customers (a customer's
    renewals and lead times are not independent evidence)."""
    by = {}
    for p in ev:
        by.setdefault(p['group'][0], []).append(p)
    accs = sorted(by)
    R = random.Random(seed)
    got = {name: [] for name in S}
    diff = []
    for _ in range(draws):
        P = [p for a in (R.choice(accs) for _ in accs) for p in by[a]]
        y = [p['y'] for p in P]
        a = {name: auc([f(p) for p in P], y) for name, f in S.items()}
        if None in a.values():
            continue
        for name in S:
            got[name].append(a[name])
        diff.append(a['trained model (probability)'] - a['expert prior (probability)'])

    def q(v):
        v = sorted(v)
        return [v[int(0.025 * (len(v) - 1))], v[int(0.975 * (len(v) - 1))]] if len(v) >= 20 else [None, None]
    out = {name: q(v) for name, v in got.items()}
    out['_diff'] = q(diff)
    return out


def lead_times(preds, cfg):
    """For each renewal lost: was it flagged beforehand, how early, and was the flag still up when it mattered?
      warned             flagged on at least one snapshot before the loss was recorded
      median_days_ahead  among those warned, days from the first flag to the loss
      held               flagged on the last snapshot taken `hold_days` or more before the loss
      false_alarm_share  share of snapshots of renewals that were NOT lost that carried the flag
    The model's flag is a fixed probability (backtest.alert_probability), known on the day; the scorecard's is its
    at-risk band."""
    bt = cfg['backtest']
    hits = {'model_alert': lambda p: p['model'] >= bt['alert_probability'], 'scorecard_at_risk': lambda p: p['rule'] == 1}
    by = {}
    for p in preds:
        by.setdefault(p['group'], []).append(p)
    summary = {}
    for key, hit in hits.items():
        leads, held, n = [], 0, 0
        for g, ps in by.items():
            if not ps[0]['y']:
                continue
            n += 1
            ps = sorted(ps, key=lambda p: str(p['row']['as_of']))
            od = ps[0]['event_date']
            flagged = [p for p in ps if hit(p)]
            if flagged:
                leads.append((od - _d(flagged[0]['row']['as_of'])).days)
            early = [p for p in ps if (od - _d(p['row']['as_of'])).days >= bt['hold_days']]
            held += int(bool(early) and hit(early[-1]))
        leads.sort()
        neg = [p for p in preds if not p['y']]
        summary[key] = {'events': n, 'warned': len(leads), 'median_days_ahead': leads[len(leads) // 2] if leads else None,
                        'held': held, 'false_alarm_share': sum(1 for p in neg if hit(p)) / len(neg) if neg else None}
    return summary
