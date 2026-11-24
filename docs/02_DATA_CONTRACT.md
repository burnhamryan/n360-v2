# 02 · Data contract

**The tables, columns and rules every producer and consumer must respect.** `code/schema.sql` is
the source of truth; this file explains it. The company exporter and the synthetic generator write
identical rows. If you change a column, both must change, and that is Ryan's call.

## Row format

One NDJSON file per table in a `rows/` folder, plus `run.json`:

```json
{"run_id":"<uuid>","data_date":"2026-11-20","window_end":"2026-11-19 23:59:00",
 "capture_utc":"2026-11-20 03:00:00","source_dir":"...","code_fingerprint":"...",
 "row_counts":{"va_day":9632, "...":0}}
```

`data_date` = the night the pipeline ran (D). `window_end` = D-1 23:59 UTC, the last data day.

## Conventions

- **NULL = not read. 0 = read and zero.** Never write 0 for "unknown".
- Ratios are never stored. Rule outputs and forecasts are stored, because they need Python logic.
- Every row carries `run_id`; `runs` holds its capture time, window and fingerprint.
- Dimensions are `LowCardinality(String)`. `Enum8` only for fixed sets (region, source, kind); a new
  value then fails the load on purpose.
- IDs are `UUID`. The strict types have caught real data bugs twice (a source flag packed into an
  ID; a number stored as "500,000" text). Fix the export, never loosen the type.

## Fact tables (one row per entity per day; `PARTITION BY toYYYYMM(day)`)

### `va_day` — one row per virtual agent (VA) per day. Sort: account, bot_id, day

| Column | Type | Meaning |
|---|---|---|
| day | Date | UTC day |
| account | LowCardinality(String) | Account key (stable name; display name lives in `account`) |
| bot_id | UUID | Configuration id of the VA |
| live_ref | UUID | Runtime id that usage rows carry |
| consumption_source | Enum8 aggregate / conversation_records / contract_rule | Which reader produced the count |
| region | Enum8 US / EU / SG | |
| conversations | Nullable(UInt32) | All conversations |
| engaged | Nullable(UInt32) | Platform engaged flag |
| handoffs | Nullable(UInt32) | NULL where handoffs are not read (conversation_records VAs) |
| run_id | UUID | |

### `line_day` — one row per contract-line series per day. Sort: account, series_key, day

| Column | Type | Meaning |
|---|---|---|
| day, account | | |
| series_key | String | `<account>|<idx>` for a line; `<account>|<idx>|bot|<id>` for a pool component; `<account>|unbilled|<id>` for an unbilled VA |
| kind | Enum8 line / component / unbilled | |
| bot_id | Nullable(UUID) | Components and unbilled only |
| conversations, engaged | Nullable(UInt32) | As read |
| measured | Nullable(UInt32) | **The contract's measure.** Equals engaged when no rule applies |

### `line_day_billable` — meaningful-engagement rule output per line per day

`day, account, line_key, rule_id, rule_hash, conversations, engaged_flag, billable`. A changed rule
definition changes `rule_hash`.

## Contract and reference tables (reloaded whole every night)

### `contract_line` — one row per contract line. Key: `line_key`

| Column | Meaning |
|---|---|
| line_key | `<sheet customer>|<channel>|<start d-Mon-YYYY>`. **Unique** (checked). Account + channel + start is NOT unique in real data |
| series_key | Joins `line_day` and `forecast` |
| account, idx, sheet_customer, channel | |
| start_date, end_date | After any signed-SOW override |
| billed | After SOW override. NULL = blank, or carried by a pool |
| sheet_billed, billed_as_stored | What the contract sheet says, kept to show overrides |
| unit | Contract unit when a SOW overrides the sheet |
| measure | engaged · billable (contract rule) · conversations (contract rule) · not measurable · not implemented |
| rule_id, rule_kind | NULL = no rule live on this line |
| pool_id | Pooled lines are measured at the pool, never alone |
| not_implemented, unmeasured | Bools derived from measure |
| reported_measured, reported_last30_measured | Totals as the pipeline computed them; NULL if not measurable / not implemented |
| reported_engaged_flag, reported_consumed_convs | Always kept beside the measure |
| reported_billable | Meaningful-engagement lines only |
| reported_pct_page | % of billed as the pipeline computed it; the view must match it to 0.01 |
| term_days, segment, csm_sheet | |

### Other reference tables

| Table | One row per | Key columns |
|---|---|---|
| `account` | Account | account, display_name, rocketlane_account_id, csm_rocketlane (column names kept as the company uses them) |
| `va` | VA | bot_id, live_ref (Nullable), consumption_source, account, va_name, region, channels, on_contract |
| `line_va` | Line × VA | line_key, bot_id, live_ref, consumption_source, mapping_basis |
| `pool` | Pooled entitlement | account, pool_id, billed, start/end, line_keys |
| `rule` | Contract rule | rule_id, kind, status (active / pending_probe / blocked), label, contract_text, lines_measured (empty = not live) |
| `forecast` | Line or pool | series_key, rate_per_day, forecast_m3, exhaust_date, cross_date, flags |
| `source` | Metric family | source_id, description, definition, last_day |
| `runs` (append-only) | Load attempt | run_id, data_date, window_end, status loaded / failed_checks, checks_failed, row_counts |
| `line_totals_history` (append-only) | Line × night | data_date, line_key, measure, consumed_measured, engaged_flag, pct_billed_page |

## Views

| View | Purpose |
|---|---|
| `v_line_status` | % of billed and % of term per line (pooled lines get none) |
| `v_pool_status` | % of billed per pool |
| `v_va_containment_day` | 1 − handoffs / engaged per VA per day |
| `v_va_day_anomaly` | Anomaly flags (see `05_ANOMALY_AND_FORECAST.md`) |
| `v_line_pace` | One row per entitlement (unpooled line, or pool once): % of billed, % of term, pace gap, crossing date from `forecast`, `pace` (red / amber / green / not measurable / not implemented / not read), `pace_reason`, `term_ended`. Holds the proposed pace thresholds, the only place they exist |
| `v_customer_attention` | One row per customer: worst entitlement by pace, counts of red and amber entitlements, drops and silences and spikes in the 7 newest settled days, provisional flags |
| `v_anomaly_heat` | Heatmap band per VA-day for the Anomalies dashboard (watch band ±2; flags from `v_va_day_anomaly`) |
| `v_source_freshness` | Days each source is behind the data-through day; green / amber (2) / red (3) |
| `settings` | Every proposed threshold, one row (pace, anomaly, freshness, engine, asks, anomaly v2). Views read it |
| `v_forecast_accuracy`, `v_forecast_change`, `v_forecast_drift` | Forecast MAPE at 30/60/90 days; night over night changes; crossing-date drift (B16) |
| `v_anomaly_review`, `v_flag_quality` | Flags with their newest review; weekly flag quality (B19) |
| `v_containment_week`, `v_measure_gap_week`, `v_anomaly_v2` | Anomaly detection v2 signals (B20) |
| `v_challenger_backtest` | Newest challenger backtest MAPE per model and horizon (B21) |
| `v_anomaly_events` | Cross-account events: the same settled day and direction (spikes, or drops and silences) flagged on 3 or more accounts |

## Keys and joins

| Join | On | Watch out for |
|---|---|---|
| Line → daily series | `contract_line.series_key = line_day.series_key`, kind line | |
| Line → VAs | `line_va.line_key` | Many-to-many in principle; today one VA sits on one line (checked) |
| VA identity | `bot_id` vs `live_ref` | Usage rows carry `live_ref` |
| Line → pool | `pool_id` + account | Never compare a pooled line with billed alone |
| Row → load | `run_id = runs.run_id` | Only `status = 'loaded'` is live |

## The 8 checks (`checks.sql`; any offending row refuses the load, exit 3)

| Check | Fails when |
|---|---|
| crosswalk_one_line_per_va | A VA is on two lines (totals would double count) |
| line_key_unique | Two lines share a key |
| line_measured_reconciles | Daily measured ≠ the line total |
| pct_matches_page | View % of billed differs from the pipeline's by > 0.01 |
| billable_reconciles | Daily billable ≠ the rule total |
| facts_end_on_last_day | A fact table does not end on the data-through day |
| crosswalk_vas_known | A VA on a line has no VA row |
| facts_not_empty | A core table is empty |

## History and review tables (B15–B21, 24 Nov 2026; never swapped)

| Table | Written by | Grain |
|---|---|---|
| `forecast_history` | `load.py` after each swap (copy of `forecast`), optional backfill file | Line or pool × night |
| `source_freshness` | `load.py` after each swap (copy of `source`) | Source × night |
| `anomaly_log` | `code/review_anomaly.py` (loader user) | Flag × review; newest wins |
| `forecast_challenger` | `code/forecast/challenger.py` | Backtest prediction (run × model × series × cutoff × horizon) |

## Phase 2 tables (designed 24 Nov 2026, proposed; see `schema.sql`)

`va_config_snapshot`, `playbook_day` (added), `cortex_day`, `explorer_day` and `ps_ticket` are defined in `schema.sql`
with views `v_va_config_latest`, `v_va_config_change`, `v_top_asks`. `load.py` loads them only when the export
carries the file. Their field meanings are open questions (`08`). Still planned: `forecast_history`,
`source_freshness` (a view `v_source_freshness` reads `source.last_day` for now), `anomaly_log`.

## Planned tables (Phase 2; design them, test on synthetic)

`va_config_snapshot` (VA × weekly config read: engine version, V8 playbooks, legacy intents, MCP
servers, supervisory rules; keeps history), `cortex_day`, `explorer_day` (customer vs staff
questions), `ps_ticket` (support tickets with area/ask tags), `forecast_history` (line/pool ×
night), `source_freshness`, `anomaly_log` (flag × review outcome).
