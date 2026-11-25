# 20 · Datadog and Langfuse in Grafana: plan

**Plan only; nothing is built or connected yet.** Researched 25 Nov 2026.

N360 runs **Grafana OSS**. Neither Datadog nor Langfuse has a free, open-source Grafana data source, so the plan
uses an open-source general-purpose plugin for live panels, and our own nightly readers for anything that feeds risk
signals or history.

## What the market offers

| | Official Grafana data source | Works in Grafana OSS? | Open-source route |
|---|---|---|---|
| **Datadog** | Yes, `grafana-datadog-datasource`, by Grafana Labs | **No.** It needs a Grafana Enterprise licence or Grafana Cloud Pro/Advanced ([docs](https://grafana.com/docs/plugins/grafana-datadog-datasource/latest/install/)) | The Infinity data source calling Datadog's REST APIs; or a nightly reader into N360's ClickHouse |
| **Langfuse** | None | — | The Infinity data source calling Langfuse's **Metrics API**; or, if Langfuse is self-hosted, our ClickHouse plugin reading Langfuse's own ClickHouse; or a nightly reader |

**Infinity** (`yesoreyeram-infinity-datasource`) is maintained by Grafana Labs under the Apache-2.0 licence. It
queries any JSON, CSV or GraphQL API from the Grafana server. When keys are attached, an **allowed-hosts** list
restricts it to the hosts we name
([docs](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/setup/authentication),
[maintenance announcement](https://grafana.com/blog/2024/02/05/infinity-plugin-for-grafana-grafana-labs-will-now-maintain-the-versatile-data-source-plugin)).

## Recommendation: two lanes, like Salesforce (docs/19)

| Lane | For | How |
|---|---|---|
| **Live panels** (no copy) | Operations views, e.g. today's latency, errors and LLM cost per VA, queried when someone opens the dashboard | Infinity data sources for Datadog and Langfuse, provisioned as code like the ClickHouse one, with keys from the environment or Parameter Store |
| **Nightly readers** (copy into N360) | Anything that feeds a **risk signal**, the history tables, or a number that must match across dashboards | Small readers like `sf_pull.py`: pull a fixed set of daily aggregates per customer or VA, then stage, check and swap. They fill signals 23–27 in docs/17: tool and integration failure rate and latency, and LLM quality and cost per VA |

**Why not live panels for risk.** The risk rules need stored, checked numbers, history ("since last week") and
back-testing. A live API call gives none of that. It can also fail or be rate-limited at the moment someone opens
the dashboard.

## Datadog: plan

1. **Decide what we want from it** (Ryan, with whoever owns Datadog). Candidates for the architect view and risk
   signals:
   - per VA or customer: error rate, latency (p95), tool or MCP call failures, integration timeouts;
   - platform incidents (monitors in alert) that touched a customer.
2. **Find the join key.** Which Datadog tag identifies the customer or VA (for example `bot_id`, `account`, `tenant`)?
   Without it, Datadog data can't be tied to N360 customers. If no such tag exists, adding it is an engineering ask.
3. **Keys.** A Datadog **API key** plus an **application key** that is scoped read-only (metrics read, monitors read,
   events read), and the org's **site**: `api.datadoghq.com`, `api.us5.datadoghq.com`, `api.datadoghq.eu` and so on.
   These go in `.env` or Parameter Store, never in Git.
4. **Live lane:** an Infinity data source with `DD-API-KEY` and `DD-APPLICATION-KEY` headers. Its allowed hosts are
   the Datadog site only. Panels call the timeseries query and monitor APIs. Datadog's API rate limits apply per org,
   so live panels should be few and use modest refresh intervals.
5. **Nightly lane:** `code/datadog/dd_pull.py` pulls daily aggregates per VA for the last N days, and
   `load_datadog.py` stages, checks and swaps them, as for Salesforce. Signals and the architect view read them.
6. **Not recommended:** buying Grafana Enterprise for the official plugin. It goes against the open-source
   self-hosting decision (docs/08), and the nightly lane is needed for risk signals anyway.

## Langfuse: plan

1. **Cloud or self-hosted, and which version?** This decides the route:
   - **Langfuse Cloud or self-hosted v4+:** Metrics API **v2** (`GET /api/public/v2/metrics`). It returns aggregated
     metrics with dimensions, such as count, latency, tokens, cost and time to first token, grouped by trace name,
     release, model and so on. It defaults to 100 rows per query
     ([docs](https://langfuse.com/docs/metrics/features/metrics-api),
     [changelog](https://langfuse.com/changelog/2025-12-17-v2-metrics-and-observations-api)).
   - **Self-hosted v3:** Metrics API v1.
   - **Self-hosted, any version:** Langfuse stores traces in ClickHouse, which can be queried directly
     ([docs](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse)). Our existing ClickHouse plugin
     could read it through a read-only user. **Caution:** Langfuse's tables are internal and change between versions,
     so prefer the API.
2. **Find the join key.** Do traces carry the customer or VA in their metadata, tags, user ID or session? Without it,
   LLM cost and quality can't be put against a customer.
3. **Keys.** A Langfuse **public key and secret key** for the project. Infinity sends them as HTTP Basic auth. Its
   allowed host is the Langfuse host only (for example `cloud.langfuse.com`, `us.cloud.langfuse.com`, or our own
   domain).
4. **Live lane:** an Infinity data source for panels such as LLM latency and cost per VA today, and error-level
   observations.
5. **Nightly lane:** `code/langfuse/lf_pull.py` reads daily per-VA aggregates through Metrics API v2, and possibly
   evaluation scores. Candidate risk signals, all thresholds to be agreed:
   - LLM error rate;
   - p95 latency;
   - cost per conversation trend;
   - evaluation or quality score below target.

## How it would be set up in N360 (when we build it)

- **Plugin:** add `yesoreyeram-infinity-datasource@<pinned version>` beside the ClickHouse plugin in
  `GF_PLUGINS_PREINSTALL_SYNC`. Pin an exact version and verify it, as for ClickHouse.
- **Data sources as code:** `grafana/provisioning/datasources/datadog.yaml` and `langfuse.yaml`.
  - Keys come in through Grafana's `$__env{...}` interpolation, from `.env` on the laptop or Parameter Store on AWS
    (the same path as the Google secret, docs/12).
  - Allowed hosts are set.
  - Each file is provisioned only when its keys are set, so the stack starts without them.
- **AWS network:** the server already reaches the internet outbound. Nothing inbound changes.
- **Who sees it:**
  - Grafana OSS has no per-data-source permissions (an Enterprise feature). Anyone who can open a dashboard sees
    what its panels query.
  - Only Editors and Admins can run ad-hoc queries in Explore.
  - So keep read-only keys, and don't put raw logs or prompts (which may contain customer conversation text) on
    dashboards.
- **Tests:** as for Salesforce, a fake API for each, synthetic data that plants each signal, and CI.

## Decisions and asks

1. **What we want from each tool.** Which signals and panels; section 1 of each plan has candidates.
2. **Join keys.** The Datadog tag and the Langfuse trace field that identify a customer or VA.
3. **Langfuse deployment and version.** Cloud, or self-hosted and which version.
4. **Read-only credentials.** Datadog API key, scoped application key and site; Langfuse public and secret keys.
5. **Privacy.** Langfuse traces can contain conversation text. Panels and readers should use aggregates only.
   Sending any of it to Claude falls under the same company approval as the rest (docs/08).

**Separate from Grafana.** Datadog also offers an MCP server for asking Claude questions directly. This Claude
account has a Datadog connector that needs signing in. That is the "live questions" lane of docs/19, not a Grafana
data source.
