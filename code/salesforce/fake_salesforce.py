#!/usr/bin/env python3
"""A fake Salesforce for tests: the OAuth client credentials token endpoint and the SOQL query endpoint, serving
records from a JSON file (sample_data/generate_salesforce.py). Pages of 3 records, so paging is exercised.
WHERE clauses are ignored (every record of the object is returned); field lists are not checked.

    python3 code/salesforce/fake_salesforce.py --records /tmp/sf_records.json --port 18443
"""
import argparse, json, re, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = 3
CLIENT = ('fake-client-id', 'fake-client-secret')
TOKEN = 'fake-access-token'


def make_handler(records, base):
    pages = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != '/services/oauth2/token':
                return self.send(404, {'error': 'not found'})
            form = urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
            if (form.get('grant_type') != ['client_credentials']
                    or (form.get('client_id', [''])[0], form.get('client_secret', [''])[0]) != CLIENT):
                return self.send(400, {'error': 'invalid_client'})
            self.send(200, {'access_token': TOKEN, 'instance_url': base, 'token_type': 'Bearer'})

        def do_GET(self):
            if self.headers.get('Authorization') != f'Bearer {TOKEN}':
                return self.send(401, [{'errorCode': 'INVALID_SESSION_ID', 'message': 'Session expired or invalid'}])
            u = urllib.parse.urlsplit(self.path)
            m = re.match(r'/services/data/v[\d.]+/query/?(\w+-\d+)?$', u.path)
            if not m:
                return self.send(404, [{'errorCode': 'NOT_FOUND'}])
            if m.group(1):                                  # next page
                key = m.group(1)
                sobject, start = key.rsplit('-', 1)
                start = int(start)
            else:
                q = urllib.parse.parse_qs(u.query)['q'][0]
                sobject = re.search(r'\bFROM\s+(\w+)', q, re.I).group(1)
                start = 0
            rows = records.get(sobject, [])
            chunk = rows[start:start + PAGE]
            done = start + PAGE >= len(rows)
            body = {'totalSize': len(rows), 'done': done, 'records': chunk}
            if not done:
                body['nextRecordsUrl'] = f'/services/data/v62.0/query/{sobject}-{start + PAGE}'
            self.send(200, body)
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--records', required=True)
    ap.add_argument('--port', type=int, default=18443)
    a = ap.parse_args()
    records = json.load(open(a.records))
    base = f'http://127.0.0.1:{a.port}'
    HTTPServer(('127.0.0.1', a.port), make_handler(records, base)).serve_forever()


if __name__ == '__main__':
    main()
