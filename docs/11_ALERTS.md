# 11 · Alerts: off for now, ready to configure

**All 7 alert rules ship paused** (decided 24 Nov 2026: not needed yet). They are visible in Grafana under
Alerting, never evaluated, and never send anything. Their queries are still tested in CI (`test_alerts.py`),
so they will work the day you switch them on.

Everything is configured in one file, `code/grafana/build_alerts.py`, then generated (never edit the JSON):

```bash
python3 code/grafana/build_alerts.py            # writes code/grafana/provisioning/alerting/*.json
docker compose restart grafana                  # Grafana reads alerting files at start-up
```

## Turn alerts on

| You want | Change in `build_alerts.py` |
|---|---|
| All rules on | `ALERTS_ON = True` |
| Only some rules | keep `ALERTS_ON = False`, list them: `TURN_ON = {'n360-load-failed', 'n360-va-silent-dropped'}` |
| Who receives them | `AUDIENCE`: replace the `example.invalid` addresses (owner; CS leadership and CSM) |
| How often they repeat | `repeat` in `notify_file()`: immediately = 4h, daily = 24h, weekly = 168h |
| A rule's audience or cadence | its `audience` / `cadence` in `RULES` |

Email needs SMTP set up in Grafana (`GF_SMTP_*` in `docker-compose.yml` / `.env`); other channels (Slack, Teams,
webhook) are a different `type` in the contact point.

## The rules

| uid | Fires when | Audience · cadence |
|---|---|---|
| `n360-load-failed` | The latest nightly run was stopped by a data check | owner · immediately |
| `n360-data-stale` | No loaded run with data through yesterday, from 10:00 ET | owner · daily |
| `n360-source-stale` | A source is behind (amber 2 / red 3 days, in `n360.settings`) | owner · daily |
| `n360-va-silent-dropped` | A VA went silent or dropped on the newest settled day (never provisional) | owner · daily |
| `n360-cross-account` | 3+ customers flagged the same way on the newest settled day | owner · daily |
| `n360-over-billed` | A line or pool went above 100% of billed since the night before | CS · weekly |
| `n360-crossing-soon` | A line newly crosses billed within 30 days, or its crossing moved 14+ days earlier | CS · weekly |

Thresholds are not in the rules: they are in `n360.settings` (`code/schema.sql`), shared with the dashboards.
To check what would fire on today's data without turning anything on:

```bash
python3 code/grafana/test_alerts.py --user grafana --password "$GRAFANA_CH_PASSWORD" --scenario final
```
