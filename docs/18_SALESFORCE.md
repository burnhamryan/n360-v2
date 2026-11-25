# 18 · Reading Salesforce

**N360 reads Salesforce, read-only: accounts, opportunities (renewals), contracts, and activity (tasks and events).**
It feeds the Risk dashboard's Renewal and Last contact signals, and the at-risk renewal amounts. It never writes to
Salesforce. Tested against a fake Salesforce API on synthetic data. **Not yet run against the company org.**

```
 Salesforce ──(REST API, SOQL, read-only)──► code/salesforce/sf_pull.py ──► sf_*.ndjson ──► load_salesforce.py ──► n360.sf_*
                                              (salesforce.json: what to read)                (stage, checks, swap)
```

## What is read (`code/salesforce/salesforce.json`)

**Standard objects and fields only, so it works on any org.** If the company names things differently, change the
JSON, never the code.

| N360 table | Salesforce object | Fields | Filter |
|---|---|---|---|
| `sf_account` | Account | Id, Name, Owner.Name, Type, LastActivityDate | all |
| `sf_opportunity` | Opportunity | Id, AccountId, Name, Type, StageName, ForecastCategoryName, CloseDate, Amount, IsClosed, IsWon, NextStep, Owner.Name, LastActivityDate | closing within the last 2 years or later |
| `sf_contract` | Contract | Id, AccountId, ContractNumber, Status, StartDate, EndDate, ContractTerm | all |
| `sf_activity` | Task + Event | Id, AccountId, ActivityDate, Type, TaskSubtype / EventSubtype, Subject, Status, Owner.Name, IsClosed | last 730 days, with an account (the risk model rebuilds past dates: "days since the last executive meeting" a year ago needs the year before it) |

The reader works out four values itself, all set in the same JSON:
- **Renewal** = the opportunity Type is one of `renewal_opportunity_types` (default `Renewal`).
- **Contact** = a past event, or a closed task whose subtype is in `contact.task_subtypes` (default Call, Email).
- **Executive meeting** = a contact whose subject contains one of `executive_keywords` (default EBR, QBR, executive).
- **Account link** = the Salesforce account name matches the N360 account key or display name, ignoring case.
  Where names differ, add the pair to `account_overrides`. Accounts with no match are listed by
  `n360.v_sf_unlinked` and after every pull.

**Open questions before relying on it** (Ryan, with the Salesforce admin):
1. Does the company mark renewals with Opportunity **Type = Renewal**, or with a record type or custom field?
2. Which **forecast categories** count as safe? The default is Commit and Closed, and "Omitted" counts as risk. Set
   these in `n360.settings`.
3. Is **Amount** the renewal value in one currency? If the org has multiple currencies, decide whether to read
   `convertCurrency(Amount)`.
4. Do CSMs log calls and meetings as **Tasks/Events on the Account**? Activity logged only on Contacts or Opportunities
   needs a different filter.
5. Is there a contract value or ARR field (open decision 3 in docs/08), and a risk field CSMs already fill in?

## Credentials (never in Git)

Two ways in. Put the values in the environment or in `code/.env`; `.env` is ignored by Git.

**A. A session you already have (quickest, for the laptop).** With the Salesforce CLI:
```bash
sf org login web --alias company
```
```bash
sf org display --target-org company --json
```
The second command prints an `accessToken` and an `instanceUrl`. Set:
```bash
export SF_INSTANCE_URL=https://yourcompany.my.salesforce.com SF_ACCESS_TOKEN='00D...'
```
The token expires with the session; repeat when it does.

**B. An integration app, for nightly use (ask the Salesforce admin).** An External Client App (or Connected App)
with the **OAuth 2.0 client credentials flow** enabled. It runs as an integration user with **read-only** access to
the objects above. Set:
```bash
export SF_LOGIN_URL=https://yourcompany.my.salesforce.com SF_CLIENT_ID=... SF_CLIENT_SECRET=...
```

## Run it

```bash
cd ~/Documents/Github/n360 && source .venv/bin/activate && set -a && . code/.env && set +a
```
```bash
python3 code/salesforce/sf_pull.py --out ~/n360-work/salesforce
```
```bash
python3 code/salesforce/load_salesforce.py --rows ~/n360-work/salesforce --user n360 --password "$N360_CH_PASSWORD"
```

To pull Salesforce with every nightly run, set `N360_SALESFORCE=1` (for example in `code/.env`). `nightly.sh` then
pulls and loads Salesforce first, so that night's risk snapshot sees it. A failed pull is logged and the previous
copy stays live.

**Checks.** Any failure refuses the pull, exit 3, and the previous copy stays live:
- ids are unique in each table;
- there is at least one account;
- every opportunity, contract and activity names an account.

Every pull is logged in `n360.sf_runs`.

**After a pull, run `code/readiness.py`.** It says how many customers are linked, whether renewals and contact were
recognised, and which labels your org uses, so the settings in `salesforce.json` can be checked against them.

**Past renewal dates come from here too.** The export keeps today's contract lines. For a past date that no line
covers, the risk model takes the close date of the account's next renewal opportunity, open or closed. That is how
last year's renewals become something the model can learn from.

## Data handling

The copy stays in ClickHouse on the laptop, or on the company's own AWS server. Activity subjects can contain
customer details, so treat them like the rest of the real data. Keep the Claude chat off with real data
(`docs/09_CHAT.md`).
