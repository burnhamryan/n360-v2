# 24 · Adoption data: what the company export must add

**N360 is ready for adoption data; the company export does not write it yet.** Five tables were designed on 24 Nov
2026 ("Phase 2"): the schema, the loader, the Adoption and Requests dashboards and four risk-model features are
built and tested on synthetic data. They stay empty until the nightly export (`export_rows.py`, in the company
repository, not here) writes five more files. This page is the spec for that work. Hand it to whoever owns the
export (your work Claude: docs/14).

Nothing in N360 changes for this. When a file appears in the export's `rows/` folder, `load.py` checks it, loads it
and swaps it in with the rest; until then the live table is left as it is.

## What it feeds

| Table | N360 uses it for |
|---|---|
| `va_config_snapshot` | Adoption dashboard (engine version per VA, V8 coverage); risk features *legacy VA share*, *weeks since configuration changed*, and part of *target features used* (MCP servers, supervisory rules) |
| `playbook_day` | Adoption dashboard (playbooks built and run); part of *target features used* |
| `cortex_day` | Adoption dashboard (customer authoring); *target features used*, *authoring and assistant trend* |
| `explorer_day` | Adoption dashboard (analytics-assistant questions); *target features used*, *authoring and assistant trend* |
| `ps_ticket` | Requests dashboard (top asks); risk features *open tickets*, *oldest open ticket* |

The adoption dimension is 15% of the health score and support is 5%. While the files are missing, those features
are "not reported" and their weight moves to the other dimensions; coverage drops.

## The five files

The same format as every other table (docs/02): one NDJSON file per table in `rows/`, one JSON object per line,
every row carrying the night's `run_id`. **NULL = not read; 0 = read and zero.** Column types are strict; a value
that does not fit fails the load on purpose (fix the export, never the type). Exact types: `code/schema.sql`.
Example rows: run `python3 sample_data/generate_synthetic.py --out /tmp/rows` and look at the five files it writes.

### `va_config_snapshot.ndjson`: one row per VA per configuration read

| Column | Type | Meaning |
|---|---|---|
| snapshot_date | Date | The day the configuration was read |
| account | String | Account key, as in `account.account` |
| bot_id | UUID | Configuration id of the VA, as in `va.bot_id` |
| engine_version | String | The engine version as the configuration reports it, e.g. `V8.2` |
| engine_major | UInt8 | Its major number, e.g. 8 (N360 counts 8 or later as current: `settings.engine_current_major`) |
| v8_playbooks | UInt16 | Playbooks on the VA (V8) |
| legacy_intents | UInt16 | Legacy intents still on the VA |
| mcp_servers_live | UInt8 | MCP servers connected and live |
| supervisory_rules_on | UInt8 | Supervisory rules switched on |
| run_id | UUID | |

**Grain and history.** Read every VA at least weekly. **Keep every past read and write them all each night**
(the table is replaced whole, like the others): "weeks since the configuration changed" compares reads over time.
If the source keeps no history of configurations, start reading now; the history builds from the first read.

### `playbook_day.ndjson`: one row per VA, playbook and day

| Column | Type | Meaning |
|---|---|---|
| day | Date | UTC day |
| account | String | |
| bot_id | UUID | |
| playbook | String | Playbook name |
| executions | UInt32, nullable | Times the playbook ran that day (0 = built, not run; NULL = not read) |
| run_id | UUID | |

### `cortex_day.ndjson`: one row per account, user type and day

| Column | Type | Meaning |
|---|---|---|
| day | Date | UTC day |
| account | String | |
| user_type | `customer` or `staff` | Who was authoring. Only `customer` counts as adoption |
| authoring_sessions | UInt32, nullable | Authoring-tool sessions that day |
| run_id | UUID | |

### `explorer_day.ndjson`: one row per account, user type and day

| Column | Type | Meaning |
|---|---|---|
| day | Date | UTC day |
| account | String | |
| user_type | `customer` or `staff` | Who asked. Only `customer` counts as adoption |
| questions | UInt32, nullable | Questions asked of the analytics assistant that day |
| run_id | UUID | |

### `ps_ticket.ndjson`: one row per support ticket

| Column | Type | Meaning |
|---|---|---|
| ticket_id | String | The ticket's id in the support system |
| account | String | |
| opened | Date | Day the ticket was opened |
| title | String | Ticket title |
| product_area | String, nullable | Product area, tagged by hand. NULL until tagged; never guessed |
| ask | String, nullable | The feature asked for, in one phrase, tagged by hand. NULL until tagged |
| status | String | `open` or anything else (N360 only tests for `open`) |
| run_id | UUID | |

All open tickets, and closed ones opened in the last two years.

## How far back

The risk model rebuilds its features for past dates (docs/21), and the usage history goes back to May 2024. For
the adoption features to be rebuilt as far back:
- `playbook_day`, `cortex_day`, `explorer_day`: **from 1 November 2024**, every night, if the sources hold it.
  The authoring and assistant trend compares two 30-day spans, so the 60 days before a date are needed.
- `va_config_snapshot`: every read since reads began. Past configurations cannot be invented: where there is no
  record, the history starts at the first read.
- `ps_ticket`: tickets opened since November 2024.

Then, in N360: `risk_model.py features --from 2024-11-04 --every 7 --overwrite`, and `score --as-of all`.

## Questions for whoever builds it (don't guess; leave the column NULL)

1. Where each table comes from: the configuration store, playbook execution logs, the authoring tool's and the
   assistant's usage logs, the support system. Each may need its own access.
2. How to tell a **customer** user from **staff** in the authoring and assistant logs (email domain, role).
3. Whether the support system holds `product_area` and `ask`, or whether they are tagged by hand later.
4. Whether `engine_version` is the same string on every VA (`V8.2`, `8.2`, `v8`): `engine_major` must be the
   number.

## Checking it worked

After the first night with the files, run the readiness report:

```bash
python3 code/readiness.py --user n360 --password "$N360_CH_PASSWORD"
```

Its PHASE 2 section should read READY for all five, and the risk model's "Adoption and depth" line should show the
share of customers reported for each feature. The Adoption and Requests dashboards fill in on their own.
