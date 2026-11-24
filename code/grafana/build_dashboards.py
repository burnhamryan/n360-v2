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
FORECAST_WINDOWS = (30, 60, 90)   # Forecast "Crossing billed" stat (spec 6): display windows
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
COLOUR = {'green': 'green', 'amber': 'orange', 'red': 'red', 'grey': '#8e8e8e', 'none': 'text', 'neutral': 'blue',
          'blue': 'blue', 'dark': '#3d3d3d', 'dark_red': 'dark-red', 'dark_blue': 'dark-blue', 'normal': '#5a5a5a',
          # provisional anomaly days are shown faded (spec 7)
          'red_faded': 'rgba(242, 73, 92, 0.35)', 'dark_red_faded': 'rgba(196, 22, 42, 0.35)', 'normal_faded': 'rgba(90, 90, 90, 0.35)',
          'dark_blue_faded': 'rgba(31, 96, 196, 0.35)', 'blue_faded': 'rgba(87, 148, 242, 0.35)'}

# Runway words (from n360.v_line_pace.runway), source freshness (v_source_freshness.status), heatmap bands (v_anomaly_heat.band)
RUNWAY_LABEL = {'above billed': ('Already above billed', 'red'), 'crosses soon': ('Crosses billed soon', 'red'),
                'crosses before term end': ('Crosses before term end', 'blue'),
                'runs out after term end': ('Runs out after term end', 'grey'),
                'no use in the last 30 days': ('No use in the last 30 days', 'grey'),
                'not measurable': ('Not measurable', 'grey'), 'not implemented': ('Not implemented', 'grey'),
                'not read': (NOT_READ, 'grey')}
FRESH_LABEL = {'green': ('On time', 'green'), 'amber': ('Behind', 'amber'), 'red': ('Late', 'red')}
HEAT_BANDS = [(-2, 'Drop or silent', 'red'), (-1, 'Low (z -4 to -2)', 'dark_red'), (0, 'Normal', 'normal'),
              (1, 'High (z 2 to 4)', 'dark_blue'), (2, 'Spike', 'blue')]
N_CHECKS = open(os.path.join(os.path.dirname(HERE), 'checks.sql')).read().count('-- check: ')   # the data checks, counted

# VA status words (VA dashboard): settled flags red, provisional orange (spec 4)
VA_STATUS = {'Normal': ('Normal', 'none'), 'Not scored': ('Not scored', 'grey'),
             **{f'{k}{p}': (f'{k}{p}', 'amber' if p else 'red') for k in ('Went silent', 'Drop', 'Spike')
                for p in ('', ' (provisional)')}}

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


def panel(pid, title, typ, sql, x, y, w, h, desc, fmt='table', field=None, options=None, links=None,
          may_be_empty=None, repeat=None):
    p = {'id': pid, 'title': title, 'type': typ, 'datasource': DS, 'description': desc,
         'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'targets': [target(sql, fmt)],
         'fieldConfig': field or field_config(), 'options': options or {}}
    if links:
        p['links'] = list(links)
    if may_be_empty:            # read by test_panels.py; Grafana ignores it
        p['n360MayBeEmpty'] = may_be_empty
    if repeat:                  # one copy per value of a variable; none when the variable is empty
        p['repeat'] = repeat
        p['repeatDirection'] = 'h'
    return p


def text_panel(pid, title, x, y, w, h, markdown):
    """A note (no query), e.g. a panel whose data is not built yet."""
    return {'id': pid, 'title': title, 'type': 'text', 'gridPos': {'x': x, 'y': y, 'w': w, 'h': h},
            'options': {'mode': 'markdown', 'content': markdown}}


def num_sql(col):
    """SQL for a whole number as text with thousands separators, e.g. 1,234,567 (rule 14), for text cells."""
    return (f"if({col} IS NULL, NULL, if({col} >= 1000000, concat(toString(intDiv({col}, 1000000)), ',', "
            f"leftPad(toString(intDiv({col}, 1000) % 1000), 3, '0'), ',', leftPad(toString({col} % 1000), 3, '0')), "
            f"if({col} >= 1000, concat(toString(intDiv({col}, 1000)), ',', leftPad(toString({col} % 1000), 3, '0')), "
            f"toString({col}))))")


DATA_THROUGH_SQL = "(SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded')"
VA_LABEL = "concat(v.va_name, ' (', arrayStringConcat(v.channels, ', '), ')')"   # VAs as "Name (Channel)" (03)
LINE_LABEL = "concat(l.channel, if(l.sheet_customer != l.account, concat(' (', l.sheet_customer, ')'), ''))"


def date_sql(col):
    """SQL for a date shown as "4 Dec 2026" (rule 14; formatDateTime's %e pads single-digit days)."""
    return f"concat(toString(toDayOfMonth({col})), ' ', formatDateTime({col}, '%b %Y'))"


def field_config(overrides=(), **defaults):
    """fieldConfig with "Not reported yet" for empty values unless a panel says otherwise."""
    return {'defaults': {'noValue': NOT_READ, **defaults}, 'overrides': list(overrides)}


def plain(fc):
    """No colour unless a value mapping gives one (Grafana's default scale is green, red above 80: meaningless here)."""
    fc['defaults'].setdefault('color', {'mode': 'fixed', 'fixedColor': COLOUR['none']})
    return fc


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


def var_one_account():
    """One customer (Customer, Contract line, VA): value = account key, shown as the display name."""
    return query_var('account', 'Customer', 'SELECT account, display_name FROM n360.account ORDER BY display_name',
                     multi=False, include_all=False)


def var_line():
    return query_var('line', 'Contract line', f"""SELECT l.line_key, concat({LINE_LABEL}, ' · from ',
        toString(toDayOfMonth(l.start_date)), ' ', formatDateTime(l.start_date, '%b %Y'))
        FROM n360.contract_line AS l WHERE l.account IN (${{account:singlequote}}) ORDER BY l.channel, l.start_date""",
                     multi=False, include_all=False)


def var_va():
    return query_var('va', 'VA', f"""SELECT toString(v.bot_id), {VA_LABEL}
        FROM n360.va AS v WHERE v.account IN (${{account:singlequote}}) ORDER BY 2""", multi=False, include_all=False)


def custom_var(name, label, choices):
    """Fixed choices [(shown, value)]; the first is the default."""
    opts = [{'text': t, 'value': v, 'selected': i == 0} for i, (t, v) in enumerate(choices)]
    return {'name': name, 'label': label, 'type': 'custom', 'query': ', '.join(f'{t} : {v}' for t, v in choices),
            'options': opts, 'current': {'text': choices[0][0], 'value': choices[0][1]}, 'multi': False, 'includeAll': False}


def hidden_var(name, sql):
    """A variable not shown on screen, e.g. to repeat a panel only when there is something to show."""
    v = query_var(name, name, sql, multi=True, include_all=True)
    v['hide'] = 2
    return v


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


def back_link(to=HOME, keep_vars=False):
    """'Back to ...' link at the top. keep_vars carries the customer (and line / VA) back up."""
    uid, title = to
    return {'type': 'link', 'title': f'Back to {title}', 'url': f'/d/{uid}', 'icon': 'dashboard',
            'keepTime': True, 'includeVars': keep_vars, 'targetBlank': False, 'asDropdown': False, 'tags': []}


CUSTOMER = ('n360-customer', 'Customer')


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
    return [{'type': 'value', 'options': {'0': {'color': 'transparent', 'index': 0}}},
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


def dashboard(uid, title, panels, variables=(), links=(), time=None):
    return {
        'uid': uid, 'title': title, 'tags': ['n360'], 'editable': False,
        'schemaVersion': 39, 'version': 1, 'timezone': 'utc',
        'time': dict(time or DEFAULT_RANGE),
        'templating': {'list': list(variables)},
        'panels': panels,
        **({'links': list(links)} if links else {}),
    }


# ───────────────────────── dashboards ─────────────────────────

def containment_weekly(pid, x, y, w, h, acct=ACCT, filter_text='Customer'):
    """Containment per VA per week (spec 2, 5, 8): one series per VA, channels never combined."""
    return panel(pid, 'Containment per VA (weekly)', 'timeseries', f"""
        SELECT toDateTime(toMonday(d.day)) AS time, concat(v.va_name, ' (', arrayStringConcat(v.channels, ', '), ')') AS va,
               1 - sum(d.handoffs) / sum(d.engaged) AS containment
        FROM n360.va_day AS d INNER JOIN n360.va AS v ON v.bot_id = d.bot_id
        WHERE d.{acct} AND d.handoffs IS NOT NULL AND $__dateFilter(d.day)
        GROUP BY time, va HAVING sum(d.engaged) > 0 ORDER BY time""", x, y, w, h,
        describe('containment = 1 - handoffs / engaged conversations, per VA and week. '
                 'One line per VA, so channels are never combined. VAs whose handoffs are not read (conversation records '
                 'reader) are not shown, rather than shown as zero.',
                 'the platform daily conversation aggregate table (n360.va_day, n360.va)',
                 f'{filter_text}; VAs with handoff data; weeks with engaged conversations',
                 'dashboard time range, by week (Monday start)'),
        fmt='timeseries',
        field=field_config(unit='percentunit', min=0, max=1, decimals=1, displayName='${__field.labels.va}',
                           custom={'spanNulls': False}))




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


STATS = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value_and_name', 'colorMode': 'none',
         'text': {'titleSize': 12, 'valueSize': 26}}
ONE_ACCT = "account IN (${account:singlequote})"
STATS_TEXT = {**STATS, 'text': {'titleSize': 12, 'valueSize': 18}}   # stats that carry words
AT_POOL = {'type': 'special', 'options': {'match': 'null', 'result': {'text': 'At the pool', 'color': 'transparent', 'index': 0}}}
TIMELINE = {'noValue': '', 'custom': {'spanNulls': True}}
LINE = "line_key IN (${line:singlequote})"
VA = "toString(bot_id) IN (${va:singlequote})"


def customer():
    """2. Customer (spec 2): one customer, every line, pool, VA and flag. Drills: line -> Contract line, VA -> VA."""
    p = header(1, 0, w=6)
    p.append(panel(3, 'Contract lines', 'stat', f"""
        SELECT count() AS `Contract lines`, countIf(measure = 'not implemented') AS `Not implemented`,
               countIf(measure = 'not measurable') AS `Not measurable`
        FROM n360.contract_line WHERE {ONE_ACCT}""", 12, 0, 6, 3,
        describe('Contract lines on the entitlement sheet for this customer, and how many have no usage to show: '
                 'not implemented (contracted, nothing built) or not measurable (unit the platform does not count).',
                 'n360.contract_line', 'Customer', 'the latest load'), options=STATS))
    p.append(panel(4, 'Anomalies, 7 settled days', 'stat', f"""
        SELECT drops_silences_7d AS `Drops or silences`, spikes_7d AS Spikes, provisional_flags AS Provisional
        FROM n360.v_customer_attention WHERE {ONE_ACCT}""", 18, 0, 6, 3,
        describe('VA-days flagged on the 7 newest settled days, and flags on the newest 3 days (provisional: may '
                 'still change, never an alert).', 'n360.v_va_day_anomaly via n360.v_customer_attention', 'Customer',
                 'the 7 newest settled days; provisional = the newest 3 days'), options=STATS))
    p.append(panel(5, 'Worst line', 'stat', f"""
        SELECT if(worst_entitlement = 'pool', 'Pooled entitlement', worst_label) AS Line, pct_billed AS `% of billed`, pct_term AS `% of term`, pace AS Pace
        FROM n360.v_customer_attention WHERE {ONE_ACCT}""", 0, 3, 8, 3,
        describe('The most off-pace line or pool (red, then amber, then the larger pace gap), in its own measure.',
                 'n360.v_customer_attention (from n360.v_line_pace)', 'Customer', 'start date to the data-through day'),
        field=plain(field_config([pct('% of billed'), pct('% of term'), override('Pace', ('mappings', label_colours(PACE_LABEL)))])),
        options={**STATS_TEXT, 'colorMode': 'value'}))
    p.append(panel(6, 'Total used against billed', 'stat', f"""
        SELECT if(same, sum(used), NULL) AS Used, if(same, sum(billed), NULL) AS Billed,
               if(same, round(100 * sum(used) / sum(billed), 1), NULL) AS `% of billed`,
               if(same, any(measure), 'Not added up: lines differ in measure or term') AS Measure
        FROM (SELECT *, (SELECT uniqExact(measure, start_date, end_date) = 1 FROM n360.v_line_pace
                         WHERE {ONE_ACCT} AND pct_billed IS NOT NULL) AS same
              FROM n360.v_line_pace WHERE {ONE_ACCT} AND pct_billed IS NOT NULL)
        GROUP BY same""", 8, 3, 8, 3,
        describe('Used against billed across this customer\'s lines and pools, only when they share one measure and '
                 'one term (rules 2 and 5): never across different measures or contracts. Not implemented, not measurable '
                 'and unread lines are left out. A pool counts once.',
                 'n360.v_line_pace', 'Customer; lines and pools with a % of billed', 'start date to the data-through day'),
        field=field_config([count_col('Used'), count_col('Billed'), pct('% of billed')], noValue='Not added up'),
        options=STATS_TEXT, may_be_empty='a customer with no measured line'))
    p.append(panel(7, 'Main line, last 30 days', 'stat', f"""
        SELECT argMax(if(entitlement = 'pool', 'Pooled entitlement', label), used) AS Line, argMax(round(rate_per_day), used) AS `Per day`,
               argMax(ifNull(step_change, 'none'), used) AS `Step change`
        FROM n360.v_line_pace WHERE {ONE_ACCT} AND rate_per_day IS NOT NULL""", 16, 3, 8, 3,
        describe('The line or pool with the most use: its rate per day over the last 30 days (the stored forecast\'s '
                 'rate), and whether that rate is a step change from its term average (more than 50% different, '
                 'flagged upstream).', 'n360.forecast via n360.v_line_pace', 'Customer', 'last 30 days to the data-through day'),
        field=field_config([count_col('Per day')]), options=STATS_TEXT, may_be_empty='a customer with no forecast'))
    p.append(panel(8, 'Contract lines', 'table', f"""
        SELECT label AS Line, measure AS Measure, used AS Used, billed AS Billed, pct_billed AS `% of billed`,
               pace AS Pace, pct_term AS `% of term`, forecast_m3 AS `Forecast to horizon`,
               multiIf(exhaust_date IS NOT NULL, {date_sql('exhaust_date')}, pct_billed > 100, 'Already above billed', NULL) AS `Runs out`,
               concat('/d/n360-line?var-account=', account, '&var-line=', line_key) AS drill
        FROM n360.v_line_pace WHERE {ONE_ACCT} AND entitlement = 'line'
        ORDER BY pace_rank DESC, label""", 0, 6, 24, 7,
        describe('Every contract line not in a pool, in its own measure. A not implemented or not measurable line '
                 'shows grey and "Not reported yet": it has no usage, never zero. Forecast to horizon and Runs out are '
                 'the stored forecast (straight line at the last-30-day rate). Click a line to open it.',
                 'n360.v_line_pace (n360.v_line_status, n360.forecast)', 'Customer; lines not in a pool (pools below)',
                 'start date to the data-through day; forecast to its horizon'),
        field=field_config([hidden('drill'), count_col('Used'), count_col('Billed'), count_col('Forecast to horizon'),
                            pct('% of billed'), pct('% of term'), pace_chip('Pace'), width('Pace', 70), width('Measure', 190),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('Used', '% of billed', 'Forecast to horizon',
                                                                                  'Runs out')],
                            with_links('Line', path_link('drill', 'Open line'))], custom={'align': 'auto'},
                           noValue='All lines are pooled: see Pooled entitlement below'),
        may_be_empty='a customer whose lines are all pooled'))
    p.append(panel(9, 'Pooled entitlement', 'table', f"""
        SELECT label AS Entitlement, used AS Used, toNullable(billed) AS Billed, pct_billed AS `% of billed`,
               pace AS Pace, pct_term AS `% of term`
        FROM (SELECT 0 AS o, label, used, billed, pct_billed, pace, pct_term
              FROM n360.v_line_pace WHERE {ONE_ACCT} AND entitlement = 'pool'
              UNION ALL
              SELECT 1, concat('  ', l.channel, ' (in the pool)'), s.consumed, NULL, NULL, NULL, NULL
              FROM n360.v_line_status AS s INNER JOIN n360.contract_line AS l ON l.line_key = s.line_key
              WHERE s.{ONE_ACCT} AND s.pool_id IS NOT NULL)
        ORDER BY o, label""", 0, 13, 24, 6,
        describe('One billed amount shared by several lines of this customer. Use is compared with billed only at the '
                 'pool; each line\'s own use is shown for information, never against billed. Shown only for customers '
                 'with a pool.', 'n360.v_line_pace, n360.v_line_status (consumption_mapping.json "pools")', 'Customer',
                 'start date to the data-through day'),
        field=field_config([count_col('Used'), count_col('Billed'), pct('% of billed'), pct('% of term'),
                            *[override(c, ('mappings', [AT_POOL])) for c in ('Billed', '% of billed', '% of term')],
                            override('Pace', ('mappings', label_colours(PACE_LABEL) + [AT_POOL]),
                                     ('custom.cellOptions', {'type': 'color-background'})), width('Pace', 90)],
                           custom={'align': 'auto'}, noValue='No pooled entitlement for this customer'),
        may_be_empty='a customer with no pool'))
    p.append(panel(10, 'Used per week, per line', 'timeseries', f"""
        SELECT toDateTime(toMonday(d.day)) AS time, {LINE_LABEL} AS line,
               if(countIf(d.measured IS NULL) > 0, NULL, sum(d.measured)) AS used
        FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
        WHERE d.kind = 'line' AND d.{ONE_ACCT} AND l.measure NOT IN ('not measurable', 'not implemented')
          AND $__dateFilter(d.day) AND toMonday(d.day) >= toDate($__fromTime) AND toMonday(d.day) + 6 <= {DATA_THROUGH_SQL}
        GROUP BY time, line ORDER BY time""", 0, 18, 12, 9,
        describe('Each line\'s contract measure per week, one series per line (never added up across lines). '
                 'Not measurable and not implemented lines have no usage and are left out. A week with an unread day is a '
                 'gap. Complete weeks only.', 'n360.line_day with n360.contract_line', 'Customer; measured lines',
                 'dashboard time range, complete weeks (Monday start)'),
        fmt='timeseries', field=field_config(unit='locale', decimals=0, displayName='${__field.labels.line}',
                                             custom={'spanNulls': False, 'lineWidth': 2})))
    p.append(containment_weekly(11, 12, 18, 12, 9, acct=ONE_ACCT))
    p.append(panel(12, 'VAs', 'table', f"""
        SELECT {VA_LABEL} AS VA, toString(v.region) AS Region, toString(v.consumption_source) AS Reader,
               if(countIf(d.conversations IS NULL) > 0, NULL, sum(d.conversations)) AS `Conversations, 30 days`,
               if(countIf(d.handoffs IS NOT NULL) = 0, NULL, 1 - sumIf(d.handoffs, d.handoffs IS NOT NULL) / sumIf(d.engaged, d.handoffs IS NOT NULL)) AS `Containment, 30 days`,
               any(f.n_settled) AS `Flags, 30 days`, any(f.n_provisional) AS `Provisional`,
               concat('/d/n360-va?var-account=', v.account, '&var-va=', toString(v.bot_id)) AS drill
        FROM n360.va AS v
        LEFT JOIN n360.va_day AS d ON d.bot_id = v.bot_id AND d.day > {DATA_THROUGH_SQL} - 30
        LEFT JOIN (SELECT bot_id, countIf(NOT provisional) AS n_settled, countIf(provisional) AS n_provisional
                   FROM n360.v_va_day_anomaly WHERE kind != 'normal' AND day > {DATA_THROUGH_SQL} - 30 GROUP BY bot_id) AS f
               ON f.bot_id = v.bot_id
        WHERE v.{ONE_ACCT}
        GROUP BY v.bot_id, v.account, v.va_name, v.channels, v.region, v.consumption_source ORDER BY VA""", 0, 27, 24, 6,
        describe('Every VA of this customer. Containment = 1 - handoffs / engaged, per VA; "Not reported yet" where '
                 'handoffs are not read (conversation records reader), never 0% or 100%. Flags = drops, silences and '
                 'spikes on settled days; provisional = the newest 3 days. Click a VA to open it.',
                 'n360.va, n360.va_day, n360.v_va_day_anomaly', 'Customer', 'the 30 days to the data-through day'),
        field=field_config([hidden('drill'), count_col('Conversations, 30 days'), count_col('Flags, 30 days'),
                            count_col('Provisional'),
                            override('Containment, 30 days', ('unit', 'percentunit'), ('decimals', 1), ('mappings', [empty_grey()])),
                            override('Conversations, 30 days', ('mappings', [empty_grey()])),
                            with_links('VA', path_link('drill', 'Open VA'))], custom={'align': 'auto'})))
    p.append(panel(13, 'Recent anomalies', 'table', f"""
        SELECT {date_sql('a.day')} AS Day, {VA_LABEL} AS VA, a.kind AS Kind, a.actual AS Actual, a.expected AS Expected,
               a.z AS z, if(a.provisional, 'Provisional', 'Settled') AS Status
        FROM n360.v_va_day_anomaly AS a INNER JOIN n360.va AS v ON v.bot_id = a.bot_id
        WHERE a.{ONE_ACCT} AND a.kind != 'normal' AND a.day > {DATA_THROUGH_SQL} - 30
        ORDER BY a.day DESC""", 0, 33, 24, 6,
        describe('Days a VA went silent, dropped (z of -4 or lower) or spiked (4 or higher) against the same weekday '
                 'over the previous 8 weeks. Provisional days (the newest 3) may still change and never alert.',
                 'n360.v_va_day_anomaly', 'Customer; flagged days only', 'the 30 days to the data-through day'),
        field=field_config([count_col('Actual'), count_col('Expected'),
                            override('Status', ('mappings', label_colours({'Provisional': ('Provisional', 'amber'),
                                                                          'Settled': ('Settled', 'red')})),
                                     ('custom.cellOptions', {'type': 'color-text'}))],
                           noValue='No flags in the last 30 days'),
        may_be_empty='a customer with no flags'))
    p.append(text_panel(14, 'Requests', 0, 39, 24, 3, 'Requests are Phase 2 (support tickets, `ps_ticket`): see the '
                        'Requests dashboard once built.'))
    return dashboard(UID['customer'], 'N360 · Customer', p, [var_one_account()], [back_link()])


def contract_line():
    """3. Contract line (spec 3): one line, how it is measured and where it is heading."""
    p = header(1, 0, w=4)
    p.append(panel(3, 'Used', 'stat', f"""
        SELECT reported_measured AS Used, reported_last30_measured AS `Last 30 days`
        FROM n360.contract_line WHERE {LINE}""", 8, 0, 4, 3,
        describe('The line\'s contract measure from its start date, and the last 30 days.', 'n360.contract_line',
                 'Contract line', 'start date to the data-through day'),
        field=field_config([count_col('Used'), count_col('Last 30 days')]), options=STATS))
    p.append(panel(4, 'Billed', 'stat', f"""
        SELECT billed AS Billed FROM n360.contract_line WHERE {LINE}""", 12, 0, 4, 3,
        describe('The billed amount for this line (after any signed SOW override; the basis is under "How this line is '
                 'measured"). A pooled line has none of its own.', 'n360.contract_line', 'Contract line', 'the contract term'),
        field=field_config([count_col('Billed')], noValue='None of its own'), options={**STATS, 'textMode': 'value'}))
    p.append(panel(5, '% of billed', 'stat', f"""
        SELECT pct_billed AS `% of billed`, pace AS Pace FROM n360.v_line_pace WHERE {LINE}""", 16, 0, 4, 3,
        describe('Used / billed in the line\'s own measure, and its pace colour (proposed rule, set in n360.v_line_pace). '
                 'A pooled line has no % of its own: see the pool on the Customer dashboard.',
                 'n360.v_line_pace', 'Contract line', 'start date to the data-through day'),
        field=plain(field_config([pct('% of billed'), override('Pace', ('mappings', label_colours(PACE_LABEL)))])),
        options={**STATS, 'colorMode': 'value'}, may_be_empty='a pooled line (measured at the pool)'))
    p.append(panel(6, '% of term', 'stat', f"""
        SELECT s.pct_term_elapsed AS `% of term`,
               toUInt32(round(s.pct_term_elapsed * l.term_days / 100)) AS `Days in`, l.term_days AS `Term days`
        FROM n360.v_line_status AS s INNER JOIN n360.contract_line AS l ON l.line_key = s.line_key WHERE s.{LINE}""", 20, 0, 4, 3,
        describe('Days elapsed / term days.', 'n360.v_line_status', 'Contract line', 'start date to the data-through day'),
        field=field_config([pct('% of term')]), options=STATS_TEXT))
    p.append(panel(7, 'Forecast to horizon', 'stat', f"""
        SELECT round(100 * forecast_m3 / billed, 1) AS `% of billed`, round(rate_per_day) AS `Per day`,
               {date_sql('horizon')} AS Horizon
        FROM n360.v_line_pace WHERE {LINE} AND forecast_m3 IS NOT NULL""", 0, 3, 8, 3,
        describe('The stored forecast: used + rate x days to the horizon (the earlier of contract end and the month end '
                 '3 months out), as % of billed; rate = last 30 days / 30. Never recomputed here.',
                 'n360.forecast via n360.v_line_pace', 'Contract line', 'to the horizon'),
        field=field_config([pct('% of billed'), count_col('Per day')], noValue='No forecast for this line'),
        options=STATS_TEXT, may_be_empty='no forecast (pooled, not implemented or not measurable)'))
    p.append(panel(8, 'How this line is measured', 'table', f"""
        SELECT Item, Value FROM (
            SELECT 1 AS o, 'Measure' AS Item, toString(l.measure) AS Value FROM n360.contract_line AS l WHERE l.{LINE}
            UNION ALL SELECT 2, 'Contract rule', if(l.rule_id IS NULL, 'None: engaged conversations (billable unit unconfirmed)', r.label)
                FROM n360.contract_line AS l LEFT JOIN n360.rule AS r ON r.rule_id = l.rule_id WHERE l.{LINE}
            UNION ALL SELECT 3, 'What counts', if(l.rule_id IS NULL, 'Conversations the platform flags as engaged', r.counts_plain)
                FROM n360.contract_line AS l LEFT JOIN n360.rule AS r ON r.rule_id = l.rule_id WHERE l.{LINE}
            UNION ALL SELECT 4, 'Rule status', if(l.rule_id IS NULL, 'no rule', toString(r.status))
                FROM n360.contract_line AS l LEFT JOIN n360.rule AS r ON r.rule_id = l.rule_id WHERE l.{LINE}
            UNION ALL SELECT 5, 'Conversations → engaged → measured',
                if(l.reported_measured IS NULL, NULL, concat({num_sql('l.reported_consumed_convs')}, ' → ',
                   {num_sql('l.reported_engaged_flag')}, ' → ', {num_sql('l.reported_measured')}))
                FROM n360.contract_line AS l WHERE l.{LINE}
            UNION ALL SELECT 6, 'Matches the page', if(l.reported_pct_page IS NULL OR s.pct_billed IS NULL, NULL,
                concat(if(abs(l.reported_pct_page - s.pct_billed) < 0.05, 'Yes', 'NO'), ': page ',
                       toString(round(l.reported_pct_page, 1)), '%, here ', toString(round(s.pct_billed, 1)), '%'))
                FROM n360.contract_line AS l INNER JOIN n360.v_line_status AS s ON s.line_key = l.line_key WHERE l.{LINE}
            UNION ALL SELECT 8, 'Billed basis', multiIf(l.pool_id IS NOT NULL, 'billed at the pool', l.billed IS NULL,
                'blank on the sheet', l.unit != '', concat('signed SOW: ', l.unit), 'entitlement sheet')
                FROM n360.contract_line AS l WHERE l.{LINE}
            UNION ALL SELECT 7, 'Source', ifNull(any(src.description), '')
                FROM n360.contract_line AS l LEFT JOIN n360.source AS src
                  ON src.source_id = if(l.rule_id IS NULL, 'consumption_days', 'consumption_rules') WHERE l.{LINE}
        ) ORDER BY o""", 8, 3, 16, 10,
        describe('How the contract measure for this line is counted, from conversations to the measure, and whether '
                 'this view\'s % of billed matches the existing page\'s figure.',
                 'n360.contract_line, n360.rule, n360.source, n360.v_line_status', 'Contract line', 'the contract term'),
        field=field_config([width('Item', 290), override('Value', ('mappings', [empty_grey()]))]),
        options={'showHeader': False}))
    p.append(panel(9, 'Cumulative used against billed', 'timeseries', f"""
        SELECT time, Used, Forecast, Billed FROM (
            SELECT toDateTime(d.day) AS time,
                   toNullable(toFloat64(sum(d.measured) OVER (ORDER BY d.day ROWS UNBOUNDED PRECEDING))) AS Used,
                   CAST(NULL, 'Nullable(Float64)') AS Forecast, CAST(NULL, 'Nullable(Float64)') AS Billed
            FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
            WHERE d.kind = 'line' AND l.{LINE} AND l.measure NOT IN ('not measurable', 'not implemented') AND d.day >= l.start_date
            UNION ALL SELECT toDateTime(data_through), NULL, toFloat64(used), NULL FROM n360.v_line_pace WHERE {LINE} AND forecast_m3 IS NOT NULL
            UNION ALL SELECT toDateTime(horizon), NULL, toFloat64(forecast_m3), NULL FROM n360.v_line_pace WHERE {LINE} AND forecast_m3 IS NOT NULL
            UNION ALL SELECT toDateTime(start_date), NULL, NULL, toFloat64(billed) FROM n360.contract_line WHERE {LINE} AND billed IS NOT NULL
            UNION ALL SELECT toDateTime(end_date), NULL, NULL, toFloat64(billed) FROM n360.contract_line WHERE {LINE} AND billed IS NOT NULL
        ) ORDER BY time""", 0, 13, 24, 9,
        describe('Used so far (solid), the stored forecast from the data-through day to its horizon (dashed), and the '
                 'billed amount (dashed red) over the term. Days not read add nothing to the running total. '
                 'Not measurable and not implemented lines have no usage line.',
                 'n360.line_day, n360.v_line_pace (n360.forecast), n360.contract_line', 'Contract line',
                 'start date to the end of the term'),
        fmt='timeseries',
        field=field_config([
            override('Forecast', ('custom.lineStyle', {'fill': 'dash', 'dash': [10, 10]}), ('custom.spanNulls', True),
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['neutral']})),
            override('Billed', ('custom.lineStyle', {'fill': 'dash', 'dash': [4, 6]}), ('custom.spanNulls', True), ('custom.lineWidth', 2),
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['red']})),
            override('Used', ('custom.lineWidth', 2), ('color', {'mode': 'fixed', 'fixedColor': COLOUR['green']}))],
            unit='locale', decimals=0, custom={'spanNulls': False})))
    p.append(panel(10, 'Measure against the engaged flag, by month', 'barchart', f"""
        SELECT formatDateTime(toStartOfMonth(d.day), '%b %y') AS Month, sum(d.measured) AS `Contract measure`,
               sum(d.engaged) AS `Engaged (platform flag)`
        FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
        WHERE d.kind = 'line' AND l.{LINE} AND d.day >= l.start_date
        GROUP BY toStartOfMonth(d.day) ORDER BY toStartOfMonth(d.day)""", 0, 22, 12, 9,
        describe('Per month: the contract measure beside the platform engaged flag, so a contract rule\'s effect is '
                 'visible (for lines with no rule they are the same). Never one replaced by the other.',
                 'n360.line_day', 'Contract line', 'start date to the data-through day, by calendar month'),
        field=field_config(unit='locale', decimals=0),
        options={'xField': 'Month', 'orientation': 'vertical', 'xTickLabelRotation': -45, 'xTickLabelSpacing': 0, 'groupWidth': 0.7, 'barWidth': 0.9, 'showValue': 'never',
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True}},
        may_be_empty='a line with no daily series (not implemented)'))
    p.append(panel(11, 'VAs on this line', 'table', f"""
        SELECT {VA_LABEL} AS VA, lv.region AS Region, sum(d.conversations) AS `Conversations, 30 days`,
               round(100 * sum(d.conversations) / sum(sum(d.conversations)) OVER (), 1) AS `Share`,
               concat('/d/n360-va?var-account=', lv.account, '&var-va=', toString(lv.bot_id)) AS drill
        FROM n360.line_va AS lv INNER JOIN n360.va AS v ON v.bot_id = lv.bot_id
        LEFT JOIN n360.va_day AS d ON d.bot_id = lv.bot_id AND d.day > {DATA_THROUGH_SQL} - 30
        WHERE lv.{LINE}
        GROUP BY lv.bot_id, lv.account, lv.region, v.va_name, v.channels ORDER BY `Conversations, 30 days` DESC""", 12, 22, 12, 9,
        describe('The VAs feeding this line and each one\'s share of the line\'s conversations (conversations, not the '
                 'contract measure). Click a VA to open it.', 'n360.line_va, n360.va_day', 'Contract line',
                 'the 30 days to the data-through day'),
        field=field_config([hidden('drill'), count_col('Conversations, 30 days'), pct('Share'), width('Share', 80),
                            width('Region', 80), width('Conversations, 30 days', 170),
                            with_links('VA', path_link('drill', 'Open VA'))]),
        may_be_empty='a line with no VA (not implemented)'))
    p.append(panel(12, '% of billed, night by night', 'timeseries', f"""
        SELECT toDateTime(data_date) AS time, pct_billed_page AS `% of billed`
        FROM n360.line_totals_history
        WHERE {LINE} AND (SELECT count(DISTINCT data_date) FROM n360.line_totals_history) >= 7
        ORDER BY time""", 0, 31, 24, 7,
        describe('What N360 said each night for this line, so a jump between nights is visible. Shown once 7 nights '
                 'are stored.', 'n360.line_totals_history (append-only, one row per line per night)', 'Contract line',
                 'every stored night'),
        fmt='timeseries', field=field_config(unit='percent', decimals=1, noValue='Shows once 7 nights are stored'),
        may_be_empty='fewer than 7 nights stored'))
    p[-1]['timeFrom'] = '45d'      # nightly history: the last weeks, not the whole term
    return dashboard(UID['line'], 'N360 · Contract line', p, [var_one_account(), var_line()],
                     [back_link(CUSTOMER, keep_vars=True), back_link()], time={'from': 'now-400d', 'to': 'now+120d'})


def va():
    """4. VA (spec 4): one VA against its own normal."""
    p = header(1, 0, w=4)
    p.append(panel(3, 'Status', 'stat', f"""
        SELECT if(count() = 0, 'Not scored',
                  if(argMax(kind, day) = 'normal', 'Normal',
                     concat(multiIf(argMax(kind, day) = 'went silent', 'Went silent', argMax(kind, day) = 'drop', 'Drop', 'Spike'),
                            if(argMax(provisional, day), ' (provisional)', '')))) AS Status
        FROM n360.v_va_day_anomaly WHERE {VA}""", 8, 0, 4, 3,
        describe('The newest scored day for this VA. Provisional (newest 3 days) may still change. "Not scored": the VA '
                 'has under 8 weeks of history or under 20 conversations on a typical weekday.',
                 'n360.v_va_day_anomaly', 'VA', 'the newest scored day'),
        field=field_config(mappings=label_colours(VA_STATUS)), options={**STATS, 'textMode': 'value', 'colorMode': 'background'}))
    p.append(panel(4, 'Last 30 days', 'stat', f"""
        SELECT if(countIf(conversations IS NULL) > 0, NULL, sum(conversations)) AS Conversations
        FROM n360.va_day WHERE {VA} AND day > {DATA_THROUGH_SQL} - 30""", 12, 0, 4, 3,
        describe('Conversations started in the 30 days to the data-through day. "Not reported yet" if any day was not read.',
                 'n360.va_day', 'VA', 'the 30 days to the data-through day'),
        field=field_config(unit='locale', decimals=0), options={**STATS, 'textMode': 'value'}))
    p.append(panel(5, 'Expected', 'stat', f"""
        SELECT argMax(expected, day) AS Expected FROM n360.v_va_day_anomaly WHERE {VA}""", 16, 0, 4, 3,
        describe('Expected conversations for the newest scored day: the median of the same weekday over the previous '
                 '8 weeks.', 'n360.v_va_day_anomaly', 'VA', 'previous 8 weeks, same weekday'),
        field=field_config(unit='locale', decimals=0, noValue='Not scored'), options={**STATS, 'textMode': 'value'}))
    p.append(panel(6, 'Containment', 'stat', f"""
        SELECT if(countIf(handoffs IS NOT NULL) = 0, NULL,
                  1 - sumIf(handoffs, handoffs IS NOT NULL) / sumIf(engaged, handoffs IS NOT NULL)) AS Containment
        FROM n360.va_day WHERE {VA} AND day > {DATA_THROUGH_SQL} - 30""", 20, 0, 4, 3,
        describe('1 - handoffs / engaged, for this VA only. "Not reported yet" where handoffs are not read.',
                 'n360.va_day', 'VA', 'the 30 days to the data-through day'),
        field=field_config(unit='percentunit', decimals=1), options={**STATS, 'textMode': 'value'}))
    p.append(panel(7, 'Daily conversations against the expected range', 'timeseries', f"""
        SELECT toDateTime(d.day) AS time, d.conversations AS Actual, a.expected AS Expected,
               a.expected_low AS `Expected range low`, a.expected_high AS `Expected range high`,
               if(a.kind NOT IN ('normal', '') AND NOT a.provisional, d.conversations, NULL) AS `Flag (settled)`,
               if(a.kind NOT IN ('normal', '') AND a.provisional, d.conversations, NULL) AS `Flag (provisional)`
        FROM n360.va_day AS d
        LEFT JOIN n360.v_va_day_anomaly AS a ON a.bot_id = d.bot_id AND a.day = d.day
        WHERE toString(d.bot_id) IN (${{va:singlequote}}) AND $__dateFilter(d.day)
        ORDER BY time
        SETTINGS join_use_nulls = 1""", 0, 3, 24, 10,
        describe('Each day\'s conversations (line), the expected value for that weekday (dots) and the range within '
                 'which a day is not flagged (shaded: expected +/- 4 spreads). Flags: red settled, orange provisional. '
                 'Days not scored have no expected value; days not read are gaps.',
                 'n360.va_day, n360.v_va_day_anomaly', 'VA', 'dashboard time range, daily'),
        fmt='timeseries',
        field=field_config([
            override('Actual', ('custom.lineWidth', 2), ('color', {'mode': 'fixed', 'fixedColor': COLOUR['neutral']})),
            override('Expected', ('custom.drawStyle', 'points'), ('custom.pointSize', 4),
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['grey']})),
            override('Expected range high', ('custom.fillBelowTo', 'Expected range low'), ('custom.lineWidth', 0),
                     ('custom.fillOpacity', 15), ('color', {'mode': 'fixed', 'fixedColor': COLOUR['grey']}),
                     ('custom.hideFrom', {'legend': True, 'tooltip': False, 'viz': False})),
            override('Expected range low', ('custom.lineWidth', 0), ('color', {'mode': 'fixed', 'fixedColor': COLOUR['grey']}),
                     ('custom.hideFrom', {'legend': True, 'tooltip': False, 'viz': False})),
            override('Flag (settled)', ('custom.drawStyle', 'points'), ('custom.pointSize', 10),
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['red']})),
            override('Flag (provisional)', ('custom.drawStyle', 'points'), ('custom.pointSize', 10),
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['amber']}))],
            unit='locale', decimals=0, custom={'spanNulls': False})))
    p.append(panel(8, 'Flags', 'table', f"""
        SELECT {date_sql('day')} AS Day, kind AS Kind, actual AS Actual, expected AS Expected, z AS z,
               if(provisional, 'Provisional', 'Settled') AS Status
        FROM n360.v_va_day_anomaly WHERE {VA} AND kind != 'normal' AND $__dateFilter(day) ORDER BY day DESC""", 0, 13, 12, 10,
        describe('Every flagged day for this VA in the time range. Provisional days may still change.',
                 'n360.v_va_day_anomaly', 'VA; flagged days', 'dashboard time range'),
        field=field_config([count_col('Actual'), count_col('Expected'), width('Day', 110), width('Kind', 100),
                            override('z', ('decimals', 1)),
                            override('Status', ('mappings', label_colours({'Provisional': ('Provisional', 'amber'),
                                                                          'Settled': ('Settled', 'red')})),
                                     ('custom.cellOptions', {'type': 'color-text'}))],
                           noValue='No flags in this time range'),
        may_be_empty='a VA with no flags'))
    p.append(panel(9, 'Reader and configuration', 'table', f"""
        SELECT Item, Value FROM (
            SELECT 1 AS o, 'Reader' AS Item, toNullable(toString(consumption_source)) AS Value FROM n360.va WHERE {VA}
            UNION ALL SELECT 2, 'Contract line', if(on_contract, arrayStringConcat(line_labels, ', '), 'None (unbilled VA)')
                FROM n360.va WHERE {VA}
            UNION ALL SELECT 3, 'Region', toString(region) FROM n360.va WHERE {VA}
            UNION ALL SELECT 4, 'Handoff data', if((SELECT countIf(handoffs IS NOT NULL) FROM n360.va_day WHERE {VA}) > 0,
                'Read', 'Not read (conversation records reader): containment not reported') 
            UNION ALL SELECT 5, 'Contract start', if(contract_start IS NULL, NULL, {date_sql('contract_start')}) FROM n360.va WHERE {VA}
            UNION ALL SELECT 6, 'Engine version', CAST(NULL, 'Nullable(String)')
            UNION ALL SELECT 7, 'V8 playbooks / legacy intents', NULL
            UNION ALL SELECT 8, 'MCP servers, supervisory rules', NULL
        ) ORDER BY o""", 12, 13, 12, 10,
        describe('Where this VA\'s counts come from. Configuration fields are Phase 2 (weekly configuration read) '
                 'and show "Not reported yet" until that read exists.', 'n360.va, n360.va_day', 'VA', 'the latest load'),
        field=field_config([width('Item', 290), override('Value', ('mappings', [empty_grey()]))]),
        options={'showHeader': False}))
    return dashboard(UID['va'], 'N360 · VA', p, [var_one_account(), var_va()],
                     [back_link(CUSTOMER, keep_vars=True), back_link()])


def contracts():
    """5. Contracts (spec 5, B10): every line and pool, with pace colours, header, links and Back link."""
    p = [data_through(1, 0, 0, 6, 3), last_load(7, 6, 0, 6, 3)]
    p.append(panel(2, 'Lines above billed', 'stat', f"""
        SELECT countIf(pct_billed > 100) AS above FROM n360.v_line_pace WHERE {ACCT}""", 12, 0, 12, 3,
        describe('Lines and pools whose use is over the billed amount, each in its own contract measure (a contract rule '
                 'where one is mapped, otherwise the platform engaged flag, billable unit unconfirmed). A pool is counted '
                 'once, as the pool.', 'n360.v_line_pace', 'Customer', 'each from its start date to the data-through day'),
        field=field_config(mappings=red_when_any()), options={**STAT_LAST, 'colorMode': 'background'}))
    p.append(panel(3, 'Contract lines', 'table', f"""
        SELECT a.display_name AS Customer, {LINE_LABEL} AS Line, l.measure AS Measure,
               s.consumed AS Used, s.billed AS Billed, s.pct_billed AS `% of billed`, s.pct_term_elapsed AS `% of term`,
               pc.pace AS Pace, concat({date_sql('l.start_date')}, ' to ', {date_sql('l.end_date')}) AS Term,
               l.unit AS `Contract unit`,
               l.account AS account, concat('/d/n360-line?var-account=', l.account, '&var-line=', l.line_key) AS drill
        FROM n360.v_line_status AS s
        INNER JOIN n360.contract_line AS l ON l.line_key = s.line_key
        INNER JOIN n360.account AS a ON a.account = l.account
        LEFT JOIN n360.v_line_pace AS pc ON pc.line_key = l.line_key
        WHERE s.pool_id IS NULL AND s.{ACCT}
        ORDER BY Customer, Line""", 0, 3, 24, 13,
        describe('One row per contract line not in a pool (pools below). Used = the contract measure. Not measurable and '
                 'not implemented lines have no usage ("Not reported yet", grey), never zero. Contract unit: the unit '
                 'of a signed SOW that overrides the entitlement sheet. Click a customer or a line to open it.',
                 'the platform daily conversation aggregate table and the conversation records reader; contract terms '
                 'from the entitlement sheet unless a signed SOW overrides it. Via n360.v_line_status, n360.v_line_pace',
                 'Customer; lines not in a pool', 'Used: from the start date to the data-through day'),
        field=field_config([hidden('account'), hidden('drill'), count_col('Used'), count_col('Billed'),
                            pct('% of billed'), pct('% of term'), pace_chip('Pace'), width('Pace', 70), width('Measure', 185),
                            width('Term', 200), width('Customer', 160),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('Used', 'Billed', '% of billed')],
                            override('Contract unit', ('mappings', [{'type': 'value', 'options': {
                                '': {'text': 'Sheet terms (no SOW override)', 'index': 0}}}])),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer')),
                            with_links('Line', path_link('drill', 'Open line'))], custom={'align': 'auto'})))
    p.append(panel(4, 'Pooled entitlements', 'table', f"""
        SELECT a.display_name AS Customer, pc.label AS Pool, pc.billed AS Billed, pc.used AS Used,
               pc.pct_billed AS `% of billed`, pc.pct_term AS `% of term`, pc.pace AS Pace,
               concat({date_sql('pc.start_date')}, ' to ', {date_sql('pc.end_date')}) AS Term, pc.account AS account
        FROM n360.v_line_pace AS pc INNER JOIN n360.account AS a ON a.account = pc.account
        WHERE pc.entitlement = 'pool' AND pc.{ACCT}""", 0, 16, 24, 4,
        describe('One entitlement shared by several lines of one account. Lines in a pool are never compared with '
                 'the billed amount one by one.', 'consumption_mapping.json "pools", via n360.v_line_pace', 'Customer',
                 'Used: the pooled lines, each from its start date to the data-through day'),
        field=field_config([hidden('account'), count_col('Used'), count_col('Billed'), pct('% of billed'), pct('% of term'),
                            pace_chip('Pace'), width('Pace', 70),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))],
                           noValue='No pooled entitlement'),
        may_be_empty='no customer in the selection has a pool'))
    p.append(panel(5, 'Daily use per contract line', 'timeseries', f"""
        SELECT toDateTime(d.day) AS time, concat(a.display_name, ' · ', {LINE_LABEL}) AS line, d.measured AS used
        FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
        INNER JOIN n360.account AS a ON a.account = l.account
        WHERE d.kind = 'line' AND d.{ACCT} AND l.measure NOT IN ('not measurable', 'not implemented') AND $__dateFilter(d.day)
        ORDER BY time""", 0, 20, 24, 9,
        describe('Daily contract measure per line (conversations for rules that bill every conversation, billable for '
                 'meaningful-engagement rules, engaged otherwise). Not measurable and not implemented lines have no usage '
                 'and are left out. Days not read show as gaps, not zero.',
                 'n360.line_day with n360.contract_line', 'Customer; measured contract lines', 'dashboard time range, per day'),
        fmt='timeseries', field=field_config(unit='locale', decimals=0, displayName='${__field.labels.line}',
                                             custom={'spanNulls': False, 'lineWidth': 1})))
    p.append(containment_weekly(6, 0, 29, 24, 8))
    return dashboard('n360-contracts', 'N360 · Contracts', p, [var_account()], [back_link()])


def forecast():
    """6. Forecast and runway (spec 6): when each line runs out, from the stored forecast only."""
    w30, w60, w90 = FORECAST_WINDOWS
    p = header(1, 0, w=4)
    p.append(panel(3, 'Crossing billed', 'stat', f"""
        SELECT countIf(days_to_cross BETWEEN 0 AND {w30}) AS `In {w30} days`,
               countIf(days_to_cross BETWEEN 0 AND {w60}) AS `In {w60} days`,
               countIf(days_to_cross BETWEEN 0 AND {w90}) AS `In {w90} days`
        FROM n360.v_line_pace WHERE {ACCT}""", 8, 0, 8, 3,
        describe('Lines and pools under billed today that the stored forecast says will cross billed within 30, 60 and '
                 '90 days, before their term ends.', 'n360.forecast via n360.v_line_pace', 'Customer',
                 'from the data-through day'), options=STATS))
    p.append(panel(4, 'Above billed', 'stat', f"""
        SELECT countIf(runway = 'above billed') AS above FROM n360.v_line_pace WHERE {ACCT}""", 16, 0, 4, 3,
        describe('Lines and pools already over billed (a pool counted once).', 'n360.v_line_pace', 'Customer',
                 'start date to the data-through day'),
        field=field_config(mappings=red_when_any()), options={**STAT_LAST, 'colorMode': 'background'},
        links=[panel_link('contracts', 'Contracts')]))
    p.append(panel(5, 'Step changes', 'stat', f"""
        SELECT countIf(step_change IS NOT NULL) AS steps FROM n360.v_line_pace WHERE {ACCT}""", 20, 0, 4, 3,
        describe('Lines and pools whose last-30-day rate differs from their term average by more than 50% (flagged '
                 'upstream in the forecast).', 'n360.forecast via n360.v_line_pace', 'Customer', 'last 30 days against the term'),
        options=STAT_LAST))
    p.append(panel(6, 'Runway', 'state-timeline', f"""
        WITH lanes AS (
                 SELECT concat(a.display_name, ' · ', pc.label) AS lane, pc.data_through AS dt, pc.end_date AS term_end,
                        pc.exhaust_date AS runs_out,
                        multiIf(pc.runway = 'crosses soon', 3, pc.runway = 'crosses before term end', 2, 1) AS cat
                 FROM n360.v_line_pace AS pc INNER JOIN n360.account AS a ON a.account = pc.account
                 WHERE pc.{ACCT} AND pc.exhaust_date IS NOT NULL),
             times AS (SELECT DISTINCT t FROM (SELECT dt AS t FROM lanes UNION ALL SELECT term_end FROM lanes WHERE term_end >= dt
                                               UNION ALL SELECT runs_out FROM lanes))
        -- every lane gets a value at every date on the chart, so Grafana never fills a gap
        SELECT toDateTime(times.t) AS time, lanes.lane AS lane,
               toUInt8(multiIf(times.t >= lanes.runs_out, 0, times.t >= lanes.term_end, 9, lanes.cat)) AS state
        FROM lanes CROSS JOIN times
        ORDER BY time""", 0, 3, 24, 10,
        describe('One lane per line or pool: from the data-through day to the day the stored forecast says it runs out '
                 '(uses up billed). Red: crosses billed soon (the red window of the pace rule). Blue: crosses billed '
                 'before its term ends. Grey: runs out only after the term ends; dark after the term end (the change of '
                 'shade marks the contract end). Lines already above billed, not measurable or not implemented have no '
                 'runway and are not shown.', 'n360.forecast via n360.v_line_pace', 'Customer; lines with a runs-out date',
                 'from the data-through day forward'),
        fmt='timeseries',
        field=field_config(mappings=[{'type': 'value', 'options': {
            '3': {'text': 'Crosses billed soon', 'color': COLOUR['red'], 'index': 0},
            '2': {'text': 'Crosses billed before term end', 'color': COLOUR['blue'], 'index': 1},
            '1': {'text': 'Runs out after term end', 'color': COLOUR['grey'], 'index': 2},
            '9': {'text': 'After the term end', 'color': COLOUR['dark'], 'index': 3},
            '0': {'text': 'Runs out', 'color': 'transparent', 'index': 4}}}],
            displayName='${__field.labels.lane}', color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, **TIMELINE),
        options={'showValue': 'never', 'mergeValues': True, 'rowHeight': 0.8, 'alignValue': 'left',
                 'legend': {'showLegend': True, 'displayMode': 'list', 'placement': 'bottom'}}))
    p.append(panel(7, 'Runway by line', 'table', f"""
        SELECT concat(a.display_name, ' · ', pc.label) AS Line, pc.measure AS Measure, pc.used AS Used, pc.billed AS Billed,
               round(pc.rate_per_day) AS `Per day`,
               multiIf(pc.exhaust_date IS NOT NULL, {date_sql('pc.exhaust_date')}, pc.runway = 'above billed', 'Already above billed', NULL) AS `Runs out`,
               {date_sql('pc.end_date')} AS `Term ends`, pc.runway AS Runway,
               trim(BOTH '; ' FROM concat(if(pc.term_ended, 'Term already ended; ', ''), arrayStringConcat(pc.flags, '; '))) AS Note,
               if(pc.entitlement = 'pool', concat('/d/n360-customer?var-account=', pc.account),
                  concat('/d/n360-line?var-account=', pc.account, '&var-line=', pc.line_key)) AS drill
        FROM n360.v_line_pace AS pc INNER JOIN n360.account AS a ON a.account = pc.account
        WHERE pc.{ACCT} ORDER BY pc.exhaust_date IS NULL, pc.exhaust_date""", 0, 13, 24, 14,
        describe('Every line and pool: use, rate (last 30 days / 30), the runs-out date from the stored forecast, and '
                 'when the term ends. Note carries the forecast flags and says when a term has already ended. '
                 'Click a line to open it (a pool opens its customer).', 'n360.forecast via n360.v_line_pace', 'Customer',
                 'start date to the data-through day; forecast forward'),
        field=field_config([hidden('drill'), count_col('Used'), count_col('Billed'), count_col('Per day'),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('Used', 'Per day', 'Runs out')],
                            coloured('Runway', RUNWAY_LABEL), width('Runway', 200), width('Line', 260), width('Measure', 180),
                            with_links('Line', path_link('drill', 'Open'))], custom={'align': 'auto'})))
    p.append(panel(8, 'Step changes', 'table', f"""
        SELECT a.display_name AS Customer, pc.label AS Line, pc.last30_vs_term_pct AS `Last 30 days vs term average`,
               pc.step_change AS Change
        FROM n360.v_line_pace AS pc INNER JOIN n360.account AS a ON a.account = pc.account
        WHERE pc.{ACCT} AND pc.step_change IS NOT NULL""", 0, 27, 12, 6,
        describe('Lines whose last-30-day rate is more than 50% away from their term average: rising, falling or '
                 'stopped. The forecast uses the last 30 days, so a step change moves the runs-out date a lot.',
                 'n360.forecast via n360.v_line_pace', 'Customer; flagged lines', 'last 30 days against the term'),
        field=field_config([pct('Last 30 days vs term average')], noValue='No step changes'),
        may_be_empty='no line has a step change'))
    p.append(text_panel(9, 'Forecast accuracy', 12, 27, 12, 6,
        'Not available yet: accuracy needs each night\'s forecast stored (`forecast_history`, backlog B15). '
        'It will show how far forecasts made 30, 60 and 90 days ago were off (MAPE) and how much crossing dates moved, '
        'once 30 nights are stored.'))
    return dashboard(UID['forecast'], 'N360 · Forecast and runway', p, [var_account()], [back_link()],
                     time={'from': 'now-14d', 'to': 'now+18M'})


def anomalies():
    """7. Anomalies (spec 7): drops, silences and spikes, settled vs provisional, cross-account events."""
    kind_sql = "if(${kind:singlequote} = 'spikes', kind = 'spike', kind IN ('drop', 'went silent'))"
    prov_sql = "(NOT provisional OR ${provisional:singlequote} = 'yes')"
    settled7 = f"NOT provisional AND day >= {DATA_THROUGH_SQL} - 9"
    p = header(1, 0, w=4)
    p.append(panel(3, 'Flags, 7 settled days', 'stat', f"""
        SELECT countIf(kind = 'drop' AND {settled7}) AS Drops, countIf(kind = 'went silent' AND {settled7}) AS Silences,
               countIf(kind = 'spike' AND {settled7}) AS Spikes, countIf(kind != 'normal' AND provisional) AS Provisional
        FROM n360.v_va_day_anomaly WHERE {ACCT}""", 8, 0, 16, 3,
        describe('VA-days flagged on the 7 newest settled days, and flags on the newest 3 days (provisional).',
                 'n360.v_va_day_anomaly', 'Customer', 'the 7 newest settled days; provisional = the newest 3 days'),
        options=STATS))
    p.append(panel(4, 'Distance from normal, flagged VAs', 'state-timeline', f"""
        SELECT toDateTime(h.day) AS time, concat(h.va_name, ' · ', a.display_name) AS va,
               toInt8(h.band + if(h.provisional, 10, 0)) AS band
        FROM n360.v_anomaly_heat AS h INNER JOIN n360.account AS a ON a.account = h.account
        WHERE h.{ACCT} AND h.day > {DATA_THROUGH_SQL} - 30
          AND h.bot_id IN (SELECT bot_id FROM n360.v_va_day_anomaly
                           WHERE kind != 'normal' AND day > {DATA_THROUGH_SQL} - 30 AND {prov_sql})
        ORDER BY time""", 0, 3, 24, 9,
        describe('Each VA flagged in the last 30 days, day by day, coloured by distance from its normal: red = flag '
                 '(z of -4 or lower, or silent), dark red = -4 to -2, grey = normal, dark blue = 2 to 4, blue = spike '
                 '(4 or higher). Provisional days (newest 3) are faded. Days not scored are blank.',
                 'n360.v_anomaly_heat (n360.v_va_day_anomaly)', 'Customer; VAs with a flag; Include provisional',
                 'the 30 days to the data-through day'),
        fmt='timeseries',
        field=field_config(mappings=[{'type': 'value', 'options': {
            str(code + off): {'text': text + (' (provisional)' if off else ''), 'color': COLOUR[key + ('_faded' if off else '')],
                              'index': i * 2 + (1 if off else 0)}
            for i, (code, text, key) in enumerate(HEAT_BANDS) for off in (0, 10)}}],
            displayName='${__field.labels.va}', color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, noValue=''),
        options={'showValue': 'never', 'mergeValues': False, 'rowHeight': 0.85,
                 'legend': {'showLegend': True, 'displayMode': 'list', 'placement': 'bottom'}},
        may_be_empty='no VA flagged in the last 30 days'))
    p[-1]['timeFrom'] = '35d'      # the heatmap covers 30 days to the data-through day
    p.append(panel(5, 'Anomaly feed', 'table', f"""
        SELECT {date_sql('f.day')} AS Day, ac.display_name AS Customer, {VA_LABEL} AS VA, f.actual AS Actual,
               f.expected AS Expected, f.z AS z, f.kind AS Kind, if(f.provisional, 'Provisional', 'Settled') AS Status,
               CAST(NULL, 'Nullable(String)') AS Review,
               concat('/d/n360-va?var-account=', f.account, '&var-va=', toString(f.bot_id)) AS drill
        FROM n360.v_va_day_anomaly AS f INNER JOIN n360.va AS v ON v.bot_id = f.bot_id
        INNER JOIN n360.account AS ac ON ac.account = f.account
        WHERE f.{ACCT} AND {kind_sql.replace('kind', 'f.kind').replace('${f.kind', '${kind')} AND {prov_sql.replace('provisional OR', 'f.provisional OR')} AND $__dateFilter(f.day)
        ORDER BY f.day DESC""", 0, 12, 24, 9,
        describe('Every flagged VA-day of the chosen kind in the time range. Review outcomes (real / expected / data '
                 'issue) come with the anomaly log (backlog B19): "Not reported yet" until then. Click a VA to open it.',
                 'n360.v_va_day_anomaly', 'Customer; Kind; Include provisional', 'dashboard time range'),
        field=field_config([hidden('drill'), count_col('Actual'), count_col('Expected'), override('z', ('decimals', 1)),
                            override('Review', ('mappings', [empty_grey()])),
                            override('Status', ('mappings', label_colours({'Provisional': ('Provisional', 'amber'),
                                                                          'Settled': ('Settled', 'red')})),
                                     ('custom.cellOptions', {'type': 'color-text'})),
                            with_links('VA', path_link('drill', 'Open VA'))], noValue='No flags of this kind'),
        may_be_empty='no flags of the chosen kind in the time range'))
    p.append(panel(6, 'Same day, several customers', 'table', f"""
        SELECT {date_sql('e.day')} AS Day, e.direction AS Direction, e.accounts AS Customers, e.vas AS VAs,
               arrayStringConcat(e.account_list, ', ') AS Which
        FROM n360.v_anomaly_events AS e
        WHERE arrayExists(x -> x IN (${{account:singlequote}}), e.account_list) AND $__dateFilter(e.day)
        ORDER BY e.day DESC""", 0, 21, 12, 7,
        describe('Settled days when 3 or more customers were flagged in the same direction: one event (for example an '
                 'industry-wide disruption), not separate customer stories.', 'n360.v_anomaly_events', 'Customer',
                 'dashboard time range, settled days'),
        field=field_config([width('Day', 110), width('Direction', 120), width('Customers', 90), width('VAs', 60)],
                           noValue='No cross-account events'), may_be_empty='no cross-account event in the range'))
    p.append(panel(7, 'Settled flags per week', 'timeseries', f"""
        SELECT toDateTime(toMonday(day)) AS time, countIf(kind IN ('drop', 'went silent')) AS `Drops and silences`,
               countIf(kind = 'spike') AS Spikes
        FROM n360.v_va_day_anomaly WHERE {ACCT} AND NOT provisional AND $__dateFilter(day)
        GROUP BY time ORDER BY time""", 12, 21, 12, 7,
        describe('Settled flags per week (Monday start), drops and silences apart from spikes.', 'n360.v_va_day_anomaly',
                 'Customer; settled days', 'dashboard time range, by week'),
        fmt='timeseries',
        field=field_config([override('Drops and silences', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['red']})),
                            override('Spikes', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['blue']}))],
                           decimals=0, custom={'drawStyle': 'bars', 'fillOpacity': 80, 'stacking': {'mode': 'normal', 'group': 'A'}})))
    return dashboard(UID['anomalies'], 'N360 · Anomalies', p,
                     [var_account(), custom_var('kind', 'Kind', [('Drops and silences', 'drops'), ('Spikes', 'spikes')]),
                      custom_var('provisional', 'Include provisional', [('No', 'no'), ('Yes', 'yes')])], [back_link()])


def containment():
    """8. Containment (spec 8): per VA only, never one number across channels."""
    p = header(1, 0, w=12)
    p.append(panel(3, 'Containment per VA: last 4 weeks against the 4 before', 'table', f"""
        SELECT {VA_LABEL} AS VA, ac.display_name AS Customer,
               1 - sumIf(d.handoffs, d.day > dt - 28) / sumIf(d.engaged, d.day > dt - 28) AS `Last 4 weeks`,
               1 - sumIf(d.handoffs, d.day <= dt - 28) / sumIf(d.engaged, d.day <= dt - 28) AS `Previous 4 weeks`,
               round(100 * (`Last 4 weeks` - `Previous 4 weeks`), 1) AS `Change (points)`,
               sumIf(d.engaged, d.day > dt - 28) AS `Engaged, last 4 weeks`,
               concat('/d/n360-va?var-account=', v.account, '&var-va=', toString(v.bot_id)) AS drill
        FROM n360.va_day AS d INNER JOIN n360.va AS v ON v.bot_id = d.bot_id
        INNER JOIN n360.account AS ac ON ac.account = v.account
        CROSS JOIN (SELECT {DATA_THROUGH_SQL} AS dt) AS r
        WHERE d.{ACCT} AND d.handoffs IS NOT NULL AND d.day > dt - 56
        GROUP BY v.bot_id, v.account, v.va_name, v.channels, ac.display_name
        HAVING sumIf(d.engaged, d.day > dt - 28) > 0 ORDER BY `Change (points)`""", 0, 3, 24, 9,
        describe('containment = 1 - handoffs / engaged, per VA, for the 28 days to the data-through day and the 28 '
                 'before. Change is in percentage points. Each VA on its own: chat and email are never combined. '
                 'Click a VA to open it.', 'n360.va_day (handoffs from the platform daily aggregate)',
                 'Customer; VAs with handoff data', 'the 56 days to the data-through day'),
        field=field_config([hidden('drill'), count_col('Engaged, last 4 weeks'),
                            *[override(c, ('unit', 'percentunit'), ('decimals', 1)) for c in ('Last 4 weeks', 'Previous 4 weeks')],
                            with_links('VA', path_link('drill', 'Open VA'))])))
    p.append(panel(4, 'VAs without handoff data', 'table', f"""
        SELECT {VA_LABEL} AS VA, ac.display_name AS Customer, toString(v.consumption_source) AS Reader,
               CAST(NULL, 'Nullable(Float64)') AS Containment
        FROM n360.va AS v INNER JOIN n360.account AS ac ON ac.account = v.account
        WHERE v.{ACCT} AND v.bot_id NOT IN (SELECT bot_id FROM n360.va_day WHERE handoffs IS NOT NULL)""", 0, 12, 24, 5,
        describe('VAs whose handoffs are not read (for example the conversation records reader). Their containment is '
                 '"Not reported yet": never 0% or 100%, and never left out silently.', 'n360.va, n360.va_day', 'Customer',
                 'all loaded days'),
        field=field_config([override('Containment', ('mappings', [empty_grey()]))], noValue='Every VA has handoff data'),
        may_be_empty='every VA has handoff data'))
    p.append(containment_weekly(5, 0, 17, 24, 10))
    return dashboard('n360-containment', 'N360 · Containment', p, [var_account()], [back_link()])


def pipeline():
    """11. Pipeline health (spec 11): did last night load, how fresh is each source, what is measured how."""
    p = [data_through(1, 0, 0, 4, 3, link=False), last_load(2, 4, 0, 4, 3)]
    p.append(panel(3, 'Nights loaded, last 14', 'stat', """
        SELECT countIf(status = 'loaded') AS loaded FROM (
            SELECT data_date, argMax(status, loaded_at) AS status FROM n360.runs GROUP BY data_date)
        WHERE data_date > (SELECT max(data_date) FROM n360.runs) - 14""", 8, 0, 5, 3,
        describe('Nights whose latest attempt loaded, of the last 14 (by data date).', 'n360.runs', 'none',
                 'the 14 nights to the latest run'), options=STAT_LAST))
    p.append(panel(4, 'Checks passed, last load', 'stat', f"""
        SELECT concat(toString({N_CHECKS} - length(checks_failed)), ' of {N_CHECKS}') AS checks
        FROM n360.runs ORDER BY loaded_at DESC LIMIT 1""", 13, 0, 5, 3,
        describe(f'Data checks the latest run passed, of the {N_CHECKS} in code/checks.sql. Any failure stops the load.',
                 'n360.runs, code/checks.sql', 'none', 'the latest run'),
        options={**STAT_LAST, 'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value'}))
    p.append(panel(5, 'Stale sources', 'stat', """
        SELECT countIf(status != 'green') AS stale FROM n360.v_source_freshness""", 18, 0, 6, 3,
        describe('Sources whose last day is 2 or more days behind the data-through day (proposed thresholds, in '
                 'n360.v_source_freshness).', 'n360.v_source_freshness', 'none', 'the latest load'),
        field=field_config(mappings=red_when_any()), options={**STAT_LAST, 'colorMode': 'background'}))
    p.append(panel(6, 'Nightly loads, last 14 nights', 'state-timeline', """
        SELECT toDateTime(n.night) AS time,
               toNullable(multiIf(r.status = 'loaded', 1, r.status = 'failed_checks', 2, 0)) AS `Nightly load`
        FROM (SELECT (SELECT max(data_date) FROM n360.runs) - number AS night FROM numbers(14)) AS n
        LEFT JOIN (SELECT data_date, toString(argMax(status, loaded_at)) AS status FROM n360.runs GROUP BY data_date) AS r
               ON r.data_date = n.night
        ORDER BY time""", 0, 3, 24, 4,
        describe('One block per night (by data date): green loaded, red stopped by a data check (the previous night '
                 'stayed on screen), grey no run. A run that errored before logging (loader exit 2) is not recorded '
                 'in n360.runs and shows as no run.', 'n360.runs', 'none', 'the 14 nights to the latest run'),
        fmt='timeseries',
        field=field_config(mappings=[{'type': 'value', 'options': {
            '1': {'text': 'Loaded', 'color': COLOUR['green'], 'index': 0},
            '2': {'text': 'Stopped: data checks failed', 'color': COLOUR['red'], 'index': 1},
            '0': {'text': 'No run', 'color': COLOUR['grey'], 'index': 2}}}],
            color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, noValue=''),
        options={'showValue': 'never', 'mergeValues': False, 'rowHeight': 0.9,
                 'legend': {'showLegend': True, 'displayMode': 'list', 'placement': 'bottom'}}))
    p.append(panel(7, 'Why nights stopped', 'table', f"""
        SELECT {date_sql('data_date')} AS Night, arrayStringConcat(checks_failed, ', ') AS `Checks failed`,
               formatDateTime(loaded_at, '%e %b %Y %H:%i UTC') AS Attempted
        FROM n360.runs WHERE status = 'failed_checks' ORDER BY loaded_at DESC LIMIT 20""", 0, 7, 12, 6,
        describe('Runs stopped by a data check, with the checks that failed (definitions in code/checks.sql and '
                 'docs/02_DATA_CONTRACT.md).', 'n360.runs', 'stopped runs', 'the latest 20'),
        field=field_config(noValue='No night has stopped'), may_be_empty='no night has stopped'))
    p.append(panel(8, 'Source freshness', 'table', f"""
        SELECT source_id AS Source, description AS Description, {date_sql('last_day')} AS `Last day`,
               days_behind AS `Days behind`, status AS Status
        FROM n360.v_source_freshness ORDER BY days_behind DESC""", 12, 7, 12, 6,
        describe('Each source\'s last day against the data-through day. Amber 2 days behind, red 3 (proposed, in '
                 'n360.v_source_freshness).', 'n360.source via n360.v_source_freshness', 'none', 'the latest load'),
        field=field_config([coloured('Status', FRESH_LABEL), width('Status', 90), width('Source', 160),
                            width('Last day', 110), width('Days behind', 100)])))
    p.append(panel(9, 'Contract measure mix', 'table', """
        SELECT measure AS Measure, count() AS Lines FROM n360.contract_line GROUP BY measure ORDER BY Lines DESC""",
        0, 13, 12, 8,
        describe('How many contract lines are measured each way. Engaged = no contract rule applies (billable unit '
                 'unconfirmed).', 'n360.contract_line', 'none', 'the latest load')))
    p.append(panel(10, 'Contract rules not live', 'table', """
        SELECT label AS Rule, status AS Status, counts_plain AS `What it would count`
        FROM n360.rule WHERE status != 'active' OR empty(lines_measured)""", 12, 13, 12, 8,
        describe('Contract rules that are pending or blocked, so their lines are measured on engaged for now.',
                 'n360.rule', 'rules not active, or measuring no line', 'the latest load'),
        field=field_config(noValue='Every rule is live'), may_be_empty='every rule is live'))
    return dashboard('n360-pipeline', 'N360 · Pipeline health', p, links=[back_link()], time={'from': 'now-30d', 'to': 'now'})


DASHBOARDS = [overview, contracts, customer, contract_line, va, forecast, anomalies, containment, pipeline]


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
