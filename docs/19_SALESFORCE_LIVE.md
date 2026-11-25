# 19 · Connecting to live Salesforce: plan

**Two different needs, two different connections.** Researched 25 Nov 2026; nothing here has been run against the
company org yet.

| Need | Right connection | Why |
|---|---|---|
| **A. Data into N360**: dashboards, nightly risk signals, history | The Salesforce REST API, read by `code/salesforce/sf_pull.py` (built, docs/18). It runs nightly as a read-only **integration user**, through an External Client App with the OAuth client credentials flow | A pipeline needs the same data every night, checked before it goes live, kept as history and backed up. An MCP server is built for a person or an agent asking questions, not for bulk nightly copies |
| **B. Live questions in Claude**: "what's the latest on Willow's renewal?", drilling into a record | **Salesforce Hosted MCP Servers**, connected to Claude as a connector, with the **SObject Reads** (read-only) server | Each person signs in with their own Salesforce login, so Claude sees only what that person may see in Salesforce. Nothing is copied |

The two work side by side. N360 answers "which customers are at risk and why", from stored, checked numbers.
Salesforce through MCP answers "what does the record say right now".

## A. The pipeline connection (do this first)

`sf_pull.py` already supports two ways to log in. **Production should use the second one.**

1. **Today, on the laptop:** your own session (`sf org login web`, then `sf org display --json`). This is good for a
   first look, but it runs as you, and the token expires.
2. **Production:** an External Client App with the client credentials flow, running as a dedicated integration user.
   Ask the Salesforce admin for the following:
   - An **integration user** (an API-only Salesforce Integration licence, if the org has them) with a **read-only
     permission set**:
     - Read on Account, Opportunity, Contract, Task and Event;
     - field access to the fields in `code/salesforce/salesforce.json`;
     - nothing else.
   - An **External Client App** (or Connected App):
     - OAuth enabled, with the `api` scope;
     - **client credentials flow** enabled, with the "run as" user set to the integration user;
     - access restricted to that user.
   - Send the **consumer key and secret through a password manager**, never in chat or email. They go in `code/.env`
     on the laptop as `SF_LOGIN_URL`, `SF_CLIENT_ID` and `SF_CLIENT_SECRET`. On AWS they go in Parameter Store as
     SecureStrings, beside the other secrets (docs/12).
   - Answers to the mapping questions in docs/18:
     - how renewals are marked;
     - which forecast categories count as safe;
     - which currency Amount is in;
     - where CSMs log calls and meetings.
3. **Then:** run one pull by hand and check the "not linked" list. Add `account_overrides` for any names that differ.
   Then set `N360_SALESFORCE=1` so it runs every night.

API use is small: a handful of SOQL queries a night, each paged by Salesforce. That is far inside any org's daily
API allowance.

**On AWS later:** the server can run the same pull nightly, with the secret in Parameter Store. That needs the
server's network to reach the org's `my.salesforce.com` host (one allowed host). It is a small addition to `update.sh`
and `server_loads`; not built yet.

## B. Live questions in Claude (a connector)

**Salesforce's own option.** Salesforce Hosted MCP Servers became generally available in April 2026 (Enterprise
Edition and up).
- An admin enables them in Setup.
- The **SObject Reads** server is read-only. It covers queries, schema, search across objects, and walking from a
  record to its related records.
- The **SObject All** server can also write and delete. **Don't enable that one for this.**
- People connect from Claude (Customize → Connectors → add a custom connector, or the Salesforce connector in
  Claude's directory). They sign in with their own Salesforce login via OAuth, against an External Client App the
  admin creates for MCP.
- Sources:
  - [Salesforce announcement](https://developer.salesforce.com/blogs/2026/04/salesforce-hosted-mcp-servers-are-now-generally-available)
  - [Hosted MCP guide](https://developer.salesforce.com/docs/platform/hosted-mcp-servers/guide)
  - [Connecting Claude (Salesforce workshop)](https://developer.salesforce.com/workshops/try-headless-360/3-connect-claude)
  - [Claude directory listing](https://claude.com/marketplace/connectors/salesforce-headless-360)

**Avoid the local Salesforce DX MCP server** (`@salesforce/mcp`, runs on the `sf` CLI login). Salesforce reports it
reaches end of life on 2 November 2026 ([repo](https://github.com/salesforcecli/mcp)). Avoid community MCP servers
too: they add a third party between Claude and customer data.

**Caveats:**
- **Permissions decide what Claude sees.** The MCP server inherits each user's Salesforce access, so loose
  permissions in Salesforce become loose answers in Claude. Review the profiles and permission sets of the people who
  will connect.
- **Company approval comes first.** This sends customer records from Salesforce to Claude, the same question as the
  N360 chat (docs/08, docs/09). It needs the company's approval of sending customer data to Anthropic before anyone
  connects it.
- **Sources disagree on status.** Some describe the connector as beta and others as GA; check the admin's Setup page
  for the org's actual status.

## Recommended order

1. **Now:** ask the Salesforce admin for the integration user and External Client App (section A), and the mapping
   answers.
2. **This week:** run the first live pull from the laptop. Review the not-linked accounts, then turn on nightly pulls.
3. **Once company approval for sending customer data to Claude is in place:** the admin enables the SObject Reads
   hosted MCP server, and the CS team connects it in Claude.
4. **Later:**
   - the same pull on the AWS server;
   - other sources in docs/17 section 3 (platform outcome data, ticket priority), each through its own reader, in the
     same staged-and-checked way.
