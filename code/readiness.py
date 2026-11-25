#!/usr/bin/env python3
"""Is N360 ready to run the risk model? A read-only check of every source the dashboards and the risk model read.

    python3 code/readiness.py --user n360 --password "$N360_CH_PASSWORD"            # a ClickHouse server
    python3 code/readiness.py --chdb /tmp/n360                                     # an embedded database (tests)

It prints COUNTS, DATES AND SHARES ONLY: no customer name, amount, note or subject. It does print the labels the
company uses in Salesforce (opportunity types, forecast categories, task subtypes), because those decide the
settings in code/salesforce/salesforce.json and n360.settings. Read it before pasting it anywhere.

Each line is READY, PARTIAL, MISSING or INFO, then what to do. The list at the end is the order to fix things in.
Changes nothing. Exit code 0 always (it is a report, not a gate).
"""
import argparse, datetime as dt, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'risk'))

# where each risk feature comes from: what to do when it is missing
FEATURE_SOURCE = {
    'pace_gap_pts': 'contract lines with a daily series (company export: contract_line, line_day)',
    'forecast_end_pct': 'contract lines with a daily series (company export: contract_line, line_day)',
    'renewal_forecast_risk': 'an open Salesforce renewal opportunity linked to the account',
    'conv_change_30_pct': '8 weeks of conversations per VA (company export: va_day)',
    'conv_change_90_pct': '24 weeks of conversations per VA (company export: va_day)',
    'conv_volatility': '12 weeks of conversations per VA (company export: va_day)',
    'silent_va_days_30': 'conversations per VA (company export: va_day) and the anomaly view',
    'containment_pct': 'handoffs per VA (company export: va_day.handoffs)',
    'containment_change_pts': '60 days of handoffs per VA (company export: va_day.handoffs)',
    'drop_days_30': 'conversations per VA (company export: va_day) and the anomaly view',
    'legacy_va_share': 'weekly VA configuration reads (Phase 2: va_config_snapshot)',
    'features_used': 'playbook, authoring and assistant use (Phase 2: playbook_day, cortex_day, explorer_day, va_config_snapshot)',
    'build_change_pct': 'authoring and assistant use (Phase 2: cortex_day, explorer_day)',
    'weeks_config_unchanged': 'weekly VA configuration reads (Phase 2: va_config_snapshot)',
    'days_since_contact': 'Salesforce tasks and events on the account (sf_pull.py)',
    'days_since_exec': 'Salesforce events whose subject names an executive meeting (salesforce.json executive_keywords)',
    'open_tickets': 'support tickets (Phase 2: ps_ticket)',
    'oldest_open_ticket_days': 'support tickets (Phase 2: ps_ticket)',
    'csm_pulse': 'CSM ratings in the last 30 days (risk_register.py pulse)',
    'open_churn_risks': 'the risk register (risk_register.py add)',
}

out = []          # (status, area, text, todo)


def note(status, area, text, todo=''):
    out.append((status, area, text, todo))


def one(ch, sql, default=None):
    try:
        r = ch.rows(sql)
    except Exception as e:                                   # a missing table or view: report it, keep going
        note('MISSING', 'schema', f'query failed: {str(e).splitlines()[0][:120]}',
             'run load.py --schema-only, then this report again')
        return default
    return r[0] if r else default


def many(ch, sql):
    try:
        return ch.rows(sql)
    except Exception:
        return []


def n(x):
    return int(x or 0)


def pct(a, b):
    return f'{100 * a / b:.0f}%' if b else '-'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    a = ap.parse_args()
    from load import CH
    ch = CH(a)
    cfg = json.load(open(os.path.join(HERE, 'risk', 'risk_model.json')))
    sfc = json.load(open(os.path.join(HERE, 'salesforce', 'salesforce.json')))

    # ── 1. the company export ──
    runs = one(ch, "SELECT countIf(status = 'loaded') AS ok, countIf(status != 'loaded') AS bad, "
                   "toString(toDate(minIf(window_end, status = 'loaded'))) AS first, "
                   "toString(toDate(maxIf(window_end, status = 'loaded'))) AS last FROM n360.runs", {})
    if not n(runs.get('ok')):
        note('MISSING', 'export', 'no night loaded', 'load a night: docs/13_LAPTOP_POC.md section 4')
        return report()
    last = dt.date.fromisoformat(runs['last'])
    age = (dt.date.today() - last).days
    note('READY' if age <= 2 else 'PARTIAL', 'export',
         f"{runs['ok']} night{'s' if n(runs['ok']) != 1 else ''} loaded ({runs['first']} to {runs['last']}, {age} days ago); {runs['bad']} refused",
         '' if age <= 2 else 'the newest night is old: run nightly.sh (docs/13)')
    D = f"toDate('{runs['last']}')"
    accts = n(one(ch, 'SELECT count() AS n FROM n360.account', {}).get('n'))
    note('INFO', 'export', f'{accts} customers')

    cl = one(ch, f"""SELECT uniqExactIf(account, end_date >= {D} AND start_date <= {D}) AS running,
                            countIf(end_date >= {D} AND start_date <= {D}) AS lines,
                            countIf(end_date >= {D} AND start_date <= {D} AND ifNull(billed, 0) > 0) AS billed,
                            countIf(end_date < {D}) AS past_terms,
                            toString(min(start_date)) AS first FROM n360.contract_line""", {})
    r = n(cl.get('running'))
    note('READY' if r == accts else 'PARTIAL' if r else 'MISSING', 'contracts',
         f"{r} of {accts} customers have a running contract line ({pct(r, accts)}): {cl.get('lines')} lines, "
         f"{cl.get('billed')} with a billed volume; {cl.get('past_terms')} past terms kept (earliest start {cl.get('first')})",
         '' if r == accts else 'customers without a running line get no renewal date from N360: check their entitlement rows, '
                               'or rely on the Salesforce renewal (section 2)')
    if not n(cl.get('past_terms')):
        note('PARTIAL', 'contracts', 'no ended contract terms are kept',
             'the risk model learns from past renewals; if the export drops ended lines, past renewals are known '
             'only from Salesforce and the register')

    va = one(ch, f"""SELECT toString(min(day)) AS first, toString(max(day)) AS last, dateDiff('day', min(day), max(day)) + 1 AS days,
                            uniqExactIf(account, day > {D} - 30) AS recent,
                            uniqExactIf(account, day > {D} - 30 AND handoffs IS NOT NULL) AS handoffs
                     FROM n360.va_day""", {})
    days = n(va.get('days'))
    note('READY' if days >= 168 else 'PARTIAL' if days else 'MISSING', 'usage',
         f"conversations per VA from {va.get('first')} to {va.get('last')} ({days} days); "
         f"{va.get('recent')} of {accts} customers have conversations in the last 30 days",
         '' if days >= 168 else 'the 12-week volume change needs 168 days of history: load older nights, or wait')
    h = n(va.get('handoffs'))
    note('READY' if h >= 0.8 * n(va.get('recent')) else 'PARTIAL' if h else 'MISSING', 'usage',
         f'{h} customers have handoff counts in the last 30 days (containment)',
         '' if h else 'containment needs va_day.handoffs from the export')

    for t, col, what in (('va_config_snapshot', 'snapshot_date', 'VA configuration reads'), ('playbook_day', 'day', 'playbook runs'),
                         ('cortex_day', 'day', 'authoring sessions'), ('explorer_day', 'day', 'assistant questions'),
                         ('ps_ticket', 'opened', 'support tickets')):
        x = one(ch, f'SELECT count() AS n, uniqExact(account) AS a, toString(max({col})) AS last FROM n360.{t}', {})
        if n(x.get('n')):
            note('READY', 'phase 2', f"{what} ({t}): {x['n']} rows, {x['a']} customers, newest {x['last']}")
        else:
            note('MISSING', 'phase 2', f'{what} ({t}): empty',
                 'a Phase 2 source (proposed, docs/02): until the export has it, its features are "not reported" and '
                 'their weight moves to the rest of the dimension')

    # ── 2. Salesforce ──
    sr = one(ch, "SELECT countIf(status = 'loaded') AS ok, toString(maxIf(pulled_at, status = 'loaded')) AS last, "
                 "countIf(status != 'loaded') AS bad FROM n360.sf_runs", {})
    if not n(sr.get('ok')):
        note('MISSING', 'salesforce', 'never pulled: no renewal dates or amounts from Salesforce, no last contact, '
             'no executive meetings, no renewal forecast, and no won or lost renewals to learn from',
             'pull Salesforce: docs/18_SALESFORCE.md (sf_pull.py, then load_salesforce.py)')
    else:
        note('READY', 'salesforce', f"pulled {sr['ok']} times, last {sr['last']} UTC; {sr['bad']} pulls refused by the checks")
        lk = one(ch, """SELECT uniqExactIf(account, account != '') AS linked, countIf(account = '') AS unlinked,
                               count() AS sf FROM n360.v_sf_account""", {})
        li = n(lk.get('linked'))
        note('READY' if li >= 0.9 * accts else 'PARTIAL', 'salesforce',
             f"{li} of {accts} N360 customers are linked to a Salesforce account ({pct(li, accts)}); "
             f"{lk.get('unlinked')} of {lk.get('sf')} Salesforce accounts are not linked to any N360 customer",
             '' if li >= 0.9 * accts else 'see the names: SELECT * FROM n360.v_sf_unlinked; add the pairs to '
                                          'account_overrides in code/salesforce/salesforce.json, then pull again')
        types = many(ch, "SELECT ifNull(type, '(none)') AS t, count() AS n, countIf(is_renewal) AS r FROM n360.sf_opportunity "
                         "GROUP BY t ORDER BY n DESC LIMIT 12")
        ren = sum(n(t['r']) for t in types)
        note('READY' if ren else 'MISSING', 'salesforce',
             f'{ren} renewal opportunities. Opportunity types seen: ' + ', '.join(f"{t['t']} {t['n']}" for t in types),
             '' if ren else f"renewals are opportunities whose Type is one of {sfc['renewal_opportunity_types']}: none are. "
                            'Put the company\'s renewal type in renewal_opportunity_types (salesforce.json), or ask the admin '
                            'how renewals are marked (docs/18, question 1)')
        op = one(ch, """SELECT uniqExactIf(s.account, NOT o.is_closed) AS open_acc, countIf(NOT o.is_closed AND ifNull(o.amount, 0) > 0) AS open_amt,
                               countIf(NOT o.is_closed) AS open_n, countIf(o.is_closed AND o.is_won) AS won, countIf(o.is_closed AND NOT o.is_won) AS lost,
                               toString(minIf(o.close_date, o.is_closed)) AS first_closed
                        FROM n360.sf_opportunity AS o INNER JOIN n360.v_sf_account AS s ON s.sf_account_id = o.sf_account_id
                        WHERE o.is_renewal AND s.account != ''""", {})
        oa = n(op.get('open_acc'))
        note('READY' if oa >= 0.8 * accts else 'PARTIAL' if oa else 'MISSING', 'salesforce',
             f"{oa} of {accts} customers have an open renewal ({op.get('open_n')} open renewals, {op.get('open_amt')} with an amount)",
             '' if oa >= 0.8 * accts else 'customers with no open renewal get no renewal forecast and no revenue at risk')
        cl_ = n(op.get('won')) + n(op.get('lost'))
        note('READY' if cl_ else 'MISSING', 'outcomes',
             f"closed renewals in Salesforce: {op.get('won')} won, {op.get('lost')} lost (earliest {op.get('first_closed')})",
             '' if cl_ else 'the model learns from closed renewals: the pull reads opportunities closing in the last 730 days; '
                            'check renewals are closed won or lost in Salesforce')
        cats = many(ch, "SELECT ifNull(forecast_category, '(none)') AS c, count() AS n FROM n360.sf_opportunity "
                        "WHERE is_renewal AND NOT is_closed GROUP BY c ORDER BY n DESC")
        st = one(ch, 'SELECT risk_safe_forecast_categories AS safe, risk_lost_forecast_categories AS lost FROM n360.settings', {})
        known = set(st.get('safe') or []) | set(st.get('lost') or [])
        odd = [c['c'] for c in cats if c['c'] not in known]
        note('INFO', 'salesforce', 'forecast categories of open renewals: ' + (', '.join(f"{c['c']} {c['n']}" for c in cats) or 'none')
             + f" (safe: {st.get('safe')}, lost: {st.get('lost')}; anything else counts as open)",
             f'categories that are neither safe nor lost count as half a risk: {odd}. Change the lists in n360.settings '
             '(code/schema.sql) if that is wrong' if odd else '')
        ac = one(ch, f"""SELECT count() AS n, toString(min(a.activity_date)) AS first, countIf(a.is_contact) AS contact,
                                countIf(a.is_executive) AS exec, uniqExactIf(s.account, a.is_contact AND a.activity_date > {D} - 90) AS recent
                         FROM n360.sf_activity AS a INNER JOIN n360.v_sf_account AS s ON s.sf_account_id = a.sf_account_id
                         WHERE s.account != ''""", {})
        rc = n(ac.get('recent'))
        note('READY' if rc >= 0.5 * accts else 'PARTIAL' if n(ac.get('n')) else 'MISSING', 'salesforce',
             f"{ac.get('n')} activities on linked accounts since {ac.get('first')}: {ac.get('contact')} count as contact, "
             f"{ac.get('exec')} as executive meetings; {rc} of {accts} customers had contact in the last 90 days",
             '' if n(ac.get('contact')) else 'no activity counts as contact: check task subtypes below against '
                                             'contact.task_subtypes in salesforce.json, and whether CSMs log on the Account (docs/18, question 4)')
        if n(ac.get('contact')) and not n(ac.get('exec')):
            note('PARTIAL', 'salesforce', 'no activity counts as an executive meeting',
                 f"executive meetings are found by words in the subject {sfc['executive_keywords']}: add the words CSMs use")
        sub = many(ch, "SELECT kind, ifNull(subtype, '(none)') AS s, count() AS n, countIf(is_contact) AS c FROM n360.sf_activity "
                       "GROUP BY kind, s ORDER BY n DESC LIMIT 10")
        note('INFO', 'salesforce', 'activity subtypes: ' + ', '.join(f"{x['kind']} {x['s']} {x['n']} ({x['c']} contact)" for x in sub))

    # ── 3. what CSMs record ──
    pu = one(ch, f"SELECT uniqExact(account) AS a, uniqExactIf(account, toDate(at) > {D} - 30) AS recent FROM n360.risk_pulse", {})
    note('READY' if n(pu.get('recent')) >= 0.5 * accts else 'PARTIAL' if n(pu.get('a')) else 'MISSING', 'CSM input',
         f"CSM ratings: {pu.get('a')} customers ever rated, {pu.get('recent')} in the last 30 days (a rating counts for 30 days)",
         '' if n(pu.get('recent')) >= 0.5 * accts else 'ask CSMs to rate their customers: risk_register.py pulse (docs/17)')
    rg = one(ch, "SELECT count() AS n, countIf(status = 'open') AS open, countIf(status = 'closed' AND outcome IN ('churned', 'downsold')) AS lost, "
                 "countIf(status = 'closed' AND outcome IN ('churned', 'downsold') AND outcome_date IS NOT NULL) AS dated FROM n360.v_risk_register", {})
    note('READY' if n(rg.get('n')) else 'MISSING', 'CSM input',
         f"risk register: {rg.get('n')} risks, {rg.get('open')} open, {rg.get('lost')} closed as churned or downsold",
         '' if n(rg.get('n')) else 'until the first entry, "open churn risks" is not reported for anyone: start using '
                                   'risk_register.py add (docs/17)')
    ro = one(ch, 'SELECT count() AS n FROM n360.risk_outcome', {})
    note('INFO', 'outcomes', f"outcomes recorded by hand: {ro.get('n')}",
         'record churns and downsells that are in neither Salesforce nor the register: risk_model.py outcome (docs/21)')

    # ── 4. the risk model ──
    fs = one(ch, "SELECT uniqExact(as_of) AS dates, toString(min(as_of)) AS first, toString(max(as_of)) AS last, "
                 "uniqExactIf(as_of, source = 'nightly') AS nightly FROM n360.risk_features", {})
    if not n(fs.get('dates')):
        note('MISSING', 'risk model', 'no features stored', 'risk_model.py features --from <first night> --every 7 (docs/21 section 5)')
    else:
        note('READY', 'risk model', f"features stored for {fs['dates']} dates ({fs['first']} to {fs['last']}); {fs['nightly']} stored by the nightly run")
        cov = many(ch, f"""SELECT feature, countIf(value IS NOT NULL) AS have, count() AS n FROM n360.risk_features
                           WHERE as_of = (SELECT max(as_of) FROM n360.risk_features) GROUP BY feature""")
        cov = {c['feature']: (n(c['have']), n(c['n'])) for c in cov}
        dims = {}
        for k, f in cfg['features'].items():
            have, tot = cov.get(k, (0, 0))
            dims.setdefault(f['dimension'], []).append((k, have, tot))
        for d, fs_ in dims.items():
            w = cfg['dimensions'][d]['weight']
            parts = ', '.join(f'{k} {pct(h, t)}' for k, h, t in fs_)
            thin = [k for k, h, t in fs_ if not t or h < 0.5 * t]
            st_ = 'READY' if not thin else 'MISSING' if all(not h for _, h, _ in fs_) else 'PARTIAL'
            note(st_, 'risk model', f"{cfg['dimensions'][d]['title']} ({w:.0%} of health), share of customers reported: {parts}",
                 '; '.join(f'{k} (under half reported) needs {FEATURE_SOURCE[k]}' for k in thin))
        ren = cov.get('days_to_renewal', (0, 0))
        note('READY' if ren[0] == ren[1] and ren[1] else 'PARTIAL', 'risk model',
             f'{ren[0]} of {ren[1]} customers have a renewal date (largest running line, else the open Salesforce renewal)',
             '' if ren[0] == ren[1] else 'a customer with no renewal date is scored but never judged, and gets no revenue at risk')
        sc = one(ch, "SELECT count() AS n, countIf(band = 'at risk') AS risk, countIf(band = 'not enough data') AS thin, "
                     "countIf(band = 'not reported') AS none, round(avg(coverage), 2) AS cov FROM n360.v_risk_score", {})
        note('READY' if n(sc.get('n')) else 'MISSING', 'risk model',
             f"scored today: {sc.get('n')} customers, {sc.get('risk')} at risk, {sc.get('thin')} not enough data, "
             f"{sc.get('none')} nothing reported; average coverage {sc.get('cov')}",
             '' if n(sc.get('n')) else 'risk_model.py score (docs/21 section 5)')
        # how past renewals would be labelled: the same code train uses
        import backtest as bt
        import risk_model as cli
        rows, outs = cli.ch_features_load(ch, cfg), cli.ch_outcomes(ch)
        lc = bt.label_counts(rows, outs, cfg, last)
        src = many(ch, 'SELECT source, outcome, count() AS n FROM n360.v_risk_outcomes GROUP BY source, outcome ORDER BY source, outcome')
        note('INFO', 'outcomes', 'outcomes known: ' + (', '.join(f"{x['source']} {x['outcome']} {x['n']}" for x in src) or 'none'))
        stg = cfg['probability']['stages']
        lost, kept = lc['lost'], lc['recorded'] + lc['contract continues']
        note('READY' if min(lost, kept) >= stg['calibrate'] else 'PARTIAL' if lost + kept else 'MISSING', 'outcomes',
             f"past renewals in the stored features: {lost} lost, {lc['recorded']} kept on record, {lc['contract continues']} kept "
             f"(a contract ran on), {lc['unconfirmed']} unconfirmed. Learning starts at {stg['calibrate']} lost and {stg['calibrate']} kept",
             ('unconfirmed renewals are not used: record what happened (Salesforce, the register, or risk_model.py outcome). '
              if lc['unconfirmed'] else '')
             + ('' if lost + kept else
                f"no renewal has passed inside the stored features ({fs['first']} to {fs['last']}). Features can be rebuilt "
                f"from the usage history, which starts {va.get('first')}: features --from <a date 168 days after that> --every 7. "
                "A past renewal is only seen if the contract line that ended then is still in the export (contracts above)"))
        md = one(ch, 'SELECT toString(run_id) AS r, kind, note FROM n360.v_risk_model', None)
        note('READY' if md else 'PARTIAL', 'risk model',
             f"model in use: adopted run {md['r'][:8]}, {md['kind']} ({md['note']})" if md else
             'model in use: the expert prior at the level the settings give (no run adopted)',
             '' if md else 'run train, then adopt --run <id>: that fixes the level of the chance on your own customers (docs/21 section 7)')
    p = cfg['probability']
    note('INFO', 'settings', f"prior_mean_rate is {p['prior_mean_rate']:.0%} (PROPOSED). It should be renewals lost / renewals due "
         f"over the last two or three years", 'set it from the real record in code/risk/risk_model.json, then train and adopt')
    return report()


def report():
    order = {'MISSING': 0, 'PARTIAL': 1, 'READY': 2, 'INFO': 3}
    print('N360 readiness: counts, dates and shares only. Read before pasting anywhere.')
    areas = []
    for _, ar, _, _ in out:
        if ar not in areas:
            areas.append(ar)
    for ar in areas:
        print(f'\n{ar.upper()}')
        for st, a_, text, _ in out:
            if a_ == ar:
                print(f'  {st:8s} {text}')
    todo = sorted([(order[st], ar, td) for st, ar, _, td in out if td and st in ('MISSING', 'PARTIAL')], key=lambda x: x[0])
    info = [(ar, td) for st, ar, _, td in out if td and st == 'INFO']
    print('\nTO DO, in order')
    seen = set()
    i = 0
    for _, ar, td in todo + [(9, ar, td) for ar, td in info]:
        if td in seen:
            continue
        seen.add(td)
        i += 1
        print(f'  {i}. [{ar}] {td}')
    if not i:
        print('  nothing: every source is in')
    c = {s: sum(1 for x in out if x[0] == s) for s in order}
    print(f"\n{c['READY']} ready, {c['PARTIAL']} partial, {c['MISSING']} missing")
    return 0


if __name__ == '__main__':
    sys.exit(main())
