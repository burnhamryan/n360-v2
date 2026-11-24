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

-- ───────────────────────── views (one definition per ratio) ─────────────────────────────────────

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
CROSS JOIN (SELECT window_end, capture_utc FROM n360.runs WHERE status = 'loaded' ORDER BY loaded_at DESC LIMIT 1) AS r;

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
WITH base AS (
    SELECT day, account, bot_id, conversations,
           quantilesExact(0.25, 0.5, 0.75)(conversations) OVER w AS q, count() OVER w AS n
    FROM n360.va_day
    WHERE conversations IS NOT NULL
    WINDOW w AS (PARTITION BY bot_id, toDayOfWeek(day) ORDER BY day ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING)
)
SELECT b.day, b.account, b.bot_id, v.va_name, b.conversations AS actual, b.q[2] AS expected,
       greatest((b.q[3] - b.q[1]) / 1.349, 0.1 * b.q[2], 5) AS spread,
       round((b.conversations - b.q[2]) / spread, 2) AS z,
       multiIf(b.conversations = 0, 'went silent', z <= -4, 'drop', z >= 4, 'spike', 'normal') AS kind,
       dateDiff('day', b.day, (SELECT toDate(max(window_end)) FROM n360.runs WHERE status = 'loaded')) < 3 AS provisional
FROM base AS b
LEFT JOIN n360.va AS v ON v.bot_id = b.bot_id
WHERE b.n = 8 AND b.q[2] >= 20;
