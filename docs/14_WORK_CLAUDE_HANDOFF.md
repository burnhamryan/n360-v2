# 14 · Handoff for the work Claude (27 Nov 2026)

**For the Claude working on Ryan's company machine, with the company pipeline.** N360 (this repo,
`burnhamryan/n360-v2`) is the receiving end: ClickHouse 25.8 + Grafana 13.3.1 OSS on Ryan's Mac, 18 dashboards, all
built and tested on synthetic data. You own the other end: the nightly pull, `export_rows.py` and the local runner.
This page is what N360 now needs from you, in order.

```
company systems → nightly pull → export_rows.py → data/<D>/rows/*.ndjson ──► load.py (checks, swap) ──► ClickHouse ──► Grafana
Salesforce ──────────────────────► sf_pull.py ──► load_salesforce.py ──────────────┘
                                                   risk_model.py nightly (features, scores) ──┘
```

## Start here

On the Mac, in `~/Documents/Github/n360` (`.venv` and `code/.env` as before):

```bash
git pull origin main
```
```bash
source .venv/bin/activate && set -a && . code/.env && set +a
```
```bash
python3 code/load.py --schema-only --user n360 --password "$N360_CH_PASSWORD"
```
```bash
python3 code/readiness.py --user n360 --password "$N360_CH_PASSWORD"
```

`readiness.py` checks every source N360 reads and ends with a to-do list. It prints **counts, dates and shares
only** (and the labels Salesforce uses). That output is safe to pass back to Ryan for the repo's Claude; real rows
never are.

If `git pull` stops on local changes (Ryan has edits in `code/checks.sql` and `code/docker-compose.yml`), use
`git stash && git pull origin main && git stash pop`, and tell Ryan whether those edits should be committed or
dropped. The `checks.sql` fix (`pct_matches_page`) is already on main.

## 1. Cortex per person: `cortex_user_day` (new, the most wanted)

**Why:** the new **Cortex by Netomians** dashboard (`n360-cortex`: a 3-month leaderboard and a running leaderboard)
and the Netomi-against-customer splits on the Adoption pages read it. Until it exists they show "Not reported yet".

**First find out whether it is possible.** `cortex_day` is written with `user_type = 'unknown'` because the source
behind it has no user id. Look for any Cortex log that records who opened a session (user email or id, workspace
audit log, session table). If none exists, say so to Ryan; nothing else in N360 depends on it.

**If it exists**, export one row per day, account, VA and person (spec and columns: `docs/25_ADOPTION_DETAIL.md`,
"Wanted next"; types: `code/schema.sql`):
- `user_type` = `staff` when the email is on Netomi's domain (`netomi.com`), else `customer`.
- `user` = **the Netomi user's email only.** For customer users it is always NULL: they are counted, never named.
- `sessions`, and if the source has them `edits` and `production_edits` (NULL when not read, never 0).
- From 1 Sep 2026 if the source holds it; the whole table each night.
- **Keep `cortex_day` as it is.** N360 tests that `cortex_day`, `cortex_user_day` and `va_adoption.cortex_sessions_30d`
  add up to the same totals; if they cannot (a different source or window), tell Ryan which and why.

## 2. Check the adoption tables you already export

You export `va_adoption`, `playbook_window`, `feature_window` and `cortex_day` (docs/25). The three new Adoption
pages are built on them (docs/04 section 9). Check, on one real night:
- **One number, one value:** total executions agree in `va_adoption.executions`, `feature_window` (feature
  `V8 playbooks`) and `playbook_window.executions`. N360's tests require it on synthetic data.
- **NULL = not read, 0 = read and none,** everywhere. A NULL shows grey "Not reported yet"; a 0 shows "None recorded".
- **`playbook_window.creator_domain`** is filled: it drives "who builds the playbooks" and the Netomi-built against
  customer-built executions. `netomi.com` = Netomi.
- **`va_adoption.behavior_validation`** is exactly `enabled`, `off` or NULL (not set).

## 3. History the risk model and dashboards need

- **`contract_line`: keep ended lines** (past terms), not only current ones. Readiness found only 4 ended lines. Past
  renewal dates come from them; without them the risk model can only learn from renewals Salesforce knows about.
- **`va_config_snapshot`: weekly configuration reads, all history kept** and written whole each night. It feeds
  "VAs on V8" (with its 30-night trend), the engine-week-by-week timelines, the configuration-change list, and two
  risk features. Spec: `docs/24_ADOPTION_EXPORT.md`.
- **Still not exported, if the sources allow** (docs/24): `explorer_day` (questions per account per day, customer vs
  staff, from Nov 2024), `playbook_day` (executions per playbook per day), `ps_ticket` (support tickets, for the
  Requests page and two risk features). Leave a file out rather than write zeros.

## 4. Salesforce (blocked on access)

`sf_pull.py` reads Accounts, Opportunities, Contracts, Tasks and Events, read-only (docs/18). Ryan's login fails with
**"API is disabled for this User"**: his Salesforce user lacks the *API Enabled* permission. Any API route (CLI,
token, MCP) needs it.
- **Ask the Salesforce admin** for API Enabled on Ryan's user (a permission set), or better a read-only integration
  user (checklist: `docs/19_SALESFORCE_LIVE.md`).
- Then: `sf org login web --alias netomi --instance-url https://netomi-production.my.salesforce.com`, export
  `SF_INSTANCE_URL` and `SF_ACCESS_TOKEN` (docs/13 section 6 has the two lines), run `sf_pull.py` and
  `load_salesforce.py`, then `readiness.py`.
- **Read the readiness output and fix the settings to match the org:** unlinked account names → `account_overrides`
  in `code/salesforce/salesforce.json`; the renewal opportunity type, forecast categories and task subtypes it lists →
  `salesforce.json` and `n360.settings`. Pull every week or two; leave `N360_SALESFORCE` unset until there is an
  integration user.
- If API access is refused, tell Ryan: a CSV import (report exports in the same shape) can be added to the reader.

## 5. Risk model, once Salesforce is in

Ryan has already backfilled features from 4 Nov 2024 (usage goes back to May 2024). After the Salesforce load:

```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" features --from 2024-11-04 --every 7 --overwrite
```
```bash
python3 code/risk/risk_model.py --user n360 --password "$N360_CH_PASSWORD" train
```

`train` prints how past renewals were labelled (lost, kept on record, kept because a contract ran on, unconfirmed)
and a run id. Before adopting:
- **Set `prior_mean_rate`** in `code/risk/risk_model.json` to renewals lost ÷ renewals due over the last two or
  three years (currently 0.15, a guess), bump `version`, and train again.
- **Record known churns and downsells** that are in neither Salesforce nor the risk register:
  `risk_model.py ... outcome --customer "<name>" --date YYYY-MM-DD --outcome churned|downsold`.
- Then `adopt --run <id>` and `score --as-of all`. Docs: `docs/21_RISK_MODEL.md` sections 5 and 7.

## 6. The local runner and the Operations dashboard

`n360.ops_job` and `n360.ops_night` are declared in `code/schema.sql` (the runner creates them the same way). The
Operations dashboard (`n360-ops`, generated by `code/grafana/build_ops.py`) reads them and links to the runner on
127.0.0.1:8099. If you change what the runner writes, change both and keep the columns.

## Rules (unchanged)

- **Real data stays on company-approved machines.** Pass back error text, counts (readiness) and labels, never rows,
  names or amounts. Keep the N360 chat (docs/09) off with real data until the company approves it.
- **Don't rename N360 tables, columns or dashboard uids.** A contract change is Ryan's call, and the synthetic
  generator (`sample_data/generate_synthetic.py`) must change with the exporter.
- **Never commit `code/.env`, tokens, exports or Salesforce pulls.**
- **Don't loosen a type to make a load pass; fix the export.** A refused night (exit 3) leaves the last good night
  live; `load.py` names the table and column on a format error (exit 2).
- **Back up the history tables** weekly: `N360_BACKUP=~/n360-backups code/ops/backup.sh` (risk history, register,
  forecast history and the risk model's tables cannot be rebuilt from exports).

## Where to read more

| Doc | What |
|---|---|
| `docs/02_DATA_CONTRACT.md`, `code/schema.sql` | The export format and every column |
| `docs/24_ADOPTION_EXPORT.md`, `docs/25_ADOPTION_DETAIL.md` | Adoption tables, `cortex_user_day` spec |
| `docs/13_LAPTOP_POC.md` | Mac setup, nightly run, section 6: getting the risk model running |
| `docs/18_SALESFORCE.md`, `docs/19_SALESFORCE_LIVE.md` | The Salesforce reader; admin checklist |
| `docs/21_RISK_MODEL.md` | Running, training and governing the risk model |
| `docs/04_DASHBOARDS.md` | Every dashboard, panel by panel |
| `docs/08_DECISIONS.md` | What is decided, proposed and open |
| `CHANGES.md` | What changed, newest first |
