#!/usr/bin/env python3
"""Generate the N360 Grafana dashboards as JSON. Dashboards are code: edit this file, run it,
commit the JSON. Grafana reloads grafana/dashboards/ every 30 s and refuses edits made in the UI.

    python3 code/grafana/build_dashboards.py

Wording rules carried over from the page: plain words on the panels, provenance in each panel's
description (the (i)), and "Not reported yet" wherever a value was not read. Never red for empty.

Layout of this file: settings (one place each) -> building blocks (target, panel, describe,
variables, header, links, formats, colours from view labels) -> one function per dashboard -> main().
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
UID = {'overview': 'n360-overview', 'contracts': 'n360-contracts', 'customer': 'n360-customer', 'line': 'n360-line',
       'va': 'n360-va', 'forecast': 'n360-forecast', 'anomalies': 'n360-anomalies'}   # never change (links depend on them)
CROSS_WINDOW_DAYS = 90      # Portfolio "Crossing billed in 90 days" (spec 1.4): a display window, not a pace rule
TOP_CUSTOMERS = 5           # Portfolio "Conversations per week": top 5 + Other (spec 1.7)

# Pace: n360.v_line_pace decides the colour (decided 24 Nov 2026) and holds the PROPOSED thresholds.
# This file only says how each of the view's `pace` values looks: {stored: (shown, colour key)}.
PACE_LABEL = {
    'red': ('Red', 'red'),              # the reason is in the view's pace_reason, shown beside the chip
    'amber': ('Amber', 'amber'),
    'green': ('Green', 'green'),
    'not measurable': ('Not measurable', 'grey'),
    'not implemented': ('Not implemented', 'grey'),
    'not read': (NOT_READ, 'grey'),
}

# Display colours (Grafana colour names). Empty is grey, never red.
COLOUR = {'green': 'green', 'amber': 'orange', 'red': 'red', 'grey': '#8e8e8e', 'none': 'text', 'neutral': 'blue'}

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


def panel(pid, title, typ, sql, x, y, w, h, desc, fmt='table', field=None, options=None, links=None):
    p = {'id': pid, 'title': title, 'type': typ, 'datasource': DS, 'description': desc,
         'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'targets': [target(sql, fmt)],
         'fieldConfig': field or field_config(), 'options': options or {}}
    if links:
        p['links'] = list(links)
    return p


def date_sql(col):
    """SQL for a date shown as "4 Dec 2026" (rule 14; formatDateTime's %e pads single-digit days)."""
    return f"concat(toString(toDayOfMonth({col})), ' ', formatDateTime({col}, '%b %Y'))"


def field_config(overrides=(), **defaults):
    """fieldConfig with "Not reported yet" for empty values unless a panel says otherwise."""
    return {'defaults': {'noValue': NOT_READ, **defaults}, 'overrides': list(overrides)}


def override(column, *properties):
    return {'matcher': {'id': 'byName', 'options': column},
            'properties': [{'id': k, 'value': v} for k, v in properties]}


def count_col(column):
    """A count shown with thousands separators (rule 14)."""
    return override(column, ('unit', 'locale'), ('decimals', 0))


def width(column, px):
    return override(column, ('custom.width', px))


def hidden(column):
    """A column kept for links (keys) but not shown (rule 13: internal IDs not on screen)."""
    return override(column, ('custom.hidden', True))


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


def panel_link(key, title):
    """Panel-level link (the panel's link menu) to another dashboard, keeping the time range."""
    return {'title': title, 'url': f'/d/{UID[key]}?${{__url_time_range}}'}


def path_link(column, title):
    """Data link whose path a query built (when one panel drills to different dashboards per row,
    e.g. a line to Contract line, a pool to Customer). Grafana encodes the values; the query must not (it would be encoded twice)."""
    return {'title': title, 'url': f'${{__data.fields.{column}}}&${{__url_time_range}}'}


def with_links(column, *links):
    return override(column, ('links', list(links)))


def back_link(to=HOME):
    uid, title = to
    return {'type': 'link', 'title': f'Back to {title}', 'url': f'/d/{uid}', 'icon': 'dashboard',
            'keepTime': True, 'includeVars': False, 'targetBlank': False, 'asDropdown': False, 'tags': []}


# Colours

def empty_grey():
    """Value mapping: empty shows "Not reported yet" in grey. Without it Grafana paints empty cells in the
    base colour, which reads as "fine" (rule 1)."""
    return {'type': 'special', 'options': {'match': 'null', 'result': {'text': NOT_READ, 'color': COLOUR['grey'], 'index': 0}}}


def coloured(column, labels, cell='color-text'):
    """Table column coloured by the label a view stored, empty in grey. Use for every coloured column.
    Colours come from labels, never from thresholds here: rules live in views (rule 4)."""
    return override(column, ('mappings', label_colours(labels) + [empty_grey()]), ('custom.cellOptions', {'type': cell}))


def pace_chip(column='Pace'):
    """The pace column from n360.v_line_pace / v_customer_attention, as a coloured word."""
    return coloured(column, PACE_LABEL, cell='color-background')


def red_when_any():
    """A count stat: red when above zero, uncoloured at zero (spec 1.3)."""
    return [{'type': 'value', 'options': {'0': {'color': COLOUR['none'], 'index': 0}}},
            {'type': 'range', 'options': {'from': 1, 'to': 1e15, 'result': {'color': COLOUR['red'], 'index': 1}}}]


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


def header(first_id=1, y=0, w=6):
    """Top row of every dashboard: Data through (drills to Pipeline health) and last night's result."""
    return [data_through(first_id, 0, y, w, 3), last_load(first_id + 1, w, y, w, 3)]


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


def overview():
    """1. Portfolio overview: which customers need attention this week, and why? (04, spec 1). The home page:
    no Back link. Colours come from n360.v_line_pace (pace) and v_customer_attention; nothing is re-ruled here."""
    stat = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value', 'colorMode': 'none'}
    p = header(1, 0, w=8)
    p.append(panel(3, 'Customers', 'stat', """
        SELECT (SELECT count() FROM n360.account) AS Customers,
               (SELECT count() FROM n360.contract_line) AS `Contract lines`,
               (SELECT count() FROM n360.pool) AS Pools""", 16, 0, 8, 3,
        describe('Customers in N360, with their contract lines and pooled entitlements. Includes customers with '
                 'VAs but no contract line.', 'n360.account, n360.contract_line, n360.pool', 'none', 'the latest load'),
        field=field_config(unit='locale'),
        options={**stat, 'textMode': 'value_and_name', 'text': {'titleSize': 12, 'valueSize': 30}}))
    p.append(panel(4, 'Lines above billed', 'stat', """
        SELECT countIf(pct_billed > 100) AS above FROM n360.v_line_pace""", 0, 3, 8, 3,
        describe('Lines and pools whose use is over the billed amount, each in its own contract measure. '
                 'A pool is counted once, as the pool; its lines are never compared with billed alone.',
                 'n360.v_line_pace', 'none (every customer)', 'each from its start date to the data-through day'),
        field=field_config(mappings=red_when_any()), options={**stat, 'colorMode': 'background'},
        links=[panel_link('contracts', 'Contracts')]))
    p.append(panel(5, f'Crossing billed in {CROSS_WINDOW_DAYS} days', 'stat', f"""
        SELECT countIf(days_to_cross BETWEEN 0 AND {CROSS_WINDOW_DAYS}) AS crossing FROM n360.v_line_pace""", 8, 3, 8, 3,
        describe(f'Lines and pools under billed today that the stored forecast says will cross billed within '
                 f'{CROSS_WINDOW_DAYS} days, before their term ends. Already above billed is counted in "Lines above billed".',
                 'n360.forecast (straight line at the last-30-day rate, never recomputed), via n360.v_line_pace',
                 'none (every customer)', f'next {CROSS_WINDOW_DAYS} days from the data-through day'),
        options=stat, links=[panel_link('forecast', 'Forecast and runway')]))
    p.append(panel(6, 'Drops or silences, 7 settled days', 'stat', """
        SELECT sum(drops_silences_7d) AS flags FROM n360.v_customer_attention""", 16, 3, 8, 3,
        describe('VA-days flagged as a drop (z of -4 or lower) or went silent (0 conversations), on settled days only. '
                 'Provisional days (the newest 3) are not counted.',
                 'n360.v_va_day_anomaly, via n360.v_customer_attention', 'none (every customer)',
                 'the 7 newest settled days (data-through minus 9 to minus 3)'),
        options=stat, links=[panel_link('anomalies', 'Anomalies')]))
    p.append(panel(7, 'Customers needing attention', 'table', f"""
        SELECT display_name AS Customer, account, worst_label AS `Worst line`, measure AS Measure,
               pct_billed AS `% of billed`, pct_term AS `% of term`, pace AS Pace, pace_reason AS Why,
               multiIf(cross_date IS NOT NULL, {date_sql('cross_date')}, pct_billed IS NULL, NULL,
                       pct_billed > 100, 'Already above billed', 'Not before term end') AS `Crosses billed`,
               drops_silences_7d AS `Drops 7d`
        FROM n360.v_customer_attention
        WHERE pace IN ('red', 'amber') OR drops_silences_7d > 0
        ORDER BY pace_rank DESC, pace_gap DESC, Customer""", 0, 6, 24, 9,
        describe('Customers with a line or pool off pace (red or amber), or a drop or silence on a settled day. '
                 'Worst line = the customer\'s most off-pace line or pool (red, then amber, then the larger pace gap). '
                 'Pace colours are proposed, set in n360.v_line_pace. Sorted red first, then by pace gap. '
                 'Drops 7d = settled drops or silences in the 7 newest settled days (the newest 3 days are '
                 'provisional and not counted). Click a customer to open it.',
                 'n360.v_customer_attention (pace from n360.v_line_pace and n360.forecast; flags from n360.v_va_day_anomaly)',
                 'red or amber pace, or a settled drop or silence',
                 'use: start date to the data-through day; flags: the 7 newest settled days'),
        field=field_config([
            hidden('account'), pct('% of billed'), pct('% of term'), pace_chip('Pace'),
            width('Customer', 140), width('Worst line', 130), width('Measure', 185), width('% of billed', 90),
            width('% of term', 80), width('Pace', 65), width('Why', 235), width('Crosses billed', 145), width('Drops 7d', 75),
            override('Crosses billed', ('mappings', [empty_grey()])),
            override('Worst line', ('mappings', [empty_grey()])),
            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer')),
        ], custom={'align': 'auto'})))
    p.append(panel(8, 'Conversations per week, top customers', 'timeseries', f"""
        WITH top AS (SELECT account FROM n360.va_day WHERE $__dateFilter(day)
                     GROUP BY account ORDER BY sum(conversations) DESC LIMIT {TOP_CUSTOMERS})
        SELECT toDateTime(toMonday(d.day)) AS time,
               if(d.account IN (SELECT account FROM top), a.display_name, 'Other') AS customer,
               if(d.account IN (SELECT account FROM top), d.account, '') AS account,
               if(countIf(d.conversations IS NULL) > 0, NULL, sum(d.conversations)) AS conversations
        FROM n360.va_day AS d INNER JOIN n360.account AS a ON a.account = d.account
        WHERE $__dateFilter(d.day) AND toMonday(d.day) >= toDate($__fromTime)
          AND toMonday(d.day) + 6 <= (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded')
        GROUP BY time, customer, account ORDER BY time""", 0, 15, 10, 14,
        describe(f'Conversations started per week (Monday to Sunday), for the {TOP_CUSTOMERS} customers with the most '
                 f'conversations in the time range, and every other customer together as Other. Conversations are the '
                 f'platform count, not a contract measure. A week with any day not read shows as a gap, not a lower bar. '
                 f'Only complete weeks. Click a bar to open the customer.',
                 'n360.va_day (the platform daily conversation aggregate table and the conversation records reader)',
                 'none (every customer)', 'dashboard time range, complete weeks only'),
        fmt='timeseries',
        field=field_config(unit='locale', decimals=0, displayName='${__field.labels.customer}',
                           links=[{'title': 'Open customer',
                                   'url': f"/d/{UID['customer']}?var-account=${{__field.labels.account}}&${{__url_time_range}}"}],
                           custom={'drawStyle': 'bars', 'fillOpacity': 80, 'lineWidth': 1, 'spanNulls': False,
                                   'stacking': {'mode': 'normal', 'group': 'A'}}),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}))
    p.append(panel(9, 'Use against billed, by line', 'table', f"""
        SELECT if(l.entitlement = 'pool', l.label, concat(a.display_name, ' · ', l.label)) AS Line,
               l.pct_billed AS `% of billed`, l.pct_billed AS `Used`, l.pct_term AS `% of term`,
               if(l.entitlement = 'pool',
                  concat('/d/{UID['customer']}?var-account=', l.account),
                  concat('/d/{UID['line']}?var-line=', l.line_key, '&var-account=', l.account))
                   AS drill
        FROM n360.v_line_pace AS l INNER JOIN n360.account AS a ON a.account = l.account
        WHERE l.pct_billed IS NOT NULL
        ORDER BY l.pct_billed DESC""", 10, 15, 14, 14,
        describe('Each line and pool: use as % of billed, in its own contract measure. The bar stops at 100%; the '
                 'number beside it is the true %. % of term beside it (a tick mark on the bar is not available in '
                 'Grafana OSS). Not measurable, not implemented and unread lines are left out (see Contracts). '
                 'Click a line to open it; a pool opens its customer.',
                 'n360.v_line_pace', 'lines and pools with a % of billed', 'start date to the data-through day'),
        field=field_config([
            hidden('drill'), width('% of billed', 90), width('% of term', 90), pct('% of billed'), pct('% of term'),
            override('Used', ('unit', 'percent'), ('min', 0), ('max', 100), ('color', {'mode': 'fixed', 'fixedColor': COLOUR['neutral']}),
                     ('custom.cellOptions', {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'hidden'})),
            with_links('Line', path_link('drill', 'Open')),
        ], custom={'align': 'auto'})))
    return dashboard(UID['overview'], 'N360 · Portfolio overview', p)


DASHBOARDS = [overview, contracts]


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
