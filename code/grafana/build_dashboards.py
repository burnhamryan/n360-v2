#!/usr/bin/env python3
"""Generate the N360 Grafana dashboards as JSON. Dashboards are code: edit this file, run it,
commit the JSON. Grafana reloads grafana/dashboards/ every 30 s and refuses edits made in the UI.

    python3 code/grafana/build_dashboards.py

Wording rules carried over from the page: plain words on the panels, provenance in each panel's
description (the (i)), and "Not reported yet" wherever a value was not read. Never red for empty.

Layout of this file: settings (one place each) -> building blocks (target, panel, describe,
variables, header, links, formats, colours from view labels) -> one function per dashboard -> main().
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'dashboards')

# ───────────────────────── settings: each value is written once, here ─────────────────────────

DS = {'type': 'grafana-clickhouse-datasource', 'uid': 'n360-clickhouse'}
NOT_READ = 'Not reported yet'
ACCT = "account IN (${account:singlequote})"
HOME = ('n360-overview', 'Overview')      # every dashboard's Back link and header link
PIPELINE = ('n360-pipeline', 'Pipeline health')     # "Data through" drills here (04 conventions)
DEFAULT_RANGE = {'from': 'now-90d', 'to': 'now'}
UID = {'overview': 'n360-overview', 'contracts': 'n360-contracts', 'customer': 'n360-customer', 'line': 'n360-line',
       'va': 'n360-va', 'forecast': 'n360-forecast', 'anomalies': 'n360-anomalies',
       'adoption': 'n360-adoption', 'requests': 'n360-requests',
       'containment': 'n360-containment', 'pipeline': 'n360-pipeline', 'health': 'n360-health', 'risk': 'n360-risk',
       'risk_model': 'n360-risk-model', 'adoption_customer': 'n360-adoption-customer',
       'adoption_va': 'n360-adoption-va', 'cortex': 'n360-cortex'}   # never change (links depend on them)
FORECAST_WINDOWS = (30, 60, 90)   # Forecast "Crossing billed" stat (spec 6): display windows

# Display colours: ONE palette (v2 principle 6: colour means status, nowhere else). Mid tones chosen to read on both
# the light and the dark theme; Grafana picks black or white text on a coloured cell by contrast. Empty is grey, never
# a status colour. Contract statuses use the v1.1 page's colours (no red: a renewal risk is a stronger blue).
COLOUR = {
    # contract status (v_contract_status)
    'peach': '#F2A07B', 'lavender': '#C3B0F2', 'lavender_light': '#E0D6F8', 'green': '#76C893', 'light_blue': '#9CC8EE', 'risk_blue': '#3E7CC4',
    'grey': '#B4B4B4', 'grey_light': '#D3D3D3',
    # risk levels (Risk dashboard): risk warm, watch amber, fine green, not reported grey
    'risk': '#E07B5F',
    # operations: amber = look at it (stale data, a stopped night); green = loaded
    'amber': '#F0C25A',
    # anomalies: down warm, up cool; provisional days faded; 'normal' a neutral grey
    'down': '#D9695F', 'down_watch': '#E8A89F', 'up': '#5E93DB', 'up_watch': '#A9C5EC', 'normal': '#5A5A5A',
    'down_faded': 'rgba(217, 105, 95, 0.35)', 'down_watch_faded': 'rgba(232, 168, 159, 0.35)',
    'normal_faded': 'rgba(90, 90, 90, 0.35)', 'up_watch_faded': 'rgba(169, 197, 236, 0.35)', 'up_faded': 'rgba(94, 147, 219, 0.35)',
    # not a status: series in charts are shades of one neutral blue-grey; reference marks (billed, 100%) dark grey
    'series': '#7F9CC0', 'series_light': '#C3D2E6', 'reference': '#6E6E6E', 'dark': '#4A4A4A',
    'none': 'text', 'neutral': '#7F9CC0',
}

# Contract status (n360.v_contract_status.status, plus 'renewal risk' when its flag is set): {stored: (shown, colour)}.
# Order = urgency, the order of every legend and sorted list.
STATUS = {
    'over billed': ('Over billed', 'peach'),
    'will exceed': ('Will exceed', 'lavender'),
    'renewal risk': ('Renewal risk', 'risk_blue'),
    'below pace': ('Below pace', 'light_blue'),
    'ahead': ('Ahead', 'green'),
    'on pace': ('On pace', 'green'),
    'too early': ('Too early to tell', 'grey_light'),
    'under review': ('Under review', 'grey'),
    'billed not recorded': ('Billed not recorded', 'grey'),
    'unmapped': ('Unmapped', 'grey'),
    'not built': ('Not built', 'grey'),
    'not measured': ('Not measured', 'grey'),
    'not read': (NOT_READ, 'grey'),
}
# Risk level (n360.v_risk_signal.level): {stored: (shown, colour)}
LEVEL = {'risk': ('Risk', 'risk'), 'watch': ('Watch', 'amber'), 'fine': ('OK', 'green'), 'not reported': ('Not reported', 'grey')}
STATUS_SHOWN = "if(renewal_risk, 'renewal risk', status)"
RISK_SIGNALS = [('pace', 'Pace'), ('renewal', 'Renewal'), ('volume', 'Volume'), ('contact', 'Contact'),
                ('billing', 'Billing'), ('csm', 'CSM view'),
                ('va_health', 'VA health'), ('containment', 'Contained'), ('concentration', 'Reliance'),
                ('engine', 'Engine'), ('building', 'Building'), ('support', 'Support')]   # CSM lens first, then CA
     # the status a screen shows (renewal risk on top)
BULLET_CAP = 250            # bullet bars stop here; the true % is printed in the label (v2 visual grammar)

# Runway words (from n360.v_line_pace.runway), source freshness (v_source_freshness.status), heatmap bands (v_anomaly_heat.band)
RUNWAY_LABEL = {'above billed': ('Already above billed', 'peach'), 'crosses soon': ('Crosses billed soon', 'lavender'),
                'crosses before term end': ('Crosses before term end', 'lavender'),
                'runs out after term end': ('Runs out after term end', 'grey'),
                'no use in the last 30 days': ('No use in the last 30 days', 'grey'),
                'not measurable': ('Not measurable', 'grey'), 'not implemented': ('Not implemented', 'grey'),
                'not read': (NOT_READ, 'grey')}
REVIEW_LABEL = {'real': ('Real', 'down'), 'expected': ('Expected', 'green'), 'data issue': ('Data issue', 'amber')}
FRESH_LABEL = {'green': ('On time', 'green'), 'amber': ('Behind', 'amber'), 'red': ('Late', 'down')}
HEAT_BANDS = [(-2, 'Drop or silent', 'down'), (-1, 'Low (z -4 to -2)', 'down_watch'), (0, 'Normal', 'normal'),
              (1, 'High (z 2 to 4)', 'up_watch'), (2, 'Spike', 'up')]
N_CHECKS = open(os.path.join(os.path.dirname(HERE), 'checks.sql')).read().count('-- check: ')   # the data checks, counted

PHASE2_NOTE = ('Phase 2 (proposed design, tested on synthetic only): field meanings are open questions in '
               'docs/08_DECISIONS.md. Empty until the company export carries these files.')

# VA status words (VA dashboard): settled flags red, provisional orange (spec 4)
VA_STATUS = {'Normal': ('Normal', 'none'), 'Not scored': ('Not scored', 'grey'),
             **{f'{k}{p}': (f'{k}{p}', c + ('_faded' if p else '')) for k, c in (('Went silent', 'down'), ('Drop', 'down'),
                                                                                   ('Spike', 'up'))
                for p in ('', ' (provisional)')}}

# A flag's state: settled flags count; provisional ones (the newest 3 days) may still change and are greyed
FLAG_STATE = {'Settled': ('Settled', 'none'), 'Provisional': ('Provisional', 'grey')}

# Words for run status, as stored in n360.runs.status
RUN_STATUS = {'loaded': ('Loaded', 'green'), 'failed_checks': ('Stopped: data checks failed', 'amber')}


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
    # every customer, including one with VAs but no contract line (unbilled), so "All" means all
    return query_var('account', 'Customer', 'SELECT account FROM n360.account ORDER BY account')


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
    return override(column, ('mappings', label_colours(labels) + [empty_grey()]), ('custom.cellOptions', {'type': cell}),
                    ('color', {'mode': 'fixed', 'fixedColor': COLOUR['none']}))   # unmapped values: plain text, never green


def amber_when_any(colour='amber'):
    """A count stat: coloured when above zero (amber = look at it, or a status colour), uncoloured at zero."""
    return [{'type': 'value', 'options': {'0': {'color': 'transparent', 'index': 0}}},
            {'type': 'range', 'options': {'from': 1, 'to': 1e15, 'result': {'color': COLOUR[colour], 'index': 1}}}]


# a line or pool as "Customer · Line" from v_line_pace (alias pc) and n360.account (alias a); a pool's name not twice
PC_LABEL = ("if(startsWith(pc.label, concat(a.display_name, ':')), concat(a.display_name, ' · ', "
            "trimLeft(substring(pc.label, length(a.display_name) + 2))), concat(a.display_name, ' · ', pc.label))")
KIND = {'drop': ('Drop', 'down'), 'went silent': ('Went silent', 'down'), 'spike': ('Spike', 'up')}


def label_colours(labels):
    """Value mappings for a text column holding colour keys or statuses: {stored: (shown, colour key)}."""
    return [{'type': 'value', 'options': {k: {'text': t, 'color': COLOUR[c], 'index': i}
                                          for i, (k, (t, c)) in enumerate(labels.items())}}]


# Header (Data through, last load status) for the top row of every dashboard

def data_through(pid, x, y, w, h, link=True):
    links = [{'title': PIPELINE[1], 'url': f'/d/{PIPELINE[0]}?${{__url_time_range}}'}] if link else []
    return panel(pid, 'Data through', 'stat', """
        SELECT formatDateTime(window_end, '%e %b %Y') AS data_through
        FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC, data_date DESC LIMIT 1""", x, y, w, h,
        describe('Last day included in this load (the nightly window ends at 23:59 UTC that day).',
                 'n360.runs, written by load.py only after every data check passes',
                 'loaded runs only; the latest', 'the latest load'),
        field=field_config(links=links) if links else None,
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value', 'colorMode': 'none'})


def last_load(pid, x, y, w, h):
    return panel(pid, 'Last night', 'stat', """
        SELECT toString(status) AS status FROM n360.runs ORDER BY loaded_at DESC, data_date DESC LIMIT 1""", x, y, w, h,
        describe('Result of the latest nightly run. A stopped night leaves the previous good night on screen.',
                 'n360.runs (load.py writes one row per night, loaded or not)', 'none', 'the latest run'),
        field=field_config(mappings=label_colours(RUN_STATUS)),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value',
                 'colorMode': 'background'})


def dashboard(uid, title, panels, variables=(), links=(), time=None):
    return {
        'uid': uid, 'title': title, 'tags': ['n360'], 'editable': False,
        'schemaVersion': 39, 'version': 1, 'timezone': 'utc',
        'time': dict(time or DEFAULT_RANGE),
        'templating': {'list': list(variables)},
        'panels': panels,
        **({'links': list(links)} if links else {}),
    }


# ───────────────────────── v2 building blocks (dashboard review, 25 Nov 2026) ─────────────────────────
# Every page reads: question (the title) -> sentence that answers it with a number -> at most 4 tiles, each with a
# reference -> one lead chart -> one action list -> tables. Nothing below repeats what is above.

def status_chip(column='Status'):
    """A contract status column as a coloured chip (the stored status, or 'renewal risk' on top)."""
    return coloured(column, STATUS, cell='color-background')


def series_colours(**custom):
    """Charts whose series are not statuses: shades of one neutral colour (colour means status, nowhere else)."""
    return {'color': {'mode': 'shades', 'fixedColor': COLOUR['series']}, **custom}


def reference_line(value):
    """A reference mark (billed, 100%) drawn as a line. Not a status rule: one step, the reference colour.
    test_build.py allows thresholds only in this shape."""
    return {'thresholds': {'mode': 'absolute', 'steps': [{'color': 'transparent', 'value': None},
                                                          {'color': COLOUR['reference'], 'value': value}]},
            'custom': {'thresholdsStyle': {'mode': 'dashed'}}}


def stamp(pid, x, y, w=6, h=3):
    """Freshness stamp on every page: "Usage to 19 Nov 2026 · loaded 25 Nov", amber when the data is old."""
    return panel(pid, 'Data', 'stat', """
        SELECT concat(stamp, if(stale, concat(' · ', toString(days_old), ' days old'), '')) AS stamp FROM n360.v_freshness""",
        x, y, w, h,
        describe('The last day of usage in this load, and the day it was loaded. Amber when the data is more than '
                 '2 days old (n360.settings stamp_stale_days): the numbers are the last good night\'s. Click for '
                 'Pipeline health.', 'n360.v_freshness (n360.runs: loaded runs only)', 'none', 'the latest load'),
        field=plain(field_config(mappings=[{'type': 'regex', 'options': {'pattern': '.* days old$', 'result': {
            'color': COLOUR['amber'], 'index': 0}}}], links=[{'title': PIPELINE[1], 'url': f'/d/{PIPELINE[0]}'}])),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value',
                 'colorMode': 'background', 'graphMode': 'none', 'justifyMode': 'center', 'text': {'valueSize': 13}})


def sentence(pid, sql, x, y, w, h, desc):
    """The page's answer in one computed sentence (v2 row 1). A stat showing a string: no plugin needed."""
    return panel(pid, 'In one sentence', 'stat', sql, x, y, w, h, desc,
                 field=plain(field_config(noValue='Nothing loaded yet')),
                 options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value',
                          'colorMode': 'none', 'graphMode': 'none', 'justifyMode': 'auto', 'text': {'valueSize': 17}})


def page_header(pid, sentence_sql, sentence_desc, y=0):
    """Row 1 of every page: the sentence (18 columns) and the freshness stamp (6)."""
    return [sentence(pid, sentence_sql, 0, y, 18, 3, sentence_desc), stamp(pid + 1, 18, y)]


def change_text(now, then, then_date):
    """SQL: "+3 since 21 Oct" / "no change since 21 Oct" (the tile's reference, v2 principle 3)."""
    return (f"if({then_date} IS NULL, 'no earlier night stored', concat(multiIf({now} > {then}, concat('+', toString({now} - {then})), "
            f"{now} < {then}, concat('−', toString({then} - {now})), 'no change'), ' since ', "
            f"toString(toDayOfMonth({then_date})), ' ', formatDateTime({then_date}, '%b')))")


def tile(pid, title, nightly_sql, x, y, desc, w=6, h=5, unit='locale', links=None, metric=None):
    """A headline number with its change since 30 nights ago (or the oldest stored night) and a 30-night line.
    nightly_sql returns one row per night: (night Date, value). The tile shows the newest night's value."""
    sql = f"""
        WITH nightly AS ({nightly_sql.strip()}),
             (SELECT max(night) FROM nightly) AS newest,
             (SELECT max(night) FROM nightly WHERE night <= newest - 30) AS ref_30,
             (SELECT min(night) FROM nightly) AS oldest,
             if(ref_30 IS NULL OR ref_30 = toDate(0), if(oldest < newest, oldest, NULL), ref_30) AS ref,
             (SELECT any(value) FROM nightly WHERE night = newest) AS v_now,
             (SELECT any(value) FROM nightly WHERE night = ref) AS v_then
        SELECT toDateTime(night) AS time, value, {change_text('v_now', 'v_then', 'ref')} AS change
        FROM nightly WHERE night > newest - 30 ORDER BY time"""
    p = panel(pid, title, 'stat', sql, x, y, w, h, desc, fmt='timeseries',
              field=plain(field_config(unit=unit, decimals=0, displayName='${__field.labels.change}',
                                       color={'mode': 'fixed', 'fixedColor': COLOUR['series']})),
              options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': ''}, 'textMode': 'value_and_name',
                       'colorMode': 'none', 'graphMode': 'area', 'justifyMode': 'auto', 'showPercentChange': False,
                       'text': {'titleSize': 13, 'valueSize': 34}}, links=links)
    p['timeFrom'] = '35d'                 # the 30-night line fills the tile whatever the dashboard's range
    p['hideTimeOverride'] = True
    if metric:
        p['n360Metric'] = metric          # test_panels.py: panels with the same metric must show the same value
    return p


def single_tile(pid, title, sql, x, y, desc, w=6, h=5, unit='locale', links=None, metric=None, field=None):
    """A headline number with no stored history (the reference is in the name, e.g. "of 23 VAs")."""
    p = panel(pid, title, 'stat', sql, x, y, w, h, desc,
              field=field or plain(field_config(unit=unit, decimals=0)),
              options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value_and_name',
                       'colorMode': 'none', 'graphMode': 'none', 'justifyMode': 'auto',
                       'text': {'titleSize': 13, 'valueSize': 34}}, links=links)
    if metric:
        p['n360Metric'] = metric
    return p


def status_night_count(cond, acct='1'):
    """nightly_sql for tile(): entitlements per stored night matching a status condition (v_status_night)."""
    return f"""SELECT data_date AS night, toUInt64(countIf({cond})) AS value FROM n360.v_status_night
               WHERE {acct} GROUP BY night"""


def status_filter_note(pid, x, y, w, h, where, total_where='1'):
    """"12 of 19 lines · filtered to Below pace" above a filtered table (v2 interaction), with a clear link."""
    return panel(pid, 'Showing', 'stat', f"""
        SELECT concat(toString(countIf({where})), ' of ', toString(countIf({total_where})), ' lines and pools',
                      if(countIf({where}) = countIf({total_where}), '', ' · filtered (Status / Customer above)')) AS shown
        FROM n360.v_contract_status""", x, y, w, h,
        describe('How many lines the tables below show, and whether a filter is on. Clear it with the Status and '
                 'Customer pickers (All), or the Clear filters link.', 'n360.v_contract_status', 'Customer; Status',
                 'the latest load'),
        field=plain(field_config()), links=[{'title': 'Clear filters', 'url': '/d/n360-contracts'}],
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value', 'colorMode': 'none',
                 'graphMode': 'none', 'justifyMode': 'auto', 'text': {'valueSize': 14}})


def var_status():
    """Status filter (Contracts): every status the view can store, shown in plain words."""
    v = custom_var('status', 'Status', [(t, k) for k, (t, _) in STATUS.items()])
    v.update({'multi': True, 'includeAll': True, 'current': {'text': 'All', 'value': '$__all'}})
    for o in v['options']:
        o['selected'] = False
    return v


def var_csm():
    return query_var('csm', 'CSM', 'SELECT DISTINCT csm_rocketlane FROM n360.account ORDER BY 1')


CSM = "csm_rocketlane IN (${csm:singlequote})"


def with_header(p, sentence_sql, desc):
    """Put the v2 header (sentence and freshness stamp) above a dashboard's panels: everything moves down 3 rows and
    the top row's tiles are spread over the full width (no gap where the old header was)."""
    top = sorted((x for x in p if x['gridPos']['y'] == 0), key=lambda x: x['gridPos']['x'])
    for i, x in enumerate(top):
        x['gridPos'].update(x=24 * i // len(top), w=24 * (i + 1) // len(top) - 24 * i // len(top))
    shift = 3 - min(x['gridPos']['y'] for x in p)       # the old header's row may already be empty
    for x in p:
        x['gridPos']['y'] += shift
    first = max(x['id'] for x in p) + 1
    return page_header(first, sentence_sql, desc) + p


STATUS_WORDS = (f"transform({STATUS_SHOWN}, ['" + "', '".join(STATUS) + "'], ['"
                + "', '".join(t for t, _ in STATUS.values()) + "'], '')")


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
    """Overview (v2): where does the book stand, and which customers need attention? Leadership, under a minute.
    The home page: no Back link. Statuses come from n360.v_contract_status; nothing is re-ruled here."""
    p = page_header(1, f"""
        SELECT concat({plural('c', 'customer needs', 'customers need')}, ' attention · ',
                      {plural('e', 'line ends', 'lines end')}, ' in the next ', toString(days), ' days · ',
                      toString(r), ' at renewal risk') AS sentence
        FROM (SELECT (SELECT uniqExact(account) FROM n360.v_needs_attention) AS c,
                     (SELECT ending_soon_days FROM n360.settings) AS days,
                     (SELECT countIf(days_left BETWEEN 0 AND days) FROM n360.v_contract_status) AS e,
                     (SELECT countIf(renewal_risk) FROM n360.v_contract_status) AS r)""",
        describe('Customers with something needing attention (the list below), contract lines and pools whose term '
                 'ends within 30 days (n360.settings ending_soon_days), and lines at renewal risk (under 50% used '
                 'with 120 days or fewer left; proposed).', 'n360.v_needs_attention, n360.v_contract_status',
                 'none (every customer)', 'the latest load'))
    p.append(tile(3, 'Lines over or set to exceed billed', status_night_count("status IN ('over billed', 'will exceed')"),
                  0, 3, describe('Contract lines and pools already over billed, or that the stored forecast says will '
                                 'cross billed before their term ends. A pool counts once. The line under the number is '
                                 'the last 30 nights; the change is against 30 nights ago (or the oldest stored night).',
                                 'n360.v_status_night (n360.forecast_history), the same rules as n360.v_contract_status',
                                 'none (every customer)', 'the last 30 stored nights'),
                  links=[{'title': 'Contracts: over or will exceed',
                          'url': f"/d/{UID['contracts']}?var-status=over billed&var-status=will exceed"}],
                  metric='lines over or will exceed'))
    p.append(tile(4, 'Lines below pace', status_night_count("status = 'below pace'"), 6, 3,
                  describe('Contract lines and pools whose % of billed is more than 10 points behind their % of term '
                           '(proposed, n360.settings status_pace_pts), renewal risks included.',
                           'n360.v_status_night (n360.forecast_history)', 'none (every customer)', 'the last 30 stored nights'),
                  links=[{'title': 'Contracts: below pace',
                          'url': f"/d/{UID['contracts']}?var-status=below pace&var-status=renewal risk"}],
                  metric='lines below pace'))
    p.append(tile(5, 'VAs on V8 or later', f"""
        SELECT snapshot_date AS night, toUInt64(countIf(engine_major >= (SELECT engine_current_major FROM n360.settings))) AS value
        FROM n360.va_config_snapshot GROUP BY night""", 12, 3,
        describe('VAs whose weekly configuration read shows engine V8 or later. The change is against the read 30 days '
                 'earlier (or the oldest stored). ' + PHASE2_NOTE, 'n360.va_config_snapshot', 'none (every customer)',
                 'weekly reads, the last 30 days'),
        links=[panel_link('adoption', 'Adoption')], metric='vas on v8'))
    p.append(panel(6, 'Customers using 3+ target features', 'stat', f"""
        SELECT now() AS time, if(countIf(f.n_read > 0) = 0, NULL, toUInt64(countIf(f.n_used >= m))) AS value,
               concat('of ', toString(countIf(f.n_read > 0)), ' customers · history not stored yet') AS change
        FROM (SELECT account, countIf(used) AS n_used, countIf(used IS NOT NULL) AS n_read FROM n360.v_feature_use
              GROUP BY account) AS f
        CROSS JOIN (SELECT target_features_min AS m FROM n360.settings) AS s""", 18, 3, 6, 5,
        describe('Customers using at least 3 of the 5 target features in the last 30 days: V8 playbooks run, the '
                 'Cortex, Explorer, MCP servers live, supervisory rules on (proposed list; the '
                 'v1.1 page also counts Testing, Calibration and Recipes, not exported yet). ' + PHASE2_NOTE,
                 'n360.v_feature_use', 'none (every customer)', 'the 30 days to the data-through day'),
        fmt='timeseries', field=plain(field_config(unit='locale', decimals=0, displayName='${__field.labels.change}')),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': ''}, 'textMode': 'value_and_name', 'colorMode': 'none',
                 'graphMode': 'none', 'justifyMode': 'auto', 'text': {'titleSize': 13, 'valueSize': 34}},
        links=[panel_link('adoption', 'Adoption')]))
    p[-1]['n360Metric'] = 'customers on 3+ features'
    p.append(panel(7, 'Contract lines by status', 'barchart', f"""
        SELECT 'Lines and pools' AS Book, {status_columns()} FROM n360.v_contract_status""", 0, 8, 16, 8,
        describe('Every contract line and pool (a pool counts once) by its status, as a share of the book. Grey = no '
                 'reading yet (not built, not measured, billed not recorded, unmapped, under review, not reported). '
                 'Click a segment to open Contracts filtered to it. Rules: n360.v_contract_status (proposed thresholds '
                 'in n360.settings).', 'n360.v_contract_status', 'none (every customer)', 'the latest load'),
        field=field_config(status_overrides(link=True), unit='locale', decimals=0, min=0,
                           max=1, custom={'fillOpacity': 90, 'lineWidth': 0, 'axisPlacement': 'hidden'}),
        options={'xField': 'Book', 'orientation': 'horizontal', 'stacking': 'percent', 'showValue': 'never', 'barWidth': 0.9,
                 'groupWidth': 0.9, 'xTickLabelMaxLength': 0,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True, 'calcs': ['sum']},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}, 'text': {'valueSize': 14}}))
    p.append(panel(8, 'Status, last 30 nights', 'timeseries', f"""
        SELECT toDateTime(data_date) AS time, {status_columns()}
        FROM n360.v_status_night WHERE data_date > (SELECT max(data_date) FROM n360.v_status_night) - 30
        GROUP BY time ORDER BY time""", 16, 8, 8, 8,
        describe('The same statuses, night by night, as a share of the lines with a stored forecast (lines with no use '
                 'or no billed amount have no stored forecast and are left out here).', 'n360.v_status_night '
                 '(n360.forecast_history)', 'none (every customer)', 'the last 30 stored nights'),
        fmt='timeseries',
        field=field_config(status_overrides(), unit='percentunit', min=0,
                           custom={'drawStyle': 'bars', 'fillOpacity': 90, 'lineWidth': 0, 'barAlignment': 0,
                                   'stacking': {'mode': 'percent', 'group': 'A'}}),
        options={'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}))
    p[-1]['timeFrom'] = '35d'
    p.append(panel(9, 'Needs attention', 'table', f"""
        SELECT display_name AS Customer, subject AS `Line or VA`, issue AS Issue, {date_sql('issue_date')} AS `Date`,
               status AS Status, csm AS CSM, account, drill
        FROM (SELECT n.*, if(c.renewal_risk, 'renewal risk', n.status) AS status
              FROM n360.v_needs_attention AS n
              LEFT JOIN (SELECT full_label, renewal_risk FROM n360.v_contract_status) AS c ON c.full_label = n.subject)
        ORDER BY issue_date, rank LIMIT 8""", 0, 16, 24, 9,
        describe('At most 8 items, soonest date first: a line or pool over billed, set to exceed (date = when it '
                 'crosses), at renewal risk, or whose term ended in the last 90 days; or a VA with 1,000+ '
                 'conversations down 25% or more (last 30 days against the 30 before). Thresholds proposed '
                 '(n360.settings). Click a customer, a line or a VA to open it; "View all" opens Customer health.',
                 'n360.v_needs_attention (n360.v_contract_status, n360.v_va_change)', 'none (every customer)',
                 'the latest load'),
        field=field_config([hidden('account'), hidden('drill'),
                            coloured('Status', {**STATUS, 'conversations down': ('Conversations down', 'down')}, 'color-background'),
                            width('Customer', 180),
                            width('Line or VA', 300), width('Issue', 230), width('Date', 120), width('Status', 130),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer')),
                            with_links('Line or VA', path_link('drill', 'Open'))], noValue='Nothing needs attention'),
        links=[{'title': 'View all: Customer health', 'url': f"/d/{UID['health']}?${{__url_time_range}}"}],
        may_be_empty='nothing needs attention'))
    p.append(conversations_chart(10, 0, 25, 24, 9))
    return dashboard(UID['overview'], 'N360 · Overview', p, [var_period()])


STATS = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value_and_name', 'colorMode': 'none',
         'text': {'titleSize': 12, 'valueSize': 26}}
ONE_ACCT = "account IN (${account:singlequote})"
STATS_TEXT = {**STATS, 'text': {'titleSize': 12, 'valueSize': 18}}   # stats that carry words
TIMELINE = {'noValue': '', 'custom': {'spanNulls': True}}
LINE = "line_key IN (${line:singlequote})"
VA = "toString(bot_id) IN (${va:singlequote})"


def customer():
    """One customer (v2): everything about one account, in meeting order. Is this contract on track, and what is the
    customer running? Drills: line -> Contract line, VA -> VA."""
    A = ONE_ACCT
    worst = f"""(SELECT argMin(tuple(line_label, {STATUS_SHOWN}, pct_billed, pct_term, days_left, cross_date),
                                tuple(status_rank, -ifNull(pct_billed, 0))) AS w
                 FROM n360.v_contract_status WHERE {A})"""
    p = page_header(1, f"""
        WITH {worst} AS ws,
             (SELECT count() FROM n360.v_contract_status WHERE {A}) AS n_lines,
             (SELECT minIf(end_date, days_left >= 0) FROM n360.v_contract_status WHERE {A}) AS next_end,
             (SELECT count() FROM n360.va WHERE {A}) AS n_vas
        SELECT concat(a.display_name, ': ',
                      if(n_lines = 0, 'no contract line (unbilled)', multiIf(
                          ws.2 = 'over billed', concat(ws.1, ' is over billed (', toString(round(ws.3)), '%)'),
                          ws.2 = 'will exceed', concat(ws.1, ' is set to exceed billed on ', {date_sql('ws.6')}),
                          ws.2 = 'renewal risk', concat(ws.1, ' is at renewal risk: ', toString(round(ws.3)), '% used, ',
                                                        toString(ws.5), ' days left'),
                          ws.2 = 'below pace', concat(ws.1, ' is below pace: ', toString(round(ws.3)), '% used, ',
                                                      toString(round(ws.4)), '% of the term gone'),
                          ws.2 IN ('on pace', 'ahead'), 'every line on pace',
                          ws.2 = 'too early', 'too early to tell',
                          'no line has a reading yet')),
                      ' · ', {plural('n_vas', 'VA', 'VAs')},
                      if(next_end = toDate(0) OR n_lines = 0, '', concat(' · next term end ', {date_sql('next_end')},
                         ' (', toString(dateDiff('day', (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded'), next_end)), ' days)')),
                      ' · CSM ', a.csm_rocketlane) AS sentence
        FROM n360.account AS a WHERE a.{A}""",
        describe('The customer\'s most urgent line or pool (statuses from n360.v_contract_status, proposed thresholds), '
                 'how many VAs it runs, its next term end and its CSM.', 'n360.v_contract_status, n360.va, n360.account',
                 'Customer', 'the latest load'))
    shown = "', '".join(t for t, _ in STATUS.values())
    keys = "', '".join(STATUS)
    p.append(text_tile(3, 'Contract status (most urgent line)', f"""
        SELECT concat(arrayElement(['{shown}'], indexOf(['{keys}'], argMin({STATUS_SHOWN}, tuple(status_rank, -ifNull(pct_billed, 0))))),
                      ' · ', argMin(line_label, tuple(status_rank, -ifNull(pct_billed, 0)))) AS status
        FROM n360.v_contract_status WHERE {A} HAVING count() > 0""",
        0, 3, 6, 5, describe('The status of this customer\'s most urgent line or pool, and which one it is. Colours: the '
                             'contract status palette (Contracts).', 'n360.v_contract_status', 'Customer', 'the latest load'),
        mappings=status_regex_colours(), no_value='No contract line (unbilled)', colour_mode='background',
        may_be_empty='an unbilled customer'))
    p.append(text_tile(4, 'VAs on V8 or later', f"""
        SELECT if(count() = 0, NULL, concat(toString(countIf(current_engine)), ' of ', toString(count()))) AS vas
        FROM n360.v_va_config_latest WHERE {A}""", 6, 3, 6, 5,
        describe('VAs whose newest weekly configuration read shows engine V8 or later. ' + PHASE2_NOTE,
                 'n360.v_va_config_latest', 'Customer', 'the newest read per VA')))
    p.append(text_tile(5, 'Target features used', f"""
        SELECT if(countIf(used IS NOT NULL) = 0, NULL, concat(toString(countIf(used)), ' of ', toString(count()))) AS f
        FROM n360.v_feature_use WHERE {A}""", 12, 3, 6, 5,
        describe('Target features this customer used in the last 30 days (proposed list: V8 playbooks, Cortex, '
                 'Explorer, MCP servers, supervisory rules). Which ones: Adoption, below. ' + PHASE2_NOTE,
                 'n360.v_feature_use', 'Customer', 'the 30 days to the data-through day')))
    p.append(text_tile(6, 'Open tickets', f"""
        SELECT if((SELECT count() FROM n360.ps_ticket) = 0, NULL,
                  if(countIf(status = 'open') = 0, 'None open', toString(countIf(status = 'open')))) AS t
        FROM n360.ps_ticket WHERE {A}""", 18, 3, 6, 5,
        describe('Support tickets still open for this customer ("None open": tickets are exported and none is open; '
                 '"Not reported yet": no ticket export). Priority is not exported yet. ' + PHASE2_NOTE,
                 'n360.ps_ticket', 'Customer', 'all tickets')))
    p.append(panel(7, 'Notes', 'table', f"""
        SELECT note AS Note FROM n360.v_customer_notes WHERE {A} ORDER BY o""", 0, 8, 12, 5,
        describe('Pinned facts about this customer\'s contracts, from the contract data: pools, signed SOW terms, contract '
                 'rules (live or pending), lines with no reading, ended terms, VAs on no contract.',
                 'n360.v_customer_notes (n360.pool, n360.contract_line, n360.rule, n360.v_contract_status, n360.va)',
                 'Customer', 'the latest load'),
        field=field_config(noValue='No notes'), options={'showHeader': False, 'cellHeight': 'sm'},
        may_be_empty='a customer with nothing to note'))
    p.append(panel(8, 'Needs attention', 'table', f"""
        SELECT subject AS `Line or VA`, issue AS Issue, {date_sql('issue_date')} AS `Date`, drill
        FROM n360.v_needs_attention WHERE {A} ORDER BY issue_date""", 12, 8, 12, 5,
        describe('Items from the Overview\'s Needs attention list for this customer (same rule, proposed thresholds). '
                 'Click one to open it.', 'n360.v_needs_attention', 'Customer', 'the latest load'),
        field=field_config([hidden('drill'), width('Issue', 210), width('Date', 115),
                            with_links('Line or VA', path_link('drill', 'Open'))], noValue='Nothing needs attention'),
        may_be_empty='nothing needs attention'))
    p.append(panel(9, 'Contract lines', 'table', f"""
        SELECT Line, Status, `% of billed`, `% of term`, Used, Billed, `At term end`, `Term ends`, `Days left`, Measure, drill
        FROM (
            SELECT 0 AS o, status_rank AS r, line_label AS Line, {STATUS_SHOWN} AS Status, pct_billed AS `% of billed`,
                   pct_term AS `% of term`, used AS Used, billed AS Billed, forecast_end_pct AS `At term end`,
                   {date_sql('end_date')} AS `Term ends`, days_left AS `Days left`, measure AS Measure, drill
            FROM n360.v_contract_status WHERE {A}
            UNION ALL
            SELECT 1, 99, concat('   in the pool: ', l.channel), 'in pool', NULL, NULL, s.consumed, NULL, NULL,
                   {date_sql('l.end_date')}, NULL, l.measure, concat('/d/n360-line?var-account=', l.account, '&var-line=', l.line_key)
            FROM n360.v_line_status AS s INNER JOIN n360.contract_line AS l ON l.line_key = s.line_key
            WHERE s.{A} AND s.pool_id IS NOT NULL)
        ORDER BY o, r, Line""", 0, 13, 24, 6,
        describe('Every line and pool, in its own contract measure; a pool\'s lines are listed under it for information, '
                 'never against billed. The bar is % of billed (full at 100%; the number is the true %). At term end = '
                 'the stored forecast\'s rate carried to the term end. Grey statuses have no reading ("Not reported '
                 'yet", never zero). Click a line to open it.', 'n360.v_contract_status, n360.v_line_status', 'Customer',
                 'start date to the data-through day'),
        field=field_config([hidden('drill'), coloured('Status', {**STATUS, 'in pool': ('In the pool', 'none')}, 'color-background'),
                            count_col('Used'), count_col('Billed'),
                            override('% of billed', ('unit', 'percent'), ('decimals', 0), ('min', 0), ('max', 100),
                                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']}),
                                     ('custom.cellOptions', {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'text'})),
                            pct('% of term'), override('% of term', ('decimals', 0)), pct('At term end'),
                            override('At term end', ('decimals', 0)),
                            override('Days left', ('mappings', [{'type': 'range', 'options': {'from': -100000, 'to': -1,
                                     'result': {'text': 'Ended', 'index': 0}}}])),
                            *[override(c, ('mappings', [{'type': 'special', 'options': {'match': 'null', 'result': {
                                'text': '', 'index': 0}}}])) for c in ('% of billed', 'At term end', 'Billed', '% of term', 'Days left')],
                            override('Used', ('mappings', [empty_grey()])),
                            width('Line', 220), width('Status', 140), width('% of billed', 160), width('% of term', 80),
                            width('Used', 135), width('Billed', 110), width('At term end', 100), width('Term ends', 115),
                            width('Days left', 80), with_links('Line', path_link('drill', 'Open line'))],
                           noValue='No contract line (unbilled customer)'),
        options={'cellHeight': 'sm'}, may_be_empty='an unbilled customer'))
    p.append(panel(10, '% of billed over the term', 'timeseries', f"""
        WITH ent AS (
            SELECT l.series_key AS sk, if(l.pool_id IS NULL, c1.line_label, c2.line_label) AS lab,
                   if(l.pool_id IS NULL, c1.billed, c2.billed) AS b, if(l.pool_id IS NULL, l.start_date, c2.start_date) AS st
            FROM n360.contract_line AS l
            LEFT JOIN (SELECT line_key, line_label, billed FROM n360.v_contract_status WHERE entitlement = 'line') AS c1
                   ON c1.line_key = l.line_key
            LEFT JOIN (SELECT account, pool_id, line_label, billed, start_date FROM n360.v_contract_status
                       WHERE entitlement = 'pool') AS c2 ON c2.account = l.account AND c2.pool_id = l.pool_id
            WHERE l.{A}),
             daily AS (SELECT d.day AS day, ent.lab AS lab, any(ent.b) AS b, sum(d.measured) AS m
                       FROM n360.line_day AS d INNER JOIN ent ON ent.sk = d.series_key
                       WHERE d.kind = 'line' AND d.day >= ent.st AND ent.b > 0 GROUP BY day, lab)
        SELECT toDateTime(day) AS time, lab AS line,
               round(100 * sum(m) OVER (PARTITION BY lab ORDER BY day ROWS UNBOUNDED PRECEDING) / b, 1) AS pct
        FROM daily ORDER BY time""", 0, 19, 12, 9,
        describe('Each line or pool\'s running total as % of its billed amount, from its start date (a pool as one line). '
                 'The dashed line is 100% of billed. Days not read add nothing. Lines with no reading are left out.',
                 'n360.line_day, n360.v_contract_status', 'Customer; lines and pools with a billed amount',
                 'each term from its start date'),
        fmt='timeseries',
        field=field_config(unit='percent', decimals=0, min=0, displayName='${__field.labels.line}',
                           custom={'lineWidth': 2, 'spanNulls': False, **reference_line(100)['custom']},
                           thresholds=reference_line(100)['thresholds'], **series_colours()),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}, may_be_empty='no line with a billed amount'))
    p[-1]['n360Reference'] = True
    p[-1]['timeFrom'] = '1y'
    conv = conversations_chart(11, 12, 21, 12, 9, acct=f"d.{A}", title='Conversations, and containment', va_filter='va')
    conv['gridPos'].update(y=19)
    conv['targets'][0]['rawSql'] = conv['targets'][0]['rawSql'].replace(
        "AS `Still counting`\n", """AS `Still counting`,
               -- containment only when the chosen VAs share one channel (never across channels)
               if(uniqExact(arrayStringConcat(v.channels, ',')) = 1 AND sum(d.engaged) > 0 AND countIf(d.handoffs IS NULL) = 0,
                  round(100 * (1 - sum(d.handoffs) / sum(d.engaged)), 1), NULL) AS `Containment %`
""").replace("              FROM n360.va_day) AS d\n", "              FROM n360.va_day) AS d\n        INNER JOIN n360.va AS v ON v.bot_id = d.bot_id\n")
    conv['description'] = describe(
        'Conversations per day, week or month (Period picker) for the VAs chosen in the VA picker. The newest period is '
        'faded ("Still counting") while it is not over or holds days that may still settle. The line is containment '
        '(1 - handoffs / engaged, right axis), drawn only when the chosen VAs share one channel: containment is never '
        'added up across channels. Pick one VA to see its own.', 'n360.va_day, n360.va', 'Customer; VA',
        'dashboard time range, by the Period picker')
    conv['fieldConfig']['overrides'].append(override('Containment %', ('unit', 'percent'), ('custom.drawStyle', 'line'),
                                                     ('custom.lineWidth', 2), ('custom.axisPlacement', 'right'),
                                                     ('custom.stacking', {'mode': 'none'}), ('min', 0), ('max', 100),
                                                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['dark']})))
    p.append(conv)
    p.append(panel(12, 'VAs', 'table', f"""
        SELECT c.va AS VA, cfg.engine_version AS Engine, if(cfg.bot_id IS NULL, NULL, if(cfg.current_engine, 'V8 or later', 'Older'))
                   AS `Engine status`, cfg.v8_playbooks AS `V8 playbooks`, cfg.legacy_intents AS `Legacy intents`,
               c.convs_30 AS `Conversations, 30 days`, c.convs_change_pct AS `vs previous 30`,
               c.containment_30 AS Containment, c.containment_change_pts AS `Change (points)`,
               arrayStringConcat(arrayFilter(x -> x != '', [if(c.convs_down, 'Conversations down', ''),
                                                            if(c.containment_down, 'Containment down', '')]), ' · ') AS Flag,
               concat('/d/n360-va?var-account=', c.account, '&var-va=', toString(c.bot_id)) AS drill
        FROM n360.v_va_change AS c
        LEFT JOIN (SELECT toNullable(bot_id) AS bot_id, engine_version, current_engine, v8_playbooks, legacy_intents
                   FROM n360.v_va_config_latest) AS cfg ON cfg.bot_id = c.bot_id
        WHERE c.{A} ORDER BY `Conversations, 30 days` DESC""", 0, 28, 24, 5,
        describe('Every VA of this customer: engine and coverage (V8 playbooks against legacy intents, from the newest '
                 'weekly configuration read), conversations and containment over the last 30 days against the 30 '
                 'before. Containment is per VA, never across channels; "Not reported yet" where handoffs are not read. '
                 'Flags: conversations down 10%+ or containment down 3+ points (proposed, n360.settings). Click a VA '
                 'to open it.', 'n360.v_va_change (n360.va_day), n360.v_va_config_latest', 'Customer',
                 'the 60 days to the data-through day; the newest configuration read'),
        field=field_config([hidden('drill'), count_col('Conversations, 30 days'),
                            override('vs previous 30', ('unit', 'percent'), ('decimals', 0)),
                            override('Containment', ('unit', 'percent'), ('decimals', 0)),
                            override('Change (points)', ('decimals', 1)),
                            coloured('Engine status', {'V8 or later': ('V8 or later', 'green'), 'Older': ('Older', 'amber')},
                                     'color-background'),
                            coloured('Flag', {'Conversations down': ('Conversations down', 'down'),
                                              'Containment down': ('Containment down', 'down'),
                                              'Conversations down · Containment down': ('Conversations and containment down', 'down'),
                                              '': ('', 'none')}),
                            *[override(c, ('mappings', [empty_grey()])) for c in
                              ('Engine', 'V8 playbooks', 'Legacy intents', 'Conversations, 30 days', 'vs previous 30',
                               'Containment', 'Change (points)')],
                            width('VA', 200), width('Engine', 80), width('Engine status', 120), width('Flag', 230),
                            with_links('VA', path_link('drill', 'Open VA'))]),
        options={'cellHeight': 'sm'}))
    p.append(panel(13, 'Target features', 'table', f"""
        SELECT feature AS Feature, if(used IS NULL, NULL, if(used, 'Used', 'Not used')) AS `Last 30 days`
        FROM n360.v_feature_use WHERE {A} ORDER BY feature""", 0, 33, 8, 7,
        describe('Which target features this customer used in the last 30 days (proposed list). ' + PHASE2_NOTE,
                 'n360.v_feature_use', 'Customer', 'the 30 days to the data-through day'),
        field=field_config([coloured('Last 30 days', {'Used': ('Used', 'green'), 'Not used': ('Not used', 'grey_light')},
                                     'color-background')]), options={'cellHeight': 'sm'}))
    p.append(panel(14, 'Playbooks, last 31 days', 'table', f"""
        SELECT pb.playbook AS Playbook, {VA_LABEL} AS VA, sum(pb.executions) AS Executions,
               if(sum(pb.executions) = 0, 'Built, not run', 'Running') AS Status
        FROM n360.playbook_day AS pb INNER JOIN n360.va AS v ON v.bot_id = pb.bot_id
        WHERE pb.{A} AND pb.day > {DATA_THROUGH_SQL} - 31
        GROUP BY pb.playbook, v.va_name, v.channels ORDER BY Executions, Playbook""", 8, 33, 16, 7,
        describe('Executions per playbook. "Built, not run" = authored and live but run 0 times (v1.1 "Authored, not '
                 'live"). ' + PHASE2_NOTE, 'n360.playbook_day', 'Customer', 'the 31 days to the data-through day'),
        field=field_config([count_col('Executions'), width('Status', 130),
                            coloured('Status', {'Built, not run': ('Built, not run', 'amber'), 'Running': ('Running', 'none')})],
                           noValue='No playbook data for this customer'),
        options={'cellHeight': 'sm'}, may_be_empty='no playbook data'))
    p.append(panel(15, 'Recent anomalies', 'table', f"""
        SELECT {date_sql('a.day')} AS Day, {VA_LABEL} AS VA, a.kind AS Kind, a.actual AS Actual, a.expected AS Expected,
               if(a.provisional, 'Provisional', 'Settled') AS State
        FROM n360.v_va_day_anomaly AS a INNER JOIN n360.va AS v ON v.bot_id = a.bot_id
        WHERE a.{A} AND a.kind != 'normal' AND a.day > {DATA_THROUGH_SQL} - 30
        ORDER BY a.day DESC""", 0, 40, 12, 6,
        describe('Days a VA went silent, dropped (z of -4 or lower) or spiked (4 or higher) against the same weekday over '
                 'the previous 8 weeks. Provisional days (the newest 3) may still change and never alert.',
                 'n360.v_va_day_anomaly', 'Customer; flagged days only', 'the 30 days to the data-through day'),
        field=field_config([count_col('Actual'), count_col('Expected'),
                            coloured('Kind', KIND),
                            override('State', ('mappings', label_colours(FLAG_STATE)))], noValue='No flags in the last 30 days'),
        options={'cellHeight': 'sm'}, may_be_empty='a customer with no flags'))
    p.append(panel(16, 'Requests', 'table', f"""
        SELECT {date_sql('opened')} AS Opened, title AS Title, product_area AS Area, ask AS Ask, status AS Status
        FROM n360.ps_ticket WHERE {A} ORDER BY status = 'open' DESC, opened DESC""", 12, 40, 12, 6,
        describe('This customer\'s support tickets, open first; area and ask are tagged by hand ("Not tagged yet" until '
                 'then). ' + PHASE2_NOTE, 'n360.ps_ticket', 'Customer', 'all tickets'),
        field=field_config([*[override(c, ('mappings', [{'type': 'special', 'options': {'match': 'null', 'result':
                                {'text': 'Not tagged yet', 'color': COLOUR['grey'], 'index': 0}}}])) for c in ('Area', 'Ask')],
                            width('Opened', 110), width('Status', 80)], noValue='No tickets for this customer'),
        options={'cellHeight': 'sm'}, may_be_empty='a customer with no tickets'))
    return dashboard(UID['customer'], 'N360 · Customer', p, [var_one_account(), var_vas(), var_period()],
                     [back_link(), {**back_link(ADOPT_CUSTOMER, keep_vars=True), 'title': 'Adoption detail'}])


def contract_line():
    """3. Contract line (spec 3): one line, how it is measured and where it is heading. The Contracts drawer."""
    p = []
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
        SELECT pct_billed AS `% of billed`, {STATUS_SHOWN} AS Status FROM n360.v_contract_status WHERE {LINE}""", 16, 0, 4, 3,
        describe('Used / billed in the line\'s own measure, and its contract status (n360.v_contract_status, proposed '
                 'thresholds). A pooled line has no % of its own: see the pool on the Customer dashboard.',
                 'n360.v_contract_status', 'Contract line', 'start date to the data-through day'),
        field=plain(field_config([pct('% of billed'), override('% of billed', ('decimals', 0)),
                                  override('Status', ('mappings', label_colours(STATUS)))])),
        options={**STATS, 'colorMode': 'value', 'textMode': 'value'}, may_be_empty='a pooled line (measured at the pool)'))
    p.append(panel(6, '% of term', 'stat', f"""
        SELECT s.pct_term_elapsed AS `% of term`,
               toUInt32(round(s.pct_term_elapsed * l.term_days / 100)) AS `Days in`, l.term_days AS `Term days`
        FROM n360.v_line_status AS s INNER JOIN n360.contract_line AS l ON l.line_key = s.line_key WHERE s.{LINE}""", 20, 0, 4, 3,
        describe('Days elapsed / term days.', 'n360.v_line_status', 'Contract line', 'start date to the data-through day'),
        field=field_config([pct('% of term')]), options=STATS_TEXT))
    p.append(panel(7, 'Forecast to horizon', 'stat', f"""
        SELECT round(100 * forecast_m3 / billed, 1) AS `% of billed`, round(rate_per_day) AS `Per day`,
               {date_sql('horizon')} AS Horizon
        FROM n360.v_line_pace WHERE {LINE} AND forecast_m3 IS NOT NULL""", 20, 0, 4, 3,
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
        ) ORDER BY o""", 12, 3, 12, 9,
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
        ) ORDER BY time""", 0, 3, 12, 9,
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
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['reference']})),
            override('Used', ('custom.lineWidth', 2), ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']}))],
            unit='locale', decimals=0, custom={'spanNulls': False})))
    p.append(panel(10, 'Measure against the engaged flag, by month', 'barchart', f"""
        SELECT formatDateTime(toStartOfMonth(d.day), '%b %y') AS Month, sum(d.measured) AS `Contract measure`,
               sum(d.engaged) AS `Engaged (platform flag)`
        FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
        WHERE d.kind = 'line' AND l.{LINE} AND d.day >= l.start_date
        GROUP BY toStartOfMonth(d.day) ORDER BY toStartOfMonth(d.day)""", 0, 12, 12, 8,
        describe('Per month: the contract measure beside the platform engaged flag, so a contract rule\'s effect is '
                 'visible (for lines with no rule they are the same). Never one replaced by the other.',
                 'n360.line_day', 'Contract line', 'start date to the data-through day, by calendar month'),
        field=field_config([override('Contract measure', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']})),
                            override('Engaged (platform flag)', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series_light']}))],
                           unit='locale', decimals=0),
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
        GROUP BY lv.bot_id, lv.account, lv.region, v.va_name, v.channels ORDER BY `Conversations, 30 days` DESC""", 12, 12, 12, 8,
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
        ORDER BY time""", 0, 20, 24, 7,
        describe('What N360 said each night for this line, so a jump between nights is visible. Shown once 7 nights '
                 'are stored.', 'n360.line_totals_history (append-only, one row per line per night)', 'Contract line',
                 'every stored night'),
        fmt='timeseries', field=field_config(unit='percent', decimals=1, noValue='Shows once 7 nights are stored',
                                             color={'mode': 'fixed', 'fixedColor': COLOUR['series']}),
        may_be_empty='fewer than 7 nights stored'))
    p[-1]['timeFrom'] = '45d'      # nightly history: the last weeks, not the whole term
    p = with_header(p, f"""
        SELECT if(c.n > 0, c.s, concat(l.channel, ' is in a pooled entitlement: its use counts toward the pool, which is compared with billed (customer page).')) AS sentence
        FROM (SELECT count() AS n, any(concat(full_label, ': ', multiIf(
                  status = 'over billed', concat('over billed, ', toString(round(pct_billed)), '% of billed'),
                  status = 'will exceed', concat('set to exceed billed on ', {date_sql('cross_date')}, ', ',
                                                 toString(dateDiff('day', cross_date, end_date)), ' days before the term ends'),
                  pct_billed IS NULL, lower({STATUS_WORDS}),
                  concat(lower({STATUS_WORDS}), ': ', toString(round(pct_billed)), '% used, ', toString(round(pct_term)),
                         '% of the term gone')),
                  if(forecast_end_pct IS NULL, '', concat(' · ', toString(round(forecast_end_pct)), '% by the term end at the current rate')))) AS s
              FROM n360.v_contract_status WHERE {LINE}) AS c
        CROSS JOIN (SELECT channel FROM n360.contract_line WHERE {LINE}) AS l""",
        describe('Where this line stands: its status (n360.v_contract_status, proposed thresholds), and where the stored '
                 'forecast\'s rate takes it by the term end.', 'n360.v_contract_status', 'Contract line', 'the latest load'))
    return dashboard(UID['line'], 'N360 · Contract line', p, [var_one_account(), var_line()],
                     [back_link(CUSTOMER, keep_vars=True), back_link()], time={'from': 'now-400d', 'to': 'now+120d'})


def va():
    """4. VA (spec 4): one VA against its own normal."""
    p = []
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
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['down']})),
            override('Flag (provisional)', ('custom.drawStyle', 'points'), ('custom.pointSize', 10),
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['down_faded']}))],
            unit='locale', decimals=0, custom={'spanNulls': False})))
    p.append(panel(8, 'Flags', 'table', f"""
        SELECT {date_sql('day')} AS Day, kind AS Kind, actual AS Actual, expected AS Expected, z AS z,
               if(provisional, 'Provisional', 'Settled') AS Status
        FROM n360.v_va_day_anomaly WHERE {VA} AND kind != 'normal' AND $__dateFilter(day) ORDER BY day DESC""", 0, 13, 12, 10,
        describe('Every flagged day for this VA in the time range. Provisional days may still change.',
                 'n360.v_va_day_anomaly', 'VA; flagged days', 'dashboard time range'),
        field=field_config([count_col('Actual'), count_col('Expected'), width('Day', 110), width('Kind', 100), coloured('Kind', KIND),
                            override('z', ('decimals', 1)),
                            override('Status', ('mappings', label_colours(FLAG_STATE)),
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
            UNION ALL SELECT 6, 'Engine version', (SELECT if(count() = 0, NULL, toNullable(concat(any(engine_version),
                if(any(current_engine), ' (V8 or later)', ' (older than V8)')))) FROM n360.v_va_config_latest WHERE {VA})
            UNION ALL SELECT 7, 'V8 playbooks / legacy intents', (SELECT if(count() = 0, NULL, toNullable(concat(
                toString(any(v8_playbooks)), ' / ', toString(any(legacy_intents))))) FROM n360.v_va_config_latest WHERE {VA})
            UNION ALL SELECT 8, 'MCP servers live / supervisory rules on', (SELECT if(count() = 0, NULL, toNullable(concat(
                toString(any(mcp_servers_live)), ' / ', toString(any(supervisory_rules_on))))) FROM n360.v_va_config_latest WHERE {VA})
            UNION ALL SELECT 9, 'Configuration read on', (SELECT if(count() = 0, NULL, toNullable({date_sql('any(snapshot_date)')}))
                FROM n360.v_va_config_latest WHERE {VA})
        ) ORDER BY o""", 12, 13, 12, 10,
        describe('Where this VA\'s counts come from, and its newest weekly configuration read (Phase 2: "Not reported '
                 'yet" until that read is in the export).', 'n360.va, n360.va_day, n360.v_va_config_latest', 'VA',
                 'the latest load; the newest configuration read'),
        field=field_config([width('Item', 290), override('Value', ('mappings', [empty_grey()]))]),
        options={'showHeader': False}))
    p = with_header(p, f"""
        SELECT concat(c.va, ': ', if(c.convs_30 IS NULL, 'conversations not reported for every day of the last 30',
                      concat({num_sql('c.convs_30')}, ' conversations in 30 days',
                             if(c.convs_change_pct IS NULL, '', concat(' (', if(c.convs_change_pct >= 0, '+', ''),
                                toString(round(c.convs_change_pct)), '% on the 30 before)')))),
                      ' · containment ', if(c.containment_30 IS NULL, 'not reported (no handoff data)',
                                            concat(toString(round(c.containment_30)), '%')),
                      ' · newest scored day: ', ifNull(a.k, 'not scored')) AS sentence
        FROM n360.v_va_change AS c
        LEFT JOIN (SELECT bot_id, toNullable(concat(argMax(kind, day), if(argMax(provisional, day), ' (provisional)', ''))) AS k
                   FROM n360.v_va_day_anomaly GROUP BY bot_id) AS a ON a.bot_id = c.bot_id
        WHERE toString(c.bot_id) IN (${{va:singlequote}})""",
        describe('This VA\'s conversations over the last 30 days against the 30 before, its containment (per VA only), '
                 'and the newest scored day against its own normal.', 'n360.v_va_change, n360.v_va_day_anomaly', 'VA',
                 'the 60 days to the data-through day'))
    return dashboard(UID['va'], 'N360 · VA', p, [var_one_account(), var_va()],
                     [back_link(CUSTOMER, keep_vars=True), back_link(),
                      {**back_link(ADOPT_VA, keep_vars=True), 'title': 'Adoption detail'}])


def contracts():
    """Contracts (v2): what renews when, and which lines are over or under? Account management, weekly.
    The Contract line dashboard is each line's drawer."""
    st = "status_shown IN (${status:singlequote})"
    where = f"{ACCT} AND {st}"
    base = f"(SELECT *, {STATUS_SHOWN} AS status_shown FROM n360.v_contract_status)"
    p = page_header(1, f"""
        SELECT concat({plural('e', 'line ends', 'lines end')}, ' in the next ', toString(days), ' days · ',
                      toString(o), ' over billed · ', toString(w), ' set to exceed · ', toString(r), ' at renewal risk') AS sentence
        FROM (SELECT (SELECT decision_window_days FROM n360.settings) AS days,
                     countIf(days_left BETWEEN 0 AND days) AS e, countIf(status = 'over billed') AS o,
                     countIf(status = 'will exceed') AS w, countIf(renewal_risk) AS r
              FROM n360.v_contract_status WHERE {ACCT})""",
        describe('Lines and pools whose term ends within 90 days (n360.settings decision_window_days), over billed, '
                 'set to exceed billed before the term ends, and at renewal risk (proposed thresholds).',
                 'n360.v_contract_status', 'Customer', 'the latest load'))
    acct_h = ACCT
    p.append(tile(3, 'Ending in 90 days', f"""
        SELECT r.data_date AS night,
               toUInt64(countIf(dateDiff('day', r.data_date - 1, c.end_date) BETWEEN 0 AND (SELECT decision_window_days FROM n360.settings))) AS value
        FROM (SELECT DISTINCT data_date FROM n360.runs WHERE status = 'loaded') AS r
        CROSS JOIN (SELECT end_date FROM n360.v_contract_status WHERE {acct_h}) AS c GROUP BY night""", 0, 3,
        describe('Lines and pools (every one, a pool once) whose term ends within 90 days of each loaded night\'s '
                 'data-through day (n360.settings decision_window_days).',
                 'n360.v_contract_status, n360.runs', 'Customer', 'the last 30 loaded nights'), metric='ending in 90 days'))
    p.append(tile(4, 'Over billed', status_night_count("status = 'over billed'", acct_h), 6, 3,
                  describe('Lines and pools whose use is above billed, in their own contract measure.',
                           'n360.v_status_night', 'Customer', 'the last 30 stored nights')))
    p.append(tile(5, 'Set to exceed billed', status_night_count("status = 'will exceed'", acct_h), 12, 3,
                  describe('Lines and pools under billed that the stored forecast says will cross billed before the '
                           'term ends.', 'n360.v_status_night (n360.forecast_history)', 'Customer', 'the last 30 stored nights')))
    p.append(tile(6, 'At renewal risk', status_night_count('renewal_risk', acct_h), 18, 3,
                  describe('Lines and pools under 50% used with 120 days or fewer left (proposed, n360.settings).',
                           'n360.v_status_night', 'Customer', 'the last 30 stored nights')))
    cap = BULLET_CAP
    bars = ',\n               '.join(
        f"if(status_shown = {k!r}, least(pct_billed, {cap}), NULL) AS `{t}`" for k, (t, c) in STATUS.items() if c != 'grey')
    p.append(panel(7, '% of billed now, and by the term end at the current rate', 'barchart', f"""
        SELECT concat(full_label, '   ', toString(round(pct_billed)), '% · term ', toString(round(pct_term)), '%') AS Line,
               {bars},
               greatest(least(forecast_end_pct, {cap}) - least(pct_billed, {cap}), 0) AS `More by the term end (forecast)`
        FROM {base} WHERE {where} AND pct_billed IS NOT NULL
          AND status_shown NOT IN ({', '.join(repr(g) for g in GREY_STATUSES)})
        ORDER BY status_rank, pct_billed DESC""", 0, 8, 24, 16,
        describe(f'A bullet chart per line and pool (a pool once). Solid: % of billed now, in its status colour. Pale, '
                 f'after it: how much more the stored forecast\'s rate adds by the term end, so the bar ends at the '
                 f'forecast. The dashed line is 100% of billed. The label prints the true % and how much of the term has '
                 f'gone (Grafana OSS has no tick mark inside a bar). Bars stop at {cap}%. Sorted by status, then %. '
                 f'Lines with no reading (grey statuses) are in the table below.',
                 'n360.v_contract_status (n360.v_line_pace, n360.forecast: rate never refitted)', 'Customer; Status',
                 'start date to the data-through day; forecast to the term end'),
        field=field_config([*status_overrides(group_grey=False)[:7],
                            override('More by the term end (forecast)', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series_light']}),
                                     ('custom.fillOpacity', 45))],
                           unit='percent', decimals=0, min=0, max=cap,
                           custom={'fillOpacity': 90, 'lineWidth': 0, 'stacking': {'mode': 'normal', 'group': 'A'},
                                   **reference_line(100)['custom']},
                           thresholds=reference_line(100)['thresholds']),
        options={'xField': 'Line', 'orientation': 'horizontal', 'stacking': 'normal', 'showValue': 'never',
                 'barWidth': 0.85, 'groupWidth': 0.8, 'xTickLabelMaxLength': 60, 'fullHighlight': False,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}))
    p[-1]['n360Reference'] = True
    p.append(panel(8, 'Decisions due', 'table', f"""
        SELECT display_name AS Customer, line_label AS Line, issue AS Issue, {date_sql('end_date')} AS `Term ends`,
               days_left AS `Days left`, pct_billed AS `% used`, forecast_end_pct AS `At term end`,
               status_shown AS Status, account, drill
        FROM {base} WHERE {where} AND decision_due
        ORDER BY days_left < 0 DESC, days_left, status_rank""", 0, 24, 24, 11,
        describe('Every line or pool needing a decision: over billed, set to exceed, at renewal risk, ended in the last '
                 '90 days, or ending within 90 days (proposed, n360.settings). At term end = the stored forecast\'s '
                 'rate carried to the end of the term, as % of billed. Ended terms first, then soonest. Click a '
                 'customer or a line to open it.', 'n360.v_contract_status', 'Customer; Status',
                 'the latest load; forecast to the term end'),
        field=field_config([hidden('account'), hidden('drill'), status_chip('Status'), pct('% used'), pct('At term end'),
                            override('% used', ('decimals', 0)), override('At term end', ('decimals', 0)),
                            override('Days left', ('mappings', [{'type': 'range', 'options': {'from': -100000, 'to': -1,
                                     'result': {'text': 'Ended', 'index': 0}}}])),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('% used', 'At term end')],
                            width('Customer', 170), width('Line', 230), width('Issue', 200), width('Term ends', 120),
                            width('Days left', 90), width('% used', 80), width('At term end', 100), width('Status', 140),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer')),
                            with_links('Line', path_link('drill', 'Open line'))], noValue='No decision due'),
        may_be_empty='no decision due for the selection'))
    p.append(status_filter_note(9, 0, 35, 24, 2, f"{ACCT} AND {STATUS_SHOWN} IN (${{status:singlequote}})", ACCT))
    p.append(panel(10, 'Every line', 'table', f"""
        SELECT display_name AS Customer, line_label AS Line, status_shown AS Status, pct_billed AS `% of billed`,
               pct_term AS `% of term`, used AS Used, billed AS Billed, forecast_end_pct AS `At term end`,
               {date_sql('end_date')} AS `Term ends`, days_left AS `Days left`, measure AS Measure,
               concat('Q', toString(toQuarter(end_date)), ' ', toString(toYear(end_date))) AS `Term-end quarter`,
               csm AS CSM, account, drill
        FROM {base} WHERE {where}
        ORDER BY status_rank, pct_billed DESC""", 0, 37, 24, 18,
        describe('Every contract line and pool (a pool once, its lines on the customer page), in its own contract '
                 'measure. The bar is % of billed (full at 100%; the number is the true %). Grey statuses have no '
                 'reading: "Not reported yet", never zero. Sort by any column; group by term-end quarter with its '
                 'column; filter with Status and Customer. Click a customer or a line to open it.',
                 'n360.v_contract_status (n360.v_line_pace, n360.contract_line, n360.forecast)', 'Customer; Status',
                 'start date to the data-through day'),
        field=field_config([hidden('account'), hidden('drill'), status_chip('Status'), count_col('Used'), count_col('Billed'),
                            override('% of billed', ('unit', 'percent'), ('decimals', 0), ('min', 0), ('max', 100),
                                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']}),
                                     ('custom.cellOptions', {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'text'})),
                            pct('% of term'), override('% of term', ('decimals', 0)), pct('At term end'),
                            override('At term end', ('decimals', 0)),
                            override('Days left', ('mappings', [{'type': 'range', 'options': {'from': -100000, 'to': -1,
                                     'result': {'text': 'Ended', 'index': 0}}}])),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('% of billed', 'Used', 'At term end')],
                            override('Billed', ('mappings', [{'type': 'special', 'options': {'match': 'null', 'result': {
                                'text': 'Not on the sheet', 'color': COLOUR['grey'], 'index': 0}}}])),
                            width('Customer', 160), width('Line', 200), width('Status', 140), width('% of billed', 160),
                            width('% of term', 80), width('Used', 125), width('Billed', 125), width('At term end', 125),
                            width('Term ends', 115), width('Days left', 80), width('Measure', 190),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer')),
                            with_links('Line', path_link('drill', 'Open line'))], noValue='No line matches the filter'),
        options={'showHeader': True, 'cellHeight': 'sm', 'footer': {'show': False}},
        may_be_empty='no line matches the filter'))
    return dashboard(UID['contracts'], 'N360 · Contracts', p, [var_account(), var_status()], [back_link()])


def forecast():
    """6. Forecast and runway (spec 6): when each line runs out, from the stored forecast only."""
    w30, w60, w90 = FORECAST_WINDOWS
    p = []
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
        field=field_config(mappings=amber_when_any('peach')), options={**STAT_LAST, 'colorMode': 'background'},
        links=[panel_link('contracts', 'Contracts')]))
    p.append(panel(5, 'Step changes', 'stat', f"""
        SELECT countIf(step_change IS NOT NULL) AS steps FROM n360.v_line_pace WHERE {ACCT}""", 20, 0, 4, 3,
        describe('Lines and pools whose last-30-day rate differs from their term average by more than 50% (flagged '
                 'upstream in the forecast).', 'n360.forecast via n360.v_line_pace', 'Customer', 'last 30 days against the term'),
        options=STAT_LAST))
    p.append(panel(6, 'Runway', 'state-timeline', f"""
        WITH lanes AS (
                 SELECT {PC_LABEL} AS lane, pc.data_through AS dt, pc.end_date AS term_end,
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
            '3': {'text': 'Crosses billed soon', 'color': COLOUR['lavender'], 'index': 0},
            '2': {'text': 'Crosses billed before term end', 'color': COLOUR['lavender_light'], 'index': 1},
            '1': {'text': 'Runs out after term end', 'color': COLOUR['grey'], 'index': 2},
            '9': {'text': 'After the term end', 'color': COLOUR['dark'], 'index': 3},
            '0': {'text': 'Runs out', 'color': 'transparent', 'index': 4}}}],
            displayName='${__field.labels.lane}', color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, **TIMELINE),
        options={'showValue': 'never', 'mergeValues': True, 'rowHeight': 0.8, 'alignValue': 'left',
                 'legend': {'showLegend': True, 'displayMode': 'list', 'placement': 'bottom'}}))
    p.append(panel(7, 'Runway by line', 'table', f"""
        SELECT {PC_LABEL} AS Line, pc.measure AS Measure, pc.used AS Used, pc.billed AS Billed,
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
        WHERE pc.{ACCT} AND pc.step_change IS NOT NULL""", 0, 27, 24, 6,
        describe('Lines whose last-30-day rate is more than 50% away from their term average: rising, falling or '
                 'stopped. The forecast uses the last 30 days, so a step change moves the runs-out date a lot.',
                 'n360.forecast via n360.v_line_pace', 'Customer; flagged lines', 'last 30 days against the term'),
        field=field_config([pct('Last 30 days vs term average')], noValue='No step changes'),
        may_be_empty='no line has a step change'))
    p.append(panel(9, 'Forecast accuracy (MAPE)', 'table', f"""
        SELECT Line, `30 days`, `60 days`, `90 days`, Forecasts FROM (
            SELECT 0 AS o, {PC_LABEL} AS Line,
                   anyIf(fa.mape, fa.horizon_days = 30) AS `30 days`, anyIf(fa.mape, fa.horizon_days = 60) AS `60 days`,
                   anyIf(fa.mape, fa.horizon_days = 90) AS `90 days`, maxIf(fa.forecasts, fa.horizon_days = 30) AS Forecasts
            FROM n360.v_forecast_accuracy AS fa
            INNER JOIN n360.v_line_pace AS pc ON pc.series_key = fa.series_key
            INNER JOIN n360.account AS a ON a.account = fa.account
            WHERE fa.{ACCT} GROUP BY Line
            UNION ALL
            SELECT 1, 'All lines (weighted by forecasts)',
                   round(sumIf(mape * forecasts, horizon_days = 30) / sumIf(forecasts, horizon_days = 30), 1),
                   round(sumIf(mape * forecasts, horizon_days = 60) / sumIf(forecasts, horizon_days = 60), 1),
                   round(sumIf(mape * forecasts, horizon_days = 90) / sumIf(forecasts, horizon_days = 90), 1),
                   sumIf(forecasts, horizon_days = 30)
            FROM n360.v_forecast_accuracy WHERE {ACCT} HAVING sumIf(forecasts, horizon_days = 30) > 0
        ) ORDER BY o, Line""", 0, 33, 24, 13,
        describe('How far the stored forecast was off: forecasts made 30, 60 and 90 days earlier against what was then '
                 'used (mean absolute % error; lower is better). The forecast is evaluated as stored, never refitted. '
                 'Shows once forecasts 30 days old are stored.', 'n360.v_forecast_accuracy (n360.forecast_history)',
                 'Customer; lines and pools with a billed amount', 'every stored night'),
        field=field_config([*[override(c, ('unit', 'percent'), ('decimals', 1)) for c in ('30 days', '60 days', '90 days')],
                            count_col('Forecasts'), width('Line', 380)],
                           noValue='Shows once forecasts 30 days old are stored'),
        may_be_empty='fewer than 30 nights of stored forecasts'))
    p.append(panel(10, 'Crossing date drift', 'table', f"""
        SELECT {PC_LABEL} AS Line,
               {date_sql('d.cross_now')} AS `Crosses billed`, {date_sql('d.cross_7_nights_ago')} AS `7 nights ago`,
               {date_sql('d.cross_30_nights_ago')} AS `30 nights ago`, d.moved_7 AS `Moved, 7 nights`,
               d.moved_30 AS `Moved, 30 nights`
        FROM n360.v_forecast_drift AS d
        INNER JOIN n360.v_line_pace AS pc ON pc.series_key = d.series_key
        INNER JOIN n360.account AS a ON a.account = d.account
        WHERE d.{ACCT} ORDER BY ifNull(d.moved_7, 0)""", 0, 46, 24, 8,
        describe('Where each line\'s crossing date stood 7 and 30 stored nights ago, and how many days it has moved '
                 '(negative = earlier). Empty where the forecast had no crossing date then ("Not reported yet" = no '
                 'crossing before the term end, or not stored).', 'n360.v_forecast_drift (n360.forecast_history)',
                 'Customer; lines that cross billed now or did then', 'stored nights'),
        field=field_config([*[override(c, ('mappings', [empty_grey()])) for c in
                              ('Crosses billed', '7 nights ago', '30 nights ago', 'Moved, 7 nights', 'Moved, 30 nights')],
                            width('Line', 380)], noValue='No line crosses billed'),
        may_be_empty='no line crosses billed'))
    p.append(panel(11, 'Challenger backtest (comparison only)', 'table', """
        SELECT concat(toString(horizon_days), ' days') AS Horizon,
               anyIf(mape, model = 'straight line') AS `Straight line (forecast of record)`,
               anyIf(mape, model = 'weekday HW') AS `Weekday Holt-Winters (challenger)`,
               maxIf(forecasts, model = 'straight line') AS Forecasts, toString(any(data_through)) AS `Data through`
        FROM n360.v_challenger_backtest GROUP BY horizon_days ORDER BY horizon_days""", 0, 54, 24, 6,
        describe('Mean absolute % error of each model when backtested at weekly cutoffs over the loaded history (all '
                 'lines and pools). The straight line stays the forecast of record; the challenger replaces it only if '
                 'it has the better 30-day MAPE on most lines for 8 straight weeks AND Ryan approves (08). Run '
                 'code/forecast/challenger.py to refresh; the full report is docs/10_CHALLENGER_BACKTEST.md.',
                 'n360.v_challenger_backtest (n360.forecast_challenger)', 'none (every customer)',
                 'the newest backtest run'),
        field=field_config([*[override(c, ('unit', 'percent'), ('decimals', 2)) for c in
                              ('Straight line (forecast of record)', 'Weekday Holt-Winters (challenger)')],
                            count_col('Forecasts')], noValue='No backtest run yet: run code/forecast/challenger.py'),
        may_be_empty='no backtest run yet'))
    p = with_header(p, f"""
        WITH (SELECT round(sumIf(mape * forecasts, horizon_days = 30) / nullIf(sumIf(forecasts, horizon_days = 30), 0), 1)
              FROM n360.v_forecast_accuracy WHERE {ACCT}) AS err
        SELECT concat({plural('countIf(status = ' + repr('will exceed') + ')', 'line or pool crosses', 'lines and pools cross')},
                      ' billed before the term ends',
                      if(countIf(status = 'will exceed') = 0, '', concat('; soonest: ', argMinIf(full_label, cross_date, status = 'will exceed'),
                         ' on ', {date_sql("minIf(cross_date, status = 'will exceed')")})),
                      ' · ', toString(countIf(status = 'over billed')), ' already over',
                      ' · 30-day forecast error ', if(err IS NULL, 'shows once 30 nights are stored', concat(toString(err), '%'))) AS sentence
        FROM n360.v_contract_status WHERE {ACCT}""",
        describe('Lines and pools the stored forecast says will cross billed before their term ends (the soonest named), '
                 'those already over, and how far the forecast was off 30 days ahead (MAPE, weighted by forecasts).',
                 'n360.v_contract_status, n360.v_forecast_accuracy', 'Customer', 'the latest load; every stored night'))
    return dashboard(UID['forecast'], 'N360 · Forecast and runway', p, [var_account()], [back_link()],
                     time={'from': 'now-14d', 'to': 'now+18M'})


def anomalies():
    """7. Anomalies (spec 7): drops, silences and spikes, settled vs provisional, cross-account events."""
    kind_sql = "if(${kind:singlequote} = 'spikes', kind = 'spike', kind IN ('drop', 'went silent'))"
    prov_sql = "(NOT provisional OR ${provisional:singlequote} = 'yes')"
    settled7 = f"NOT provisional AND day >= {DATA_THROUGH_SQL} - 9"
    p = []
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
               CAST(f.outcome, 'Nullable(String)') AS Review, f.note AS Note,
               concat('/d/n360-va?var-account=', f.account, '&var-va=', toString(f.bot_id)) AS drill
        FROM n360.v_anomaly_review AS f INNER JOIN n360.va AS v ON v.bot_id = f.bot_id
        INNER JOIN n360.account AS ac ON ac.account = f.account
        WHERE f.{ACCT} AND {kind_sql.replace('kind', 'f.kind').replace('${f.kind', '${kind')} AND {prov_sql.replace('provisional OR', 'f.provisional OR')} AND $__dateFilter(f.day)
        ORDER BY f.day DESC""", 0, 12, 24, 9,
        describe('Every flagged VA-day of the chosen kind in the time range, with its review outcome (real / expected / '
                 'data issue, or "Not reviewed"). Reviews are recorded with code/review_anomaly.py. '
                 'Click a VA to open it.', 'n360.v_anomaly_review (n360.v_va_day_anomaly, n360.anomaly_log)',
                 'Customer; Kind; Include provisional', 'dashboard time range'),
        field=field_config([hidden('drill'), count_col('Actual'), count_col('Expected'), override('z', ('decimals', 1)),
                            override('Note', ('mappings', [{'type': 'special', 'options': {'match': 'null', 'result': {'text': '', 'index': 0}}}])),
                            override('Review', ('mappings', label_colours(REVIEW_LABEL) + [{'type': 'special', 'options': {
                                'match': 'null', 'result': {'text': 'Not reviewed', 'color': COLOUR['grey'], 'index': 9}}}]),
                                     ('custom.cellOptions', {'type': 'color-text'})), width('Review', 100),
                            width('Day', 110), width('Customer', 150), width('VA', 170), width('Actual', 70),
                            width('Expected', 80), width('z', 60), width('Kind', 100), width('Status', 90),
                            coloured('Kind', KIND),
                            override('Status', ('mappings', label_colours(FLAG_STATE)),
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
    p.append(panel(9, 'Other signals: containment, measure, pace, freshness', 'table', f"""
        SELECT v2.signal AS Signal, ifNull(ac.display_name, '') AS Customer,
               multiIf(c1.full_label != '', c1.full_label, c2.full_label != '', c2.full_label, v2.subject) AS Subject,
               {date_sql('v2.period')} AS `When`,
               v2.value AS `This period`, v2.usual AS Usual, v2.detail AS Detail
        FROM n360.v_anomaly_v2 AS v2 LEFT JOIN n360.account AS ac ON ac.account = v2.account
        LEFT JOIN (SELECT line_key, full_label FROM n360.v_contract_status WHERE entitlement = 'line') AS c1 ON c1.line_key = v2.subject
        LEFT JOIN (SELECT series_key, full_label FROM n360.v_contract_status) AS c2 ON c2.series_key = v2.subject
        WHERE (v2.account = '' OR v2.{ACCT}) AND v2.period > {DATA_THROUGH_SQL} - 60
        ORDER BY v2.period DESC""", 0, 35, 24, 7,
        describe('Anomaly detection v2 (05): a VA\'s weekly containment more than 10 points below its 8-week median '
                 '(on 200+ engaged); a rule line\'s measure share of engaged more than 20 points from its usual level; '
                 'a crossing date moved more than 14 days earlier overnight; a source 2+ days behind. "This period" and '
                 '"Usual" are % (days moved, for pace change). Thresholds in n360.settings. Complete weeks only.',
                 'n360.v_anomaly_v2 (v_containment_week, v_measure_gap_week, v_forecast_change, v_source_freshness)',
                 'Customer', 'the 60 days to the data-through day'),
        field=field_config([width('Signal', 140), width('Customer', 140), width('When', 110), width('This period', 95),
                            width('Usual', 80), width('Subject', 260), override('Usual', ('mappings', [{'type': 'special', 'options': {
                                'match': 'null', 'result': {'text': '', 'index': 0}}}]))],
                           noValue='No other signals in the last 60 days'),
        may_be_empty='no v2 signal in the last 60 days'))
    p.append(panel(8, 'Flag quality per week', 'table', f"""
        SELECT {date_sql('week')} AS `Week of`, flags AS Flags, reviewed AS Reviewed, real AS Real, expected AS Expected,
               data_issue AS `Data issue`, pct_real_of_reviewed AS `Real, of reviewed`
        FROM n360.v_flag_quality WHERE $__dateFilter(week) ORDER BY week DESC""", 0, 28, 24, 7,
        describe('Settled flags per week and what reviews found. A low share of real flags means the thresholds '
                 '(n360.settings) flag too much; tuned monthly from these outcomes (05). All customers.',
                 'n360.v_flag_quality (n360.v_anomaly_review)', 'settled flags', 'dashboard time range, by week'),
        field=field_config([pct('Real, of reviewed')]), may_be_empty='no settled flags in the range'))
    p[-1]['fieldConfig']['overrides'].append(override('Real, of reviewed', ('mappings', [{'type': 'special', 'options': {
        'match': 'null', 'result': {'text': 'None reviewed', 'color': COLOUR['grey'], 'index': 0}}}])))
    p.append(panel(7, 'Settled flags per week', 'timeseries', f"""
        SELECT toDateTime(toMonday(day)) AS time, countIf(kind IN ('drop', 'went silent')) AS `Drops and silences`,
               countIf(kind = 'spike') AS Spikes
        FROM n360.v_va_day_anomaly WHERE {ACCT} AND NOT provisional AND $__dateFilter(day)
        GROUP BY time ORDER BY time""", 12, 21, 12, 7,
        describe('Settled flags per week (Monday start), drops and silences apart from spikes.', 'n360.v_va_day_anomaly',
                 'Customer; settled days', 'dashboard time range, by week'),
        fmt='timeseries',
        field=field_config([override('Drops and silences', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['down']})),
                            override('Spikes', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['up']}))],
                           decimals=0, custom={'drawStyle': 'bars', 'fillOpacity': 80, 'stacking': {'mode': 'normal', 'group': 'A'}})))
    p = with_header(p, f"""
        SELECT concat({plural("countIf(kind IN ('drop', 'went silent') AND " + settled7 + ")", 'drop or silence', 'drops or silences')},
                      ' and ', {plural("countIf(kind = 'spike' AND " + settled7 + ")", 'spike', 'spikes')},
                      ' on the 7 newest settled days · ', toString(countIf(kind != 'normal' AND provisional)), ' provisional · ',
                      toString((SELECT count() FROM n360.v_anomaly_events WHERE day > {DATA_THROUGH_SQL} - 30)),
                      ' cross-account ', if((SELECT count() FROM n360.v_anomaly_events WHERE day > {DATA_THROUGH_SQL} - 30) = 1, 'event', 'events'),
                      ' in 30 days') AS sentence
        FROM n360.v_va_day_anomaly WHERE {ACCT}""",
        describe('Flags on the 7 newest settled days (the newest 3 days are provisional and never alert), and days when '
                 '3 or more customers were flagged in the same direction.', 'n360.v_va_day_anomaly, n360.v_anomaly_events',
                 'Customer', 'the 7 newest settled days; 30 days for events'))
    return dashboard(UID['anomalies'], 'N360 · Anomalies', p,
                     [var_account(), custom_var('kind', 'Kind', [('Drops and silences', 'drops'), ('Spikes', 'spikes')]),
                      custom_var('provisional', 'Include provisional', [('No', 'no'), ('Yes', 'yes')])], [back_link()])


def containment():
    """8. Containment (spec 8): per VA only, never one number across channels."""
    p = []
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
    p = with_header(p, f"""
        SELECT concat({plural('countIf(containment_30 IS NOT NULL)', 'VA', 'VAs')}, ' with handoff data · ',
                      toString(countIf(containment_30 < (SELECT containment_low_pct FROM n360.settings))), ' contain under 50% · ',
                      toString(countIf(containment_down)), ' down 3+ points on the 30 days before · ',
                      toString(countIf(containment_30 IS NULL)), ' without handoff data (not reported)') AS sentence
        FROM n360.v_va_change WHERE {ACCT}""",
        describe('Containment = 1 - handoffs / engaged, per VA (never across channels), last 30 days against the 30 '
                 'before (proposed thresholds, n360.settings).', 'n360.v_va_change', 'Customer',
                 'the 60 days to the data-through day'))
    return dashboard(UID['containment'], 'N360 · Containment', p, [var_account()], [back_link()])


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
        FROM n360.runs ORDER BY loaded_at DESC, data_date DESC LIMIT 1""", 13, 0, 5, 3,
        describe(f'Data checks the latest run passed, of the {N_CHECKS} in code/checks.sql. Any failure stops the load.',
                 'n360.runs, code/checks.sql', 'none', 'the latest run'),
        options={**STAT_LAST, 'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value'}))
    p.append(panel(5, 'Stale sources', 'stat', """
        SELECT countIf(status != 'green') AS stale FROM n360.v_source_freshness""", 18, 0, 6, 3,
        describe('Sources whose last day is 2 or more days behind the data-through day (proposed thresholds, in '
                 'n360.v_source_freshness).', 'n360.v_source_freshness', 'none', 'the latest load'),
        field=field_config(mappings=amber_when_any()), options={**STAT_LAST, 'colorMode': 'background'}))
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
            '2': {'text': 'Stopped: data checks failed', 'color': COLOUR['amber'], 'index': 1},
            '0': {'text': 'No run', 'color': COLOUR['grey'], 'index': 2}}}],
            color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, noValue=''),
        options={'showValue': 'never', 'mergeValues': False, 'rowHeight': 0.9,
                 'legend': {'showLegend': True, 'displayMode': 'list', 'placement': 'bottom'}}))
    p.append(panel(7, 'Why nights stopped', 'table', f"""
        SELECT {date_sql('data_date')} AS Night, arrayStringConcat(checks_failed, ', ') AS `Checks failed`,
               formatDateTime(loaded_at, '%e %b %Y %H:%i UTC') AS Attempted
        FROM n360.runs WHERE status = 'failed_checks' ORDER BY loaded_at DESC, data_date DESC LIMIT 20""", 0, 7, 12, 6,
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
    p.append(panel(11, 'Days behind, night by night', 'timeseries', """
        SELECT toDateTime(data_date) AS time, source_id AS source, days_behind
        FROM n360.source_freshness WHERE $__dateFilter(data_date) ORDER BY time""", 0, 21, 24, 7,
        describe('How far each source\'s last day was behind the data-through day on every loaded night (0 = on time). '
                 'Amber from 2, red from 3 (n360.settings).', 'n360.source_freshness (append-only, one row per source '
                 'per night)', 'none', 'dashboard time range, by night'),
        fmt='timeseries', field=field_config(decimals=0, min=0, displayName='${__field.labels.source}',
                                             custom={'drawStyle': 'line', 'lineWidth': 2, 'spanNulls': False, 'axisSoftMax': 4},
                                             **series_colours()),
        may_be_empty='no night stored in the time range'))
    p.append(panel(9, 'Contract measure mix', 'table', """
        SELECT measure AS Measure, count() AS Lines FROM n360.contract_line GROUP BY measure ORDER BY Lines DESC""",
        0, 13, 12, 8,
        describe('How many contract lines are measured each way. Engaged = no contract rule applies (billable unit '
                 'unconfirmed).', 'n360.contract_line', 'none', 'the latest load')))
    p.append(panel(10, 'Contract rules not live', 'table', """
        SELECT label AS Rule, multiIf(status = 'pending_probe', 'Pending: probe not run yet', status = 'blocked', 'Blocked',
                                      status = 'active', 'Live but measuring no line', status) AS Status,
               counts_plain AS `What it would count`
        FROM n360.rule WHERE status != 'active' OR empty(lines_measured)""", 12, 13, 12, 8,
        describe('Contract rules that are pending or blocked, so their lines are measured on engaged for now.',
                 'n360.rule', 'rules not active, or measuring no line', 'the latest load'),
        field=field_config(noValue='Every rule is live'), may_be_empty='every rule is live'))
    p = with_header(p, f"""
        SELECT concat('Last night ', if(argMax(toString(status), (loaded_at, data_date)) = 'loaded', 'loaded',
                                         'was stopped by a data check (the previous good night stays on screen)'),
                      ' · ', toString((SELECT countIf(s = 'loaded') FROM (SELECT data_date, argMax(toString(status), loaded_at) AS s
                                       FROM n360.runs GROUP BY data_date) WHERE data_date > (SELECT max(data_date) FROM n360.runs) - 14)),
                      ' of the last 14 nights loaded · ',
                      {plural("(SELECT countIf(status != 'green') FROM n360.v_source_freshness)", 'source', 'sources')}, ' behind') AS sentence
        FROM n360.runs""",
        describe('The latest nightly run, nights loaded of the last 14, and sources 2 or more days behind.',
                 'n360.runs, n360.v_source_freshness', 'none', 'the last 14 nights'))
    return dashboard(UID['pipeline'], 'N360 · Pipeline health', p, links=[back_link()], time={'from': 'now-30d', 'to': 'now'})




# ───────────────────────── Adoption: Portfolio → Customer → VA (27 Nov 2026) ─────────────────────────
# Three linked pages on the Adoption detail tables (docs/25): n360.va_adoption, n360.playbook_window,
# n360.feature_window, n360.cortex_day, and the views v_va_feature_adoption, v_customer_feature_adoption,
# v_customer_adoption, v_playbook. Every state (adopted, partly, not yet, not reported) is decided in those views.

ADOPT = {'adopted': ('Adopted', 'green'), 'partly': ('Partly', 'amber'), 'none': ('Not yet', 'grey_light'),
         'not reported': (NOT_READ, 'grey')}
# (column, name in the views, what adopted means): the v1.1 Adoption view's definitions (docs/25)
FEATURES = [('Cortex', 'Cortex', 'Used to create or edit a production playbook in the last 30 days. Cortex is read for '
             'US VAs only and has no user id: whether Netomi or the customer ran a session is not known yet.'),
            ('Testing', 'Test / playbook validation', 'Run on every playbook version before promotion (Behavior '
             'Validation on, with test runs). Runs with validation off = partly.'),
            ('Calibration', 'Calibration', 'At least monthly on Tier 1 and Tier 2 use cases: a run in the last 31 days '
             '(n360.settings adoption_calibration_days). An older run = partly (lapsed).'),
            ('V8 / V9', 'V8 / V9', 'Production VA running on V8 or V9; target 100%. A customer is adopted only when '
             'every VA is.'),
            ('Recipes', 'Action-oriented recipes', 'At least one production recipe performs a write against a customer '
             'system. Recipes built but none writing in production = partly.'),
            ('Explorer', 'Explorer', 'At least one question by a customer user in the last 30 days, in Production or '
             'Staging; Netomi staff not counted.')]
BANDS = ['<200 (short)', '200-999', '1000-2999', '3000-5999', '6000-9999', '10000+']
ADOPT_NOTE = ('Newest read of the Adoption detail tables (docs/25): the pipeline replaces them whole each night, so '
              'these are the 31-day window to the data-through day, not the dashboard\'s time range.')
ONE_VA = "toString(bot_id) IN (${va:singlequote})"
ADOPT_CUSTOMER = ('n360-adoption-customer', 'Customer adoption')
ADOPT_VA = ('n360-adoption-va', 'VA adoption')


def adopt_cells(columns, cell='color-background'):
    """Feature cells: the view writes "state|words"; the cell shows the words on the state's colour.
    Regex mappings with a capture group ($1): one column carries both the colour and the evidence."""
    maps = [{'type': 'regex', 'options': {'pattern': f'^{k}\\|(.*)$', 'result': {'text': '$1', 'color': COLOUR[c], 'index': i}}}
            for i, (k, (_, c)) in enumerate(ADOPT.items())]
    return [override(col, ('mappings', maps + [empty_grey()]), ('custom.cellOptions', {'type': cell}),
                     ('color', {'mode': 'fixed', 'fixedColor': COLOUR['none']}), ('description', means), ('custom.align', 'center'), ('custom.minWidth', 150))
            for col, means in columns]


def gauge_cell(column, max_value, colour='green', unit=None):
    """A number drawn as a bar inside its cell (the colour is fixed: a bar here is a size, not a status)."""
    props = [('custom.cellOptions', {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'text'}),
             ('color', {'mode': 'fixed', 'fixedColor': COLOUR[colour]}), ('min', 0)]
    if max_value is not None:
        props.append(('max', max_value))
    if unit:
        props.append(('unit', unit))
    return override(column, *props)


def adoption():
    """Adoption, the portfolio: which features each customer has adopted, how much the playbooks carry, and what to move
    next. Customer architects, CSMs, product. Drills: a customer → Customer adoption → VA adoption."""
    p = page_header(1, f"""
        WITH (SELECT topK(1)(next_feature)[1] FROM n360.v_customer_adoption WHERE next_feature != '' AND {ACCT}) AS common_next,
             (SELECT countIf(next_feature = common_next) FROM n360.v_customer_adoption WHERE {ACCT}) AS n_next
        SELECT if(count() = 0, NULL, concat(
                   toString(countIf(features_adopted = 6)), ' of ', {plural('count()', 'customer', 'customers')},
                   ' use all six features · playbooks ran in ', {num_sql('sum(conversations)')}, ' conversations in 31 days, ',
                   toString(round(100 * sum(handoffs) / nullIf(sum(conversations), 0))), '% handed off',
                   if(common_next = '', '', concat(' · most common next step: ', common_next, ' (', toString(n_next), ')')))) AS sentence
        FROM n360.v_customer_adoption WHERE {ACCT}""",
        describe('Customers using all six features (the v1.1 definitions), the conversations that ran at least one playbook '
                 'and the share handed off to an agent from within one, and the feature most customers should move to next '
                 '(the first not adopted, in the agreed order n360.settings adoption_next_order: V8 / V9, Cortex, Recipes, then the rest). ' + ADOPT_NOTE,
                 'n360.v_customer_adoption', 'Customer', 'the 31 days to the data-through day'))
    p.append(single_tile(3, 'Playbook executions, 31 days', f"""
        SELECT if(count() = 0, NULL, sum(executions)) AS `across every V8 playbook` FROM n360.v_customer_adoption WHERE {ACCT}""", 0, 3,
        describe('Playbook executions on V8 VAs in the 31 days to the data-through day: the same figure as the v1.1 page\'s '
                 'first tile. Not added to Cortex sessions or Explorer questions (different things). ' + ADOPT_NOTE,
                 'n360.va_adoption', 'Customer', 'the 31 days to the data-through day'),
        links=[{'title': 'Every playbook, busiest first', 'url': f"/d/{ADOPT_CUSTOMER[0]}?${{__url_time_range}}"}]))
    p.append(single_tile(4, 'Handed off from a playbook', f"""
        SELECT if(sum(conversations) = 0, NULL, round(100 * sum(handoffs) / sum(conversations), 1)) AS `of conversations that ran one`
        FROM n360.v_customer_adoption WHERE {ACCT}""", 6, 3,
        describe('Handoffs to an agent from within a playbook, as a share of the conversations that ran at least one '
                 'playbook. ' + ADOPT_NOTE, 'n360.va_adoption', 'Customer', 'the 31 days to the data-through day'),
        field=plain(field_config(unit='percent', decimals=1))))
    p.append(tile(5, 'VAs on V8 or later', f"""
        SELECT snapshot_date AS night, toUInt64(countIf(engine_major >= (SELECT engine_current_major FROM n360.settings))) AS value
        FROM n360.va_config_snapshot WHERE {ACCT} GROUP BY night""", 12, 3,
        describe('VAs whose weekly configuration read shows V8 or later; change against the read 30 days earlier.',
                 'n360.va_config_snapshot', 'Customer', 'weekly reads, the last 30 days'), metric='vas on v8'))
    p.append(single_tile(6, 'Customers on all six features', f"""
        SELECT if(count() = 0, NULL, countIf(features_adopted = 6)) AS `customers` FROM n360.v_customer_adoption WHERE {ACCT}""", 18, 3,
        describe('Customers that have adopted every one of the six features (the grid below shows where the rest stand). '
                 + ADOPT_NOTE, 'n360.v_customer_adoption', 'Customer', 'the newest read')))
    # lead: the grid, one row per customer
    cols = ',\n               '.join(f"anyIf(concat(f.state, '|', f.value), f.feature = '{name}') AS `{col}`" for col, name, _ in FEATURES)
    p.append(panel(7, 'Six features, every customer', 'table', f"""
        SELECT a.display_name AS Customer, c.features_adopted AS `Adopted, of 6`, {cols},
               if(c.next_feature = '', 'Every feature adopted', c.next_feature) AS `Next step`,
               c.executions AS `Executions, 31 days`, c.handoff_pct AS `Handed off`, a.account AS account
        FROM n360.v_customer_feature_adoption AS f INNER JOIN n360.account AS a ON a.account = f.account
        INNER JOIN n360.v_customer_adoption AS c ON c.account = f.account
        WHERE f.{ACCT}
        GROUP BY a.display_name, a.account, c.features_adopted, c.next_feature, c.executions, c.handoff_pct
        ORDER BY `Adopted, of 6` DESC, `Executions, 31 days` DESC""", 0, 8, 24, 18,
        describe('Each customer against the six features of the v1.1 Adoption view. A cell is green when adopted, amber '
                 'when partly (for example tests run with validation off, or calibration lapsed), light grey when not yet, '
                 'grey when the source has nothing ("Not reported yet", never "not yet"). Hover a column heading for what '
                 'adopted means. Adopted, of 6 is drawn as a bar. Next step: the first feature not adopted, in the agreed '
                 'order (n360.settings adoption_next_order). Click a customer for its VAs and playbooks. ' + ADOPT_NOTE,
                 'n360.v_customer_feature_adoption, n360.v_customer_adoption', 'Customer', 'the newest read'),
        field=field_config([hidden('account'), *adopt_cells([(c, m) for c, _, m in FEATURES]),
                            gauge_cell('Adopted, of 6', 6), count_col('Executions, 31 days'), pct('Handed off'),
                            override('Next step', ('description', 'The first of the six not adopted, in the agreed order '
                                                   'V8 / V9 → Cortex → Recipes → Testing → Calibration → Explorer.')),
                            width('Customer', 170), width('Adopted, of 6', 120), width('Next step', 190),
                            with_links('Customer', drill(ADOPT_CUSTOMER[0], 'account', 'account', 'Open this customer\'s adoption'),
                                       drill(UID['customer'], 'account', 'account', 'Open the Customer page'))]),
        options={'cellHeight': 'md', 'sortBy': []}))
    # where each feature stands, and the next-step queue
    states = ',\n               '.join(f"countIf(state = '{k}') AS `{t}`" for k, (t, _) in ADOPT.items())
    p.append(panel(8, 'Where each feature stands', 'barchart', f"""
        SELECT feature AS Feature, {states}
        FROM n360.v_customer_feature_adoption WHERE {ACCT} GROUP BY ord, feature ORDER BY ord""", 0, 26, 12, 9,
        describe('For each feature, how many customers have adopted it, partly, not yet, or are not reported. Hover a bar '
                 'for the counts. ' + ADOPT_NOTE, 'n360.v_customer_feature_adoption', 'Customer', 'the newest read'),
        field=field_config([override(t, ('color', {'mode': 'fixed', 'fixedColor': COLOUR[c]})) for t, c in ADOPT.values()],
                           decimals=0, min=0, custom={'fillOpacity': 90, 'lineWidth': 0, 'axisGridShow': False}),
        options={'xField': 'Feature', 'orientation': 'horizontal', 'stacking': 'normal', 'showValue': 'auto', 'barWidth': 0.75,
                 'xTickLabelMaxLength': 30,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}))
    p.append(panel(9, 'Next step queue', 'table', f"""
        SELECT if(c.next_feature = '', 'Every feature adopted', c.next_feature) AS `Next step`,
               count() AS Customers, arrayStringConcat(arraySort(groupArray(a.display_name)), ', ') AS Who,
               sum(c.executions) AS `Their executions, 31 days`,
               indexOf((SELECT adoption_next_order FROM n360.settings), c.next_feature) AS o
        FROM n360.v_customer_adoption AS c INNER JOIN n360.account AS a ON a.account = c.account
        WHERE c.{ACCT} GROUP BY c.next_feature ORDER BY o = 0, o""", 12, 26, 12, 9,
        describe('Customers grouped by the next feature to move them to (the first of the six not adopted, in the agreed '
                 'order n360.settings adoption_next_order), with the playbook traffic behind them: the biggest group with '
                 'the most traffic is the campaign to run first. ' + ADOPT_NOTE,
                 'n360.v_customer_adoption', 'Customer', 'the newest read'),
        field=field_config([hidden('o'), count_col('Their executions, 31 days'),
                            width('Next step', 180), width('Customers', 110)]),
        options={'cellHeight': 'md'}))
    # the playbook estate per customer
    p.append(panel(10, 'Playbook estate by customer', 'table', f"""
        SELECT a.display_name AS Customer, concat(toString(c.vas_v8), ' of ', toString(c.vas)) AS `VAs on V8+`,
               c.playbooks_live AS `Playbooks live`, c.playbooks_executed AS `Ran in 31 days`, c.idle_playbooks AS `Live but idle`,
               c.executions AS Executions, c.conversations AS `Conversations with a playbook`, c.handoff_pct AS `Handed off`,
               c.legacy_intents AS `Legacy intents left`, {date_sql('c.last_execution')} AS `Last execution`, a.account AS account
        FROM n360.v_customer_adoption AS c INNER JOIN n360.account AS a ON a.account = c.account
        WHERE c.{ACCT} ORDER BY Executions DESC""", 0, 35, 24, 13,
        describe('Per customer: VAs on V8 or later, playbooks live and how many ran in 31 days, live-but-idle playbooks, '
                 'executions (bar), conversations that ran a playbook, the share handed off from within one, and the legacy '
                 'intents still to move. Click a customer to open its adoption. ' + ADOPT_NOTE,
                 'n360.v_customer_adoption', 'Customer', 'the 31 days to the data-through day'),
        field=field_config([hidden('account'), gauge_cell('Executions', None, 'series'), count_col('Conversations with a playbook'),
                            pct('Handed off'), amber_cell('Live but idle'), width('Customer', 170), width('Executions', 220),
                            with_links('Customer', drill(ADOPT_CUSTOMER[0], 'account', 'account', 'Open this customer\'s adoption'))]),
        options={'cellHeight': 'sm'}))
    # complexity and who builds
    bands = ',\n               '.join(f"countIf(length_band = '{b}') AS `{b}`" for b in BANDS)
    p.append(panel(11, 'Playbook length, by customer', 'barchart', f"""
        SELECT a.display_name AS Customer, {bands}
        FROM n360.v_playbook AS pb INNER JOIN n360.account AS a ON a.account = pb.account
        WHERE pb.{ACCT} GROUP BY Customer ORDER BY count() DESC""", 0, 48, 12, 10,
        describe('Production playbooks per customer by the length of their instructions (characters), shortest band palest. '
                 'Long playbooks (10,000+) are the hardest to test and to change; short ones (under 200) may be '
                 'placeholders. Hover for the counts. ' + ADOPT_NOTE, 'n360.v_playbook', 'Customer', 'the newest read'),
        field=field_config(decimals=0, min=0, custom={'fillOpacity': 90, 'lineWidth': 0, 'axisGridShow': False},
                           **series_colours()),
        options={'xField': 'Customer', 'orientation': 'horizontal', 'stacking': 'normal', 'showValue': 'never', 'barWidth': 0.8,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}, may_be_empty='no playbook read'))
    p.append(panel(12, 'Who builds the playbooks', 'barchart', f"""
        SELECT a.display_name AS Customer, countIf(pb.built_by = 'Customer') AS `The customer`, countIf(pb.built_by = 'Netomi') AS Netomi,
               countIf(pb.built_by IS NULL) AS `Not reported`
        FROM n360.v_playbook AS pb INNER JOIN n360.account AS a ON a.account = pb.account
        WHERE pb.{ACCT} GROUP BY Customer ORDER BY countIf(pb.built_by = 'Customer') / count() DESC""", 12, 48, 12, 10,
        describe('Each customer\'s production playbooks by who created them (the creator\'s email domain: Netomi\'s '
                 'domain = Netomi, any other = the customer, n360.settings adoption_staff_domain), as a share. The customers '
                 'at the top build for themselves; at the bottom, Netomi does the work. ' + ADOPT_NOTE,
                 'n360.v_playbook', 'Customer', 'the newest read'),
        field=field_config([override('The customer', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['green']})),
                            override('Netomi', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']})),
                            override('Not reported', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['grey']}))],
                           decimals=0, min=0, max=1, custom={'fillOpacity': 90, 'lineWidth': 0, 'axisGridShow': False}),
        options={'xField': 'Customer', 'orientation': 'horizontal', 'stacking': 'percent', 'showValue': 'never', 'barWidth': 0.8,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}, may_be_empty='no playbook read'))
    # history: Cortex daily, V8 weekly
    p.append(panel(13, 'Cortex sessions per day', 'timeseries', f"""
        SELECT toDateTime(c.day) AS time, a.display_name AS Customer, sum(c.authoring_sessions) AS Sessions
        FROM n360.cortex_day AS c INNER JOIN n360.account AS a ON a.account = c.account
        WHERE c.{ACCT} AND $__dateFilter(c.day) GROUP BY time, Customer ORDER BY time""", 0, 58, 12, 9,
        describe('Cortex sessions per day, stacked by customer (US VAs only; who ran a session is not known yet). Read '
                 'since 5 Oct 2026; a day read with no session is 0. Hover for every customer that day.',
                 'n360.cortex_day', 'Customer', 'dashboard time range'),
        fmt='timeseries', field=field_config(decimals=0, min=0, displayName='${__field.labels.Customer}',
                                             custom={'drawStyle': 'bars', 'fillOpacity': 80, 'lineWidth': 0, 'barWidthFactor': 0.9,
                                                     'stacking': {'mode': 'normal', 'group': 'A'}}, **series_colours()),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}, may_be_empty='no Cortex day read'))
    p[-1]['transformations'] = [{'id': 'prepareTimeSeries', 'options': {'format': 'multi'}}]
    p.append(panel(14, 'The move to V8, week by week', 'timeseries', f"""
        SELECT toDateTime(snapshot_date) AS time,
               countIf(engine_major >= (SELECT engine_current_major FROM n360.settings)) AS `V8 or later`,
               countIf(engine_major < (SELECT engine_current_major FROM n360.settings)) AS `Below V8`,
               sum(legacy_intents) AS `Legacy intents left`
        FROM n360.va_config_snapshot WHERE {ACCT} AND $__dateFilter(snapshot_date) GROUP BY time ORDER BY time""", 12, 58, 12, 9,
        describe('Each weekly configuration read: VAs on V8 or later (green) and below (amber), and the legacy intents still '
                 'configured (dashed, right axis): the upgrade backlog shrinking.',
                 'n360.va_config_snapshot', 'Customer', 'dashboard time range, weekly reads'),
        fmt='timeseries', field=field_config(
            [override('V8 or later', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['green']})),
             override('Below V8', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['amber']})),
             override('Legacy intents left', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['reference']}),
                      ('custom.axisPlacement', 'right'), ('custom.lineStyle', {'fill': 'dash', 'dash': [6, 4]}),
                      ('custom.drawStyle', 'line'), ('custom.fillOpacity', 0))],
            decimals=0, min=0, custom={'drawStyle': 'bars', 'fillOpacity': 85, 'lineWidth': 2, 'stacking': {'mode': 'normal', 'group': 'A'}}),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}, may_be_empty='no configuration read'))
    # action lists
    p.append(panel(15, 'Longest playbooks', 'table', f"""
        SELECT pb.playbook AS Playbook, a.display_name AS Customer, pb.va AS VA, pb.instruction_chars AS `Instructions (characters)`,
               pb.tools AS Tools, pb.executions AS `Executions, 31 days`, ifNull(pb.built_by, '') AS `Built by`,
               pb.account AS account, toString(pb.bot_id) AS bot
        FROM n360.v_playbook AS pb INNER JOIN n360.account AS a ON a.account = pb.account
        WHERE pb.{ACCT} ORDER BY `Instructions (characters)` DESC LIMIT 15""", 0, 67, 12, 9,
        describe('The 15 longest production playbooks (instruction characters, drawn as a bar), with their tools and '
                 'traffic. Long and busy = worth splitting or testing first. Click a VA for its playbooks. ' + ADOPT_NOTE,
                 'n360.v_playbook', 'Customer', 'the newest read'),
        field=field_config([hidden('account'), hidden('bot'), gauge_cell('Instructions (characters)', None, 'lavender'),
                            count_col('Executions, 31 days'), width('Playbook', 190), width('Instructions (characters)', 190),
                            with_links('VA', {'title': 'Open this VA\'s adoption', 'url': f"/d/{ADOPT_VA[0]}?var-account=${{__data.fields.account}}&var-va=${{__data.fields.bot}}&${{__url_time_range}}"})]),
        options={'cellHeight': 'sm'}, may_be_empty='no playbook read'))
    p.append(panel(16, 'Live but idle: no run in 31 days', 'table', f"""
        SELECT pb.playbook AS Playbook, a.display_name AS Customer, pb.va AS VA, pb.instruction_chars AS `Instructions (characters)`,
               ifNull(pb.built_by, '') AS `Built by`, pb.account AS account, toString(pb.bot_id) AS bot
        FROM n360.v_playbook AS pb INNER JOIN n360.account AS a ON a.account = pb.account
        WHERE pb.{ACCT} AND pb.idle ORDER BY Customer, VA""", 12, 67, 12, 9,
        describe('V8 playbooks in production with no execution in their window: built, switched on, never triggered. Check '
                 'the trigger, or retire them. Click a VA for its playbooks. ' + ADOPT_NOTE, 'n360.v_playbook', 'Customer',
                 'the newest read'),
        field=field_config([hidden('account'), hidden('bot'), count_col('Instructions (characters)'), width('Playbook', 200),
                            with_links('VA', {'title': 'Open this VA\'s adoption', 'url': f"/d/{ADOPT_VA[0]}?var-account=${{__data.fields.account}}&var-va=${{__data.fields.bot}}&${{__url_time_range}}"})],
                           noValue='No idle playbook'),
        options={'cellHeight': 'sm'}, may_be_empty='no idle playbook'))
    p.append(panel(17, 'Upgrade queue: VAs below V8, busiest first', 'table', f"""
        SELECT c.va AS VA, a.display_name AS Customer, ad.engine_version AS Engine, ad.legacy_intents AS `Legacy intents`,
               c.convs_30 AS `Conversations, 30 days`,
               concat('/d/{ADOPT_VA[0]}?var-account=', c.account, '&var-va=', toString(c.bot_id)) AS drill
        FROM n360.v_va_change AS c INNER JOIN n360.va_adoption AS ad ON ad.bot_id = c.bot_id
        INNER JOIN n360.account AS a ON a.account = c.account
        WHERE c.{ACCT} AND ad.engine_major < (SELECT engine_current_major FROM n360.settings)
        ORDER BY `Conversations, 30 days` DESC""", 0, 76, 12, 8,
        describe('VAs still on an engine older than V8, ranked by conversations in the last 30 days: the first row is the '
                 'one to upgrade next (most traffic moves). ' + ADOPT_NOTE, 'n360.v_va_change, n360.va_adoption',
                 'Customer', 'the newest read; the 30 days to the data-through day'),
        field=field_config([hidden('drill'), count_col('Conversations, 30 days'), width('VA', 170),
                            with_links('VA', path_link('drill', 'Open this VA\'s adoption'))], noValue='Every VA is on V8 or later'),
        options={'cellHeight': 'sm'}, may_be_empty='every VA on V8'))
    p.append(panel(18, 'Configuration changes since the last read', 'table', f"""
        SELECT {VA_LABEL} AS VA, ac.display_name AS Customer, {date_sql('ch.snapshot_date')} AS `Read on`,
               arrayStringConcat(ch.changes, '; ') AS Changes,
               concat('/d/{ADOPT_VA[0]}?var-account=', ch.account, '&var-va=', toString(ch.bot_id)) AS drill
        FROM n360.v_va_config_change AS ch INNER JOIN n360.va AS v ON v.bot_id = ch.bot_id
        INNER JOIN n360.account AS ac ON ac.account = ch.account
        WHERE ch.{ACCT}""", 12, 76, 12, 8,
        describe('What changed per VA between its two newest weekly configuration reads.', 'n360.v_va_config_change',
                 'Customer', 'the two newest reads per VA'),
        field=field_config([hidden('drill'), width('VA', 170), width('Customer', 140), width('Read on', 105),
                            with_links('VA', path_link('drill', 'Open this VA\'s adoption'))], noValue='No changes since the last read'),
        options={'cellHeight': 'sm'}, may_be_empty='no change since the last read'))
    p.append(panel(19, 'Containment by channel', 'table', f"""
        SELECT c.channel AS Channel, c.va AS VA, a.display_name AS Customer, c.containment_30 AS `Last 30 days`,
               c.containment_prev_30 AS `30 before`,
               multiIf(c.containment_change_pts IS NULL, NULL, c.containment_change_pts >= 1, concat('▲ ', toString(c.containment_change_pts)),
                       c.containment_change_pts <= -1, concat('▼ ', toString(abs(c.containment_change_pts))), '● steady') AS Change,
               concat('/d/n360-va?var-account=', c.account, '&var-va=', toString(c.bot_id)) AS drill
        FROM n360.v_va_change AS c INNER JOIN n360.account AS a ON a.account = c.account
        WHERE c.{ACCT} AND c.containment_30 IS NOT NULL ORDER BY Channel, `Last 30 days`""", 0, 84, 12, 9,
        describe('Containment per VA, grouped by channel (never one number across channels), last 30 days against the 30 '
                 'before; the change is in points (▼ down, ▲ up). VAs without handoff data are on the Containment '
                 'dashboard.', 'n360.v_va_change', 'Customer; VAs with handoff data', 'the 60 days to the data-through day'),
        field=field_config([hidden('drill'), *[override(c, ('unit', 'percent'), ('decimals', 0)) for c in ('Last 30 days', '30 before')],
                            override('Change', ('mappings', [{'type': 'regex', 'options': {'pattern': '^▼.*', 'result': {
                                'color': COLOUR['down'], 'index': 0}}}]), ('custom.cellOptions', {'type': 'color-text'})),
                            width('Channel', 70), width('VA', 160), width('Customer', 130), width('Last 30 days', 95),
                            width('30 before', 85), with_links('VA', path_link('drill', 'Open VA'))]),
        options={'cellHeight': 'sm'}))
    feats = ['V8 playbooks', 'Cortex', 'Explorer', 'MCP servers', 'Supervisory rules']
    cols5 = ',\n               '.join(f"anyIf(if(f.used IS NULL, NULL, if(f.used, 'Used', 'Not used')), f.feature = '{x}') AS `{x}`"
                                     for x in feats)
    p.append(panel(20, 'Target features used (the risk model\'s list)', 'table', f"""
        SELECT a.display_name AS Customer, {cols5}, a.account AS account
        FROM n360.account AS a INNER JOIN n360.v_feature_use AS f ON f.account = a.account
        WHERE a.{ACCT} GROUP BY a.display_name, a.account ORDER BY Customer""", 12, 84, 12, 9,
        describe('The five target features the risk model and the Overview count ("customers using 3+ target features"): '
                 'used in the last 30 days (green) or not (light grey); "Not reported yet" where the source has no rows.',
                 'n360.v_feature_use', 'Customer', 'the 30 days to the data-through day'),
        field=field_config([hidden('account'),
                            *[coloured(x, {'Used': ('Used', 'green'), 'Not used': ('Not used', 'grey_light')}, 'color-background')
                              for x in feats],
                            width('Customer', 140), with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))]),
        options={'cellHeight': 'sm'}))
    p.append(cortex_split_by_customer(21, 0, 93, 12, 10))
    p.append(executions_by_builder(22, 12, 93, 12, 10))
    return dashboard(UID['adoption'], 'N360 · Adoption', p, [var_account()],
                     [back_link(), {**back_link(('n360-cortex', 'Cortex by Netomians')), 'title': 'Cortex by Netomians'}])


def amber_cell(column):
    """A count that needs a look when above zero: amber text then, plain at zero."""
    return override(column, ('mappings', amber_when_any()), ('custom.cellOptions', {'type': 'color-text'}),
                    ('color', {'mode': 'fixed', 'fixedColor': COLOUR['none']}))


def adoption_customer():
    """Customer adoption: one customer's six features VA by VA, its playbooks, and who does the work.
    From the Adoption grid (or the Customer page); drills to VA adoption."""
    p = page_header(1, f"""
        SELECT if(count() = 0, NULL, concat(any(a.display_name), ': ', toString(any(c.features_adopted)), ' of 6 features · ',
                   'playbooks ran in ', {num_sql('any(c.conversations)')}, ' conversations in 31 days, ',
                   toString(round(any(c.handoff_pct))), '% handed off · ',
                   if(any(c.next_feature) = '', 'every feature adopted', concat('next step: ', any(c.next_feature))))) AS sentence
        FROM n360.v_customer_adoption AS c INNER JOIN n360.account AS a ON a.account = c.account WHERE c.{ACCT}""",
        describe('The customer\'s features adopted (of the six), the conversations that ran at least one playbook and the '
                 'share handed off from within one, and the next feature to move to (V8 / V9, Cortex, Recipes, then the rest). ' + ADOPT_NOTE,
                 'n360.v_customer_adoption', 'Customer', 'the 31 days to the data-through day'))
    p.append(single_tile(3, 'Playbook executions, 31 days', f"""
        SELECT if(count() = 0, NULL, sum(executions)) AS `across every V8 playbook` FROM n360.va_adoption WHERE {ACCT}""", 0, 3,
        describe('Playbook executions on V8 VAs in the 31 days to the data-through day. ' + ADOPT_NOTE,
                 'n360.va_adoption', 'Customer', 'the 31 days to the data-through day')))
    p.append(single_tile(4, 'Conversations with a playbook', f"""
        SELECT if(count() = 0, NULL, sum(conversations)) AS `ran at least one` FROM n360.va_adoption WHERE {ACCT}""", 6, 3,
        describe('Conversations that ran at least one playbook, 31 days. ' + ADOPT_NOTE, 'n360.va_adoption', 'Customer',
                 'the 31 days to the data-through day')))
    p.append(single_tile(5, 'Handed off from a playbook', f"""
        SELECT if(sum(conversations) = 0, NULL, round(100 * sum(handoffs) / sum(conversations), 1)) AS `of those conversations`
        FROM n360.va_adoption WHERE {ACCT}""", 12, 3,
        describe('Handoffs to an agent from within a playbook, as a share of the conversations that ran one. ' + ADOPT_NOTE,
                 'n360.va_adoption', 'Customer', 'the 31 days to the data-through day'),
        field=plain(field_config(unit='percent', decimals=1))))
    p.append(single_tile(6, 'Features adopted', f"""
        SELECT if(count() = 0, NULL, any(features_adopted)) AS `of 6` FROM n360.v_customer_adoption WHERE {ACCT}""", 18, 3,
        describe('Of the six features, how many this customer has adopted on at least one VA (V8 / V9: on every VA). '
                 + ADOPT_NOTE, 'n360.v_customer_adoption', 'Customer', 'the newest read')))
    cols = ',\n               '.join(f"anyIf(concat(f.state, '|', f.value), f.feature = '{name}') AS `{col}`" for col, name, _ in FEATURES)
    p.append(panel(7, 'Six features, VA by VA', 'table', f"""
        SELECT f.va AS VA, any(f.region) AS Region, countIf(f.state = 'adopted') AS `Adopted, of 6`, {cols},
               toString(f.bot_id) AS bot, any(f.account) AS acct
        FROM n360.v_va_feature_adoption AS f WHERE f.{ACCT} GROUP BY f.va, f.bot_id ORDER BY `Adopted, of 6` DESC, VA""", 0, 8, 24, 6,
        describe('Each VA against the six features: green adopted, amber partly, light grey not yet, grey not reported. '
                 'Hover a heading for what adopted means; the evidence behind each cell is in the table below. Click a VA '
                 'for its playbooks and configuration. ' + ADOPT_NOTE, 'n360.v_va_feature_adoption', 'Customer', 'the newest read'),
        field=field_config([hidden('bot'), hidden('acct'), *adopt_cells([(c, m) for c, _, m in FEATURES]),
                            gauge_cell('Adopted, of 6', 6), width('VA', 170), width('Region', 70), width('Adopted, of 6', 120),
                            with_links('VA', {'title': 'Open this VA\'s adoption', 'url': f"/d/{ADOPT_VA[0]}?var-account=${{__data.fields.acct}}&var-va=${{__data.fields.bot}}&${{__url_time_range}}"},
                                       {'title': 'Open the VA page (traffic, anomalies)', 'url': "/d/n360-va?var-account=${__data.fields.acct}&var-va=${__data.fields.bot}&${__url_time_range}"})]),
        options={'cellHeight': 'md'}))
    p.append(panel(8, 'The evidence behind each feature', 'table', f"""
        SELECT feature AS Feature, concat(state, '|', value) AS `This customer`, detail AS `VA by VA`, means AS `What adopted means`
        FROM n360.v_customer_feature_adoption WHERE {ACCT} ORDER BY ord""", 0, 14, 24, 9,
        describe('For each feature: where the customer stands, every VA\'s reading, and the definition it is judged by. '
                 + ADOPT_NOTE, 'n360.v_customer_feature_adoption', 'Customer', 'the newest read'),
        field=field_config([*adopt_cells([('This customer', 'Adopted on any VA; V8 / V9 only when every VA is.')]),
                            width('Feature', 190), width('This customer', 190), override('VA by VA', ('custom.cellOptions', {'type': 'auto', 'wrapText': True}))]),
        options={'cellHeight': 'sm'}))
    p.append(panel(9, 'Executions by playbook', 'barchart', f"""
        SELECT concat(playbook, '  ·  ', va) AS Playbook, executions AS Executions
        FROM n360.v_playbook WHERE {ACCT} AND executions > 0 ORDER BY executions DESC LIMIT 20""", 0, 23, 12, 11,
        describe('The 20 busiest playbooks over 31 days. Hover a bar for the count. Long tails are normal; one playbook '
                 'carrying most of a VA is a single point of failure. ' + ADOPT_NOTE, 'n360.v_playbook', 'Customer',
                 'the newest read'),
        field=field_config(decimals=0, min=0, custom={'fillOpacity': 85, 'lineWidth': 0, 'axisGridShow': False}, **series_colours()),
        options={'xField': 'Playbook', 'orientation': 'horizontal', 'showValue': 'always', 'barWidth': 0.75,
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no playbook executed'))
    p.append(panel(10, 'Length against traffic', 'xychart', f"""
        SELECT instruction_chars AS `Instructions (characters)`, executions AS `Executions, 31 days`, playbook AS Playbook,
               va AS VA
        FROM n360.v_playbook WHERE {ACCT} AND executions IS NOT NULL ORDER BY instruction_chars""", 12, 23, 12, 11,
        describe('Every production playbook as a point: instruction length across, executions up. Top right (long and busy) '
                 'is where a change is riskiest and testing matters most; bottom right (long and quiet) is effort that is '
                 'not used. Hover a point for the playbook. ' + ADOPT_NOTE, 'n360.v_playbook', 'Customer', 'the newest read'),
        field=field_config(decimals=0, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['lavender']},
                           custom={'show': 'points', 'pointSize': {'fixed': 9}}),
        options={'mapping': 'auto', 'series': [{}], 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no playbook read'))
    p.append(panel(11, 'Every playbook', 'table', f"""
        SELECT playbook AS Playbook, va AS VA, length_band AS Length, instruction_chars AS `Instructions (characters)`, tools AS Tools,
               trigger_chars AS `Trigger (characters)`, if(short, 'yes', 'no') AS `Short (<200)`,
               executions AS `Executions, 31 days`, share_of_va_pct AS `Share of the VA`,
               multiIf(idle IS NULL, NULL, idle, 'Idle', 'Ran') AS `In use`, ifNull(built_by, '') AS `Built by`,
               toString(bot_id) AS bot, account
        FROM n360.v_playbook WHERE {ACCT} ORDER BY VA, rank_on_va""", 0, 34, 24, 10,
        describe('Every production playbook (the v1.1 complexity table): instruction, tool and trigger size, length band, '
                 'executions and their share of the VA\'s, whether it ran, and who built it. Click a VA for its page. '
                 + ADOPT_NOTE, 'n360.v_playbook', 'Customer', 'the newest read'),
        field=field_config([hidden('bot'), hidden('account'), gauge_cell('Share of the VA', 100, 'series', 'percent'),
                            gauge_cell('Instructions (characters)', None, 'lavender'), count_col('Executions, 31 days'),
                            coloured('In use', {'Idle': ('Idle', 'amber'), 'Ran': ('Ran', 'none')}),
                            width('Playbook', 200), width('VA', 140), width('Length', 105), width('Instructions (characters)', 180),
                            width('Share of the VA', 150),
                            with_links('VA', {'title': 'Open this VA\'s adoption', 'url': f"/d/{ADOPT_VA[0]}?var-account=${{__data.fields.account}}&var-va=${{__data.fields.bot}}&${{__url_time_range}}"})]),
        options={'cellHeight': 'sm'}, may_be_empty='no playbook read'))
    bands = ',\n               '.join(f"countIf(length_band = '{b}') AS `{b}`" for b in BANDS)
    p.append(panel(12, 'Length bands, VA by VA', 'table', f"""
        SELECT va AS VA, count() AS Playbooks, {bands}, toString(bot_id) AS bot, any(account) AS acct
        FROM n360.v_playbook WHERE {ACCT} GROUP BY va, bot_id ORDER BY VA""", 0, 44, 12, 7,
        describe('Production playbooks per VA in each length band (instruction characters). ' + ADOPT_NOTE,
                 'n360.v_playbook', 'Customer', 'the newest read'),
        field=field_config([hidden('bot'), hidden('acct'), width('VA', 120),
                            with_links('VA', {'title': 'Open this VA\'s adoption', 'url': f"/d/{ADOPT_VA[0]}?var-account=${{__data.fields.acct}}&var-va=${{__data.fields.bot}}&${{__url_time_range}}"})],
                           custom={'minWidth': 55}),
        options={'cellHeight': 'sm'}, may_be_empty='no playbook read'))
    p.append(panel(13, 'Who built them', 'piechart', f"""
        SELECT countIf(built_by = 'Customer') AS `The customer`, countIf(built_by = 'Netomi') AS Netomi,
               countIf(built_by IS NULL) AS `Not reported`
        FROM n360.v_playbook WHERE {ACCT}""", 12, 44, 6, 7,
        describe('Production playbooks by who created them (creator email domain; n360.settings adoption_staff_domain). '
                 + ADOPT_NOTE, 'n360.v_playbook', 'Customer', 'the newest read'),
        field=field_config([override('The customer', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['green']})),
                            override('Netomi', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']})),
                            override('Not reported', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['grey']}))], decimals=0),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False}, 'pieType': 'donut',
                 'displayLabels': ['percent'], 'legend': {'displayMode': 'list', 'placement': 'right', 'showLegend': True,
                                                          'values': ['value']},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no playbook read'))
    p.append(panel(14, 'Live but idle', 'table', f"""
        SELECT playbook AS Playbook, va AS VA FROM n360.v_playbook WHERE {ACCT} AND idle ORDER BY VA, Playbook""", 18, 44, 6, 7,
        describe('Production V8 playbooks with no execution in their window. ' + ADOPT_NOTE, 'n360.v_playbook', 'Customer',
                 'the newest read'),
        field=field_config(noValue='None idle'), options={'cellHeight': 'sm'}, may_be_empty='no idle playbook'))
    p.append(panel(15, 'Cortex sessions per day', 'timeseries', f"""
        SELECT toDateTime(day) AS time, sum(authoring_sessions) AS Sessions FROM n360.cortex_day
        WHERE {ACCT} AND $__dateFilter(day) GROUP BY time ORDER BY time""", 0, 51, 12, 8,
        describe('Cortex sessions per day on the customer\'s US VAs (who ran them is not known yet). Read since 5 Oct 2026; '
                 'a day read with none is 0.', 'n360.cortex_day', 'Customer', 'dashboard time range'),
        fmt='timeseries', field=field_config(decimals=0, min=0, custom={'drawStyle': 'bars', 'fillOpacity': 80, 'lineWidth': 0},
                                              **series_colours()),
        options={'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no Cortex day read'))
    p.append(panel(16, 'Engine, week by week', 'state-timeline', f"""
        SELECT toDateTime(c.snapshot_date) AS time, {VA_LABEL} AS VA, c.engine_version AS Engine
        FROM n360.va_config_snapshot AS c INNER JOIN n360.va AS v ON v.bot_id = c.bot_id
        WHERE c.{ACCT} AND $__dateFilter(c.snapshot_date) ORDER BY time""", 12, 51, 12, 8,
        describe('Each VA\'s engine version at each weekly configuration read: green V8 or later, amber older. Hover for '
                 'the version.', 'n360.va_config_snapshot', 'Customer', 'dashboard time range, weekly reads'),
        fmt='timeseries', field=field_config(mappings=[
            {'type': 'regex', 'options': {'pattern': '^[Vv]?(8|9|[1-9][0-9]).*', 'result': {'color': COLOUR['green'], 'index': 0}}},
            {'type': 'regex', 'options': {'pattern': '^[Vv]?[0-7]\\..*', 'result': {'color': COLOUR['amber'], 'index': 1}}}],
            color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, custom={'fillOpacity': 80, 'lineWidth': 0}),
        options={'showValue': 'always', 'mergeValues': True, 'rowHeight': 0.8, 'alignValue': 'center',
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no configuration read'))
    p[-1]['transformations'] = [{'id': 'partitionByValues', 'options': {'fields': ['VA'], 'keepFields': False, 'naming': {'asLabels': True}}}]
    p[-1]['fieldConfig']['defaults']['displayName'] = '${__field.labels.VA}'
    p.append(config_table(17, ACCT, 0, 59, 'Customer'))
    p.append(cortex_weekly_split(18, 0, 67, 12, 9))
    p.append(executions_by_builder(19, 12, 67, 12, 9, per='va'))
    return dashboard(ADOPT_CUSTOMER[0], 'N360 · Customer adoption', p, [var_one_account()],
                     [back_link(('n360-adoption', 'Adoption')), back_link(CUSTOMER, keep_vars=True), back_link()])


def config_table(pid, where, x, y, filter_text, h=8):
    """The v1.1 configuration per VA: engine, playbooks, calibration, testing, recipes, MCP servers, supervisory rules."""
    return panel(pid, 'Configuration, VA by VA', 'table', f"""
        SELECT va AS VA, engine_version AS Engine, playbooks_live AS `Playbooks live`, legacy_intents AS `Legacy intents`,
               idle_playbooks AS Idle,
               concat(toString(ifNull(calibration_runs, 0)), if(calibration_runs = 1, ' run, ', ' runs, '),
                      toString(ifNull(calibration_completed, 0)), ' completed') AS Calibration,
               {date_sql('calibration_last')} AS `Last calibrated`, ifNull(behavior_validation, 'not set') AS `Behavior Validation`,
               test_runs AS `Test runs`, concat(toString(ifNull(recipes_production, 0)), ' of ', toString(ifNull(recipes, 0)), ' live') AS Recipes,
               recipe_actions AS `Write actions`,
               concat(toString(ifNull(mcp_live, 0)), ' live', if(ifNull(mcp_not_live, 0) > 0, concat(', ', toString(mcp_not_live), ' set up, not live'), '')) AS `MCP servers`,
               mcp_names AS `MCP names`,
               concat(toString(ifNull(supervisory_live, 0)), ' live, ', toString(ifNull(supervisory_default, 0)), ' default') AS `Supervisory rules`,
               supervisory_names AS `Rule names`, toString(bot_id) AS bot, account
        FROM n360.va_adoption WHERE {where} ORDER BY VA""", x, y, 24, h,
        describe('Per VA, the v1.1 configuration table: engine and playbooks, legacy intents left, idle playbooks, '
                 'calibration, Behavior Validation and test runs, recipes live and their write actions, MCP servers (live, '
                 'set up but not live, names) and supervisory rules (live, default). ' + ADOPT_NOTE,
                 'n360.va_adoption', filter_text, 'the newest read'),
        field=field_config([hidden('bot'), hidden('account'), amber_cell('Idle'),
                            coloured('Behavior Validation', {'enabled': ('Enabled', 'green'), 'off': ('Off', 'amber'),
                                                             'not set': ('Not set', 'grey_light')}),
                            width('VA', 130), width('MCP names', 150), width('Rule names', 130),
                            with_links('VA', {'title': 'Open this VA\'s adoption', 'url': f"/d/{ADOPT_VA[0]}?var-account=${{__data.fields.account}}&var-va=${{__data.fields.bot}}&${{__url_time_range}}"})]),
        options={'cellHeight': 'sm'})


def adoption_va():
    """VA adoption: one VA's six features as cards, its playbooks, and its configuration over time."""
    p = page_header(1, f"""
        SELECT if(count() = 0, NULL, concat(any(a.va), ' on ', ifNull(any(a.engine_version), 'an unread engine'), ' · ',
                   toString(any(c.n)), ' of 6 features · ', toString(any(a.playbooks_live)), ' playbooks live',
                   if(any(a.idle_playbooks) > 0, concat(', ', toString(any(a.idle_playbooks)), ' idle'), ''), ' · ',
                   {num_sql('any(a.executions)')}, ' executions in 31 days')) AS sentence
        FROM n360.va_adoption AS a
        LEFT JOIN (SELECT bot_id, countIf(state = 'adopted') AS n FROM n360.v_va_feature_adoption GROUP BY bot_id) AS c ON c.bot_id = a.bot_id
        WHERE toString(a.bot_id) IN (${{va:singlequote}})""",
        describe('The VA\'s engine, features adopted of the six, playbooks live and idle, and executions. ' + ADOPT_NOTE,
                 'n360.va_adoption, n360.v_va_feature_adoption', 'VA', 'the 31 days to the data-through day'))
    p.append(single_tile(3, 'Playbook executions, 31 days', f"""
        SELECT any(executions) AS `on this VA` FROM n360.va_adoption WHERE {ONE_VA}""", 0, 3,
        describe('Playbook executions in the 31 days to the data-through day. ' + ADOPT_NOTE, 'n360.va_adoption', 'VA',
                 'the 31 days to the data-through day')))
    p.append(single_tile(4, 'Conversations with a playbook', f"""
        SELECT any(conversations) AS `ran at least one` FROM n360.va_adoption WHERE {ONE_VA}""", 6, 3,
        describe('Conversations that ran at least one playbook, 31 days. ' + ADOPT_NOTE, 'n360.va_adoption', 'VA',
                 'the 31 days to the data-through day')))
    p.append(single_tile(5, 'Handed off from a playbook', f"""
        SELECT if(any(conversations) = 0, NULL, round(100 * any(handoffs) / any(conversations), 1)) AS `of those conversations`
        FROM n360.va_adoption WHERE {ONE_VA}""", 12, 3,
        describe('Handoffs to an agent from within a playbook, as a share of the conversations that ran one. ' + ADOPT_NOTE,
                 'n360.va_adoption', 'VA', 'the 31 days to the data-through day'),
        field=plain(field_config(unit='percent', decimals=1))))
    p.append(single_tile(6, 'Playbooks that ran', f"""
        SELECT if(count() = 0, NULL, concat(toString(any(playbooks_executed)), ' of ', toString(any(playbooks_live)))) AS `live playbooks`
        FROM n360.va_adoption WHERE {ONE_VA}""", 18, 3,
        describe('Production playbooks that ran at least once in 31 days, of those live. ' + ADOPT_NOTE, 'n360.va_adoption',
                 'VA', 'the 31 days to the data-through day'),
        field=plain(field_config(noValue=NOT_READ))))
    maps = [{'type': 'regex', 'options': {'pattern': f'^{k}\\|(.*)$', 'result': {'text': '$1', 'color': COLOUR[c], 'index': i}}}
            for i, (k, (_, c)) in enumerate(ADOPT.items())]
    for i, (col, name, means) in enumerate(FEATURES):
        p.append(panel(7 + i, col, 'stat', f"""
            SELECT concat(state, '|', value) AS v FROM n360.v_va_feature_adoption WHERE {ONE_VA} AND feature = '{name}'""",
            4 * i, 8, 4, 4,
            describe(f'{means} Green adopted, amber partly, light grey not yet, grey not reported. The evidence is in the '
                     'table below. ' + ADOPT_NOTE, 'n360.v_va_feature_adoption', 'VA', 'the newest read'),
            field=field_config(mappings=maps + [empty_grey()], color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}),
            options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value', 'colorMode': 'background',
                     'graphMode': 'none', 'justifyMode': 'center', 'text': {'valueSize': 18}}))
    p.append(panel(13, 'The evidence behind each feature', 'table', f"""
        SELECT feature AS Feature, concat(state, '|', value) AS `This VA`, detail AS Evidence, means AS `What adopted means`
        FROM n360.v_va_feature_adoption WHERE {ONE_VA} ORDER BY ord""", 0, 12, 24, 11,
        describe('For each feature: the reading, the evidence (counts and dates from the source) and the definition. '
                 + ADOPT_NOTE, 'n360.v_va_feature_adoption', 'VA', 'the newest read'),
        field=field_config([*adopt_cells([('This VA', 'Green adopted, amber partly, light grey not yet, grey not reported.')]),
                            width('Feature', 190), width('This VA', 190),
                            override('Evidence', ('custom.cellOptions', {'type': 'auto', 'wrapText': True}))]),
        options={'cellHeight': 'sm'}))
    p.append(panel(14, 'Executions by playbook', 'barchart', f"""
        SELECT playbook AS Playbook, executions AS Executions FROM n360.v_playbook WHERE {ONE_VA} ORDER BY ifNull(executions, 0) DESC""",
        0, 23, 12, 10,
        describe('Every production playbook on this VA by executions over 31 days (an idle one shows 0). ' + ADOPT_NOTE,
                 'n360.v_playbook', 'VA', 'the newest read'),
        field=field_config(decimals=0, min=0, custom={'fillOpacity': 85, 'lineWidth': 0, 'axisGridShow': False}, **series_colours()),
        options={'xField': 'Playbook', 'orientation': 'horizontal', 'showValue': 'always', 'barWidth': 0.75,
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no playbook read'))
    p.append(panel(15, 'Length against traffic', 'xychart', f"""
        SELECT instruction_chars AS `Instructions (characters)`, executions AS `Executions, 31 days`, playbook AS Playbook
        FROM n360.v_playbook WHERE {ONE_VA} AND executions IS NOT NULL ORDER BY instruction_chars""", 12, 23, 12, 10,
        describe('Each playbook on this VA as a point: instruction length across, executions up. Hover a point for the '
                 'playbook. ' + ADOPT_NOTE, 'n360.v_playbook', 'VA', 'the newest read'),
        field=field_config(decimals=0, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['lavender']},
                           custom={'show': 'points', 'pointSize': {'fixed': 10}}),
        options={'mapping': 'auto', 'series': [{}], 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no playbook read'))
    p.append(panel(16, 'Every playbook on this VA', 'table', f"""
        SELECT playbook AS Playbook, length_band AS Length, instruction_chars AS `Instructions (characters)`, tools AS Tools,
               trigger_chars AS `Trigger (characters)`, if(short, 'yes', 'no') AS `Short (<200)`,
               executions AS `Executions, 31 days`, share_of_va_pct AS `Share of the VA`,
               multiIf(idle IS NULL, NULL, idle, 'Idle', 'Ran') AS `In use`, ifNull(built_by, '') AS `Built by`, task_id AS Task
        FROM n360.v_playbook WHERE {ONE_VA} ORDER BY rank_on_va""", 0, 33, 24, 9,
        describe('Every production playbook on this VA: sizes, length band, executions and their share of the VA, whether it '
                 'ran, who built it, and its task id (to find it in the product). ' + ADOPT_NOTE, 'n360.v_playbook', 'VA',
                 'the newest read'),
        field=field_config([gauge_cell('Share of the VA', 100, 'series', 'percent'), gauge_cell('Instructions (characters)', None, 'lavender'),
                            count_col('Executions, 31 days'), coloured('In use', {'Idle': ('Idle', 'amber'), 'Ran': ('Ran', 'none')}),
                            width('Playbook', 220), width('Length', 105), width('Instructions (characters)', 190),
                            width('Share of the VA', 160)]),
        options={'cellHeight': 'sm'}, may_be_empty='no playbook read'))
    p.append(panel(17, 'Playbooks and legacy intents, week by week', 'timeseries', f"""
        SELECT toDateTime(snapshot_date) AS time, any(v8_playbooks) AS `V8 playbooks`, any(legacy_intents) AS `Legacy intents`
        FROM n360.va_config_snapshot WHERE {ONE_VA} AND $__dateFilter(snapshot_date) GROUP BY time ORDER BY time""", 0, 42, 12, 8,
        describe('At each weekly configuration read: V8 playbooks (solid) and legacy intents still configured (dashed). '
                 'The migration is done when the dashed line reaches 0.', 'n360.va_config_snapshot', 'VA',
                 'dashboard time range, weekly reads'),
        fmt='timeseries', field=field_config(
            [override('Legacy intents', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['reference']}),
                      ('custom.lineStyle', {'fill': 'dash', 'dash': [6, 4]}))],
            decimals=0, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['series']},
            custom={'drawStyle': 'line', 'lineWidth': 2, 'showPoints': 'always', 'pointSize': 6}),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}, may_be_empty='no configuration read'))
    p.append(panel(18, 'Engine, week by week', 'state-timeline', f"""
        SELECT toDateTime(snapshot_date) AS time, engine_version AS Engine
        FROM n360.va_config_snapshot WHERE {ONE_VA} AND $__dateFilter(snapshot_date) ORDER BY time""", 12, 42, 12, 8,
        describe('The engine version at each weekly read: green V8 or later, amber older. Hover for the version.',
                 'n360.va_config_snapshot', 'VA', 'dashboard time range, weekly reads'),
        fmt='timeseries', field=field_config(mappings=[
            {'type': 'regex', 'options': {'pattern': '^[Vv]?(8|9|[1-9][0-9]).*', 'result': {'color': COLOUR['green'], 'index': 0}}},
            {'type': 'regex', 'options': {'pattern': '^[Vv]?[0-7]\\..*', 'result': {'color': COLOUR['amber'], 'index': 1}}}],
            color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, custom={'fillOpacity': 80, 'lineWidth': 0}),
        options={'showValue': 'always', 'mergeValues': True, 'rowHeight': 0.8, 'alignValue': 'center',
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'single', 'sort': 'none'}}, may_be_empty='no configuration read'))
    p.append(config_table(19, ONE_VA, 0, 50, 'VA', h=4))
    return dashboard(ADOPT_VA[0], 'N360 · VA adoption', p, [var_one_account(), var_va()],
                     [back_link(ADOPT_CUSTOMER, keep_vars=True), back_link(('n360-va', 'VA'), keep_vars=True),
                      back_link(('n360-adoption', 'Adoption'))])


# ───────────────────────── Netomi and the customer: who does the work (27 Nov 2026) ─────────────────────────

WHO = {'Netomi': 'series', 'Customer': 'green'}       # Netomi's work in the neutral series colour, the customer's in green
WHO_NOTE = ('Netomi = a Cortex user on Netomi\'s domain, or a playbook created from it (n360.settings adoption_staff_domain); '
            'customer users are counted, never named.')
NETOMIAN = "ifNull(name, '') IN (${netomian:singlequote})"


def who_colours(**names):
    return [override(n, ('color', {'mode': 'fixed', 'fixedColor': COLOUR[c]})) for n, c in names.items()]


def cortex_split_by_customer(pid, x, y, w, h, where=ACCT):
    return panel(pid, 'Cortex sessions: Netomi and the customer', 'barchart', f"""
        SELECT a.display_name AS Customer, sumIf(c.sessions, c.who = 'Netomi') AS Netomi,
               sumIf(c.sessions, c.who = 'Customer') AS `The customer`
        FROM n360.v_cortex_user AS c INNER JOIN n360.account AS a ON a.account = c.account
        WHERE c.{where} AND $__dateFilter(c.day) GROUP BY Customer ORDER BY sum(c.sessions) DESC""", x, y, w, h,
        describe('Cortex sessions per customer, split by who ran them: Netomi (blue-grey) or the customer\'s own users '
                 '(green). Hover for the counts. A customer whose bar is all Netomi depends on Netomi to change its '
                 'playbooks. ' + WHO_NOTE, 'n360.v_cortex_user', 'Customer', 'dashboard time range'),
        field=field_config(who_colours(Netomi='series', **{'The customer': 'green'}), decimals=0, min=0,
                           custom={'fillOpacity': 90, 'lineWidth': 0, 'axisGridShow': False}),
        options={'xField': 'Customer', 'orientation': 'horizontal', 'stacking': 'normal', 'showValue': 'never', 'barWidth': 0.8,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}, may_be_empty='no per-person Cortex data')


def executions_by_builder(pid, x, y, w, h, where=ACCT, per='customer'):
    key, label = (("a.display_name", 'Customer') if per == 'customer' else ("pb.va", 'VA'))
    return panel(pid, 'Executions: Netomi-built and customer-built playbooks', 'barchart', f"""
        SELECT {key} AS {label}, sumIf(pb.executions, pb.built_by = 'Netomi') AS `Built by Netomi`,
               sumIf(pb.executions, pb.built_by = 'Customer') AS `Built by the customer`,
               sumIf(pb.executions, pb.built_by IS NULL) AS `Builder not reported`
        FROM n360.v_playbook AS pb INNER JOIN n360.account AS a ON a.account = pb.account
        WHERE pb.{where} GROUP BY {label} ORDER BY sum(pb.executions) DESC""", x, y, w, h,
        describe('Playbook executions in 31 days, split by who created the playbook (creator domain): Netomi (blue-grey) or '
                 'the customer (green), as a share. Hover for the counts. ' + WHO_NOTE + ' ' + ADOPT_NOTE,
                 'n360.v_playbook', label, 'the newest read'),
        field=field_config(who_colours(**{'Built by Netomi': 'series', 'Built by the customer': 'green', 'Builder not reported': 'grey'}),
                           decimals=0, min=0, max=1, custom={'fillOpacity': 90, 'lineWidth': 0, 'axisGridShow': False}),
        options={'xField': label, 'orientation': 'horizontal', 'stacking': 'percent', 'showValue': 'never', 'barWidth': 0.8,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}, may_be_empty='no playbook read')


def cortex_weekly_split(pid, x, y, w, h, where=ACCT, filter_text='Customer'):
    return panel(pid, 'Cortex sessions per week: Netomi and the customer', 'timeseries', f"""
        SELECT toDateTime(toMonday(day)) AS time, sumIf(sessions, who = 'Netomi') AS Netomi,
               sumIf(sessions, who = 'Customer') AS `The customer`
        FROM n360.v_cortex_user WHERE {where} AND $__dateFilter(day) GROUP BY time ORDER BY time""", x, y, w, h,
        describe('Cortex sessions per week by Netomi users and by the customer\'s own users, side by side. The customer\'s '
                 'line rising while Netomi\'s falls is the hand-over working. Hover for the week. ' + WHO_NOTE,
                 'n360.v_cortex_user', filter_text, 'dashboard time range, weeks from Monday'),
        fmt='timeseries', field=field_config(who_colours(Netomi='series', **{'The customer': 'green'}), decimals=0, min=0,
                                             custom={'drawStyle': 'bars', 'fillOpacity': 85, 'lineWidth': 0, 'barWidthFactor': 0.8}),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}, may_be_empty='no per-person Cortex data')


def var_netomian():
    return query_var('netomian', 'Netomian', "SELECT DISTINCT name FROM n360.v_cortex_user WHERE who = 'Netomi' AND name IS NOT NULL ORDER BY name")


def cortex_netomi():
    """Cortex by Netomians: who on the Netomi team builds in Cortex, for which customers, and how that is moving.
    A running leaderboard over the dashboard's range (3 months by default)."""
    w = f"who = 'Netomi' AND {NETOMIAN} AND {ACCT} AND $__dateFilter(day)"
    p = page_header(1, f"""
        WITH (SELECT argMax(name, s) FROM (SELECT name, sum(sessions) AS s FROM n360.v_cortex_user WHERE {w} GROUP BY name)) AS leader,
             (SELECT max(s) FROM (SELECT name, sum(sessions) AS s FROM n360.v_cortex_user WHERE {w} GROUP BY name)) AS lead_n,
             (SELECT sum(sessions) FROM n360.v_cortex_user WHERE {ACCT} AND $__dateFilter(day)) AS everyone
        SELECT if(count() = 0, NULL, concat(toString(uniqExact(name)), ' Netomians ran ', {num_sql('sum(sessions)')},
                   ' Cortex sessions for ', {plural('uniqExact(account)', 'customer', 'customers')}, ' · leader: ', leader, ' (',
                   toString(lead_n), ') · Netomi did ', toString(round(100 * sum(sessions) / nullIf(everyone, 0))),
                   '% of all Cortex sessions')) AS sentence
        FROM n360.v_cortex_user WHERE {w}""",
        describe('Over the dashboard\'s time range (3 months by default): how many Netomians used Cortex, their sessions, '
                 'the leader, and Netomi\'s share of every Cortex session (customers\' own users are the rest). '
                 + WHO_NOTE, 'n360.v_cortex_user', 'Netomian, Customer', 'dashboard time range'))
    p.append(single_tile(3, 'Netomians in Cortex', f"""
        SELECT if(count() = 0, NULL, uniqExact(name)) AS `with at least one session` FROM n360.v_cortex_user WHERE {w}""", 0, 3,
        describe('Netomi users with at least one Cortex session in the range. ' + WHO_NOTE, 'n360.v_cortex_user',
                 'Netomian, Customer', 'dashboard time range')))
    p.append(single_tile(4, 'Sessions by Netomians', f"""
        SELECT if(count() = 0, NULL, sum(sessions)) AS `in the range` FROM n360.v_cortex_user WHERE {w}""", 6, 3,
        describe('Cortex sessions by Netomi users in the range. ' + WHO_NOTE, 'n360.v_cortex_user', 'Netomian, Customer',
                 'dashboard time range')))
    p.append(single_tile(5, 'Netomi\'s share of Cortex', f"""
        SELECT if(sum(sessions) = 0, NULL, round(100 * sumIf(sessions, who = 'Netomi') / sum(sessions), 1)) AS `of every session`
        FROM n360.v_cortex_user WHERE {ACCT} AND $__dateFilter(day)""", 12, 3,
        describe('Netomi users\' sessions as a share of every Cortex session (Netomi and customers) in the range: lower '
                 'means customers build for themselves. ' + WHO_NOTE, 'n360.v_cortex_user', 'Customer',
                 'dashboard time range'), field=plain(field_config(unit='percent', decimals=1))))
    p.append(single_tile(6, 'Production edits by Netomians', f"""
        SELECT if(count() = 0, NULL, sum(production_edits)) AS `saved to live playbooks` FROM n360.v_cortex_user WHERE {w}""", 18, 3,
        describe('Playbook versions Netomi users saved to a production playbook in the range: changes customers see. '
                 + WHO_NOTE, 'n360.v_cortex_user', 'Netomian, Customer', 'dashboard time range')))
    p.append(panel(7, 'Leaderboard', 'table', f"""
        WITH (SELECT max(day) FROM n360.v_cortex_user) AS newest
        SELECT row_number() OVER (ORDER BY sum(sessions) DESC, name) AS `#`, name AS Netomian, sum(sessions) AS Sessions,
               sum(production_edits) AS `Production edits`, uniqExact(account) AS Customers, uniqExact(bot_id) AS VAs,
               multiIf(sumIf(sessions, day > newest - 30) > sumIf(sessions, day <= newest - 30 AND day > newest - 60),
                         concat('▲ ', toString(sumIf(sessions, day > newest - 30))),
                       sumIf(sessions, day > newest - 30) < sumIf(sessions, day <= newest - 30 AND day > newest - 60),
                         concat('▼ ', toString(sumIf(sessions, day > newest - 30))),
                       concat('● ', toString(sumIf(sessions, day > newest - 30)))) AS `Last 30 days`,
               {date_sql('max(day)')} AS `Last active`
        FROM n360.v_cortex_user WHERE {w} GROUP BY name ORDER BY `#`""", 0, 8, 12, 12,
        describe('Every Netomian ranked by Cortex sessions in the range (3 months by default), with production edits, '
                 'customers and VAs worked on. Last 30 days: their sessions in the newest 30 days, ▲ more than the 30 before, '
                 '▼ fewer. Click a name to see only them. ' + WHO_NOTE, 'n360.v_cortex_user', 'Netomian, Customer',
                 'dashboard time range; last 30 days to the newest day read'),
        field=field_config([gauge_cell('Sessions', None, 'lavender'), width('#', 36), width('Netomian', 115), width('Sessions', 140),
                            override('Last 30 days', ('mappings', [
                                {'type': 'regex', 'options': {'pattern': '^▲.*', 'result': {'color': COLOUR['green'], 'index': 0}}},
                                {'type': 'regex', 'options': {'pattern': '^▼.*', 'result': {'color': COLOUR['down'], 'index': 1}}}]),
                                     ('custom.cellOptions', {'type': 'color-text'})),
                            width('Production edits', 80), width('Customers', 75), width('VAs', 45), width('Last 30 days', 90),
                            with_links('Netomian', {'title': 'Only this Netomian', 'url': '/d/n360-cortex?var-netomian=${__value.raw}&${__url_time_range}'})],
                           custom={'minWidth': 50}),
        options={'cellHeight': 'md'}, may_be_empty='no per-person Cortex data'))
    p.append(panel(8, 'Running leaderboard', 'timeseries', f"""
        SELECT toDateTime(day) AS time, name AS Netomian,
               sum(sum(sessions)) OVER (PARTITION BY name ORDER BY day ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS Sessions
        FROM n360.v_cortex_user WHERE {w} GROUP BY day, name ORDER BY time""", 12, 8, 12, 12,
        describe('Each Netomian\'s Cortex sessions added up day by day from the start of the range: the race. Where two '
                 'lines cross, the lead changed hands. Hover for every Netomian that day, highest first. ' + WHO_NOTE,
                 'n360.v_cortex_user', 'Netomian, Customer', 'dashboard time range'),
        fmt='timeseries', field=field_config(decimals=0, min=0, displayName='${__field.labels.Netomian}',
                                             color={'mode': 'palette-classic-by-name'},
                                             custom={'drawStyle': 'line', 'lineWidth': 2, 'lineInterpolation': 'stepAfter',
                                                     'fillOpacity': 0, 'showPoints': 'never', 'spanNulls': True}),
        options={'legend': {'displayMode': 'table', 'placement': 'right', 'showLegend': True, 'calcs': ['lastNotNull'],
                            'sortBy': 'Last *', 'sortDesc': True},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}, may_be_empty='no per-person Cortex data'))
    p[-1]['transformations'] = [{'id': 'prepareTimeSeries', 'options': {'format': 'multi'}}]
    p.append(panel(9, 'Sessions per week, by Netomian', 'timeseries', f"""
        SELECT toDateTime(toMonday(day)) AS time, name AS Netomian, sum(sessions) AS Sessions
        FROM n360.v_cortex_user WHERE {w} GROUP BY time, name ORDER BY time""", 0, 20, 12, 9,
        describe('Cortex sessions per week, stacked by Netomian: who carried each week. Hover for the week. ' + WHO_NOTE,
                 'n360.v_cortex_user', 'Netomian, Customer', 'dashboard time range, weeks from Monday'),
        fmt='timeseries', field=field_config(decimals=0, min=0, displayName='${__field.labels.Netomian}',
                                             color={'mode': 'palette-classic-by-name'},
                                             custom={'drawStyle': 'bars', 'fillOpacity': 85, 'lineWidth': 0, 'barWidthFactor': 0.8,
                                                     'stacking': {'mode': 'normal', 'group': 'A'}}),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}, may_be_empty='no per-person Cortex data'))
    p[-1]['transformations'] = [{'id': 'prepareTimeSeries', 'options': {'format': 'multi'}}]
    p.append(cortex_weekly_split(10, 12, 20, 12, 9))
    p.append(panel(11, 'Who works where', 'table', f"""
        SELECT name AS Netomian, a.display_name AS Customer, sum(c.sessions) AS Sessions, sum(c.production_edits) AS `Production edits`,
               {date_sql('max(c.day)')} AS `Last active`, c.account AS account
        FROM n360.v_cortex_user AS c INNER JOIN n360.account AS a ON a.account = c.account
        WHERE c.who = 'Netomi' AND ifNull(c.name, '') IN (${{netomian:singlequote}}) AND c.{ACCT} AND $__dateFilter(c.day)
        GROUP BY name, a.display_name, c.account ORDER BY Netomian, Sessions DESC""", 0, 29, 12, 10,
        describe('Each Netomian\'s Cortex work per customer in the range. Click a customer for its adoption page. ' + WHO_NOTE,
                 'n360.v_cortex_user', 'Netomian, Customer', 'dashboard time range'),
        field=field_config([hidden('account'), gauge_cell('Sessions', None, 'lavender'), width('Netomian', 120), width('Sessions', 150),
                            width('Production edits', 110),
                            with_links('Customer', drill(ADOPT_CUSTOMER[0], 'account', 'account', 'Open this customer\'s adoption')),
                            with_links('Netomian', {'title': 'Only this Netomian', 'url': '/d/n360-cortex?var-netomian=${__value.raw}&${__url_time_range}'})],
                           custom={'minWidth': 60}),
        options={'cellHeight': 'sm'}, may_be_empty='no per-person Cortex data'))
    p.append(cortex_split_by_customer(12, 12, 29, 12, 10))
    return dashboard('n360-cortex', 'N360 · Cortex by Netomians', p, [var_netomian(), var_account()],
                     [back_link(('n360-adoption', 'Adoption')), back_link()])


def requests():
    """Requests (v2): what are customers asking us to build or fix? Product, CS (Phase 2 data)."""
    t90 = f"opened > {DATA_THROUGH_SQL} - 90"
    p = page_header(1, f"""
        WITH (SELECT argMax(concat(ask, ' (', toString(customers), ' customers)'), tuple(customers, tickets)) FROM n360.v_top_asks) AS top
        SELECT if(count() = 0, NULL, concat({plural('countIf(' + t90 + ')', 'ticket', 'tickets')}, ' in 90 days · ',
                      toString(countIf(status = 'open')), ' open · ',
                      {plural('uniqExactIf(account, ' + t90 + ')', 'customer', 'customers')}, ' raising them',
                      if(top = '', '', concat(' · most asked: ', top)))) AS sentence
        FROM n360.ps_ticket WHERE {ACCT}""",
        describe('Support tickets opened in the 90 days to the data-through day, those still open, how many customers '
                 'raised them, and the ask wanted by the most customers. ' + PHASE2_NOTE,
                 'n360.ps_ticket, n360.v_top_asks', 'Customer', 'the 90 days to the data-through day'))
    p.append(single_tile(3, 'Tickets', f"""
        SELECT if((SELECT count() FROM n360.ps_ticket) = 0, NULL, countIf({t90})) AS `opened in 90 days`
        FROM n360.ps_ticket WHERE {ACCT}""", 0, 3, describe('Tickets opened in the 90 days to the data-through day. '
                                                             + PHASE2_NOTE, 'n360.ps_ticket', 'Customer', 'the last 90 days')))
    p.append(single_tile(4, 'Open', f"""
        SELECT if((SELECT count() FROM n360.ps_ticket) = 0, NULL, countIf(status = 'open')) AS `priority not exported yet`
        FROM n360.ps_ticket WHERE {ACCT}""", 6, 3, describe('Tickets still open, whenever opened. The v2 tile marks P1; '
                                                             'priority is not in the export yet. ' + PHASE2_NOTE,
                                                             'n360.ps_ticket', 'Customer', 'all tickets')))
    p.append(single_tile(5, 'Customers raising them', f"""
        SELECT if((SELECT count() FROM n360.ps_ticket) = 0, NULL, uniqExactIf(account, {t90})) AS `in 90 days`
        FROM n360.ps_ticket WHERE {ACCT}""", 12, 3, describe('Customers with a ticket opened in the last 90 days. '
                                                              + PHASE2_NOTE, 'n360.ps_ticket', 'Customer', 'the last 90 days')))
    p.append(single_tile(6, 'Asks from several customers', """
        SELECT if((SELECT count() FROM n360.ps_ticket) = 0, NULL, count()) AS `2 or more customers (proposed)`
        FROM n360.v_top_asks""", 18, 3,
        describe('Asks tagged on tickets from 2 or more customers (n360.settings asks_several_customers; the v2 spec '
                 'says 3 or more: open decision). ' + PHASE2_NOTE, 'n360.v_top_asks', 'none (every customer)', 'all tickets')))
    p.append(panel(7, 'Asks, by number of customers', 'barchart', """
        SELECT concat(ask, if(open_n > 0, concat('  (', toString(open_n), ' open)'), '')) AS Ask, customers AS Customers
        FROM (SELECT t.ask AS ask, t.customers AS customers, t.tickets AS tickets,
                     (SELECT countIf(status = 'open') FROM n360.ps_ticket AS k WHERE k.ask = t.ask) AS open_n
              FROM n360.v_top_asks AS t)
        ORDER BY Customers DESC, tickets DESC""", 0, 8, 12, 9,
        describe('Each ask wanted by 2 or more customers, ranked by how many customers want it; open tickets are noted '
                 'after the ask. ' + PHASE2_NOTE, 'n360.v_top_asks, n360.ps_ticket', 'none (every customer)', 'all tickets'),
        field=field_config(decimals=0, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['series']}),
        options={'xField': 'Ask', 'orientation': 'horizontal', 'showValue': 'always', 'barWidth': 0.7,
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'}},
        may_be_empty='no shared ask'))
    p[-1]['targets'][0]['rawSql'] = """
        SELECT concat(t.ask, if(o.n > 0, concat('  (', toString(o.n), ' open)'), '')) AS Ask, t.customers AS Customers
        FROM n360.v_top_asks AS t
        LEFT JOIN (SELECT ask, countIf(status = 'open') AS n FROM n360.ps_ticket WHERE ask IS NOT NULL GROUP BY ask) AS o
               ON o.ask = t.ask
        ORDER BY Customers DESC, t.tickets DESC""".strip()
    p.append(panel(8, 'Tickets by product area', 'barchart', f"""
        SELECT ifNull(product_area, 'Not tagged yet') AS Area, count() AS Tickets
        FROM n360.ps_ticket WHERE {ACCT} AND $__dateFilter(opened) GROUP BY Area ORDER BY Tickets DESC""", 12, 8, 12, 9,
        describe('Tickets opened in the time range by product area. "Not tagged yet" is a real bucket, never a guess. '
                 + PHASE2_NOTE, 'n360.ps_ticket', 'Customer', 'dashboard time range (opened)'),
        field=field_config(decimals=0, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['series']}),
        options={'xField': 'Area', 'orientation': 'horizontal', 'showValue': 'always', 'barWidth': 0.7,
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'}},
        may_be_empty='no tickets in the range'))
    p.append(panel(9, 'Tickets opened per week, last 26 weeks', 'timeseries', f"""
        SELECT toDateTime(toMonday(opened)) AS time, count() AS Opened
        FROM n360.ps_ticket WHERE {ACCT} AND opened > {DATA_THROUGH_SQL} - 182 GROUP BY time ORDER BY time""", 0, 17, 24, 7,
        describe('Tickets opened per week. The v2 page also shows tickets closed per week; the export has no closed date '
                 'yet. ' + PHASE2_NOTE, 'n360.ps_ticket', 'Customer', 'the 26 weeks to the data-through day'),
        fmt='timeseries', field=field_config(decimals=0, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['series']},
                                             custom={'drawStyle': 'bars', 'fillOpacity': 85, 'lineWidth': 1, 'barAlignment': 1}),
        options={'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'}},
        may_be_empty='no tickets in 26 weeks'))
    p[-1]['timeFrom'] = '200d'
    p.append(panel(10, 'Every ticket', 'table', f"""
        SELECT t.ticket_id AS Ticket, ac.display_name AS Customer, {date_sql('t.opened')} AS Opened, t.title AS Title,
               t.product_area AS Area, t.ask AS Ask, t.status AS Status, t.account AS account
        FROM n360.ps_ticket AS t INNER JOIN n360.account AS ac ON ac.account = t.account
        WHERE t.{ACCT} ORDER BY t.status = 'open' DESC, t.opened DESC""", 0, 24, 24, 12,
        describe('Every ticket, open first then newest. Area and ask are tagged by hand ("Not tagged yet" until then). '
                 'Filter a column from its header; export with the panel menu (Inspect > Data). ' + PHASE2_NOTE,
                 'n360.ps_ticket', 'Customer', 'all tickets'),
        field=field_config([hidden('account'),
                            *[override(c, ('mappings', [{'type': 'special', 'options': {'match': 'null', 'result':
                                {'text': 'Not tagged yet', 'color': COLOUR['grey'], 'index': 0}}}]),
                                       ('custom.cellOptions', {'type': 'color-text'}),
                                       ('color', {'mode': 'fixed', 'fixedColor': COLOUR['none']})) for c in ('Area', 'Ask')],
                            width('Ticket', 90), width('Customer', 160), width('Opened', 110), width('Status', 80),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))],
                           custom={'filterable': True}, noValue='No tickets'),
        options={'cellHeight': 'sm'}, may_be_empty='no tickets'))
    return dashboard(UID['requests'], 'N360 · Requests', p, [var_account()], [back_link()])


def plural(n, one, many):
    """SQL: "1 line" / "3 lines"."""
    return f"concat(toString({n}), if({n} = 1, ' {one}', ' {many}'))"


GREY_STATUSES = [k for k, (_, c) in STATUS.items() if c == 'grey']
NOT_READABLE = 'No reading yet'           # the grey statuses together, in charts of parts of a whole


def status_columns(count_expr='countIf({cond})', group_grey=True):
    """SQL select list: one column per status shown (urgency order), the grey ones together when group_grey."""
    cols, seen = [], False
    for k, (t, c) in STATUS.items():
        if group_grey and c == 'grey':
            if seen:
                continue
            seen = True
            cond = f"{STATUS_SHOWN} IN ({', '.join(repr(g) for g in GREY_STATUSES)})"
            cols.append(f"{count_expr.format(cond=cond)} AS `{NOT_READABLE}`")
        else:
            cols.append(f"{count_expr.format(cond=f'{STATUS_SHOWN} = {k!r}')} AS `{t}`")
    return ',\n               '.join(cols)


def status_overrides(group_grey=True, link=None):
    """Colour (and optionally a Contracts link) for every status column from status_columns()."""
    out = []
    for k, (t, c) in STATUS.items():
        if group_grey and c == 'grey':
            continue
        props = [('color', {'mode': 'fixed', 'fixedColor': COLOUR[c]})]
        if link:
            props.append(('links', [{'title': f'Contracts: {t}', 'url': f"/d/{UID['contracts']}?var-status={k}"}]))
        out.append(override(t, *props))
    if group_grey:
        props = [('color', {'mode': 'fixed', 'fixedColor': COLOUR['grey']})]
        if link:
            props.append(('links', [{'title': f'Contracts: {NOT_READABLE}', 'url': f"/d/{UID['contracts']}?"
                                     + '&'.join(f'var-status={g}' for g in GREY_STATUSES)}]))
        out.append(override(NOT_READABLE, *props))
    return out


def conversations_chart(pid, x, y, w, h, acct='1', title='All conversations', va_filter=None):
    """Conversations per day, week or month (the Period picker). Periods not over yet, or holding days that may still
    settle, are drawn faded as "Still counting" (v2 grammar: partial periods faded and labelled)."""
    va = f" AND toString(d.bot_id) IN (${{{va_filter}:singlequote}})" if va_filter else ''
    return panel(pid, title, 'timeseries', f"""
        WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS dt,
             (SELECT anomaly_provisional_days FROM n360.settings) AS settling
        SELECT toDateTime(b) AS time,
               if(partial, NULL, if(countIf(d.conversations IS NULL) > 0, NULL, sum(d.conversations))) AS Conversations,
               if(partial, if(countIf(d.conversations IS NULL) > 0, NULL, sum(d.conversations)), NULL) AS `Still counting`
        FROM (SELECT *, multiIf(${{period:singlequote}} = 'day', day, ${{period:singlequote}} = 'month', toStartOfMonth(day), toMonday(day)) AS b,
                     multiIf(${{period:singlequote}} = 'day', day, ${{period:singlequote}} = 'month', toLastDayOfMonth(day), toMonday(day) + 6) AS b_end
              FROM n360.va_day) AS d
        WHERE {acct}{va} AND $__dateFilter(d.day)
        GROUP BY b, b_end > dt OR b_end > dt - settling AS partial ORDER BY time""", x, y, w, h,
        describe('Conversations started per day, week (Monday to Sunday) or month, every VA together. The newest period '
                 'is drawn faded and labelled "Still counting" while it is not over or holds days that may still '
                 'settle (the newest 3). A period with any day not read is a gap, never a lower bar. Conversations '
                 'are the platform count, not a contract measure.',
                 'n360.va_day (the platform daily conversation aggregate and the conversation records reader)',
                 'Customer' if acct != '1' else 'none (every customer)', 'dashboard time range, by the Period picker'),
        fmt='timeseries',
        field=field_config([override('Conversations', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']})),
                            override('Still counting', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series_light']}))],
                           unit='short', decimals=0, min=0,
                           custom={'drawStyle': 'bars', 'fillOpacity': 85, 'lineWidth': 1, 'spanNulls': False,
                                   'stacking': {'mode': 'normal', 'group': 'A'}, 'barAlignment': 1}),
        options={'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}})


def var_period():
    return custom_var('period', 'Period', [('Week', 'week'), ('Day', 'day'), ('Month', 'month')])


def var_vas():
    """Several VAs of the chosen customer (All by default): the conversations chart's filter."""
    return query_var('va', 'VA', f"""SELECT toString(v.bot_id), {VA_LABEL}
        FROM n360.va AS v WHERE v.account IN (${{account:singlequote}}) ORDER BY 2""")


def status_regex_colours():
    """Stat value mappings: a text starting with a status word takes that status's colour."""
    return [{'type': 'regex', 'options': {'pattern': f'^{t}.*', 'result': {'color': COLOUR[c], 'index': i}}}
            for i, (k, (t, c)) in enumerate(STATUS.items())]


def text_tile(pid, title, sql, x, y, w, h, desc, mappings=None, no_value=NOT_READ, colour_mode='none', may_be_empty=None):
    return panel(pid, title, 'stat', sql, x, y, w, h, desc,
                 field=plain(field_config(mappings=mappings or [], noValue=no_value)),
                 options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/'}, 'textMode': 'value',
                          'colorMode': colour_mode, 'graphMode': 'none', 'justifyMode': 'auto', 'text': {'valueSize': 24}},
                 may_be_empty=may_be_empty)


def health():
    """Customer health (v2, new): how is each customer doing, and what changed in 30 days? Customer success, by CSM."""
    in_csm = "account IN (SELECT account FROM n360.account WHERE csm_rocketlane IN (${csm:singlequote}))"
    p = page_header(1, f"""
        WITH (SELECT argMax(concat(va, ' conversations ', if(convs_change_pct < 0, 'down ', 'up '),
                                   toString(abs(toInt32(round(convs_change_pct)))), '%'), abs(convs_change_pct))
              FROM n360.v_va_change WHERE {in_csm} AND convs_change_pct IS NOT NULL AND convs_prev_30 >= 300) AS biggest
        SELECT concat({plural('(SELECT count() FROM n360.account WHERE ' + in_csm + ')', 'customer', 'customers')}, ' · ',
                      toString((SELECT countIf(status = 'below pace') FROM n360.v_contract_status WHERE {in_csm})), ' lines below pace · ',
                      toString((SELECT countIf(convs_down) FROM n360.v_va_change WHERE {in_csm})), ' VAs with conversations down 10%+',
                      if(biggest = '', '', concat(' · biggest change: ', biggest))) AS sentence""",
        describe('For the chosen CSMs: customers, lines below pace, VAs whose conversations fell 10% or more (last 30 '
                 'days against the 30 before; proposed, n360.settings), and the biggest change in conversations of a VA '
                 'with at least 300 conversations in the previous 30 days.', 'n360.v_contract_status, n360.v_va_change',
                 'CSM', 'the 60 days to the data-through day'))
    p.append(tile(3, 'Lines below pace', status_night_count("status = 'below pace'", in_csm), 0, 3,
                  describe('Contract lines and pools more than 10 points behind their term (renewal risks included).',
                           'n360.v_status_night', 'CSM', 'the last 30 stored nights'),
                  links=[{'title': 'Contracts: below pace', 'url': f"/d/{UID['contracts']}?var-status=below pace&var-status=renewal risk"}],
                  metric='lines below pace'))
    p.append(single_tile(4, 'VAs with conversations down 10%+', f"""
        SELECT countIf(convs_down) AS `last 30 days against the 30 before` FROM n360.v_va_change WHERE {in_csm}""", 6, 3,
        describe('VAs whose conversations in the last 30 days are 10% or more below the 30 days before (proposed, '
                 'n360.settings health_convs_down_pct). A window with an unread day is not compared.',
                 'n360.v_va_change', 'CSM', 'the 60 days to the data-through day')))
    p.append(single_tile(5, 'VAs with containment down 3+ points', f"""
        SELECT countIf(containment_down) AS `last 30 days against the 30 before` FROM n360.v_va_change WHERE {in_csm}""", 12, 3,
        describe('VAs whose containment (1 - handoffs / engaged, per VA) fell 3 points or more (proposed, n360.settings '
                 'health_containment_down_pts). VAs without handoff data are not compared.', 'n360.v_va_change', 'CSM',
                 'the 60 days to the data-through day')))
    p.append(single_tile(6, 'Open tickets', f"""
        SELECT if((SELECT count() FROM n360.ps_ticket) = 0, NULL, countIf(status = 'open')) AS `priority not exported yet`
        FROM n360.ps_ticket WHERE {in_csm}""", 18, 3,
        describe('Support tickets still open for these customers. The v2 tile counts P1 tickets; priority is not in the '
                 'export yet, so every open ticket is counted. ' + PHASE2_NOTE, 'n360.ps_ticket', 'CSM', 'all tickets'),
        links=[panel_link('requests', 'Requests')]))
    p.append(panel(7, 'Customers', 'table', f"""
        WITH st AS (SELECT account, argMin({STATUS_SHOWN}, tuple(status_rank, -ifNull(pct_billed, 0))) AS worst,
                           arrayStringConcat(arrayFilter(x -> x != '', [
                               if(countIf(status = 'over billed') > 0, concat(toString(countIf(status = 'over billed')), ' over'), ''),
                               if(countIf(status = 'will exceed') > 0, concat(toString(countIf(status = 'will exceed')), ' will exceed'), ''),
                               if(countIf(renewal_risk) > 0, concat(toString(countIf(renewal_risk)), ' at risk'), ''),
                               if(countIf(status = 'below pace' AND NOT renewal_risk) > 0,
                                  concat(toString(countIf(status = 'below pace' AND NOT renewal_risk)), ' below'), ''),
                               if(countIf(status IN ('on pace', 'ahead')) > 0, concat(toString(countIf(status IN ('on pace', 'ahead'))), ' on pace'), ''),
                               if(countIf(status IN ({', '.join(repr(g) for g in GREY_STATUSES)}, 'too early')) > 0,
                                  concat(toString(countIf(status IN ({', '.join(repr(g) for g in GREY_STATUSES)}, 'too early'))), ' no reading'), '')]),
                               ' · ') AS lines
                    FROM n360.v_contract_status GROUP BY account),
             vc AS (SELECT account, if(countIf(convs_30 IS NULL) > 0, NULL, sum(convs_30)) AS c30,
                           if(countIf(convs_prev_30 IS NULL) > 0, NULL, sum(convs_prev_30)) AS cp30,
                           argMax(concat(va, ': conversations ', if(convs_change_pct < 0, 'down ', 'up '),
                                         toString(abs(toInt32(round(convs_change_pct)))), '%'), abs(ifNull(convs_change_pct, 0))) AS big_conv,
                           max(abs(ifNull(convs_change_pct, 0))) AS big_conv_size,
                           argMax(concat(va, ': containment ', if(containment_change_pts < 0, 'down ', 'up '),
                                         toString(abs(containment_change_pts)), ' points'), abs(ifNull(containment_change_pts, 0))) AS big_cont,
                           max(abs(ifNull(containment_change_pts, 0))) AS big_cont_size
                    FROM n360.v_va_change GROUP BY account),
             fu AS (SELECT account, if(countIf(used IS NOT NULL) = 0, NULL, concat(toString(countIf(used)), ' of ', toString(count()))) AS f
                    FROM n360.v_feature_use GROUP BY account),
             tk AS (SELECT account, countIf(status = 'open') AS open_t FROM n360.ps_ticket GROUP BY account)
        SELECT a.display_name AS Customer, a.csm_rocketlane AS CSM, if(st.account = '', 'unbilled', st.worst) AS Status,
               if(st.account = '', 'No contract line', st.lines) AS Lines,
               vc.c30 AS `Conversations, 30 days`, round(100 * (vc.c30 - vc.cp30) / vc.cp30, 1) AS `vs previous 30`,
               -- the bigger of the two changes, a conversations change weighted 1, a containment point weighted 3
               if(vc.big_conv_size >= 3 * vc.big_cont_size, vc.big_conv, vc.big_cont) AS `Biggest change`,
               fu.f AS `Target features`, if((SELECT count() FROM n360.ps_ticket) = 0, NULL, ifNull(tk.open_t, 0)) AS `Open tickets`,
               a.account AS account
        FROM n360.account AS a
        LEFT JOIN st ON st.account = a.account LEFT JOIN vc ON vc.account = a.account
        LEFT JOIN fu ON fu.account = a.account LEFT JOIN tk ON tk.account = a.account
        WHERE a.{CSM}
        ORDER BY ifNull(st.worst, '') = '', indexOf(['{"', '".join(STATUS)}'], st.worst), Customer""", 0, 8, 24, 15,
        describe('One row per customer: its most urgent contract status and its lines by status, conversations over the '
                 'last 30 days against the 30 before (a window with an unread day is "Not reported yet"), the biggest '
                 'change among its VAs in a sentence (a containment point counted as 3% of conversations), target '
                 'features used, and open tickets. Sorted by status urgency. Click a customer to open it.',
                 'n360.v_contract_status, n360.v_va_change, n360.v_feature_use, n360.ps_ticket', 'CSM',
                 'the 60 days to the data-through day'),
        field=field_config([hidden('account'), coloured('Status', {**STATUS, 'unbilled': ('Unbilled', 'none')}, 'color-background'),
                            count_col('Conversations, 30 days'), override('vs previous 30', ('unit', 'percent'), ('decimals', 0)),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('Conversations, 30 days', 'vs previous 30',
                                                                                 'Target features', 'Open tickets')],
                            width('Customer', 170), width('CSM', 170), width('Status', 130), width('Lines', 230),
                            width('Conversations, 30 days', 120), width('vs previous 30', 100), width('Target features', 110),
                            width('Open tickets', 100),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))]),
        options={'cellHeight': 'sm'}))
    p.append(panel(8, 'Conversations per week, last 26 weeks', 'timeseries', f"""
        SELECT toDateTime(toMonday(d.day)) AS time, a.display_name AS customer,
               if(countIf(d.conversations IS NULL) > 0, NULL, sum(d.conversations)) AS conversations
        FROM n360.va_day AS d INNER JOIN n360.account AS a ON a.account = d.account
        WHERE d.{in_csm} AND d.day > {DATA_THROUGH_SQL} - 182
          AND toMonday(d.day) + 6 <= {DATA_THROUGH_SQL}
        GROUP BY time, customer ORDER BY time""", 0, 23, 24, 9,
        describe('Each customer\'s conversations per complete week (Monday to Sunday), one line per customer, in shades '
                 'of one colour (customers are not statuses). A week with an unread day is a gap. Hover for values.',
                 'n360.va_day', 'CSM', 'the 26 complete weeks to the data-through day'),
        fmt='timeseries',
        field=field_config(unit='short', decimals=0, min=0, displayName='${__field.labels.customer}',
                           custom={'lineWidth': 2, 'spanNulls': False, 'scaleDistribution': {'type': 'log', 'log': 10}},
                           **series_colours()),
        options={'legend': {'displayMode': 'table', 'placement': 'right', 'showLegend': True, 'calcs': ['lastNotNull']},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}))
    p[-1]['timeFrom'] = '200d'
    p.append(panel(9, 'VAs: last 30 days against the 30 before', 'table', f"""
        SELECT c.va AS VA, a.display_name AS Customer, c.convs_30 AS `Conversations, 30 days`,
               c.convs_change_pct AS `vs previous 30`, c.containment_30 AS Containment,
               c.containment_change_pts AS `Change (points)`,
               arrayStringConcat(arrayFilter(x -> x != '', [if(c.convs_down, 'Conversations down', ''),
                                                            if(c.containment_down, 'Containment down', '')]), ' · ') AS Flag,
               concat('/d/n360-va?var-account=', c.account, '&var-va=', toString(c.bot_id)) AS drill, c.account AS account
        FROM n360.v_va_change AS c INNER JOIN n360.account AS a ON a.account = c.account
        WHERE a.{CSM}
        ORDER BY c.convs_down OR c.containment_down DESC, abs(ifNull(c.convs_change_pct, 0)) DESC""", 0, 32, 24, 12,
        describe('Every VA of the chosen CSMs\' customers, flagged ones first. Containment is per VA, never across '
                 'channels; "Not reported yet" where handoffs are not read. Flags: conversations down 10%+, '
                 'containment down 3+ points (proposed). Click a VA or a customer to open it.',
                 'n360.v_va_change (n360.va_day)', 'CSM', 'the 60 days to the data-through day'),
        field=field_config([hidden('drill'), hidden('account'), count_col('Conversations, 30 days'),
                            override('vs previous 30', ('unit', 'percent'), ('decimals', 0)),
                            override('Containment', ('unit', 'percent'), ('decimals', 0)), override('Change (points)', ('decimals', 1)),
                            coloured('Flag', {'Conversations down': ('Conversations down', 'down'),
                                              'Containment down': ('Containment down', 'down'),
                                              'Conversations down · Containment down': ('Conversations and containment down', 'down'),
                                              '': ('', 'none')}),
                            *[override(c, ('mappings', [empty_grey()])) for c in
                              ('Conversations, 30 days', 'vs previous 30', 'Containment', 'Change (points)')],
                            width('VA', 220), width('Customer', 170), width('Flag', 240),
                            with_links('VA', path_link('drill', 'Open VA')),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))]),
        options={'cellHeight': 'sm'}))
    return dashboard(UID['health'], 'N360 · Customer health', p, [var_csm()], [back_link()])


def level_chip(column, short=False):
    """A risk level as a coloured chip; short = a dash for "not reported" (the grid, where columns are narrow)."""
    labels = {**LEVEL, 'not reported': ('—', 'grey')} if short else LEVEL
    return coloured(column, labels, cell='color-background')


def risk():
    """Risk (docs/17): which customers are at risk, why, and who is acting? CSMs and customer architects.
    Levels come from n360.v_risk_signal (rules PROPOSED in n360.settings); nothing is re-ruled here."""
    in_csm = "account IN (SELECT account FROM n360.account WHERE csm_rocketlane IN (${csm:singlequote}))"
    window = "(SELECT risk_renewal_window_days FROM n360.settings)"
    ref_night = """(SELECT if(countIf(data_date <= newest - 7) > 0, maxIf(data_date, data_date <= newest - 7), min(data_date))
                    FROM n360.risk_history CROSS JOIN (SELECT max(data_date) AS newest FROM n360.risk_history) AS m)"""
    p = page_header(1, f"""
        SELECT concat({plural("countIf(level = 'risk')", 'customer', 'customers')}, ' at risk · ',
                      toString(countIf(level = 'risk' AND days_to_renewal BETWEEN 0 AND {window})), ' of them renew within ',
                      toString({window}), ' days · ',
                      {plural(f"(SELECT count() FROM n360.v_risk_register WHERE status = 'open' AND {in_csm})", 'open risk', 'open risks')},
                      ' in the register, ', toString((SELECT countIf(overdue) FROM n360.v_risk_register WHERE status = 'open' AND {in_csm})),
                      ' past due') AS sentence
        FROM n360.v_risk_customer WHERE {in_csm}""",
        describe('Customers with at least one signal at risk (one risk signal is enough, as in Totango), how many of them '
                 'renew within 120 days (n360.settings risk_renewal_window_days), and the risk register. Rules and '
                 'thresholds are PROPOSED (n360.settings; docs/17_RISK_RESEARCH.md).',
                 'n360.v_risk_customer, n360.v_risk_register', 'CSM', 'the latest load'))
    p.append(tile(3, 'Customers at risk', f"""
        SELECT data_date AS night, toUInt64(uniqExactIf(account, level = 'risk')) AS value FROM n360.risk_history
        WHERE {in_csm} GROUP BY night""", 0, 3,
        describe('Customers with at least one signal at risk, each loaded night (n360.risk_history, written by the nightly '
                 'load from n360.v_risk_signal). The change is against 30 nights ago, or the oldest stored night.',
                 'n360.risk_history', 'CSM', 'the last 30 loaded nights'), metric='customers at risk'))
    p.append(panel(4, 'At-risk renewals', 'stat', f"""
        SELECT now() AS time, toUInt64(countIf(level = 'risk' AND days_to_renewal BETWEEN 0 AND {window})) AS value,
               if(countIf(level = 'risk' AND days_to_renewal BETWEEN 0 AND {window} AND renewal_amount IS NOT NULL) = 0,
                  concat('within ', toString({window}), ' days'),
                  concat({num_sql("toUInt64(sumIf(ifNull(renewal_amount, 0), level = 'risk' AND days_to_renewal BETWEEN 0 AND " + window + "))")},
                         ' up for renewal within ', toString({window}), ' days')) AS change
        FROM n360.v_risk_customer WHERE {in_csm}""", 6, 3, 6, 5,
        describe('Customers at risk whose next renewal (the open Salesforce renewal, else the next contract term end) is '
                 'within 120 days, and the amount on those renewal opportunities (Salesforce Amount, in its currency; '
                 'not added up across currencies if there are several).', 'n360.v_risk_customer (n360.sf_opportunity)',
                 'CSM', 'the latest load'),
        fmt='timeseries', field=plain(field_config(unit='locale', decimals=0, displayName='${__field.labels.change}')),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': ''}, 'textMode': 'value_and_name', 'colorMode': 'none',
                 'graphMode': 'none', 'justifyMode': 'auto', 'text': {'titleSize': 13, 'valueSize': 34}}))
    p.append(panel(5, 'New risk signals', 'stat', f"""
        WITH {ref_night} AS ref
        SELECT now() AS time, toUInt64(count()) AS value,
               concat('at risk now, not on ', toString(toDayOfMonth(ref)), ' ', formatDateTime(ref, '%b')) AS change
        FROM n360.v_risk_signal AS s
        WHERE s.level = 'risk' AND s.{in_csm}
          AND (s.account, s.signal) NOT IN (SELECT account, signal FROM n360.risk_history WHERE data_date = ref AND level = 'risk')""",
        12, 3, 6, 5,
        describe('Signals at risk now that were not at risk a week ago (the stored night 7 nights back, or the oldest).',
                 'n360.v_risk_signal, n360.risk_history', 'CSM', 'now against a week ago'),
        fmt='timeseries', field=plain(field_config(unit='locale', decimals=0, displayName='${__field.labels.change}')),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': ''}, 'textMode': 'value_and_name', 'colorMode': 'none',
                 'graphMode': 'none', 'justifyMode': 'auto', 'text': {'titleSize': 13, 'valueSize': 34}}))
    p.append(panel(6, 'Open risks past due', 'stat', f"""
        SELECT now() AS time, toUInt64(countIf(overdue)) AS value,
               concat('of ', toString(count()), ' open in the register') AS change
        FROM n360.v_risk_register WHERE status = 'open' AND {in_csm}""", 18, 3, 6, 5,
        describe('Open register entries whose due date has passed. Risks are recorded with code/risk_register.py '
                 '(Grafana cannot write data).', 'n360.v_risk_register (n360.risk_register)', 'CSM', 'now'),
        fmt='timeseries', field=plain(field_config(unit='locale', decimals=0, displayName='${__field.labels.change}',
                                                   noValue='Nothing in the register yet')),
        options={'reduceOptions': {'calcs': ['lastNotNull'], 'fields': ''}, 'textMode': 'value_and_name', 'colorMode': 'none',
                 'graphMode': 'none', 'justifyMode': 'auto', 'text': {'titleSize': 13, 'valueSize': 34}}))
    cols = ',\n               '.join(f"anyIf(s.level, s.signal = '{k}') AS `{t}`" for k, t in RISK_SIGNALS)
    p.append(panel(7, 'Risk by customer and signal', 'table', f"""
        WITH {ref_night} AS ref,
             before AS (SELECT account, max(multiIf(level = 'risk', 3, level = 'watch', 2, level = 'fine', 1, 0)) AS r
                        FROM n360.risk_history WHERE data_date = ref GROUP BY account)
        SELECT c.display_name AS Customer, c.level AS Overall, c.csm_level AS CSM, c.ca_level AS Architect,
               c.days_to_renewal AS `Renews in`,
               {cols},
               multiIf(b.r = 0, '', max(s.level_rank) > b.r, '▲ worse', max(s.level_rank) < b.r, '▼ better', '● same') AS `vs a week ago`,
               c.account AS account
        FROM n360.v_risk_customer AS c INNER JOIN n360.v_risk_signal AS s ON s.account = c.account
        LEFT JOIN before AS b ON b.account = c.account
        WHERE c.{in_csm}
        GROUP BY c.display_name, c.level, c.csm_level, c.ca_level, c.days_to_renewal, c.account, b.r
        ORDER BY indexOf(['risk', 'watch', 'fine', 'not reported'], Overall), `Renews in` IS NULL, `Renews in`, Customer""",
        0, 8, 24, 15,
        describe('One row per customer, one chip per signal: Risk, Watch, OK, or a grey dash = not reported (the source has '
                 'nothing for this customer: never OK). Overall = the worst signal; CSM = the commercial and relationship '
                 'signals (Pace to CSM view); Architect = the technical ones (VA health to Support). Renews in = days to '
                 'the open Salesforce renewal, else the next contract term end. "vs a week ago" compares the worst level '
                 'with the stored night a week back. The reasons are in the table below. Sorted by level, then the '
                 'soonest renewal. Click a customer to open it.',
                 'n360.v_risk_customer, n360.v_risk_signal (contract status, usage, anomalies, Salesforce, register, '
                 'configuration, tickets), n360.risk_history', 'CSM', 'the latest load; a week ago for the trend'),
        field=field_config([hidden('account'), *[level_chip(c, True) for c in ('Overall', 'CSM', 'Architect')],
                            *[level_chip(t, True) for _, t in RISK_SIGNALS],
                            override('Renews in', ('unit', 'suffix: d'), ('decimals', 0), ('mappings', [
                                {'type': 'range', 'options': {'from': -100000, 'to': -1, 'result': {'text': 'Ended', 'index': 0}}},
                                {'type': 'special', 'options': {'match': 'null', 'result': {'text': 'No date', 'color': COLOUR['grey'], 'index': 1}}}])),
                            override('vs a week ago', ('mappings', [{'type': 'regex', 'options': {'pattern': '^▲.*', 'result': {
                                'color': COLOUR['risk'], 'index': 0}}}]), ('custom.cellOptions', {'type': 'color-text'}),
                                ('color', {'mode': 'fixed', 'fixedColor': COLOUR['none']})),
                            width('Customer', 145), *[width(c, 68) for c in ('Overall', 'CSM', 'Architect')],
                            width('Renews in', 70), *[width(t, 72) for _, t in RISK_SIGNALS], width('vs a week ago', 100),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))],
                           custom={'align': 'center'}),
        options={'cellHeight': 'sm'}))
    p.append(panel(8, 'Why: every signal at risk or watch', 'table', f"""
        SELECT c.display_name AS Customer, s.lens AS Lens, s.title AS Signal, s.level AS Level, s.reason AS Reason,
               c.account AS account
        FROM n360.v_risk_signal AS s INNER JOIN n360.v_risk_customer AS c ON c.account = s.account
        WHERE s.level IN ('risk', 'watch') AND s.{in_csm}
        ORDER BY indexOf(['risk', 'watch', 'fine', 'not reported'], c.level), c.days_to_renewal IS NULL, c.days_to_renewal,
                 Customer, s.level_rank DESC, Lens DESC""", 0, 23, 24, 12,
        describe('The reason behind every Risk and Watch chip above, in words, customers in the same order. Lens: CSM '
                 '(commercial, relationship) or CA (customer architect: technical). Rules: n360.v_risk_signal.',
                 'n360.v_risk_signal', 'CSM; signals at risk or watch', 'the latest load'),
        field=field_config([hidden('account'), level_chip('Level'), width('Customer', 170), width('Lens', 60),
                            width('Signal', 170), width('Level', 80),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))],
                           noValue='No signal at risk or watch'),
        options={'cellHeight': 'sm'}, may_be_empty='no signal at risk or watch'))
    p.append(panel(9, 'Risk register: open', 'table', f"""
        SELECT a.display_name AS Customer, r.reason AS Reason, r.impact AS Impact, r.owner AS Owner,
               {date_sql('r.due')} AS Due, r.age_days AS `Age (days)`,
               if(r.overdue, 'Past due', if(r.due IS NULL, 'No due date', 'Open')) AS State, r.note AS Note, r.risk_id AS Id,
               r.account AS account
        FROM n360.v_risk_register AS r INNER JOIN n360.account AS a ON a.account = r.account
        WHERE r.status = 'open' AND r.{in_csm}
        ORDER BY r.overdue DESC, r.impact = 'churn' DESC, r.due IS NULL, r.due""", 0, 35, 24, 7,
        describe('Open risks recorded by CSMs: reason (a short fixed list), impact (churn, downsell, contraction), owner '
                 'and due date. Past due first. Record, update and close with code/risk_register.py; Grafana cannot '
                 'write data.', 'n360.v_risk_register (n360.risk_register)', 'CSM; open risks', 'now'),
        field=field_config([hidden('account'), coloured('State', {'Past due': ('Past due', 'risk'), 'Open': ('Open', 'none'),
                                                                  'No due date': ('No due date', 'amber')}),
                            coloured('Impact', {'churn': ('Churn', 'risk'), 'downsell': ('Downsell', 'none'),
                                                'contraction': ('Contraction', 'none')}),
                            width('Customer', 170), width('Reason', 190), width('Impact', 100), width('Owner', 100),
                            width('Due', 110), width('Age (days)', 90), width('State', 110), width('Id', 90),
                            with_links('Customer', drill(UID['customer'], 'account', 'account', 'Open customer'))],
                           noValue='No open risk in the register'),
        options={'cellHeight': 'sm'}, may_be_empty='no open risk'))
    p.append(panel(10, 'Renewal runway', 'state-timeline', f"""
        WITH lanes AS (SELECT display_name AS lane, (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS dt,
                              renewal_date AS rd, multiIf(level = 'risk', 3, level = 'watch', 2, level = 'fine', 1, 0) AS lv
                       FROM n360.v_risk_customer WHERE {in_csm} AND days_to_renewal BETWEEN 0 AND 180),
             times AS (SELECT DISTINCT t FROM (SELECT dt AS t FROM lanes UNION ALL SELECT rd FROM lanes))
        SELECT toDateTime(times.t) AS time, lanes.lane AS lane, toUInt8(if(times.t >= lanes.rd, 9, lanes.lv)) AS state
        FROM lanes CROSS JOIN times ORDER BY time""", 0, 42, 12, 10,
        describe('Customers renewing within 180 days: one lane from the data-through day to the renewal date (the open '
                 'Salesforce renewal, else the term end), in the customer\'s risk colour. Lanes ending first are the most '
                 'urgent; 120 days is the at-risk window (n360.settings).', 'n360.v_risk_customer', 'CSM',
                 'the next 180 days'),
        fmt='timeseries',
        field=field_config(mappings=[{'type': 'value', 'options': {
            '3': {'text': 'Risk', 'color': COLOUR['risk'], 'index': 0}, '2': {'text': 'Watch', 'color': COLOUR['amber'], 'index': 1},
            '1': {'text': 'OK', 'color': COLOUR['green'], 'index': 2}, '0': {'text': 'Not reported', 'color': COLOUR['grey'], 'index': 3},
            '9': {'text': 'Renewal', 'color': 'transparent', 'index': 4}}}],
            displayName='${__field.labels.lane}', color={'mode': 'fixed', 'fixedColor': COLOUR['grey']}, **TIMELINE),
        options={'showValue': 'never', 'mergeValues': True, 'rowHeight': 0.8, 'alignValue': 'left',
                 'legend': {'showLegend': True, 'displayMode': 'list', 'placement': 'bottom'}},
        may_be_empty='no renewal within 180 days'))
    p.append(panel(11, 'Customers at risk and on watch, night by night', 'timeseries', f"""
        SELECT toDateTime(data_date) AS time, uniqExactIf(account, r = 3) AS `At risk`, uniqExactIf(account, r = 2) AS `On watch`
        FROM (SELECT data_date, account, max(multiIf(level = 'risk', 3, level = 'watch', 2, 1)) AS r FROM n360.risk_history
              WHERE {in_csm} GROUP BY data_date, account)
        GROUP BY time ORDER BY time""", 12, 42, 12, 10,
        describe('How many customers were at risk (any signal at risk) and on watch (worst signal watch), each loaded '
                 'night. Grows as nights are stored; with weekly history this becomes the base to check, later, '
                 'whether the rules predicted churn (docs/17).', 'n360.risk_history', 'CSM', 'every stored night'),
        fmt='timeseries',
        field=field_config([override('At risk', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['risk']})),
                            override('On watch', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['amber']}))],
                           decimals=0, min=0, custom={'drawStyle': 'bars', 'fillOpacity': 85, 'lineWidth': 1,
                                                      'stacking': {'mode': 'normal', 'group': 'A'}, 'barAlignment': 0}),
        options={'legend': {'showLegend': True, 'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'multi', 'sort': 'none'}}))
    p[-1]['timeFrom'] = '45d'
    p.append(panel(12, 'Architect view: every VA', 'table', f"""
        WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS dt,
             chg AS (SELECT bot_id, max(if(changed, snapshot_date, first_read)) AS last_change, max(snapshot_date) AS last_read
                     FROM (SELECT bot_id, snapshot_date, min(snapshot_date) OVER (PARTITION BY bot_id) AS first_read,
                                  (engine_version, v8_playbooks, legacy_intents, mcp_servers_live, supervisory_rules_on)
                                    != lagInFrame((engine_version, v8_playbooks, legacy_intents, mcp_servers_live, supervisory_rules_on))
                                       OVER (PARTITION BY bot_id ORDER BY snapshot_date ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
                                    AND row_number() OVER (PARTITION BY bot_id ORDER BY snapshot_date) > 1 AS changed
                           FROM n360.va_config_snapshot) GROUP BY bot_id),
             fl AS (SELECT bot_id, countIf(kind = 'went silent') AS s, countIf(kind = 'drop') AS d FROM n360.v_va_day_anomaly
                    WHERE NOT provisional AND day >= dt - 9 GROUP BY bot_id),
             idle AS (SELECT bot_id, count() AS n FROM (SELECT bot_id, playbook FROM n360.playbook_day WHERE day > dt - 31
                      GROUP BY bot_id, playbook HAVING sum(executions) = 0) GROUP BY bot_id)
        SELECT c.va AS VA, a.display_name AS Customer, c.convs_30 AS `Conversations, 30 days`, c.convs_change_pct AS `vs 30 before`,
               c.containment_30 AS Containment, c.containment_change_pts AS `Containment change`,
               g.engine_version AS Engine, g.mcp_servers_live AS `MCP servers`,
               if(ch.last_read IS NULL, NULL, dateDiff('week', ch.last_change, ch.last_read)) AS `Weeks unchanged`,
               concat(if(ifNull(fl.s, 0) > 0, 'Silent', ''), if(ifNull(fl.s, 0) > 0 AND ifNull(fl.d, 0) > 0, ' · ', ''),
                      if(ifNull(fl.d, 0) > 0, concat(toString(fl.d), ' drops'), '')) AS `Flags, 7 days`,
               ifNull(idle.n, 0) AS `Idle playbooks`,
               concat('/d/n360-va?var-account=', c.account, '&var-va=', toString(c.bot_id)) AS drill
        FROM n360.v_va_change AS c INNER JOIN n360.account AS a ON a.account = c.account
        LEFT JOIN (SELECT toNullable(bot_id) AS bot_id, engine_version, mcp_servers_live FROM n360.v_va_config_latest) AS g ON g.bot_id = c.bot_id
        LEFT JOIN (SELECT toNullable(bot_id) AS bot_id, last_change, toNullable(last_read) AS last_read FROM chg) AS ch ON ch.bot_id = c.bot_id
        LEFT JOIN fl ON fl.bot_id = c.bot_id LEFT JOIN idle ON idle.bot_id = c.bot_id
        WHERE c.{in_csm}
        ORDER BY `Conversations, 30 days` DESC""", 0, 52, 24, 11,
        describe('One row per VA, busiest first, so the biggest technical problem is at the top: conversations and '
                 'containment against the 30 days before (containment per VA only), engine, MCP servers live (a VA with '
                 'none is capped at answering questions), weeks since its configuration last changed, settled silences '
                 'and drops in the 7 newest settled days, and playbooks built but not run in 31 days. Engine, MCP and '
                 'weeks unchanged need the configuration export ("Not reported yet" until then). Click a VA to open it.',
                 'n360.v_va_change, n360.v_va_config_latest, n360.va_config_snapshot, n360.v_va_day_anomaly, '
                 'n360.playbook_day', 'CSM', 'the 60 days to the data-through day; the newest configuration read'),
        field=field_config([hidden('drill'), count_col('Conversations, 30 days'),
                            override('vs 30 before', ('unit', 'percent'), ('decimals', 0)),
                            override('Containment', ('unit', 'percent'), ('decimals', 0)),
                            override('Containment change', ('decimals', 1)),
                            coloured('Flags, 7 days', {'': ('', 'none')}),
                            override('Flags, 7 days', ('mappings', [{'type': 'regex', 'options': {'pattern': '.+', 'result': {
                                'color': COLOUR['down'], 'index': 0}}}]), ('custom.cellOptions', {'type': 'color-text'})),
                            *[override(c, ('mappings', [empty_grey()])) for c in
                              ('Conversations, 30 days', 'vs 30 before', 'Containment', 'Containment change', 'Engine',
                               'MCP servers', 'Weeks unchanged')],
                            width('VA', 200), width('Customer', 160),
                            with_links('VA', path_link('drill', 'Open VA'))]),
        options={'cellHeight': 'sm'}))
    p.append(panel(13, 'Closed risks, last 90 days', 'table', f"""
        SELECT a.display_name AS Customer, r.reason AS Reason, r.impact AS Impact, r.outcome AS Outcome,
               {date_sql('toDate(r.updated_at)')} AS Closed, r.note AS Note
        FROM n360.v_risk_register AS r INNER JOIN n360.account AS a ON a.account = r.account
        WHERE r.status = 'closed' AND r.updated_at > now() - INTERVAL 90 DAY AND r.{in_csm}
        ORDER BY r.updated_at DESC""", 0, 63, 24, 6,
        describe('Risks closed in the last 90 days and how they ended (saved, churned, downsold, no action, invalid): what '
                 'worked, and the reasons behind churn. Over time this is the outcome data a weighted score would be '
                 'checked against (docs/17).', 'n360.v_risk_register', 'CSM; closed risks', 'the last 90 days'),
        field=field_config([coloured('Outcome', {'saved': ('Saved', 'green'), 'churned': ('Churned', 'risk'),
                                                 'downsold': ('Downsold', 'amber'), 'no action': ('No action', 'none'),
                                                 'invalid': ('Invalid', 'grey')}, 'color-background'),
                            width('Customer', 170), width('Reason', 190), width('Impact', 100), width('Outcome', 100),
                            width('Closed', 110)], noValue='No risk closed in 90 days'),
        options={'cellHeight': 'sm'}, may_be_empty='no risk closed in 90 days'))
    return dashboard(UID['risk'], 'N360 · Risk', p, [var_csm()], [back_link(), back_link(('n360-risk-model', 'Risk model'))],
                     time={'from': 'now-60d', 'to': 'now+180d'})


def risk_model_dashboard():
    """Risk model (docs/21): how likely is each customer to churn or downsell at its next renewal, and why? Reads the
    scores code/risk/risk_model.py writes; feature titles and the expert prior come from code/risk/risk_model.json."""
    cfg = json.load(open(os.path.join(os.path.dirname(HERE), 'risk', 'risk_model.json')))
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'risk'))
    import model as rm
    keys = list(cfg['features'])
    def titles(col='feature'):
        return (f"transform({col}, ['" + "', '".join(keys + [rm.CAP]) + "'], ['"
                + "', '".join([cfg['features'][k]['title'].replace("'", "\\'") for k in keys]
                              + [f"Hard red (health capped at {cfg['hard_red']['cap_health']})"]) + f"'], {col})")
    title_sql = titles()
    dims = list(cfg['dimensions'])
    dim_sql = (f"transform(dimension, ['" + "', '".join(dims + ['hard red']) + "'], ['"
               + "', '".join([cfg['dimensions'][d]['title'] for d in dims] + ['Hard red']) + "'], dimension)")
    pr = rm.prior(cfg)
    first_stage = cfg['probability']['stages']['calibrate']
    prior_vals = ', '.join(f"('{k}', {pr['coef'][k]:.4f})" for k in pr['coef'])
    BAND = {'at risk': ('At risk', 'risk'), 'watch': ('Watch', 'amber'), 'healthy': ('Healthy', 'green'),
            'not enough data': ('Not enough data', 'grey'), 'not reported': ('Not reported', 'grey'),
            'churned': ('Churned', 'grey')}
    band_order = "['at risk', 'watch', 'healthy', 'not enough data', 'not reported', 'churned']"
    horizon = cfg['eligible_days_to_renewal']
    in_csm = "account IN (SELECT account FROM n360.account WHERE csm_rocketlane IN (${csm:singlequote}))"
    one = "account IN (${customer:singlequote})"
    trained_auc = """JSONExtract(backtest, 'methods', 'trained model (probability)', 'auc', 'Nullable(Float64)')"""
    p = page_header(1, f"""
        WITH (SELECT any(kind) FROM n360.v_risk_model) AS kind,
             (SELECT countIf(outcome IN ('churned', 'downsold')) FROM n360.v_risk_outcomes) AS events,
             (SELECT any({trained_auc}) FROM n360.v_risk_model) AS auc
        SELECT if(count() = 0, NULL, concat(
                   {plural("countIf(band = 'at risk')", 'customer', 'customers')}, ' at risk · ',
                   if(sumIf(revenue_at_risk, revenue_at_risk IS NOT NULL) > 0,
                      concat({num_sql("toUInt64(sumIf(revenue_at_risk, revenue_at_risk IS NOT NULL))")}, ' revenue at risk in renewals within {horizon} days · '), ''),
                   'model: ', if(kind = 'trained', 'learning from ', 'expert prior (starts learning at {first_stage} renewals lost; '),
                   {plural('events', 'recorded loss', 'recorded losses')}, if(kind = 'trained', '', ' so far)'),
                   if(auc IS NULL, '', concat(' · backtest AUC ', toString(round(auc, 2)))))) AS sentence
        FROM n360.v_risk_score WHERE {in_csm}""",
        describe('Customers the model puts at risk (health under 50 or a hard red), the revenue at risk (chance of churn or '
                 'downsell at the next renewal x the open Salesforce renewal amount, renewals within the horizon), and '
                 'the model in use: the expert prior until enough outcomes exist, then a model trained on them. AUC: how '
                 'well it ranked customers out of time (0.5 = chance, 1 = perfect).',
                 'n360.v_risk_score, n360.v_risk_model, n360.v_risk_outcomes (code/risk/risk_model.py)', 'CSM', 'the newest score'))
    p.append(tile(3, 'At risk (model)', f"""
        SELECT as_of AS night, toUInt64(countIf(band = 'at risk')) AS value FROM n360.risk_scores
        WHERE (as_of, scored_at) IN (SELECT as_of, max(scored_at) FROM n360.risk_scores GROUP BY as_of) AND {in_csm}
        GROUP BY night""", 0, 3,
        describe('Customers with health under 50 or a hard red, each scored date (the score history is filled by '
                 '"risk_model.py score --as-of all" and each nightly run).', 'n360.risk_scores', 'CSM', 'the last 30 scored dates')))
    p.append(single_tile(4, 'Revenue at risk', f"""
        SELECT toUInt64(sumIf(revenue_at_risk, revenue_at_risk IS NOT NULL)) AS `chance x renewal amount, next {horizon} days`
        FROM n360.v_risk_score WHERE {in_csm}""", 6, 3,
        describe(f'Sum over customers renewing within {horizon} days of: the chance of churn or downsell at that renewal '
                 'times the open Salesforce renewal amount (its currency; not converted). Customers with no renewal '
                 'amount add nothing.', 'n360.v_risk_score (n360.sf_opportunity)', 'CSM', 'the newest score')))
    p.append(single_tile(5, 'Outcomes to learn from', f"""
        SELECT countIf(outcome IN ('churned', 'downsold')) AS `churns and downsells recorded (learning starts at {first_stage} renewals lost)`
        FROM n360.v_risk_outcomes""", 12, 3,
        describe('Churns and downsells known so far: closed renewals in Salesforce, closed register entries, outcomes '
                 'recorded by hand ("risk_model.py outcome"). The same loss can be recorded by two sources; the model '
                 'counts one outcome per renewal. Under the first stage the model is the expert prior.',
                 'n360.v_risk_outcomes', 'none (every customer)', 'all time')))
    p.append(single_tile(6, 'Backtest AUC', f"""
        SELECT {trained_auc} AS `out of time; 0.5 = chance, 1 = perfect` FROM n360.v_risk_model""", 18, 3,
        describe('How well the model ranked customers who later churned or downsold above those who did not, scored '
                 'out of time (each period by a model trained only on earlier outcomes). Empty until there is enough '
                 'history: the walk-forward needs 180 days of features and outcomes after them.',
                 'n360.v_risk_model (risk_model.py train)', 'none', 'the newest training run'),
        field=plain(field_config(decimals=2, noValue='Not enough history yet'))))
    p.append(panel(7, 'Customers by health and chance at the next renewal', 'table', f"""
        WITH top AS (SELECT account, arrayStringConcat(arraySlice(arrayMap(x -> x.2, arraySort(x -> -x.1,
                                groupArray((points, {titles('c.feature')})))), 1, 3), ' · ') AS drivers
                     FROM n360.v_risk_drivers AS c WHERE c.points > 0.5 GROUP BY account)
        SELECT a.display_name AS Customer, s.band AS Band, s.health AS Health, s.probability AS `Chance at renewal`,
               if(s.eligible, 'within {horizon} days', 'later') AS Renewal, s.days_to_renewal AS `Renews in`,
               s.revenue_at_risk AS `Revenue at risk`, ifNull(t.drivers, '') AS `Biggest drivers`,
               arrayStringConcat(s.hard_reds, ' · ') AS `Hard reds`, s.coverage AS Coverage, s.account AS account
        FROM n360.v_risk_score AS s INNER JOIN n360.account AS a ON a.account = s.account
        LEFT JOIN top AS t ON t.account = s.account
        WHERE s.{in_csm}
        ORDER BY indexOf({band_order}, Band), s.probability DESC""", 0, 8, 24, 13,
        describe('Every customer: band (hard reds, else health under 50 at risk, under 75 watch), health (100 = no risk; the '
                 'weighted scorecard), the chance of churn or downsell at the next renewal (logistic model; the expert '
                 'prior until trained), revenue at risk, the three features costing the most health points, hard reds, '
                 'and coverage (share of the model\'s weight with data: low coverage = less to go on). Pick a customer '
                 'above to see every feature below.', 'n360.v_risk_score, n360.v_risk_drivers', 'CSM', 'the newest score'),
        field=field_config([hidden('account'), coloured('Band', BAND, 'color-background'),
                            override('Health', ('min', 0), ('max', 100), ('decimals', 0), ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']}),
                                     ('custom.cellOptions', {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'text'})),
                            override('Chance at renewal', ('unit', 'percentunit'), ('decimals', 0)),
                            override('Coverage', ('unit', 'percentunit'), ('decimals', 0)),
                            count_col('Revenue at risk'), override('Renews in', ('unit', 'suffix: d'), ('decimals', 0)),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('Health', 'Revenue at risk', 'Renews in')],
                            coloured('Renewal', {'later': ('Later', 'none')}),
                            override('Hard reds', ('mappings', [{'type': 'value', 'options': {'': {'text': ' ', 'index': 0}}}])),
                            width('Customer', 160), width('Band', 90), width('Health', 130), width('Chance at renewal', 90),
                            width('Renewal', 120), width('Renews in', 120), width('Revenue at risk', 120), width('Coverage', 80),
                            width('Biggest drivers', 380),
                            with_links('Customer', {'title': 'Show this customer below',
                                                    'url': f"/d/{UID['risk_model']}?var-customer=${{__data.fields.account}}&${{__url_time_range}}"})]),
        options={'cellHeight': 'sm'}, may_be_empty='nothing scored yet'))
    p.append(panel(8, 'What costs this customer health', 'barchart', f"""
        SELECT {title_sql} AS Feature, round(points, 1) AS `Health points lost`
        FROM n360.v_risk_drivers WHERE {one} AND points > 0 ORDER BY points DESC""", 0, 21, 12, 11,
        describe('For the customer chosen above: health points each feature costs (its share of the scorecard x its risk; '
                 'they add up to 100 - health; when a hard red caps health, the points it takes have their own bar).', 'n360.v_risk_drivers', 'Customer (one)',
                 'the newest score'),
        field=field_config(decimals=1, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['series']}),
        options={'xField': 'Feature', 'orientation': 'horizontal', 'showValue': 'always', 'barWidth': 0.7,
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'}},
        may_be_empty='no feature costs this customer health'))
    p.append(panel(9, 'Every feature for this customer', 'table', f"""
        SELECT {dim_sql} AS Dimension, {title_sql} AS Feature, value AS Value, risk AS Risk, points AS `Points lost`,
               logodds AS `Push on the odds`
        FROM n360.v_risk_drivers WHERE {one}
        ORDER BY indexOf(['{"', '".join(dims)}'], dimension), points DESC""", 12, 21, 12, 11,
        describe('Each feature for the chosen customer: its value, its risk (0 at the "good" anchor, 1 at the "bad" one; '
                 'risk_model.json), health points lost, and its push on the log-odds of churn against an average '
                 'customer (positive = riskier). "Not reported yet" = the source has nothing: it costs nothing and its '
                 'weight moves to the rest of its dimension.', 'n360.v_risk_drivers', 'Customer (one)', 'the newest score'),
        field=field_config([override('Value', ('decimals', 1)), override('Risk', ('decimals', 2), ('min', 0), ('max', 1),
                            ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']}),
                            ('custom.cellOptions', {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'text'})),
                            override('Points lost', ('decimals', 1)), override('Push on the odds', ('decimals', 2)),
                            *[override(c, ('mappings', [empty_grey()])) for c in ('Value', 'Risk')],
                            width('Dimension', 140), width('Feature', 250), width('Value', 70), width('Risk', 100),
                            width('Points lost', 80)]),
        options={'cellHeight': 'sm'}, may_be_empty='nothing scored yet'))
    p.append(panel(10, 'Health over time', 'timeseries', f"""
        SELECT toDateTime(s.as_of) AS time, a.display_name AS customer, s.health AS health
        FROM n360.risk_scores AS s INNER JOIN n360.account AS a ON a.account = s.account
        WHERE (s.as_of, s.scored_at) IN (SELECT as_of, max(scored_at) FROM n360.risk_scores GROUP BY as_of) AND s.{in_csm}
        ORDER BY time""", 0, 32, 12, 10,
        describe('Each customer\'s health on every scored date (one line per customer, shades of one colour; customers are '
                 'not statuses). The dashed line is 50, the at-risk line.', 'n360.risk_scores', 'CSM', 'every scored date'),
        fmt='timeseries',
        field=field_config(min=0, max=100, decimals=0, displayName='${__field.labels.customer}',
                           custom={'lineWidth': 2, 'spanNulls': False, **reference_line(cfg['bands']['watch'])['custom']},
                           thresholds=reference_line(cfg['bands']['watch'])['thresholds'], **series_colours()),
        options={'legend': {'displayMode': 'table', 'placement': 'right', 'showLegend': True, 'calcs': ['lastNotNull']},
                 'tooltip': {'mode': 'multi', 'sort': 'asc'}}, may_be_empty='nothing scored yet'))
    p[-1]['n360Reference'] = True
    p[-1]['timeFrom'] = '210d'
    p.append(panel(11, 'Dimensions for this customer', 'barchart', f"""
        SELECT {dim_sql} AS Dimension, round(sum(points), 1) AS `Health points lost`
        FROM n360.v_risk_drivers WHERE {one} GROUP BY dimension
        ORDER BY indexOf(['{"', '".join(dims)}'], dimension)""", 12, 32, 12, 10,
        describe('Health points the chosen customer loses in each dimension (weights in risk_model.json).',
                 'n360.v_risk_drivers', 'Customer (one)', 'the newest score'),
        field=field_config(decimals=1, min=0, color={'mode': 'fixed', 'fixedColor': COLOUR['series']}),
        options={'xField': 'Dimension', 'orientation': 'horizontal', 'showValue': 'always', 'barWidth': 0.7,
                 'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom'}},
        may_be_empty='nothing scored yet'))
    p.append(panel(12, 'Backtest: how well each method ranked churn, out of time', 'table', """
        SELECT method AS Method, JSONExtract(backtest, 'methods', method, 'auc', 'Nullable(Float64)') AS AUC,
               coalesce(JSONExtract(backtest, 'methods', method, 'top_precision', 'Nullable(Float64)'),
                        JSONExtract(backtest, 'methods', method, 'precision', 'Nullable(Float64)')) AS `Precision, top 10%`,
               coalesce(JSONExtract(backtest, 'methods', method, 'top_recall', 'Nullable(Float64)'),
                        JSONExtract(backtest, 'methods', method, 'recall', 'Nullable(Float64)')) AS `Recall, top 10%`,
               JSONExtract(backtest, 'methods', method, 'brier', 'Nullable(Float64)') AS Brier
        FROM n360.v_risk_model ARRAY JOIN JSONExtractKeys(backtest, 'methods') AS method""", 0, 42, 12, 8,
        describe('Walk-forward backtest of the newest training run: each period scored by a model trained only on '
                 'outcomes known before it, each renewal judged 150, 90 and 30 days ahead. AUC 0.5 = chance, 1 = perfect; '
                 'precision = share of the top 10% flagged that churned or downsold; recall = share of all churns and '
                 'downsells caught in the top 10% (for the rules: of what the rules flag, whatever its share); Brier = '
                 'calibration error of a probability (lower is better).',
                 'n360.v_risk_model (risk_model.py train)', 'none', 'the newest training run'),
        field=field_config([*[override(c, ('decimals', 3)) for c in ('AUC', 'Precision, top 10%', 'Recall, top 10%', 'Brier')],
                            *[override(c, ('mappings', [{'type': 'special', 'options': {'match': 'null', 'result': {
                                'text': '—', 'color': COLOUR['grey'], 'index': 0}}}])) for c in ('AUC', 'Precision, top 10%', 'Recall, top 10%', 'Brier')],
                            width('Method', 230)], noValue='Not enough history for a backtest yet (180 days of features and outcomes after them)'),
        options={'cellHeight': 'sm'}, may_be_empty='not enough history for a backtest'))
    p.append(panel(13, 'Calibration: predicted against observed', 'barchart', """
        SELECT JSONExtractString(x, 'bin') AS `Predicted chance`, JSONExtractFloat(x, 'predicted') AS `Mean predicted`,
               JSONExtractFloat(x, 'observed') AS Observed
        FROM n360.v_risk_model ARRAY JOIN JSONExtractArrayRaw(backtest, 'reliability') AS x""", 12, 42, 12, 8,
        describe('In the backtest, snapshots grouped by predicted chance: mean predicted beside the share that actually '
                 'churned or downsold. Bars of equal height = a well-calibrated model.', 'n360.v_risk_model', 'none',
                 'the newest training run'),
        field=field_config([override('Mean predicted', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series_light']})),
                            override('Observed', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']}))],
                           unit='percentunit', decimals=0, min=0),
        options={'xField': 'Predicted chance', 'orientation': 'vertical', 'showValue': 'never', 'groupWidth': 0.7,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True}},
        may_be_empty='not enough history for a backtest'))
    p.append(panel(14, 'Model weights against the expert prior', 'barchart', f"""
        SELECT {titles('p.f')} AS Feature, p.w AS `Expert prior`,
               if(m.kind = 'trained', JSONExtract(m.model, 'coef', p.f, 'Nullable(Float64)'), NULL) AS `Trained`
        FROM (SELECT f, w FROM values('f String, w Float64', {prior_vals})) AS p
        CROSS JOIN (SELECT any(kind) AS kind, any(model) AS model FROM n360.v_risk_model) AS m
        ORDER BY p.w DESC""", 0, 50, 24, 10,
        describe('Each feature\'s weight on the log-odds of churn: the expert prior (from the scorecard weights in '
                 'risk_model.json) and, once outcomes exist, the weight learned from them. Learning comes in stages: '
                 'first every weight moves together (one slope), then by dimension, then feature by feature; each is '
                 'pulled toward the prior, and none goes below zero. Features that keep no history (Salesforce forecast '
                 'category, open tickets) keep their prior weight, rescaled by the learned slope.', 'code/risk/risk_model.json, n360.v_risk_model', 'none', 'the newest training run'),
        field=field_config([override('Expert prior', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series_light']})),
                            override('Trained', ('color', {'mode': 'fixed', 'fixedColor': COLOUR['series']}))], decimals=2),
        options={'xField': 'Feature', 'orientation': 'horizontal', 'showValue': 'never', 'groupWidth': 0.8, 'barWidth': 0.9,
                 'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True}}))
    v_customer = query_var('customer', 'Customer', 'SELECT account, display_name FROM n360.account ORDER BY display_name',
                           multi=False, include_all=False)
    return dashboard(UID['risk_model'], 'N360 · Risk model', p, [var_csm(), v_customer],
                     [back_link(), back_link(('n360-risk', 'Risk'))], time={'from': 'now-210d', 'to': 'now'})


DASHBOARDS = [overview, contracts, customer, health, risk, risk_model_dashboard, contract_line, va, forecast, anomalies, containment, pipeline,
              adoption, requests, adoption_customer, adoption_va, cortex_netomi]


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
