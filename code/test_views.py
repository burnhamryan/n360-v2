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
    bb = [v for v in rows if v['account'] == 'Bluebird Air']
    check('Bluebird Air pool measured once: one pool row, no pooled line rows',
          len(bb) == 1 and bb[0]['entitlement'] == 'pool', bb)
    check('Bluebird Air pool is red at 122.3%', bb and bb[0]['pace'] == 'red' and abs(bb[0]['pct_billed'] - 122.3) < 0.05, bb)
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

    print('VIEW TESTS PASSED' if not failed else f'{failed} VIEW TESTS FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
