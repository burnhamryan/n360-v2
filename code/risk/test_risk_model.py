#!/usr/bin/env python3
"""Tests for the N360 risk model (code/risk/), no database needed.

    python3 code/risk/test_risk_model.py                # generates a synthetic history with a known risk process

1. Feature risks: anchors in both directions, clamping, "not reported" stays None.
2. Scorecard: points lost add up to 100 - health, with and without a hard red; missing features re-weight inside
   their dimension and lower coverage; too little reported is "not enough data"; hard reds and their windows.
3. Probability: the prior gives the average customer the portfolio rate and never reads "not reported" as "fine";
   hard reds set a floor; the explanation adds up.
4. Labels: one outcome per renewal; nothing taken after the outcome was recorded or after a churn; an unknown
   renewal date is never judged; every label becomes usable on the same day relative to its renewal.
5. Training: events are renewals, not snapshots; the stages; weights never go below zero; a wrong prior rate is
   corrected; convergence is reported.
6. Metrics: AUC with ties, a top share that falls inside a tie, a yes/no rule judged as a rule.
7. On a synthetic history whose truth is known (sample_data/generate_risk_panel.py), out of time: the scorecard ranks
   nearly as well as the hidden truth itself; the trained model is never clearly worse than the prior, at full size
   and at the size of the real customer base; the warning comes ahead of the loss.
8. Scoring: a churned customer is marked "churned", then dropped; the command line runs on files alone.
"""
import datetime as dt, json, os, statistics, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import backtest as bt  # noqa: E402
import model as m  # noqa: E402
import risk_model as cli  # noqa: E402

CFG = json.load(open(os.path.join(HERE, 'risk_model.json')))
failed = 0


def check(name, ok, got=''):
    global failed
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + ('' if ok else f'\n     got: {got}'))
    failed += 0 if ok else 1


def calm(**kw):
    """A healthy customer on every feature, overridden by kw."""
    r = {'account': 'X', 'as_of': '2026-11-19'}
    for k, f in CFG['features'].items():
        r[k] = f['good']
    r.update(kw)
    return r


def worst(**kw):
    r = {'account': 'X', 'as_of': '2026-11-19'}
    for k, f in CFG['features'].items():
        r[k] = f['bad']
    r.update(kw)
    return r


def panel(tmp, *args):
    subprocess.run([sys.executable, os.path.join(ROOT, 'sample_data', 'generate_risk_panel.py'), '--out', tmp, *args],
                   check=True, stdout=subprocess.DEVNULL)
    return ([json.loads(l) for l in open(os.path.join(tmp, 'features.ndjson'))],
            [json.loads(l) for l in open(os.path.join(tmp, 'outcomes.ndjson'))])


def main():
    # 1. feature risks
    check('risk: 0 at good, 1 at bad, linear between, clamped',
          m.feature_risk(0, 0, 40) == 0 and m.feature_risk(40, 0, 40) == 1 and m.feature_risk(20, 0, 40) == 0.5
          and m.feature_risk(99, 0, 40) == 1 and m.feature_risk(-5, 0, 40) == 0)
    check('risk: works when lower is worse (containment 60 good, 30 bad)',
          m.feature_risk(60, 60, 30) == 0 and m.feature_risk(45, 60, 30) == 0.5 and m.feature_risk(20, 60, 30) == 1)
    check('risk: not reported stays not reported', m.feature_risk(None, 0, 40) is None)
    for d in CFG['dimensions']:
        tot = sum(f['weight'] for f in CFG['features'].values() if f['dimension'] == d)
        if abs(tot - 1) > 1e-9:
            check(f'settings: feature weights inside "{d}" add up to 1', False, tot)
    check('settings: dimension weights add up to 1', abs(sum(d['weight'] for d in CFG['dimensions'].values()) - 1) < 1e-9)

    # 2. scorecard
    sc = m.scorecard(calm(), CFG)
    check('scorecard: a customer at every good anchor scores 100, healthy', sc['health'] == 100 and sc['band'] == 'healthy', sc)
    sc = m.scorecard(calm(conv_change_30_pct=-30, containment_pct=30), CFG)
    check('scorecard: points lost add up to 100 - health', abs(sum(sc['contributions'].values()) - (100 - sc['health'])) < 0.2,
          (sum(sc['contributions'].values()), sc['health']))
    usage_w = CFG['dimensions']['usage']['weight'] * CFG['features']['conv_change_30_pct']['weight'] * 100
    check('scorecard: one feature at full risk costs its overall weight in points',
          abs(sc['contributions']['conv_change_30_pct'] - usage_w) < 0.05, sc['contributions']['conv_change_30_pct'])
    s2 = m.scorecard(calm(conv_change_30_pct=-30, conv_change_90_pct=None, conv_volatility=None, silent_va_days_30=None), CFG)
    check('scorecard: missing features re-weight inside their dimension (usage risk 1 when the only reading is 1)',
          s2['dimensions']['usage'] == 1.0 and s2['coverage'] < 1, (s2['dimensions']['usage'], s2['coverage']))
    s3 = m.scorecard(calm(csm_pulse=1), CFG)
    check('scorecard: a hard red caps health and forces "at risk"',
          s3['band'] == 'at risk' and s3['health'] <= CFG['hard_red']['cap_health'] and 'CSM rated red' in s3['hard_reds'], s3)
    check('scorecard: points lost still add up to 100 - health under a hard red (the cap has its own line)',
          abs(sum(s3['contributions'].values()) - (100 - s3['health'])) < 0.2 and s3['contributions'].get(m.CAP, 0) > 50, s3['contributions'])
    s4 = m.scorecard(calm(forecast_end_pct=30, days_to_renewal=300), CFG)
    s5 = m.scorecard(calm(forecast_end_pct=30, days_to_renewal=60), CFG)
    s5b = m.scorecard(calm(forecast_end_pct=30, days_to_renewal=None), CFG)
    check('scorecard: a renewal-window hard red applies only inside its window',
          not s4['hard_reds'] and s5['hard_reds'], (s4['hard_reds'], s5['hard_reds']))
    check('scorecard: with no renewal date, a window hard red is listed as "could not be checked", not dropped silently',
          not s5b['hard_reds'] and s5b['hard_reds_unchecked'], s5b)
    s6 = m.scorecard({'account': 'X', 'as_of': '2026-11-19'}, CFG)
    check('scorecard: nothing reported gives no health, never 100', s6['health'] is None and s6['band'] == 'not reported', s6)
    thin_row = {'account': 'X', 'as_of': '2026-11-19', 'conv_change_30_pct': 5, 'containment_pct': 70}
    s7 = m.scorecard(thin_row, CFG)
    check('scorecard: two good readings and nothing else is "not enough data", not "healthy"',
          s7['band'] == 'not enough data' and s7['coverage'] < CFG['bands']['min_coverage'], s7)
    check('scorecard: a hard red still shows on a customer with little data',
          m.scorecard(dict(thin_row, csm_pulse=1), CFG)['band'] == 'at risk')

    # 3. probability
    pr = m.prior(CFG)
    w = m.weights(CFG)
    P = CFG['probability']
    check('prior: ranks drivers exactly as the scorecard weights do',
          sorted(w, key=lambda k: -pr['coef'][k]) == sorted(w, key=lambda k: -w[k]) and abs(sum(w.values()) - 1) < 1e-9)
    avg = {'account': 'X', 'as_of': '2026-11-19'}
    for k, f in CFG['features'].items():
        avg[k] = f['good'] + P['prior_mean_risk'] * (f['bad'] - f['good'])
    check('prior: a customer at the average risk gets the portfolio rate; full risk everywhere gets the max rate',
          abs(m.predict(pr, avg, CFG) - P['prior_mean_rate']) < 1e-6 and abs(m.predict(pr, worst(), CFG) - P['prior_max_rate']) < 1e-6,
          (m.predict(pr, avg, CFG), m.predict(pr, worst(), CFG)))
    bad_c = {k: (f['bad'] if f['dimension'] in ('commercial', 'usage') else f['good']) for k, f in CFG['features'].items()}
    only_c = {k: (v if CFG['features'][k]['dimension'] in ('commercial', 'usage') else None) for k, v in bad_c.items()}
    p_full, p_miss = m.predict(pr, bad_c, CFG), m.predict(pr, only_c, CFG)
    check('prior: "not reported" is never "fine" (bad readings and nothing else score higher than bad readings beside good ones)',
          p_miss > p_full + 0.2 and abs(p_miss - P['prior_max_rate']) < 1e-6, (p_full, p_miss))
    check('prior: nothing reported gives no probability, never the healthiest one',
          m.predict(pr, {'account': 'X', 'as_of': '2026-11-19'}, CFG) is None)
    book = [calm(), calm(conv_change_30_pct=-20), calm(containment_pct=35, days_since_contact=80), worst()]
    pr2 = m.prior(CFG, book)
    check('prior: its level is set so the customers in their renewal window average the portfolio rate',
          abs(sum(m.predict(pr2, r, CFG) for r in book) / len(book) - P['prior_mean_rate']) < 1e-4)
    floor = [r for r in CFG['hard_red']['rules'] if r['feature'] == 'csm_pulse'][0]['min_probability']
    check('probability: a hard red sets a floor under the chance shown',
          m.predict(pr, calm(csm_pulse=1), CFG) < floor and m.probability(pr, calm(csm_pulse=1), CFG) == floor)
    for name, row in (('every feature reported', calm(conv_change_30_pct=-25, days_since_contact=70, csm_pulse=0.5)),
                      ('half of them missing', dict(only_c, account='X', as_of='2026-11-19', pace_gap_pts=10))):
        push = m.explain(pr, row, CFG)
        base = pr['intercept'] + sum(pr['coef'][k] * pr['mean'][k] for k in pr['coef'])
        check(f'explain: the pushes add up to the log-odds against the average customer ({name})',
              abs(sum(push.values()) - (m.linear(pr, row, CFG) - base)) < 1e-3, (sum(push.values()), m.linear(pr, row, CFG) - base))

    # 4. labels: the unit is the renewal
    def snap(day, to_renewal, acct='A'):
        return calm(account=acct, as_of=day, days_to_renewal=to_renewal)
    obs = dt.date(2027, 6, 1)
    outs = [{'account': 'A', 'date': '2026-05-01', 'outcome': 'churned'},          # logged 61 days before the term ends
            {'account': 'A', 'date': '2026-05-06', 'outcome': 'churned'},          # the same churn, seen a second time
            {'account': 'B', 'date': '2026-07-02', 'outcome': 'renewed'}]
    rows = [snap('2026-02-01', 150), snap('2026-04-01', 91), snap('2026-05-15', 47), snap('2026-08-01', None),
            snap('2026-02-01', 150, 'B'), snap('2026-08-01', 335, 'B'), snap('2026-02-01', 150, 'C'), snap('2026-02-01', None, 'D')]
    lab = bt.labelled(rows, outs, CFG, obs)
    by = {(x['row']['account'], x['row']['as_of']): x for x in lab}
    check('labels: snapshots before a recorded loss are 1; the same loss recorded twice is one renewal',
          by[('A', '2026-02-01')]['y'] == 1 and by[('A', '2026-04-01')]['y'] == 1
          and len({x['group'] for x in lab if x['row']['account'] == 'A'}) == 1, by.keys())
    check('labels: snapshots taken after the loss was recorded, or after the churn, are never used',
          ('A', '2026-05-15') not in by and ('A', '2026-08-01') not in by, by.keys())
    check('labels: a renewal that closed won is 0; one with nothing recorded is 0 once the grace has passed',
          by[('B', '2026-02-01')]['y'] == 0 and by[('C', '2026-02-01')]['y'] == 0, by.keys())
    check('labels: a renewal far off, or with no known date, is not judged',
          ('B', '2026-08-01') not in by and ('D', '2026-02-01') not in by and not bt.eligible({'as_of': '2026-02-01', 'days_to_renewal': None}, CFG))
    early = bt.labelled(rows, outs, CFG, dt.date(2026, 7, 15))
    check('labels: no label, lost or kept, before the grace after its renewal date has passed',
          early == [] and all(x['known'] == bt._d(x['group'][1]) + dt.timedelta(CFG['label']['outcome_days_after']) for x in lab),
          [(x['row']['account'], x['y']) for x in early])

    # 5. training
    def history(n_lost, n_kept, snaps=6, flip=None):
        """n renewals, `snaps` snapshots each; lost ones run at high risk. flip = a feature that runs the WRONG way."""
        rows_, y, g = [], [], []
        for i in range(n_lost + n_kept):
            lost = i < n_lost
            for s in range(snaps):
                lvl = (0.75 if lost else 0.15) + 0.1 * ((i * 7 + s * 3) % 5 - 2) / 2
                r = {'account': f'c{i}', 'as_of': '2026-01-01'}
                for k, f in CFG['features'].items():
                    v = lvl + 0.15 * (((i + 1) * (len(k) + s)) % 7 - 3) / 3
                    if k == flip:
                        v = (0.1 if lost else 0.8) + 0.1 * ((i + s) % 3 - 1)
                    r[k] = f['good'] + min(1, max(0, v)) * (f['bad'] - f['good'])
                rows_.append(r); y.append(int(lost)); g.append(f'c{i}')
        return rows_, y, g
    few = m.train(*history(5, 40), CFG)
    check('train: 5 lost renewals seen in 30 snapshots is 5 events, not 30: the prior is used, and says so',
          few['kind'] == 'prior' and few['events'] == 5 and few['n'] == 270, few.get('note'))
    st = CFG['probability']['stages']
    check('train: the stage follows the number of distinct renewals lost and kept',
          [m.stage_for(e, o, CFG) for e, o in ((st['calibrate'] - 1, 500), (st['calibrate'], 500), (st['dimension'], st['dimension']),
                                               (st['feature'], 500), (500, st['calibrate'] - 1))]
          == ['prior', 'calibrated', 'dimension', 'feature', 'prior'])
    cal = m.train(*history(15, 60), CFG)
    ratio = {round(cal['coef'][k] / pr['coef'][k], 6) for k in pr['coef']}
    check('train: at "calibrated" only the level and one slope move, so customers rank exactly as the scorecard ranks them',
          cal['stage'] == 'calibrated' and len(ratio) == 1 and cal['converged'], (cal['stage'], ratio))
    loose = json.loads(json.dumps(CFG)); loose['probability']['ridge_lambda'] = 0.01     # let the data speak, to test the floor
    wrong = m.train(*history(120, 150, flip='days_since_contact'), loose)
    check('train: at "feature" a signal that runs the wrong way is held at zero, never below (and clean separation still converges)',
          wrong['stage'] == 'feature' and wrong['coef']['days_since_contact'] == 0 and min(wrong['coef'].values()) >= 0
          and wrong['converged'], (wrong['stage'], wrong['coef']['days_since_contact']))
    check('train: features that keep no history are not learned: they keep their prior weight, rescaled by the learned slope',
          all(abs(wrong['coef'][k] - wrong['slope'] * pr['coef'][k]) < 1e-9 for k in CFG['features'] if not CFG['features'][k]['pit']))
    try:
        m.solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]); raised = False
    except ArithmeticError:
        raised = True
    check('train: a singular system raises instead of returning a wrong answer', raised)

    # 6. metrics
    check('metrics: AUC shares ties', m.auc([0, 0, 1, 1], [0, 1, 0, 1]) == 0.5 and m.auc([1, 2, 2, 3], [0, 0, 1, 1]) == 0.875)
    ts = m.top_share([1, 1, 1, 1, 0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 0, 0, 0, 0], 0.2)
    check('metrics: a top share that falls inside a tie counts the tied rows in proportion, not by row order',
          abs(ts['precision'] - 0.25) < 1e-9, ts)
    fs = m.flag_stats([1, 1, 0, 0], [1, 0, 1, 0])
    check('metrics: a yes/no rule is judged as a rule (share flagged, its own precision and recall)',
          fs == {'flagged_share': 0.5, 'precision': 0.5, 'recall': 0.5}, fs)

    # 7. synthetic history with a known truth
    with tempfile.TemporaryDirectory() as tmp:
        rows, outs = panel(tmp)
        res = bt.run(rows, outs, CFG)
        meth = res['methods']
        SC, PR, TM = 'scorecard (100 - health)', 'expert prior (probability)', 'trained model (probability)'
        print(f"     backtest: {res['renewals']} renewals, {res['events']} lost; " + '; '.join(f"{k} AUC {v['auc']:.3f}" for k, v in meth.items()))
        observed = max(dt.date.fromisoformat(r['as_of']) for r in rows)
        lab = bt.labelled(rows, outs, CFG, observed)
        judged = bt.at_leads([dict(x, health=m.scorecard(m.mask_pit(x['row'], CFG), CFG)['health']) for x in lab],
                             CFG['backtest']['lead_days'], CFG['backtest']['lead_tolerance_days'])
        ys = [x['y'] for x in judged]
        oracle, card = m.auc([x['row']['_h'] for x in judged], ys), m.auc([100 - x['health'] for x in judged], ys)
        print(f'     the hidden truth itself ranks at AUC {oracle:.3f}; the scorecard at {card:.3f} on the same judgements')
        check('backtest: the scorecard ranks within 0.04 of the best possible ranking (the hidden health itself)',
              card >= oracle - 0.04, (card, oracle))
        check('backtest: every AUC comes with an interval from resampling customers',
              all(meth[k]['auc_low'] < meth[k]['auc'] < meth[k]['auc_high'] for k in (SC, PR, TM)), meth[SC])
        d = res['trained_minus_prior_auc']
        check('backtest: the trained model is never clearly worse than the prior (AUC interval reaches -0.02 at worst; Brier within 0.003)',
              d[0] > -0.02 and meth[TM]['brier'] < meth[PR]['brier'] + 0.003, (d, meth[TM]['brier'], meth[PR]['brier']))
        check('backtest: the model expects about as many losses as happened (within 20%)',
              abs(meth[TM]['expected_events'] / meth[TM]['observed_events'] - 1) < 0.2,
              (meth[TM]['expected_events'], meth[TM]['observed_events']))
        check('backtest: the top 10% flagged hold losses at well over twice the base rate',
              meth[TM]['top_precision'] > 2 * meth[TM]['observed_events'] / res['judged'], meth[TM])
        check('backtest: the rules are reported as a flag, with the share flagged',
              0 < meth['rules (at-risk band)']['flagged_share'] < 0.5 and 'top_precision' not in meth['rules (at-risk band)'])
        lt = res['lead_time_days']['scorecard_at_risk']
        check('backtest: most losses were flagged at risk beforehand, a month or more ahead when flagged',
              lt['warned'] > lt['events'] / 2 and lt['median_days_ahead'] >= 30 and lt['held'] <= lt['warned'], lt)
        stages = [r['stage'] for r in res['retrainings']]
        check('backtest: the model learns in order (prior, then calibrated, then by dimension), on renewals, not snapshots',
              stages[0] == 'prior' and stages == sorted(stages, key=['prior', 'calibrated', 'dimension', 'feature'].index)
              and res['retrainings'][-1]['events'] <= len(outs), stages)
        # a prior set at the wrong rate is what calibration is for
        low = json.loads(json.dumps(CFG)); low['probability']['prior_mean_rate'] = 0.04; low['backtest']['bootstrap'] = 0
        r2 = bt.run(rows, outs, low)['methods']
        check('backtest: when the prior rate is set far too low, learning from outcomes corrects it (much lower Brier)',
              r2[TM]['brier'] < r2[PR]['brier'] - 0.002 and r2[TM]['expected_events'] > 1.5 * r2[PR]['expected_events'],
              (r2[TM]['brier'], r2[PR]['brier']))
        full = bt.fit(lab, CFG, stage='feature')
        check('train: on the full history, per-feature weights stay at or above zero and converge',
              min(full['coef'].values()) >= 0 and full['converged'], min(full['coef'].values()))
        check('train: a feature with no signal (legacy_va_share) is pulled below the common slope',
              full['coef']['legacy_va_share'] / pr['coef']['legacy_va_share'] < full['slope'],
              (full['coef']['legacy_va_share'] / pr['coef']['legacy_va_share'], full['slope']))
        check('train: deterministic (same data, same coefficients)', bt.fit(lab, CFG, stage='feature')['coef'] == full['coef'])

        # the size of the real customer base: 40 customers, three years
        diffs = []
        small = json.loads(json.dumps(CFG)); small['backtest']['bootstrap'] = 0
        for seed in range(6):
            with tempfile.TemporaryDirectory() as t2:
                r_, o_ = panel(t2, '--customers', '40', '--weeks', '156', '--seed', str(seed))
                x = bt.run(r_, o_, small).get('methods')
                if x and x[TM]['auc'] is not None:
                    diffs.append(x[TM]['auc'] - x[PR]['auc'])
        check('backtest: at 40 customers the trained model ranks about as well as the prior (median AUC change above -0.02)',
              len(diffs) >= 4 and statistics.median(diffs) > -0.02, diffs)

        # 8. scoring and the command line on files alone
        gone = bt.churn_dates(outs)
        acct, day = sorted(gone.items(), key=lambda kv: kv[1])[0]
        mine = [r for r in rows if r['account'] == acct]
        soon = [r for r in mine if 0 <= (bt._d(r['as_of']) - day).days <= 60]
        s_soon, _ = cli.score_rows(soon[:1], None, CFG, outs)
        late = dict(soon[0], as_of=(day + dt.timedelta(CFG['label']['churned_keep_days'] + 30)).isoformat())
        s_late, _ = cli.score_rows([late], None, CFG, outs)
        check('score: a churned customer is marked "churned" with no chance and no revenue at risk, then drops out after the keep period',
              s_soon and s_soon[0]['band'] == 'churned' and s_soon[0]['probability'] is None and s_soon[0]['revenue_at_risk'] is None
              and not s_soon[0]['eligible'] and s_late == [], (s_soon, s_late))
        R = [sys.executable, os.path.join(HERE, 'risk_model.py'), '--no-db']
        f, o = os.path.join(tmp, 'features.ndjson'), os.path.join(tmp, 'outcomes.ndjson')
        mo, so, rep = os.path.join(tmp, 'model.json'), os.path.join(tmp, 'scores.ndjson'), os.path.join(tmp, 'report.md')
        ok = all(subprocess.run(R + c, capture_output=True).returncode == 0 for c in (
            ['train', '--features', f, '--outcomes', o, '--model-out', mo],
            ['score', '--features', f, '--outcomes', o, '--model', mo, '--out', so],
            ['explain', '--customer', 'Synthetic Customer 001', '--features', f, '--model', mo],
            ['backtest', '--features', f, '--outcomes', o, '--report', rep]))
        sc_rows = [json.loads(l) for l in open(so)] if os.path.exists(so) else []
        check('command line: train, score, explain and backtest run on files alone',
              ok and sc_rows and all(s['probability'] is None or 0 <= s['probability'] <= 1 for s in sc_rows) and os.path.getsize(rep) > 0)
    print('RISK MODEL TESTS PASSED' if not failed else f'{failed} RISK MODEL TESTS FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
