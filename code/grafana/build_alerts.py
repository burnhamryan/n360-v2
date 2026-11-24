#!/usr/bin/env python3
"""Generate the N360 Grafana alert rules, contact points and notification policy (backlog B18).
Alerts are code, like the dashboards: edit this file, run it, commit the output. Grafana provisions
grafana/provisioning/alerting/*.json at start-up (JSON is accepted as YAML).

    python3 code/grafana/build_alerts.py

Rules follow docs/05_ANOMALY_AND_FORECAST.md "Alerting". Each rule is one SQL query against a view that already
decides the condition (thresholds live in n360.settings), returning one row per thing to alert on, with string
columns as labels and a numeric `value`. A rule fires for each row whose value is above 0. No rows = nothing to
report (normal). Provisional anomaly days never fire: every anomaly rule reads settled days only.

Contact points are PLACEHOLDERS (example.invalid addresses): who receives what is an open company decision.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'provisioning', 'alerting')
DS = 'n360-clickhouse'
FOLDER = 'N360'

NEWEST_SETTLED = "(SELECT max(day) FROM n360.v_va_day_anomaly WHERE NOT provisional)"
NEWEST_NIGHT = "(SELECT max(data_date) FROM n360.forecast_history)"

# Who and when (05 "Alerting"). Addresses are placeholders until the company names recipients.
AUDIENCE = {
    'owner': ('N360 day-to-day owner (placeholder)', 'n360-owner@example.invalid'),
    'cs': ('CS leadership and CSM (placeholder)', 'n360-cs@example.invalid'),
}

RULES = [
    dict(uid='n360-load-failed', title='Load failed', audience='owner', cadence='immediately', severity='critical',
         summary='The latest nightly run was stopped by a data check; dashboards still show the previous good night.',
         sql="""SELECT toUInt8(argMax(toString(status), (loaded_at, data_date)) = 'failed_checks') AS value FROM n360.runs""",
         no_data='OK'),
    dict(uid='n360-data-stale', title='Data stale', audience='owner', cadence='daily', severity='warning',
         summary='No loaded run with data through yesterday, checked from 10:00 ET.',
         sql="""SELECT toUInt8(dateDiff('day', toDate(max(window_end)), toDate(now('America/New_York'))) > 1
                              AND toHour(now('America/New_York')) >= 10) AS value
                FROM n360.runs WHERE status = 'loaded'""",
         no_data='Alerting'),
    dict(uid='n360-source-stale', title='Source stale', audience='owner', cadence='daily', severity='warning',
         summary='A source is 2 or more days behind the data-through day (thresholds in n360.settings).',
         sql="""SELECT source_id AS source, toString(status) AS status, toUInt8(status != 'green') AS value
                FROM n360.v_source_freshness""",
         no_data='OK'),
    dict(uid='n360-va-silent-dropped', title='VA went silent or dropped', audience='owner', cadence='daily',
         severity='warning',
         summary='A VA went silent or dropped on the newest settled day (provisional days never alert). Pass to the '
                 'account CSM after a look.',
         sql=f"""SELECT concat(v.va_name, ' (', arrayStringConcat(v.channels, ', '), ')') AS va, ac.display_name AS customer,
                        a.kind AS kind, toUInt8(1) AS value
                 FROM n360.v_va_day_anomaly AS a INNER JOIN n360.va AS v ON v.bot_id = a.bot_id
                 INNER JOIN n360.account AS ac ON ac.account = a.account
                 WHERE a.kind IN ('drop', 'went silent') AND NOT a.provisional AND a.day = {NEWEST_SETTLED}""",
         no_data='OK'),
    dict(uid='n360-cross-account', title='Cross-account event', audience='owner', cadence='daily', severity='info',
         summary='Several customers were flagged in the same direction on the newest settled day: one event.',
         sql=f"""SELECT toString(day) AS event_day, direction, toUInt32(accounts) AS value
                 FROM n360.v_anomaly_events WHERE day = {NEWEST_SETTLED}""",
         no_data='OK'),
    dict(uid='n360-over-billed', title='Newly over billed', audience='cs', cadence='weekly', severity='warning',
         summary='A line or pool went above 100% of billed since the night before.',
         sql=f"""SELECT series_key AS line, account AS customer, toUInt8(1) AS value
                 FROM n360.v_forecast_change WHERE data_date = {NEWEST_NIGHT} AND newly_over_billed""",
         no_data='OK'),
    dict(uid='n360-crossing-soon', title='Crossing billed soon', audience='cs', cadence='weekly', severity='warning',
         summary='A line now crosses billed within the red window (n360.settings), or its crossing date moved more '
                 'than the set number of days earlier overnight.',
         sql=f"""SELECT series_key AS line, account AS customer,
                        if(newly_crossing_soon, 'newly crossing soon', 'moved earlier') AS reason, toUInt8(1) AS value
                 FROM n360.v_forecast_change
                 WHERE data_date = {NEWEST_NIGHT} AND (newly_crossing_soon OR moved_earlier)""",
         no_data='OK'),
]


def rule(r):
    return {
        'uid': r['uid'], 'title': r['title'], 'condition': 'C',
        'data': [
            {'refId': 'A', 'relativeTimeRange': {'from': 600, 'to': 0}, 'datasourceUid': DS,
             'model': {'refId': 'A', 'editorType': 'sql', 'format': 1, 'queryType': 'table',
                       'rawSql': ' '.join(r['sql'].split()), 'intervalMs': 1000, 'maxDataPoints': 43200}},
            {'refId': 'C', 'datasourceUid': '__expr__',
             'model': {'refId': 'C', 'type': 'threshold', 'expression': 'A',
                       'conditions': [{'evaluator': {'type': 'gt', 'params': [0]}}]}},
        ],
        'noDataState': r['no_data'], 'execErrState': 'Error', 'for': '0s', 'isPaused': False,
        'annotations': {'summary': r['summary'],
                        'description': 'Rule source: code/grafana/build_alerts.py. Data: N360 ClickHouse views.'},
        'labels': {'team': 'n360', 'audience': r['audience'], 'cadence': r['cadence'], 'severity': r['severity']},
    }


def rules_file():
    return {'apiVersion': 1, 'groups': [{'orgId': 1, 'name': 'n360', 'folder': FOLDER, 'interval': '5m',
                                         'rules': [rule(r) for r in RULES]}]}


def notify_file():
    """Placeholder contact points; routes by audience and cadence (05). Clock times (09:00 ET daily, Monday
    digest) are approximated by repeat intervals; exact delivery windows are for the company to set."""
    cps = [{'orgId': 1, 'name': name, 'receivers': [{'uid': f'n360-{key}', 'type': 'email', 'disableResolveMessage': False,
                                                     'settings': {'addresses': addr, 'singleEmail': True}}]}
           for key, (name, addr) in AUDIENCE.items()]
    repeat = {'immediately': '4h', 'daily': '24h', 'weekly': '168h'}
    routes = [{'receiver': AUDIENCE[a][0], 'group_by': ['alertname'], 'repeat_interval': repeat[c], 'group_wait': '30s',
               'object_matchers': [['team', '=', 'n360'], ['audience', '=', a], ['cadence', '=', c]]}
              for a in AUDIENCE for c in repeat]
    return {'apiVersion': 1, 'contactPoints': cps,
            'policies': [{'orgId': 1, 'receiver': AUDIENCE['owner'][0], 'group_by': ['alertname'], 'routes': routes}]}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, body in (('n360-alert-rules.json', rules_file()), ('n360-notifications.json', notify_file())):
        path = os.path.join(OUT, name)
        with open(path, 'w') as f:
            json.dump(body, f, indent=2)
        print('wrote', os.path.relpath(path, os.path.dirname(HERE)))


if __name__ == '__main__':
    main()
