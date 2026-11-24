#!/usr/bin/env python3
"""Run every alert rule's query (grafana/provisioning/alerting) and check which rules fire in a known state of the
synthetic data (backlog B18). code/ci.sh runs it after chosen nights of the 14-night load.

    python3 code/grafana/test_alerts.py --chdb /tmp/n360 --scenario final
    python3 code/grafana/test_alerts.py --url ... --user grafana --password ... --scenario night9

A rule fires for each row with value > 0 (the rule's threshold). Scenarios state which rules must fire (and for
what) and which must not; every other rule only has to run without error. "Data stale" depends on the clock and
is only run.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from load import CH  # noqa: E402

# rule uid -> label value that must appear among firing rows (or True = fires at all; False = must not fire)
SCENARIOS = {
    # 14 nights end 19 Nov. Night 7 = data through 12 Nov: Lakeside's silence (from 11 Nov) is only provisional.
    'night7': {'n360-va-silent-dropped': False, 'n360-load-failed': False},
    'night9': {'n360-load-failed': True},                                     # the broken night was the last run
    'night11': {'n360-cross-account': 'spikes', 'n360-va-silent-dropped': 'Lakeside Email (Email)'},  # 13 Nov settled
    'night12': {'n360-crossing-soon': 'Harbor Telecom|0'},                    # Harbor now crosses within 30 days
    'final': {'n360-load-failed': False, 'n360-va-silent-dropped': 'Lakeside Email (Email)',
              'n360-cross-account': False, 'n360-source-stale': False},
    'stale2': {'n360-source-stale': 'containment_days'},                      # --stale containment_days=2 (amber)
    'stale3': {'n360-source-stale': 'consumption_rules'},                     # --stale consumption_rules=3 (red)
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    ap.add_argument('--scenario', choices=sorted(SCENARIOS), required=True)
    a = ap.parse_args()
    ch = CH(a)
    rules = json.load(open(os.path.join(HERE, 'provisioning', 'alerting', 'n360-alert-rules.json')))['groups'][0]['rules']
    want = SCENARIOS[a.scenario]
    failed = 0
    print(f'alerts, scenario {a.scenario}')
    for r in rules:
        sql = r['data'][0]['model']['rawSql']
        try:
            rows = ch.rows(sql)
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {r['title']}: query error {str(e)[:200]}")
            failed += 1
            continue
        firing = [row for row in rows if float(row.get('value') or 0) > 0]
        labels = [' / '.join(str(v) for k, v in row.items() if k != 'value') for row in firing]
        exp = want.get(r['uid'])
        if exp is None:
            ok = True
        elif exp is False:
            ok = not firing
        elif exp is True:
            ok = bool(firing)
        else:
            ok = any(exp in l for l in labels)
        state = f"firing {labels}" if firing else 'normal'
        print(f"  {'OK  ' if ok else 'FAIL'} {r['title']:28s} {state}" + ('' if exp is None else f"   (expected: {exp})"))
        failed += 0 if ok else 1
    print('ALERT TESTS PASSED' if not failed else f'{failed} ALERT TESTS FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
