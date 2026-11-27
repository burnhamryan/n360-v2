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
    20  AS measure_gap_pts,          -- anomaly v2: measure / engaged ratio this many points from its usual level
    -- contract status and attention (dashboard review, 25 Nov 2026): PROPOSED, from the v2 redesign spec; Ryan to approve
    10  AS status_pace_pts,          -- ahead / below pace: % of billed this many points above / below % of term
    30  AS status_too_early_days,    -- too early to tell: fewer days into the term than this
    50  AS renewal_risk_pct,         -- renewal risk: under this % of billed used ...
    120 AS renewal_risk_days,        -- ... with this many days or fewer left
    90  AS decision_window_days,     -- decisions due: terms ending within this many days
    30  AS ending_soon_days,         -- overview: lines ending within this many days
    90  AS ended_recent_days,        -- needs attention: a term that ended within this many days
    1000 AS attention_va_min_convs,  -- needs attention: a VA with at least this many conversations (previous 30 days) ...
    25  AS attention_va_drop_pct,    -- ... down this % or more (last 30 days against the 30 before)
    10  AS health_convs_down_pct,    -- customer health: VA conversations down this % or more
    3   AS health_containment_down_pts, -- customer health: VA containment down this many points or more
    50  AS containment_low_pct,      -- adoption: VAs containing under this %
    3   AS target_features_min,      -- adoption: customers using at least this many target features
    2   AS stamp_stale_days,         -- freshness stamp turns amber when the data is more than this many days old
    -- risk dashboard (25 Nov 2026): PROPOSED, docs/17_RISK_RESEARCH.md; no published market standard for most of these
    120 AS risk_renewal_window_days, -- renewals this close count as "at-risk renewals" and show on the runway
    30  AS risk_contact_watch_days,  -- no customer contact (Salesforce) for this many days: watch
    60  AS risk_contact_risk_days,   -- ... risk (GitLab's non-engagement play fires at 60 days)
    180 AS risk_exec_watch_days,     -- no executive meeting for this many days: watch
    ['Commit', 'Closed'] AS risk_safe_forecast_categories,   -- a renewal inside the decision window not in these: watch
    ['Omitted'] AS risk_lost_forecast_categories,            -- an open renewal in these: risk
    180 AS risk_renewal_lost_days,   -- a renewal closed lost within this many days: risk
    10  AS risk_volume_watch_pct,    -- customer conversations down this % (last 30 days against the 30 before): watch
    25  AS risk_volume_risk_pct,     -- ... risk
    80  AS risk_concentration_pct,   -- one VA carries this share of a customer's conversations (2+ VAs): watch
    8   AS risk_stale_config_weeks,  -- no configuration change on any of a customer's VAs for this many weeks: watch
    30  AS risk_ticket_age_days,     -- oldest open ticket older than this: watch
    3   AS risk_open_tickets,        -- this many open tickets or more: watch
    50  AS risk_building_drop_pct,   -- customer authoring + assistant use down this %: watch
    30  AS risk_pulse_valid_days,    -- a CSM's rating counts for this many days, then shows as expired
    0.9 AS risk_downsell_ratio,      -- PROPOSED: a renewal won at under this share of the customer's previous won renewal is a downsell
    -- adoption (27 Nov 2026, docs/04 section 9): the six features' definitions come from the v1.1 page; these settle the edges
    31  AS adoption_calibration_days,     -- "at least monthly": a calibration run within this many days of the window end
    10000 AS adoption_long_playbook_chars,  -- PROPOSED: a playbook this long or longer is flagged as long (the 10000+ band)
    25  AS adoption_handoff_high_pct,     -- PROPOSED: handoffs from playbooks over this share of their conversations: look at it
    'netomi.com' AS adoption_staff_domain, -- playbook_window.creator_domain: this domain = built by Netomi, any other = the customer
    -- the order to move a customer through the features (next step = the first one not adopted): decided 27 Nov 2026
    ['V8 / V9', 'Cortex', 'Action-oriented recipes', 'Test / playbook validation', 'Calibration', 'Explorer'] AS adoption_next_order;


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

CREATE TABLE IF NOT EXISTS n360.cortex_day                 -- Cortex (the playbook authoring tool) sessions per account per day
(
    day                 Date,
    account             LowCardinality(String),
    user_type           Enum8('customer' = 1, 'staff' = 2, 'unknown' = 3),   -- unknown: Cortex has no user id (27 Nov 2026)
    authoring_sessions  Nullable(UInt32),
    run_id              UUID
)
ENGINE = MergeTree PARTITION BY toYYYYMM(day) ORDER BY (account, user_type, day);

CREATE TABLE IF NOT EXISTS n360.explorer_day               -- Explorer (the analytics assistant) questions per account per day
(
    day          Date,
    account      LowCardinality(String),
    user_type    Enum8('customer' = 1, 'staff' = 2),
    questions    Nullable(UInt32),
    run_id       UUID
)
ENGINE = MergeTree PARTITION BY toYYYYMM(day) ORDER BY (account, user_type, day);

ALTER TABLE n360.cortex_day MODIFY COLUMN user_type Enum8('customer' = 1, 'staff' = 2, 'unknown' = 3);

CREATE TABLE IF NOT EXISTS n360.feature_window             -- feature use per VA over the window the source reads (27 Nov 2026)
(                                                          -- executions and Explorer are read as window totals, not per day
    as_of         Date,
    account       LowCardinality(String),
    bot_id        UUID,
    feature       LowCardinality(String),                  -- 'V8 playbooks' | 'Analytics assistant' | 'Authoring tool'
    user_type     Enum8('customer' = 1, 'staff' = 2, 'unknown' = 3),
    window_start  Date,
    window_end    Date,
    amount        Nullable(UInt32),                        -- NULL = not read (Not reported yet); 0 = read and none
    unit          LowCardinality(String),                  -- executions | questions | sessions
    last_day      Nullable(Date),
    run_id        UUID
)
ENGINE = MergeTree ORDER BY (account, feature, bot_id);

CREATE TABLE IF NOT EXISTS n360.va_adoption                -- the v1.1 Adoption view per VA, newest read (27 Nov 2026)
(                                                          -- every count: NULL = not read (Not reported yet), 0 = read and none
    as_of                  Date,
    account                LowCardinality(String),
    bot_id                 UUID,
    va                     String,
    region                 LowCardinality(String),
    engine_version         LowCardinality(Nullable(String)),
    engine_major           Nullable(UInt8),
    playbooks_live         Nullable(UInt16),               -- every production playbook
    v8_playbooks           Nullable(UInt16),               -- the ones that count as V8 (VA engine V8 or later)
    legacy_intents         Nullable(UInt16),
    window_start           Date,                           -- the executions window (31 days)
    window_end             Date,
    executions             Nullable(UInt32),
    conversations          Nullable(UInt32),               -- conversations that ran at least one playbook
    handoffs               Nullable(UInt32),               -- handoffs to an agent from within a playbook
    playbooks_executed     Nullable(UInt16),
    last_execution         Nullable(Date),
    idle_playbooks         Nullable(UInt16),               -- V8 playbooks live with no execution in the per-playbook window
    calibration_runs       Nullable(UInt16),
    calibration_completed  Nullable(UInt16),
    calibration_last       Nullable(Date),
    behavior_validation    LowCardinality(Nullable(String)),  -- enabled | off | not set
    test_conversations     Nullable(UInt32),
    test_days              Nullable(UInt16),
    test_suites            Nullable(UInt16),
    test_runs              Nullable(UInt32),
    test_last              Nullable(Date),
    recipes                Nullable(UInt16),
    recipes_production     Nullable(UInt16),
    recipe_actions         Nullable(UInt16),
    mcp_live               Nullable(UInt8),
    mcp_not_live           Nullable(UInt8),
    mcp_names              String,
    supervisory_live       Nullable(UInt8),
    supervisory_default    Nullable(UInt8),
    supervisory_names      String,
    cortex_sessions_30d    Nullable(UInt32),               -- US VAs only; Netomi or customer not known
    cortex_last            Nullable(Date),
    explorer_questions_30d Nullable(UInt32),               -- customer users only, Netomi staff never count
    explorer_users_30d     Nullable(UInt16),
    explorer_last          Nullable(Date),
    run_id                 UUID
)
ENGINE = MergeTree ORDER BY (account, bot_id);

CREATE TABLE IF NOT EXISTS n360.playbook_window            -- each production playbook, newest read (27 Nov 2026)
(
    as_of              Date,
    account            LowCardinality(String),
    bot_id             UUID,
    va                 String,
    task_id            String,
    playbook           String,
    instruction_chars  UInt32,
    tools              UInt16,
    trigger_chars      UInt32,
    length_band        LowCardinality(String),             -- <200 (short) | 200-999 | 1000-2999 | 3000-5999 | 6000-9999 | 10000+
    counts_as_v8       Bool,
    executions         Nullable(UInt32),                   -- in the per-playbook window; NULL = not read
    idle               Nullable(Bool),                     -- V8 and no execution in the window
    window_start       Date,
    window_end         Date,
    creator_domain     Nullable(String),
    run_id             UUID
)
ENGINE = MergeTree ORDER BY (account, bot_id, playbook);

CREATE TABLE IF NOT EXISTS n360.cortex_user_day            -- Cortex sessions per person per VA per day (27 Nov 2026, docs/25)
(                                                          -- the Netomi leaderboard and Netomi-vs-customer comparisons
    day               Date,
    account           LowCardinality(String),
    bot_id            Nullable(UUID),                      -- NULL = the session was not on one VA
    user_type         Enum8('customer' = 1, 'staff' = 2),  -- staff = a Netomi user (settings.adoption_staff_domain)
    user              Nullable(String),                    -- the Netomi user's email; NULL for every customer user:
                                                           -- customer users are counted, never named
    sessions          UInt32,
    edits             Nullable(UInt32),                    -- playbook versions saved; NULL = not read
    production_edits  Nullable(UInt32),                    -- of those, saved to a production playbook; NULL = not read
    run_id            UUID
)
ENGINE = MergeTree PARTITION BY toYYYYMM(day) ORDER BY (account, day);

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

CREATE OR REPLACE VIEW n360.v_va_feature_adoption AS
-- The six adoption features per VA (the v1.1 Adoption view), one row each, from n360.va_adoption (newest read).
-- state: adopted | partly | none | not reported. value: the cell's words ("54 recipes"). detail: the tooltip. The
-- "what adopted means" lines are the v1.1 page's, where the source cannot show it all (Cortex cannot tell who ran a
-- session, or whether it changed a production playbook) the detail says so. NULL = not read = "not reported", never
-- "none" (docs/25). Edges decided here, not in dashboards: n360.settings adoption_*.
WITH (SELECT adoption_calibration_days FROM n360.settings) AS cal_days,
     (SELECT engine_current_major FROM n360.settings) AS current_major
SELECT a.account AS account, a.bot_id AS bot_id, a.va AS va, a.region AS region, f.1 AS ord, f.2 AS feature,
       f.3 AS state, f.4 AS value, f.5 AS detail, f.6 AS metric, f.7 AS unit, f.8 AS means
FROM n360.va_adoption AS a
ARRAY JOIN [
  (1, 'Cortex',
   multiIf(a.cortex_sessions_30d IS NULL, 'not reported', a.cortex_sessions_30d > 0, 'adopted', 'none'),
   multiIf(a.cortex_sessions_30d IS NULL, 'Not reported yet', a.cortex_sessions_30d > 0,
           concat(toString(a.cortex_sessions_30d), if(a.cortex_sessions_30d = 1, ' session', ' sessions')), 'None recorded'),
   multiIf(a.cortex_sessions_30d IS NULL, concat('Cortex is read for US VAs only· this VA is in ', toString(a.region), ' or was not read.'),
           concat(toString(a.cortex_sessions_30d), if(a.cortex_sessions_30d = 1, ' Cortex session', ' Cortex sessions'), ' in the 30 days to ', concat(toString(toDayOfMonth(a.window_end)), ' ', formatDateTime(a.window_end, '%b %Y')),
                  if(a.cortex_last IS NULL, '', concat(' · last ', concat(toString(toDayOfMonth(a.cortex_last)), ' ', formatDateTime(a.cortex_last, '%b %Y')))),
                  '. Cortex has no user id: whether Netomi or the customer ran a session, and whether it changed a production playbook, is not known yet.')),
   toFloat64(ifNull(a.cortex_sessions_30d, 0)), 'sessions',
   'Used to create or edit a production playbook in the last 30 days'),
  (2, 'Test / playbook validation',
   multiIf(a.test_runs IS NULL AND a.behavior_validation IS NULL, 'not reported',
           a.behavior_validation = 'enabled' AND ifNull(a.test_runs, 0) > 0, 'adopted',
           ifNull(a.test_runs, 0) > 0 OR a.behavior_validation = 'enabled', 'partly', 'none'),
   multiIf(a.test_runs IS NULL AND a.behavior_validation IS NULL, 'Not reported yet',
           ifNull(a.test_runs, 0) > 0, concat(toString(a.test_runs), if(a.test_runs = 1, ' run', ' runs'),
                                              if(a.behavior_validation = 'enabled', '', ', validation off')),
           a.behavior_validation = 'enabled', 'On, no runs', 'None recorded'),
   concat('Behavior Validation: ', ifNull(a.behavior_validation, 'not set'), '. ',
          toString(ifNull(a.test_runs, 0)), if(a.test_runs = 1, ' test run in ', ' test runs in '), toString(ifNull(a.test_suites, 0)),
          if(a.test_suites = 1, ' suite, ', ' suites, '),
          toString(ifNull(a.test_conversations, 0)), ' validation conversations over ', toString(ifNull(a.test_days, 0)), if(a.test_days = 1, ' day', ' days'),
          if(a.test_last IS NULL, '', concat(' · last ', concat(toString(toDayOfMonth(a.test_last)), ' ', formatDateTime(a.test_last, '%b %Y')))), '.'),
   toFloat64(ifNull(a.test_runs, 0)), 'runs', 'Run on every playbook version before promotion'),
  (3, 'Calibration',
   multiIf(a.calibration_runs IS NULL, 'not reported',
           a.calibration_last IS NOT NULL AND a.calibration_last > a.window_end - cal_days, 'adopted',
           a.calibration_last IS NOT NULL OR a.calibration_runs > 0, 'partly', 'none'),
   multiIf(a.calibration_runs IS NULL, 'Not reported yet',
           a.calibration_last IS NOT NULL AND a.calibration_last > a.window_end - cal_days,
             concat(toString(a.calibration_runs), if(a.calibration_runs = 1, ' run', ' runs')),
           a.calibration_last IS NOT NULL, concat('Lapsed: last ', toString(toDayOfMonth(a.calibration_last)), ' ', formatDateTime(a.calibration_last, '%b')),
           'None recorded'),
   concat(toString(ifNull(a.calibration_runs, 0)), if(a.calibration_runs = 1, ' calibration run (', ' calibration runs ('), toString(ifNull(a.calibration_completed, 0)), ' completed)',
          if(a.calibration_last IS NULL, ' · never run', concat(' · last ', concat(toString(toDayOfMonth(a.calibration_last)), ' ', formatDateTime(a.calibration_last, '%b %Y')))),
          '. Adopted = a run in the last ', toString(cal_days), ' days (at least monthly).'),
   toFloat64(ifNull(a.calibration_runs, 0)), 'runs', 'At least monthly on Tier 1 and Tier 2 use cases'),
  (4, 'V8 / V9',
   multiIf(a.engine_major IS NULL, 'not reported', a.engine_major >= current_major, 'adopted', 'none'),
   multiIf(a.engine_major IS NULL, 'Not reported yet', a.engine_major >= current_major, ifNull(a.engine_version, 'V8+'),
           concat(ifNull(a.engine_version, 'below V8'), ': upgrade')),
   concat('Engine ', ifNull(a.engine_version, 'not read'), ' · ', toString(ifNull(a.playbooks_live, 0)), ' playbooks live (',
          toString(ifNull(a.v8_playbooks, 0)), ' count as V8), ', toString(ifNull(a.legacy_intents, 0)), ' legacy intents.'),
   toFloat64(a.engine_major >= current_major), 'VAs', 'Production VA running on V8 or V9, target 100%'),
  (5, 'Action-oriented recipes',
   multiIf(a.recipes IS NULL, 'not reported', ifNull(a.recipes_production, 0) > 0 AND ifNull(a.recipe_actions, 0) > 0, 'adopted',
           a.recipes > 0, 'partly', 'none'),
   multiIf(a.recipes IS NULL, 'Not reported yet',
           ifNull(a.recipes_production, 0) > 0 AND ifNull(a.recipe_actions, 0) > 0,
             concat(toString(a.recipes_production), ' in production'),
           a.recipes > 0, concat(toString(a.recipes), ' built, none live'), 'None recorded'),
   concat(toString(ifNull(a.recipes, 0)), if(a.recipes = 1, ' recipe, ', ' recipes, '), toString(ifNull(a.recipes_production, 0)),
          ' in production, ', toString(ifNull(a.recipe_actions, 0)),
          if(a.recipe_actions = 1, ' write action', ' write actions'), ' against customer systems.'),
   toFloat64(ifNull(a.recipes_production, 0)), 'in production', 'At least one production recipe performs a write against a customer system'),
  (6, 'Explorer',
   multiIf(a.explorer_questions_30d IS NULL, 'not reported', a.explorer_questions_30d > 0, 'adopted', 'none'),
   multiIf(a.explorer_questions_30d IS NULL, 'Not reported yet', a.explorer_questions_30d > 0,
           concat(toString(a.explorer_questions_30d), if(a.explorer_questions_30d = 1, ' question', ' questions')), 'None recorded'),
   if(a.explorer_questions_30d IS NULL, 'Explorer was not read for this VA.',
      concat(toString(a.explorer_questions_30d), ' questions from ', toString(ifNull(a.explorer_users_30d, 0)),
             ' customer users in the last 30 days', if(a.explorer_last IS NULL, '', concat(' · last ', concat(toString(toDayOfMonth(a.explorer_last)), ' ', formatDateTime(a.explorer_last, '%b %Y')))),
             '. Production and Staging, Netomi staff never count.')),
   toFloat64(ifNull(a.explorer_questions_30d, 0)), 'questions',
   'At least one question by a customer user in the last 30 days, in Production or Staging· Netomi staff not counted')
] AS f;

CREATE OR REPLACE VIEW n360.v_customer_feature_adoption AS
-- The six features per customer, rolled up from its VAs. A feature is adopted when any VA has adopted it, except
-- V8 / V9, adopted only when every VA read is on V8 or later (target 100%). value: the cell's words. (Inner names
-- x_*: ClickHouse would read an output name reused inside an aggregate as the aggregate.)
SELECT account, ord, feature, any(x_means) AS means,
       multiIf(countIf(x_state != 'not reported') = 0, 'not reported',
               feature = 'V8 / V9', if(countIf(x_state = 'none') = 0, 'adopted', if(countIf(x_state = 'adopted') > 0, 'partly', 'none')),
               countIf(x_state = 'adopted') > 0, 'adopted', countIf(x_state = 'partly') > 0, 'partly', 'none') AS state,
       countIf(x_state = 'adopted') AS vas_adopted, countIf(x_state != 'not reported') AS vas_read,
       sumIf(x_metric, x_state != 'not reported') AS metric,
       multiIf(countIf(x_state != 'not reported') = 0, 'Not reported yet',
               feature = 'V8 / V9', concat(toString(countIf(x_state = 'adopted')), ' of ', toString(countIf(x_state != 'not reported')),
                                          if(countIf(x_state != 'not reported') = 1, ' VA', ' VAs')),
               sumIf(x_metric, x_state != 'not reported') = 0 AND countIf(x_state = 'partly') = 0, 'None recorded',
               countIf(x_state = 'adopted') = 0 AND countIf(x_state = 'partly') > 0, anyIf(x_value, x_state = 'partly'),
               concat(toString(toUInt64(sumIf(x_metric, x_state != 'not reported'))), ' ',
                      if(toUInt64(sumIf(x_metric, x_state != 'not reported')) = 1,
                         transform(any(x_unit), ['sessions', 'runs', 'questions'], ['session', 'run', 'question'], any(x_unit)),
                         any(x_unit)))) AS value,
       concat(toString(countIf(x_state = 'adopted')), ' of ', toString(count()), ' VAs adopted · ',
              arrayStringConcat(groupArray(concat(x_va, ': ', x_value)), ' · ')) AS detail
FROM (SELECT account, ord, feature, means AS x_means, state AS x_state, metric AS x_metric, value AS x_value, unit AS x_unit,
             va AS x_va FROM n360.v_va_feature_adoption)
GROUP BY account, ord, feature;

CREATE OR REPLACE VIEW n360.v_customer_adoption AS
-- One row per customer: the six features adopted, the playbook window totals, and the next feature to move (the
-- first not adopted, in settings.adoption_next_order: PROPOSED). Handoff share = handoffs from playbooks / the
-- conversations that ran a playbook. A customer with no va_adoption row is not here (not reported).
WITH (SELECT adoption_next_order FROM n360.settings) AS next_order
SELECT a.account AS account, f.adopted AS features_adopted, f.read AS features_read, f.next_feature AS next_feature,
       a.vas AS vas, a.vas_v8 AS vas_v8, a.executions AS executions, a.conversations AS conversations, a.handoffs AS handoffs,
       if(a.conversations > 0, round(100 * a.handoffs / greatest(ifNull(a.conversations, 0), 1), 1), NULL) AS handoff_pct,
       a.playbooks_live AS playbooks_live, a.playbooks_executed AS playbooks_executed, a.idle_playbooks AS idle_playbooks,
       a.last_execution AS last_execution, a.legacy_intents AS legacy_intents, a.window_start AS window_start, a.window_end AS window_end
FROM (SELECT account, count() AS vas, countIf(engine_major >= (SELECT engine_current_major FROM n360.settings)) AS vas_v8,
             sum(executions) AS executions, sum(conversations) AS conversations, sum(handoffs) AS handoffs,
             sum(playbooks_live) AS playbooks_live, sum(playbooks_executed) AS playbooks_executed, sum(idle_playbooks) AS idle_playbooks,
             max(last_execution) AS last_execution, sum(legacy_intents) AS legacy_intents,
             min(window_start) AS window_start, max(window_end) AS window_end
      FROM n360.va_adoption GROUP BY account) AS a
INNER JOIN (SELECT account, countIf(state = 'adopted') AS adopted, countIf(state != 'not reported') AS read,
                   arrayFirst(x -> has(groupArrayIf(feature, state IN ('none', 'partly')), x), next_order) AS next_feature
            FROM n360.v_customer_feature_adoption GROUP BY account) AS f ON f.account = a.account;

CREATE OR REPLACE VIEW n360.v_playbook AS
-- Each production playbook (newest read, n360.playbook_window) with what the Adoption pages show about it: its share
-- of its VA's executions, rank on the VA, whether it is long (settings.adoption_long_playbook_chars), short (< 200
-- characters) and who built it (settings.adoption_staff_domain = Netomi, another domain = the customer, NULL = not read).
WITH (SELECT adoption_long_playbook_chars FROM n360.settings) AS long_chars,
     (SELECT adoption_staff_domain FROM n360.settings) AS staff
SELECT p.account AS account, p.bot_id AS bot_id, p.va AS va, p.task_id AS task_id, p.playbook AS playbook,
       p.instruction_chars AS instruction_chars, p.tools AS tools, p.trigger_chars AS trigger_chars, p.length_band AS length_band,
       p.instruction_chars < 200 AS short, p.instruction_chars >= long_chars AS long, p.counts_as_v8 AS counts_as_v8,
       p.executions AS executions, p.idle AS idle, p.window_start AS window_start, p.window_end AS window_end,
       multiIf(p.creator_domain IS NULL, NULL, p.creator_domain = staff, 'Netomi', 'Customer') AS built_by,
       if(sum(p.executions) OVER (PARTITION BY p.bot_id) > 0,
          round(100 * p.executions / sum(p.executions) OVER (PARTITION BY p.bot_id), 1), NULL) AS share_of_va_pct,
       row_number() OVER (PARTITION BY p.bot_id ORDER BY ifNull(p.executions, 0) DESC, p.playbook) AS rank_on_va,
       indexOf(['<200 (short)', '200-999', '1000-2999', '3000-5999', '6000-9999', '10000+'], p.length_band) AS band_order
FROM n360.playbook_window AS p;

CREATE OR REPLACE VIEW n360.v_cortex_user AS
-- n360.cortex_user_day with who it was (Netomi or the customer) and, for Netomi users only, a display name made from the
-- email ("avery.kim@netomi.com" -> "Avery Kim"). Customer users have no name here, by design.
SELECT c.day AS day, c.account AS account, c.bot_id AS bot_id, c.user_type AS user_type,
       if(c.user_type = 'staff', 'Netomi', 'Customer') AS who, c.user AS user,
       if(c.user_type = 'staff' AND c.user IS NOT NULL,
          arrayStringConcat(arrayMap(w -> concat(upper(substring(w, 1, 1)), substring(w, 2)),
                                     splitByChar('.', splitByChar('@', assumeNotNull(c.user))[1])), ' '), NULL) AS name,
       c.sessions AS sessions, c.edits AS edits, c.production_edits AS production_edits
FROM n360.cortex_user_day AS c;

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

-- ───────────────────────── contract status and attention (dashboard review, 25 Nov 2026) ─────────────────────
-- The v1.1 page's contract statuses, decided HERE and nowhere else; dashboards only map a status to its colour.
-- Thresholds are PROPOSED (n360.settings). One row per entitlement: every line not in a pool, and each pool once.
--   not built            the line is contracted but nothing is implemented (measure 'not implemented')
--   not measured         the contract unit is one the platform does not count (measure 'not measurable')
--   billed not recorded  the billed amount is blank on the sheet and the line is not in a pool
--   unmapped             no VA is mapped to the line yet
--   under review         the line's contract rule is not live yet (pending or blocked): measured on engaged for now
--   not read             no use was read for the line
--   over billed          used is above billed
--   will exceed          the stored forecast crosses billed before the term ends
--   too early            fewer than status_too_early_days into the term
--   ahead / below pace   % of billed more than status_pace_pts above / below % of term
--   on pace              otherwise
-- renewal_risk (a flag on top): under renewal_risk_pct used with renewal_risk_days or fewer left in the term.
CREATE OR REPLACE VIEW n360.v_contract_status AS
WITH (SELECT status_pace_pts FROM n360.settings) AS pace_pts,
     (SELECT status_too_early_days FROM n360.settings) AS early_days,
     (SELECT renewal_risk_pct FROM n360.settings) AS risk_pct,
     (SELECT renewal_risk_days FROM n360.settings) AS risk_days,
     (SELECT decision_window_days FROM n360.settings) AS window_days,
     (SELECT ended_recent_days FROM n360.settings) AS ended_days,
     lines AS (SELECT l.line_key AS lk, l.rule_id AS rid, l.billed IS NULL AS no_billed, ifNull(m.n, 0) = 0 AS no_va
               FROM n360.contract_line AS l
               LEFT JOIN (SELECT line_key, toNullable(count()) AS n FROM n360.line_va GROUP BY line_key) AS m
                      ON m.line_key = l.line_key),
     rules AS (SELECT rule_id AS rid, status AS rstatus FROM n360.rule)
SELECT p.account AS account, a.display_name AS display_name, a.csm_rocketlane AS csm, p.entitlement AS entitlement,
       p.line_key AS line_key, p.pool_id AS pool_id, p.series_key AS series_key, p.label AS label,
       -- customer · line; a pool's name usually repeats the customer ("Bluebird Air: 3 lines ..."): not twice
       if(startsWith(p.label, concat(a.display_name, ':')), trimLeft(substring(p.label, length(a.display_name) + 2)), p.label)
           AS line_label,
       concat(a.display_name, ' · ', line_label) AS full_label,
       p.measure AS measure, p.used AS used, p.billed AS billed,
       p.pct_billed AS pct_billed, p.pct_term AS pct_term, p.start_date AS start_date, p.end_date AS end_date,
       p.data_through AS data_through, dateDiff('day', p.data_through, p.end_date) AS days_left,
       dateDiff('day', p.start_date, p.data_through) + 1 AS days_in, p.term_ended AS term_ended,
       p.cross_date AS cross_date, p.rate_per_day AS rate_per_day,
       -- forecast at term end: the stored forecast's rate carried to the end of the term (never refitted)
       if(p.billed > 0 AND p.rate_per_day IS NOT NULL,
          round(100 * (p.used + p.rate_per_day * greatest(dateDiff('day', p.data_through, p.end_date), 0)) / p.billed, 1),
          NULL) AS forecast_end_pct,
       multiIf(p.measure = 'not implemented', 'not built',
               p.measure = 'not measurable', 'not measured',
               p.entitlement = 'line' AND ln.no_billed, 'billed not recorded',
               p.entitlement = 'line' AND ln.no_va, 'unmapped',
               p.entitlement = 'line' AND ln.rid IS NOT NULL AND ifNull(r.rstatus, '') != 'active', 'under review',
               p.pct_billed IS NULL, 'not read',
               p.pct_billed > 100, 'over billed',
               p.cross_date IS NOT NULL, 'will exceed',
               days_in < early_days, 'too early',
               p.pct_billed - p.pct_term > pace_pts, 'ahead',
               p.pct_term - p.pct_billed > pace_pts, 'below pace',
               'on pace') AS status,
       status IN ('below pace', 'on pace', 'ahead', 'too early') AND p.pct_billed < risk_pct
           AND days_left BETWEEN 0 AND risk_days AS renewal_risk,
       -- urgency order for every sorted list: over, will exceed, renewal risk, below, ahead, on pace, then no reading
       multiIf(status = 'over billed', 1, status = 'will exceed', 2, renewal_risk, 3, status = 'below pace', 4,
               status = 'ahead', 5, status = 'on pace', 6, status = 'too early', 7, status = 'under review', 8,
               status = 'billed not recorded', 9, status = 'unmapped', 10, status = 'not built', 11,
               status = 'not measured', 12, 13) AS status_rank,
       -- what needs a decision: at most 6 words, plus the date it is about
       multiIf(p.term_ended, 'Term ended',
               status = 'over billed', 'Over billed',
               status = 'will exceed', 'Set to exceed billed',
               renewal_risk, concat('Renewal risk: ', toString(round(p.pct_billed)), '% used'),
               days_left BETWEEN 0 AND window_days, 'Term ends soon', '') AS issue,
       multiIf(p.term_ended, p.end_date, status = 'over billed', p.data_through, status = 'will exceed', p.cross_date,
               p.end_date) AS issue_date,
       (p.term_ended AND dateDiff('day', p.end_date, p.data_through) <= ended_days)
           OR status IN ('over billed', 'will exceed') OR renewal_risk AS needs_attention,
       needs_attention OR days_left BETWEEN 0 AND window_days AS decision_due,
       if(p.entitlement = 'pool', concat('/d/n360-customer?var-account=', p.account),
          concat('/d/n360-line?var-account=', p.account, '&var-line=', p.line_key)) AS drill
FROM n360.v_line_pace AS p
INNER JOIN n360.account AS a ON a.account = p.account
LEFT JOIN lines AS ln ON ln.lk = p.line_key
LEFT JOIN rules AS r ON r.rid = ln.rid;   -- no join_use_nulls (25.8 ignores it in a view): unmatched = false / ''

CREATE OR REPLACE VIEW n360.v_status_night AS
-- The contract status as it stood each stored night (from the forecast of record as stored that night), for tiles'
-- "change since" and the status-over-time chart. Same rules as v_contract_status; states that do not move night to
-- night (not built, not measured, billed not recorded, unmapped, under review) are taken as they are today.
-- Lines with no forecast (no use, no billed) have no row.
WITH (SELECT status_pace_pts FROM n360.settings) AS pace_pts,
     (SELECT status_too_early_days FROM n360.settings) AS early_days,
     (SELECT renewal_risk_pct FROM n360.settings) AS risk_pct,
     (SELECT renewal_risk_days FROM n360.settings) AS risk_days
SELECT h.data_date AS data_date, h.data_date - 1 AS data_through, s.account AS account, s.series_key AS series_key,
       s.entitlement AS entitlement, round(100 * h.consumed / h.billed, 2) AS pct_billed,
       round(100 * least(greatest(dateDiff('day', s.start_date, h.data_date - 1) + 1, 0), dateDiff('day', s.start_date, s.end_date) + 1)
             / (dateDiff('day', s.start_date, s.end_date) + 1), 1) AS pct_term,
       dateDiff('day', h.data_date - 1, s.end_date) AS days_left,
       multiIf(s.status IN ('not built', 'not measured', 'billed not recorded', 'unmapped', 'under review'), s.status,
               h.data_date - 1 < s.start_date, 'not started',
               pct_billed > 100, 'over billed',
               h.cross_date IS NOT NULL, 'will exceed',
               dateDiff('day', s.start_date, h.data_date - 1) + 1 < early_days, 'too early',
               pct_billed - pct_term > pace_pts, 'ahead',
               pct_term - pct_billed > pace_pts, 'below pace',
               'on pace') AS status,
       status IN ('below pace', 'on pace', 'ahead', 'too early') AND pct_billed < risk_pct
           AND days_left BETWEEN 0 AND risk_days AS renewal_risk
FROM n360.forecast_history AS h
INNER JOIN n360.v_contract_status AS s ON s.series_key = h.series_key
WHERE h.billed > 0;

CREATE OR REPLACE VIEW n360.v_va_change AS
-- Each VA: the last 30 days against the 30 before (conversations, containment), and the flags health and attention
-- read. A window with any day not read is NULL (not read), never a lower number.
WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS dt,
     (SELECT health_convs_down_pct FROM n360.settings) AS down_pct,
     (SELECT health_containment_down_pts FROM n360.settings) AS cont_pts,
     (SELECT attention_va_min_convs FROM n360.settings) AS min_convs,
     (SELECT attention_va_drop_pct FROM n360.settings) AS drop_pct
SELECT v.account AS account, v.bot_id AS bot_id, concat(v.va_name, ' (', arrayStringConcat(v.channels, ', '), ')') AS va,
       v.va_name AS va_name, arrayStringConcat(v.channels, ', ') AS channel,
       if(countIf(d.day > dt - 30) < 30 OR countIf(d.day > dt - 30 AND d.conversations IS NULL) > 0, NULL,
          sumIf(d.conversations, d.day > dt - 30)) AS convs_30,
       if(countIf(d.day <= dt - 30 AND d.conversations IS NULL) > 0 OR countIf(d.day <= dt - 30) < 30, NULL,
          sumIf(d.conversations, d.day <= dt - 30)) AS convs_prev_30,
       -- divisor floored at 1 inside the branch: ClickHouse computes both branches, and an inf left under a NULL breaks a later toInt32
       if(convs_prev_30 > 0, round(100 * (convs_30 - convs_prev_30) / greatest(ifNull(convs_prev_30, 0), 1), 1), NULL) AS convs_change_pct,
       if(sumIf(d.engaged, d.day > dt - 30 AND d.handoffs IS NOT NULL) > 0,
          round(100 * (1 - sumIf(d.handoffs, d.day > dt - 30 AND d.handoffs IS NOT NULL)
                         / sumIf(d.engaged, d.day > dt - 30 AND d.handoffs IS NOT NULL)), 1), NULL) AS containment_30,
       if(sumIf(d.engaged, d.day <= dt - 30 AND d.handoffs IS NOT NULL) > 0,
          round(100 * (1 - sumIf(d.handoffs, d.day <= dt - 30 AND d.handoffs IS NOT NULL)
                         / sumIf(d.engaged, d.day <= dt - 30 AND d.handoffs IS NOT NULL)), 1), NULL) AS containment_prev_30,
       round(containment_30 - containment_prev_30, 1) AS containment_change_pts,
       ifNull(convs_change_pct <= -down_pct, false) AS convs_down,
       ifNull(containment_change_pts <= -cont_pts, false) AS containment_down,
       ifNull(convs_prev_30 >= min_convs AND convs_change_pct <= -drop_pct, false) AS attention_drop,
       dt AS data_through
FROM n360.va AS v
LEFT JOIN n360.va_day AS d ON d.bot_id = v.bot_id AND d.day > dt - 60 AND d.day <= dt
GROUP BY v.account, v.bot_id, v.va_name, v.channels;   -- a VA with no days joins one empty row: day 1970, counts NULL

CREATE OR REPLACE VIEW n360.v_needs_attention AS
-- One row per thing needing attention (v2 rule, PROPOSED thresholds in n360.settings): a line or pool over billed,
-- set to exceed, at renewal risk, or whose term ended recently; or a busy VA whose conversations fell sharply.
SELECT account, display_name, csm, 'line' AS kind, full_label AS subject, issue, issue_date, status, status_rank AS rank, drill
FROM n360.v_contract_status WHERE needs_attention
UNION ALL
SELECT c.account, a.display_name, a.csm_rocketlane, 'va', c.va,
       concat('Conversations down ', toString(toInt32(round(-c.convs_change_pct))), '%'), c.data_through, 'conversations down', 3,
       concat('/d/n360-va?var-account=', c.account, '&var-va=', toString(c.bot_id))
FROM n360.v_va_change AS c INNER JOIN n360.account AS a ON a.account = c.account
WHERE c.attention_drop;

CREATE OR REPLACE VIEW n360.v_freshness AS
-- The freshness stamp on every dashboard: data through, when it was loaded, and whether it is stale (amber).
WITH (SELECT stamp_stale_days FROM n360.settings) AS stale_days
SELECT toDate(r.window_end) AS data_through, r.loaded_at AS loaded_at,
       dateDiff('day', data_through, today()) AS days_old, days_old > stale_days AS stale,
       concat('Usage to ', toString(toDayOfMonth(data_through)), ' ', formatDateTime(data_through, '%b %Y'),
              ' · loaded ', toString(toDayOfMonth(toDate(r.loaded_at))), ' ', formatDateTime(r.loaded_at, '%b')) AS stamp
FROM (SELECT window_end, loaded_at FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC, data_date DESC LIMIT 1) AS r;

-- ───────────────────────── adoption and health (dashboard review; Phase 2 tables, PROPOSED) ───────────────────
CREATE OR REPLACE VIEW n360.v_feature_use AS
-- Target features per customer, used in the last 30 days (PROPOSED list; the v1.1 page also counts Testing,
-- Calibration and Recipes, which no export carries yet). One row per customer and feature; used NULL = not reported
-- (no rows for that customer in the source table).
WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS dt,
     src AS (
        -- V8 playbooks, Cortex, Explorer: the window totals the pipeline reads (feature_window, 27 Nov. The export
        -- names them 'Authoring tool' and 'Analytics assistant'; shown by their product names, Cortex and Explorer.
        -- 2026), the same figures as the v1.1 Adoption view; rows with amount NULL (not read) are left out
        SELECT account, transform(feature, ['Authoring tool', 'Analytics assistant'], ['Cortex', 'Explorer'], feature) AS feature,
               assumeNotNull(max(amount)) > 0 AS u FROM n360.feature_window
            WHERE amount IS NOT NULL GROUP BY account, feature
        UNION ALL SELECT account, 'MCP servers', sum(mcp_servers_live) > 0 FROM n360.v_va_config_latest GROUP BY account
        UNION ALL SELECT account, 'Supervisory rules', sum(supervisory_rules_on) > 0 FROM n360.v_va_config_latest GROUP BY account),
     feats AS (SELECT arrayJoin(['V8 playbooks', 'Cortex', 'Explorer', 'MCP servers', 'Supervisory rules']) AS feature)
SELECT a.account AS account, f.feature AS feature,
       if(s.found = 1, toNullable(s.u), NULL) AS used
FROM n360.account AS a CROSS JOIN feats AS f
LEFT JOIN (SELECT account, feature, toBool(max(u)) AS u, 1 AS found FROM src GROUP BY account, feature) AS s
       ON s.account = a.account AND s.feature = f.feature;

CREATE OR REPLACE VIEW n360.v_customer_notes AS
-- Notes pinned to a customer's header (v2 "pinned customer notes"), written from the contract data: pools, signed SOW
-- terms, contract rules (live or pending), lines with no reading, ended terms, VAs on no contract. Plain words.
SELECT account, kind, note, o FROM (
    SELECT p.account AS account, 'pool' AS kind,
           concat('Pooled entitlement: ', toString(length(p.line_keys)), ' lines share one billed amount of ',
                  formatReadableQuantity(p.billed), '. Use is compared with billed only at the pool.') AS note, 1 AS o
    FROM n360.pool AS p
    UNION ALL
    SELECT l.account, 'sow', concat(l.channel, ': a signed SOW sets the contract unit (', l.unit, ').'), 2
    FROM n360.contract_line AS l WHERE l.unit != ''
    UNION ALL
    SELECT l.account, 'rule', concat(l.channel, ': measured by a contract rule, ', r.label,
                                     if(r.status = 'active', '.', ' (not live yet: measured on engaged until it is).')), 3
    FROM n360.contract_line AS l INNER JOIN n360.rule AS r ON r.rule_id = l.rule_id
    UNION ALL
    SELECT c.account, 'no reading', concat(c.line_label, ': ', multiIf(
               c.status = 'not built', 'contracted, nothing implemented yet.',
               c.status = 'not measured', 'the contract unit is one the platform does not count.',
               c.status = 'billed not recorded', 'no billed amount on the entitlement sheet.',
               c.status = 'unmapped', 'no VA is mapped to this line yet.',
               'no use read yet.')), 4
    FROM n360.v_contract_status AS c WHERE c.status IN ('not built', 'not measured', 'billed not recorded', 'unmapped', 'not read')
    UNION ALL
    SELECT c.account, 'ended', concat(c.line_label, ': the term ended on ', toString(toDayOfMonth(c.end_date)), ' ',
                                      formatDateTime(c.end_date, '%b %Y'), '.'), 5
    FROM n360.v_contract_status AS c WHERE c.term_ended
    UNION ALL
    SELECT v.account, 'unbilled', concat(v.va_name, ' is on no contract line (unbilled).'), 6
    FROM n360.va AS v WHERE NOT v.on_contract
);

-- ───────────────────────── Salesforce (25 Nov 2026): read-only copy, loaded by code/salesforce/load_salesforce.py ─────────
-- What sf_pull.py reads is set in code/salesforce/salesforce.json (standard objects). Each pull replaces these tables
-- whole (stage, check, swap, like the nightly load); sf_runs logs every pull. Linked to N360 accounts by name
-- (v_sf_account), with overrides in salesforce.json where names differ.
CREATE TABLE IF NOT EXISTS n360.sf_account
(
    sf_account_id       String,
    name                String,
    owner               Nullable(String),
    type                Nullable(String),
    last_activity_date  Nullable(Date),
    account_hint        String,                                 -- N360 account key from salesforce.json overrides ('' = match by name)
    run_id              UUID
)
ENGINE = MergeTree ORDER BY sf_account_id;

CREATE TABLE IF NOT EXISTS n360.sf_opportunity
(
    opp_id              String,
    sf_account_id       Nullable(String),
    name                String,
    type                Nullable(String),
    stage               Nullable(String),
    forecast_category   Nullable(String),
    close_date          Nullable(Date),
    amount              Nullable(Float64),
    is_closed           Bool,
    is_won              Bool,
    is_renewal          Bool,                                   -- type in salesforce.json renewal_opportunity_types
    next_step           Nullable(String),
    owner               Nullable(String),
    last_activity_date  Nullable(Date),
    run_id              UUID
)
ENGINE = MergeTree ORDER BY opp_id;

CREATE TABLE IF NOT EXISTS n360.sf_contract
(
    contract_id         String,
    sf_account_id       Nullable(String),
    number              Nullable(String),
    status              Nullable(String),
    start_date          Nullable(Date),
    end_date            Nullable(Date),
    term_months         Nullable(UInt16),
    run_id              UUID
)
ENGINE = MergeTree ORDER BY contract_id;

CREATE TABLE IF NOT EXISTS n360.sf_activity
(
    activity_id         String,
    sf_account_id       Nullable(String),
    kind                Enum8('task' = 1, 'event' = 2),
    activity_date       Nullable(Date),
    type                Nullable(String),
    subtype             Nullable(String),
    subject             Nullable(String),
    status              Nullable(String),
    owner               Nullable(String),
    is_closed           Bool,
    is_contact          Bool,                                   -- a past event, or a closed call/email task (salesforce.json)
    is_executive        Bool,                                   -- a contact whose subject names an executive meeting (keywords)
    run_id              UUID
)
ENGINE = MergeTree ORDER BY activity_id;

CREATE TABLE IF NOT EXISTS n360.sf_runs
(
    run_id              UUID,
    pulled_at           DateTime('UTC'),
    loaded_at           DateTime('UTC') DEFAULT now(),
    status              Enum8('loaded' = 1, 'failed_checks' = 2),
    checks_failed       Array(String),
    row_counts          Map(String, UInt64),
    unmatched_accounts  Array(String)                           -- Salesforce accounts with no N360 account (by name)
)
ENGINE = MergeTree ORDER BY loaded_at;

CREATE OR REPLACE VIEW n360.v_sf_account AS
-- Salesforce account -> N360 account: the override in salesforce.json, else the same name (case-insensitive) as the N360
-- account key or display name. account = '' when not linked (listed in sf_runs.unmatched_accounts).
SELECT s.sf_account_id AS sf_account_id, s.name AS sf_name, s.owner AS owner,
       multiIf(s.account_hint != '', s.account_hint, m.account != '', m.account, '') AS account
FROM n360.sf_account AS s
LEFT JOIN (SELECT lower(account) AS k, account FROM n360.account
           UNION DISTINCT SELECT lower(display_name), account FROM n360.account) AS m ON m.k = lower(s.name);


-- ───────────────────────── risk (25 Nov 2026): register, CSM rating, signals, history ─────────────────────────
-- Written only by code/risk_register.py (CSMs); never swapped by a load. Append-only: the newest row per risk wins.
CREATE TABLE IF NOT EXISTS n360.risk_register
(
    risk_id     String,
    account     LowCardinality(String),
    reason      LowCardinality(String),                    -- one of risk_register.py REASONS
    impact      LowCardinality(String),                    -- churn | downsell | contraction
    owner       LowCardinality(String),
    due         Nullable(Date),
    status      Enum8('open' = 1, 'closed' = 2),
    outcome     LowCardinality(String),                    -- when closed: saved | churned | downsold | no action | invalid
    note        String,
    by          LowCardinality(String),
    at          DateTime64(3, 'UTC') DEFAULT now64(3),
    outcome_date Nullable(Date)                            -- the day the outcome happened, when that is not the day it was closed (close --date)
)
ENGINE = MergeTree ORDER BY (risk_id, at);
ALTER TABLE n360.risk_register ADD COLUMN IF NOT EXISTS outcome_date Nullable(Date);

CREATE TABLE IF NOT EXISTS n360.risk_pulse
(
    account     LowCardinality(String),
    level       Enum8('green' = 1, 'amber' = 2, 'red' = 3),
    note        String,
    by          LowCardinality(String),
    at          DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree ORDER BY (account, at);

CREATE TABLE IF NOT EXISTS n360.risk_history          -- every signal as it stood each loaded night (load.py copies it)
(
    data_date   Date,
    account     LowCardinality(String),
    signal      LowCardinality(String),
    lens        LowCardinality(String),
    level       LowCardinality(String)
)
ENGINE = MergeTree PARTITION BY toYYYYMM(data_date) ORDER BY (account, signal, data_date);

CREATE OR REPLACE VIEW n360.v_risk_register AS
-- Each risk as it stands now (its newest row), with age and whether it is past due. (Aliases differ from the column
-- names inside: ClickHouse would read a reused name as the aggregate.)
SELECT risk_id, x_account AS account, x_reason AS reason, x_impact AS impact, x_owner AS owner, x_due AS due,
       x_status AS status, x_outcome AS outcome, x_note AS note, x_opened AS opened_at, x_updated AS updated_at,
       dateDiff('day', toDate(x_opened), today()) AS age_days, x_status = 'open' AND x_due < today() AS overdue,
       -- when a closed risk's outcome happened: the date given at closing, else the day it was FIRST closed. Never the
       -- last edit: a note added months later must not move a churn to a different renewal.
       if(x_status = 'closed', coalesce(x_outcome_date, toDate(x_closed)), NULL) AS outcome_date
FROM (SELECT risk_id, argMax(account, at) AS x_account, argMax(reason, at) AS x_reason, argMax(impact, at) AS x_impact,
             argMax(owner, at) AS x_owner, argMax(due, at) AS x_due, argMax(status, at) AS x_status,
             argMax(outcome, at) AS x_outcome, argMax(note, at) AS x_note, min(at) AS x_opened, max(at) AS x_updated,
             minIf(at, status = 'closed') AS x_closed, argMaxIf(outcome_date, at, outcome_date IS NOT NULL) AS x_outcome_date
      FROM n360.risk_register GROUP BY risk_id);

CREATE OR REPLACE VIEW n360.v_risk_pulse AS
-- Each customer's newest CSM rating, and whether it has expired (n360.settings risk_pulse_valid_days).
SELECT account, x_level AS level, x_note AS note, x_by AS by, x_at AS at,
       dateDiff('day', toDate(x_at), today()) > (SELECT risk_pulse_valid_days FROM n360.settings) AS expired
FROM (SELECT account, argMax(level, at) AS x_level, argMax(note, at) AS x_note, argMax(by, at) AS x_by, max(at) AS x_at
      FROM n360.risk_pulse GROUP BY account);

CREATE OR REPLACE VIEW n360.v_risk_signal AS
-- One row per customer and signal: level (risk | watch | fine | not reported) and the reason in words. The rules are
-- PROPOSED (n360.settings, docs/17). "Not reported" = the source has nothing for this customer: grey, never "fine".
-- Signals and lenses: CSM = commercial and relationship; CA = customer architect (technical).
WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS dt,
     (SELECT decision_window_days FROM n360.settings) AS window_days,
     (SELECT risk_contact_watch_days FROM n360.settings) AS contact_watch,
     (SELECT risk_contact_risk_days FROM n360.settings) AS contact_risk,
     (SELECT risk_exec_watch_days FROM n360.settings) AS exec_watch,
     (SELECT risk_safe_forecast_categories FROM n360.settings) AS safe_cats,
     (SELECT risk_lost_forecast_categories FROM n360.settings) AS lost_cats,
     (SELECT risk_renewal_lost_days FROM n360.settings) AS lost_days,
     (SELECT risk_volume_watch_pct FROM n360.settings) AS vol_watch,
     (SELECT risk_volume_risk_pct FROM n360.settings) AS vol_risk,
     (SELECT risk_concentration_pct FROM n360.settings) AS conc_pct,
     (SELECT risk_stale_config_weeks FROM n360.settings) AS stale_weeks,
     (SELECT risk_ticket_age_days FROM n360.settings) AS ticket_age,
     (SELECT risk_open_tickets FROM n360.settings) AS open_tickets,
     (SELECT risk_building_drop_pct FROM n360.settings) AS build_drop,
     (SELECT engine_current_major FROM n360.settings) AS current_major,
     (SELECT attention_va_min_convs FROM n360.settings) AS busy_convs,
     (SELECT containment_low_pct FROM n360.settings) AS low_cont,
     (SELECT count() > 0 FROM n360.sf_account) AS sf_loaded,
     (SELECT count() > 0 FROM n360.ps_ticket) AS tickets_loaded,
     -- contract pace (from v_contract_status)
     pace AS (SELECT account, 1 AS found, countIf(renewal_risk) AS n_risk, countIf(status = 'below pace' AND NOT renewal_risk) AS n_below,
                     countIf(pct_billed IS NOT NULL) AS n_measured,
                     argMinIf(concat(line_label, ' at renewal risk: ', toString(round(pct_billed)), '% used, ', toString(days_left), ' days left'),
                              days_left, renewal_risk) AS risk_reason,
                     argMinIf(concat(line_label, ': ', toString(round(pct_billed)), '% used, ', toString(round(pct_term)), '% of the term gone'),
                              pct_billed - pct_term, status = 'below pace') AS below_reason,
                     countIf(status IN ('over billed', 'will exceed')) AS n_expansion,
                     minIf(toNullable(days_left), days_left >= 0) AS next_end_days
              FROM n360.v_contract_status GROUP BY account),
     -- Salesforce renewals and contact
     sfa AS (SELECT sf_account_id, account FROM n360.v_sf_account WHERE account != ''),
     ren AS (SELECT sfa.account AS account, 1 AS found,
                    argMinIf(tuple(o.close_date, ifNull(o.forecast_category, ''), ifNull(o.stage, ''), ifNull(o.amount, 0)),
                             o.close_date, NOT o.is_closed AND o.close_date >= dt - 30) AS nxt,
                    countIf(NOT o.is_closed AND o.close_date >= dt - 30) AS n_open,
                    maxIf(o.close_date, o.is_closed AND NOT o.is_won AND o.close_date >= dt - lost_days) AS lost_date,
                    countIf(o.is_closed AND NOT o.is_won AND o.close_date >= dt - lost_days) AS n_lost,
                    maxIf(o.close_date, o.is_closed AND o.is_won AND o.close_date >= dt - lost_days) AS won_date,
                    countIf(o.is_closed AND o.is_won AND o.close_date >= dt - lost_days) AS n_won
             FROM sfa LEFT JOIN (SELECT * FROM n360.sf_opportunity WHERE is_renewal) AS o ON o.sf_account_id = sfa.sf_account_id
             GROUP BY sfa.account),
     con AS (SELECT sfa.account AS account, 1 AS found, maxIf(a.activity_date, a.is_contact) AS last_contact,
                    maxIf(a.activity_date, a.is_executive) AS last_exec, countIf(a.is_contact) AS n_contact,
                    countIf(a.is_executive) AS n_exec
             FROM sfa LEFT JOIN n360.sf_activity AS a ON a.sf_account_id = sfa.sf_account_id GROUP BY sfa.account),
     -- usage
     vol AS (SELECT account, 1 AS found, countIf(convs_30 IS NULL OR convs_prev_30 IS NULL) AS n_unread,
                    sum(ifNull(convs_30, 0)) AS c30, sum(ifNull(convs_prev_30, 0)) AS cp30,
                    max(ifNull(convs_30, 0)) AS top_va, argMax(va, ifNull(convs_30, 0)) AS top_va_name, count() AS n_vas,
                    countIf(containment_down) AS n_cont_down, argMinIf(concat(va, ': containment down ', toString(abs(containment_change_pts)), ' points'),
                              containment_change_pts, containment_down) AS cont_reason,
                    countIf(containment_30 IS NOT NULL) AS n_cont
             FROM n360.v_va_change GROUP BY account),
     cont2 AS (SELECT account, count() AS n, any(subject) AS va FROM n360.v_anomaly_v2
               WHERE signal = 'containment drop' AND period > dt - 14 GROUP BY account),
     flags AS (SELECT a.account AS account, countIf(a.kind = 'went silent') AS n_silent, countIf(a.kind = 'drop') AS n_drop,
                      anyIf(a.va_name, a.kind = 'went silent') AS silent_va, anyIf(a.va_name, a.kind = 'drop') AS drop_va
               FROM n360.v_va_day_anomaly AS a WHERE NOT a.provisional AND a.day >= dt - 9 GROUP BY a.account),
     bill AS (SELECT account, sum(n_pace) AS n_pace, sum(n_gap) AS n_gap FROM (
                 SELECT account, count() AS n_pace, 0 AS n_gap FROM n360.v_forecast_change
                 WHERE moved_earlier AND data_date > (SELECT max(data_date) FROM n360.forecast_history) - 7 GROUP BY account
                 UNION ALL SELECT account, 0, count() FROM n360.v_anomaly_v2 WHERE signal = 'measure gap' AND period > dt - 14
                 GROUP BY account) GROUP BY account),
     hyg AS (SELECT account, countIf(status IN ('billed not recorded', 'unmapped', 'under review', 'not read')) AS n_gap,
                    arrayStringConcat(arrayDistinct(groupArrayIf(status, status IN ('billed not recorded', 'unmapped', 'under review', 'not read'))), ', ') AS what
             FROM n360.v_contract_status GROUP BY account),
     -- CSM view
     reg AS (SELECT account, countIf(status = 'open' AND impact = 'churn') AS n_churn, countIf(status = 'open') AS n_open,
                    anyIf(concat(reason, ' (', impact, ')'), status = 'open') AS what
             FROM n360.v_risk_register GROUP BY account),
     pul AS (SELECT account, toString(level) AS level, expired, note, toDate(at) AS d FROM n360.v_risk_pulse),
     -- configuration (Phase 2)
     cfgchg AS (SELECT bot_id, max(if(changed, snapshot_date, toDate(0))) AS last_change, min(snapshot_date) AS first_read,
                       max(snapshot_date) AS last_read
                FROM (SELECT bot_id, snapshot_date,
                             (engine_version, v8_playbooks, legacy_intents, mcp_servers_live, supervisory_rules_on)
                               != lagInFrame((engine_version, v8_playbooks, legacy_intents, mcp_servers_live, supervisory_rules_on))
                                  OVER (PARTITION BY bot_id ORDER BY snapshot_date ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
                               AND row_number() OVER (PARTITION BY bot_id ORDER BY snapshot_date) > 1 AS changed
                      FROM n360.va_config_snapshot) GROUP BY bot_id),
     eng AS (SELECT g.account AS account, 1 AS found,
                    countIf(NOT g.current_engine AND ifNull(c.convs_30, 0) >= busy_convs) AS n_busy_old,
                    countIf(NOT g.current_engine) AS n_old,
                    argMaxIf(concat(c.va, ' on ', g.engine_version), ifNull(c.convs_30, 0), NOT g.current_engine) AS old_va,
                    countIf(g.mcp_servers_live = 0 AND ifNull(c.containment_30, 100) < low_cont) AS n_capped,
                    anyIf(c.va, g.mcp_servers_live = 0 AND ifNull(c.containment_30, 100) < low_cont) AS capped_va,
                    max(greatest(ch.last_change, ch.first_read)) AS newest_change, max(ch.last_read) AS newest_read
             FROM n360.v_va_config_latest AS g
             LEFT JOIN n360.v_va_change AS c ON c.bot_id = g.bot_id
             LEFT JOIN cfgchg AS ch ON ch.bot_id = g.bot_id
             GROUP BY g.account),
     -- Cortex: user_type 'unknown' since 27 Nov 2026 (no user id in the source), so every row but staff counts
     bld AS (SELECT account, 1 AS found, sumIf(n, day > dt - 30) AS b30, sumIf(n, day <= dt - 30 AND day > dt - 60) AS bp30 FROM (
                 SELECT account, day, ifNull(authoring_sessions, 0) AS n FROM n360.cortex_day WHERE user_type != 'staff'
                 UNION ALL SELECT account, day, ifNull(questions, 0) FROM n360.explorer_day WHERE user_type = 'customer')
             GROUP BY account),
     idle AS (SELECT account, count() AS n, any(playbook) AS pb FROM (
                 SELECT account, bot_id, playbook FROM n360.playbook_day WHERE day > dt - 31
                 GROUP BY account, bot_id, playbook HAVING sum(executions) = 0) GROUP BY account),
     tix AS (SELECT account, 1 AS found, countIf(status = 'open') AS n_open,
                    dateDiff('day', minIf(opened, status = 'open'), dt) AS oldest
             FROM n360.ps_ticket GROUP BY account)
SELECT account, signal, lens, title, level, reason,
       multiIf(level = 'risk', 3, level = 'watch', 2, level = 'fine', 1, 0) AS level_rank
FROM (
    SELECT a.account AS account, 'pace' AS signal, 'CSM' AS lens, 'Contract pace' AS title,
           multiIf(p.found != 1 OR p.n_measured = 0 AND p.n_risk = 0, 'not reported', p.n_risk > 0, 'risk', p.n_below > 0, 'watch', 'fine') AS level,
           multiIf(p.found != 1, 'No contract line (unbilled)', p.n_risk > 0, p.risk_reason, p.n_below > 0,
                   concat(p.below_reason, if(p.n_below > 1, concat(' (+', toString(p.n_below - 1), ' more)'), '')),
                   p.n_measured = 0, 'No line has a reading yet',
                   p.n_expansion > 0, concat('On pace or above · ', toString(p.n_expansion), ' over or will exceed (expansion)'), 'On pace') AS reason
    FROM n360.account AS a LEFT JOIN pace AS p ON p.account = a.account
    UNION ALL
    SELECT a.account, 'renewal', 'CSM', 'Renewal',
           multiIf(r.found != 1, 'not reported', r.n_lost > 0, 'risk',
                   r.n_open > 0 AND has(lost_cats, r.nxt.2), 'risk',
                   r.n_open > 0 AND dateDiff('day', dt, r.nxt.1) <= window_days AND NOT has(safe_cats, r.nxt.2), 'watch',
                   r.n_open = 0 AND r.n_won = 0 AND p.found = 1 AND p.next_end_days <= window_days, 'watch', 'fine'),
           multiIf(r.found != 1, if(sf_loaded, 'Not linked to a Salesforce account', 'Salesforce not loaded'),
                   r.n_lost > 0, concat('Renewal lost on ', toString(toDayOfMonth(r.lost_date)), ' ', formatDateTime(r.lost_date, '%b %Y')),
                   r.n_open > 0, concat('Renewal ', toString(toDayOfMonth(r.nxt.1)), ' ', formatDateTime(r.nxt.1, '%b %Y'), ' · ',
                                        if(r.nxt.2 = '', 'no forecast category', r.nxt.2), ' · ', r.nxt.3),
                   r.n_won > 0, concat('Renewed: won on ', toString(toDayOfMonth(r.won_date)), ' ', formatDateTime(r.won_date, '%b %Y')),
                   p.found = 1 AND p.next_end_days <= window_days,
                   concat('Term ends in ', toString(p.next_end_days), ' days and no renewal opportunity'),
                   'No open renewal opportunity')
    FROM n360.account AS a LEFT JOIN ren AS r ON r.account = a.account LEFT JOIN pace AS p ON p.account = a.account
    UNION ALL
    SELECT a.account, 'volume', 'CSM', 'Conversations',
           multiIf(v.found != 1 OR v.n_unread > 0 OR v.cp30 = 0, 'not reported',
                   100 * (v.c30 - v.cp30) / v.cp30 <= -vol_risk, 'risk', 100 * (v.c30 - v.cp30) / v.cp30 <= -vol_watch, 'watch', 'fine'),
           multiIf(v.found != 1, 'No VA', v.n_unread > 0 OR v.cp30 = 0, 'Not every day read in the last 60 days',
                   concat(if(v.c30 >= v.cp30, 'Up ', 'Down '), toString(abs(toInt32(round(100 * (v.c30 - v.cp30) / v.cp30)))),
                          '% on the 30 days before'))
    FROM n360.account AS a LEFT JOIN vol AS v ON v.account = a.account
    UNION ALL
    SELECT a.account, 'contact', 'CSM', 'Last contact',
           multiIf(c.found != 1, 'not reported', c.n_contact = 0, 'risk', dateDiff('day', c.last_contact, dt) > contact_risk, 'risk',
                   dateDiff('day', c.last_contact, dt) > contact_watch OR c.n_exec = 0
                       OR dateDiff('day', c.last_exec, dt) > exec_watch, 'watch', 'fine'),
           multiIf(c.found != 1, if(sf_loaded, 'Not linked to a Salesforce account', 'Salesforce not loaded'),
                   c.n_contact = 0, 'No contact recorded in a year',
                   concat('Last contact ', toString(dateDiff('day', c.last_contact, dt)), ' days ago · ',
                          if(c.n_exec = 0, 'no executive meeting in a year',
                             concat('executive meeting ', toString(dateDiff('day', c.last_exec, dt)), ' days ago'))))
    FROM n360.account AS a LEFT JOIN con AS c ON c.account = a.account
    UNION ALL
    SELECT a.account, 'billing', 'CSM', 'Billing stability',
           if(ifNull(b.n_pace, 0) + ifNull(b.n_gap, 0) + ifNull(h.n_gap, 0) > 0, 'watch', 'fine'),
           arrayStringConcat(arrayFilter(x -> x != '', [
               if(ifNull(b.n_pace, 0) > 0, 'crossing date moved 14+ days earlier this week', ''),
               if(ifNull(b.n_gap, 0) > 0, 'contract measure drifted from engaged', ''),
               if(ifNull(h.n_gap, 0) > 0, concat('contract data incomplete: ', h.what), ''),
               if(ifNull(b.n_pace, 0) + ifNull(b.n_gap, 0) + ifNull(h.n_gap, 0) = 0, 'Stable', '')]), ' · ')
    FROM n360.account AS a LEFT JOIN bill AS b ON b.account = a.account LEFT JOIN hyg AS h ON h.account = a.account
    UNION ALL
    SELECT a.account, 'csm', 'CSM', 'CSM view',
           multiIf(ifNull(g.n_churn, 0) > 0 OR (u.level = 'red' AND NOT u.expired), 'risk',
                   ifNull(g.n_open, 0) > 0 OR (u.level = 'amber' AND NOT u.expired), 'watch',
                   u.level = 'green' AND NOT u.expired, 'fine', 'not reported'),
           arrayStringConcat(arrayFilter(x -> x != '', [
               if(ifNull(g.n_open, 0) > 0, concat(toString(g.n_open), ' open in the register: ', g.what), ''),
               multiIf(u.level = '', 'No CSM rating yet', u.expired, concat('Rating expired (', toString(u.d), ')'),
                       concat('Rated ', u.level, if(u.note = '', '', concat(': ', u.note))))]), ' · ')
    FROM n360.account AS a LEFT JOIN reg AS g ON g.account = a.account LEFT JOIN pul AS u ON u.account = a.account
    UNION ALL
    SELECT a.account, 'va_health', 'CA', 'VA health',
           multiIf(v.found != 1, 'not reported', ifNull(f.n_silent, 0) > 0, 'risk', ifNull(f.n_drop, 0) >= 2, 'watch', 'fine'),
           multiIf(v.found != 1, 'No VA', ifNull(f.n_silent, 0) > 0, concat(f.silent_va, ' went silent (settled)'),
                   ifNull(f.n_drop, 0) >= 2, concat(toString(f.n_drop), ' drops in the 7 newest settled days (', f.drop_va, ')'),
                   'No silence, under 2 drops')
    FROM n360.account AS a LEFT JOIN vol AS v ON v.account = a.account LEFT JOIN flags AS f ON f.account = a.account
    UNION ALL
    SELECT a.account, 'containment', 'CA', 'Containment',
           multiIf(v.found != 1 OR v.n_cont = 0, 'not reported', ifNull(k.n, 0) > 0, 'risk', v.n_cont_down > 0, 'watch', 'fine'),
           multiIf(v.found != 1, 'No VA', v.n_cont = 0, 'No handoff data', ifNull(k.n, 0) > 0,
                   concat(k.va, ': weekly containment far below its usual level'), v.n_cont_down > 0, v.cont_reason, 'Steady')
    FROM n360.account AS a LEFT JOIN vol AS v ON v.account = a.account LEFT JOIN cont2 AS k ON k.account = a.account
    UNION ALL
    SELECT a.account, 'concentration', 'CA', 'Concentration',
           multiIf(v.found != 1 OR v.c30 = 0, 'not reported', v.n_vas >= 2 AND 100 * v.top_va / v.c30 >= conc_pct, 'watch', 'fine'),
           multiIf(v.found != 1, 'No VA', v.c30 = 0, 'No conversations in 30 days', v.n_vas = 1, 'One VA',
                   concat(v.top_va_name, ' carries ', toString(toInt32(round(100 * v.top_va / v.c30))), '% of ',
                          toString(v.n_vas), ' VAs\' conversations'))
    FROM n360.account AS a LEFT JOIN vol AS v ON v.account = a.account
    UNION ALL
    SELECT a.account, 'engine', 'CA', 'Engine and configuration',
           multiIf(e.found != 1, 'not reported', e.n_busy_old > 0, 'risk',
                   e.n_old > 0 OR e.n_capped > 0 OR dateDiff('week', e.newest_change, e.newest_read) >= stale_weeks, 'watch', 'fine'),
           if(e.found != 1, 'No configuration read', arrayStringConcat(arrayFilter(x -> x != '', [
               if(e.n_old > 0, concat(e.old_va, if(e.n_busy_old > 0, ' (busy) is below V8', ' is below V8')), ''),
               if(e.n_capped > 0, concat(e.capped_va, ': no MCP servers and containment under 50%'), ''),
               if(dateDiff('week', e.newest_change, e.newest_read) >= stale_weeks,
                  concat('no configuration change in ', toString(dateDiff('week', e.newest_change, e.newest_read)), ' weeks'), ''),
               if(e.n_old = 0 AND e.n_capped = 0 AND dateDiff('week', e.newest_change, e.newest_read) < stale_weeks,
                  'Every VA on V8 or later, configuration changing', '')]), ' · '))
    FROM n360.account AS a LEFT JOIN eng AS e ON e.account = a.account
    UNION ALL
    SELECT a.account, 'building', 'CA', 'Customer building',
           multiIf(bd.found != 1, 'not reported', bd.b30 = 0, 'watch',
                   bd.bp30 > 0 AND 100 * (bd.b30 - bd.bp30) / bd.bp30 <= -build_drop, 'watch', ifNull(i.n, 0) > 0, 'watch', 'fine'),
           if(bd.found != 1, 'No authoring or assistant data', arrayStringConcat(arrayFilter(x -> x != '', [
               if(bd.b30 = 0, 'no authoring or assistant use by customer users in 30 days', ''),
               if(bd.b30 > 0 AND bd.bp30 > 0 AND 100 * (bd.b30 - bd.bp30) / bd.bp30 <= -build_drop,
                  concat('authoring and assistant use down ', toString(toInt32(round(-100 * (bd.b30 - bd.bp30) / bd.bp30))), '%'), ''),
               if(ifNull(i.n, 0) > 0, concat(toString(i.n), ' playbook built, not run (', i.pb, ')'), ''),
               if(bd.b30 > 0 AND NOT (bd.bp30 > 0 AND 100 * (bd.b30 - bd.bp30) / bd.bp30 <= -build_drop) AND ifNull(i.n, 0) = 0,
                  concat(toString(bd.b30), ' sessions and questions in 30 days'), '')]), ' · '))
    FROM n360.account AS a LEFT JOIN bld AS bd ON bd.account = a.account LEFT JOIN idle AS i ON i.account = a.account
    UNION ALL
    SELECT a.account, 'support', 'CA', 'Support',
           multiIf(NOT tickets_loaded, 'not reported', t.found != 1, 'fine',
                   t.n_open >= open_tickets OR (t.n_open > 0 AND t.oldest > ticket_age), 'watch', 'fine'),
           multiIf(NOT tickets_loaded, 'Tickets not loaded', t.found != 1 OR t.n_open = 0, 'No open ticket',
                   concat(toString(t.n_open), ' open, oldest ', toString(t.oldest), ' days'))
    FROM n360.account AS a LEFT JOIN tix AS t ON t.account = a.account
);

CREATE OR REPLACE VIEW n360.v_risk_customer AS
-- One row per customer: the worst signal overall and per lens (Totango's rule: one risk signal makes the customer
-- "at risk"), counts, the reasons at risk, and the renewal it sits in front of.
WITH (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded') AS dt,
     ren AS (SELECT account, if(n > 0, nxt.1, NULL) AS close_date, if(n > 0, nxt.2, NULL) AS amount FROM (
                 SELECT s.account AS account, countIf(NOT o.is_closed AND o.close_date >= dt) AS n,
                        argMinIf(tuple(o.close_date, ifNull(o.amount, 0)), o.close_date, NOT o.is_closed AND o.close_date >= dt) AS nxt
                 FROM n360.v_sf_account AS s INNER JOIN n360.sf_opportunity AS o ON o.sf_account_id = s.sf_account_id
                 WHERE s.account != '' AND o.is_renewal GROUP BY s.account)),
     term AS (SELECT account, minIf(toNullable(end_date), days_left >= 0) AS end_date FROM n360.v_contract_status GROUP BY account)
SELECT a.account AS account, a.display_name AS display_name, a.csm_rocketlane AS csm,
       multiIf(max(r.level_rank) = 3, 'risk', max(r.level_rank) = 2, 'watch', max(r.level_rank) = 1, 'fine', 'not reported') AS level,
       multiIf(maxIf(r.level_rank, r.lens = 'CSM') = 3, 'risk', maxIf(r.level_rank, r.lens = 'CSM') = 2, 'watch',
               maxIf(r.level_rank, r.lens = 'CSM') = 1, 'fine', 'not reported') AS csm_level,
       multiIf(maxIf(r.level_rank, r.lens = 'CA') = 3, 'risk', maxIf(r.level_rank, r.lens = 'CA') = 2, 'watch',
               maxIf(r.level_rank, r.lens = 'CA') = 1, 'fine', 'not reported') AS ca_level,
       countIf(r.level = 'risk') AS n_risk, countIf(r.level = 'watch') AS n_watch,
       arrayStringConcat(groupArrayIf(concat(r.title, ': ', r.reason), r.level = 'risk'), ' · ') AS risk_reasons,
       arrayStringConcat(groupArrayIf(concat(r.title, ': ', r.reason), r.level = 'watch'), ' · ') AS watch_reasons,
       -- the next renewal: the open Salesforce renewal, else the next contract term end
       multiIf(any(rn.close_date) IS NOT NULL, any(rn.close_date), any(t.end_date) IS NOT NULL, any(t.end_date), NULL) AS renewal_date,
       dateDiff('day', dt, renewal_date) AS days_to_renewal,
       any(rn.amount) AS renewal_amount
FROM n360.account AS a
INNER JOIN n360.v_risk_signal AS r ON r.account = a.account
LEFT JOIN ren AS rn ON rn.account = a.account
LEFT JOIN term AS t ON t.account = a.account
GROUP BY a.account, a.display_name, a.csm_rocketlane;

CREATE OR REPLACE VIEW n360.v_sf_unlinked AS
-- Salesforce accounts with no N360 account, as of now (sf_runs.unmatched_accounts is the list at the time of a pull).
SELECT sf_account_id, sf_name, owner FROM n360.v_sf_account WHERE account = '';

-- ───────────────────────── risk model (26 Nov 2026): code/risk/, docs/21_RISK_MODEL.md ─────────────────────────
-- Written only by code/risk/risk_model.py; never swapped by a load. Append-only; the newest run wins in the views.
CREATE TABLE IF NOT EXISTS n360.risk_features         -- the feature store: each feature as it stood on a date
(
    as_of        Date,
    account      LowCardinality(String),
    feature      LowCardinality(String),
    value        Nullable(Float64),                     -- NULL = not reported (the source had nothing)
    computed_at  DateTime('UTC') DEFAULT now(),
    source       LowCardinality(String) DEFAULT 'rebuilt'  -- 'nightly' = stored that night (the true record; a rebuild never
                                                           -- replaces it) | 'rebuilt' = computed later from today's data
)
ENGINE = MergeTree PARTITION BY toYYYYMM(as_of) ORDER BY (account, as_of, feature);
ALTER TABLE n360.risk_features ADD COLUMN IF NOT EXISTS source LowCardinality(String) DEFAULT 'rebuilt';

CREATE TABLE IF NOT EXISTS n360.risk_outcome          -- outcomes recorded by hand (risk_model.py outcome)
(
    account   LowCardinality(String),
    date      Date,
    outcome   Enum8('churned' = 1, 'downsold' = 2, 'renewed' = 3, 'expanded' = 4),
    note      String,
    by        LowCardinality(String),
    at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree ORDER BY (account, date);

CREATE TABLE IF NOT EXISTS n360.risk_model_runs       -- every trained model, with its backtest
(
    run_id      UUID,
    trained_at  DateTime('UTC') DEFAULT now(),
    version     String,                                 -- risk_model.json version
    kind        LowCardinality(String),                 -- 'prior' (too few outcomes) | 'trained'
    n           UInt32,                                 -- snapshots
    events      UInt32,                                 -- distinct renewals lost (never snapshots)
    note        String,
    model       String,                                 -- JSON: intercept, coef, mean, stage
    backtest    String                                  -- JSON: metrics per method, reliability, lead times
)
ENGINE = MergeTree ORDER BY trained_at;

CREATE TABLE IF NOT EXISTS n360.risk_model_adopted    -- which run is in use: the newest row (risk_model.py adopt). Training
(                                                     -- never puts a model into use by itself.
    run_id  UUID,                                     -- all zeros = back to the built-in expert prior
    at      DateTime64(3, 'UTC') DEFAULT now64(3),
    by      LowCardinality(String),
    note    String
)
ENGINE = MergeTree ORDER BY at;

CREATE TABLE IF NOT EXISTS n360.risk_scores
(
    run_id           UUID,
    scored_at        DateTime('UTC') DEFAULT now(),
    model_run_id     Nullable(UUID),                    -- NULL = the expert prior (no model trained yet)
    model_kind       LowCardinality(String),
    as_of            Date,
    account          LowCardinality(String),
    health           Nullable(Float64),
    band             LowCardinality(String),            -- healthy | watch | at risk | not enough data | not reported | churned
    probability      Nullable(Float64),                 -- churn or downsell at the next renewal; NULL = churned, or nothing reported
    eligible         Bool,                              -- next renewal within the horizon (an unknown date is not)
    coverage         Float64,                           -- share of the model's weight with data
    hard_reds        Array(String),
    days_to_renewal  Nullable(Float64),
    renewal_amount   Nullable(Float64),
    revenue_at_risk  Nullable(Float64)                  -- probability x renewal amount, when eligible
)
ENGINE = MergeTree ORDER BY (as_of, account, scored_at);
ALTER TABLE n360.risk_scores MODIFY COLUMN probability Nullable(Float64);

CREATE TABLE IF NOT EXISTS n360.risk_contributions    -- why: each feature's value, risk, health points lost, log-odds push
(
    run_id     UUID,
    as_of      Date,
    account    LowCardinality(String),
    feature    LowCardinality(String),
    dimension  LowCardinality(String),
    value      Nullable(Float64),
    risk       Nullable(Float64),
    points     Float64,
    logodds    Float64
)
ENGINE = MergeTree ORDER BY (as_of, account, feature, run_id);

CREATE OR REPLACE VIEW n360.v_risk_outcomes AS
-- Every known outcome of a renewal: recorded by hand, closed in the risk register, or a renewal closed in Salesforce.
-- Salesforce is the source that covers EVERY customer, flagged or not: lost = churned; won at under
-- settings.risk_downsell_ratio of the customer's previous won RENEWAL = downsold; else renewed. The bar is the previous
-- renewal, not any won deal: a small upsell before it would make a cut renewal look like growth, and a multi-year
-- first deal would make a normal annual renewal look like a cut. A first renewal has no bar and is never a downsell
-- here (PROPOSED; record it by hand or in the register).
-- The register only knows customers somebody raised a risk for, so it is a second witness. Its date is the day the
-- outcome happened (v_risk_register.outcome_date), not the last edit. A risk closed as "saved" is not a renewal
-- outcome (it is usually closed mid-term): it is left out, and the renewal is judged by what happens at its date.
-- The same event may appear from two sources; the model takes one outcome per renewal (code/risk/backtest.py).
SELECT account, date, outcome, source FROM (
    SELECT account, date, toString(outcome) AS outcome, 'recorded' AS source FROM n360.risk_outcome
    UNION ALL
    SELECT account, assumeNotNull(outcome_date), outcome, 'register'
    FROM n360.v_risk_register WHERE status = 'closed' AND outcome IN ('churned', 'downsold') AND outcome_date IS NOT NULL
    UNION ALL
    SELECT s.account, o.close_date,
           multiIf(NOT o.is_won, 'churned',
                   o.amount > 0 AND o.prev_amount > 0 AND o.amount < (SELECT risk_downsell_ratio FROM n360.settings) * o.prev_amount, 'downsold',
                   'renewed'), 'salesforce'
    FROM (SELECT sf_account_id, close_date, is_won, amount,
                 -- the customer's previous won RENEWAL with an amount (a lost one never sets the bar)
                 anyLast(if(is_won AND amount > 0, amount, NULL))
                     OVER (PARTITION BY sf_account_id ORDER BY close_date, opp_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_amount
          FROM n360.sf_opportunity WHERE is_renewal AND is_closed AND close_date IS NOT NULL) AS o
    INNER JOIN n360.v_sf_account AS s ON s.sf_account_id = o.sf_account_id
    WHERE s.account != '');

CREATE OR REPLACE VIEW n360.v_risk_model AS
-- The model in use: the run adopted last (risk_model.py adopt). No row = the built-in expert prior.
SELECT r.* FROM n360.risk_model_runs AS r
WHERE r.run_id = (SELECT argMax(run_id, at) FROM n360.risk_model_adopted)
ORDER BY r.trained_at DESC LIMIT 1;

CREATE OR REPLACE VIEW n360.v_risk_score AS
-- The newest score per customer: the newest scoring run of the newest date.
SELECT * FROM n360.risk_scores
WHERE (as_of, scored_at) IN (SELECT as_of, max(scored_at) FROM n360.risk_scores
                             WHERE as_of = (SELECT max(as_of) FROM n360.risk_scores) GROUP BY as_of);

CREATE OR REPLACE VIEW n360.v_risk_drivers AS
-- The newest contributions per customer (same run as v_risk_score).
SELECT c.* FROM n360.risk_contributions AS c
WHERE c.run_id IN (SELECT run_id FROM n360.v_risk_score);

-- ───────────────────────── operations (26 Nov 2026): written by the company pipeline's local runner ──────────────
-- ops_runner.py (127.0.0.1:8099, the buttons on the N360 · Operations dashboard) records each job and what ran each
-- pipeline night. Not loaded from exports, not swapped, not part of the 8 checks; declared here so a fresh server and
-- CI have them before the runner first starts (the runner creates them the same way, IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS n360.ops_job
(
    job_id     String,
    action     LowCardinality(String),                       -- pull | export | load
    state      LowCardinality(String),                       -- running | done | failed
    started    DateTime('UTC'),
    finished   Nullable(DateTime('UTC')),
    exit_code  Nullable(Int16),
    last_line  String,
    updated    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated) ORDER BY job_id;

CREATE TABLE IF NOT EXISTS n360.ops_night
(
    night             Date,
    claude_result     String,                                -- 'published vN', 'stopped: …', 'no run'
    claude_published  Nullable(UInt32),
    code_result       String,                                -- run_nightly.py: 'complete', 'export only', the failed step, 'not run'
    export_run_id     String,                                -- joins n360.runs (toString(run_id)) to show whether it loaded
    export_counts     String,
    updated           DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated) ORDER BY night;
