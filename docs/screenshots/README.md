# Screenshots · 25 Nov 2026 · [SYNTHETIC] data

All 12 dashboards after the v2 rebuild (`docs/16_DASHBOARD_GAPS.md`), in Grafana 13.3.1 OSS at 1440 px wide (dark
theme; 14 and 15 in the light theme). Loaded with 14 synthetic nights; night 9 was refused on purpose. Every customer,
VA and number is invented by `sample_data/generate_synthetic.py`. Maple Clinics shows the grey states (too early,
under review, billed not recorded, unmapped); Willow Energy shows renewal risk. The data is 6 days old when these
were taken, so the freshness stamp is amber. Regenerate after a dashboard change; CI does not check these.

`16-risk.png` and `17-risk-light.png` show the Risk dashboard (`docs/17_RISK_RESEARCH.md`, section 7), on the same synthetic data. The
Salesforce records come from `sample_data/generate_salesforce.py` through the fake API. The register entries and CSM
ratings are the ones `code/ci.sh` records.

`18-risk-model.png`: the Risk model dashboard (`docs/21_RISK_MODEL.md`) on the same synthetic data, with Willow Energy chosen. The score is the expert prior (too few synthetic outcomes to train), and the backtest panels are empty until there is enough history.

`07-adoption.png`, `19-adoption-customer.png` (Harbor Telecom) and `20-adoption-va.png` (Pinecrest Chat): the three
Adoption pages rebuilt on 27 Nov 2026 (docs/04 section 9), on the same synthetic data. Pinecrest Chat shows a
lapsed calibration (amber); Harbor Telecom has every feature adopted.

**27 Nov 2026: every dashboard re-shot** in Grafana 13.3.1 at 1440 px (dark), loaded by `code/ci.sh server` (14
synthetic nights, the fake Salesforce, the register and ratings CI records, the risk model, the synthetic runner
rows). New: `21-cortex-by-netomians.png` (the leaderboard over the last 3 months: Jordan Patel, rising, has just
overtaken Avery Kim) and `22-operations.png`. The light-theme shots (14, 15, 17) are from 25 Nov. Every person and
customer is invented.

`00-welcome.png`: the Welcome page, the home page since 27 Nov 2026.
