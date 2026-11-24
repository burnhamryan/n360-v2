# CHANGES · 24 Nov 2026 · backlog B1, B2, B3, B3a

All tested on synthetic data in a Linux container (Docker 29.9, ClickHouse 25.8.33.6, Grafana 13.3.1,
ClickHouse plugin 4.23.5, Python 3.13). **Not tested on macOS / Docker Desktop.** Paths match the pack.

## Two fixes you need before the first start on the Mac

| # | Problem | Effect if not fixed | Fix |
|---|---|---|---|
| 1 | `init/grafana_user.sh` gave Grafana's user `readonly = 1`. The ClickHouse plugin sets `max_execution_time` on every query, which `readonly = 1` refuses | Data source health check fails; **every panel errors** | `readonly = 2 CONST`: still no writes or DDL (tested), and a query cannot switch `readonly` off (tested) |
| 2 | `GF_INSTALL_PLUGINS` is deprecated in Grafana 13; plugin version unpinned | Warning now, breakage later; plugin version drifts | `GF_PLUGINS_PREINSTALL_SYNC` with `GRAFANA_CH_PLUGIN_VERSION` from `.env` (4.23.5, latest on 24 Nov 2026). A failed install now stops start-up instead of leaving blank panels |

Also check: the zip stored every file as owner-only (`600`, folders `700`). Grafana in Docker runs as
another user and could not read the provisioning folder (no N360 folder, no dashboard). A git checkout
gives normal `644`; if you unzip the pack directly, run `chmod -R go+rX` on it.

## What changed

| File | Change | Item |
|---|---|---|
| `code/docker-compose.yml` | Plugin preinstall as above | B1 |
| `code/.env.example` | `GRAFANA_CH_PLUGIN_VERSION=4.23.5` | B1 |
| `code/init/grafana_user.sh` | `readonly = 2 CONST`, with the reason in a comment | B1 |
| `code/grafana/test_panels.py` (new) | Runs every panel and variable query in the dashboard JSON with Grafana macros expanded; fails on any error or empty panel | B1, B3 |
| `sample_data/generate_synthetic.py` | `--nights N`, `--bad-night k`. Multi-night: per-VA random streams (a day reads the same every night), planted events on fixed dates, newest 2 days read 3% and 10% low until they settle. Bad night: Northwind's line total is off by 1, so `line_measured_reconciles` refuses it. **Single-night output unchanged** (compared file by file, run_id aside) | B2 |
| `code/ci.sh` (new), `.github/workflows/ci.yml` (new) | Generate 14 nights → load in order (exactly night 9 refused) → check `runs` and history → regenerate dashboards and require the committed JSON to match → run every panel query. Two jobs: chdb, and ClickHouse server 25.8 as user `grafana` (created from `init/grafana_user.sh`, so it is defined once) | B3 |
| `chat/n360_chat_instructions.md` (new) | Instructions for the Claude people chat with: dashboards first, then views; the 12 rules from `03`/`07` | B3a |
| `chat/mcp_config.example.json` (new) | `mcp-grafana@2.2.0`, write tools off, 16-tool allowlist | B3a |
| `code/grafana/mcp_service_account.sh` (new) | Creates the Viewer service account and prints a token | B3a |
| `docs/09_CHAT.md` (new) | Setup, the three locks, the trap-question test | B3a |
| `docs/06_BACKLOG.md`, `docs/08_DECISIONS.md`, `README.md` | B3a added; 4 open questions; status table | |
| `.gitignore` (new) | Generated rows, `code/.env` | |

New dependencies: none in the pipeline. Chat needs `uv` on the machine running Claude.

## How it was tested

| Check | Result |
|---|---|
| Pack as received: generate, load (chdb), build | 8 × PASS, LOADED; rebuilt dashboard JSON identical to the shipped one |
| B1: `docker compose up`, load through the server | 8 × PASS; Grafana shows folder N360, Contracts renders all 6 panels in Chromium (no errors, no empty panels) |
| B1: Grafana user | Reads `n360`; refused on `n360_next`, INSERT, DROP, CREATE, `SETTINGS readonly = 0` |
| B1: UI edits | Saving the provisioned dashboard refused |
| B2: 14 nights, night 9 bad | 13 loaded + 1 failed_checks; `line_totals_history` 13 nights; after the bad night live tables stayed on night 8; settled days identical across nights |
| B2: edge cases on the final night | Atlas 282.6%; Harbor Chat crosses 17 Dec (≤ 30 d); Lakeside silent 9 days (last 3 provisional); 13 Nov spike on 3 accounts; no false drops from settling |
| B3: `ci.sh chdb` and `ci.sh server` (against a fresh ClickHouse 25.8 volume) | Both pass |
| B3: planted faults | Broken panel SQL, a check failing on a good night, hand-edited dashboard JSON: each fails CI |
| B3a: chat | 10 trap questions answered correctly by a fresh Claude through Grafana (table in `09_CHAT.md`); a write via `query_sql` refused |

**Not tested:** the workflow on GitHub itself (no repository attached); macOS / Docker Desktop; the
chat config in Claude Desktop (tested with a stdio client against the same binary and flags).
In this container ClickHouse needed a lower `nofile` limit and the plugin had to be mounted (no DNS
from containers). Both were done in a test-only override file, not in the pack.

## Found, not fixed (for B4 / B10, or your decision)

Contracts dashboard as rendered:
1. **"Lines above billed" shows 2, but 3 entitlements are over** (Atlas, Pinecrest Chat, and the
   Bluebird pool at 122.3%). Its description says pools are "counted once, as their pool"; the query
   only reads `v_line_status`.
2. **Harbor Voice (not measurable) is plotted** in "Daily use per contract line". `line_day` carries
   engaged as `measured` for it. Rule: not measurable shows no usage.
3. "Contract unit" shows "Not reported yet" for lines with no SOW override: an empty string is a real
   "none", not "not read".
4. Formatting against rule 14: no thousands separators; dates as `2026-10-31 00:00:00`; pool % with
   no unit or decimal; series names prefixed "used " / "containment ".

Open questions (also in `08_DECISIONS.md`):
5. **Usage after a line's end date.** Synthetic `reported_measured` counts every day to data-through.
   Atlas: 282.6% (all days) vs 268.4% (within term); Lakeside also. Does the real pipeline stop at the end date?
6. **Term-end convention.** Synthetic ends a 12-month line on the last day of the month before its
   start month (29 Jan start → 31 Dec end), while the pool ends 28 Jan. Does the contract sheet work that way?
7. **Chat with real data** sends query results to Anthropic; that is a company decision.
8. **Who may chat, and with which customers.** Grafana OSS can't restrict rows per person; this would
   need ClickHouse row policies.

Version notes: chdb 4.5.1 embeds ClickHouse 26.11, newer than the pinned server 25.8, so CI runs both.
ClickHouse 25.8 image resolved to 25.8.33.6. Grafana plugin 4.23.5 and mcp-grafana 2.2.0 were the
latest on 24 Nov 2026.
