#!/usr/bin/env python3
"""Generate the N360 · Operations dashboard (uid n360-ops): what ran each night, the runner's jobs, the loads, and the
buttons that start a job. Added 26 Nov 2026. Separate from build_dashboards.py on purpose: it depends on the local
runner (the company pipeline's ops_runner.py on 127.0.0.1:8099) and on two tables that runner creates
(n360.ops_job, n360.ops_night), which are not part of schema.sql or the 8 checks.

    python3 code/grafana/build_ops.py        # writes code/grafana/dashboards/n360-ops.json

Grafana cannot run commands. Each button is a link to the runner's confirm page (new tab); the runner starts the job
only from its own confirm form (one-time token), so a stray click or another web page cannot start one.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
DS = {'type': 'grafana-clickhouse-datasource', 'uid': 'n360-clickhouse'}
RUNNER = 'http://127.0.0.1:8099'
NOT_READ = 'Not reported yet'


def target(sql):
    return {'datasource': DS, 'editorType': 'sql', 'queryType': 'table', 'format': 1, 'rawSql': sql.strip(), 'refId': 'A'}


def stat(pid, title, sql, x, y, w, h, desc, mappings=None):
    return {'id': pid, 'type': 'stat', 'title': title, 'description': desc, 'datasource': DS,
            'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'targets': [target(sql)],
            'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '/.*/', 'values': False},
                        'textMode': 'value', 'colorMode': 'value' if mappings else 'none', 'graphMode': 'none',
                        'justifyMode': 'auto', 'wideLayout': True},
            'fieldConfig': {'defaults': {'noValue': NOT_READ, 'mappings': mappings or [],
                                         'color': {'mode': 'fixed', 'fixedColor': 'text'} if not mappings else {'mode': 'thresholds'},
                                         'thresholds': {'mode': 'absolute', 'steps': [{'color': 'text', 'value': None}]}},
                            'overrides': []}}


def rx(pattern, color, idx):
    return {'type': 'regex', 'options': {'pattern': pattern, 'result': {'color': color, 'index': idx}}}


STATUS_MAP = [rx('^(published|loaded|done|complete).*', 'green', 0), rx('^(stopped|failed|refused).*', 'red', 1),
              rx('^(running|not loaded yet|export only|ran).*', 'orange', 2)]


def table(pid, title, sql, x, y, w, h, desc, colored=(), links=None):
    ov = [{'matcher': {'id': 'byName', 'options': c},
           'properties': [{'id': 'mappings', 'value': STATUS_MAP}, {'id': 'custom.cellOptions', 'value': {'type': 'color-text'}}]}
          for c in colored]
    if links:
        col, url, label = links
        ov.append({'matcher': {'id': 'byName', 'options': col},
                   'properties': [{'id': 'links', 'value': [{'title': label, 'url': url, 'targetBlank': True}]}]})
    return {'id': pid, 'type': 'table', 'title': title, 'description': desc, 'datasource': DS,
            'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'targets': [target(sql)],
            'options': {'showHeader': True, 'cellHeight': 'sm', 'footer': {'show': False}},
            'fieldConfig': {'defaults': {'noValue': NOT_READ, 'custom': {'align': 'auto', 'filterable': True}}, 'overrides': ov}}


ACTIONS_HTML = f"""
<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:stretch">
  <a href="{RUNNER}/confirm?action=pull" target="_blank" rel="noopener" style="flex:1 1 220px;padding:14px 16px;border-radius:4px;background:#3d71d9;color:#fff;text-decoration:none;font-weight:600">
    &#9654; Run the full nightly<br><span style="font-weight:400;font-size:12px">Pull from every source (VPN needed, about an hour), export, load into ClickHouse</span></a>
  <a href="{RUNNER}/confirm?action=export" target="_blank" rel="noopener" style="flex:1 1 220px;padding:14px 16px;border-radius:4px;background:#22252b;border:1px solid #2f333b;color:#ccccdc;text-decoration:none;font-weight:600">
    &#8635; Export and load<br><span style="font-weight:400;font-size:12px">Rebuild the export for the newest pulled night, then load it</span></a>
  <a href="{RUNNER}/confirm?action=load" target="_blank" rel="noopener" style="flex:1 1 220px;padding:14px 16px;border-radius:4px;background:#22252b;border:1px solid #2f333b;color:#ccccdc;text-decoration:none;font-weight:600">
    &#8681; Load new exports<br><span style="font-weight:400;font-size:12px">Load any export not yet in ClickHouse (no VPN needed)</span></a>
  <a href="{RUNNER}/" target="_blank" rel="noopener" style="flex:0 1 160px;padding:14px 16px;border-radius:4px;background:#22252b;border:1px solid #2f333b;color:#ccccdc;text-decoration:none;font-weight:600">
    Open runner<br><span style="font-weight:400;font-size:12px">Live log of the current job</span></a>
</div>
<p style="font-size:12px;color:#9fa3ad;margin-top:10px">Each button opens a confirm page in a new tab; nothing starts until you press Start there.
If a button shows "can't connect", the runner isn't running: in Terminal, from the pipeline folder,
<code>~/Documents/Github/n360/.venv/bin/python ops_runner.py</code></p>
"""


def dashboard():
    p = []
    p.append(stat(1, 'In one sentence', """
        SELECT concat(
          'Last night pulled: ', ifNull((SELECT any(night) FROM (SELECT night FROM n360.ops_night FINAL
                                  WHERE claude_result LIKE 'published%' OR code_result = 'complete' ORDER BY night DESC LIMIT 1)), 'none on record'),
          ' · last load: ', ifNull((SELECT concat(toString(argMax(data_date, loaded_at)), ' ', toString(argMax(status, loaded_at))) FROM n360.runs), 'none'),
          ' · runner: ', ifNull((SELECT concat(argMax(action, started), ' ', argMax(state, started)) FROM n360.ops_job FINAL), 'no job yet')) AS sentence""",
        0, 0, 18, 3, 'Newest night the sources were pulled (by the Claude nightly or by code), the newest ClickHouse load, and the '
        'runner\'s latest job. Sources: n360.ops_night and n360.ops_job (written by the local runner), n360.runs (load.py).'))
    p.append(stat(2, 'Data through', """
        SELECT formatDateTime(window_end, '%e %b %Y') AS d FROM n360.runs WHERE status = 'loaded'
        ORDER BY loaded_at DESC, data_date DESC LIMIT 1""", 18, 0, 6, 3,
        'Last data day in the live ClickHouse tables (the export window ends 23:59 UTC that day). Source: n360.runs.'))
    p.append({'id': 3, 'type': 'text', 'title': 'Actions', 'gridPos': {'x': 0, 'y': 3, 'w': 24, 'h': 6},
              'description': 'Buttons for the local runner on this Mac (127.0.0.1:8099). It runs only these three jobs, one at a time.',
              'options': {'mode': 'html', 'content': ACTIONS_HTML.strip()}})
    p.append(stat(4, 'Runner: latest job', """
        SELECT if(count() = 0, NULL, concat(argMax(state, started), ' · ', argMax(action, started))) AS s FROM n360.ops_job FINAL""",
        0, 9, 6, 3, 'State and kind of the newest runner job. Source: n360.ops_job.', STATUS_MAP))
    p.append(stat(5, 'Claude nightly, last result', """
        SELECT if(count() = 0, NULL, concat(argMax(claude_result, night), ' (', toString(max(night)), ')')) AS s FROM n360.ops_night FINAL
        WHERE claude_result != 'no run'""", 6, 9, 6, 3,
        'Result of the newest Claude nightly run, read from that night\'s RUN_REPORT.md and PUBLISHED.json.', STATUS_MAP))
    p.append(stat(6, 'Code pull, last result', """
        SELECT if(count() = 0, NULL, concat(argMax(code_result, night), ' (', toString(max(night)), ')')) AS s FROM n360.ops_night FINAL
        WHERE code_result NOT IN ('not run', 'export only')""", 12, 9, 6, 3,
        'Result of the newest run of run_nightly.py (the nightly as code), from ORCH_REPORT.md / ORCH_DONE.json.', STATUS_MAP))
    p.append(stat(7, 'Last load', """
        SELECT concat(toString(argMax(status, loaded_at)), ' · ', toString(argMax(data_date, loaded_at))) AS s FROM n360.runs""",
        18, 9, 6, 3, 'The newest load.py attempt: loaded, or refused by a data check. Source: n360.runs.', STATUS_MAP))
    p.append(table(8, 'Nights: what ran', """
        SELECT n.night AS Night, n.claude_result AS `Claude nightly`, n.code_result AS `Code pull`,
               if(n.export_run_id = '', '', n.export_counts) AS `Export`,
               multiIf(n.export_run_id = '', '', r.status = 'loaded', 'loaded', r.status = 'failed_checks',
                       concat('refused by a check: ', arrayStringConcat(r.checks_failed, ', ')), 'not loaded yet') AS `In ClickHouse`
        FROM (SELECT * FROM n360.ops_night FINAL) AS n
        LEFT JOIN (SELECT toString(run_id) AS rid, toString(argMax(status, loaded_at)) AS status, argMax(checks_failed, loaded_at) AS checks_failed
                   FROM n360.runs GROUP BY rid) AS r ON r.rid = n.export_run_id
        ORDER BY n.night DESC LIMIT 30""", 0, 12, 24, 12,
        'One row per pipeline night, newest first. Claude nightly: published (version) or why it stopped. Code pull: run_nightly.py. '
        'Export: row counts written. In ClickHouse: whether that export loaded. A missing night = no run that day.',
        colored=('Claude nightly', 'Code pull', 'In ClickHouse')))
    p.append(table(9, 'Runner jobs', """
        SELECT job_id, started AS `Started (UTC)`, action AS Action, state AS State,
               if(finished IS NULL, '', concat(toString(dateDiff('minute', started, finished)), ' min')) AS Took,
               exit_code AS `Exit`, last_line AS `Last line`
        FROM n360.ops_job FINAL ORDER BY started DESC LIMIT 20""", 0, 24, 24, 9,
        'Every job the runner started. Click a job id for its full log (runner page, new tab).', colored=('State',),
        links=('job_id', RUNNER + '/job/${__value.raw}', 'Open the log')))
    p.append(table(10, 'ClickHouse loads', """
        SELECT loaded_at AS `Loaded (UTC)`, data_date AS Night, toString(status) AS Status,
               arrayStringConcat(checks_failed, ', ') AS `Checks failed`, toString(run_id) AS `Run id`
        FROM n360.runs ORDER BY loaded_at DESC LIMIT 20""", 0, 33, 24, 8,
        'Every load.py attempt (stage, 8 checks, swap). Source: n360.runs.', colored=('Status',)))
    return {'uid': 'n360-ops', 'title': 'N360 · Operations', 'tags': ['n360'], 'editable': False, 'schemaVersion': 39,
            'version': 1, 'timezone': 'utc', 'refresh': '30s', 'time': {'from': 'now-30d', 'to': 'now'},
            'links': [{'type': 'link', 'title': 'Back to Overview', 'url': '/d/n360-overview', 'icon': 'dashboard', 'keepTime': True,
                       'includeVars': False, 'targetBlank': False, 'asDropdown': False, 'tags': []},
                      {'type': 'link', 'title': 'Pipeline health', 'url': '/d/n360-pipeline', 'icon': 'dashboard', 'keepTime': True,
                       'includeVars': False, 'targetBlank': False, 'asDropdown': False, 'tags': []}],
            'templating': {'list': []}, 'panels': p}


if __name__ == '__main__':
    out = os.path.join(HERE, 'dashboards', 'n360-ops.json')
    json.dump(dashboard(), open(out, 'w'), indent=2)
    print('wrote', os.path.relpath(out, os.path.dirname(os.path.dirname(HERE))))
