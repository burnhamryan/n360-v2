#!/usr/bin/env python3
"""Run every panel query and every variable query in grafana/dashboards/*.json against ClickHouse,
with Grafana's macros replaced the way the ClickHouse plugin expands them. Fails (exit 1) if any
query errors or a panel listed as must-have-rows returns none.

    python3 code/grafana/test_panels.py --chdb /tmp/n360                          # embedded, after load.py --chdb
    python3 code/grafana/test_panels.py --user grafana --password ...             # server, as Grafana's user

Macros handled: ${var}, ${var:singlequote}, $var (variables expand to every value, as "All" does),
$__dateFilter(col), $__timeFilter(col), $__fromTime, $__toTime, ${__from}, ${__to}.
Time range: --days back from the run's data-through day (default 90, the dashboards' default).
"""
import argparse, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from load import CH  # noqa: E402  the loader's server/chdb wrapper


def expand(sql, values, frm, to):
    def var(m):
        return quote(m.group(1), m.group(2))

    def quote(name, fmt=None):
        vals = values.get(name)
        if vals is None:
            raise KeyError(f'unknown variable ${name}')
        if fmt == 'singlequote' or fmt is None:
            return ','.join("'" + v.replace("'", "\\'") + "'" for v in vals)
        raise KeyError(f'unhandled variable format :{fmt}')
    sql = re.sub(r'\$__dateFilter\(([^)]+)\)', rf"\1 >= toDate('{frm}') AND \1 <= toDate('{to}')", sql)
    sql = re.sub(r'\$__timeFilter\(([^)]+)\)', rf"\1 >= toDateTime('{frm} 00:00:00') AND \1 <= toDateTime('{to} 23:59:59')", sql)
    sql = sql.replace('$__fromTime', f"toDateTime('{frm} 00:00:00')").replace('$__toTime', f"toDateTime('{to} 23:59:59')")
    sql = sql.replace('${__from}', f"toDateTime('{frm} 00:00:00')").replace('${__to}', f"toDateTime('{to} 23:59:59')")
    sql = re.sub(r'\$\{(\w+)(?::(\w+))?\}', var, sql)
    sql = re.sub(r'\$(?!__)(\w+)', lambda m: quote(m.group(1)), sql)
    if '$' in sql:
        raise KeyError('unexpanded macro left: ' + sql[sql.index('$'):sql.index('$') + 40])
    return sql


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    ap.add_argument('--days', type=int, default=90)
    ap.add_argument('--dashboards', default=os.path.join(HERE, 'dashboards'))
    a = ap.parse_args()
    ch = CH(a)

    last = ch.rows("SELECT toString(toDate(window_end)) AS d FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC LIMIT 1")
    if not last:
        print('no loaded run in n360.runs; load data first')
        return 1
    to = last[0]['d']
    frm = ch.rows(f"SELECT toString(toDate('{to}') - {a.days}) AS d")[0]['d']

    failed = 0
    for path in sorted(glob.glob(os.path.join(a.dashboards, '*.json'))):
        dash = json.load(open(path))
        print(f"{dash['uid']}  ({frm} to {to})")
        values = {}
        for v in dash.get('templating', {}).get('list', []):
            if v.get('type') != 'query':
                continue
            try:
                rows = ch.rows(expand(v['query'], values, frm, to))
                values[v['name']] = [str(next(iter(r.values()))) for r in rows]
                print(f"  var  {v['name']:28s} OK   {len(rows)} values")
                if not rows:
                    failed += 1
                    print('       FAIL: variable has no values')
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  var  {v['name']:28s} FAIL {e}")
        for p in dash['panels']:
            for t in p.get('targets', []):
                try:
                    rows = ch.rows(expand(t['rawSql'], values, frm, to))
                    ok = bool(rows)
                    print(f"  {p['id']:>3}  {p['title'][:28]:28s} {'OK  ' if ok else 'FAIL'} {len(rows)} rows")
                    if not ok:
                        failed += 1
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"  {p['id']:>3}  {p['title'][:28]:28s} FAIL {str(e)[:300]}")
    print('ALL PANEL QUERIES OK' if not failed else f'{failed} FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
