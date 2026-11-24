# 07 · Working rules

How Ryan wants this work done. These came from real mistakes; they are not style preferences.

## Data and numbers

1. **Empty is never a gap.** A broken dataset, an unread source and a real zero look identical in
   an empty panel. NULL renders as "Not reported yet", never 0, never red.
2. **Never present different measures as comparable.** Chat and email deflection have different
   formulas and denominators. Containment is per VA; never one number across channels.
3. **One contract per view.** Never average or sum across contracts with different terms. A pool is
   measured once, at the pool; its lines never against billed alone.
4. **Rules belong in code, once.** A business rule lives in one function upstream. Views divide;
   dashboards display. If you need a rule in two places, call the one place.
5. **Every number carries its provenance.** Source, filter, window, capture date, in the panel
   description. Rolling windows move; frozen numbers say when they were frozen.
6. **No invented numbers, names or copy.** Missing fact → a labelled placeholder or an open
   question. Synthetic data is labelled `[SYNTHETIC]` where it could be mistaken for real.
7. **The strict types are a feature.** If a load fails on a type, the data is wrong. Fix the
   producer.

## Building

8. **Dashboards are code.** Generated JSON in Git, provisioned read-only. Never hand-edit dashboard
   JSON or click-build in the UI.
9. **Test before you say done.** Load synthetic data, all checks pass, every panel query runs.
   Say exactly what you tested and what you could not.
10. **Change only what was asked.** No drive-by renames, no reformatting, no new dependencies
    without a reason in `CHANGES.md`.
11. **Keep names stable.** Table, column and dashboard uids are contracts with the company side.
12. **Portable code.** No absolute paths; config by arguments and env vars; pinned versions
    (ClickHouse 25.8, Grafana 13.3.1 OSS; check for newer patch releases and say so, don't silently bump).

## Words on screen

13. **Plain words in titles and labels.** Table names, query numbers and internal IDs only in panel
    descriptions.
14. **Dates as "4 Dec 2026".** Percentages to 1 dp on screen. Thousands separators.
15. **Never draw a company logo or brand asset.** Not the company's, not a customer's. Placeholders only.

## Communicating with Ryan

16. **Lead with the answer.** Tables for anything comparative. No padding, no restating his question.
17. **Push back** when an assumption looks wrong; he'd rather be corrected than agreed with.
18. **When you can't source something, say so** and mark it open rather than filling the gap with
    a plausible guess.
19. **Proposed ≠ decided.** Anything in `08_DECISIONS.md` marked proposed or open stays a setting
    and a question until Ryan decides.
