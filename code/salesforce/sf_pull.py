#!/usr/bin/env python3
"""Read Salesforce (accounts, opportunities, contracts, tasks, events) into NDJSON files for load_salesforce.py.
Read-only: SOQL queries over the REST API, no writes to Salesforce. Python standard library only.

    python3 code/salesforce/sf_pull.py --out ~/n360-work/salesforce          # then load_salesforce.py --rows that folder

Credentials, from the environment (or code/.env), never from a file in Git:
    SF_INSTANCE_URL + SF_ACCESS_TOKEN          a session you already have, e.g. from `sf org display --json`
  or
    SF_LOGIN_URL + SF_CLIENT_ID + SF_CLIENT_SECRET   an External Client App / Connected App with the OAuth 2.0
                                                     client credentials flow (runs as its assigned integration user)
What is read, and how fields are named, is in salesforce.json next to this file (standard objects by default).

Output: one NDJSON file per N360 table (sf_account, sf_opportunity, sf_contract, sf_activity) and sf_run.json.
Exit codes: 0 pulled, 2 error (nothing written).
"""
import argparse, datetime as dt, json, os, sys, urllib.error, urllib.parse, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 60


class SalesforceError(Exception):
    pass


def http(method, url, token=None, data=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors='replace')[:500]
        raise SalesforceError(f'{method} {urllib.parse.urlsplit(url).path} -> HTTP {e.code}: {detail}') from None


def session(env):
    """(instance_url, access_token) from an existing token, or the client credentials flow."""
    if env.get('SF_ACCESS_TOKEN') and env.get('SF_INSTANCE_URL'):
        return env['SF_INSTANCE_URL'].rstrip('/'), env['SF_ACCESS_TOKEN']
    need = ['SF_LOGIN_URL', 'SF_CLIENT_ID', 'SF_CLIENT_SECRET']
    missing = [k for k in need if not env.get(k)]
    if missing:
        raise SalesforceError('set SF_INSTANCE_URL + SF_ACCESS_TOKEN, or ' + ' + '.join(need) + f' (missing: {", ".join(missing)})')
    tok = http('POST', env['SF_LOGIN_URL'].rstrip('/') + '/services/oauth2/token',
               data={'grant_type': 'client_credentials', 'client_id': env['SF_CLIENT_ID'],
                     'client_secret': env['SF_CLIENT_SECRET']})
    return tok['instance_url'].rstrip('/'), tok['access_token']


def soql(obj):
    q = f"SELECT {', '.join(obj['fields'].values())} FROM {obj['sobject']}"
    return q + (f" WHERE {obj['where']}" if obj.get('where') else '')


def query(instance, token, version, q):
    """Every record of a SOQL query, following nextRecordsUrl."""
    url = f'{instance}/services/data/{version}/query?q=' + urllib.parse.quote(q)
    while url:
        page = http('GET', url, token)
        yield from page.get('records', [])
        nxt = page.get('nextRecordsUrl')
        url = instance + nxt if nxt and not page.get('done', True) else None


def field(rec, path):
    """A record's value at a dotted path (Owner.Name); None when a relationship is empty."""
    for part in path.split('.'):
        if not isinstance(rec, dict):
            return None
        rec = rec.get(part)
    return rec


def row(rec, fields):
    return {col: field(rec, path) for col, path in fields.items()}


def pull(cfg, instance, token, today):
    v = cfg['api_version']
    o = cfg['objects']
    out = {}
    out['sf_account'] = [row(r, o['sf_account']['fields']) for r in query(instance, token, v, soql(o['sf_account']))]
    renewal = {t.lower() for t in cfg.get('renewal_opportunity_types', [])}
    out['sf_opportunity'] = []
    for r in query(instance, token, v, soql(o['sf_opportunity'])):
        x = row(r, o['sf_opportunity']['fields'])
        x['is_renewal'] = (x.get('type') or '').lower() in renewal
        out['sf_opportunity'].append(x)
    out['sf_contract'] = [row(r, o['sf_contract']['fields']) for r in query(instance, token, v, soql(o['sf_contract']))]
    contact_subtypes = {t.lower() for t in cfg.get('contact', {}).get('task_subtypes', [])}
    execs = [k.lower() for k in cfg.get('executive_keywords', [])]
    acts = []
    for kind in ('task', 'event'):
        for r in query(instance, token, v, soql(o[kind])):
            x = row(r, o[kind]['fields'])
            x['kind'] = kind
            x.setdefault('status', None)
            x['is_closed'] = bool(x.get('is_closed')) if kind == 'task' else (x.get('activity_date') or '9999') <= today
            x['is_contact'] = (x['is_closed'] and (kind == 'event' or (x.get('subtype') or '').lower() in contact_subtypes))
            x['is_executive'] = x['is_contact'] and any(k in (x.get('subject') or '').lower() for k in execs)
            acts.append(x)
    out['sf_activity'] = acts
    overrides = {k: v for k, v in cfg.get('account_overrides', {}).items() if not k.startswith('_')}
    for a in out['sf_account']:
        a['account_hint'] = overrides.get(a.get('name') or '', '')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='folder for the NDJSON files (created; old files replaced)')
    ap.add_argument('--config', default=os.path.join(HERE, 'salesforce.json'))
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    try:
        instance, token = session(os.environ)
        today = dt.date.today().isoformat()
        tables = pull(cfg, instance, token, today)
    except (SalesforceError, urllib.error.URLError, KeyError) as e:
        print('ERROR:', e)
        return 2
    os.makedirs(a.out, exist_ok=True)
    pulled_at = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    run_id = str(uuid.uuid4())
    for t, rows in tables.items():
        with open(os.path.join(a.out, f'{t}.ndjson'), 'w') as f:
            for r in rows:
                f.write(json.dumps({**r, 'run_id': run_id}, separators=(',', ':'), default=str) + '\n')
    run = {'run_id': run_id, 'pulled_at': pulled_at, 'instance': urllib.parse.urlsplit(instance).hostname,
           'api_version': cfg['api_version'], 'row_counts': {t: len(r) for t, r in tables.items()}}
    with open(os.path.join(a.out, 'sf_run.json'), 'w') as f:
        json.dump(run, f, indent=1)
    print('PULLED', ' '.join(f'{t}={n}' for t, n in run['row_counts'].items()), 'into', a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
