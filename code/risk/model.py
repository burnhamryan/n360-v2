"""The N360 customer risk model: pure functions over feature rows, no database, standard library only.
docs/21_RISK_MODEL.md explains the model; code/risk/risk_model.json holds every setting.

A feature row is a dict: {"account": ..., "as_of": "YYYY-MM-DD", <feature>: value or None, ...}.
None means "not reported" (the source had nothing): it is left out and its weight goes to the rest of its
dimension, never scored as 0 risk.

Three layers, each explainable:
  1. feature risks    each feature to a 0-1 risk by two anchors (good, bad)
  2. scorecard        weighted dimension risks -> health 0-100, band, hard-red exceptions, points lost per feature
  3. probability      P(churn or downsell within the horizon): logistic regression on the feature risks, shrunk
                      toward an expert prior built from the scorecard weights (ridge to the prior, not to zero)
"""
import math

# ───────────────────────── layer 1: feature risks ─────────────────────────


def feature_risk(value, good, bad):
    """0 at or better than `good`, 1 at or worse than `bad`, linear between; None stays None."""
    if value is None:
        return None
    if bad == good:
        return 0.0
    return min(1.0, max(0.0, (float(value) - good) / (bad - good)))


def risks(row, cfg):
    return {k: feature_risk(row.get(k), f['good'], f['bad']) for k, f in cfg['features'].items()}


# ───────────────────────── layer 2: scorecard ─────────────────────────


def scorecard(row, cfg):
    """Health 0-100 (100 = no risk), band, coverage, dimension risks, points lost per feature, hard reds."""
    r = risks(row, cfg)
    dims = cfg['dimensions']
    dim_risk, dim_cov = {}, {}
    for d in dims:
        fs = [(k, f['weight']) for k, f in cfg['features'].items() if f['dimension'] == d]
        have = [(k, w) for k, w in fs if r[k] is not None]
        tot = sum(w for _, w in fs)
        dim_cov[d] = sum(w for _, w in have) / tot if tot else 0.0
        dim_risk[d] = sum(r[k] * w for k, w in have) / sum(w for _, w in have) if have else None
    present = {d: dims[d]['weight'] for d in dims if dim_risk[d] is not None}
    wsum = sum(present.values())
    if not wsum:
        return dict(health=None, band='not reported', coverage=0.0, dimensions=dim_risk, contributions={}, hard_reds=[])
    total_risk = sum(dim_risk[d] * w for d, w in present.items()) / wsum
    # points of health each feature costs: its share of its dimension, times the dimension's share, times 100
    contrib = {}
    for d, w in present.items():
        fs = [(k, f['weight']) for k, f in cfg['features'].items() if f['dimension'] == d and r[k] is not None]
        fw = sum(x for _, x in fs)
        for k, x in fs:
            contrib[k] = 100.0 * (w / wsum) * (x / fw) * r[k]
    health = 100.0 * (1.0 - total_risk)
    reds = hard_reds(row, cfg)
    if reds:
        health = min(health, cfg['hard_red']['cap_health'])
    band = ('at risk' if reds or health < cfg['bands']['watch'] else 'watch' if health < cfg['bands']['healthy'] else 'healthy')
    coverage = sum(dims[d]['weight'] * dim_cov[d] for d in dims)
    return dict(health=round(health, 1), band=band, coverage=round(coverage, 3), dimensions=dim_risk,
                contributions={k: round(v, 2) for k, v in contrib.items()}, hard_reds=reds)


OPS = {'>=': lambda a, b: a >= b, '>': lambda a, b: a > b, '<=': lambda a, b: a <= b, '<': lambda a, b: a < b,
       '==': lambda a, b: a == b}


def hard_reds(row, cfg):
    out = []
    for rule in cfg['hard_red']['rules']:
        v = row.get(rule['feature'])
        if v is None or not OPS[rule['op']](float(v), rule['value']):
            continue
        lim = rule.get('when_days_to_renewal_le')
        if lim is not None and (row.get('days_to_renewal') is None or not 0 <= row['days_to_renewal'] <= lim):
            continue
        out.append(rule['label'])
    return out


# ───────────────────────── layer 3: probability ─────────────────────────


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z)) if z > -700 else 0.0


def logit(p):
    return math.log(p / (1.0 - p))


def feature_keys(cfg, pit_only=False):
    return [k for k, f in cfg['features'].items() if f['pit'] or not pit_only]


def prior(cfg, keys):
    """The expert prior: intercept = logit(base rate); each feature's coefficient = its overall scorecard weight
    times the span logit(max rate) - logit(base rate), so a customer at full risk on every feature scores the max
    rate and one at no risk the base rate. The scorecard and the prior always agree on the ranking of drivers."""
    p = cfg['probability']
    span = logit(p['prior_max_rate']) - logit(p['prior_base_rate'])
    w = {k: cfg['dimensions'][cfg['features'][k]['dimension']]['weight'] * cfg['features'][k]['weight'] for k in keys}
    tot = sum(w.values())
    return {'intercept': logit(p['prior_base_rate']), 'coef': {k: span * w[k] / tot for k in keys},
            'fill': {k: 0.0 for k in keys}}


def design(row, keys, fill, cfg):
    """Model inputs: each feature's 0-1 risk; a missing one takes the training mean (fill), so it adds nothing."""
    out = []
    for k in keys:
        f = cfg['features'][k]
        x = feature_risk(row.get(k), f['good'], f['bad'])
        out.append(fill[k] if x is None else x)
    return out


def predict(model, row, cfg):
    keys = list(model['coef'])
    x = design(row, keys, model['fill'], cfg)
    return sigmoid(model['intercept'] + sum(model['coef'][k] * xi for k, xi in zip(keys, x)))


def explain(model, row, cfg):
    """Each feature's push on the log-odds against an average customer (coef x (risk - training mean))."""
    keys = list(model['coef'])
    x = design(row, keys, model['fill'], cfg)
    return {k: round(model['coef'][k] * (xi - model['fill'][k]), 4) for k, xi in zip(keys, x)}


def solve(a, b):
    """Solve a x = b (small dense system, Gauss-Jordan with partial pivoting)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        inv = 1.0 / m[c][c]
        for j in range(c, n + 1):
            m[c][j] *= inv
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                for j in range(c, n + 1):
                    m[r][j] -= f * m[c][j]
    return [m[i][n] for i in range(n)]


def train(rows, labels, cfg, pit_only=True):
    """Logistic regression on the feature risks, ridge-penalised toward the expert prior (Newton / IRLS).
    Fewer than min_events_to_train positive outcomes: returns the prior itself (and says so)."""
    keys = feature_keys(cfg, pit_only)
    pr = prior(cfg, keys)
    pcfg = cfg['probability']
    events = sum(labels)
    n = len(rows)
    if events < pcfg['min_events_to_train'] or n - events < pcfg['min_events_to_train']:
        pr.update(kind='prior', n=n, events=events, note=f'{events} events: under {pcfg["min_events_to_train"]}, prior only')
        return pr
    # fill = training mean of each risk, so a missing feature contributes nothing against the average customer
    fill = {}
    for k in keys:
        vals = [feature_risk(r.get(k), cfg['features'][k]['good'], cfg['features'][k]['bad']) for r in rows]
        vals = [v for v in vals if v is not None]
        fill[k] = sum(vals) / len(vals) if vals else 0.0
    X = [[1.0] + design(r, keys, fill, cfg) for r in rows]
    beta0 = [pr['intercept']] + [pr['coef'][k] for k in keys]
    beta = beta0[:]
    lam = pcfg['ridge_lambda']
    d = len(beta)
    for _ in range(pcfg['max_iter']):
        grad = [0.0] * d
        hess = [[0.0] * d for _ in range(d)]
        for xi, yi in zip(X, labels):
            p = sigmoid(sum(b * x for b, x in zip(beta, xi)))
            w = max(p * (1 - p), 1e-9)
            for a in range(d):
                grad[a] += (yi - p) * xi[a]
                wa = w * xi[a]
                for c in range(a, d):
                    hess[a][c] += wa * xi[c]
        for a in range(d):
            for c in range(a):
                hess[a][c] = hess[c][a]
        for a in range(1, d):                          # the intercept is not penalised
            grad[a] -= lam * (beta[a] - beta0[a])
            hess[a][a] += lam
        step = solve(hess, grad)
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-7:
            break
    return {'kind': 'trained', 'intercept': beta[0], 'coef': dict(zip(keys, beta[1:])), 'fill': fill,
            'n': n, 'events': events, 'note': f'{events} events in {n} snapshots; ridge {lam} toward the prior'}


# ───────────────────────── metrics ─────────────────────────


def auc(scores, labels):
    """Area under the ROC curve (Mann-Whitney, ties shared). None if one class is missing."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2.0 + 1
        i = j + 1
    rp = sum(r for r, y in zip(ranks, labels) if y)
    return (rp - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def brier(probs, labels):
    return sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(probs) if probs else None


def top_share(scores, labels, share):
    """Precision and recall when the top `share` of scores is flagged."""
    n = max(1, round(len(scores) * share))
    top = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]
    hit = sum(labels[i] for i in top)
    tot = sum(labels)
    return {'precision': hit / n, 'recall': hit / tot if tot else None, 'flagged': n}


def reliability(probs, labels, bins=10):
    """Calibration: per probability bin, mean predicted against the observed rate."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            out.append({'bin': f'{lo:.1f}-{hi:.1f}', 'n': len(idx), 'predicted': sum(probs[i] for i in idx) / len(idx),
                        'observed': sum(labels[i] for i in idx) / len(idx)})
    return out
