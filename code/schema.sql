-- N360 ClickHouse schema (POC, 22 Nov 2026).
-- Time-series layout: one row per entity per day, low-cardinality dimensions first in the
-- sort key, the day last, one partition per month. Elasticsearch equivalents in comments.
--
-- Conventions
--   NULL  = not read (source missing for that entity/day). 0 = read, and the count was zero.
--           "Empty is never a gap" is enforced by the type, not by a renderer.
--   Every row carries run_id; n360.runs holds capture time, window end and code fingerprint.
--   Percentages and ratios are NOT stored: they are computed in the v_* views (one definition).
--   Pipeline outputs that need Python logic (rules, forecasts) ARE stored, never re-derived here.
--   Tables are reloaded whole each run into <name>_next, checked, then swapped with
--   EXCHANGE TABLES (ES: write a new index, then flip the alias).

CREATE DATABASE IF NOT EXISTS n360;

-- ───────────────────────── reference tables (ES: small lookup indices) ─────────────────────────

CREATE TABLE IF NOT EXISTS n360.account
(
    account                LowCardinality(String),        -- Rocketlane name; the stable key
    display_name           LowCardinality(String),        -- customer_overrides.json display_names
    rocketlane_account_id  UInt32,
    csm_rocketlane         LowCardinality(String),
    run_id                 UUID
)
ENGINE = MergeTree ORDER BY account;

CREATE TABLE IF NOT EXISTS n360.va
(
    bot_id          UUID,                                  -- configuration bot id
    live_ref        Nullable(UUID),                        -- runtime botReferenceId; NULL = read only by a contract rule
    consumption_source Enum8('aggregate' = 1, 'conversation_records' = 2, 'contract_rule' = 3), -- which reader produced the count
    account         LowCardinality(String),
    va_name         LowCardinality(String),
    region          Enum8('US' = 1, 'EU' = 2, 'SG' = 3),   -- fixed set: anything new should fail loudly
    channels        Array(LowCardinality(String)),
    line_labels     Array(String),
    contract_start  Nullable(Date),
    on_contract     Bool,                                  -- false = unbilled VA (no contract line)
    run_id          UUID
)
ENGINE = MergeTree ORDER BY (account, bot_id);

CREATE TABLE IF NOT EXISTS n360.contract_line
(
    line_key                  String,                      -- pipeline key: '<sheet customer>|<channel>|<start>', unique
    series_key                String,                      -- '<account>|<idx>' (pipeline key)
    account                   LowCardinality(String),
    idx                       UInt8,
    sheet_customer            String,
    channel                   LowCardinality(String),
    start_date                Date,
    end_date                  Date,
    billed                    Nullable(UInt64),            -- NULL = blank or carried by a pool
    billed_as_stored          String,
    csm_sheet                 LowCardinality(String),
    segment                   LowCardinality(String),
    pool_id                   Nullable(UInt8),
    rule_id                   Nullable(String),
    term_days                 UInt16,
    sheet_billed              Nullable(UInt64),            -- what the entitlement sheet says, before SOW terms
    unit                      String,                      -- contract unit when a signed SOW overrides the sheet
    measure                   LowCardinality(String),      -- 'engaged' | 'billable (contract rule)' | 'conversations (contract rule)'
                                                           -- | 'not measurable' | 'not implemented'
    rule_kind                 LowCardinality(Nullable(String)),
    not_implemented           Bool,
    unmeasured                Bool,
    -- totals as the page's model (n360.data.Run) computed them: kept for display and reconciliation
    reported_measured         Nullable(UInt64),            -- NULL = not measured / not implemented / not read
    reported_last30_measured  Nullable(UInt64),
    reported_engaged_flag     Nullable(UInt64),            -- platform engaged flag, always kept beside the measure
    reported_consumed_convs   Nullable(UInt64),
    reported_billable         Nullable(UInt64),            -- meaningful-engagement rule lines (consumption_rules.json)
    reported_pct_page         Nullable(Float64),           -- the page's % of billed; the view must match it
    run_id                    UUID
)
ENGINE = MergeTree ORDER BY (account, idx);

CREATE TABLE IF NOT EXISTS n360.line_va                    -- the crosswalk: many-to-many in principle
(
    line_key       String,
    account        LowCardinality(String),
    bot_id         UUID,
    va_name        String,
    live_ref       Nullable(UUID),
    consumption_source Enum8('aggregate' = 1, 'conversation_records' = 2, 'contract_rule' = 3),
    region         LowCardinality(String),
    mapping_basis  String,
    run_id         UUID
)
ENGINE = MergeTree ORDER BY (account, line_key, bot_id);

CREATE TABLE IF NOT EXISTS n360.pool
(
    account       LowCardinality(String),
    pool_id       UInt8,
    name          String,
    billed        UInt64,
    billed_basis  String,
    start_date    Date,
    end_date      Date,
    line_keys     Array(String),
    run_id        UUID
)
ENGINE = MergeTree ORDER BY (account, pool_id);

CREATE TABLE IF NOT EXISTS n360.rule
(
    rule_id          LowCardinality(String),
    rule_hash        String,
    kind             LowCardinality(String),
    status           LowCardinality(String),
    label            String,
    contract_text    String,
    source_document  String,
    counts_plain     String,
    decisions        String,
    lines_measured   Array(String),                        -- empty = pending / blocked, not live
    run_id           UUID
)
ENGINE = MergeTree ORDER BY rule_id;

CREATE TABLE IF NOT EXISTS n360.source                     -- provenance text per metric family
(
    source_id    LowCardinality(String),                   -- 'consumption_days', 'containment_days', ...
    description  String,
    definition   String,
    check_text   String,
    last_day     Date,
    run_id       UUID
)
ENGINE = MergeTree ORDER BY source_id;

-- ───────────────────────── time-series facts (ES: data streams, one doc per entity-day) ─────────

CREATE TABLE IF NOT EXISTS n360.va_day
(
    day            Date CODEC(DoubleDelta, ZSTD),
    account        LowCardinality(String),
    bot_id         UUID,
    live_ref       UUID,
    consumption_source Enum8('aggregate' = 1, 'conversation_records' = 2, 'contract_rule' = 3),
    region         Enum8('US' = 1, 'EU' = 2, 'SG' = 3),
    conversations  Nullable(UInt32) CODEC(T64, ZSTD),
    engaged        Nullable(UInt32) CODEC(T64, ZSTD),
    handoffs       Nullable(UInt32) CODEC(T64, ZSTD),     -- NULL where containment is not read
    run_id         UUID
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(day)
ORDER BY (account, bot_id, day);

CREATE TABLE IF NOT EXISTS n360.line_day                   -- the pipeline's per-line daily series
(
    day            Date CODEC(DoubleDelta, ZSTD),
    account        LowCardinality(String),
    series_key     String,                                 -- line, pool component or unbilled VA
    kind           Enum8('line' = 1, 'component' = 2, 'unbilled' = 3),
    bot_id         Nullable(UUID),                         -- set for component / unbilled
    conversations  Nullable(UInt32) CODEC(T64, ZSTD),
    engaged        Nullable(UInt32) CODEC(T64, ZSTD),      -- platform engaged flag
    measured       Nullable(UInt32) CODEC(T64, ZSTD),      -- the contract's measure (= engaged when no rule)
    run_id         UUID
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(day)
ORDER BY (account, series_key, day);

CREATE TABLE IF NOT EXISTS n360.line_day_billable          -- contract-rule output (consumption_rules.py)
(
    day            Date CODEC(DoubleDelta, ZSTD),
    account        LowCardinality(String),
    line_key       String,
    rule_id        LowCardinality(String),
    rule_hash      String,
    conversations  Nullable(UInt32) CODEC(T64, ZSTD),
    engaged_flag   Nullable(UInt32) CODEC(T64, ZSTD),
    billable       Nullable(UInt32) CODEC(T64, ZSTD),
    run_id         UUID
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(day)
ORDER BY (account, line_key, day);

CREATE TABLE IF NOT EXISTS n360.forecast                   -- consumption_forecast_m3.json, per line or pool
(
    account           LowCardinality(String),
    series_key        String,
    pool_id           Nullable(UInt8),
    start_date        Date,
    end_date          Date,
    horizon           Date,
    rate_per_day      Float64,
    avg_per_day       Float64,
    forecast_m3       UInt64,
    billed            Nullable(UInt64),
    consumed_engaged  UInt64,
    elapsed_days      UInt16,
    cross_date        Nullable(Date),
    exhaust_date      Nullable(Date),
    flags             Array(LowCardinality(String)),
    run_id            UUID
)
ENGINE = MergeTree ORDER BY (account, series_key);

-- ───────────────────────── run log and history (append-only, never swapped) ─────────────────────

CREATE TABLE IF NOT EXISTS n360.runs
(
    run_id            UUID,
    data_date         Date,                                -- D: the pipeline folder
    window_end        DateTime('UTC'),
    capture_utc       DateTime('UTC'),
    loaded_at         DateTime('UTC') DEFAULT now(),
    source_dir        String,
    code_fingerprint  String,
    status            Enum8('loaded' = 1, 'failed_checks' = 2),
    checks_failed     Array(String),
    row_counts        Map(String, UInt64)
)
ENGINE = MergeTree ORDER BY loaded_at;

CREATE TABLE IF NOT EXISTS n360.line_totals_history        -- "what did N360 say on 14 Nov"
(
    data_date         Date,
    run_id            UUID,
    account           LowCardinality(String),
    line_key          String,
    billed            Nullable(UInt64),
    measure           LowCardinality(String),
    consumed_convs    Nullable(UInt64),
    consumed_measured Nullable(UInt64),
    engaged_flag      Nullable(UInt64),
    pct_billed_page   Nullable(Float64)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(data_date)
ORDER BY (account, line_key, data_date);

CREATE TABLE IF NOT EXISTS n360.forecast_history         -- B15: the forecast of record as it stood each loaded night
(                                                           -- (append-only; load.py copies n360.forecast after each swap)
    data_date      Date,                                    -- the night (data through = data_date - 1)
    run_id         UUID,
    account        LowCardinality(String),
    series_key     String,
    pool_id        Nullable(UInt8),
    horizon        Date,
    rate_per_day   Float64,
    forecast_m3    UInt64,
    billed         Nullable(UInt64),
    consumed       UInt64,                                  -- used to the data-through day, in the contract measure
    cross_date     Nullable(Date),
    exhaust_date   Nullable(Date)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(data_date)
ORDER BY (account, series_key, data_date);

CREATE TABLE IF NOT EXISTS n360.source_freshness         -- B17: each source's last day as it stood each loaded night
(                                                           -- (append-only; load.py copies n360.source after each swap)
    data_date     Date,
    source_id     LowCardinality(String),
    last_day      Date,
    days_behind   Int32                                     -- data-through day minus the source's last day
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(data_date)
ORDER BY (source_id, data_date);

CREATE TABLE IF NOT EXISTS n360.anomaly_log              -- B19: review outcome per flagged VA-day. Written only by
(                                                           -- code/review_anomaly.py; the nightly load never touches it
    bot_id        UUID,
    day           Date,
    kind          LowCardinality(String),                   -- the flag as it was when reviewed
    outcome       Enum8('real' = 1, 'expected' = 2, 'data issue' = 3),
    note          String,
    reviewed_by   LowCardinality(String),
    reviewed_at   DateTime64(3, 'UTC') DEFAULT now64(3)      -- milliseconds: two reviews in one second must not tie
)
ENGINE = MergeTree ORDER BY (bot_id, day, reviewed_at);

CREATE TABLE IF NOT EXISTS n360.forecast_challenger      -- B21: backtest predictions of each model; written only by
(                                                           -- code/forecast/challenger.py, never the forecast of record
    run_id         UUID,
    run_at         DateTime('UTC'),
    data_through   Date,
    model          LowCardinality(String),                  -- 'straight line' (the record's formula) | 'weekday HW'
    series_key     String,
    account        LowCardinality(String),
    cutoff         Date,                                    -- data up to this day only
    horizon_days   UInt16,
    predicted      Float64,                                 -- cumulative use at cutoff + horizon
    actual         Float64
)
ENGINE = MergeTree ORDER BY (run_at, series_key, model, cutoff, horizon_days);

-- ───────────────────────── views (one definition per ratio) ─────────────────────────────────────

CREATE OR REPLACE VIEW n360.settings AS
-- Every PROPOSED threshold (08_DECISIONS), in one place. Views read these; dashboards and alert rules never
-- hold a threshold. Change a value here and every view, panel and alert follows.
SELECT
    10  AS pace_amber_ahead_pts,     -- amber: % of billed more than this many points ahead of % of term
    100 AS pace_red_pct_billed,      -- red: above this % of billed
    60  AS pace_amber_cross_days,    -- amber: crossing billed within this many days
    30  AS pace_red_cross_days,      -- red (and "crossing soon" alerts): crossing billed within this many days
    4   AS anomaly_flag_z,           -- drop / spike at |z| >= this
    20  AS anomaly_min_expected,     -- only VAs whose same-weekday median is at least this are scored
    3   AS anomaly_provisional_days, -- days within this many of data-through are provisional
    2   AS anomaly_watch_z,          -- heatmap "watch" band
    2   AS freshness_amber_days,     -- source this many days behind: amber
    3   AS freshness_red_days,       -- ... red
    8   AS engine_current_major,     -- "V8 or later"
    2   AS asks_several_customers,   -- top asks: wanted by at least this many customers
    14  AS pace_change_days,         -- alert / anomaly v2: crossing date moved this many days earlier overnight
    10  AS containment_drop_pts,     -- anomaly v2: weekly containment this many points below its 8-week median
    200 AS containment_min_engaged,  -- ... on at least this many engaged that week
    20  AS measure_gap_pts;          -- anomaly v2: measure / engaged ratio this many points from its usual level


CREATE OR REPLACE VIEW n360.v_line_status AS
-- The measure is decided upstream by the page's model (contract rule, SOW terms); this view only divides.
-- Pooled lines carry no % of their own: the pool is measured once, in v_pool_status.
SELECT
    l.account, l.line_key, l.channel, l.start_date, l.end_date, l.pool_id, l.rule_id, l.measure, l.unit,
    l.reported_measured AS consumed, l.reported_engaged_flag AS engaged_flag,
    if(l.pool_id IS NULL, l.billed, NULL) AS billed,
    if(l.pool_id IS NULL AND l.billed > 0 AND consumed IS NOT NULL, round(100 * consumed / l.billed, 2), NULL) AS pct_billed,
    round(100 * least(greatest(dateDiff('day', l.start_date, toDate(r.window_end)) + 1, 0), l.term_days) / l.term_days, 1) AS pct_term_elapsed,
    r.window_end, r.capture_utc
FROM n360.contract_line AS l
CROSS JOIN (SELECT window_end, capture_utc FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC, data_date DESC LIMIT 1) AS r;

CREATE OR REPLACE VIEW n360.v_pool_status AS
SELECT p.account, p.pool_id, p.name, p.billed, sum(l.reported_measured) AS consumed,
       'engaged' AS measure, round(100 * consumed / p.billed, 1) AS pct_billed, p.start_date, p.end_date
FROM n360.pool AS p
INNER JOIN n360.contract_line AS l ON l.account = p.account AND l.pool_id = p.pool_id
GROUP BY p.account, p.pool_id, p.name, p.billed, p.start_date, p.end_date;

CREATE OR REPLACE VIEW n360.v_va_containment_day AS     -- chat-style containment; never across channels
SELECT day, account, bot_id, region, engaged, handoffs,
       if(engaged > 0 AND handoffs IS NOT NULL, 1 - handoffs / engaged, NULL) AS containment
FROM n360.va_day;

-- ───────────────────────── anomaly detection (POC, 22 Nov 2026) ─────────────────────────────────
-- Baseline per VA = the same weekday over the previous 8 weeks (weekly seasonality): median, and a robust spread
-- from the interquartile range (IQR / 1.349), floored at 10% of the median and at 5 conversations so quiet VAs do
-- not alert on noise. Only VAs whose baseline median is at least 20 a day are scored. A day is:
--   went silent  read, 0 conversations, baseline >= 20
--   drop / spike robust z <= -4 / >= 4
-- provisional = within 3 days of the data-through day: those days still settle (days-store drift), and most
-- recent-day drops are settling, not real. Alerts fire on settled days only. NULL (not read) is never scored.
CREATE OR REPLACE VIEW n360.v_va_day_anomaly AS
WITH (SELECT anomaly_flag_z FROM n360.settings) AS flag_z,
     (SELECT anomaly_min_expected FROM n360.settings) AS min_expected,
     (SELECT anomaly_provisional_days FROM n360.settings) AS provisional_days,
base AS (
    SELECT day, account, bot_id, conversations,
           quantilesExact(0.25, 0.5, 0.75)(conversations) OVER w AS q, count() OVER w AS n
    FROM n360.va_day
    WHERE conversations IS NOT NULL
    WINDOW w AS (PARTITION BY bot_id, toDayOfWeek(day) ORDER BY day ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING)
)
SELECT b.day, b.account, b.bot_id, v.va_name, b.conversations AS actual, b.q[2] AS expected,
       greatest((b.q[3] - b.q[1]) / 1.349, 0.1 * b.q[2], 5) AS spread,
       round((b.conversations - b.q[2]) / spread, 2) AS z,
       multiIf(b.conversations = 0, 'went silent', z <= -flag_z, 'drop', z >= flag_z, 'spike', 'normal') AS kind,
       greatest(expected - flag_z * spread, 0) AS expected_low, expected + flag_z * spread AS expected_high,
       dateDiff('day', b.day, (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded')) < provisional_days AS provisional
FROM base AS b
LEFT JOIN n360.va AS v ON v.bot_id = b.bot_id
WHERE b.n = 8 AND b.q[2] >= min_expected;

-- ───────────────────────── dashboard views (B5, 24 Nov 2026) ─────────────────────────────────────

CREATE OR REPLACE VIEW n360.v_line_pace AS
-- Pace per entitlement: every line not in a pool, and every pool once (its lines are never paced alone).
-- The colour is decided HERE and nowhere else (decided 24 Nov 2026). Dashboards only map `pace` to a display colour.
-- The four thresholds are PROPOSED (08_DECISIONS) and live in n360.settings:
--   red    above red_pct_billed % of billed, or crossing billed within red_cross_days
--   amber  % of billed more than amber_ahead_pts points ahead of % of term, or crossing within amber_cross_days
--   green  otherwise
--   not measurable / not implemented / not read: no colour (grey on screen), never red
-- Crossing date comes from the stored forecast (never recomputed). term_ended is reported, not coloured:
-- the proposed rule says nothing about ended terms.
WITH
    (SELECT pace_amber_ahead_pts FROM n360.settings) AS amber_ahead_pts,
    (SELECT pace_red_pct_billed FROM n360.settings) AS red_pct_billed,
    (SELECT pace_amber_cross_days FROM n360.settings) AS amber_cross_days,
    (SELECT pace_red_cross_days FROM n360.settings) AS red_cross_days,
    e AS (
        SELECT s.account AS account, 'line' AS entitlement, s.line_key AS line_key, CAST(NULL, 'Nullable(UInt8)') AS pool_id,
               l.series_key AS series_key,
               s.channel AS name,
               -- label for screens: the channel, plus the sheet's customer name where one account has several (Juniper)
               concat(s.channel, if(l.sheet_customer != s.account, concat(' (', l.sheet_customer, ')'), '')) AS label,
               s.measure AS measure, s.consumed AS used, s.billed AS billed, s.pct_billed AS pct_billed,
               s.pct_term_elapsed AS pct_term, s.start_date AS start_date, s.end_date AS end_date,
               toDate(s.window_end) AS data_through, f.cross_date AS cross_date, f.exhaust_date AS exhaust_date,
               f.rate_per_day AS rate_per_day, f.avg_per_day AS avg_per_day, f.forecast_m3 AS forecast_m3,
               f.horizon AS horizon, f.flags AS flags
        FROM n360.v_line_status AS s
        INNER JOIN n360.contract_line AS l ON l.line_key = s.line_key
        LEFT JOIN (SELECT series_key, cross_date, exhaust_date, rate_per_day, avg_per_day, forecast_m3, horizon, flags
                   FROM n360.forecast WHERE pool_id IS NULL) AS f ON f.series_key = l.series_key
        WHERE s.pool_id IS NULL
        UNION ALL
        SELECT p.account, 'pool', CAST(NULL, 'Nullable(String)'), CAST(p.pool_id, 'Nullable(UInt8)'), f.series_key,
               p.name, p.name, p.measure, p.consumed, p.billed, p.pct_billed,
               -- % of term as in v_line_status (term = start to end inclusive); pools carry dates, not term_days
               round(100 * least(greatest(dateDiff('day', p.start_date, r.d) + 1, 0), dateDiff('day', p.start_date, p.end_date) + 1)
                     / (dateDiff('day', p.start_date, p.end_date) + 1), 1),
               p.start_date, p.end_date, r.d, f.cross_date, f.exhaust_date, f.rate_per_day, f.avg_per_day, f.forecast_m3,
               f.horizon, f.flags
        FROM n360.v_pool_status AS p
        CROSS JOIN (SELECT toDate(window_end) AS d FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC, data_date DESC LIMIT 1) AS r
        LEFT JOIN (SELECT account, pool_id, series_key, cross_date, exhaust_date, rate_per_day, avg_per_day, forecast_m3, horizon, flags
                   FROM n360.forecast WHERE pool_id IS NOT NULL) AS f
               ON f.account = p.account AND f.pool_id = p.pool_id
    )
SELECT account, entitlement, line_key, pool_id, series_key, name, label, measure, used, billed, pct_billed, pct_term,
       round(pct_billed - pct_term, 1) AS pace_gap,
       cross_date, dateDiff('day', data_through, cross_date) AS days_to_cross,
       end_date < data_through AS term_ended,
       multiIf(measure IN ('not measurable', 'not implemented'), measure,
               pct_billed IS NULL, 'not read',
               pct_billed > red_pct_billed OR days_to_cross <= red_cross_days, 'red',
               pace_gap > amber_ahead_pts OR days_to_cross <= amber_cross_days, 'amber',
               'green') AS pace,
       multiIf(pace IN ('not measurable', 'not implemented', 'not read'), '',
               pct_billed > red_pct_billed, concat('above ', toString(red_pct_billed), '% of billed'),
               days_to_cross <= red_cross_days, concat('crosses billed within ', toString(red_cross_days), ' days'),
               pace_gap > amber_ahead_pts, concat('more than ', toString(amber_ahead_pts), ' points ahead of term'),
               days_to_cross <= amber_cross_days, concat('crosses billed within ', toString(amber_cross_days), ' days'),
               'on pace') AS pace_reason,
       multiIf(pace = 'red', 3, pace = 'amber', 2, pace = 'green', 1, 0) AS pace_rank,
       start_date, end_date, data_through,
       -- forecast of record, as stored (never recomputed): runs-out date, rate, forecast to horizon, flags
       exhaust_date, rate_per_day, avg_per_day, forecast_m3, horizon, flags,
       -- runway (spec 6): one category per entitlement, using the same red crossing window as pace
       multiIf(pace IN ('not measurable', 'not implemented', 'not read'), pace,
               pct_billed > red_pct_billed, 'above billed',
               days_to_cross <= red_cross_days, 'crosses soon',
               cross_date IS NOT NULL, 'crosses before term end',
               exhaust_date IS NOT NULL, 'runs out after term end',
               'no use in the last 30 days') AS runway,
       -- step change: flagged upstream (last-30 rate vs term average differs > 50%), direction read here
       round(100 * rate_per_day / nullIf(avg_per_day, 0), 1) AS last30_vs_term_pct,
       if(arrayExists(f -> startsWith(f, 'Step change'), flags),
          multiIf(rate_per_day = 0, 'stopped', rate_per_day > avg_per_day, 'rising', 'falling'), NULL) AS step_change
FROM e;

CREATE OR REPLACE VIEW n360.v_anomaly_events AS
-- Same settled day, same direction, flagged on 3 or more accounts = one cross-account event (05: "one item").
-- Direction: spikes apart from drops and silences (a surge and an outage on the same day are two things).
SELECT day,
       if(kind = 'spike', 'spikes', 'drops or silences') AS direction,
       uniqExact(account) AS accounts, count() AS vas,
       arraySort(groupUniqArray(account)) AS account_list,
       arraySort(groupUniqArray(kind)) AS kinds
FROM n360.v_va_day_anomaly
WHERE kind != 'normal' AND NOT provisional
GROUP BY day, direction
HAVING accounts >= 3;

CREATE OR REPLACE VIEW n360.v_customer_attention AS
-- One row per customer: its worst entitlement by pace (red, amber, green, then no colour; ties by pace gap),
-- and its anomaly flags. "7 settled days" = the 7 newest days that are no longer provisional.
WITH
    worst AS (
        SELECT account,
               argMax(tuple(entitlement, name, measure, used, billed, pct_billed, pct_term, pace_gap, cross_date,
                            days_to_cross, pace, pace_reason, label, pace_rank), tuple(pace_rank, ifNull(pace_gap, -1e9))) AS w,
               countIf(pace = 'red') AS red_entitlements, countIf(pace = 'amber') AS amber_entitlements,
               count() AS entitlements, 1 AS found
        FROM n360.v_line_pace GROUP BY account
    ),
    flags AS (
        SELECT account,
               countIf(kind IN ('drop', 'went silent') AND NOT provisional AND day >= settled_from) AS drops_silences_7d,
               countIf(kind = 'spike' AND NOT provisional AND day >= settled_from) AS spikes_7d,
               countIf(kind != 'normal' AND provisional) AS provisional_flags
        FROM n360.v_va_day_anomaly
        CROSS JOIN (SELECT toDate(max(window_end)) - 9 AS settled_from FROM n360.runs WHERE status = 'loaded') AS r
        GROUP BY account
    )
SELECT a.account AS account, a.display_name AS display_name,
       -- no entitlement row: NULL (not read), never '' or 0. Explicit, as join_use_nulls inside a view is not
       -- honoured by ClickHouse 25.8 (it is by newer versions): tested both.
       w.found = 1 AS has_entitlement,
       if(has_entitlement, w.w.1, NULL) AS worst_entitlement, if(has_entitlement, w.w.2, NULL) AS worst_name,
       if(has_entitlement, w.w.3, NULL) AS measure, if(has_entitlement, w.w.4, NULL) AS used,
       if(has_entitlement, w.w.5, NULL) AS billed, if(has_entitlement, w.w.6, NULL) AS pct_billed,
       if(has_entitlement, w.w.7, NULL) AS pct_term, if(has_entitlement, w.w.8, NULL) AS pace_gap,
       if(has_entitlement, w.w.9, NULL) AS cross_date, if(has_entitlement, w.w.10, NULL) AS days_to_cross,
       if(has_entitlement, w.w.11, NULL) AS pace, if(has_entitlement, w.w.12, NULL) AS pace_reason,
       if(has_entitlement, w.w.13, NULL) AS worst_label, if(has_entitlement, w.w.14, 0) AS pace_rank,
       ifNull(w.red_entitlements, 0) AS red_entitlements, ifNull(w.amber_entitlements, 0) AS amber_entitlements,
       ifNull(w.entitlements, 0) AS entitlements,
       ifNull(f.drops_silences_7d, 0) AS drops_silences_7d, ifNull(f.spikes_7d, 0) AS spikes_7d,
       ifNull(f.provisional_flags, 0) AS provisional_flags
FROM n360.account AS a
LEFT JOIN worst AS w ON w.account = a.account
LEFT JOIN flags AS f ON f.account = a.account;

CREATE OR REPLACE VIEW n360.v_anomaly_heat AS
-- Heatmap band per VA-day for the Anomalies dashboard (spec 7). Display bands, PROPOSED with the anomaly
-- thresholds (08):  band  2 spike | 1  2 <= z < 4 | 0 normal | -1  -4 < z <= -2 | -2 drop or went silent
-- Not scored (expected below 20, or under 8 weeks of history) has no row, never a colour.
WITH (SELECT anomaly_watch_z FROM n360.settings) AS watch_z   -- watch band; the flag band comes from v_va_day_anomaly's kind
SELECT day, account, bot_id, va_name, actual, expected, z, kind, provisional,
       multiIf(kind IN ('went silent', 'drop'), -2, kind = 'spike', 2, z <= -watch_z, -1, z >= watch_z, 1, 0) AS band
FROM n360.v_va_day_anomaly;

CREATE OR REPLACE VIEW n360.v_source_freshness AS
-- Source freshness (spec 11): days the source's last day is behind the data-through day.
-- Thresholds in n360.settings (amber 2 days behind, red 3).
WITH (SELECT freshness_amber_days FROM n360.settings) AS amber_days, (SELECT freshness_red_days FROM n360.settings) AS red_days
SELECT s.source_id, s.description, s.last_day, r.d AS data_through,
       dateDiff('day', s.last_day, r.d) AS days_behind,
       multiIf(days_behind >= red_days, 'red', days_behind >= amber_days, 'amber', 'green') AS status
FROM n360.source AS s
CROSS JOIN (SELECT toDate(max(window_end)) AS d FROM n360.runs WHERE status = 'loaded') AS r;

-- ───────────────────────── Phase 2 (B22, 24 Nov 2026): PROPOSED design, tested on synthetic only ─────────
-- 02_DATA_CONTRACT "Planned tables". Field meanings are this pack's reading of the spec (04 sections 9, 10) and
-- are open questions for Ryan (08). Loaded only when the export carries the file (load.py OPTIONAL), so a
-- nightly run without Phase 2 files is unaffected.

CREATE TABLE IF NOT EXISTS n360.va_config_snapshot         -- weekly configuration read per VA; keeps history
(
    snapshot_date        Date,
    account              LowCardinality(String),
    bot_id               UUID,
    engine_version       LowCardinality(String),           -- as the configuration read reports it, e.g. 'V8.2'
    engine_major         UInt8,
    v8_playbooks         UInt16,
    legacy_intents       UInt16,
    mcp_servers_live     UInt8,
    supervisory_rules_on UInt8,
    run_id               UUID
)
ENGINE = MergeTree ORDER BY (account, bot_id, snapshot_date);

CREATE TABLE IF NOT EXISTS n360.playbook_day               -- executions per playbook per day (built = has rows)
(
    day          Date,
    account      LowCardinality(String),
    bot_id       UUID,
    playbook     String,
    executions   Nullable(UInt32),
    run_id       UUID
)
ENGINE = MergeTree PARTITION BY toYYYYMM(day) ORDER BY (account, bot_id, playbook, day);

CREATE TABLE IF NOT EXISTS n360.cortex_day                 -- authoring sessions per account per day
(
    day                 Date,
    account             LowCardinality(String),
    user_type           Enum8('customer' = 1, 'staff' = 2),
    authoring_sessions  Nullable(UInt32),
    run_id              UUID
)
ENGINE = MergeTree PARTITION BY toYYYYMM(day) ORDER BY (account, user_type, day);

CREATE TABLE IF NOT EXISTS n360.explorer_day               -- analytics-assistant questions per account per day
(
    day          Date,
    account      LowCardinality(String),
    user_type    Enum8('customer' = 1, 'staff' = 2),
    questions    Nullable(UInt32),
    run_id       UUID
)
ENGINE = MergeTree PARTITION BY toYYYYMM(day) ORDER BY (account, user_type, day);

CREATE TABLE IF NOT EXISTS n360.ps_ticket                  -- support tickets; area and ask tagged by hand
(
    ticket_id     String,
    account       LowCardinality(String),
    opened        Date,
    title         String,
    product_area  LowCardinality(Nullable(String)),        -- NULL = not tagged yet (never tagged automatically)
    ask           Nullable(String),                        -- the feature asked for, in one phrase; NULL = not tagged
    status        LowCardinality(String),
    run_id        UUID
)
ENGINE = MergeTree ORDER BY (account, opened, ticket_id);

CREATE OR REPLACE VIEW n360.v_va_config_latest AS
-- Latest configuration read per VA. "Current engine" = major version 8 or later (spec 9: green = V8 or later).
WITH (SELECT engine_current_major FROM n360.settings) AS current_major
SELECT c.snapshot_date, c.account, c.bot_id, c.engine_version, c.engine_major, c.v8_playbooks, c.legacy_intents,
       c.mcp_servers_live, c.supervisory_rules_on, c.engine_major >= current_major AS current_engine
FROM n360.va_config_snapshot AS c
WHERE (c.bot_id, c.snapshot_date) IN (SELECT bot_id, max(snapshot_date) FROM n360.va_config_snapshot GROUP BY bot_id);

CREATE OR REPLACE VIEW n360.v_va_config_change AS
-- What changed per VA between its two newest configuration reads ("since last week").
WITH ranked AS (SELECT *, row_number() OVER (PARTITION BY bot_id ORDER BY snapshot_date DESC) AS rn
                FROM n360.va_config_snapshot)
SELECT cur.account AS account, cur.bot_id AS bot_id, cur.snapshot_date AS snapshot_date, prev.snapshot_date AS previous_date,
       arrayFilter(x -> x != '', [
           if(cur.engine_version != prev.engine_version, concat('engine ', prev.engine_version, ' → ', cur.engine_version), ''),
           if(cur.v8_playbooks != prev.v8_playbooks, concat('V8 playbooks ', toString(prev.v8_playbooks), ' → ', toString(cur.v8_playbooks)), ''),
           if(cur.legacy_intents != prev.legacy_intents, concat('legacy intents ', toString(prev.legacy_intents), ' → ', toString(cur.legacy_intents)), ''),
           if(cur.mcp_servers_live != prev.mcp_servers_live, concat('MCP servers ', toString(prev.mcp_servers_live), ' → ', toString(cur.mcp_servers_live)), ''),
           if(cur.supervisory_rules_on != prev.supervisory_rules_on, concat('supervisory rules ', toString(prev.supervisory_rules_on), ' → ', toString(cur.supervisory_rules_on)), '')
       ]) AS changes
FROM ranked AS cur INNER JOIN ranked AS prev ON prev.bot_id = cur.bot_id AND prev.rn = 2
WHERE cur.rn = 1 AND length(changes) > 0;

CREATE OR REPLACE VIEW n360.v_top_asks AS
-- An ask wanted by several customers (spec 10 "top asks"). PROPOSED: "several" = 2 or more customers.
WITH (SELECT asks_several_customers FROM n360.settings) AS several
SELECT ask, uniqExact(account) AS customers, count() AS tickets, arraySort(groupUniqArray(account)) AS account_list,
       min(opened) AS first_asked, max(opened) AS last_asked
FROM n360.ps_ticket WHERE ask IS NOT NULL
GROUP BY ask HAVING customers >= several;

-- ───────────────────────── forecast accuracy (B16) ─────────────────────────────────────────────

CREATE OR REPLACE VIEW n360.v_forecast_accuracy AS
-- How far the stored forecast was off: a forecast made on night m (data through m - 1) predicts used d days later
-- as consumed + rate x d (the straight line it stores; evaluated, never refitted). Actual = consumed as stored on
-- the night d days later. APE = |actual - predicted| / actual; MAPE = mean APE, per line or pool and horizon.
SELECT f.account AS account, f.series_key AS series_key, h.d AS horizon_days, count() AS forecasts,
       round(100 * avg(abs(a.consumed - (f.consumed + f.rate_per_day * h.d)) / a.consumed), 1) AS mape,
       min(f.data_date) AS first_forecast, max(f.data_date) AS last_forecast
FROM n360.forecast_history AS f
CROSS JOIN (SELECT arrayJoin([30, 60, 90]) AS d) AS h
INNER JOIN n360.forecast_history AS a ON a.series_key = f.series_key AND a.data_date = f.data_date + h.d
WHERE a.consumed > 0
GROUP BY f.account, f.series_key, h.d;

CREATE OR REPLACE VIEW n360.v_forecast_change AS
-- Each night against the night before (per line or pool): % of billed, crossing date, and how far the crossing
-- date moved (negative = earlier). Feeds the "over billed", "crossing soon" alerts and the pace-change anomaly.
WITH (SELECT pace_red_pct_billed FROM n360.settings) AS red_pct,
     (SELECT pace_red_cross_days FROM n360.settings) AS soon_days,
     (SELECT pace_change_days FROM n360.settings) AS moved_days_flag,
     nights AS (SELECT data_date AS night,
                       lagInFrame(data_date) OVER (ORDER BY data_date ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS previous_night
                FROM (SELECT DISTINCT data_date FROM n360.forecast_history))
SELECT cur.data_date AS data_date, prev.data_date AS previous_date, cur.account AS account, cur.series_key AS series_key,
       round(100 * cur.consumed / cur.billed, 1) AS pct_billed, round(100 * prev.consumed / prev.billed, 1) AS pct_billed_before,
       cur.cross_date AS cross_date, prev.cross_date AS cross_date_before,
       dateDiff('day', prev.cross_date, cur.cross_date) AS moved_days,
       pct_billed > red_pct AND pct_billed_before <= red_pct AS newly_over_billed,
       dateDiff('day', cur.data_date - 1, cur.cross_date) <= soon_days
           AND (prev.cross_date IS NULL OR dateDiff('day', prev.data_date - 1, prev.cross_date) > soon_days) AS newly_crossing_soon,
       moved_days <= -moved_days_flag AS moved_earlier
FROM n360.forecast_history AS cur
INNER JOIN nights AS n ON n.night = cur.data_date
INNER JOIN n360.forecast_history AS prev ON prev.series_key = cur.series_key AND prev.data_date = n.previous_night
WHERE cur.billed > 0;

CREATE OR REPLACE VIEW n360.v_forecast_drift AS
-- How far each line's crossing date has moved: now against 7 and 30 stored nights ago (negative = earlier).
WITH (SELECT max(data_date) FROM n360.forecast_history) AS newest
SELECT account, series_key,
       argMax(cross_date, data_date) AS cross_now,
       argMaxIf(cross_date, data_date, data_date <= newest - 7) AS cross_7_nights_ago,
       argMaxIf(cross_date, data_date, data_date <= newest - 30) AS cross_30_nights_ago,
       dateDiff('day', cross_7_nights_ago, cross_now) AS moved_7,
       dateDiff('day', cross_30_nights_ago, cross_now) AS moved_30,
       countIf(data_date <= newest - 30) > 0 AS has_30_nights
FROM n360.forecast_history
GROUP BY account, series_key
HAVING cross_now IS NOT NULL OR cross_7_nights_ago IS NOT NULL OR cross_30_nights_ago IS NOT NULL;

-- ───────────────────────── anomaly review (B19) ────────────────────────────────────────────────

CREATE OR REPLACE VIEW n360.v_anomaly_review AS
-- Every flag with its newest review (a later review replaces an earlier one). outcome NULL = not reviewed yet.
SELECT a.day AS day, a.account AS account, a.bot_id AS bot_id, a.va_name AS va_name, a.kind AS kind, a.provisional AS provisional,
       a.actual AS actual, a.expected AS expected, a.z AS z,
       r.last_outcome AS outcome, r.last_note AS note, r.last_by AS reviewed_by, r.last_at AS reviewed_at
FROM n360.v_va_day_anomaly AS a
LEFT JOIN (SELECT bot_id, day, argMax(toNullable(outcome), reviewed_at) AS last_outcome,
                  toNullable(argMax(note, reviewed_at)) AS last_note, toNullable(argMax(reviewed_by, reviewed_at)) AS last_by,
                  toNullable(max(reviewed_at)) AS last_at
           FROM n360.anomaly_log GROUP BY bot_id, day) AS r ON r.bot_id = a.bot_id AND r.day = a.day
WHERE a.kind != 'normal';   -- Nullable columns: a flag with no review gets NULL (no join_use_nulls: it would leak into the inner view)

CREATE OR REPLACE VIEW n360.v_flag_quality AS
-- Weekly flag quality (settled flags only): how many were reviewed, and how many were real. Tunes the thresholds.
SELECT toMonday(day) AS week, count() AS flags, countIf(outcome IS NOT NULL) AS reviewed,
       countIf(outcome = 'real') AS real, countIf(outcome = 'expected') AS expected,
       countIf(outcome = 'data issue') AS data_issue,
       if(reviewed = 0, NULL, round(100 * real / reviewed, 1)) AS pct_real_of_reviewed
FROM n360.v_anomaly_review WHERE NOT provisional
GROUP BY week;

-- ───────────────────────── anomaly detection v2 (B20) ───────────────────────────────────────────
-- Complete weeks only (Monday to Sunday, every day read). Each week is compared with the median of the same series'
-- previous 8 complete weeks; thresholds in n360.settings.

CREATE OR REPLACE VIEW n360.v_containment_week AS
WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS data_through,
     w AS (SELECT d.bot_id AS bot_id, d.account AS account, toMonday(d.day) AS week, sum(d.engaged) AS week_engaged,
                  1 - sum(d.handoffs) / week_engaged AS containment
           FROM n360.va_day AS d WHERE d.handoffs IS NOT NULL AND d.engaged IS NOT NULL
           GROUP BY bot_id, account, week HAVING week + 6 <= data_through AND count() = 7 AND week_engaged > 0)
SELECT bot_id, account, week, week_engaged AS engaged, containment,
       quantileExact(0.5)(containment) OVER (PARTITION BY bot_id ORDER BY week ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS usual,
       count() OVER (PARTITION BY bot_id ORDER BY week ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS weeks_before
FROM w;

CREATE OR REPLACE VIEW n360.v_measure_gap_week AS
-- Lines with a contract rule: weekly contract measure / platform engaged flag.
WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS data_through,
     w AS (SELECT l.account AS account, l.line_key AS line_key, toMonday(d.day) AS week,
                  sum(d.measured) AS measured, sum(d.engaged) AS engaged, measured / engaged AS ratio
           FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
           WHERE d.kind = 'line' AND l.rule_id IS NOT NULL AND d.measured IS NOT NULL AND d.engaged IS NOT NULL
           GROUP BY account, line_key, week HAVING week + 6 <= data_through AND count() = 7 AND engaged > 0)
SELECT account, line_key, week, measured, engaged, ratio,
       quantileExact(0.5)(ratio) OVER (PARTITION BY line_key ORDER BY week ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS usual,
       count() OVER (PARTITION BY line_key ORDER BY week ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS weeks_before
FROM w;

CREATE OR REPLACE VIEW n360.v_anomaly_v2 AS
-- Anomaly detection v2 (05): one row per signal raised. value / usual in % (containment, measure share) or days
-- (pace change: how far the crossing date moved). Cross-account events are in v_anomaly_events.
WITH (SELECT containment_drop_pts FROM n360.settings) AS drop_pts,
     (SELECT containment_min_engaged FROM n360.settings) AS min_engaged,
     (SELECT measure_gap_pts FROM n360.settings) AS gap_pts
SELECT 'containment drop' AS signal, toString(c.account) AS account,
       concat(v.va_name, ' (', arrayStringConcat(v.channels, ', '), ')') AS subject, c.week AS period,
       round(100 * c.containment, 1) AS value, toNullable(round(100 * c.usual, 1)) AS usual,
       concat('week of ', toString(c.week), ', ', toString(c.engaged), ' engaged') AS detail
FROM n360.v_containment_week AS c INNER JOIN n360.va AS v ON v.bot_id = c.bot_id
WHERE c.weeks_before = 8 AND c.engaged >= min_engaged AND 100 * (c.usual - c.containment) > drop_pts
UNION ALL
SELECT 'measure gap', toString(g.account), g.line_key, g.week, round(100 * g.ratio, 1), toNullable(round(100 * g.usual, 1)),
       concat('contract measure as % of engaged, week of ', toString(g.week))
FROM n360.v_measure_gap_week AS g
WHERE g.weeks_before = 8 AND abs(100 * (g.ratio - g.usual)) > gap_pts
UNION ALL
SELECT 'pace change', toString(f.account), f.series_key, f.data_date, toFloat64(f.moved_days), CAST(NULL, 'Nullable(Float64)'),
       concat('crossing date ', toString(f.cross_date_before), ' -> ', toString(f.cross_date))
FROM n360.v_forecast_change AS f WHERE f.moved_earlier
UNION ALL
SELECT 'freshness', '', toString(s.source_id), s.data_through, toFloat64(s.days_behind), CAST(NULL, 'Nullable(Float64)'),
       concat(toString(s.days_behind), ' days behind (', s.status, ')')
FROM n360.v_source_freshness AS s WHERE s.status != 'green';

CREATE OR REPLACE VIEW n360.v_challenger_backtest AS
-- The newest challenger backtest: MAPE per model and horizon (all lines and pools). Comparison only (B21).
SELECT model, horizon_days, count() AS forecasts,
       round(100 * avg(abs(actual - predicted) / actual), 2) AS mape, any(data_through) AS data_through, any(run_at) AS run_at
FROM n360.forecast_challenger
WHERE run_id = (SELECT argMax(run_id, run_at) FROM n360.forecast_challenger) AND actual > 0
GROUP BY model, horizon_days;
