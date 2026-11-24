#!/usr/bin/env python3
"""Record what a flagged anomaly turned out to be (backlog B19): real, expected, or a data issue.
Grafana OSS cannot write data and its database user is read-only, so reviews go through this tool, as the
loader user. Outcomes land in n360.anomaly_log (the nightly load never touches it); the newest review of a
flag wins. They show in the Anomalies feed ("Review") and in weekly flag quality (n360.v_flag_quality).

    python3 code/review_anomaly.py --list                                    # settled flags not reviewed yet
    python3 code/review_anomaly.py --va "Lakeside Email" --day 2026-11-11 --outcome real --note "inbox outage" --by ryan

Connection: --url/--user/--password (server, loader user) or --chdb PATH, as load.py.
"""
import argparse, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import CH  # noqa: E402


def esc(v):
    """SQL string literal: backslashes and single quotes escaped."""
    return "'" + str(v).replace('\\', '\\\\').replace("'", "\\'") + "'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    ap.add_argument('--list', action='store_true', help='list settled flags with no review yet')
    ap.add_argument('--va', help='VA name, as on the dashboards (without the channel)')
    ap.add_argument('--day', help='the flagged day, YYYY-MM-DD')
    ap.add_argument('--outcome', choices=['real', 'expected', 'data issue'])
    ap.add_argument('--note', default='')
    ap.add_argument('--by', default=os.environ.get('USER', 'unknown'), help='who reviewed it')
    a = ap.parse_args()
    ch = CH(a)
    if a.list:
        for r in ch.rows("SELECT toString(day) AS day, va_name, account, kind, z FROM n360.v_anomaly_review "
                         "WHERE NOT provisional AND outcome IS NULL ORDER BY day DESC, va_name"):
            print(f"{r['day']}  {r['va_name']:24s} {r['account']:22s} {r['kind']:12s} z {r['z']}")
        return 0
    if not (a.va and a.day and a.outcome):
        ap.error('--va, --day and --outcome are required (or --list)')
    flag = ch.rows(f"SELECT toString(bot_id) AS bot_id, kind, provisional FROM n360.v_va_day_anomaly "
                   f"WHERE va_name = {esc(a.va)} AND day = toDate({esc(a.day)}) AND kind != 'normal'")
    if not flag:
        print(f'No flag for VA {a.va!r} on {a.day}: nothing recorded.')
        return 1
    f = flag[0]
    ch.cmd(f"INSERT INTO n360.anomaly_log (bot_id, day, kind, outcome, note, reviewed_by) VALUES "
           f"(toUUID({esc(f['bot_id'])}), toDate({esc(a.day)}), {esc(f['kind'])}, {esc(a.outcome)}, {esc(a.note)}, {esc(a.by)})")
    print(f"Recorded: {a.va} {a.day} {f['kind']} -> {a.outcome}" + (' (the flag is still provisional)' if f['provisional'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
