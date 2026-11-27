#!/usr/bin/env python3
"""Load one export (rows/*.ndjson) into ClickHouse: stage, check, swap.

    python3 clickhouse/load.py --rows data/2026-11-20/rows                       # server at localhost:8123
    python3 clickhouse/load.py --rows data/2026-11-20/rows --url http://host:8123 --user n360 --password ...
    python3 clickhouse/load.py --rows /tmp/rows --chdb /tmp/n360_chdb             # embedded engine, for tests

Steps
  1. schema.sql applied to n360 (live) and n360_next (staging).
  2. Every swappable table in n360_next truncated, then loaded from its NDJSON file.
  3. checks.sql run against n360_next. Any offending row: the run is logged as failed_checks,
     nothing is swapped, exit 3. The live tables keep the previous load.
     Phase 2 tables (OPTIONAL) are staged and swapped only when their file is in the export.
  4. EXCHANGE TABLES n360.<t> AND n360_next.<t> for each table (atomic per table; ES: alias flip).
  5. Append-only tables (runs, line_totals_history) written straight to n360; forecast_history copied from
     the forecast just loaded (plus an optional backfill of earlier nights); risk_history copied from v_risk_signal.

Exit codes: 0 loaded, 3 checks failed, 2 error.
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SWAP = ['account', 'va', 'contract_line', 'line_va', 'pool', 'rule', 'source',
        'va_day', 'line_day', 'line_day_billable', 'forecast']
APPEND = ['line_totals_history']
# Phase 2 (proposed): loaded and swapped only when the export carries the file; otherwise the live table is left
# as it is, so a night without Phase 2 files loads exactly as before.
OPTIONAL = ['va_config_snapshot', 'playbook_day', 'cortex_day', 'explorer_day', 'ps_ticket', 'feature_window', 'va_adoption', 'playbook_window']


class CH:
    """Thin wrapper over a ClickHouse server (clickhouse-connect) or embedded chdb."""

    def __init__(self, a):
        self.mode = 'chdb' if a.chdb else 'server'
        if self.mode == 'chdb':
            from chdb import session
            self.s = session.Session(a.chdb)
        else:
            import clickhouse_connect
            u = re.match(r'(https?)://([^:/]+):?(\d+)?', a.url)
            self.c = clickhouse_connect.get_client(host=u.group(2), port=int(u.group(3) or 8123),
                                                   secure=u.group(1) == 'https', username=a.user,
                                                   password=a.password or os.environ.get('CLICKHOUSE_PASSWORD', ''))

    def cmd(self, sql):
        if self.mode == 'chdb':
            self.s.query(sql)
        else:
            self.c.command(sql)

    def rows(self, sql):
        if self.mode == 'chdb':
            out = self.s.query(sql, 'JSONEachRow').bytes().decode()
            return [json.loads(l) for l in out.splitlines() if l.strip()]
        r = self.c.query(sql)
        return [dict(zip(r.column_names, row)) for row in r.result_rows]

    def insert_ndjson(self, table, path):
        if self.mode == 'chdb':
            self.s.query(f"INSERT INTO {table} SELECT * FROM file('{path}', 'JSONEachRow')")
        else:
            with open(path, 'rb') as f:
                self.c.raw_insert(table, insert_block=f.read(), fmt='JSONEachRow')


def statements(text):
    text = re.sub(r'--[^\n]*', '', text)
    return [s.strip() for s in text.split(';') if s.strip()]


def checks(text):
    """[(name, sql)] from '-- check: name' blocks."""
    out = []
    for block in re.split(r'^-- check: ', text, flags=re.M)[1:]:
        name, body = block.split('\n', 1)
        out.append((name.strip(), statements(body)[0]))
    return out


def snapshot_risk(ch, data_date=None):
    """Risk history: every risk signal as it stands now, stored under a night (default: the newest loaded night).
    Called by the nightly load, and again after a Salesforce pull or a register change, so the newest night always
    matches what the Risk dashboard shows (its "since last week" and, later, back-testing)."""
    if data_date is None:
        r = ch.rows("SELECT toString(max(data_date)) AS d FROM n360.runs WHERE status = 'loaded'")
        if not r or r[0]['d'] in (None, '1970-01-01'):
            return
        data_date = r[0]['d']
    ch.cmd(f"ALTER TABLE n360.risk_history DELETE WHERE data_date = '{data_date}' SETTINGS mutations_sync = 1")
    ch.cmd(f"""INSERT INTO n360.risk_history SELECT toDate('{data_date}'), account, signal, lens, level
               FROM n360.v_risk_signal""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rows', help='the export folder (rows/*.ndjson + run.json)')
    ap.add_argument('--schema-only', action='store_true',
                    help='create or update the databases, tables and views, load nothing (new server, before a restore)')
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb', help='path for an embedded chdb database (testing)')
    a = ap.parse_args()
    if not a.rows and not a.schema_only:
        ap.error('--rows is required (or --schema-only)')

    ch = CH(a)
    schema = open(os.path.join(HERE, 'schema.sql')).read()
    for db in ['n360', 'n360_next']:
        for st in statements(schema):
            if db == 'n360_next' and st.upper().startswith(('CREATE VIEW', 'CREATE OR REPLACE VIEW')):
                continue
            ch.cmd(st.replace('n360.', f'{db}.').replace('DATABASE IF NOT EXISTS n360', f'DATABASE IF NOT EXISTS {db}'))
    if a.schema_only:
        print('SCHEMA READY (n360, n360_next): nothing loaded')
        return 0
    run = json.load(open(os.path.join(a.rows, 'run.json')))

    for t in SWAP:
        ch.cmd(f'TRUNCATE TABLE n360_next.{t}')
        ch.insert_ndjson(f'n360_next.{t}', os.path.abspath(os.path.join(a.rows, f'{t}.ndjson')))
    optional = [t for t in OPTIONAL if os.path.exists(os.path.join(a.rows, f'{t}.ndjson'))]
    for t in optional:
        ch.cmd(f'TRUNCATE TABLE n360_next.{t}')
        ch.insert_ndjson(f'n360_next.{t}', os.path.abspath(os.path.join(a.rows, f'{t}.ndjson')))

    last_day = run['window_end'][:10]
    failed = []
    for name, sql in checks(open(os.path.join(HERE, 'checks.sql')).read()):
        bad = ch.rows(sql.replace('{db}', 'n360_next').replace('{last_day}', last_day))
        status = 'PASS' if not bad else f'FAIL ({len(bad)})'
        print(f'  {name:32s} {status}')
        for r in bad[:5]:
            print('      ', json.dumps(r, default=str))
        if bad:
            failed.append(name)

    counts = run['row_counts']
    cm = '{' + ','.join(f"'{k}':{v}" for k, v in counts.items()) + '}'
    fail_arr = '[' + ','.join(f"'{f}'" for f in failed) + ']'
    ch.cmd(f"""INSERT INTO n360.runs (run_id, data_date, window_end, capture_utc, source_dir, code_fingerprint,
               status, checks_failed, row_counts) VALUES ('{run['run_id']}', '{run['data_date']}',
               '{run['window_end']}', '{run['capture_utc']}', '{run['source_dir']}', '{run['code_fingerprint']}',
               '{'failed_checks' if failed else 'loaded'}', {fail_arr}, {cm})""")
    if failed:
        print(f'REFUSED: {len(failed)} check(s) failed; live tables unchanged (run {run["run_id"]})')
        return 3

    for t in SWAP + optional:
        ch.cmd(f'EXCHANGE TABLES n360.{t} AND n360_next.{t}')
    for t in APPEND:
        ch.cmd(f"ALTER TABLE n360.{t} DELETE WHERE data_date = '{run['data_date']}'")
        ch.insert_ndjson(f'n360.{t}', os.path.abspath(os.path.join(a.rows, f'{t}.ndjson')))
    # forecast history (B15): tonight's forecast of record, copied from the table just swapped in. Optional backfill
    # file (earlier nights' forecasts) fills only nights not already stored, so a stored night is never overwritten.
    backfill = os.path.join(a.rows, 'forecast_history.ndjson')
    if os.path.exists(backfill):
        ch.cmd('TRUNCATE TABLE n360_next.forecast_history')
        ch.insert_ndjson('n360_next.forecast_history', os.path.abspath(backfill))
        ch.cmd("""INSERT INTO n360.forecast_history SELECT * FROM n360_next.forecast_history
                  WHERE data_date NOT IN (SELECT DISTINCT data_date FROM n360.forecast_history)""")
    ch.cmd(f"ALTER TABLE n360.source_freshness DELETE WHERE data_date = '{run['data_date']}'")
    ch.cmd(f"""INSERT INTO n360.source_freshness
               SELECT toDate('{run['data_date']}'), source_id, last_day, dateDiff('day', last_day, toDate('{last_day}'))
               FROM n360.source""")
    ch.cmd(f"ALTER TABLE n360.forecast_history DELETE WHERE data_date = '{run['data_date']}'")
    ch.cmd(f"""INSERT INTO n360.forecast_history
               SELECT toDate('{run['data_date']}'), toUUID('{run['run_id']}'), account, series_key, pool_id, horizon,
                      rate_per_day, forecast_m3, billed, consumed_engaged, cross_date, exhaust_date
               FROM n360.forecast""")
    snapshot_risk(ch, run['data_date'])
    print(f'LOADED run {run["run_id"]}  data {run["data_date"]}  window end {run["window_end"]}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print('ERROR:', e)
        sys.exit(2)
