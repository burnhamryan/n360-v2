-- Data checks, run by load.py against the staging database ({db}) before the swap.
-- Each check returns the offending rows. Zero rows = pass. Any row = the load is refused and
-- the live tables keep the previous night. {last_day} is the run's last data day.

-- check: crosswalk_one_line_per_va
-- A VA on two contract lines would double count when lines roll up from VAs.
SELECT bot_id, groupArray(line_key) AS lines
FROM {db}.line_va GROUP BY bot_id HAVING count() > 1;

-- check: line_key_unique
SELECT line_key, count() AS n FROM {db}.contract_line GROUP BY line_key HAVING n > 1;

-- check: line_measured_reconciles
-- Daily measured values per line sum to the page model's total for that line.
SELECT l.series_key, l.measure, l.reported_measured AS reported, d.m AS daily_sum
FROM {db}.contract_line AS l
INNER JOIN (SELECT series_key, sum(measured) AS m FROM {db}.line_day WHERE kind = 'line' GROUP BY series_key) AS d
    ON d.series_key = l.series_key
WHERE l.pool_id IS NULL AND l.reported_measured IS NOT NULL AND d.m != l.reported_measured;

-- check: pct_matches_page
-- The view's % of billed equals the page's, to the hundredth. One formula, two places, no drift.
-- Compared with the EXACT ratio, not the view's rounded one: the page rounds in Python, the view in ClickHouse, and on
-- a ratio ending in exactly 5 (107.215) the two round opposite ways, 0.01 apart, both correct. A correctly rounded
-- page value is never more than 0.005 from the exact ratio; real drift is. (Fixed 24 Nov 2026: 13 of 32 real nights
-- were refused by the old `rounded - rounded > 0.01` test, a float artefact; no data was wrong.)
SELECT l.line_key, l.reported_pct_page AS page, v.pct_billed AS view
FROM {db}.contract_line AS l
INNER JOIN (
    SELECT line_key, if(pool_id IS NULL AND billed > 0 AND reported_measured IS NOT NULL,
                        100 * reported_measured / billed, NULL) AS pct_billed
    FROM {db}.contract_line) AS v ON v.line_key = l.line_key
WHERE l.pool_id IS NULL AND l.reported_pct_page IS NOT NULL AND abs(ifNull(v.pct_billed, -1) - l.reported_pct_page) > 0.0051;

-- check: billable_reconciles
-- Rule lines: daily billable sums to consumption_rules.json consumed_billable.
SELECT l.line_key, l.reported_billable AS reported, b.s AS daily_sum
FROM {db}.contract_line AS l
LEFT JOIN (SELECT line_key, sum(billable) AS s FROM {db}.line_day_billable GROUP BY line_key) AS b
    ON b.line_key = l.line_key
WHERE l.reported_billable IS NOT NULL AND ifNull(b.s, 0) != l.reported_billable;

-- check: facts_end_on_last_day
-- Every daily fact ends exactly on the run's last data day (no future days, no stale table).
SELECT 'va_day' AS t, max(day) AS last FROM {db}.va_day HAVING last != toDate('{last_day}')
UNION ALL
SELECT 'line_day', max(day) FROM {db}.line_day HAVING max(day) != toDate('{last_day}');

-- check: crosswalk_vas_known
-- Every VA on a contract line has a reference row.
SELECT lv.line_key, lv.bot_id
FROM {db}.line_va AS lv LEFT ANTI JOIN {db}.va AS v ON v.bot_id = lv.bot_id;

-- check: facts_not_empty
SELECT t FROM (
    SELECT 'va_day' AS t, count() AS n FROM {db}.va_day
    UNION ALL SELECT 'line_day', count() FROM {db}.line_day
    UNION ALL SELECT 'contract_line', count() FROM {db}.contract_line
) WHERE n = 0;
