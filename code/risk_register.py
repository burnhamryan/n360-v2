#!/usr/bin/env python3
"""The risk register and the CSM's rating, for the Risk dashboard. Grafana OSS cannot write data and its database user
is read-only, so CSMs record risks here, as the loader user (like code/review_anomaly.py). Rows land in
n360.risk_register / n360.risk_pulse (append-only; the nightly load never touches them); the newest row wins.

    python3 code/risk_register.py add --customer "Willow Energy" --reason "Adoption / value" --impact churn \
        --owner csm12 --due 2026-12-10 --note "usage falling, sponsor quiet"
    python3 code/risk_register.py update --id R-1a2b3c --owner csm3 --due 2026-12-15 --note "exec call booked"
    python3 code/risk_register.py close --id R-1a2b3c --outcome saved --note "renewed at 80%"
    python3 code/risk_register.py pulse --customer "Harbor Telecom" --level amber --note "budget review in Q1"
    python3 code/risk_register.py list                  # open risks (--all: closed too)

Reasons and impacts are a short fixed list (docs/17: 6-12 codes, no pre-filled default). Connection: --url/--user/
--password (server, loader user) or --chdb PATH, as load.py.
"""
import argparse, datetime as dt, os, sys, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import CH, snapshot_risk  # noqa: E402

# PROPOSED reason codes (docs/17 section 6): agree the list before CSMs start using it
REASONS = ['Adoption / value', 'Product gap', 'Quality / bugs', 'Support', 'Champion or sponsor change',
           'Budget / price', 'Competitor', 'Company change', 'Implementation']
IMPACTS = ['churn', 'downsell', 'contraction']
OUTCOMES = ['saved', 'churned', 'downsold', 'no action', 'invalid']


def esc(v):
    return 'NULL' if v is None else "'" + str(v).replace('\\', '\\\\').replace("'", "\\'") + "'"


def customer(ch, name):
    rows = ch.rows(f"SELECT account FROM n360.account WHERE lower(account) = lower({esc(name)}) "
                   f"OR lower(display_name) = lower({esc(name)})")
    if not rows:
        sys.exit(f'No customer called {name!r} (use the name on the dashboards).')
    return rows[0]['account']


def current(ch, rid):
    rows = ch.rows(f"SELECT account, reason, impact, owner, toString(due) AS due, toString(status) AS status, outcome, note "
                   f"FROM n360.v_risk_register WHERE risk_id = {esc(rid)}")
    if not rows:
        sys.exit(f'No risk {rid!r}.')
    return rows[0]


def write(ch, rid, r, by):
    due = None if r['due'] in (None, '', '\\N') else r['due']
    ch.cmd("INSERT INTO n360.risk_register (risk_id, account, reason, impact, owner, due, status, outcome, note, by) VALUES "
           f"({esc(rid)}, {esc(r['account'])}, {esc(r['reason'])}, {esc(r['impact'])}, {esc(r['owner'])}, "
           f"{'NULL' if due is None else 'toDate(' + esc(due) + ')'}, {esc(r['status'])}, {esc(r['outcome'])}, "
           f"{esc(r['note'])}, {esc(by)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    ap.add_argument('--by', default=os.environ.get('USER', 'unknown'), help='who recorded it')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('add', help='open a risk')
    p.add_argument('--customer', required=True)
    p.add_argument('--reason', required=True, choices=REASONS)
    p.add_argument('--impact', required=True, choices=IMPACTS)
    p.add_argument('--owner', required=True)
    p.add_argument('--due', type=dt.date.fromisoformat)
    p.add_argument('--note', default='')
    p.add_argument('--id', help='give the risk an id (default: a new R-xxxxxx)')
    p = sub.add_parser('update', help='change an open risk')
    p.add_argument('--id', required=True)
    p.add_argument('--reason', choices=REASONS)
    p.add_argument('--impact', choices=IMPACTS)
    p.add_argument('--owner')
    p.add_argument('--due', type=dt.date.fromisoformat)
    p.add_argument('--note')
    p = sub.add_parser('close', help='close a risk with its outcome')
    p.add_argument('--id', required=True)
    p.add_argument('--outcome', required=True, choices=OUTCOMES)
    p.add_argument('--note')
    p = sub.add_parser('pulse', help="record the CSM's rating of a customer")
    p.add_argument('--customer', required=True)
    p.add_argument('--level', required=True, choices=['green', 'amber', 'red'])
    p.add_argument('--note', default='')
    p = sub.add_parser('list', help='open risks')
    p.add_argument('--all', action='store_true')
    a = ap.parse_args()
    ch = CH(a)
    if a.cmd == 'add':
        rid = a.id or 'R-' + uuid.uuid4().hex[:6]
        write(ch, rid, dict(account=customer(ch, a.customer), reason=a.reason, impact=a.impact, owner=a.owner,
                            due=a.due and a.due.isoformat(), status='open', outcome='', note=a.note), a.by)
        print(f'Opened {rid}: {a.customer}, {a.reason} ({a.impact}), owner {a.owner}')
    elif a.cmd in ('update', 'close'):
        r = current(ch, a.id)
        if r['status'] == 'closed':
            sys.exit(f'{a.id} is already closed.')
        for k in ('reason', 'impact', 'owner', 'note'):
            if getattr(a, k, None) is not None:
                r[k] = getattr(a, k)
        if getattr(a, 'due', None):
            r['due'] = a.due.isoformat()
        if a.cmd == 'close':
            r['status'], r['outcome'] = 'closed', a.outcome
        write(ch, a.id, r, a.by)
        print(f"{'Closed' if a.cmd == 'close' else 'Updated'} {a.id}" + (f': {a.outcome}' if a.cmd == 'close' else ''))
    elif a.cmd == 'pulse':
        acct = customer(ch, a.customer)
        ch.cmd(f"INSERT INTO n360.risk_pulse (account, level, note, by) VALUES ({esc(acct)}, {esc(a.level)}, {esc(a.note)}, {esc(a.by)})")
        print(f'Rated {a.customer} {a.level}')
    if a.cmd != 'list':
        snapshot_risk(ch)                   # the newest night's risk snapshot now includes this change
    else:
        for r in ch.rows("SELECT risk_id, account, reason, impact, owner, toString(due) AS due, toString(status) AS status, "
                         "outcome, note FROM n360.v_risk_register " + ('' if a.all else "WHERE status = 'open' ")
                         + "ORDER BY due IS NULL, due"):
            print(f"{r['risk_id']}  {r['status']:6s} {r['account']:22s} {r['reason']:26s} {r['impact']:11s} "
                  f"{r['owner']:10s} due {r['due'] or '-':10s} {r['outcome'] or ''} {r['note']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
