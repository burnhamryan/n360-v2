#!/usr/bin/env python3
"""Tests for the N360 risk model (code/risk/), no database needed.

    python3 code/risk/test_risk_model.py                # generates a synthetic history with a known risk process

1. Feature risks: anchors in both directions, clamping, "not reported" stays None.
2. Scorecard: points lost add up to 100 - health; missing features re-weight within their dimension and lower
   coverage; hard reds cap health and force the band; bands follow the settings.
3. Probability: the expert prior ranks drivers like the scorecard; too few outcomes returns the prior.
4. Labels: no label before the horizon is observed; only snapshots in the renewal window are labelled.
5. On a synthetic history whose truth is known (sample_data/generate_risk_panel.py), out of time:
   the scorecard and the models rank churn well (AUC); the trained model is better calibrated than the prior;
   a feature with no signal is shrunk below its prior weight; the strongest true signals keep positive weights;
   the warning comes well before the event.
6. The command line runs on files alone (train, score, explain, backtest).
"""
import datetime as dt, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import backtest as bt  # noqa: E402
import model as m  # noqa: E402

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


def main():
    # 1. feature risks
    check('risk: 0 at good, 1 at bad, linear between, clamped',
          m.feature_risk(0, 0, 40) == 0 and m.feature_risk(40, 0, 40) == 1 and m.feature_risk(20, 0, 40) == 0.5
          and m.feature_risk(99, 0, 40) == 1 and m.feature_risk(-5, 0, 40) == 0)
    check('risk: works when lower is worse (containment 60 good, 30 bad)',
          m.feature_risk(60, 60, 30) == 0 and m.feature_risk(45, 60, 30) == 0.5 and m.feature_risk(20, 60, 30) == 1)
    check('risk: not reported stays not reported', m.feature_risk(None, 0, 40) is None)

    # 2. scorecard
    sc = m.scorecard(calm(), CFG)
    check('scorecard: a customer at every good anchor scores 100, healthy', sc['health'] == 100 and sc['band'] == 'healthy', sc)
    bad = calm(conv_change_30_pct=-30, containment_pct=30)
    sc = m.scorecard(bad, CFG)
    check('scorecard: points lost add up to 100 - health', abs(sum(sc['contributions'].values()) - (100 - sc['health'])) < 0.2,
          (sum(sc['contributions'].values()), sc['health']))
    usage_w = CFG['dimensions']['usage']['weight'] * CFG['features']['conv_change_30_pct']['weight'] * 100
    check('scorecard: one feature at full risk costs its overall weight in points',
          abs(sc['contributions']['conv_change_30_pct'] - usage_w) < 0.05, sc['contributions']['conv_change_30_pct'])
    miss = calm(conv_change_30_pct=-30, conv_change_90_pct=None, conv_volatility=None, silent_va_days_30=None)
    s2 = m.scorecard(miss, CFG)
    check('scorecard: missing features re-weight inside their dimension (usage risk 1 when the only reading is 1)',
          s2['dimensions']['usage'] == 1.0 and s2['coverage'] < 1, (s2['dimensions']['usage'], s2['coverage']))
    red = calm(csm_pulse=1)
    s3 = m.scorecard(red, CFG)
    check('scorecard: a hard red caps health and forces "at risk"',
          s3['band'] == 'at risk' and s3['health'] <= CFG['hard_red']['cap_health'] and 'CSM rated red' in s3['hard_reds'], s3)
    s4 = m.scorecard(calm(forecast_end_pct=30, days_to_renewal=300), CFG)
    s5 = m.scorecard(calm(forecast_end_pct=30, days_to_renewal=60), CFG)
    check('scorecard: a renewal-window hard red applies only inside its window',
          not s4['hard_reds'] and s5['hard_reds'], (s4['hard_reds'], s5['hard_reds']))
    s6 = m.scorecard({'account': 'X', 'as_of': '2026-11-19'}, CFG)
    check('scorecard: nothing reported gives no health, never 100', s6['health'] is None and s6['band'] == 'not reported', s6)

    # 3. probability
    keys = m.feature_keys(CFG)
    pr = m.prior(CFG, keys)
    w = {k: CFG['dimensions'][CFG['features'][k]['dimension']]['weight'] * CFG['features'][k]['weight'] for k in keys}
    check('prior: ranks drivers exactly as the scorecard weights do',
          sorted(keys, key=lambda k: -pr['coef'][k]) == sorted(keys, key=lambda k: -w[k]))
    p_lo, p_hi = m.predict(pr, calm(), CFG), m.predict(pr, {k: (f['bad'] if k in CFG['features'] else v) for k, f in
                                                             {**CFG['features'], 'account': 0, 'as_of': 0}.items()
                                                             for v in [calm()[k]]}, CFG)
    check('prior: no risk anywhere = the base rate; full risk everywhere = the max rate',
          abs(p_lo - CFG['probability']['prior_base_rate']) < 1e-6 and abs(p_hi - CFG['probability']['prior_max_rate']) < 1e-6,
          (p_lo, p_hi))
    few = m.train([calm()] * 50, [1] * 3 + [0] * 47, CFG)
    check('train: under min_events_to_train outcomes returns the prior, and says so', few['kind'] == 'prior', few.get('note'))

    # 4. labels
    oba = {'A': [{'account': 'A', 'date': '2026-06-01', 'outcome': 'churned'}]}
    lab = lambda d, obs: bt.label({'account': 'A', 'as_of': d}, oba, 180, {'churned'}, dt.date.fromisoformat(obs))  # noqa: E731
    check('labels: churn inside the horizon = 1; outside = 0; horizon not observed yet = unlabelled',
          lab('2026-03-01', '2026-12-31') == 1 and lab('2025-10-01', '2026-12-31') == 0 and lab('2026-09-01', '2026-12-31') is None)
    check('labels: only snapshots in the renewal window are used',
          bt.eligible({'days_to_renewal': 90}, CFG) and not bt.eligible({'days_to_renewal': 300}, CFG)
          and bt.eligible({'days_to_renewal': None}, CFG))

    # 5. synthetic history with a known truth
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, os.path.join(ROOT, 'sample_data', 'generate_risk_panel.py'), '--out', tmp],
                       check=True, stdout=subprocess.DEVNULL)
        rows = [json.loads(l) for l in open(os.path.join(tmp, 'features.ndjson'))]
        outs = [json.loads(l) for l in open(os.path.join(tmp, 'outcomes.ndjson'))]
        res = bt.run(rows, outs, CFG)
        meth = res['methods']
        print('     backtest: ' + '; '.join(f"{k} AUC {v['auc']:.3f}" for k, v in meth.items()))
        check('backtest: scorecard and models rank out of time with AUC >= 0.72',
              all(meth[k]['auc'] >= 0.72 for k in ('scorecard (100 - health)', 'expert prior (probability)',
                                                   'trained model (probability)')), meth)
        check('backtest: the trained model is better calibrated than the prior (lower Brier score)',
              meth['trained model (probability)']['brier'] < meth['expert prior (probability)']['brier'],
              (meth['trained model (probability)']['brier'], meth['expert prior (probability)']['brier']))
        check('backtest: the top 10% flagged hold churn at well over twice the base rate',
              meth['trained model (probability)']['top_precision'] > 2 * res['base_rate'], (meth, res['base_rate']))
        check('backtest: most churns were flagged at risk beforehand, a month or more ahead',
              res['lead_time_days']['scorecard_at_risk']['warned'] > res['lead_time_days']['scorecard_at_risk']['events'] / 2
              and res['lead_time_days']['scorecard_at_risk']['median_days_ahead'] >= 30, res['lead_time_days'])
        observed = max(dt.date.fromisoformat(r['as_of']) for r in rows)
        lab = bt.thin(bt.labelled(rows, outs, CFG, observed), CFG['backtest']['train_sample_days'])
        mdl = m.train([r for r, _ in lab], [y for _, y in lab], CFG)
        pri = m.prior(CFG, list(mdl['coef']))
        check('train: a feature with no signal (legacy_va_share) is shrunk below its prior weight',
              abs(mdl['coef']['legacy_va_share']) < pri['coef']['legacy_va_share'],
              (mdl['coef']['legacy_va_share'], pri['coef']['legacy_va_share']))
        strong = ['conv_change_30_pct', 'days_since_contact', 'pace_gap_pts', 'containment_pct']
        check('train: the strongest true signals keep positive weights', all(mdl['coef'][k] > 0 for k in strong),
              {k: mdl['coef'][k] for k in strong})
        check('train: features that keep no history are never trained on',
              'renewal_forecast_risk' not in mdl['coef'] and 'open_tickets' not in mdl['coef'])
        check('train: deterministic (same data, same coefficients)',
              m.train([r for r, _ in lab], [y for _, y in lab], CFG)['coef'] == mdl['coef'])

        # 6. the command line on files alone
        R = [sys.executable, os.path.join(HERE, 'risk_model.py'), '--no-db']
        f, o = os.path.join(tmp, 'features.ndjson'), os.path.join(tmp, 'outcomes.ndjson')
        mo, so, rep = os.path.join(tmp, 'model.json'), os.path.join(tmp, 'scores.ndjson'), os.path.join(tmp, 'report.md')
        ok = all(subprocess.run(R + c, capture_output=True).returncode == 0 for c in (
            ['train', '--features', f, '--outcomes', o, '--model-out', mo],
            ['score', '--features', f, '--model', mo, '--out', so],
            ['explain', '--customer', 'Synthetic Customer 001', '--features', f, '--model', mo],
            ['backtest', '--features', f, '--outcomes', o, '--report', rep]))
        sc_rows = [json.loads(l) for l in open(so)] if os.path.exists(so) else []
        check('command line: train, score, explain and backtest run on files alone',
              ok and sc_rows and all(0 <= s['probability'] <= 1 for s in sc_rows) and os.path.getsize(rep) > 0)
    print('RISK MODEL TESTS PASSED' if not failed else f'{failed} RISK MODEL TESTS FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
