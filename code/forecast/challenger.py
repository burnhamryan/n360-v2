#!/usr/bin/env python3
"""Challenger forecast and backtest harness (backlog B21). Runs in Python, beside the pipeline; never in Grafana.

The forecast of record stays the straight line (last-30-day rate) computed upstream and stored in n360.forecast.
This script never writes n360.forecast. It backtests a weekday-aware challenger against the straight line on the
loaded daily series and writes every prediction to n360.forecast_challenger, plus a markdown report.

    python3 code/forecast/challenger.py --chdb /tmp/n360 --report docs/10_CHALLENGER_BACKTEST.md
    python3 code/forecast/challenger.py --user n360 --password ...            # server, loader user

Models (each predicts cumulative use d days after a cutoff, from data up to the cutoff only):
  straight line   used + (last 30 days / 30) x d                     (the forecast of record's formula)
  weekday HW      additive Holt-Winters with a 7-day season, fixed smoothing (alpha 0.2, beta 0.01, gamma 0.2),
                  fitted on up to 365 days before the cutoff; daily forecasts floored at 0 and summed
Backtest: weekly cutoffs (Sundays) from 60 days after a series starts to (last day - d), d = 30, 60, 90.
Error: APE = |actual - predicted| / actual; MAPE per series, model and horizon.
Adoption bar (08, decided): better 30-day MAPE on most lines for 8 straight weeks, plus Ryan's approval. The report
shows the weekly comparison for the newest 8 cutoffs; adopting is never automatic.
"""
import argparse, datetime as dt, json, os, sys, tempfile, uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from load import CH  # noqa: E402

HORIZONS = (30, 60, 90)
ALPHA, BETA, GAMMA, SEASON, FIT_DAYS = 0.2, 0.01, 0.2, 7, 365
MODELS = ('straight line', 'weekday HW')


def series(ch):
    """{series_key: (account, label, {day: measure})} for every line and pool with a stored forecast."""
    fc = ch.rows("SELECT f.series_key AS sk, f.account AS account, toString(f.pool_id) AS pool FROM n360.forecast AS f")
    days = ch.rows("""SELECT d.series_key AS sk, l.account AS account, toString(l.pool_id) AS pool, toString(d.day) AS day,
                             d.measured AS m
                      FROM n360.line_day AS d INNER JOIN n360.contract_line AS l ON l.series_key = d.series_key
                      WHERE d.kind = 'line' AND d.day >= l.start_date""")
    out = {}
    for f in fc:
        if f['pool'] not in (None, '', '\\N'):
            rows = [r for r in days if r['account'] == f['account'] and r['pool'] == f['pool']]
        else:
            rows = [r for r in days if r['sk'] == f['sk']]
        by = {}
        for r in rows:
            if r['m'] is None:
                by = None              # a day not read: the series cannot be backtested honestly
                break
            d = dt.date.fromisoformat(r['day'])
            by[d] = by.get(d, 0) + int(r['m'])
        if by:
            out[f['sk']] = (f['account'], by)
    return out


def straight(hist, d):
    days = sorted(hist)
    last30 = sum(hist[x] for x in days[-30:])
    return sum(hist.values()) + last30 / 30.0 * d


def holt_winters(hist, d):
    days = sorted(hist)[-FIT_DAYS:]
    y = [hist[x] for x in days]
    if len(y) < 3 * SEASON:
        return straight(hist, d)
    level = sum(y[:SEASON]) / SEASON
    trend = (sum(y[SEASON:2 * SEASON]) - sum(y[:SEASON])) / SEASON / SEASON
    season = [y[i] - level for i in range(SEASON)]
    for t in range(SEASON, len(y)):
        s = season[t % SEASON]
        prev = level
        level = ALPHA * (y[t] - s) + (1 - ALPHA) * (level + trend)
        trend = BETA * (level - prev) + (1 - BETA) * trend
        season[t % SEASON] = GAMMA * (y[t] - level) + (1 - GAMMA) * s
    n = len(y)
    ahead = sum(max(0.0, level + h * trend + season[(n - 1 + h) % SEASON]) for h in range(1, d + 1))
    return sum(hist.values()) + ahead


def backtest(data):
    rows = []
    for sk, (acct, by) in data.items():
        days = sorted(by)
        first, last = days[0], days[-1]
        cum, running = {}, 0
        for x in days:
            running += by[x]; cum[x] = running
        c = first + dt.timedelta(60)
        c += dt.timedelta((6 - c.weekday()) % 7)            # Sundays
        while c <= last - dt.timedelta(min(HORIZONS)):
            hist = {x: v for x, v in by.items() if x <= c}
            for d in HORIZONS:
                target = c + dt.timedelta(d)
                if target > last or not cum.get(target):
                    continue
                for model, f in (('straight line', straight), ('weekday HW', holt_winters)):
                    rows.append(dict(model=model, series_key=sk, account=acct, cutoff=c.isoformat(), horizon_days=d,
                                     predicted=round(f(hist, d), 1), actual=float(cum[target])))
            c += dt.timedelta(7)
    return rows


def mape(rows):
    return round(100 * sum(abs(r['actual'] - r['predicted']) / r['actual'] for r in rows) / len(rows), 2) if rows else None


def report(rows, data_through, run_id):
    out = [f'# Challenger forecast backtest [SYNTHETIC]', '',
           f'Generated by `code/forecast/challenger.py` (run `{run_id}`), data through {data_through}. '
           'The forecast of record is unchanged: this is a comparison only.', '',
           '## MAPE, all lines and pools', '', '| Horizon | Straight line (record) | Weekday HW (challenger) | Forecasts |',
           '|---|---|---|---|']
    for d in HORIZONS:
        a = [r for r in rows if r['horizon_days'] == d and r['model'] == 'straight line']
        b = [r for r in rows if r['horizon_days'] == d and r['model'] == 'weekday HW']
        out.append(f'| {d} days | {mape(a)}% | {mape(b)}% | {len(a)} |')
    out += ['', '## 30-day MAPE per line or pool', '', '| Series | Straight line | Weekday HW | Better |', '|---|---|---|---|']
    for sk in sorted({r['series_key'] for r in rows}):
        a = mape([r for r in rows if r['series_key'] == sk and r['horizon_days'] == 30 and r['model'] == 'straight line'])
        b = mape([r for r in rows if r['series_key'] == sk and r['horizon_days'] == 30 and r['model'] == 'weekday HW'])
        if a is not None and b is not None:
            out.append(f"| {sk} | {a}% | {b}% | {'challenger' if b < a else 'straight line'} |")
    cutoffs = sorted({r['cutoff'] for r in rows if r['horizon_days'] == 30})[-8:]
    out += ['', '## Adoption bar: better 30-day MAPE on most lines, newest 8 weekly cutoffs', '',
            '| Cutoff | Lines where challenger is better | Of | Most? |', '|---|---|---|---|']
    streak = 0
    for c in cutoffs:
        per = {}
        for r in rows:
            if r['cutoff'] == c and r['horizon_days'] == 30:
                per.setdefault(r['series_key'], {})[r['model']] = abs(r['actual'] - r['predicted']) / r['actual']
        better = sum(1 for v in per.values() if len(v) == 2 and v['weekday HW'] < v['straight line'])
        most = better > len(per) / 2
        streak = streak + 1 if most else 0
        out.append(f'| {c} | {better} | {len(per)} | {"yes" if most else "no"} |')
    out += ['', f'**Weeks in a row the challenger met the bar (newest last): {streak} of 8 needed.** '
            'Even at 8, adoption needs Ryan\'s approval (08).', '',
            'On synthetic data the daily series are a flat rate with a weekly shape and noise, so a 30-day straight '
            'line already averages the weekly shape; expect little difference. Real series (growth, step changes) '
            'are the real test.']
    return '\n'.join(out) + '\n', streak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8123')
    ap.add_argument('--user', default='default')
    ap.add_argument('--password')
    ap.add_argument('--chdb')
    ap.add_argument('--report', help='write the markdown report here')
    a = ap.parse_args()
    ch = CH(a)
    data_through = ch.rows("SELECT toString(toDate(max(window_end))) AS d FROM n360.runs WHERE status = 'loaded'")[0]['d']
    data = series(ch)
    rows = backtest(data)
    run_id = str(uuid.uuid4())
    run_at = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    with tempfile.NamedTemporaryFile('w', suffix='.ndjson', delete=False) as f:
        for r in rows:
            f.write(json.dumps(dict(run_id=run_id, run_at=run_at, data_through=data_through, **r)) + '\n')
    ch.insert_ndjson('n360.forecast_challenger', f.name)
    os.unlink(f.name)
    text, streak = report(rows, data_through, run_id)
    if a.report:
        with open(a.report, 'w') as f:
            f.write(text)
    print(text.split('## 30-day')[0])
    print(f'{len(data)} series, {len(rows)} predictions written to n360.forecast_challenger (run {run_id})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
