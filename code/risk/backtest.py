"""Labels and the out-of-time backtest for the N360 risk model. Pure functions, standard library only.

Label for a snapshot (a customer on a date D) in its renewal window (next renewal within the horizon, or unknown):
1 when the customer churned or downsold in (D, D + horizon], 0 when not. Snapshots further from renewal are not
labelled: a contract cannot churn before it renews, so they say nothing about the model. A snapshot is labelled only once D + horizon has been observed, so a
recent snapshot is never counted as "did not churn" too early.

Backtest: walk forward in time. At each retraining date T, train on snapshots whose outcome was known by T
(D + horizon <= T), then score the snapshots of the next period. No snapshot is scored by a model that saw its own
outcome. Compared side by side: the scorecard (health), the expert prior (probability before any training), the
trained model, and the rules (hard reds or the "at risk" band).
"""
import datetime as dt

from model import auc, brier, predict, prior, reliability, scorecard, top_share, train, feature_keys


def _d(s):
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def label(row, outcomes_by_account, horizon, events, observed_until):
    """1 / 0, or None when the horizon after the snapshot is not fully observed yet."""
    d = _d(row['as_of'])
    end = d + dt.timedelta(horizon)
    if end > observed_until:
        return None
    return int(any(o['outcome'] in events and d < _d(o['date']) <= end for o in outcomes_by_account.get(row['account'], [])))


def by_account(outcomes):
    out = {}
    for o in outcomes:
        out.setdefault(o['account'], []).append(o)
    return out


def eligible(row, cfg):
    """In the decision window: the next renewal is within eligible_days_to_renewal, or its date is unknown."""
    d = row.get('days_to_renewal')
    return d is None or 0 <= d <= cfg['eligible_days_to_renewal']


def labelled(rows, outcomes, cfg, observed_until):
    oba = by_account(outcomes)
    out = []
    for r in rows:
        if not eligible(r, cfg):
            continue
        y = label(r, oba, cfg['horizon_days'], set(cfg['label_events']), observed_until)
        if y is not None:
            out.append((r, y))
    return out


def thin(pairs, days):
    """One snapshot per customer every `days` (oldest first): consecutive weekly snapshots of one customer are
    nearly the same observation, and counting each would overstate the evidence."""
    last, out = {}, []
    for r, y in sorted(pairs, key=lambda p: (p[0]['account'], str(p[0]['as_of']))):
        d = _d(r['as_of'])
        if r['account'] not in last or (d - last[r['account']]).days >= days:
            out.append((r, y)); last[r['account']] = d
    return out


def run(rows, outcomes, cfg, observed_until=None):
    """Walk-forward backtest. Returns metrics per method, the reliability table and lead times."""
    rows = sorted(rows, key=lambda r: (str(r['as_of']), r['account']))
    if not rows:
        return {'note': 'no snapshots'}
    observed_until = _d(observed_until) if observed_until else max(_d(r['as_of']) for r in rows)
    H = cfg['horizon_days']
    bt = cfg['backtest']
    lab = labelled(rows, outcomes, cfg, observed_until)
    first = _d(rows[0]['as_of'])
    t = first + dt.timedelta(bt['min_train_days'])
    step = dt.timedelta(bt['train_every_days'])
    keys = feature_keys(cfg, pit_only=True)
    preds = []                      # (row, y, health, prior_p, model_p, rule_flag, model_kind)
    pr = prior(cfg, keys)
    models = []
    while t <= observed_until:
        tr = thin([(r, y) for r, y in lab if _d(r['as_of']) + dt.timedelta(H) <= t], bt['train_sample_days'])
        te = [(r, y) for r, y in lab if t <= _d(r['as_of']) < t + step]
        if te:
            m = train([r for r, _ in tr], [y for _, y in tr], cfg, pit_only=True) if tr else dict(pr, kind='prior', events=0, n=0)
            models.append({'trained_at': t.isoformat(), 'kind': m['kind'], 'events': m.get('events', 0), 'n': m.get('n', 0)})
            for r, y in te:
                pit = {k: (v if k not in cfg['features'] or cfg['features'][k]['pit'] else None) for k, v in r.items()}
                sc = scorecard(pit, cfg)
                preds.append((r, y, sc['health'], predict(pr, pit, cfg), predict(m, pit, cfg),
                              int(sc['band'] == 'at risk'), m['kind']))
        t += step
    if not preds:
        return {'note': 'not enough history: no period has both a training set and labelled snapshots to score',
                'snapshots': len(rows), 'labelled': len(lab)}
    ys = [p[1] for p in preds]
    methods = {
        'scorecard (100 - health)': [100 - (p[2] if p[2] is not None else 50) for p in preds],
        'expert prior (probability)': [p[3] for p in preds],
        'trained model (probability)': [p[4] for p in preds],
        'rules (at-risk band)': [p[5] for p in preds],
    }
    res = {'snapshots_scored': len(preds), 'events': sum(ys), 'base_rate': sum(ys) / len(ys),
           'period': [str(preds[0][0]['as_of']), str(preds[-1][0]['as_of'])], 'retrainings': models, 'methods': {}}
    for name, s in methods.items():
        res['methods'][name] = {'auc': auc(s, ys), **{f'top_{k}': v for k, v in top_share(s, ys, bt['top_share']).items()}}
        if 'probability' in name:
            res['methods'][name]['brier'] = brier(s, ys)
    res['reliability'] = reliability(methods['trained model (probability)'], ys)
    res['lead_time_days'] = lead_times(preds, outcomes, cfg)
    return res


def lead_times(preds, outcomes, cfg):
    """For each churn or downsell, days between the first snapshot in the horizon before it that the trained model
    put in its top share (or the scorecard called at risk) and the event: how early the warning came."""
    events = set(cfg['label_events'])
    H = dt.timedelta(cfg['horizon_days'])
    probs = sorted(p[4] for p in preds)
    cut = probs[int(len(probs) * (1 - cfg['backtest']['top_share']))] if probs else 1
    out = {'model_top_share': [], 'scorecard_at_risk': []}
    for o in outcomes:
        if o['outcome'] not in events:
            continue
        od = _d(o['date'])
        window = [p for p in preds if p[0]['account'] == o['account'] and od - H <= _d(p[0]['as_of']) < od]
        for key, hit in (('model_top_share', lambda p: p[4] >= cut), ('scorecard_at_risk', lambda p: p[5] == 1)):
            flagged = [p for p in window if hit(p)]
            if window:
                out[key].append((od - _d(flagged[0][0]['as_of'])).days if flagged else 0)
    summary = {}
    for k, v in out.items():
        v = sorted(v)
        summary[k] = {'events': len(v), 'warned': sum(1 for x in v if x > 0),
                      'median_days_ahead': v[len(v) // 2] if v else None}
    return summary
