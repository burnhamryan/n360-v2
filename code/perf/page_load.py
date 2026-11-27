#!/usr/bin/env python3
"""Load one dashboard the way Grafana does (every panel at once), N times: server memory peak and failed panels.
Usage: page_load.py <queries.json from join_bench.py> <join_algorithm> <dashboard uid> <loads>"""
import json, os, sys, threading, time, urllib.request, urllib.parse, base64, concurrent.futures as cf
AUTH = {'Authorization': 'Basic ' + base64.b64encode(f"{os.environ.get('CH_USER', 'default')}:{os.environ.get('CH_PASSWORD', '')}".encode()).decode()}
URL = os.environ.get('CH_URL', 'http://localhost:8123')
algo, uid, rounds = sys.argv[2], sys.argv[3], int(sys.argv[4])
qs = [q for q in json.load(open(sys.argv[1]))['queries'] if q[0] == uid]
def http(sql, **st):
    req = urllib.request.Request(URL + '/?' + urllib.parse.urlencode(st), data=sql.encode(), headers=AUTH)
    try: urllib.request.urlopen(req, timeout=300).read(); return None
    except urllib.error.HTTPError as e: return e.read().decode()[:90]
    except Exception as e: return 'conn: ' + str(e)[:60]
peak, stop = [0], [False]
def poll():
    while not stop[0]:
        try:
            r = urllib.request.urlopen(urllib.request.Request(URL + '/', data=b"SELECT value FROM system.metrics WHERE metric='MemoryTracking'", headers=AUTH)).read()
            peak[0] = max(peak[0], int(r))
        except Exception: pass
        time.sleep(0.2)
th = threading.Thread(target=poll); th.start()
errs, t0 = [], time.time()
for r in range(rounds):
    with cf.ThreadPoolExecutor(len(qs)) as ex:
        for q, e in zip(qs, ex.map(lambda q: http(q[2], join_algorithm=algo, use_query_cache=0), qs)):
            if e: errs.append((r, q[1][:30], e))
stop[0] = True; th.join()
print(f"{uid} {algo}: {len(qs)} panels x {rounds} loads, {time.time()-t0:.1f}s, server memory peak {peak[0]/1e9:.2f} GB, failed panels {len(errs)}")
for e in errs[:4]: print('   ', e)
