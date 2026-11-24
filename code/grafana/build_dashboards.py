#!/usr/bin/env python3
"""Generate the N360 Grafana dashboards as JSON. Dashboards are code: edit this file, run it,
commit the JSON. Grafana reloads grafana/dashboards/ every 30 s and refuses edits made in the UI.

    python3 clickhouse/grafana/build_dashboards.py

Wording rules carried over from the page: plain words on the panels, provenance in each panel's
description (the (i)), and "Not reported yet" wherever a value was not read. Never red for empty.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'dashboards')
DS = {'type': 'grafana-clickhouse-datasource', 'uid': 'n360-clickhouse'}
NOT_READ = 'Not reported yet'
ACCT = "account IN (${account:singlequote})"


def target(sql, fmt='table'):
    # clickhouse plugin format: 1 = table, 0 = time series
    return {'datasource': DS, 'editorType': 'sql', 'queryType': fmt, 'format': 1 if fmt == 'table' else 0,
            'rawSql': sql.strip(), 'refId': 'A'}


def panel(pid, title, typ, sql, x, y, w, h, desc, fmt='table', field=None, options=None):
    return {'id': pid, 'title': title, 'type': typ, 'datasource': DS, 'description': desc,
            'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'targets': [target(sql, fmt)],
            'fieldConfig': field or {'defaults': {'noValue': NOT_READ}, 'overrides': []},
            'options': options or {}}


def contracts():
    p = []
    p.append(panel(1, 'Data through', 'stat', """
        SELECT formatDateTime(window_end, '%e %b %Y') AS data_through
        FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC LIMIT 1""", 0, 0, 6, 3,
        'Last day included in this load (the nightly window ends at 23:59 UTC that day). '
        'Source: n360.runs, written by load.py only after every data check passes.',
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value', 'colorMode': 'none'}))
    p.append(panel(2, 'Lines above billed', 'stat', f"""
        SELECT countIf(pct_billed > 100) AS above FROM n360.v_line_status WHERE {ACCT}""", 6, 0, 6, 3,
        'Contract lines whose measured count is over the billed amount. The measure follows the contract: '
        'a contract rule where one is mapped, otherwise the platform engaged flag (billable unit unconfirmed). '
        'Pooled lines are counted once, as their pool.',
        options={'reduceOptions': {'calcs': ['lastNotNull']}, 'colorMode': 'none'}))
    p.append(panel(3, 'Contract lines', 'table', f"""
        SELECT a.display_name AS Customer, l.channel AS Channel, l.measure AS Measure,
               l.consumed AS Used, l.billed AS Billed, l.pct_billed AS `% of billed`,
               l.pct_term_elapsed AS `% of term`, l.start_date AS Start, l.end_date AS End, l.unit AS `Contract unit`
        FROM n360.v_line_status AS l INNER JOIN n360.account AS a ON a.account = l.account
        WHERE l.pool_id IS NULL AND l.{ACCT}
        ORDER BY Customer, Channel""", 0, 3, 24, 11,
        'One row per contract line (pooled lines are in the table below). Used = the contract measure from the start '
        'date to the data-through day. Source: the platform daily conversation aggregate table and the conversation records '
        'reader; contract terms from the entitlement sheet unless a signed SOW overrides it (Contract unit). '
        'An empty cell means the line was not read or is not measurable, never zero.',
        field={'defaults': {'noValue': NOT_READ, 'custom': {'align': 'auto'}},
               'overrides': [{'matcher': {'id': 'byName', 'options': '% of billed'},
                              'properties': [{'id': 'unit', 'value': 'percent'}, {'id': 'decimals', 'value': 1}]},
                             {'matcher': {'id': 'byName', 'options': '% of term'},
                              'properties': [{'id': 'unit', 'value': 'percent'}, {'id': 'decimals', 'value': 1}]}]}))
    p.append(panel(4, 'Pooled entitlements', 'table', f"""
        SELECT name AS Pool, billed AS Billed, consumed AS Used, pct_billed AS `% of billed`, start_date AS Start, end_date AS End
        FROM n360.v_pool_status WHERE {ACCT}""", 0, 14, 24, 4,
        'One entitlement shared by several lines of one account (consumption_mapping.json "pools"). '
        'Lines in a pool are never compared with the billed amount one by one.'))
    p.append(panel(5, 'Daily use per contract line', 'timeseries', f"""
        SELECT toDateTime(d.day) AS time, concat(l.account, ' · ', l.channel) AS line, d.measured AS used
        FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
        WHERE d.kind = 'line' AND d.{ACCT} AND $__dateFilter(d.day)
        ORDER BY time""", 0, 18, 24, 9,
        'Daily contract measure per line (conversations for rules that bill every conversation, billable for '
        'meaningful-engagement rules, engaged otherwise). Days not read show as gaps, not zero.', fmt='timeseries',
        field={'defaults': {'noValue': NOT_READ, 'custom': {'spanNulls': False, 'lineWidth': 1}}, 'overrides': []}))
    p.append(panel(6, 'Containment per VA (weekly)', 'timeseries', f"""
        SELECT toDateTime(toMonday(d.day)) AS time, concat(v.va_name, ' (', arrayStringConcat(v.channels, ', '), ')') AS va,
               1 - sum(d.handoffs) / sum(d.engaged) AS containment
        FROM n360.va_day AS d INNER JOIN n360.va AS v ON v.bot_id = d.bot_id
        WHERE d.{ACCT} AND d.handoffs IS NOT NULL AND $__dateFilter(d.day)
        GROUP BY time, va HAVING sum(d.engaged) > 0 ORDER BY time""", 0, 27, 24, 8,
        'containment = 1 - handoffs / engaged conversations, per VA and week, from the platform daily conversation aggregate table. '
        'One line per VA, so channels are never combined. VAs whose handoffs are not read (conversation records '
        'reader) are not shown, rather than shown as zero.', fmt='timeseries',
        field={'defaults': {'noValue': NOT_READ, 'unit': 'percentunit', 'min': 0, 'max': 1,
                            'custom': {'spanNulls': False}}, 'overrides': []}))
    return {
        'uid': 'n360-contracts', 'title': 'N360 · Contracts', 'tags': ['n360'], 'editable': False,
        'schemaVersion': 39, 'version': 1, 'timezone': 'utc',
        'time': {'from': 'now-90d', 'to': 'now'},
        'templating': {'list': [{
            'name': 'account', 'label': 'Customer', 'type': 'query', 'datasource': DS,
            'query': 'SELECT DISTINCT account FROM n360.contract_line ORDER BY account',
            'definition': 'SELECT DISTINCT account FROM n360.contract_line ORDER BY account',
            'multi': True, 'includeAll': True, 'current': {'text': 'All', 'value': '$__all'}, 'refresh': 1}]},
        'panels': p,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    for d in [contracts()]:
        path = os.path.join(OUT, f"{d['uid']}.json")
        with open(path, 'w') as f:
            json.dump(d, f, indent=2)
        print('wrote', os.path.relpath(path, os.path.dirname(HERE)), f"({len(d['panels'])} panels)")


if __name__ == '__main__':
    main()
