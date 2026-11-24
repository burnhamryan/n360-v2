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

    print('VIEW TESTS PASSED' if not failed else f'{failed} VIEW TESTS FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
