# 09 · Chat with the data (Grafana MCP server)

**People ask Claude questions about N360 in plain words; Claude answers from the same data and the
same panel queries as the dashboards, and cannot change anything.** Built on synthetic data
24 Nov 2026 (backlog B3a). Whether real data may be sent to Claude is **open** (`08_DECISIONS.md`).

```
 Claude (Desktop / Code / Project) ──stdio──► mcp-grafana 2.2.0 ──token (Viewer)──► Grafana ──► ClickHouse
   + chat/n360_chat_instructions.md            write tools off                         user grafana:
                                               16 tools allowed                        SELECT on n360 only,
                                                                                       readonly = 2 CONST
```

## Why it cannot change data

| Layer | Lock | Tested (synthetic, 24 Nov 2026) |
|---|---|---|
| ClickHouse user `grafana` | SELECT on `n360.*` only; `readonly = 2 CONST` | INSERT, DROP, CREATE refused; `SETTINGS readonly = 0` refused; `n360_next` unreadable |
| Grafana service account `n360-chat` | Viewer role | Creating a dashboard, deleting the data source refused |
| `mcp-grafana` | `--disable-write`, tool allowlist | Only the 16 tools below exist; an INSERT through `query_sql` is refused by ClickHouse |

`query_sql` passes SQL through unfiltered (its own help says so). The ClickHouse user is the real
guarantee; the other two layers keep the tool surface small.

## Setup (once per Grafana)

```bash
GRAFANA_ADMIN_PASSWORD=... code/grafana/mcp_service_account.sh   # prints a token once
```

Put the token in `chat/mcp_config.example.json` (copy it into the Claude Desktop config, or a Claude
Code `.mcp.json`; never commit the token). Put `chat/n360_chat_instructions.md` in the Claude
Project's instructions, or install it as a skill. Needs `uv` (`uvx`) on the machine.

## Tools exposed (`--enabled-tools search,datasource,dashboard,folder,navigation,sql,runpanelquery`)

`search_dashboards`, `search_folders`, `get_dashboard_by_uid`, `get_dashboard_summary`,
`get_dashboard_property`, `get_dashboard_panel_queries`, `list_dashboard_versions`, `run_panel_query`,
`list_datasources`, `get_datasource`, `check_datasources_health`, `list_sql_databases`,
`list_sql_tables`, `describe_sql_table`, `query_sql`, `generate_deeplink`.

Left out on purpose: `grafana_api_request` (any Grafana API call), alerting, annotations, rendering,
and every data source type N360 does not use.

## Trap-question test (B3a acceptance)

A fresh Claude given only the instructions file and these tools answered ten questions; checked
against the views.

| Question | Trap | Result |
|---|---|---|
| How much has Bluebird Air used? | Pooled lines have no billed; compare at the pool | Pass: 3,669,223 / 3,000,000 = 122.3% at the pool |
| Pinecrest containment rate? | One number across channels | Pass: per VA, Chat 49.1%, Email 48.3% (30 d) |
| Harbor Telecom Voice % of billed? | Not measurable; don't substitute engaged | Pass: "Not measurable" |
| Lines over billed? | Pool missing from the line stat | Pass: 2 lines + the pool; noted the stat shows 2 |
| Total usage across all customers? | Summing across measures and contracts | Pass: declined, gave the per-line table |
| Summit Games containment? | NULL handoffs read as 0% / 100% | Pass: "Not reported yet" |
| When does Harbor Chat run out? | Recomputing the forecast | Pass: 17 Dec 2026 from `forecast` |
| VAs quiet or dropped last week? | Provisional days as alerts | Pass: Lakeside silent; 17–19 Nov provisional |
| Pinecrest Voice tracking? | Not implemented shown as 0 | Pass: "Not implemented" |
| Anything unusual on 13 Nov? | Three customer stories instead of one event | Pass: one cross-account spike, 3 accounts |

Limits of this test: one run, synthetic data, the agent was the same model family as the builder.
Repeat it with real users' own questions before relying on it. `run_panel_query` refused a panel whose
`account` variable is "All"; the agent fell back to the panel's SQL.
