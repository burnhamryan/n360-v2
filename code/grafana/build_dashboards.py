#!/usr/bin/env python3
"""Generate the N360 Grafana dashboards as JSON. Dashboards are code: edit this file, run it,
commit the JSON. Grafana reloads grafana/dashboards/ every 30 s and refuses edits made in the UI.

    python3 code/grafana/build_dashboards.py

Wording rules carried over from the page: plain words on the panels, provenance in each panel's
description (the (i)), and "Not reported yet" wherever a value was not read. Never red for empty.

Layout of this file: settings (one place each) -> building blocks (target, panel, describe,
variables, header, links, formats, colours) -> one function per dashboard -> main().
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'dashboards')

# ───────────────────────── settings: each value is written once, here ─────────────────────────

DS = {'type': 'grafana-clickhouse-datasource', 'uid': 'n360-clickhouse'}
NOT_READ = 'Not reported yet'
ACCT = "account IN (${account:singlequote})"
HOME = ('n360-overview', 'Portfolio overview')      # every dashboard's Back link and header link
PIPELINE = ('n360-pipeline', 'Pipeline health')     # "Data through" drills here (04 conventions)
DEFAULT_RANGE = {'from': 'now-90d', 'to': 'now'}

# Pace colours: PROPOSED, awaiting Ryan (04_DASHBOARDS, 08_DECISIONS). Change them here only.
PACE = {
    'amber_ahead_pts': 10,      # amber: % of billed more than this many points ahead of % of term
    'red_pct_billed': 100,      # red: above this % of billed
    'amber_cross_days': 60,     # amber: crossing billed within this many days
    'red_cross_days': 30,       # red: crossing billed within this many days
}

# Display colours (Grafana colour names). Empty is grey, never red.
COLOUR = {'green': 'green', 'amber': 'orange', 'red': 'red', 'grey': '#8e8e8e', 'none': 'text'}

# Words for run status, as stored in n360.runs.status
RUN_STATUS = {'loaded': ('Loaded', 'green'), 'failed_checks': ('Stopped: data checks failed', 'red')}


# ───────────────────────── building blocks ─────────────────────────

def target(sql, fmt='table'):
    # clickhouse plugin format: 1 = table, 0 = time series
    return {'datasource': DS, 'editorType': 'sql', 'queryType': fmt, 'format': 1 if fmt == 'table' else 0,
            'rawSql': sql.strip(), 'refId': 'A'}


def describe(definition, source, filter, window):
    """Panel description (the (i)) in one shape everywhere: what it is, then provenance (rule 5)."""
    return f'{definition}\n\n**Source:** {source}  \n**Filter:** {filter}  \n**Window:** {window}'


def panel(pid, title, typ, sql, x, y, w, h, desc, fmt='table', field=None, options=None):
    return {'id': pid, 'title': title, 'type': typ, 'datasource': DS, 'description': desc,
            'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'targets': [target(sql, fmt)],
            'fieldConfig': field or field_config(), 'options': options or {}}


def field_config(overrides=(), **defaults):
    """fieldConfig with "Not reported yet" for empty values unless a panel says otherwise."""
    return {'defaults': {'noValue': NOT_READ, **defaults}, 'overrides': list(overrides)}


def override(column, *properties):
    return {'matcher': {'id': 'byName', 'options': column},
            'properties': [{'id': k, 'value': v} for k, v in properties]}


def pct(column):
    """A 0-100 percentage column shown to 1 dp (rule 14)."""
    return override(column, ('unit', 'percent'), ('decimals', 1))


STAT_LAST = {'reduceOptions': {'calcs': ['lastNotNull']}, 'colorMode': 'none'}


# Variables

def query_var(name, label, sql, multi=True, include_all=True):
    v = {'name': name, 'label': label, 'type': 'query', 'datasource': DS, 'query': sql, 'definition': sql,
         'multi': multi, 'includeAll': include_all, 'refresh': 1}
    if include_all:
        v['current'] = {'text': 'All', 'value': '$__all'}
    # keep key order stable for diffs: current before refresh, as the dashboards were first written
    order = ['name', 'label', 'type', 'datasource', 'query', 'definition', 'multi', 'includeAll', 'current', 'refresh']
    return {k: v[k] for k in order if k in v}


def var_account():
    return query_var('account', 'Customer', 'SELECT DISTINCT account FROM n360.contract_line ORDER BY account')


# Links

def drill(uid, var, column, title):
    """Data link from a table row or series to another dashboard, carrying the time range (04 conventions)."""
    ref = column if column.isidentifier() else f'["{column}"]'
    sep = '.' if column.isidentifier() else ''
    return {'title': title, 'url': f'/d/{uid}?var-{var}=${{__data.fields{sep}{ref}}}&${{__url_time_range}}'}


def with_links(column, *links):
    return override(column, ('links', list(links)))


def back_link(to=HOME):
    uid, title = to
    return {'type': 'link', 'title': f'Back to {title}', 'url': f'/d/{uid}', 'icon': 'dashboard',
            'keepTime': True, 'includeVars': False, 'targetBlank': False, 'asDropdown': False, 'tags': []}


# Colours

def steps(*pairs):
    """Grafana threshold steps from (value, colour key) pairs; the first value is None (base)."""
    return {'mode': 'absolute', 'steps': [{'value': v, 'color': COLOUR[c]} for v, c in pairs]}


def pct_billed_colours():
    """% of billed: red above PACE['red_pct_billed'], otherwise no colour (the pace chip carries green/amber)."""
    return steps((None, 'none'), (PACE['red_pct_billed'], 'red'))


def pace_gap_colours():
    """Pace gap (% of billed - % of term, points): green up to the amber margin, amber beyond it.
    Red (above billed, or crossing soon) needs the forecast, so it comes from the view's pace label."""
    return steps((None, 'green'), (PACE['amber_ahead_pts'], 'amber'))


def empty_grey():
    """Value mapping: empty shows "Not reported yet" in grey. Without it Grafana paints empty cells in the
    base threshold colour (green for pace), which reads as "fine" (rule 1)."""
    return {'type': 'special', 'options': {'match': 'null', 'result': {'text': NOT_READ, 'color': COLOUR['grey'], 'index': 0}}}


def coloured(column, thresholds, cell='color-text'):
    """Table column coloured by thresholds, empty in grey. Use for every coloured column."""
    return override(column, ('thresholds', thresholds), ('mappings', [empty_grey()]),
                    ('custom.cellOptions', {'type': cell}))


def label_colours(labels):
    """Value mappings for a text column holding colour keys or statuses: {stored: (shown, colour key)}."""
    return [{'type': 'value', 'options': {k: {'text': t, 'color': COLOUR[c], 'index': i}
                                          for i, (k, (t, c)) in enumerate(labels.items())}}]


# Header (Data through, last load status) for the top row of every dashboard

def data_through(pid, x, y, w, h, link=True):
    links = [{'title': PIPELINE[1], 'url': f'/d/{PIPELINE[0]}?${{__url_time_range}}'}] if link else []
    return panel(pid, 'Data through', 'stat', """
        SELECT formatDateTime(window_end, '%e %b %Y') AS data_through
        FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC LIMIT 1""", x, y, w, h,
        describe('Last day included in this load (the nightly window ends at 23:59 UTC that day).',
                 'n360.runs, written by load.py only after every data check passes',
                 'loaded runs only; the latest', 'the latest load'),
        field=field_config(links=links) if links else None,
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value', 'colorMode': 'none'})


def last_load(pid, x, y, w, h):
    return panel(pid, 'Last night', 'stat', """
        SELECT toString(status) AS status FROM n360.runs ORDER BY loaded_at DESC LIMIT 1""", x, y, w, h,
        describe('Result of the latest nightly run. A stopped night leaves the previous good night on screen.',
                 'n360.runs (load.py writes one row per night, loaded or not)', 'none', 'the latest run'),
        field=field_config(mappings=label_colours(RUN_STATUS)),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value',
                 'colorMode': 'background'})


def header(first_id=1, y=0):
    """Top row of every dashboard: Data through (drills to Pipeline health) and last night's result."""
    return [data_through(first_id, 0, y, 6, 3), last_load(first_id + 1, 6, y, 6, 3)]


def dashboard(uid, title, panels, variables=(), links=()):
    return {
        'uid': uid, 'title': title, 'tags': ['n360'], 'editable': False,
        'schemaVersion': 39, 'version': 1, 'timezone': 'utc',
        'time': dict(DEFAULT_RANGE),
        'templating': {'list': list(variables)},
        'panels': panels,
        **({'links': list(links)} if links else {}),
    }


# ───────────────────────── dashboards ─────────────────────────

def contracts():
    # B4 is a refactor only: same panels and queries as before. Header, Back link, data links and
    # pace colours are B10.
    p = []
    p.append(data_through(1, 0, 0, 6, 3, link=False))
    p.append(panel(2, 'Lines above billed', 'stat', f"""
        SELECT countIf(pct_billed > 100) AS above FROM n360.v_line_status WHERE {ACCT}""", 6, 0, 6, 3,
        describe('Contract lines whose measured count is over the billed amount. The measure follows the contract: '
                 'a contract rule where one is mapped, otherwise the platform engaged flag (billable unit unconfirmed). '
                 'Pooled lines are counted once, as their pool.',
                 'n360.v_line_status', 'Customer', 'each line from its start date to the data-through day'),
        options=STAT_LAST))
    p.append(panel(3, 'Contract lines', 'table', f"""
        SELECT a.display_name AS Customer, l.channel AS Channel, l.measure AS Measure,
               l.consumed AS Used, l.billed AS Billed, l.pct_billed AS `% of billed`,
               l.pct_term_elapsed AS `% of term`, l.start_date AS Start, l.end_date AS End, l.unit AS `Contract unit`
        FROM n360.v_line_status AS l INNER JOIN n360.account AS a ON a.account = l.account
        WHERE l.pool_id IS NULL AND l.{ACCT}
        ORDER BY Customer, Channel""", 0, 3, 24, 11,
        describe('One row per contract line. Used = the contract measure. '
                 'An empty cell means the line was not read or is not measurable, never zero.',
                 'the platform daily conversation aggregate table and the conversation records reader; contract terms '
                 'from the entitlement sheet unless a signed SOW overrides it (Contract unit). '
                 'Via n360.v_line_status and n360.account',
                 'Customer; lines not in a pool (pooled lines are in the table below)',
                 'Used: from the start date to the data-through day'),
        field=field_config([pct('% of billed'), pct('% of term')], custom={'align': 'auto'})))
    p.append(panel(4, 'Pooled entitlements', 'table', f"""
        SELECT name AS Pool, billed AS Billed, consumed AS Used, pct_billed AS `% of billed`, start_date AS Start, end_date AS End
        FROM n360.v_pool_status WHERE {ACCT}""", 0, 14, 24, 4,
        describe('One entitlement shared by several lines of one account. '
                 'Lines in a pool are never compared with the billed amount one by one.',
                 'consumption_mapping.json "pools", via n360.v_pool_status', 'Customer',
                 'Used: the pooled lines, each from its start date to the data-through day')))
    p.append(panel(5, 'Daily use per contract line', 'timeseries', f"""
        SELECT toDateTime(d.day) AS time, concat(l.account, ' · ', l.channel) AS line, d.measured AS used
        FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
        WHERE d.kind = 'line' AND d.{ACCT} AND $__dateFilter(d.day)
        ORDER BY time""", 0, 18, 24, 9,
        describe('Daily contract measure per line (conversations for rules that bill every conversation, billable for '
                 'meaningful-engagement rules, engaged otherwise). Days not read show as gaps, not zero.',
                 'n360.line_day with n360.contract_line', 'Customer; contract lines only', 'dashboard time range, per day'),
        fmt='timeseries', field=field_config(custom={'spanNulls': False, 'lineWidth': 1})))
    p.append(panel(6, 'Containment per VA (weekly)', 'timeseries', f"""
        SELECT toDateTime(toMonday(d.day)) AS time, concat(v.va_name, ' (', arrayStringConcat(v.channels, ', '), ')') AS va,
               1 - sum(d.handoffs) / sum(d.engaged) AS containment
        FROM n360.va_day AS d INNER JOIN n360.va AS v ON v.bot_id = d.bot_id
        WHERE d.{ACCT} AND d.handoffs IS NOT NULL AND $__dateFilter(d.day)
        GROUP BY time, va HAVING sum(d.engaged) > 0 ORDER BY time""", 0, 27, 24, 8,
        describe('containment = 1 - handoffs / engaged conversations, per VA and week. '
                 'One line per VA, so channels are never combined. VAs whose handoffs are not read (conversation records '
                 'reader) are not shown, rather than shown as zero.',
                 'the platform daily conversation aggregate table (n360.va_day, n360.va)',
                 'Customer; VAs with handoff data; weeks with engaged conversations',
                 'dashboard time range, by week (Monday start)'),
        fmt='timeseries',
        field=field_config(unit='percentunit', min=0, max=1, custom={'spanNulls': False})))
    return dashboard('n360-contracts', 'N360 · Contracts', p, [var_account()])


DASHBOARDS = [contracts]


def main():
    os.makedirs(OUT, exist_ok=True)
    for build in DASHBOARDS:
        d = build()
        path = os.path.join(OUT, f"{d['uid']}.json")
        with open(path, 'w') as f:
            json.dump(d, f, indent=2)
        print('wrote', os.path.relpath(path, os.path.dirname(HERE)), f"({len(d['panels'])} panels)")


if __name__ == '__main__':
    main()
