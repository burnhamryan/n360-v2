#!/usr/bin/env python3
"""Clone every n360 table's per-account rows K times (account ' #k', bot ids remapped) to reach real volumes. SYNTHETIC ONLY: never run against real data.
Run after code/ci.sh server (or any synthetic load) on a scratch ClickHouse:  CH_USER=n360 CH_PASSWORD=... python3 code/perf/scale_up.py 4
4 copies = 65 customers, 110 VAs, 75k va_day rows: above the real volume (27 customers, ~60 VAs) on 27 Nov 2026."""
import os, sys, urllib.request, base64
K = int(sys.argv[1])
AUTH = {'Authorization': 'Basic ' + base64.b64encode(f"{os.environ.get('CH_USER', 'default')}:{os.environ.get('CH_PASSWORD', '')}".encode()).decode()}
URL = os.environ.get('CH_URL', 'http://localhost:8123')
def Q(sql):
    r = urllib.request.Request(URL + '/', data=sql.encode(), headers=AUTH)
    return urllib.request.urlopen(r).read().decode()
tables = [l.split('\t') for l in Q("SELECT name, engine FROM system.tables WHERE database='n360' AND engine != 'View' FORMAT TSV").split('\n') if l]
for t, eng in tables:
    cols = [l.split('\t') for l in Q(f"SELECT name, type FROM system.columns WHERE database='n360' AND table='{t}' ORDER BY position FORMAT TSV").split('\n') if l]
    names = [c for c, _ in cols]
    if 'account' not in names:
        print('skip', t); continue
    for k in range(1, K + 1):
        sel = []
        for c, ty in cols:
            base = ty.replace('Nullable(', '').replace('LowCardinality(', '').rstrip(')')
            if c == 'account':
                e = f"concat(src.account, ' #{k}')"
            elif base == 'UUID' and c in ('bot_id',):
                e = f"toUUID(concat(substring(toString(src.`{c}`), 1, 34), '{k:02x}'))"
                if ty.startswith('Nullable'): e = f"if(isNull(src.`{c}`), NULL, {e})"
            elif base == 'String' and c in ('display_name', 'va_name', 'va', 'series_key', 'entitlement', 'line_key', 'customer', 'name_n360', 'name'):
                e = f"replaceAll(src.`{c}`, src.account, concat(src.account, ' #{k}'))" if c != 'va_name' and c != 'va' else f"concat(src.`{c}`, ' #{k}')"
                if c in ('display_name',): e = f"concat(src.`{c}`, ' #{k}')"
            else:
                e = f"src.`{c}`"
            sel.append(f'{e} AS `{c}`')
        Q(f"INSERT INTO n360.`{t}` SELECT {', '.join(sel)} FROM n360.`{t}` AS src WHERE src.account NOT LIKE '% #%'")
    print('cloned', t, Q(f"SELECT count() FROM n360.`{t}`").strip())
