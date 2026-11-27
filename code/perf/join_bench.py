#!/usr/bin/env python3
"""Every dashboard panel query, once per join algorithm (parallel_hash, hash) and thread count (4, 10), on a ClickHouse
server: memory per panel (system.query_log), errors, and whether the rows agree. Usage: join_bench.py <queries-out.json>"""
import glob, json, os, sys, uuid, urllib.request, urllib.parse, base64, hashlib, time, concurrent.futures as cf
sys.path.insert(0, 'code/grafana')
from test_panels import expand
AUTH = {'Authorization': 'Basic ' + base64.b64encode(f"{os.environ.get('CH_USER', 'default')}:{os.environ.get('CH_PASSWORD', '')}".encode()).decode()}
URL = os.environ.get('CH_URL', 'http://localhost:8123')
def http(sql, **settings):
    url = URL + '/?' + urllib.parse.urlencode(settings)
    req = urllib.request.Request(url, data=(sql + '\nFORMAT JSONEachRow').encode(), headers=AUTH)
    try:
        return urllib.request.urlopen(req, timeout=300).read().decode(), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode()[:160]
def rows(sql):
    out, err = http(sql)
    if err: raise RuntimeError(err)
    return [json.loads(l) for l in out.splitlines() if l]
to = rows("SELECT toString(toDate(max(window_end))) AS d FROM n360.runs WHERE status='loaded'")[0]['d']
frm = rows(f"SELECT toString(toDate('{to}') - 90) AS d")[0]['d']
queries = []
for path in sorted(glob.glob('code/grafana/dashboards/*.json')):
    dash = json.load(open(path)); values = {}
    for v in dash.get('templating', {}).get('list', []):
        if v.get('type') == 'custom':
            cur = v.get('current', {}).get('value') or v['options'][0]['value']
            values[v['name']] = [o['value'] for o in v['options']] if cur == '$__all' else [str(cur)]
        elif v.get('type') == 'query':
            values[v['name']] = [str(next(iter(r.values()))) for r in rows(expand(v['query'], values, frm, to))]
    for p in dash['panels']:
        for t in p.get('targets', []):
            queries.append((dash['uid'], p['title'], expand(t['rawSql'], values, frm, to)))
print(len(queries), 'panel queries')
def run(q, algo, threads):
    qid = str(uuid.uuid4())
    out, err = http(q[2], join_algorithm=algo, max_threads=threads, query_id=qid, use_query_cache=0)
    canon = None if err else hashlib.md5('\n'.join(sorted(out.splitlines())).encode()).hexdigest()
    return qid, canon, err
res = {}
for algo in ('parallel_hash', 'hash'):
    for threads in (4, 10):
        for i, q in enumerate(queries):
            res[(algo, threads, i)] = run(q, algo, threads)
http('SYSTEM FLUSH LOGS')
mem = {r['query_id']: int(r['m']) for r in rows("SELECT query_id, max(memory_usage) AS m FROM system.query_log WHERE type IN ('QueryFinish','ExceptionWhileProcessing') AND event_time > now() - INTERVAL 2 HOUR GROUP BY query_id")}
summary = {}
for (algo, threads, i), (qid, canon, err) in res.items():
    s = summary.setdefault((algo, threads), dict(errors=0, total=0, peak=0, over500=0, mems=[]))
    m = mem.get(qid, 0); s['mems'].append(m); s['total'] += m; s['peak'] = max(s['peak'], m); s['over500'] += m > 500e6
    s['errors'] += err is not None
for k, s in summary.items():
    ms = sorted(s['mems'])
    print(k, f"errors {s['errors']}  peak {s['peak']/1e6:.0f} MB  median {ms[len(ms)//2]/1e6:.1f} MB  sum {s['total']/1e9:.2f} GB  >500MB {s['over500']}")
diff = [queries[i][:2] for i in range(len(queries)) if len({res[(a, t, i)][1] for a in ('parallel_hash', 'hash') for t in (4, 10)}) > 1]
print('panels whose rows differ between settings:', len(diff), diff[:10])
errs = [(k, queries[k[2]][:2], v[2]) for k, v in res.items() if v[2]]
print('errors:', errs[:5])
# the heaviest panels
top = sorted(range(len(queries)), key=lambda i: -mem.get(res[('parallel_hash', 10, i)][0], 0))[:8]
for i in top:
    print(f"  {queries[i][0]:24s} {queries[i][1][:34]:34s} parallel_hash/10 {mem.get(res[('parallel_hash',10,i)][0],0)/1e6:7.0f} MB   hash/10 {mem.get(res[('hash',10,i)][0],0)/1e6:6.0f} MB")
json.dump({'queries': queries}, open(sys.argv[1], 'w'))
