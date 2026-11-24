# 01 · Architecture

**Every number flows one way: sources → company pipeline → contract measures → row export → ClickHouse → Grafana.**
The pipeline decides every number, contract rules included, before anything stores it. ClickHouse
stores it as time series. Grafana only reads.

```
 Company side (not in this pack)                       This pack
 ───────────────────────────────                       ─────────
 Production databases, contract sheet,
 account system, auth-trace store, ticket tracker
        │  nightly pull
        ▼
 Nightly pipeline  → data/<D>/*.json  (per-night folder)
        │
        ▼
 contract_measures.py  → contract_measures.json
   (applies each contract's billing rule, signed-SOW
    overrides, forecasts; self-checked)
        │
        ▼
 export_rows.py  → data/<D>/rows/<table>.ndjson + run.json  ◄── sample_data/generate_synthetic.py
        │                                                       writes the SAME format
        ▼
 load.py:  stage in n360_next → checks.sql (8) → EXCHANGE TABLES into n360
        │
        ▼
 ClickHouse database `n360`  ── read-only user ──►  Grafana 13.3.1 OSS
                                                    11 dashboards + alert rules, all generated as code
```

## Layers

| Layer | Owner | In this pack |
|---|---|---|
| Pull from production | Company pipeline | No. Interface only |
| Contract measures (rules, SOW terms, forecasts) | Company pipeline (`contract_measures.py`) | No. Its output shape is in `02_DATA_CONTRACT.md` |
| Row export (`data/<D>/rows/*.ndjson`) | Company `export_rows.py` | Format yes; the synthetic generator writes it |
| Storage, checks, swap | `schema.sql`, `checks.sql`, `load.py` | **Yes** |
| Dashboards, alerts | `build_dashboards.py`, provisioning | **Yes** (1 of 11 built) |

## Key design decisions (all decided; see `08_DECISIONS.md`)

1. **One-way flow, rules upstream.** Grafana and SQL views never decide what a number means. A
   contract's measure (engaged, billable, all conversations, not measurable, not implemented) is
   set by the pipeline. Views only divide.
2. **Time-series layout.** One row per entity per day. Sort key: low-cardinality dimensions first,
   the day last (`ORDER BY (account, bot_id, day)`). Monthly partitions. This is the same shape as
   one Elasticsearch document per bot-day (Ryan knows ES well; see the mapping table below).
3. **Atomic nightly swap.** Every table is reloaded whole into `n360_next`, checked, then swapped
   with `EXCHANGE TABLES`. A failed check leaves last night live. The data is small (tens of
   thousands of rows a night), so a full reload is cheaper than incremental logic. Partition
   replacement comes later, when raw events land.
4. **NULL means not read.** Counts are `Nullable`. 0 = read and zero. "Empty is never a gap" is
   enforced by the type.
5. **Dashboards are code.** Generated JSON, provisioned read-only, UI edits refused. A change is a
   pull request.
6. **Local first.** The proof of concept runs on Ryan's Mac, bound to 127.0.0.1. Cloud placement
   comes later and is a company decision.

## Why ClickHouse and Grafana (what was considered)

| Option | Verdict |
|---|---|
| Elasticsearch + Kibana | Rejected: no joins (relational contract model would be flattened), serverless search priced on compute minimums, Kibana self-serve undermines one-number-per-cell |
| Elastic Observability serverless | Cheap, but same join problem |
| Looker Studio / Metabase / Superset | Viable, but dashboards are click-built, not code |
| **ClickHouse + Grafana** | **Chosen**: SQL with joins, fast time series, dashboards as JSON in Git, alerting built in, free OSS |

## Elasticsearch → ClickHouse, for reference

| Elasticsearch | Here |
|---|---|
| index template / mapping | `schema.sql` |
| `keyword` / `long` / `date` | `LowCardinality(String)` / `UInt32`,`UInt64` / `Date`,`DateTime` |
| missing field | `NULL` (Nullable column) |
| data stream + monthly backing indices | `PARTITION BY toYYYYMM(day)` |
| ILM delete phase | `TTL` (not used yet) |
| index sorting | `ORDER BY` |
| alias flip after reindex | `EXCHANGE TABLES` |
| upsert by `_id` | `ReplacingMergeTree(version)` or partition replace |
| transform / rollup | materialized view (not needed at this size) |
| `_bulk` NDJSON | `INSERT … FORMAT JSONEachRow` |

## Runtime

- Pipeline runs once a day on Ryan's Mac when the VPN connects (launchd), covering data to
  yesterday 23:59 UTC. Data a day old is acceptable by design.
- Phase 3 moves the nightly off Claude and off the laptop (see backlog). Not your concern beyond
  keeping the code portable (no absolute paths, config by env vars and arguments).
