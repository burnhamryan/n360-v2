# Screenshots · 25 Nov 2026 · [SYNTHETIC] data

All 12 dashboards after the v2 rebuild (`docs/16_DASHBOARD_GAPS.md`), in Grafana 13.3.1 OSS at 1440 px wide (dark
theme; 14 and 15 in the light theme). Loaded with 14 synthetic nights; night 9 was refused on purpose. Every customer,
VA and number is invented by `sample_data/generate_synthetic.py`. Maple Clinics shows the grey states (too early,
under review, billed not recorded, unmapped); Willow Energy shows renewal risk. The data is 6 days old when these
were taken, so the freshness stamp is amber. Regenerate after a dashboard change; CI does not check these.
