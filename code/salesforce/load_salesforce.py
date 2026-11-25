#!/usr/bin/env python3
"""Load one Salesforce pull (sf_pull.py output) into ClickHouse: stage, check, swap. Independent of the nightly
export: a failed pull or check leaves the previous Salesforce copy live, and never touches the other tables.

    python3 code/salesforce/load_salesforce.py --rows ~/n360-work/salesforce --user n360 --password ...
    python3 code/salesforce/load_salesforce.py --rows /tmp/sf --chdb /tmp/n360          # embedded, for tests

Checks (any failure refuses the pull, exit 3): ids unique per table; at least one account; every opportunity,
contract and activity names an account. Accounts with no N360 match are reported (not a failure).
Exit codes: 0 loaded, 3 refused, 2 error.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from load import CH, statements, snapshot_risk  # noqa: E402

TABLES = {'sf_account': 'sf_account_id', 'sf_opportunity': 'opp_id', 'sf_contract': 'contract_id',
          'sf_activity': 'activity_id'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rows', required=True)
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    a = ap.parse_args()
    ch = CH(a)
    schema = open(os.path.join(os.path.dirname(HERE), 'schema.sql')).read()
    for db in ['n360', 'n360_next']:
        for st in statements(schema):
            if db == 'n360_next' and st.upper().startswith(('CREATE VIEW', 'CREATE OR REPLACE VIEW')):
                continue
            ch.cmd(st.replace('n360.', f'{db}.').replace('DATABASE IF NOT EXISTS n360', f'DATABASE IF NOT EXISTS {db}'))
    run = json.load(open(os.path.join(a.rows, 'sf_run.json')))
    for t in TABLES:
        ch.cmd(f'TRUNCATE TABLE n360_next.{t}')
        path = os.path.abspath(os.path.join(a.rows, f'{t}.ndjson'))
        if os.path.getsize(path):
            ch.insert_ndjson(f'n360_next.{t}', path)
    failed = []
    for t, key in TABLES.items():
        dup = ch.rows(f'SELECT {key} AS id, count() AS n FROM n360_next.{t} GROUP BY id HAVING n > 1 LIMIT 5')
        print(f'  {t + " ids unique":32s} {"PASS" if not dup else "FAIL"}')
        if dup:
            failed.append(f'{t}_ids_unique')
    n_acc = ch.rows('SELECT count() AS n FROM n360_next.sf_account')[0]['n']
    print(f'  {"accounts not empty":32s} {"PASS" if int(n_acc) else "FAIL"}')
    if not int(n_acc):
        failed.append('accounts_not_empty')
    for t in ('sf_opportunity', 'sf_contract', 'sf_activity'):
        orphan = int(ch.rows(f'SELECT countIf(sf_account_id IS NULL OR sf_account_id = \'\') AS n FROM n360_next.{t}')[0]['n'])
        print(f'  {t + " names an account":32s} {"PASS" if not orphan else f"FAIL ({orphan})"}')
        if orphan:
            failed.append(f'{t}_names_account')
    unmatched = [r['name'] for r in ch.rows("""
        SELECT s.name AS name FROM n360_next.sf_account AS s
        WHERE s.account_hint = '' AND lower(s.name) NOT IN (SELECT lower(account) FROM n360.account)
          AND lower(s.name) NOT IN (SELECT lower(display_name) FROM n360.account) ORDER BY name""")]
    counts = '{' + ','.join(f"'{k}':{v}" for k, v in run['row_counts'].items()) + '}'
    esc = lambda x: x.replace('\\', '\\\\').replace("'", "\\'")  # noqa: E731
    ch.cmd(f"""INSERT INTO n360.sf_runs (run_id, pulled_at, status, checks_failed, row_counts, unmatched_accounts)
               VALUES ('{run['run_id']}', '{run['pulled_at']}', '{'failed_checks' if failed else 'loaded'}',
                       [{','.join(f"'{f}'" for f in failed)}], {counts}, [{','.join(f"'{esc(u)}'" for u in unmatched)}])""")
    if failed:
        print(f'REFUSED: {len(failed)} check(s) failed; the previous Salesforce copy stays live')
        return 3
    for t in TABLES:
        ch.cmd(f'EXCHANGE TABLES n360.{t} AND n360_next.{t}')
    snapshot_risk(ch)                       # the newest night's risk snapshot now includes this pull
    print(f"LOADED Salesforce pull {run['run_id']} ({run['pulled_at']} UTC): "
          + ' '.join(f'{k}={v}' for k, v in run['row_counts'].items()))
    if unmatched:
        print(f'  {len(unmatched)} Salesforce accounts not linked to an N360 account (add to account_overrides in '
              f'salesforce.json if they should be): ' + ', '.join(unmatched[:20]) + (' ...' if len(unmatched) > 20 else ''))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print('ERROR:', e)
        sys.exit(2)
