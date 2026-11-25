"""The N360 customer risk model: pure functions over feature rows, no database, standard library only.
docs/21_RISK_MODEL.md explains the model; code/risk/risk_model.json holds every setting.

A feature row is a dict: {"account": ..., "as_of": "YYYY-MM-DD", <feature>: value or None, ...}.
None means "not reported" (the source had nothing): it is left out and its weight goes to the rest of its
dimension, never scored as 0 risk. That holds in every layer, the probability included.

Three layers, each explainable:
  1. feature risks    each feature to a 0-1 risk by two anchors (good, bad)
  2. scorecard        weighted dimension risks -> health 0-100, band, hard-red exceptions, points lost per feature
  3. probability      P(churn or downsell at the next renewal): a logistic model on the same re-weighted risks.
                      It starts as an expert prior built from the scorecard and learns in stages as real outcomes
                      arrive: first only the level and one slope, then one weight per dimension, then one per feature.
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


def weights(cfg):
    """Each feature's overall weight: its dimension's weight times its share of the dimension. They add up to 1."""
    w = {k: cfg['dimensions'][f['dimension']]['weight'] * f['weight'] for k, f in cfg['features'].items()}
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()}


def mask_pit(row, cfg):
    """The row with every current-only feature (pit: false) blanked: what a past date can honestly know."""
    return {k: (v if k not in cfg['features'] or cfg['features'][k]['pit'] else None) for k, v in row.items()}


def rollup(r, cfg):
    """Dimension risks (weighted mean of the reported features), dimension coverage, and the overall risk (weighted
    mean of the dimensions that have any data). Overall risk is None when nothing is reported."""
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
    total = sum(dim_risk[d] * w for d, w in present.items()) / wsum if wsum else None
    return dim_risk, dim_cov, total, present


# ───────────────────────── layer 2: scorecard ─────────────────────────

CAP = 'hard_red_cap'      # the contribution line that carries the points a hard red takes beyond the features


def scorecard(row, cfg):
    """Health 0-100 (100 = no risk), band, coverage, dimension risks, points lost per feature, hard reds.
    Points lost always add up to 100 - health: when a hard red caps health, the extra points sit on CAP."""
    r = risks(row, cfg)
    dims = cfg['dimensions']
    dim_risk, dim_cov, total_risk, present = rollup(r, cfg)
    reds = hard_reds(row, cfg)
    coverage = round(sum(dims[d]['weight'] * dim_cov[d] for d in dims) / sum(dims[d]['weight'] for d in dims), 3)
    if total_risk is None:
        return dict(health=None, band='at risk' if reds else 'not reported', coverage=0.0, dimensions=dim_risk,
                    contributions={}, hard_reds=reds, hard_reds_unchecked=unchecked_reds(row, cfg))
    wsum = sum(present.values())
    # points of health each feature costs: its share of its dimension, times the dimension's share, times 100
    contrib = {}
    for d, w in present.items():
        fs = [(k, f['weight']) for k, f in cfg['features'].items() if f['dimension'] == d and r[k] is not None]
        fw = sum(x for _, x in fs)
        for k, x in fs:
            contrib[k] = 100.0 * (w / wsum) * (x / fw) * r[k]
    health = 100.0 * (1.0 - total_risk)
    if reds and health > cfg['hard_red']['cap_health']:
        contrib[CAP] = health - cfg['hard_red']['cap_health']
        health = cfg['hard_red']['cap_health']
    if reds:
        band = 'at risk'
    elif coverage < cfg['bands'].get('min_coverage', 0):
        band = 'not enough data'          # too little reported to call a customer healthy (or not)
    else:
        band = 'at risk' if health < cfg['bands']['watch'] else 'watch' if health < cfg['bands']['healthy'] else 'healthy'
    return dict(health=round(health, 1), band=band, coverage=coverage, dimensions=dim_risk,
                contributions={k: round(v, 2) for k, v in contrib.items()}, hard_reds=reds,
                hard_reds_unchecked=unchecked_reds(row, cfg))


OPS = {'>=': lambda a, b: a >= b, '>': lambda a, b: a > b, '<=': lambda a, b: a <= b, '<': lambda a, b: a < b,
       '==': lambda a, b: a == b}


def _fired(row, cfg):
    """(rule, state) per hard-red rule: 'red', 'clear', or 'unchecked' (it would fire, but it only applies inside a
    renewal window and the renewal date is not known)."""
    for rule in cfg['hard_red']['rules']:
        v = row.get(rule['feature'])
        if v is None or not OPS[rule['op']](float(v), rule['value']):
            yield rule, 'clear'
            continue
        lim = rule.get('when_days_to_renewal_le')
        if lim is None:
            yield rule, 'red'
        elif row.get('days_to_renewal') is None:
            yield rule, 'unchecked'
        else:
            yield rule, 'red' if 0 <= row['days_to_renewal'] <= lim else 'clear'


def hard_reds(row, cfg):
    return [rule['label'] for rule, state in _fired(row, cfg) if state == 'red']


def unchecked_reds(row, cfg):
    return [rule['label'] for rule, state in _fired(row, cfg) if state == 'unchecked']


# ───────────────────────── layer 3: probability ─────────────────────────


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z)) if z > -700 else 0.0


def logit(p):
    return math.log(p / (1.0 - p))


def feature_keys(cfg, pit_only=False):
    return [k for k, f in cfg['features'].items() if f['pit'] or not pit_only]


def design(row, cfg):
    """Model inputs, one per feature: its 0-1 risk. A feature that is not reported takes its dimension's risk (from
    the features that are), and a dimension with nothing takes the overall risk. So sum(weight x input) is exactly
    the scorecard's re-weighted risk: "not reported" never reads as "fine". None when nothing is reported."""
    r = risks(row, cfg)
    dim_risk, _, total, _ = rollup(r, cfg)
    if total is None:
        return None
    return {k: (r[k] if r[k] is not None else dim_risk[f['dimension']] if dim_risk[f['dimension']] is not None else total)
            for k, f in cfg['features'].items()}


def prior(cfg, history=None):
    """The expert prior. In words: a customer at the average scorecard risk (prior_mean_risk) has the portfolio's
    renewal-loss rate (prior_mean_rate); a customer at full risk on everything has prior_max_rate; each feature moves
    the log-odds in proportion to its scorecard weight.

    Its level is FIXED: the same readings give the same chance on any day, whoever else is renewing. (It was once
    re-set every night on the customers then in their renewal window: with a handful of customers there, one
    customer's chance moved by tens of points as others entered or left the window.)

    Without `history` the level comes from the settings alone. With `history` (the stored snapshots of customers in
    their renewal window, over the whole record: hundreds of rows, not one night) the level is set ONCE so that their
    average chance is prior_mean_rate, and it stays with the model run. That is what 'train' does; the run is used
    only once adopted. Under prior_level_min_snapshots rows the settings level is kept: too few to set a level on."""
    p = cfg['probability']
    span = (logit(p['prior_max_rate']) - logit(p['prior_mean_rate'])) / (1.0 - p['prior_mean_risk'])
    w = weights(cfg)
    m = {'kind': 'prior', 'stage': 'prior', 'intercept': logit(p['prior_mean_rate']) - span * p['prior_mean_risk'],
         'coef': {k: span * w[k] for k in w}, 'mean': {k: p['prior_mean_risk'] for k in w}, 'n': 0, 'events': 0,
         'note': 'expert prior, level from the settings'}
    if history:
        z = [v for v in (linear(m, mask_pit(r, cfg), cfg) for r in history) if v is not None]
        if len(z) >= p.get('prior_level_min_snapshots', 0):
            m['intercept'] += _level(z, p['prior_mean_rate'])
            m['note'] = f'expert prior, level set once on {len(z)} snapshots of customers in their renewal window'
    return m


def _level(scores, rate):
    """The shift that makes the mean of sigmoid(score + shift) equal `rate` (bisection)."""
    lo, hi = -20.0, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(sigmoid(s + mid) for s in scores) / len(scores) < rate:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def mean_risk(rows, cfg):
    """The average scorecard risk (0-1) of these rows, point-in-time features only: what prior_mean_risk should be
    close to when the rows are customers in their renewal window. None when no row has anything reported."""
    w = weights(cfg)
    v = [sum(w[k] * x[k] for k in w) for x in (design(mask_pit(r, cfg), cfg) for r in rows) if x is not None]
    return sum(v) / len(v) if v else None


def linear(model, row, cfg):
    x = design(row, cfg)
    if x is None:
        return None
    return model['intercept'] + sum(b * x[k] for k, b in model['coef'].items())


def predict(model, row, cfg):
    """The model's probability, before hard reds. None when nothing is reported for the customer."""
    z = linear(model, row, cfg)
    return None if z is None else sigmoid(z)


def probability(model, row, cfg):
    """The probability shown: the model's, raised to a hard red's floor when one fires (a red CSM rating or a renewal
    marked lost says more than the weights do)."""
    p = predict(model, row, cfg)
    floors = [rule['min_probability'] for rule, state in _fired(row, cfg) if state == 'red' and 'min_probability' in rule]
    if floors:
        p = max([p or 0.0] + floors)
    return p


def explain(model, row, cfg):
    """Each reported feature's push on the log-odds against the average customer. A feature that is not reported has
    no push of its own: its weight is carried by the reported features of its dimension, and their pushes include it.
    The pushes add up exactly to the customer's log-odds minus the average customer's, for any model."""
    r = risks(row, cfg)
    have = [k for k in r if r[k] is not None]
    if not have:
        return {}
    mean = model.get('mean', {})
    out = {}
    for k in have:
        # how much of each model input this feature carries: its own, plus its share of every input it stands in for
        rr = {j: (None if r[j] is None else 1.0 if j == k else 0.0) for j in r}
        dim_risk, _, total, _ = rollup(rr, cfg)
        carried = {j: (rr[j] if rr[j] is not None else dim_risk[f['dimension']] if dim_risk[f['dimension']] is not None else total)
                   for j, f in cfg['features'].items()}
        out[k] = round(sum(b * carried[j] * (r[k] - mean.get(j, 0.0)) for j, b in model['coef'].items()), 4)
    return out


def solve(a, b):
    """Solve a x = b (small dense system, Gauss-Jordan with partial pivoting). Raises on a singular system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            raise ArithmeticError('singular system: two model inputs are identical or one never varies')
        inv = 1.0 / m[c][c]
        for j in range(c, n + 1):
            m[c][j] *= inv
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                for j in range(c, n + 1):
                    m[r][j] -= f * m[c][j]
    return [m[i][n] for i in range(n)]


def fit(Z, y, wts, offset, target, lam, max_iter, lam0=0.0):
    """Weighted logistic regression by Newton / IRLS. Z's first column is the intercept: free, unless lam0 > 0, which
    pulls it toward target[0] with penalty lam0/2 x (b0 - target0)^2 (the prior's level counted as so many renewals).
    Every other coefficient is pulled toward `target` with a relative ridge: penalty = lam/2 x ((b - target) / target)^2,
    so the prior spread of a weight is target / sqrt(lam), whatever its size. Coefficients may not go below zero:
    a risk never lowers the chance of churn (one that tries is held at zero and the rest refitted).
    Returns (coefficients, converged, passes)."""
    d = len(Z[0])
    free = [j for j in range(d) if j == 0 or target[j] > 0]    # a weight whose prior is zero stays zero
    beta = [0.0] * d
    passes = 0
    converged = True
    while True:
        def objective(b):
            ll = 0.0
            for zi, yi, wi, oi in zip(Z, y, wts, offset):
                z = oi + sum(bb * zi[j] for bb, j in zip(b, free))
                ll += wi * (yi * z - (z if z > 30 else math.log1p(math.exp(z))))
            ll -= 0.5 * lam0 * (b[0] - target[0]) ** 2
            return ll - 0.5 * lam * sum(((b[a] - target[free[a]]) / target[free[a]]) ** 2 for a in range(1, len(free)))
        b = [target[0] if lam0 else logit(min(max(_wmean(y, wts), 1e-4), 1 - 1e-4))] + [target[j] for j in free[1:]]
        ok = False
        for _ in range(max_iter):
            passes += 1
            grad = [0.0] * len(free)
            hess = [[0.0] * len(free) for _ in free]
            for zi, yi, wi, oi in zip(Z, y, wts, offset):
                x = [zi[j] for j in free]
                p = sigmoid(oi + sum(bb * xx for bb, xx in zip(b, x)))
                v = wi * max(p * (1 - p), 1e-9)
                for a in range(len(free)):
                    grad[a] += wi * (yi - p) * x[a]
                    va = v * x[a]
                    for c in range(a, len(free)):
                        hess[a][c] += va * x[c]
            for a in range(len(free)):
                for c in range(a):
                    hess[a][c] = hess[c][a]
            grad[0] -= lam0 * (b[0] - target[0])
            hess[0][0] += lam0
            for a in range(1, len(free)):
                t = target[free[a]]
                grad[a] -= lam * (b[a] - t) / (t * t)
                hess[a][a] += lam / (t * t)
            step = solve(hess, grad)
            # a full Newton step can overshoot when lost and kept renewals separate cleanly (common with few
            # outcomes): halve it until the penalised likelihood does not get worse
            now = objective(b)
            for _ in range(30):
                nb = [bb + s for bb, s in zip(b, step)]
                if objective(nb) >= now - 1e-12:
                    break
                step = [s / 2 for s in step]
            else:
                nb, step = b, [0.0] * len(step)             # no shorter step improves it: stay, this is the optimum
            b = nb
            if max(abs(s) for s in step) < 1e-7:
                ok = True
                break
        converged = converged and ok
        neg = [(b[a], free[a]) for a in range(1, len(free)) if b[a] < 0]
        if not neg:
            beta = [0.0] * d
            for a, j in enumerate(free):
                beta[j] = b[a]
            return beta, converged, passes
        free.remove(min(neg)[1])


def _wmean(v, w):
    return sum(a * b for a, b in zip(v, w)) / sum(w)


def stage_for(events, others, cfg):
    """How much the data is allowed to change, from the number of distinct renewals lost and kept."""
    st = cfg['probability']['stages']
    n = min(events, others)
    return 'feature' if n >= st['feature'] else 'dimension' if n >= st['dimension'] else 'calibrated' if n >= st['calibrate'] else 'prior'


def train(rows, labels, groups, cfg, history=None, stage=None):
    """Learn from outcomes, as far as their number allows (probability.stages, counted in distinct renewals):
      prior       under `calibrate` renewals lost (or kept): the expert prior, untouched
      calibrated  the level and ONE slope on the scorecard's risk: the ranking is the scorecard's, the chance is the data's
      dimension   one weight per dimension
      feature     one weight per feature
    `groups` names the renewal each snapshot belongs to. Several snapshots of one renewal are one piece of evidence:
    the snapshots are weighted down so that together they count as many observations as there are renewals, and
    events are counted per renewal, never per snapshot. (Every snapshot gets the same weight, so the model answers
    "of customers who look like this and have not yet decided, how many are lost": a renewal lost early has fewer
    snapshots, as it should.)
    Only point-in-time features are learned; current-only ones keep their prior weight, rescaled by the learned slope.
    `history`: the stored snapshots of customers in their renewal window, to set the prior's level once (see prior)."""
    pr = prior(cfg, history)
    xs = [design(mask_pit(r, cfg), cfg) for r in rows]
    keep = [i for i, x in enumerate(xs) if x is not None]
    xs, y, g = [xs[i] for i in keep], [labels[i] for i in keep], [groups[i] for i in keep]
    size = {}
    for k in g:
        size[k] = size.get(k, 0) + 1
    wts = [len(size) / max(len(xs), 1)] * len(xs)     # all the snapshots together weigh as many observations as there are renewals
    events = len({k for k, yy in zip(g, y) if yy})
    others = len({k for k, yy in zip(g, y) if not yy})
    stage = stage or stage_for(events, others, cfg)
    count = dict(n=len(xs), renewals=len(size), events=events)
    if stage == 'prior':
        need = cfg['probability']['stages']['calibrate']
        pr.update(count, note=f'{events} renewals lost and {others} kept so far: under {need} of each, so the '
                              f'{pr["note"]} is used as it is')
        return pr
    pcfg = cfg['probability']
    w = weights(cfg)
    span = sum(pr['coef'].values())
    pit = feature_keys(cfg, pit_only=True)
    cur = [k for k in w if k not in pit]
    zero = [0.0] * len(xs)
    # 1. level and one slope on the scorecard's risk. The risk is centred, so the intercept is "the chance for the
    #    average customer", and the prior's value for it counts as prior_strength_renewals renewals of evidence.
    R = [sum(w[k] * x[k] for k in w) for x in xs]
    rbar = _wmean(R, wts)
    p0 = sigmoid(pr['intercept'] + span * rbar)
    (a1, b1), conv, passes = fit([[1.0, r - rbar] for r in R], y, wts, zero, [logit(p0), span], pcfg['ridge_lambda'],
                                 pcfg['max_iter'], lam0=pcfg['prior_strength_renewals'] * p0 * (1 - p0))
    coef = {k: b1 * w[k] for k in w}
    intercept = a1 - b1 * rbar
    if stage in ('dimension', 'feature') and b1 > 0:
        off = [sum(coef[k] * x[k] for k in cur) for x in xs]       # current-only features: not learned, held at b1 x weight
        if stage == 'dimension':
            dims = [d for d in cfg['dimensions'] if any(cfg['features'][k]['dimension'] == d for k in pit)]
            wd = {d: sum(w[k] for k in pit if cfg['features'][k]['dimension'] == d) for d in dims}
            Z = [[1.0] + [sum(w[k] * x[k] for k in pit if cfg['features'][k]['dimension'] == d) / wd[d] for d in dims] for x in xs]
            beta, conv, p2 = fit(Z, y, wts, off, [0.0] + [b1 * wd[d] for d in dims], pcfg['ridge_lambda'], pcfg['max_iter'])
            for d, bd in zip(dims, beta[1:]):
                for k in pit:
                    if cfg['features'][k]['dimension'] == d:
                        coef[k] = bd * w[k] / wd[d]
        else:
            Z = [[1.0] + [x[k] for k in pit] for x in xs]
            beta, conv, p2 = fit(Z, y, wts, off, [0.0] + [b1 * w[k] for k in pit], pcfg['ridge_lambda'], pcfg['max_iter'])
            coef.update(zip(pit, beta[1:]))
        intercept, passes = beta[0], passes + p2
    mean = {k: _wmean([x[k] for x in xs], wts) for k in w}
    what = {'calibrated': 'level and one slope', 'dimension': 'one weight per dimension', 'feature': 'one weight per feature'}[stage]
    return dict(count, kind='trained', stage=stage, intercept=intercept, coef=coef, mean=mean, converged=conv, passes=passes,
                slope=b1 / span,
                note=f'{events} renewals lost and {others} kept ({len(xs)} snapshots): {what} learned'
                     + ('' if conv else '; DID NOT CONVERGE'))


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
    """Precision and recall when the top `share` of scores is flagged. A cut that falls inside a tie counts the tied
    rows in proportion (so a yes/no score is never cut by row order)."""
    n = max(1, round(len(scores) * share))
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    edge = scores[order[n - 1]]
    above = [i for i in order if scores[i] > edge]
    tied = [i for i in order if scores[i] == edge]
    hit = sum(labels[i] for i in above) + (n - len(above)) * sum(labels[i] for i in tied) / len(tied)
    tot = sum(labels)
    return {'precision': hit / n, 'recall': hit / tot if tot else None, 'flagged': n}


def flag_stats(flags, labels):
    """A yes/no rule judged as what it is: the share it flags, and its own precision and recall."""
    n, hit, tot = sum(flags), sum(y for f, y in zip(flags, labels) if f), sum(labels)
    return {'flagged_share': n / len(flags) if flags else None, 'precision': hit / n if n else None,
            'recall': hit / tot if tot else None}


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
