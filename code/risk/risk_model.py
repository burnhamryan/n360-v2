#!/usr/bin/env python3
"""The N360 customer risk model: build features, train, score, backtest, explain (docs/21_RISK_MODEL.md).
Runs on its own (files in, files out) or against N360's ClickHouse, where Grafana reads its results.

With N360 (connection as load.py: --url/--user/--password, or --chdb PATH; the loader user, as it writes):
    python3 code/risk/risk_model.py features                       # today's features into n360.risk_features
    python3 code/risk/risk_model.py features --from 2026-01-01 --every 7   # backfill weekly, point-in-time
    python3 code/risk/risk_model.py train                          # train on stored features + v_risk_outcomes; backtest
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


def score_rows(rows, model, cfg):
    """Scores and contributions for feature rows, with a model (or the prior when model is None)."""
    if model is None:
        model = dict(m.prior(cfg, m.feature_keys(cfg)), kind='prior')
    scores, contribs = [], []
    for r in rows:
        sc = m.scorecard(r, cfg)
        p = m.predict(model, r, cfg)
        push = m.explain(model, r, cfg)
        elig = bt.eligible(r, cfg)
        amount = r.get('renewal_amount') or None
        scores.append({'account': r['account'], 'as_of': r['as_of'], 'health': sc['health'], 'band': sc['band'],
                       'probability': round(p, 4), 'eligible': elig, 'coverage': sc['coverage'],
                       'hard_reds': sc['hard_reds'], 'days_to_renewal': r.get('days_to_renewal'),
                       'renewal_amount': amount, 'revenue_at_risk': round(p * amount, 2) if elig and amount else None,
                       'model_kind': model.get('kind', 'prior')})
        rk = m.risks(r, cfg)
        for k, f in cfg['features'].items():
            contribs.append({'account': r['account'], 'as_of': r['as_of'], 'feature': k, 'dimension': f['dimension'],
                             'value': r.get(k), 'risk': None if rk[k] is None else round(rk[k], 4),
                             'points': sc['contributions'].get(k, 0.0), 'logodds': push.get(k, 0.0)})
    return scores, contribs


def report_md(res, cfg, title='Risk model backtest'):
    out = [f'# {title}', '', f"Model settings version `{cfg['version']}`; horizon {cfg['horizon_days']} days; "
           f"events: {', '.join(cfg['label_events'])}.", '']
    if 'methods' not in res:
        return '\n'.join(out + [res.get('note', 'No result.'), ''])
    out += [f"Scored out of time: {res['snapshots_scored']} snapshots ({res['period'][0]} to {res['period'][1]}), "
            f"{res['events']} with a churn or downsell within the horizon (base rate {res['base_rate']:.1%}).", '',
            f"| Method | AUC | Precision, top {cfg['backtest']['top_share']:.0%} | Recall, top {cfg['backtest']['top_share']:.0%} | Brier |",
            '|---|---|---|---|---|']
    for name, x in res['methods'].items():
        f = lambda v, p=3: '-' if v is None else f'{v:.{p}f}'  # noqa: E731
        out.append(f"| {name} | {f(x['auc'])} | {f(x['top_precision'])} | {f(x['top_recall'])} | {f(x.get('brier'))} |")
    out += ['', '## Calibration of the trained model', '', '| Predicted | Snapshots | Mean predicted | Observed |', '|---|---|---|---|']
    for b in res['reliability']:
        out.append(f"| {b['bin']} | {b['n']} | {b['predicted']:.3f} | {b['observed']:.3f} |")
    lt = res['lead_time_days']
    out += ['', '## Warning lead time', '', '| Warning | Events | Warned beforehand | Median days ahead |', '|---|---|---|---|']
    for k, v in lt.items():
        out.append(f"| {k.replace('_', ' ')} | {v['events']} | {v['warned']} | {v['median_days_ahead']} |")
    return '\n'.join(out) + '\n'


# ───────────────────────── N360 (ClickHouse) ─────────────────────────


def ch_features_store(ch, rows, cfg):
    keys = list(cfg['features']) + cfg['context']
    dates = sorted({r['as_of'] for r in rows})
    for d in dates:
        ch.cmd(f"ALTER TABLE n360.risk_features DELETE WHERE as_of = '{d}' SETTINGS mutations_sync = 1")
    long = [{'as_of': r['as_of'], 'account': r['account'], 'feature': k, 'value': r.get(k)} for r in rows for k in keys]
    insert(ch, 'n360.risk_features', long)


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
    r = ch.rows("SELECT toString(run_id) AS run_id, model, kind FROM n360.v_risk_model")
    if not r:
        return None, None
    mdl = json.loads(r[0]['model'])
    mdl['kind'] = r[0]['kind']
    return r[0]['run_id'], mdl


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
    p = sub.add_parser('train', help='train on stored features and outcomes, then backtest')
    p.add_argument('--features'); p.add_argument('--outcomes'); p.add_argument('--model-out'); p.add_argument('--report')
    p = sub.add_parser('score', help="score the newest features (or a file's)")
    p.add_argument('--features'); p.add_argument('--model'); p.add_argument('--out')
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
        ch_features_store(ch, rows, cfg)
        print(f'features: {len(rows)} customer-days over {len(ds)} date(s) ({ds[0]} to {ds[-1]}) stored in n360.risk_features')
        if a.cmd == 'features':
            return 0
    if a.cmd in ('score', 'nightly'):
        d = newest if a.cmd == 'nightly' or a.as_of == 'latest' else a.as_of
        days = ([x['d'] for x in ch.rows('SELECT DISTINCT toString(as_of) AS d FROM n360.risk_features ORDER BY d')]
                if d == 'all' else [d])
        mid, mdl = ch_model(ch)
        for day in days:
            rows = ch_features_load(ch, cfg, day)
            if not rows:
                print(f'No stored features for {day}: run "features" first.')
                return 1
            scores, contribs = score_rows(rows, mdl, cfg)
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
            lab = bt.thin(bt.labelled(rows, outs, cfg, observed), cfg['backtest']['train_sample_days'])
            mdl = m.train([r for r, _ in lab], [y for _, y in lab], cfg, pit_only=True)
            run = str(uuid.uuid4())
            body = {k: mdl[k] for k in ('intercept', 'coef', 'fill')}
            ch.cmd(f"""INSERT INTO n360.risk_model_runs (run_id, version, kind, n, events, note, model, backtest) VALUES
                       ('{run}', {esc(cfg['version'])}, {esc(mdl['kind'])}, {mdl.get('n', 0)}, {mdl.get('events', 0)},
                        {esc(mdl.get('note', ''))}, {esc(json.dumps(body))}, {esc(json.dumps(res, default=str))})""")
            print(f"model {run}: {mdl['kind']} ({mdl.get('note', '')})")
        print(report_md(res, cfg).split('## Calibration')[0])
        if a.report:
            open(a.report, 'w').write(report_md(res, cfg))
        return 0
    if a.cmd == 'explain':
        rows = [r for r in ch_features_load(ch, cfg, newest) if r['account'].lower() == a.customer.lower()]
        if not rows:
            print(f'No features for {a.customer!r} on {newest}.')
            return 1
        _, mdl = ch_model(ch)
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
    mdl = mdl or dict(m.prior(cfg, m.feature_keys(cfg)), kind='prior')
    sc = m.scorecard(row, cfg)
    p = m.predict(mdl, row, cfg)
    rk = m.risks(row, cfg)
    push = m.explain(mdl, row, cfg)
    lines = [f"{row['account']} on {row['as_of']}: health {sc['health']} ({sc['band']}), "
             f"P(churn or downsell at the next renewal) {p:.0%} [{mdl.get('kind', 'prior')}], coverage {sc['coverage']:.0%}"]
    if sc['hard_reds']:
        lines.append('Hard reds: ' + '; '.join(sc['hard_reds']))
    lines.append(f"{'feature':26s} {'value':>10s} {'risk':>6s} {'points':>7s} {'log-odds':>9s}")
    for k in sorted(cfg['features'], key=lambda k: -sc['contributions'].get(k, 0)):
        v = row.get(k)
        lines.append(f"{k:26s} {('-' if v is None else f'{v:.1f}'):>10s} {('-' if rk[k] is None else f'{rk[k]:.2f}'):>6s} "
                     f"{sc['contributions'].get(k, 0):7.1f} {push.get(k, 0):9.3f}")
    return '\n'.join(lines)


def files_mode(a, cfg):
    if a.cmd == 'train':
        rows, outs = read_ndjson(a.features), read_ndjson(a.outcomes)
        observed = max(dt.date.fromisoformat(r['as_of'][:10]) for r in rows)
        lab = bt.thin(bt.labelled(rows, outs, cfg, observed), cfg['backtest']['train_sample_days'])
        mdl = m.train([r for r, _ in lab], [y for _, y in lab], cfg, pit_only=True)
        if a.model_out:
            json.dump(mdl, open(a.model_out, 'w'), indent=1)
        print(f"model: {mdl['kind']} ({mdl.get('note', '')})")
        if a.report:
            open(a.report, 'w').write(report_md(bt.run(rows, outs, cfg, observed), cfg))
        return 0
    if a.cmd == 'backtest':
        rows, outs = read_ndjson(a.features), read_ndjson(a.outcomes)
        res = bt.run(rows, outs, cfg)
        text = report_md(res, cfg)
        if a.report:
            open(a.report, 'w').write(text)
        print(text.split('## Calibration')[0])
        return 0
    if a.cmd == 'score':
        rows = read_ndjson(a.features)
        if a.as_of != 'latest':
            rows = [r for r in rows if r['as_of'] == a.as_of]
        else:
            newest = max(r['as_of'] for r in rows)
            rows = [r for r in rows if r['as_of'] == newest]
        mdl = json.load(open(a.model)) if a.model else None
        scores, contribs = score_rows(rows, mdl, cfg)
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
