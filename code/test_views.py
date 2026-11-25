#!/usr/bin/env python3
"""Backlog B5 acceptance: the dashboard views return the synthetic edge cases correctly.
SYNTHETIC ONLY: the account names below are the generator's invented customers. Run after loading
sample_data/generate_synthetic.py output (ci.sh does, on the last of 14 nights).

    python3 code/test_views.py --chdb /tmp/n360
    python3 code/test_views.py --url http://localhost:8123 --user grafana --password ...
"""
import argparse, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import CH  # noqa: E402

failed = 0


def check(name, ok, got):
    global failed
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + ('' if ok else f'\n     got: {got}'))
    failed += 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    ch = CH(ap.parse_args())
    q = ch.rows

    rows = q('SELECT account, name, line_key, pool_id, entitlement, pace, pace_reason, '
             'toFloat64(pct_billed) AS pct_billed, days_to_cross FROM n360.v_line_pace')
    # keyed by line or pool (one customer can have two lines on one channel, e.g. Juniper Bank Chat)
    pace = {(r['account'], r['name'], r['line_key'], r['pool_id']): r for r in rows}
    one = {}
    for r in rows:
        one.setdefault((r['account'], r['name']), []).append(r)
    one = {k: v[0] for k, v in one.items() if len(v) == 1}   # names that identify a single entitlement

    # v_line_pace
    n_expected = q('SELECT (SELECT count() FROM n360.contract_line WHERE pool_id IS NULL) + (SELECT count() FROM n360.pool) AS n')[0]['n']
    check('v_line_pace: every unpooled line and every pool exactly once',
          len(rows) == len(pace) == int(n_expected), (len(rows), len(pace), n_expected))
    r = one.get(('Atlas Streaming', 'Chat'), {})
    check('Atlas Streaming Chat is red (above billed)', r.get('pace') == 'red' and r.get('pct_billed', 0) > 100, r)
    r = one.get(('Harbor Telecom', 'Chat'), {})
    check('Harbor Telecom Chat is red: crosses billed within 30 days, while under 100%',
          r.get('pace') == 'red' and r.get('pct_billed', 999) <= 100 and r.get('days_to_cross') is not None
          and int(r['days_to_cross']) <= 30, r)
    r = one.get(('Kestrel Travel', 'Chat'), {})
    check('Kestrel Travel Chat is amber: more than 10 points ahead of term, under billed, not crossing within 30 days',
          r.get('pace') == 'amber' and r.get('pct_billed', 999) <= 100 and r.get('days_to_cross') is not None
          and int(r['days_to_cross']) > 30 and 'ahead of term' in r.get('pace_reason', ''), r)
    bb = [v for v in rows if v['account'] == 'Bluebird Air']
    check('Bluebird Air pool measured once: one pool row, no pooled line rows',
          len(bb) == 1 and bb[0]['entitlement'] == 'pool', bb)
    check('Bluebird Air pool is red: above billed (about 122%)', bb and bb[0]['pace'] == 'red' and bb[0]['pct_billed'] > 100, bb)
    check('Harbor Telecom Voice: not measurable, no colour',
          one.get(('Harbor Telecom', 'Voice'), {}).get('pace') == 'not measurable', one.get(('Harbor Telecom', 'Voice')))
    check('Pinecrest Retail Voice: not implemented, no colour',
          one.get(('Pinecrest Retail', 'Voice'), {}).get('pace') == 'not implemented', one.get(('Pinecrest Retail', 'Voice')))
    bad = [(v['account'], v['name']) for v in rows if v['pct_billed'] is None and v['pace'] in ('red', 'amber', 'green')]
    check('nothing unread gets a colour', not bad, bad)

    # v_anomaly_events
    ev = q('SELECT toString(day) AS day, direction, accounts, account_list FROM n360.v_anomaly_events')
    check('one cross-account event: the spike on 3 accounts',
          len(ev) == 1 and ev[0]['direction'] == 'spikes' and int(ev[0]['accounts']) == 3
          and sorted(ev[0]['account_list']) == ['Bluebird Air', 'Harbor Telecom', 'Northwind Outfitters'], ev)
    prov = q('SELECT count() AS n FROM n360.v_va_day_anomaly WHERE provisional AND kind != \'normal\' '
             'AND day IN (SELECT day FROM n360.v_anomaly_events)')[0]['n']
    check('events use settled days only', int(prov) == 0, prov)

    # v_customer_attention
    att = {r['account']: r for r in q(
        'SELECT account, pace, worst_entitlement, entitlements, drops_silences_7d, provisional_flags FROM n360.v_customer_attention')}
    n_acc = q('SELECT count() AS n FROM n360.account')[0]['n']
    check('v_customer_attention: one row per customer', len(att) == int(n_acc), (len(att), n_acc))
    r = att.get('Lakeside Insurance', {})
    check('Lakeside Insurance: silent (settled) and provisional days counted apart',
          int(r.get('drops_silences_7d') or 0) > 0 and int(r.get('provisional_flags') or 0) > 0, r)
    r = att.get('Bluebird Air', {})
    check('Bluebird Air worst entitlement is the pool', r.get('worst_entitlement') == 'pool' and r.get('pace') == 'red', r)
    r = att.get('Harbor Telecom', {})
    check('Harbor Telecom worst is red (crossing), not the not-measurable Voice line', r.get('pace') == 'red', r)
    none = [a for a, v in att.items() if int(v['entitlements']) == 0]
    check('customers with no entitlement have no pace (not read), not a colour',
          all(att[a]['pace'] is None for a in none), {a: att[a]['pace'] for a in none})

    # Forecast history (B15) and accuracy (B16)
    nights = int(q("SELECT countIf(status = 'loaded') AS n FROM n360.runs")[0]['n'])
    first = q("SELECT toString(min(data_date)) AS d FROM n360.runs")[0]['d']
    per = q(f"SELECT series_key, count() AS n FROM n360.forecast_history WHERE data_date >= '{first}' GROUP BY series_key")
    n_fc = int(q('SELECT count() AS n FROM n360.forecast')[0]['n'])
    check('B15: one forecast_history row per line or pool per loaded night',
          len(per) == n_fc and all(int(r['n']) == nights for r in per), (nights, per))
    acc = q("SELECT mape, forecasts FROM n360.v_forecast_accuracy WHERE series_key = 'Harbor Telecom|0' AND horizon_days = 30")
    if acc:   # hand check: recompute MAPE at 30 days for Harbor Chat in Python from the stored rows
        rows = q("SELECT toString(data_date) AS d, consumed, rate_per_day FROM n360.forecast_history "
                 "WHERE series_key = 'Harbor Telecom|0'")
        import datetime as _d
        by = {_d.date.fromisoformat(r['d']): r for r in rows}
        apes = [abs(float(by[m + _d.timedelta(30)]['consumed']) - (float(r['consumed']) + float(r['rate_per_day']) * 30))
                / float(by[m + _d.timedelta(30)]['consumed'])
                for m, r in by.items() if m + _d.timedelta(30) in by and float(by[m + _d.timedelta(30)]['consumed']) > 0]
        hand = round(100 * sum(apes) / len(apes), 1)
        check('B16: Harbor Chat MAPE at 30 days matches a hand calculation',
              abs(hand - float(acc[0]['mape'])) < 0.05 and len(apes) == int(acc[0]['forecasts']), (hand, acc))
    else:
        print('SKIP B16 hand check: fewer than 30 nights of stored forecasts')
    ch_rows = q("SELECT count() AS n FROM n360.v_forecast_change WHERE series_key = 'Harbor Telecom|0' AND newly_crossing_soon")
    check('B16: Harbor Chat flagged once as newly crossing billed soon', int(ch_rows[0]['n']) == 1 or not acc, ch_rows)

    # Anomaly review log (B19): ci.sh records 3 outcomes (one flag reviewed twice) before this runs
    if int(q('SELECT count() AS n FROM n360.anomaly_log')[0]['n']):
        rev = {(r['va_name'], r['d']): r['outcome'] for r in q(
            "SELECT va_name, toString(day) AS d, toString(outcome) AS outcome FROM n360.v_anomaly_review WHERE outcome IS NOT NULL")}
        check('B19: outcomes join back to their flags; the newest review wins',
              rev == {('Lakeside Email', '2026-11-11'): 'real', ('Lakeside Email', '2026-11-12'): 'data issue',
                      ('Bluebird Web', '2026-11-13'): 'expected'}, rev)
        wk = q("SELECT reviewed, real, expected, data_issue, pct_real_of_reviewed AS pct FROM n360.v_flag_quality "
               "WHERE week = toDate('2026-11-09')")
        check('B19: weekly flag quality for the week of 9 Nov: 3 reviewed, 1 real, 1 expected, 1 data issue',
              wk and [int(wk[0][k]) for k in ('reviewed', 'real', 'expected', 'data_issue')] == [3, 1, 1, 1], wk)

    # Anomaly v2 (B20): the three planted Orchid Health cases, and nothing else (no false positives)
    if int(q("SELECT count() AS n FROM n360.runs WHERE status = 'loaded'")[0]['n']) > 1:   # needs several nights
        sig = sorted((r['signal'], r['account']) for r in q('SELECT signal, account FROM n360.v_anomaly_v2'))
        check('B20: containment drop, measure gap and pace change on Orchid Health only',
              sig == [('containment drop', 'Orchid Health'), ('measure gap', 'Orchid Health'), ('pace change', 'Orchid Health')], sig)

    # Phase 2 (proposed design): skipped when the export carried no Phase 2 files
    if int(q('SELECT count() AS n FROM n360.va_config_snapshot')[0]['n']):
        cur = {r['va_name']: r for r in q('SELECT v.va_name AS va_name, c.engine_version AS engine_version, '
                                          'c.current_engine AS current_engine FROM n360.v_va_config_latest AS c '
                                          'INNER JOIN n360.va AS v ON v.bot_id = c.bot_id')}
        check('Phase 2: VAs on V7 are not current, V8 is',
              not cur['Lakeside Email']['current_engine'] and not cur['Cedar Ops Chat']['current_engine']
              and cur['Harbor Chat']['current_engine'], {k: v['engine_version'] for k, v in cur.items()})
        ch_ = q('SELECT v.va_name AS va_name, arrayStringConcat(c.changes, \'; \') AS changes FROM n360.v_va_config_change AS c '
                'INNER JOIN n360.va AS v ON v.bot_id = c.bot_id ORDER BY va_name')
        check('Phase 2: the two planted configuration changes, nothing else',
              [r['va_name'] for r in ch_] == ['Harbor Chat', 'Pinecrest Chat'], ch_)
        idle = q("SELECT playbook FROM n360.playbook_day WHERE day > (SELECT toDate(max(window_end)) FROM n360.runs "
                 "WHERE status = 'loaded') - 31 GROUP BY bot_id, playbook HAVING sum(executions) = 0")
        check('Phase 2: one built-but-idle playbook (Gift cards)', [r['playbook'] for r in idle] == ['[SYNTHETIC] Gift cards'], idle)
        asks = {r['ask']: int(r['customers']) for r in q('SELECT ask, customers FROM n360.v_top_asks')}
        check('Phase 2: top asks = the ask from 3 customers and the one from 2',
              asks == {'Export conversations to CSV': 3, 'SMS opt-out keywords': 2}, asks)

    # Contract status (dashboard review, 25 Nov 2026): every v1.1 status from a planted synthetic case
    st = {r['full_label']: r for r in q("SELECT full_label, status, toUInt8(renewal_risk) AS risk, used, pct_billed, "
                                         "issue, toUInt8(needs_attention) AS na FROM n360.v_contract_status")}
    want = {'Atlas Streaming · Chat': 'over billed', 'Bluebird Air · 3 lines, pooled entitlement': 'over billed',
            'Harbor Telecom · Chat': 'will exceed', 'Kestrel Travel · Chat': 'will exceed', 'Willow Energy · Chat': 'below pace',
            'Summit Games · Chat': 'on pace', 'Maple Clinics · Chat': 'too early', 'Maple Clinics · Voice': 'under review',
            'Maple Clinics · Email': 'billed not recorded', 'Maple Clinics · SMS': 'unmapped',
            'Pinecrest Retail · Voice': 'not built', 'Harbor Telecom · Voice': 'not measured'}
    got = {k: st.get(k, {}).get('status') for k in want}
    check('v_contract_status: each planted case has its v1.1 status', got == want, {k: v for k, v in got.items() if v != want[k]})
    check('v_contract_status: one row per line not in a pool, and each pool once',
          len(st) == int(q("SELECT count() AS n FROM n360.v_line_pace")[0]['n']), len(st))
    check('Willow Energy Chat is at renewal risk (under half used, under 120 days left); nothing else is',
          [k for k, r in st.items() if int(r['risk'])] == ['Willow Energy · Chat'], [k for k, r in st.items() if int(r['risk'])])
    check('no reading is never zero: unmapped, not built and not measured lines have used NULL',
          all(st[k]['used'] is None for k in ('Maple Clinics · SMS', 'Pinecrest Retail · Voice', 'Harbor Telecom · Voice')),
          {k: st[k]['used'] for k in ('Maple Clinics · SMS', 'Pinecrest Retail · Voice', 'Harbor Telecom · Voice')})
    check('ended terms are flagged as ended (Atlas, Lakeside)',
          st['Atlas Streaming · Chat']['issue'] == 'Term ended' and st['Lakeside Insurance · Email']['issue'] == 'Term ended',
          (st['Atlas Streaming · Chat']['issue'], st['Lakeside Insurance · Email']['issue']))
    nights = q("SELECT status, count() AS n FROM n360.v_status_night WHERE data_date = (SELECT max(data_date) FROM "
               "n360.forecast_history) GROUP BY status")
    now = q("SELECT status, count() AS n FROM n360.v_contract_status WHERE series_key IN (SELECT series_key FROM "
            "n360.forecast_history WHERE data_date = (SELECT max(data_date) FROM n360.forecast_history)) GROUP BY status")
    check('v_status_night: the newest night equals today\'s status (one number, one value)',
          sorted((r['status'], int(r['n'])) for r in nights) == sorted((r['status'], int(r['n'])) for r in now), (nights, now))
    att = sorted((r['display_name'], r['issue']) for r in q('SELECT display_name, issue FROM n360.v_needs_attention'))
    check('v_needs_attention: the planted items (over, will exceed, renewal risk, ended, a busy VA down 25%+)',
          ('Willow Energy', 'Conversations down 33%') in att and ('Willow Energy', 'Renewal risk: 18% used') in att
          and ('Harbor Telecom', 'Set to exceed billed') in att and ('Lakeside Insurance', 'Term ended') in att
          and not any(c == 'Maple Clinics' for c, _ in att) and not any(c == 'Summit Games' for c, _ in att), att)
    vc = {r['va_name']: r for r in q('SELECT va_name, containment_30, toUInt8(containment_down) AS cd, toUInt8(convs_down) AS cv '
                                     'FROM n360.v_va_change')}
    check('v_va_change: containment not read stays NULL (Summit NCW, conversation records reader)',
          vc['Summit NCW']['containment_30'] is None, vc['Summit NCW'])
    check('v_va_change: Orchid containment down 3+ points; Willow and Lakeside conversations down 10%+',
          int(vc['Orchid Chat']['cd']) == 1 and int(vc['Willow Chat']['cv']) == 1 and int(vc['Lakeside Email']['cv']) == 1,
          {k: (v['cd'], v['cv']) for k, v in vc.items() if int(v['cd']) or int(v['cv'])})
    notes = [r['note'] for r in q("SELECT note FROM n360.v_customer_notes WHERE account = 'Maple Clinics' ORDER BY o")]
    check('v_customer_notes: Maple Clinics notes the pending rule, the blank billed amount and the unmapped line',
          len(notes) == 3 and 'not live yet' in notes[0], notes)
    fresh = q('SELECT toUInt8(stale) AS stale, stamp FROM n360.v_freshness')
    check('v_freshness: one stamp, "Usage to ..."', len(fresh) == 1 and fresh[0]['stamp'].startswith('Usage to '), fresh)

    # Salesforce and risk (25 Nov 2026), when the CI loaded the synthetic Salesforce pull and register entries
    if int(q("SELECT count() AS n FROM n360.sf_account")[0]['n']):
        link = {r['sf_name']: r['account'] for r in q('SELECT sf_name, account FROM n360.v_sf_account')}
        check('Salesforce: accounts linked by name, Juniper through an override, the prospect not at all',
              link.get('Harbor Telecom') == 'Harbor Telecom' and link.get('Juniper Bank N.A.') == 'Juniper Bank'
              and link.get('Globex Prospects') == '', link)
        un = [r['sf_name'] for r in q('SELECT sf_name FROM n360.v_sf_unlinked')]
        check('Salesforce: the unlinked account is listed (v_sf_unlinked)', un == ['Globex Prospects'], un)
        sig = {(r['account'], r['signal']): r for r in q('SELECT account, signal, level, reason FROM n360.v_risk_signal')}
        want = {('Lakeside Insurance', 'renewal'): 'risk', ('Willow Energy', 'renewal'): 'risk',
                ('Juniper Bank', 'renewal'): 'watch', ('Maple Clinics', 'renewal'): 'watch',
                ('Harbor Telecom', 'renewal'): 'fine', ('Atlas Streaming', 'renewal'): 'fine',
                ('Willow Energy', 'contact'): 'risk', ('Lakeside Insurance', 'contact'): 'risk',
                ('Kestrel Travel', 'contact'): 'watch', ('Summit Games', 'contact'): 'watch',
                ('Cedar Logistics', 'contact'): 'risk', ('Harbor Telecom', 'contact'): 'fine',
                ('Willow Energy', 'pace'): 'risk', ('Willow Energy', 'volume'): 'risk',
                ('Lakeside Insurance', 'va_health'): 'risk', ('Orchid Health', 'containment'): 'risk',
                ('Harbor Telecom', 'concentration'): 'watch', ('Maple Clinics', 'billing'): 'watch',
                ('Cedar Logistics', 'pace'): 'not reported', ('Summit Games', 'containment'): 'not reported',
                ('Willow Energy', 'csm'): 'risk', ('Harbor Telecom', 'csm'): 'watch', ('Northwind Outfitters', 'csm'): 'fine',
                ('Atlas Streaming', 'csm'): 'not reported'}
        got = {k: sig.get(k, {}).get('level') for k in want}
        check('risk signals: each planted case at its level', got == want, {k: (v, want[k]) for k, v in got.items() if v != want[k]})
        check('risk signals: 12 per customer, every level a known word',
              len(sig) == 12 * int(q('SELECT count() AS n FROM n360.account')[0]['n'])
              and {r['level'] for r in sig.values()} <= {'risk', 'watch', 'fine', 'not reported'}, len(sig))
        cust = {r['display_name']: r for r in q('SELECT display_name, level, csm_level, ca_level, days_to_renewal, '
                                                 'renewal_amount FROM n360.v_risk_customer')}
        check('risk customers: worst signal wins; Orchid is at risk on the architect side only',
              cust['Willow Energy']['level'] == 'risk' and cust['Orchid Health']['ca_level'] == 'risk'
              and cust['Orchid Health']['csm_level'] == 'watch' and cust['Atlas Streaming']['level'] == 'fine',
              {k: (v['level'], v['csm_level'], v['ca_level']) for k, v in cust.items()})
        check('risk customers: renewal date and amount from the open Salesforce renewal (Willow 42 days, 190,000)',
              int(cust['Willow Energy']['days_to_renewal']) == 42 and float(cust['Willow Energy']['renewal_amount']) == 190000,
              cust['Willow Energy'])
        reg = {r['risk_id']: r for r in q("SELECT risk_id, toString(status) AS status, outcome, toString(due) AS due, "
                                          "toUInt8(overdue) AS overdue FROM n360.v_risk_register")}
        check('risk register: the newest row wins (Kestrel updated, Atlas closed saved, Orchid overdue)',
              reg['R-kestrel']['due'] == '2026-12-20' and reg['R-atlas']['status'] == 'closed'
              and reg['R-atlas']['outcome'] == 'saved' and int(reg['R-orchid']['overdue']) == 1, reg)
        hist = q('SELECT count(DISTINCT data_date) AS n, uniqExact(signal) AS s FROM n360.risk_history')[0]
        check('risk history: every loaded night, every signal',
              int(hist['n']) == int(q("SELECT countIf(status = 'loaded') AS n FROM n360.runs")[0]['n']) and int(hist['s']) == 12, hist)

    # risk model (docs/21), when CI scored it
    if int(q("SELECT count() AS n FROM n360.risk_scores")[0]['n']):
        sc = {r['account']: r for r in q("SELECT account, health, band, probability, arrayStringConcat(hard_reds, '; ') AS reds "
                                          "FROM n360.v_risk_score")}
        check('risk model: one current score per customer, probabilities between 0 and 1',
              len(sc) == int(q('SELECT count() AS n FROM n360.account')[0]['n'])
              and all(r['probability'] is None or 0 <= float(r['probability']) <= 1 for r in sc.values()), len(sc))
        w = sc.get('Willow Energy', {})
        check('risk model: Willow Energy at risk, held there by hard reds (renewal omitted, open churn risk)',
              w.get('band') == 'at risk' and 'Renewal lost or omitted' in w.get('reds', '') and 'churn risk' in w.get('reds', ''), w)
        check('risk model: the customers the signals call fine are not at risk in the model (Atlas, Northwind)',
              sc['Atlas Streaming']['band'] != 'at risk' and sc['Northwind Outfitters']['band'] != 'at risk',
              {k: sc[k]['band'] for k in ('Atlas Streaming', 'Northwind Outfitters')})
        pts = q("SELECT s.account AS a, any(s.health) AS h, sum(c.points) AS p, arrayStringConcat(any(s.hard_reds), '') AS r "
                "FROM n360.v_risk_score AS s INNER JOIN n360.v_risk_drivers AS c ON c.account = s.account "
                "WHERE s.health IS NOT NULL GROUP BY s.account")
        bad = [r for r in pts if abs(float(r['p']) - (100 - float(r['h']))) > 0.5]
        check('risk model: points lost add up to 100 - health for every customer, hard red or not', not bad, bad)
        mk = q("SELECT any(kind) AS kind, count() AS n FROM n360.v_risk_model")[0]
        check('risk model: the model in use is the run that was adopted (the expert prior: too few outcomes to learn from)',
              int(mk['n']) == 1 and mk['kind'] == 'prior', mk)
        src = {r['source']: int(r['n']) for r in q('SELECT source, uniqExact(as_of) AS n FROM n360.risk_features GROUP BY source')}
        check('risk model: the feature store says which dates were stored that night and which were rebuilt later',
              src.get('rebuilt', 0) >= 20, src)
        hist = q('SELECT uniqExact(as_of) AS n FROM n360.risk_scores')[0]['n']
        check('risk model: a score history over the stored weeks', int(hist) >= 20, hist)
        outs = {(r['account'], r['outcome'], r['source'], r['d']) for r in
                q('SELECT account, outcome, source, toString(date) AS d FROM n360.v_risk_outcomes')}
        got = {(a_, o, src) for a_, o, src, _ in outs}
        check('risk model: outcomes from Salesforce renewals (Lakeside lost, Atlas won)',
              ('Lakeside Insurance', 'churned', 'salesforce') in got and ('Atlas Streaming', 'renewed', 'salesforce') in got, outs)
        check('risk model: a downsell is judged against the previous won RENEWAL (Kestrel 180,000 then 120,000: downsold, '
              'whatever the small add-on between them; Orchid\'s first renewal after a three-year deal: renewed)',
              ('Kestrel Travel', 'downsold', 'salesforce', '2026-02-28') in outs and ('Orchid Health', 'renewed', 'salesforce') in got
              and ('Orchid Health', 'downsold', 'salesforce') not in got, sorted(x for x in outs if x[0] in ('Kestrel Travel', 'Orchid Health')))
        check('risk model: a churn closed in the register carries the day it happened, not the day it was typed in; '
              'a risk closed as saved is not a renewal outcome',
              ('Lakeside Insurance', 'churned', 'register', '2026-09-15') in outs and not any(x[2] == 'register' and x[0] == 'Atlas Streaming' for x in outs),
              sorted(x for x in outs if x[2] == 'register'))
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'risk'))
        import features as fe
        # one renewal per account: the largest running line's end date, not the first line to end
        lines = ("(SELECT 'X' AS account, toDate('2026-12-15') AS end_date, toDate('2026-01-01') AS start_date, "
                 "toNullable(toUInt64(5000)) AS billed, CAST(NULL AS Nullable(UInt8)) AS pool_id "
                 "UNION ALL SELECT 'X', toDate('2027-06-30'), toDate('2026-07-01'), toNullable(toUInt64(400000)), CAST(NULL AS Nullable(UInt8)))")
        ren = q(fe._q(fe.RENEWAL, '2026-11-19').replace('n360.contract_line', lines))
        check('risk model: an account has one renewal date, its largest running line\'s (a small add-on ending first is not the renewal)',
              len(ren) == 1 and int(ren[0]['days_to_renewal']) == 223 and int(ren[0]['billed_lines']) == 2, ren)
        sfr = {r['account']: int(r['sf_days_to_renewal']) for r in q(fe._q(fe.SF_RENEWAL, '2025-12-01'))}
        check('risk model: a past date with no contract line takes its renewal date from Salesforce (Kestrel, 1 Dec 2025: '
              'the renewal closed 28 Feb 2026 is 89 days away), so past renewals can be learned from',
              sfr.get('Kestrel Travel') == 89 and sfr.get('Orchid Health') == 88, sfr)
        fl = q("SELECT account, value FROM n360.risk_features WHERE feature = 'open_churn_risks' AND as_of = "
               "(SELECT min(as_of) FROM n360.risk_features)")
        check('risk model: before the risk register was first used, "open churn risks" is not reported, never zero',
              fl and all(r['value'] is None for r in fl), fl[:3])

    print('VIEW TESTS PASSED' if not failed else f'{failed} VIEW TESTS FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
