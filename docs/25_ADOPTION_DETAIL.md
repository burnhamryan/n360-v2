# 25 · Adoption detail tables (for building the Adoption dashboard)

Added 27 Nov 2026. The work Claude now exports, every night, everything the v1.1 Adoption view showed. Build panels
from these tables. No company data is needed to build: `code/schema.sql` has the exact columns and the synthetic
generator can be extended from it. Do not rename tables or columns.

## Rules that hold for every column

- **NULL = not read** → show "Not reported yet" (grey). **0 = read and none** → show 0 / "Not used". Never turn
  NULL into 0, and never colour a NULL red.
- These tables hold the **newest read only** (replaced whole each load). History is in `va_config_snapshot`
  (weekly config) and `cortex_day` (daily Cortex).
- Never add Cortex sessions, Explorer questions and playbook executions together: different things.
- Never sum containment across channels (unchanged rule).

## Tables

### `feature_window`: one row per VA × feature (feeds `v_feature_use`)
`feature` ∈ V8 playbooks (executions on V8+ VAs, 31d) · Analytics assistant (customer questions, 30d) · Authoring
tool (Cortex sessions, 30d, US VAs only). `amount`, `unit`, `last_day`, `window_start/end`, `user_type`
(Cortex = `unknown`: the source has no user id). `v_feature_use` now reads it, so the Coverage table fills.

### `va_adoption`: one row per VA (the v1.1 per-VA table and the six-feature grid)
| Group | Columns |
|---|---|
| Identity | account, bot_id, va, region |
| Engine | engine_version, engine_major, playbooks_live, v8_playbooks, legacy_intents |
| Executions (31d, `window_start..window_end`) | executions, conversations, handoffs, playbooks_executed, last_execution, idle_playbooks |
| Calibration | calibration_runs, calibration_completed, calibration_last |
| Testing | behavior_validation (enabled/off/not set), test_conversations, test_days, test_suites, test_runs, test_last |
| Recipes | recipes, recipes_production, recipe_actions |
| MCP servers | mcp_live, mcp_not_live, mcp_names |
| Supervisory rules | supervisory_live, supervisory_default, supervisory_names |
| Cortex | cortex_sessions_30d, cortex_last (US only; Netomi or customer not known: say so on the panel) |
| Explorer | explorer_questions_30d, explorer_users_30d, explorer_last (customer users only) |

### `playbook_window`: one row per production playbook (complexity and live-but-idle)
account, bot_id, va, task_id, playbook, instruction_chars, tools, trigger_chars, length_band
(`<200 (short)`, `200-999`, `1000-2999`, `3000-5999`, `6000-9999`, `10000+`), counts_as_v8, executions (per-playbook
window), idle (V8 and no execution in the window), window_start/end, creator_domain (netomi.com = Netomi, else customer).

### `cortex_day`: per account per day since 5 Oct 2026
authoring_sessions summed over the account's US VAs; `user_type = 'unknown'`. A day read with no session is 0.

## What the v1.1 Adoption view had (parity target)

1. **KPI tiles**: playbook executions 31d (+ playbooks executed, last execution), conversations 31d, handoffs 31d.
2. **Per-VA table**: executions, conversations, handoffs, playbooks executed, last execution.
3. **Feature adoption grid**: six features × VAs, each cell a count plus a note:
   Cortex (sessions, last used) · Test / playbook validation (test runs, validation conversations) · Calibration
   (runs, completed, last) · V8/V9 (engine + playbooks live) · Action-oriented recipes (recipes, actions, in production) ·
   Explorer (questions, customer users, last used). Each has a "what adopted means" line:
   - Cortex: used to create or edit a production playbook in the last 30 days
   - Test / validation: run on every playbook version before promotion
   - Calibration: at least monthly on Tier 1 and Tier 2 use cases
   - V8 / V9: production VA running on V8 or V9; target 100%
   - Recipes: at least one production recipe performs a write against a customer system
   - Explorer: at least one question by a customer user in the last 30 days, Production or Staging; Netomi staff not counted
4. **Config per VA**: V8 live (0 with the reason when the VA runs below V8), legacy intents, idle, calibration,
   Behavior Validation, recipes, MCP servers (names live / set up not live), supervisory rules (default vs custom).
5. **Playbook complexity**: per playbook instructions, tools, trigger, short (<200), length band, executions;
   plus a band-count table per VA.
6. **Live but idle**: V8 playbooks in production with no execution in the window, listed per VA.

Keep the drill path Portfolio → Customer → VA. Customer-level panels filter on `account`, VA-level on `bot_id`.
