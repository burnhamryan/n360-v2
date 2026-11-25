# 14 · Handoff: connect the company pipeline to N360

**For Claude (or anyone) working on Ryan's company machine, with access to the company pipeline.**
The goal is to get tonight's real data into the N360 dashboards on Ryan's Mac.

## Where things stand

- **N360 (this repo, `burnhamryan/n360-v2`) is the receiving end, and it is done:** ClickHouse 25.8 + Grafana 13.3.1
  OSS in Docker, 11 dashboards, 8 data checks, nightly loader, backups, alert rules (paused), AWS infrastructure.
  It was built and tested on **synthetic data only**, with no access to company systems.
- **Ryan's Mac has it running** at `~/Documents/Github/n360`: `docker compose up -d` in `code/`, a Python 3.12 venv in
  `.venv`, passwords in `code/.env`.
- **The missing piece is the company side:** the nightly pull from production, `contract_measures.py`, and
  `export_rows.py`, which writes the files N360 loads. No export folders exist on the Mac yet. N360 never reads
  company systems itself, by design (`docs/01_ARCHITECTURE.md`).

```
company systems → nightly pull → contract_measures.py → export_rows.py → data/<D>/rows/   ← you are here
                                                                              │
                                     N360: code/ops/nightly.sh → load.py → 8 checks → ClickHouse → Grafana
```

## What to do

1. **Find the company pipeline** (Ryan's other repo or a server) and check whether `export_rows.py` exists and writes
   the format below. If it exists, run it for the latest night(s). If it doesn't, write it against the format below.
2. **Put the night folders on the Mac**, e.g. `~/n360-exports/<data_date>/rows/` with one `<table>.ndjson` per table
   plus `run.json`.
3. **Load them**, from `~/Documents/Github/n360`:
   ```bash
   source .venv/bin/activate && set -a && . code/.env && set +a
   N360_DROP=~/n360-exports N360_WORK=~/n360-work code/ops/nightly.sh
   ```
   It loads every night once, oldest first, and stops at the first error. If synthetic data was loaded earlier,
   wipe it first: `cd code && docker compose down -v && docker compose up -d`.
4. **Check** http://localhost:3000 → N360 → Portfolio overview, and Pipeline health for loaded / refused nights.

## The export format (the contract)

Full spec: **`docs/02_DATA_CONTRACT.md`**. Source of truth for every column and type: **`code/schema.sql`**. A
working example of the exact format: `python3 sample_data/generate_synthetic.py --out /tmp/x --nights 2`, which you
can diff against the real output.

- One folder per night: `<data_date>/rows/`, containing `run.json` and one NDJSON file per table.
- `run.json`: `{"run_id": "<uuid>", "data_date": "YYYY-MM-DD", "window_end": "<D-1> 23:59:00", "capture_utc": ...,
  "source_dir": ..., "code_fingerprint": ..., "row_counts": {...}}`. `data_date` is the night the pipeline ran;
  `window_end` is the last data day. `nightly.sh` loads by `run_id`, so the same run never loads twice.
- **Required tables:** `account`, `va`, `contract_line`, `line_va`, `pool`, `rule`, `source`, `va_day`, `line_day`,
  `line_day_billable`, `forecast`, `line_totals_history`.
- **Optional (Phase 2, proposed design):** `va_config_snapshot`, `playbook_day`, `cortex_day`, `explorer_day`,
  `ps_ticket`. Leave them out until the company has the data; the load is unchanged without them.
- **Every row carries `run_id`.**
- **NULL means not read; 0 means read and zero.** Never write 0 for unknown.
- **IDs are UUIDs.** Enums are fixed sets, so a new value fails the load on purpose.
- **Don't loosen a type to make a load pass. Fix the export.** The strict types have caught real bugs (a flag packed
  into an ID, "500,000" stored as text).
- **The pipeline decides every number** (contract rules, signed-SOW overrides, forecasts). N360 views only divide
  and compare.

## When a load fails

- **Exit 3, `REFUSED`:** one of the 8 checks found offending rows (table in `02`, "The 8 checks"). The previous
  good night stays live. Usually a real data problem upstream: fix the export, not the check.
- **Exit 2, error:** usually a format mismatch (missing column, wrong type, bad enum value). `load.py` names the
  table and column.
- If the export is right and N360 is wrong, send Ryan **the error text only** for the Claude that maintains this
  repo. Never paste customer data into a chat that isn't company-approved.

## Rules

- **Real data stays on company-approved machines.** Keep the N360 Claude chat (`docs/09_CHAT.md`) off with real
  data until the company approves sending query results to Anthropic (`docs/08_DECISIONS.md`).
- **Don't change N360 table names, column names or dashboard uids.** A schema change is Ryan's call, and both the
  exporter and `sample_data/generate_synthetic.py` must change together.
- **Never commit `code/.env`, passwords or exports.**
- **Back up the history tables**, which can't be rebuilt from exports:
  `N360_BACKUP=~/n360-backups code/ops/backup.sh`.

## Useful files

| File | What |
|---|---|
| `docs/13_LAPTOP_POC.md` | Mac setup and nightly run |
| `docs/02_DATA_CONTRACT.md` | Export format, checks, keys |
| `code/schema.sql` | Tables, types, views, `n360.settings` thresholds |
| `code/load.py` | Stage → checks → swap; `--rows <dir>` loads one night by hand |
| `code/ops/nightly.sh` | Loads every new night folder |
| `sample_data/generate_synthetic.py` | Reference exporter (synthetic) |
| `docs/12_AWS.md` | Later: the same stack on AWS, loads still from the laptop |
