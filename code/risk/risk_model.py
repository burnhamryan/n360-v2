#!/usr/bin/env python3
"""The N360 customer risk model: build features, train, score, backtest, explain (docs/21_RISK_MODEL.md).
Runs on its own (files in, files out) or against N360's ClickHouse, where Grafana reads its results.

With N360 (connection as load.py: --url/--user/--password, or --chdb PATH; the loader user, as it writes):
    python3 code/risk/risk_model.py features                       # today's features into n360.risk_features
    python3 code/risk/risk_model.py features --from 2026-01-01 --every 7   # backfill weekly, point-in-time
    python3 code/risk/risk_model.py train                          # train on stored features + v_risk_outcomes; backtest; store
    python3 code/risk/risk_model.py adopt --run RUN_ID             # put that run into use (nothing is used until adopted)
    python3 code/risk/risk_model.py score                          # score today's features; Grafana shows them
    python3 code/risk/risk_model.py nightly                        # features + score (nightly.sh runs this)
    python3 code/risk/risk_model.py explain --customer "Willow Energy"
    python3 code/risk/risk_model.py outcome --customer "Lakeside Insurance" --date 2026-10-31 --outcome churned

On its own, no database (feature and outcome rows as NDJSON, e.g. from sample_data/generate_risk_panel.py):
    python3 code/risk/risk_model.py --no-db train    --features f.ndjson --outcomes o.ndjson --model-out model.json
    python3 code/risk/risk_model.py --no-db score    --features f.ndjson --model model.json --out scores.ndjson
    python3 code/risk/risk_model.py --no-db backtest --features f.ndjson --outcomes o.ndjson --report report.md

Settings: code/risk/risk_model.json (--config to use another).
"""
import argparse, datetime as dt, json, os, sys, tempfile, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import backtest as bt  # noqa: E402
import model as m  # noqa: E402


def esc(v):
    return 'NULL' if v is None else "'" + str(v).replace('\\', '\\\\').replace("'", "\\'") + "'"


def read_ndjson(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def write_ndjson(path, rows):
    with open(path, 'w') as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')


# ───────────────────────── scoring (shared by both modes) ─────────────────────────


def score_rows(rows, model, cfg, outcomes=()):
    """Scores and contributions for feature rows, with a model (or, when model is None, the expert prior at the level
    the settings give: a customer's chance never depends on who else is renewing). A customer known to have churned keeps a
    row for label.churned_keep_days with the band 'churned' and no probability, then drops out."""
    gone = bt.churn_dates(outcomes)
    keep = dt.timedelta(cfg['label']['churned_keep_days'])

    def churned(r):
        return r['account'] in gone and bt._d(r['as_of']) >= gone[r['account']]
    rows = [r for r in rows if not (churned(r) and bt._d(r['as_of']) - gone[r['account']] > keep)]
    if model is None:
        model = m.prior(cfg)
    scores, contribs = [], []
    for r in rows:
        sc = m.scorecard(r, cfg)
        lost = churned(r)
        p = None if lost else m.probability(model, r, cfg)
        push = m.explain(model, r, cfg)
        elig = bt.eligible(r, cfg) and not lost
        amount = r.get('renewal_amount') or None
        scores.append({'account': r['account'], 'as_of': r['as_of'], 'health': sc['health'],
                       'band': 'churned' if lost else sc['band'],
                       'probability': None if p is None else round(p, 4), 'eligible': elig, 'coverage': sc['coverage'],
                       'hard_reds': sc['hard_reds'], 'days_to_renewal': r.get('days_to_renewal'),
                       'renewal_amount': amount,
                       'revenue_at_risk': round(p * amount, 2) if elig and amount and p is not None else None,
                       'model_kind': model.get('stage', model.get('kind', 'prior'))})
        rk = m.risks(r, cfg)
        for k, f in cfg['features'].items():
            contribs.append({'account': r['account'], 'as_of': r['as_of'], 'feature': k, 'dimension': f['dimension'],
                             'value': r.get(k), 'risk': None if rk[k] is None else round(rk[k], 4),
                             'points': sc['contributions'].get(k, 0.0), 'logodds': push.get(k, 0.0)})
        if m.CAP in sc['contributions']:
            contribs.append({'account': r['account'], 'as_of': r['as_of'], 'feature': m.CAP, 'dimension': 'hard red',
                             'value': None, 'risk': None, 'points': sc['contributions'][m.CAP], 'logodds': 0.0})
    return scores, contribs


def report_md(res, cfg, title='Risk model backtest'):
    out = [f'# {title}', '', f"Model settings version `{cfg['version']}`; renewals judged from {cfg['eligible_days_to_renewal']} "
           f"days out; events: {', '.join(cfg['label_events'])}.", '']
    if 'methods' not in res:
        return '\n'.join(out + [res.get('note', 'No result.'), ''] + ([labels_text(res['labels']), ''] if 'labels' in res else []))
    f = lambda v, p=3: '-' if v is None else f'{v:.{p}f}'  # noqa: E731
    top = f"{cfg['backtest']['top_share']:.0%}"
    out += [f"Scored out of time: {res['renewals']} renewals, {res['events']} of them lost (base rate {res['base_rate']:.1%}), "
            f"from {res['snapshots_scored']} snapshots ({res['period'][0]} to {res['period'][1]}). Each renewal is judged "
            f"{', '.join(str(x) for x in res['lead_days'])} days ahead ({res['judged']} judgements). Intervals are 95%, from "
            'resampling whole customers.', '', labels_text(res['labels']), '',
            f'| Method | AUC | 95% interval | Precision, top {top} | Recall, top {top} | Brier | Losses expected / seen |',
            '|---|---|---|---|---|---|---|']
    for name, x in res['methods'].items():
        if name.startswith('rules'):
            continue
        exp = f"{x['expected_events']:.0f} / {x['observed_events']}" if 'brier' in x else '-'
        out.append(f"| {name} | {f(x['auc'])} | {f(x['auc_low'])} to {f(x['auc_high'])} | {f(x['top_precision'])} | "
                   f"{f(x['top_recall'])} | {f(x.get('brier'))} | {exp} |")
    d = res['trained_minus_prior_auc']
    out += ['', f"Trained model minus expert prior, AUC: {f(d[0])} to {f(d[1])} (95% interval). The trained model is worth "
            'adopting only when this interval sits above zero and its Brier score is lower.']
    if bt.BLIND in res['methods']:
        a, b = res['methods']['scorecard (100 - health)'], res['methods'][bt.BLIND]
        out += ['', f"**Without the CSM's own view** (rating and register blanked) the scorecard ranks at AUC {f(b['auc'])}, against "
                f"{f(a['auc'])} with it. A CSM often rates a customer red AFTER it has said it is leaving. If the figure "
                "with the CSM's view is much the higher of the two, part of that is hindsight, not warning."]
    x = res['methods']['rules (at-risk band)']
    out += ['', f"**The rules alone** (a hard red or health under {cfg['bands']['watch']}) flag {f(x['flagged_share'], 3)} of judgements, "
            f"with precision {f(x['precision'])} and recall {f(x['recall'])} (AUC {f(x['auc'])})."]
    if 'trained_periods' in res:
        t = res['trained_periods']
        out += ['', f"**Only the periods where the model had learned something** ({t['judged']} judgements, {t['events']} lost): "
                f"AUC {f(t['auc_model'])} against the prior's {f(t['auc_prior'])}; Brier {f(t['brier_model'])} against {f(t['brier_prior'])}."]
    st = {}
    for r in res['retrainings']:
        st[r['stage']] = st.get(r['stage'], 0) + 1
    out += ['', 'Retrainings by stage: ' + ', '.join(f'{k} {v}' for k, v in st.items())
            + f". The last one had {res['retrainings'][-1]['events']} renewals lost to learn from."]
    out += ['', '## By lead time', '', '| Days before the renewal | Judgements | AUC, scorecard | AUC, trained model |', '|---|---|---|---|']
    for L, x in res['by_lead'].items():
        out.append(f"| {L} | {x['judged']} | {f(x['auc_scorecard'])} | {f(x['auc_model'])} |")
    for key, name in (('reliability', 'trained model'), ('reliability_prior', 'expert prior')):
        out += ['', f'## Calibration of the {name}', '', '| Predicted | Judgements | Mean predicted | Observed |', '|---|---|---|---|']
        for b in res[key]:
            out.append(f"| {b['bin']} | {b['n']} | {b['predicted']:.3f} | {b['observed']:.3f} |")
    lt = res['lead_time_days']
    out += ['', '## Warning lead time', '',
            f"Model alert = chance at or above {cfg['backtest']['alert_probability']:.0%}. Held = still flagged on the last "
            f"snapshot {cfg['backtest']['hold_days']} or more days before the loss was recorded.", '',
            '| Warning | Renewals lost | Warned beforehand | Median days ahead, when warned | Held | Flagged, renewals not lost |',
            '|---|---|---|---|---|---|']
    for k, v in lt.items():
        fa = '-' if v['false_alarm_share'] is None else f"{v['false_alarm_share']:.1%}"
        out.append(f"| {k.replace('_', ' ')} | {v['events']} | {v['warned']} | {v['median_days_ahead']} | {v['held']} | {fa} |")
    return '\n'.join(out) + '\n'


# ───────────────────────── N360 (ClickHouse) ─────────────────────────


def ch_features_store(ch, rows, cfg, source, overwrite=False):
    """Store feature rows. A row written by the nightly run is the only true record of what was known that night
    (current-only features, that night's contract terms, unsettled days), so a rebuild never replaces it unless
    `overwrite`. Returns the dates skipped."""
    keys = list(cfg['features']) + cfg['context']
    dates = sorted({r['as_of'] for r in rows})
    kept = set()
    if source == 'rebuilt' and not overwrite:
        kept = {x['d'] for x in ch.rows("SELECT DISTINCT toString(as_of) AS d FROM n360.risk_features WHERE source = 'nightly'")}
    for d in dates:
        if d not in kept:
            ch.cmd(f"ALTER TABLE n360.risk_features DELETE WHERE as_of = '{d}' SETTINGS mutations_sync = 1")
    long = [{'as_of': r['as_of'], 'account': r['account'], 'feature': k, 'value': r.get(k), 'source': source}
            for r in rows if r['as_of'] not in kept for k in keys]
    insert(ch, 'n360.risk_features', long)
    return sorted(kept & set(dates))


def insert(ch, table, rows):
    if not rows:
        return
    with tempfile.NamedTemporaryFile('w', suffix='.ndjson', delete=False) as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')
    try:
        ch.insert_ndjson(table, f.name)
    finally:
        os.unlink(f.name)


def ch_features_load(ch, cfg, as_of=None):
    """Stored feature rows, as dicts (all dates, or one)."""
    where = f"WHERE as_of = '{as_of}'" if as_of else ''
    out = {}
    for x in ch.rows(f"SELECT toString(as_of) AS as_of, account, feature, value FROM n360.risk_features {where}"):
        r = out.setdefault((x['account'], x['as_of']), {'account': x['account'], 'as_of': x['as_of']})
        r[x['feature']] = None if x['value'] is None else float(x['value'])
    return list(out.values())


def ch_outcomes(ch):
    return [{'account': x['account'], 'date': x['date'], 'outcome': x['outcome']}
            for x in ch.rows("SELECT account, toString(date) AS date, outcome FROM n360.v_risk_outcomes")]


def ch_model(ch):
    """The model in use: the newest ADOPTED run (None, None when none has been adopted: the built-in expert prior)."""
    r = ch.rows("SELECT toString(run_id) AS run_id, model, kind FROM n360.v_risk_model")
    if not r:
        return None, None
    mdl = json.loads(r[0]['model'])
    mdl['kind'] = r[0]['kind']
    return r[0]['run_id'], mdl


def labels_text(c):
    """How past renewals were labelled, in words (bt.label_counts)."""
    t = (f"Past renewals: {c['lost']} lost, {c['recorded']} kept on record, {c['contract continues']} kept with no record "
         f"(a contract ran on after the date), {c['unconfirmed']} unconfirmed.")
    if c['unconfirmed']:
        t += (' Unconfirmed renewals have no outcome recorded and no contract seen afterwards: they are NOT used. Record '
              'them (Salesforce, the register, or risk_model.py outcome) so the model can learn from them.')
    return t


def check_prior(rows, cfg, outs):
    """What the prior's level was set on, and whether prior_mean_risk still describes the customers."""
    w = bt.window_history(rows, outs, cfg)
    r = m.mean_risk(w, cfg)
    if r is None:
        return 'No customer has been in a renewal window yet: the prior keeps the level the settings give.'
    need = cfg['probability'].get('prior_level_min_snapshots', 0)
    t = f'Customers in their renewal window: {len(w)} snapshots, average scorecard risk {r:.2f} (prior_mean_risk is {cfg["probability"]["prior_mean_risk"]}).'
    if len(w) < need:
        t += f' Under {need} snapshots: the prior keeps the level the settings give.'
    else:
        t += (f" The prior's level is set on them, once, so their average chance is prior_mean_rate "
              f"({cfg['probability']['prior_mean_rate']:.0%}); it stays as it is until you train and adopt again.")
    return t


def verdict(mdl, res):
    """Whether a trained model has earned its place: the governance bar in docs/21, section 7."""
    if mdl['kind'] != 'trained':
        return ('This is the expert prior: nothing was learned from outcomes. Adopting it fixes its level '
                '(see the line on customers in their renewal window below).')
    if 'methods' not in res:
        return 'DO NOT ADOPT YET: there is no backtest to judge it by.'
    d, M = res.get('trained_minus_prior_auc') or [None, None], res['methods']
    better_brier = M['trained model (probability)']['brier'] < M['expert prior (probability)']['brier']
    if mdl['stage'] == 'calibrated':
        return ('Stage "calibrated": the ranking is the scorecard\'s, only the level and slope moved. '
                + ('Its Brier score beats the prior: reasonable to adopt.' if better_brier
                   else 'DO NOT ADOPT: its Brier score is no better than the prior.'))
    if d[0] is not None and d[0] > 0 and better_brier:
        return 'It beats the prior on AUC (the whole 95% interval is above zero) and on Brier: reasonable to adopt.'
    return 'DO NOT ADOPT: it does not clearly beat the expert prior on both AUC and Brier.'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--config', default=os.path.join(HERE, 'risk_model.json'))
    ap.add_argument('--no-db', action='store_true', help='files in, files out; no ClickHouse')
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('features', help='build features (point-in-time) and store them')
    p.add_argument('--as-of', default='latest')
    p.add_argument('--from', dest='frm')
    p.add_argument('--to')
    p.add_argument('--every', type=int, default=7)
    p.add_argument('--overwrite', action='store_true', help='let a rebuild replace rows the nightly run stored')
    p = sub.add_parser('train', help='train on stored features and outcomes, backtest, store the run (not in use until adopted)')
    p.add_argument('--features'); p.add_argument('--outcomes'); p.add_argument('--model-out'); p.add_argument('--report')
    p.add_argument('--adopt', action='store_true', help='put the new run into use at once (CI; otherwise read the backtest first)')
    p = sub.add_parser('adopt', help='put a stored model run into use')
    p.add_argument('--run', required=True, help="a run id from 'train', or 'prior' to go back to the built-in expert prior")
    p.add_argument('--by', default=os.environ.get('USER', 'unknown')); p.add_argument('--note', default='')
    p = sub.add_parser('score', help="score the newest features (or a file's)")
    p.add_argument('--features'); p.add_argument('--model'); p.add_argument('--out'); p.add_argument('--outcomes')
    p.add_argument('--as-of', default='latest', help="a date, 'latest', or 'all' (every stored date: the score history)")
    p = sub.add_parser('backtest', help='walk-forward backtest; no model is stored')
    p.add_argument('--features'); p.add_argument('--outcomes'); p.add_argument('--report')
    p = sub.add_parser('nightly', help='features for the newest day, then score (nightly.sh)')
    p = sub.add_parser('explain', help="one customer's score, feature by feature")
    p.add_argument('--customer', required=True); p.add_argument('--features'); p.add_argument('--model')
    p = sub.add_parser('outcome', help='record an outcome by hand (churned, downsold, renewed, expanded)')
    p.add_argument('--customer', required=True); p.add_argument('--date', required=True, type=dt.date.fromisoformat)
    p.add_argument('--outcome', required=True, choices=['churned', 'downsold', 'renewed', 'expanded'])
    p.add_argument('--note', default=''); p.add_argument('--by', default=os.environ.get('USER', 'unknown'))
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    if a.no_db:
        return files_mode(a, cfg)

    from load import CH
    import features as fe
    ch = CH(a)
    newest = fe.latest_day(ch)
    if not newest:
        print('No loaded night in n360.runs: load data first.')
        return 1

    if a.cmd in ('features', 'nightly'):
        ds = [newest if getattr(a, 'as_of', 'latest') == 'latest' else a.as_of]
        if getattr(a, 'frm', None):
            ds = fe.dates(a.frm, a.to or newest, a.every)
        rows = [r for d in ds for r in fe.build(ch, d, newest)]
        rebuilt = bool(getattr(a, 'frm', None)) or ds != [newest]
        skipped = ch_features_store(ch, rows, cfg, 'rebuilt' if rebuilt else 'nightly', getattr(a, 'overwrite', False))
        print(f'features: {len(rows)} customer-days over {len(ds)} date(s) ({ds[0]} to {ds[-1]}) for n360.risk_features'
              + (f'; {len(skipped)} date(s) kept as the nightly run stored them (--overwrite to replace)' if skipped else ''))
        if a.cmd == 'features':
            return 0
    if a.cmd in ('score', 'nightly'):
        d = newest if a.cmd == 'nightly' or a.as_of == 'latest' else a.as_of
        days = ([x['d'] for x in ch.rows('SELECT DISTINCT toString(as_of) AS d FROM n360.risk_features ORDER BY d')]
                if d == 'all' else [d])
        mid, mdl = ch_model(ch)
        outs = ch_outcomes(ch)
        for day in days:
            rows = ch_features_load(ch, cfg, day)
            if not rows:
                print(f'No stored features for {day}: run "features" first.')
                return 1
            scores, contribs = score_rows(rows, mdl, cfg, outs)
            run = str(uuid.uuid4())
            insert(ch, 'n360.risk_scores', [{**s, 'run_id': run, 'model_run_id': mid} for s in scores])
            insert(ch, 'n360.risk_contributions', [{**c, 'run_id': run} for c in contribs])
            n_risk = sum(1 for s in scores if s['band'] == 'at risk')
            print(f"scored {len(scores)} customers on {day} with the {'trained model' if mdl and mdl['kind'] == 'trained' else 'expert prior'}: "
                  f"{n_risk} at risk (run {run})")
        return 0
    if a.cmd in ('train', 'backtest'):
        rows, outs = ch_features_load(ch, cfg), ch_outcomes(ch)
        observed = dt.date.fromisoformat(newest)
        res = bt.run(rows, outs, cfg, observed)
        if a.cmd == 'train':
            mdl = bt.fit(bt.labelled(rows, outs, cfg, observed), cfg, bt.window_history(rows, outs, cfg))
            run = str(uuid.uuid4())
            body = {k: mdl[k] for k in ('intercept', 'coef', 'mean', 'stage')}
            ch.cmd(f"""INSERT INTO n360.risk_model_runs (run_id, version, kind, n, events, note, model, backtest) VALUES
                       ('{run}', {esc(cfg['version'])}, {esc(mdl['kind'])}, {mdl.get('n', 0)}, {mdl.get('events', 0)},
                        {esc(mdl.get('note', ''))}, {esc(json.dumps(body))}, {esc(json.dumps(res, default=str))})""")
            print(f"model {run}: {mdl['kind']} ({mdl.get('note', '')})")
            print(verdict(mdl, res))
            print(check_prior(rows, cfg, outs))
            if a.adopt:
                ch.cmd(f"INSERT INTO n360.risk_model_adopted (run_id, by, note) VALUES ('{run}', 'train --adopt', '')")
                print(f'adopted: run {run} is now in use')
            else:
                print(f'Stored, NOT in use. To use it: risk_model.py adopt --run {run}')
        print(report_md(res, cfg).split('## By lead time')[0])
        if a.report:
            open(a.report, 'w').write(report_md(res, cfg))
        return 0
    if a.cmd == 'adopt':
        if a.run == 'prior':
            run = '00000000-0000-0000-0000-000000000000'        # no run: the built-in expert prior
        else:
            got = ch.rows(f"SELECT toString(run_id) AS r FROM n360.risk_model_runs WHERE toString(run_id) = {esc(a.run)}")
            if not got:
                print(f'No model run {a.run!r} in n360.risk_model_runs.')
                return 1
            run = got[0]['r']
        ch.cmd(f"INSERT INTO n360.risk_model_adopted (run_id, by, note) VALUES ('{run}', {esc(a.by)}, {esc(a.note)})")
        print('adopted: the built-in expert prior is now in use' if a.run == 'prior' else f'adopted: run {run} is now in use')
        return 0
    if a.cmd == 'explain':
        rows = [r for r in ch_features_load(ch, cfg, newest) if r['account'].lower() == a.customer.lower()]
        if not rows:
            print(f'No features for {a.customer!r} on {newest}.')
            return 1
        _, mdl = ch_model(ch)
        mdl = mdl or m.prior(cfg)
        print(explain_text(rows[0], mdl, cfg))
        return 0
    if a.cmd == 'outcome':
        acct = ch.rows(f"SELECT account FROM n360.account WHERE lower(account) = lower({esc(a.customer)}) "
                       f"OR lower(display_name) = lower({esc(a.customer)})")
        if not acct:
            print(f'No customer called {a.customer!r}.')
            return 1
        ch.cmd(f"INSERT INTO n360.risk_outcome (account, date, outcome, note, by) VALUES ({esc(acct[0]['account'])}, "
               f"toDate({esc(a.date.isoformat())}), {esc(a.outcome)}, {esc(a.note)}, {esc(a.by)})")
        print(f"Recorded: {acct[0]['account']} {a.outcome} on {a.date}")
        return 0
    return 0


def explain_text(row, mdl, cfg):
    mdl = mdl or m.prior(cfg)
    sc = m.scorecard(row, cfg)
    p = m.probability(mdl, row, cfg)
    rk = m.risks(row, cfg)
    push = m.explain(mdl, row, cfg)
    chance = 'no chance shown (nothing reported)' if p is None else f'P(churn or downsell at the next renewal) {p:.0%}'
    lines = [f"{row['account']} on {row['as_of']}: health {sc['health']} ({sc['band']}), "
             f"{chance} [{mdl.get('stage', mdl.get('kind', 'prior'))}], coverage {sc['coverage']:.0%}"]
    if sc['hard_reds']:
        lines.append('Hard reds: ' + '; '.join(sc['hard_reds']))
    if sc['hard_reds_unchecked']:
        lines.append('Could not be checked (renewal date unknown): ' + '; '.join(sc['hard_reds_unchecked']))
    lines.append(f"{'feature':26s} {'value':>10s} {'risk':>6s} {'points':>7s} {'log-odds':>9s}")
    for k in sorted(cfg['features'], key=lambda k: -sc['contributions'].get(k, 0)):
        v = row.get(k)
        lines.append(f"{k:26s} {('-' if v is None else f'{v:.1f}'):>10s} {('-' if rk[k] is None else f'{rk[k]:.2f}'):>6s} "
                     f"{sc['contributions'].get(k, 0):7.1f} {push.get(k, 0):9.3f}")
    if m.CAP in sc['contributions']:
        lines.append(f"{'hard red (cap at ' + str(cfg['hard_red']['cap_health']) + ')':26s} {'-':>10s} {'-':>6s} {sc['contributions'][m.CAP]:7.1f} {0:9.3f}")
    return '\n'.join(lines)


def files_mode(a, cfg):
    if a.cmd == 'train':
        rows, outs = read_ndjson(a.features), read_ndjson(a.outcomes)
        observed = max(dt.date.fromisoformat(r['as_of'][:10]) for r in rows)
        mdl = bt.fit(bt.labelled(rows, outs, cfg, observed), cfg, bt.window_history(rows, outs, cfg))
        if a.model_out:
            json.dump(mdl, open(a.model_out, 'w'), indent=1)
        print(f"model: {mdl['kind']} ({mdl.get('note', '')})")
        print(labels_text(bt.label_counts(rows, outs, cfg, observed)))
        print(check_prior(rows, cfg, outs))
        if a.report:
            open(a.report, 'w').write(report_md(bt.run(rows, outs, cfg, observed), cfg))
        return 0
    if a.cmd == 'backtest':
        rows, outs = read_ndjson(a.features), read_ndjson(a.outcomes)
        res = bt.run(rows, outs, cfg)
        text = report_md(res, cfg)
        if a.report:
            open(a.report, 'w').write(text)
        print(text.split('## By lead time')[0])
        return 0
    if a.cmd == 'score':
        rows = read_ndjson(a.features)
        if a.as_of != 'latest':
            rows = [r for r in rows if r['as_of'] == a.as_of]
        else:
            newest = max(r['as_of'] for r in rows)
            rows = [r for r in rows if r['as_of'] == newest]
        mdl = json.load(open(a.model)) if a.model else None
        scores, contribs = score_rows(rows, mdl, cfg, read_ndjson(a.outcomes) if a.outcomes else ())
        if a.out:
            write_ndjson(a.out, scores)
            write_ndjson(a.out.replace('.ndjson', '') + '.contributions.ndjson', contribs)
        print(f'scored {len(scores)} customers; {sum(1 for s in scores if s["band"] == "at risk")} at risk')
        return 0
    if a.cmd == 'explain':
        rows = [r for r in read_ndjson(a.features) if r['account'].lower() == a.customer.lower()]
        if not rows:
            print(f'No features for {a.customer!r}.')
            return 1
        row = max(rows, key=lambda r: r['as_of'])
        print(explain_text(row, json.load(open(a.model)) if a.model else None, cfg))
        return 0
    print(f'"{a.cmd}" needs the database (drop --no-db).')
    return 2


if __name__ == '__main__':
    sys.exit(main())
