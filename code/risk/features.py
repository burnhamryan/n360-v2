"""Risk-model features from N360's ClickHouse, as they stood on a date (point-in-time).

Every feature is computed from the daily facts up to that date only, so a feature row for 1 September uses nothing
recorded after 1 September. That makes a backtest on N360's own history honest. The exceptions are flagged `pit:
false` in risk_model.json (current state only: the Salesforce forecast category and open tickets, which keep no
history). They are filled only for the newest date, and the model never trains on them.

Each feature is one small SQL query returning (account, value) for the date; build() joins them per customer. A
customer a source knows nothing about gets None ("not reported"), never 0.
"""
import datetime as dt

DAY = "toDate('{d}')"


def _q(sql, d, newest=False):
    """{D} = the date; {CUT} = the date, or today for the newest date (CSM ratings and register entries written after
    the last data day still count for today's score)."""
    cut = dt.date.today().isoformat() if newest else d
    return sql.replace('{D}', DAY.format(d=d)).replace('{CUT}', DAY.format(d=cut))


# ───────────────────────── commercial ─────────────────────────
# Entitlements: every measurable line not in a pool, and each pool once (its lines' daily series added up).
ENT = """
    WITH ent AS (
        SELECT l.series_key AS sk, l.account AS account,
               if(l.pool_id IS NULL, l.line_key, concat('pool:', toString(l.pool_id))) AS ek,
               if(l.pool_id IS NULL, l.billed, toNullable(p.billed)) AS billed,
               if(l.pool_id IS NULL, l.start_date, p.start_date) AS st, if(l.pool_id IS NULL, l.end_date, p.end_date) AS en
        FROM n360.contract_line AS l
        LEFT JOIN n360.pool AS p ON p.account = l.account AND p.pool_id = ifNull(l.pool_id, 255)
        WHERE l.measure NOT IN ('not measurable', 'not implemented')),
         use AS (
        SELECT ent.account AS account, ent.ek AS ek, any(ent.billed) AS b, any(ent.st) AS st, any(ent.en) AS en,
               sumIf(ifNull(d.measured, 0), d.day <= {D} AND d.day >= ent.st) AS used,
               sumIf(ifNull(d.measured, 0), d.day > {D} - 30 AND d.day <= {D}) AS l30
        FROM ent INNER JOIN n360.line_day AS d ON d.series_key = ent.sk AND d.kind = 'line'   -- a line with no series has no reading
        GROUP BY ent.account, ent.ek)
"""
PACE = ENT + """
    SELECT account,
           max(100 * least(greatest(dateDiff('day', st, {D}) + 1, 0), dateDiff('day', st, en) + 1) / (dateDiff('day', st, en) + 1)
               - 100 * used / b) AS pace_gap_pts,
           min(100 * (used + l30 / 30 * greatest(dateDiff('day', {D}, en), 0)) / b) AS forecast_end_pct
    FROM use WHERE b > 0 AND st <= {D} AND en >= {D}
    GROUP BY account"""

RENEWAL = """
    SELECT account, min(dateDiff('day', {D}, end_date)) AS days_to_renewal, count() AS billed_lines
    FROM n360.contract_line WHERE end_date >= {D} AND start_date <= {D} GROUP BY account"""

# ───────────────────────── usage and quality ─────────────────────────
USAGE = """
    SELECT account,
           if(countIf(day > {D} - 60 AND day <= {D} AND conversations IS NULL) > 0 OR sumIf(conversations, day > {D} - 60 AND day <= {D} - 30) = 0,
              NULL, 100 * (sumIf(conversations, day > {D} - 30 AND day <= {D}) - sumIf(conversations, day > {D} - 60 AND day <= {D} - 30))
                    / sumIf(conversations, day > {D} - 60 AND day <= {D} - 30)) AS conv_change_30_pct,
           if(countIf(day > {D} - 180 AND day <= {D} AND conversations IS NULL) > 0 OR sumIf(conversations, day > {D} - 180 AND day <= {D} - 90) = 0
              OR uniqExactIf(day, day > {D} - 180 AND day <= {D} - 90) < 85,
              NULL, 100 * (sumIf(conversations, day > {D} - 90 AND day <= {D}) - sumIf(conversations, day > {D} - 180 AND day <= {D} - 90))
                    / sumIf(conversations, day > {D} - 180 AND day <= {D} - 90)) AS conv_change_90_pct
    FROM n360.va_day WHERE day > {D} - 180 AND day <= {D} GROUP BY account"""

VOLATILITY = """
    SELECT account, if(count() < 8 OR avg(w) = 0, NULL, stddevPop(w) / avg(w)) AS conv_volatility FROM (
        SELECT account, toMonday(day) AS wk, sum(conversations) AS w FROM n360.va_day
        WHERE toMonday(day) > {D} - 84 AND toMonday(day) + 6 <= {D} GROUP BY account, wk)
    GROUP BY account"""

FLAGS = """
    SELECT account, countIf(kind = 'went silent') AS silent_va_days_30, countIf(kind = 'drop') AS drop_days_30
    FROM n360.v_va_day_anomaly WHERE day > {D} - 30 AND day <= {D} GROUP BY account"""

CONTAINMENT = """
    SELECT account, argMax(c30, e30) AS containment_pct, argMax(c30 - cp30, e30) AS containment_change_pts FROM (
        SELECT account, bot_id,
               sumIf(engaged, day > {D} - 30 AND day <= {D}) AS e30,
               100 * (1 - sumIf(handoffs, day > {D} - 30 AND day <= {D}) / nullIf(e30, 0)) AS c30,
               100 * (1 - sumIf(handoffs, day > {D} - 60 AND day <= {D} - 30)
                          / nullIf(sumIf(engaged, day > {D} - 60 AND day <= {D} - 30), 0)) AS cp30
        FROM n360.va_day WHERE handoffs IS NOT NULL AND day > {D} - 60 AND day <= {D} GROUP BY account, bot_id)
    WHERE e30 > 0 GROUP BY account"""

# ───────────────────────── adoption (Phase 2 tables) ─────────────────────────
LEGACY = """
    WITH cfg AS (SELECT bot_id, argMax(engine_major, snapshot_date) AS major FROM n360.va_config_snapshot
                 WHERE snapshot_date <= {D} GROUP BY bot_id)
    SELECT d.account AS account, sumIf(d.conversations, c.major < (SELECT engine_current_major FROM n360.settings))
           / nullIf(sum(d.conversations), 0) AS legacy_va_share
    FROM n360.va_day AS d INNER JOIN cfg AS c ON c.bot_id = d.bot_id
    WHERE d.day > {D} - 30 AND d.day <= {D} GROUP BY account"""

FEATURES_USED = """
    SELECT account, toFloat64(sum(u)) AS features_used FROM (
        SELECT account, toUInt8(sum(executions) > 0) AS u FROM n360.playbook_day WHERE day > {D} - 30 AND day <= {D} GROUP BY account
        UNION ALL SELECT account, toUInt8(sum(authoring_sessions) > 0) FROM n360.cortex_day
            WHERE user_type = 'customer' AND day > {D} - 30 AND day <= {D} GROUP BY account
        UNION ALL SELECT account, toUInt8(sum(questions) > 0) FROM n360.explorer_day
            WHERE user_type = 'customer' AND day > {D} - 30 AND day <= {D} GROUP BY account
        UNION ALL SELECT account, toUInt8(sum(mcp) > 0) FROM (SELECT account, bot_id, argMax(mcp_servers_live, snapshot_date) AS mcp
            FROM n360.va_config_snapshot WHERE snapshot_date <= {D} GROUP BY account, bot_id) GROUP BY account
        UNION ALL SELECT account, toUInt8(sum(sup) > 0) FROM (SELECT account, bot_id, argMax(supervisory_rules_on, snapshot_date) AS sup
            FROM n360.va_config_snapshot WHERE snapshot_date <= {D} GROUP BY account, bot_id) GROUP BY account)
    GROUP BY account"""

BUILD = """
    SELECT account, if(sumIf(n, day > {D} - 60 AND day <= {D} - 30) = 0, NULL,
                       100 * (sumIf(n, day > {D} - 30) - sumIf(n, day > {D} - 60 AND day <= {D} - 30))
                       / sumIf(n, day > {D} - 60 AND day <= {D} - 30)) AS build_change_pct FROM (
        SELECT account, day, ifNull(authoring_sessions, 0) AS n FROM n360.cortex_day
            WHERE user_type = 'customer' AND day > {D} - 60 AND day <= {D}
        UNION ALL SELECT account, day, ifNull(questions, 0) FROM n360.explorer_day
            WHERE user_type = 'customer' AND day > {D} - 60 AND day <= {D})
    GROUP BY account"""

CONFIG_AGE = """
    SELECT account, dateDiff('week', max(if(changed, snapshot_date, first_read)), {D}) AS weeks_config_unchanged FROM (
        SELECT account, bot_id, snapshot_date, min(snapshot_date) OVER (PARTITION BY bot_id) AS first_read,
               (engine_version, v8_playbooks, legacy_intents, mcp_servers_live, supervisory_rules_on)
                 != lagInFrame((engine_version, v8_playbooks, legacy_intents, mcp_servers_live, supervisory_rules_on))
                    OVER (PARTITION BY bot_id ORDER BY snapshot_date ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
                 AND row_number() OVER (PARTITION BY bot_id ORDER BY snapshot_date) > 1 AS changed
        FROM n360.va_config_snapshot WHERE snapshot_date <= {D})
    GROUP BY account"""

# ───────────────────────── relationship (Salesforce) ─────────────────────────
CONTACT = """
    SELECT s.account AS account,
           if(countIf(a.is_contact AND a.activity_date <= {D}) = 0, 365,
              least(365, dateDiff('day', maxIf(a.activity_date, a.is_contact AND a.activity_date <= {D}), {D}))) AS days_since_contact,
           if(countIf(a.is_executive AND a.activity_date <= {D}) = 0, 365,
              least(365, dateDiff('day', maxIf(a.activity_date, a.is_executive AND a.activity_date <= {D}), {D}))) AS days_since_exec
    FROM n360.v_sf_account AS s LEFT JOIN n360.sf_activity AS a ON a.sf_account_id = s.sf_account_id
    WHERE s.account != '' GROUP BY s.account"""

# current state only (pit: false): the renewal opportunity as Salesforce holds it now
RENEWAL_FORECAST = """
    WITH (SELECT risk_safe_forecast_categories FROM n360.settings) AS safe,
         (SELECT risk_lost_forecast_categories FROM n360.settings) AS lost,
         (SELECT risk_renewal_lost_days FROM n360.settings) AS lost_days
    SELECT s.account AS account,
           multiIf(countIf(o.is_closed AND NOT o.is_won AND o.close_date >= {D} - lost_days) > 0, 1.0,
                   countIf(NOT o.is_closed AND o.close_date >= {D} - 30) = 0, NULL,
                   has(lost, argMinIf(ifNull(o.forecast_category, ''), o.close_date, NOT o.is_closed AND o.close_date >= {D} - 30)), 1.0,
                   has(safe, argMinIf(ifNull(o.forecast_category, ''), o.close_date, NOT o.is_closed AND o.close_date >= {D} - 30)), 0.0,
                   0.5) AS renewal_forecast_risk,
           sumIf(ifNull(o.amount, 0), NOT o.is_closed AND o.close_date >= {D}) AS renewal_amount
    FROM n360.v_sf_account AS s INNER JOIN n360.sf_opportunity AS o ON o.sf_account_id = s.sf_account_id
    WHERE s.account != '' AND o.is_renewal GROUP BY s.account"""

# ───────────────────────── support (current state only) and the CSM's view ─────────────────────────
TICKETS = """
    SELECT account, toFloat64(countIf(status = 'open' AND opened <= {D})) AS open_tickets,
           if(countIf(status = 'open' AND opened <= {D}) = 0, 0, dateDiff('day', minIf(opened, status = 'open' AND opened <= {D}), {D}))
               AS oldest_open_ticket_days
    FROM n360.ps_ticket GROUP BY account"""

PULSE = """
    WITH (SELECT risk_pulse_valid_days FROM n360.settings) AS valid
    SELECT account, multiIf(lv = 'red', 1.0, lv = 'amber', 0.5, 0.0) AS csm_pulse FROM (
        SELECT account, toString(argMax(level, at)) AS lv, max(at) AS last FROM n360.risk_pulse
        WHERE toDate(at) <= {CUT} GROUP BY account)
    WHERE dateDiff('day', toDate(last), {CUT}) <= valid"""

REGISTER = """
    SELECT account, toFloat64(countIf(st = 'open' AND imp = 'churn')) AS open_churn_risks FROM (
        SELECT risk_id, argMax(account, at) AS account, toString(argMax(status, at)) AS st, toString(argMax(impact, at)) AS imp
        FROM n360.risk_register WHERE toDate(at) <= {CUT} GROUP BY risk_id)
    GROUP BY account"""

DATA_GAPS = """
    SELECT account, toFloat64(countIf(status IN ('billed not recorded', 'unmapped', 'under review', 'not read'))) AS data_gaps
    FROM n360.v_contract_status GROUP BY account"""

# (sql, columns, only for the newest date?)
QUERIES = [(PACE, ['pace_gap_pts', 'forecast_end_pct'], False), (RENEWAL, ['days_to_renewal', 'billed_lines'], False),
           (USAGE, ['conv_change_30_pct', 'conv_change_90_pct'], False), (VOLATILITY, ['conv_volatility'], False),
           (FLAGS, ['silent_va_days_30', 'drop_days_30'], False),
           (CONTAINMENT, ['containment_pct', 'containment_change_pts'], False), (LEGACY, ['legacy_va_share'], False),
           (FEATURES_USED, ['features_used'], False), (BUILD, ['build_change_pct'], False),
           (CONFIG_AGE, ['weeks_config_unchanged'], False), (CONTACT, ['days_since_contact', 'days_since_exec'], False),
           (PULSE, ['csm_pulse'], False), (REGISTER, ['open_churn_risks'], False),
           (RENEWAL_FORECAST, ['renewal_forecast_risk', 'renewal_amount'], True), (TICKETS, ['open_tickets', 'oldest_open_ticket_days'], True),
           (DATA_GAPS, ['data_gaps'], True)]

# zero, not "not reported", when the source is loaded but has no row for a customer (nothing flagged, no ticket...)
ZERO_WHEN_LOADED = {'silent_va_days_30': 'va_day', 'drop_days_30': 'va_day', 'open_churn_risks': None,
                    'open_tickets': 'ps_ticket', 'oldest_open_ticket_days': 'ps_ticket'}


def latest_day(ch):
    r = ch.rows("SELECT toString(toDate(max(window_end))) AS d FROM n360.runs WHERE status = 'loaded'")
    return r[0]['d'] if r and r[0]['d'] not in (None, '1970-01-01') else None


def build(ch, as_of, newest=None):
    """Feature rows for every customer on `as_of` (YYYY-MM-DD). Current-only features are filled only when as_of is
    the newest data day."""
    newest = newest or latest_day(ch)
    is_newest = as_of == newest
    accounts = [r['account'] for r in ch.rows('SELECT account FROM n360.account ORDER BY account')]
    rows = {a: {'account': a, 'as_of': as_of} for a in accounts}
    loaded = {t: int(ch.rows(f'SELECT count() AS n FROM n360.{t}')[0]['n']) > 0 for t in ('va_day', 'ps_ticket')}
    for sql, cols, current_only in QUERIES:
        for c in cols:
            for r in rows.values():
                r.setdefault(c, None)
        if current_only and not is_newest:
            continue
        for x in ch.rows(_q(sql, as_of, is_newest)):
            if x['account'] in rows:
                for c in cols:
                    v = x.get(c)
                    rows[x['account']][c] = None if v is None else float(v)
    for c, table in ZERO_WHEN_LOADED.items():
        if c in ('open_tickets', 'oldest_open_ticket_days') and not is_newest:
            continue
        if table is None or loaded.get(table):
            for r in rows.values():
                if r[c] is None and (c != 'silent_va_days_30' and c != 'drop_days_30' or r.get('conv_change_30_pct') is not None
                                     or r.get('containment_pct') is not None):
                    r[c] = 0.0
    return list(rows.values())


def dates(start, end, every):
    d, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    out = []
    while d <= e:
        out.append(d.isoformat()); d += dt.timedelta(every)
    if out[-1] != e.isoformat():
        out.append(e.isoformat())
    return out
