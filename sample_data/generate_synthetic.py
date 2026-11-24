#!/usr/bin/env python3
"""Generate a SYNTHETIC night of N360 data in the exact row format the loader expects.

    python3 sample_data/generate_synthetic.py --out sample_data/rows --last-day 2026-11-19
    python3 sample_data/generate_synthetic.py --out sample_data/nights --nights 14 --bad-night 9
        # 14 consecutive nights ending on --last-day, one folder each: <out>/<data_date>/rows/
        # Night 9 (1-based) is broken on purpose so load.py refuses it (line_measured_reconciles fails).

Every company, VA, contract and number here is invented. Nothing comes from real data. The
generator exists so the project can be built and tested anywhere, with every edge case the real
data has, and so load.py's eight data checks pass on it exactly as they do on a real night.

Edge cases deliberately present (and what each one tests):
  Bluebird Air        3 lines sharing ONE pooled entitlement (pool measured once; pooled lines have no billed)
  Pinecrest Retail    meaningful-engagement contract rule (billable < engaged) on Chat and Email;
                      Voice contracted but not implemented (measure 'not implemented', used NULL)
  Atlas Streaming     all-conversations contract rule, far above billed (~300%)
  Harbor Telecom      Voice 'not measurable' (contract unit the platform does not count); Chat crossing
                      billed within ~30 days
  Juniper Bank        two Chat lines with the same start date (line_key must stay unique)
  Summit Games        a VA read by the conversation-records reader: no handoff data (handoffs NULL)
  Lakeside Insurance  an Email VA that goes silent for the last 9 days (anomaly: went silent)
  Northwind Outfitters a normal account; joins a cross-account spike with Bluebird and Harbor
  Cedar Logistics     an unbilled VA (no contract line)
A same-day spike hits Northwind, Bluebird and Harbor 6 days before the last day (cross-account event).

Multi-night mode (--nights): every day's numbers come from per-VA random streams, so a day reads the
same on every night that includes it; the planted events stay on fixed dates (anchored to --last-day);
and the newest 2 days of each night read slightly low (still settling) and are complete the next night.
"""
import argparse, datetime as dt, json, math, os, random, uuid

SEED = 20261119
R = random.Random(SEED)
HIST_FROM = dt.date(2025, 1, 1)


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic/' + name))


def days(a, b):
    n = (b - a).days + 1
    return [a + dt.timedelta(i) for i in range(n)]


def sheet(d):
    return f'{d.day}-{d.strftime("%b")}-{d.year}'


def daily_series(base, start, last, weekend=0.55, growth=0.0, noise=0.12, rng=R):
    """conversations per day: weekly shape, gentle growth, noise; 0 before start"""
    out = {}
    for d in days(HIST_FROM, last):
        if d < start:
            out[d] = 0
            continue
        t = (d - start).days
        wk = weekend if d.weekday() >= 5 else 1.0
        v = base * wk * (1 + growth * t / 365) * (1 + rng.uniform(-noise, noise))
        out[d] = max(0, int(round(v)))
    return out


SETTLING = [0.97, 0.90]   # multi-night: share of the final count read on the second-newest and newest day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rows'))
    ap.add_argument('--last-day', default='2026-11-19', help='last data day (the window ends 23:59 UTC that day)')
    ap.add_argument('--nights', type=int, default=1, help='N consecutive nights ending on --last-day, one folder each')
    ap.add_argument('--bad-night', type=int, help='1-based night to break on purpose (multi-night only)')
    a = ap.parse_args()
    final = dt.date.fromisoformat(a.last_day)
    if a.nights == 1 and not a.bad_night:
        generate(final, final, a.out, multi=False)
        return
    if a.bad_night and not 1 <= a.bad_night <= a.nights:
        ap.error('--bad-night must be between 1 and --nights')
    for i in range(1, a.nights + 1):
        last = final - dt.timedelta(a.nights - i)
        out = os.path.join(a.out, (last + dt.timedelta(1)).isoformat(), 'rows')
        generate(last, final, out, multi=True, bad=(i == a.bad_night))


def generate(last, anchor, out, multi, bad=False):
    """one night: data through `last`; planted events dated from `anchor` (the final night's last day)"""
    data_date = last + dt.timedelta(1)
    run_id = str(uuid.uuid4())
    T = {t: [] for t in ['account', 'va', 'contract_line', 'line_va', 'pool', 'rule', 'source', 'va_day',
                         'line_day', 'line_day_billable', 'forecast', 'line_totals_history']}

    # ── rules
    rules = {
        'pinecrest-sow-2g': dict(kind='meaningful_engagement', status='active', label='Meaningful engagement (Pinecrest SOW, section 2G)',
                                 contract_text='[SYNTHETIC] Only contacts that require meaningful engagement by the AI count toward volume.',
                                 counts='Counts: the AI performs substantive processing of a customer message.'),
        'atlas-msa-a': dict(kind='all_conversations', status='active', label='Conversations (Atlas MSA, Exhibit A)',
                            contract_text='[SYNTHETIC] Every conversation counts.', counts='Counts: every conversation a user starts.'),
        'bluebird-sow-3c': dict(kind='contained', status='pending_probe', label='Contained conversations (Bluebird SOW, section 3C)',
                                contract_text='[SYNTHETIC] Contained conversations only.', counts='Not live: probe pending.'),
    }
    rule_hash = {k: uuid.uuid5(uuid.NAMESPACE_URL, k).hex[:12] for k in rules}

    # ── world: account -> lines; each line: channel, start, end, billed, va list [(name, channel, base, extra)]
    # extra keys: rule, pool, unmeasured, not_impl, cm (conversation records), silent_days, spike
    W = [
        ('Northwind Outfitters', [dict(ch='Chat', start='2026-02-01', months=12, billed=400000, vas=[('Northwind Chat', 'Chat', 1100, dict(spike=True))])]),
        ('Bluebird Air', [dict(ch='Chat', start='2026-01-29', months=12, billed=None, pool=0, vas=[('Bluebird Web', 'Chat', 9000, dict(spike=True))]),
                          dict(ch='InApp', start='2026-01-29', months=12, billed=None, pool=0, vas=[('Bluebird App', 'InApp', 6000, dict(spike=True))]),
                          dict(ch='SMS', start='2026-01-29', months=12, billed=None, pool=0, vas=[('Bluebird SMS', 'SMS', 1500, {})])]),
        ('Pinecrest Retail', [dict(ch='Chat', start='2026-04-01', months=12, billed=210000, rule='pinecrest-sow-2g', vas=[('Pinecrest Chat', 'Chat', 1800, dict(growth=0.6))]),
                              dict(ch='Email', start='2026-04-01', months=12, billed=150000, rule='pinecrest-sow-2g', vas=[('Pinecrest Email', 'Email', 420, {})]),
                              dict(ch='Voice', start='2026-04-01', months=12, billed=140000, not_impl=True, vas=[])]),
        ('Atlas Streaming', [dict(ch='Chat', start='2025-11-10', months=12, billed=750000, rule='atlas-msa-a', vas=[('Atlas Help', 'Chat', 6500, {})])]),
        ('Harbor Telecom', [dict(ch='Chat', start='2026-01-22', months=12, billed=95000, vas=[('Harbor Chat', 'Chat', 380, dict(spike=True))]),
                            dict(ch='Voice', start='2025-07-03', months=24, billed=None, unmeasured=True, unit='[SYNTHETIC] resolved calls',
                                 vas=[('Harbor IVR', 'Voice', 15000, {})])]),
        ('Juniper Bank', [dict(ch='Chat', start='2026-01-22', months=12, billed=80000, sheet_customer='Juniper Chat', vas=[('Juniper Chat', 'Chat', 260, {})]),
                          dict(ch='Chat', start='2026-01-22', months=12, billed=80000, sheet_customer='Juniper Marketing', vas=[('Juniper Marketing', 'Chat', 230, {})])]),
        ('Summit Games', [dict(ch='Chat', start='2026-09-05', months=12, billed=900000, vas=[('Summit NCW', 'Chat', 2400, dict(cm=True))])]),
        ('Lakeside Insurance', [dict(ch='Email', start='2025-11-30', months=12, billed=60000, vas=[('Lakeside Email', 'Email', 32, dict(silent_days=9))])]),
    ]
    pool_def = dict(account='Bluebird Air', pool=0, name='Bluebird Air: 3 lines, pooled entitlement', billed=3000000, start='2026-01-29')

    spike_day = anchor - dt.timedelta(6)
    va_daily = {}          # va name -> {day: (conv, engaged, handoffs)}
    acct_rows = []
    for acct, lines in W:
        acct_rows.append(acct)
        T['account'].append(dict(account=acct, display_name=acct, rocketlane_account_id=100000 + len(acct_rows),
                                 csm_rocketlane=f'csm{len(acct_rows)}@example.com', run_id=run_id))
        for idx, L in enumerate(lines):
            start = dt.date.fromisoformat(L['start'])
            end = dt.date(start.year + (start.month - 1 + L['months']) // 12, (start.month - 1 + L['months']) % 12 + 1, 1) - dt.timedelta(1)
            L['start_d'], L['end_d'] = start, end
            for (vname, vch, base, ex) in L['vas']:
                rn = random.Random(f'{SEED}/noise/{vname}') if multi else R      # per-VA streams: a day reads
                rr = random.Random(f'{SEED}/ratio/{vname}') if multi else R      # the same on every night
                conv = daily_series(base, HIST_FROM if not ex.get('cm') else start, last, growth=ex.get('growth', 0.0), rng=rn)
                rows = {}
                for d, c in conv.items():
                    if ex.get('silent_days') and d > anchor - dt.timedelta(ex['silent_days']):
                        c = 0
                    if ex.get('spike') and d == spike_day:
                        c = int(c * 2.6)
                    if multi and (last - d).days < len(SETTLING):
                        c = int(c * SETTLING[-1 - (last - d).days])
                    e = int(round(c * rr.uniform(0.82, 0.9)))
                    h = None if ex.get('cm') else int(round(e * rr.uniform(0.4, 0.6)))
                    rows[d] = (c, e, h)
                va_daily[vname] = (acct, vch, ex, rows)

    # ── VA reference rows + va_day
    def va_row(vname, acct, vch, ex, on_contract=True, line_label=''):
        bot = uid('bot/' + vname)
        live = uid('live/' + vname)
        src = 'conversation_records' if ex.get('cm') else 'aggregate'
        T['va'].append(dict(bot_id=bot, live_ref=live, consumption_source=src, account=acct, va_name=vname, region='US',
                            channels=[vch], line_labels=[line_label] if line_label else [], contract_start=None,
                            on_contract=on_contract, run_id=run_id))
        for d, (c, e, h) in sorted(va_daily[vname][3].items()):
            T['va_day'].append(dict(day=d.isoformat(), account=acct, bot_id=bot, live_ref=live, consumption_source=src,
                                    region='US', conversations=c, engaged=e, handoffs=h, run_id=run_id))
        return bot, live, src

    # unbilled VA
    cedar = daily_series(90, dt.date(2026, 5, 29), last, rng=random.Random(f'{SEED}/noise/Cedar Ops Chat') if multi else R)
    va_daily['Cedar Ops Chat'] = ('Cedar Logistics', 'Chat', {}, {d: (c, int(c * .85), int(c * .4)) for d, c in cedar.items()})
    T['account'].append(dict(account='Cedar Logistics', display_name='Cedar Logistics', rocketlane_account_id=100099,
                             csm_rocketlane='csm9@example.com', run_id=run_id))

    pool_members, pool_used, pool_last30 = [], 0, 0
    for acct, lines in W:
        for idx, L in enumerate(lines):
            sc = L.get('sheet_customer', acct)
            lk = f"{sc}|{L['ch']}|{sheet(L['start_d'])}"
            skey = f'{acct}|{idx}'
            rule = L.get('rule')
            kind = rules[rule]['kind'] if rule else None
            line_days = [d for d in days(L['start_d'], last)]
            conv = {d: 0 for d in line_days}; eng = dict(conv)
            for (vname, vch, base, ex) in L['vas']:
                bot, live, src = va_row(vname, acct, vch, ex, True, f"{acct} · {L['ch']}")
                T['line_va'].append(dict(line_key=lk, account=acct, bot_id=bot, va_name=vname, live_ref=live, consumption_source=src,
                                         region='US', mapping_basis='[SYNTHETIC] sheet VA ID', run_id=run_id))
                for d in line_days:
                    c, e, h = va_daily[vname][3][d]
                    conv[d] += c; eng[d] += e
            billable = None
            if kind == 'meaningful_engagement':
                billable = {d: int(eng[d] * 0.86) for d in line_days}
                for d in line_days:
                    T['line_day_billable'].append(dict(day=d.isoformat(), account=acct, line_key=lk, rule_id=rule, rule_hash=rule_hash[rule],
                                                       conversations=conv[d], engaged_flag=eng[d], billable=billable[d], run_id=run_id))
            meas = conv if kind == 'all_conversations' else billable if billable else eng
            if L.get('not_impl'):
                measure, used, last30 = 'not implemented', None, None
            elif L.get('unmeasured'):
                measure, used, last30 = 'not measurable', None, None
            else:
                measure = ('conversations (contract rule)' if kind == 'all_conversations' else 'billable (contract rule)' if kind else 'engaged')
                used = sum(meas.values()); last30 = sum(v for d, v in meas.items() if d > last - dt.timedelta(30))
            if L['vas'] and not L.get('not_impl'):
                for d in line_days:
                    T['line_day'].append(dict(day=d.isoformat(), account=acct, series_key=skey, kind='line', bot_id=None,
                                              conversations=conv[d], engaged=eng[d], measured=meas[d], run_id=run_id))
            billed = L['billed']
            pct = round(used / billed * 100, 2) if (used is not None and billed) else None
            term = (L['end_d'] - L['start_d']).days + 1
            T['contract_line'].append(dict(
                line_key=lk, series_key=skey, account=acct, idx=idx, sheet_customer=sc, channel=L['ch'],
                start_date=L['start_d'].isoformat(), end_date=L['end_d'].isoformat(), billed=billed,
                billed_as_stored=f'{billed:,}' if billed else '', csm_sheet='[SYNTHETIC] CSM', segment='Enterprise',
                pool_id=L.get('pool'), rule_id=rule, term_days=term, sheet_billed=billed, unit=L.get('unit', ''),
                measure=measure, rule_kind=kind, not_implemented=bool(L.get('not_impl')), unmeasured=bool(L.get('unmeasured')),
                reported_measured=used, reported_last30_measured=last30,
                reported_engaged_flag=sum(eng.values()) if L['vas'] else None,
                reported_consumed_convs=sum(conv.values()) if L['vas'] else None,
                reported_billable=sum(billable.values()) if billable else None,
                reported_pct_page=None if L.get('pool') is not None else pct, run_id=run_id))
            T['line_totals_history'].append(dict(data_date=data_date.isoformat(), run_id=run_id, account=acct, line_key=lk, billed=billed,
                                                 measure=measure, consumed_convs=sum(conv.values()) if L['vas'] else None,
                                                 consumed_measured=used, engaged_flag=sum(eng.values()) if L['vas'] else None,
                                                 pct_billed_page=pct))
            if L.get('pool') is not None:
                pool_members.append(lk); pool_used += used or 0; pool_last30 += last30 or 0
            elif used is not None and billed:
                T['forecast'].append(forecast_row(acct, skey, None, L['start_d'], L['end_d'], last, used, last30, billed, run_id))

    # pool + its forecast
    ps = dt.date.fromisoformat(pool_def['start']); pe = dt.date(ps.year + 1, ps.month, ps.day) - dt.timedelta(1)
    T['pool'].append(dict(account=pool_def['account'], pool_id=0, name=pool_def['name'], billed=pool_def['billed'],
                          billed_basis='[SYNTHETIC] one entitlement across 3 lines', start_date=ps.isoformat(), end_date=pe.isoformat(),
                          line_keys=pool_members, run_id=run_id))
    T['forecast'].append(forecast_row('Bluebird Air', 'Bluebird Air|pool0', 0, ps, pe, last, pool_used, pool_last30, pool_def['billed'], run_id))

    # unbilled VA rows and its daily series
    bot, live, src = va_row('Cedar Ops Chat', 'Cedar Logistics', 'Chat', {}, on_contract=False)
    for d, (c, e, h) in sorted(va_daily['Cedar Ops Chat'][3].items()):
        if d >= dt.date(2026, 5, 29):
            T['line_day'].append(dict(day=d.isoformat(), account='Cedar Logistics', series_key=f'Cedar Logistics|unbilled|{bot}',
                                      kind='unbilled', bot_id=bot, conversations=c, engaged=e, measured=e, run_id=run_id))

    for rid, r in rules.items():
        T['rule'].append(dict(rule_id=rid, rule_hash=rule_hash[rid], kind=r['kind'], status=r['status'], label=r['label'],
                              contract_text=r['contract_text'], source_document='[SYNTHETIC] contract.pdf', counts_plain=r['counts'],
                              decisions='[SYNTHETIC]', lines_measured=sorted(x['line_key'] for x in T['contract_line'] if x['rule_id'] == rid),
                              run_id=run_id))
    for sid, desc in [('consumption_days', 'Daily conversations and engaged per VA and per line'),
                      ('containment_days', 'Daily handoffs per VA'), ('consumption_rules', 'Contract-rule counts per line per day')]:
        T['source'].append(dict(source_id=sid, description='[SYNTHETIC] ' + desc, definition='', check_text='', last_day=last.isoformat(), run_id=run_id))

    if bad:   # a pipeline bug: one line's total disagrees with its daily series
        broken = next(x for x in T['contract_line'] if x['account'] == 'Northwind Outfitters')
        broken['reported_measured'] += 1

    os.makedirs(out, exist_ok=True)
    counts = {}
    for t, rows in T.items():
        with open(os.path.join(out, f'{t}.ndjson'), 'w') as f:
            for r in rows:
                f.write(json.dumps(r, separators=(',', ':')) + '\n')
        counts[t] = len(rows)
    run = dict(run_id=run_id, data_date=data_date.isoformat(), window_end=f'{last.isoformat()} 23:59:00',
               capture_utc=f'{data_date.isoformat()} 03:00:00', source_dir='synthetic', code_fingerprint='synthetic', row_counts=counts)
    with open(os.path.join(out, 'run.json'), 'w') as f:
        json.dump(run, f, indent=1)
    print('synthetic run', run_id, 'data', data_date.isoformat(), 'BROKEN ON PURPOSE' if bad else '',
          ' '.join(f'{k}={v}' for k, v in counts.items()))


def forecast_row(acct, skey, pool, start, end, last, used, last30, billed, run_id):
    """the straight-line forecast the real pipeline uses: last-30-day rate, horizon = earlier of contract end and M+3 month end"""
    rate = last30 / 30.0
    elapsed = (last - start).days + 1
    avg = used / elapsed if elapsed else 0
    m = last.month + 3; y = last.year + (m - 1) // 12; m = (m - 1) % 12 + 1
    m3 = dt.date(y + (m // 12), m % 12 + 1, 1) - dt.timedelta(1)
    hor = min(end, m3)
    f3 = round(used + rate * (hor - last).days)
    flags, cross, exhaust = [], None, None
    if elapsed < 60:
        flags.append(f'Early term: only {elapsed} days of history')
    if avg > 0 and abs(rate - avg) / avg > 0.5:
        flags.append(f'Step change: last-30-day rate is {rate / avg * 100:.0f}% of the term average')
    if used > billed:
        flags.append('Already above billed')
    elif rate > 0:
        to_bn = (billed - used) / rate
        if to_bn <= 36500:
            exhaust = last + dt.timedelta(days=math.floor(to_bn))
            if exhaust <= end:
                cross = exhaust
                flags.append(f'Projected to exceed billed by {exhaust.isoformat()}')
    return dict(account=acct, series_key=skey, pool_id=pool, start_date=start.isoformat(), end_date=end.isoformat(),
                horizon=hor.isoformat(), rate_per_day=rate, avg_per_day=avg, forecast_m3=f3, billed=billed,
                consumed_engaged=used, elapsed_days=elapsed, cross_date=cross.isoformat() if cross else None,
                exhaust_date=exhaust.isoformat() if exhaust else None, flags=flags, run_id=run_id)


if __name__ == '__main__':
    main()
