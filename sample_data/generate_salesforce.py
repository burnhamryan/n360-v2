#!/usr/bin/env python3
"""Generate SYNTHETIC Salesforce records (as the REST API returns them) for the synthetic customers of
generate_synthetic.py, for code/salesforce/fake_salesforce.py to serve and sf_pull.py to read in tests.

    python3 sample_data/generate_salesforce.py --out /tmp/sf_records.json --last-day 2026-11-19

Every account, opportunity, activity and amount is invented. Planted cases (risk dashboard, 25 Nov 2026):
  Lakeside Insurance  renewal closed lost; last contact 95 days ago
  Willow Energy       open renewal in 42 days, forecast category Omitted; last contact 75 days ago
  Juniper Bank        Salesforce name differs ("Juniper Bank N.A."): linked only through account_overrides
  Northwind, Juniper  renewal within 90 days, not in Commit
  Maple Clinics       term ends within 90 days and no renewal opportunity
  Kestrel Travel      last contact 41 days ago
  Summit Games        no executive meeting in the last year
  Cedar Logistics     no activity at all; Globex Prospects: a Salesforce account with no N360 account
Past opportunities (risk model outcomes, 27 Nov 2026; a downsell is judged against the previous won RENEWAL):
  Kestrel Travel      renewed at 180,000, a small add-on won at 20,000, then renewed at 120,000: a downsell
                      (against the add-on it would look like growth)
  Orchid Health       a three-year first deal at 315,000, then a first renewal at 105,000: renewed, not a downsell
  Atlas Streaming     renewed at 900,000 the year before too: renewed
"""
import argparse, datetime as dt, json, random, uuid

R = random.Random(20261125)


def sid(prefix, *key):
    return prefix + uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic-sf/' + '/'.join(key)).hex[:15].upper()


# name in Salesforce, N360 key, owner, renewal (close date, stage, forecast category, closed, won, amount) or None,
# days since last contact (None = no activity), days since last executive meeting (None = none in a year)
WORLD = [
    ('Northwind Outfitters', 'Owner One', ('2027-01-31', 'Proposal', 'Pipeline', False, False, 410000), 6, 40),
    ('Bluebird Air', 'Owner Two', ('2027-01-28', 'Negotiation', 'Commit', False, False, 2900000), 4, 25),
    ('Pinecrest Retail', 'Owner Three', ('2027-03-31', 'Discovery', 'Pipeline', False, False, 520000), 9, 60),
    ('Atlas Streaming', 'Owner Four', ('2026-10-28', 'Closed Won', 'Closed', True, True, 900000), 12, 30),
    ('Harbor Telecom', 'Owner Five', ('2026-12-31', 'Negotiation', 'Commit', False, False, 120000), 3, 20),
    ('Juniper Bank N.A.', 'Owner Six', ('2026-12-31', 'Proposal', 'Best Case', False, False, 160000), 14, 70),
    ('Summit Games', 'Owner Seven', ('2027-08-31', 'Discovery', 'Pipeline', False, False, 950000), 18, None),
    ('Lakeside Insurance', 'Owner Eight', ('2026-10-31', 'Closed Lost', 'Omitted', True, False, 60000), 95, 200),
    ('Kestrel Travel', 'Owner Nine', ('2027-02-28', 'Discovery', 'Pipeline', False, False, 130000), 41, 80),
    ('Orchid Health', 'Owner Ten', ('2027-02-28', 'Proposal', 'Best Case', False, False, 105000), 8, 45),
    ('Maple Clinics', 'Owner Eleven', None, 10, 50),
    ('Willow Energy', 'Owner Twelve', ('2026-12-31', 'Negotiation', 'Omitted', False, False, 190000), 75, 150),
    ('Cedar Logistics', 'Owner Nine', None, None, None),
    ('Globex Prospects', 'Owner One', None, 20, None),
]


# closed, won opportunities of earlier years: (close date, type, amount)
PAST = {
    'Kestrel Travel': [('2025-02-28', 'Renewal', 180000), ('2025-08-15', 'Expansion', 20000), ('2026-02-28', 'Renewal', 120000)],
    'Orchid Health': [('2024-02-28', 'New Business', 315000), ('2026-02-27', 'Renewal', 105000)],
    'Atlas Streaming': [('2025-10-28', 'Renewal', 900000)],
}


def attrs(sobject, rid):
    return {'type': sobject, 'url': f'/services/data/v62.0/sobjects/{sobject}/{rid}'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--last-day', default='2026-11-19')
    a = ap.parse_args()
    last = dt.date.fromisoformat(a.last_day)
    rec = {'Account': [], 'Opportunity': [], 'Contract': [], 'Task': [], 'Event': []}
    for name, owner, renewal, contact, exec_ in WORLD:
        acc = sid('001', name)
        last_act = None if contact is None else (last - dt.timedelta(contact)).isoformat()
        rec['Account'].append({'attributes': attrs('Account', acc), 'Id': acc, 'Name': name, 'Owner': {'Name': owner},
                               'Type': 'Prospect' if name.startswith('Globex') else 'Customer',
                               'LastActivityDate': last_act})
        if renewal:
            close, stage, cat, closed, won, amount = renewal
            oid = sid('006', name, 'renewal')
            rec['Opportunity'].append({'attributes': attrs('Opportunity', oid), 'Id': oid, 'AccountId': acc,
                                       'Name': f'[SYNTHETIC] {name} renewal', 'Type': 'Renewal', 'StageName': stage,
                                       'ForecastCategoryName': cat, 'CloseDate': close, 'Amount': amount,
                                       'IsClosed': closed, 'IsWon': won, 'NextStep': None if closed else '[SYNTHETIC] next step',
                                       'Owner': {'Name': owner}, 'LastActivityDate': last_act})
            cid = sid('800', name)
            start = (dt.date.fromisoformat(close) - dt.timedelta(365)).isoformat()
            rec['Contract'].append({'attributes': attrs('Contract', cid), 'Id': cid, 'AccountId': acc,
                                    'ContractNumber': f'{R.randint(10000, 99999):08d}', 'Status': 'Activated',
                                    'StartDate': start, 'EndDate': close, 'ContractTerm': 12})
        for i, (close, kind, amount) in enumerate(PAST.get(name, [])):
            oid = sid('006', name, f'past{i}')
            rec['Opportunity'].append({'attributes': attrs('Opportunity', oid), 'Id': oid, 'AccountId': acc,
                                       'Name': f'[SYNTHETIC] {name} {kind.lower()} {close[:4]}', 'Type': kind,
                                       'StageName': 'Closed Won', 'ForecastCategoryName': 'Closed', 'CloseDate': close,
                                       'Amount': amount, 'IsClosed': True, 'IsWon': True, 'NextStep': None,
                                       'Owner': {'Name': owner}, 'LastActivityDate': None})
        if name == 'Pinecrest Retail':      # an expansion: not a renewal, never a risk
            oid = sid('006', name, 'expansion')
            rec['Opportunity'].append({'attributes': attrs('Opportunity', oid), 'Id': oid, 'AccountId': acc,
                                       'Name': '[SYNTHETIC] Pinecrest Retail volume add-on', 'Type': 'Expansion',
                                       'StageName': 'Proposal', 'ForecastCategoryName': 'Best Case',
                                       'CloseDate': (last + dt.timedelta(30)).isoformat(), 'Amount': 180000,
                                       'IsClosed': False, 'IsWon': False, 'NextStep': None, 'Owner': {'Name': owner},
                                       'LastActivityDate': last_act})
        if contact is not None:            # a call every ~2 weeks back from the last contact, over a year
            d, i = last - dt.timedelta(contact), 0
            while d > last - dt.timedelta(365):
                tid = sid('00T', name, str(i))
                rec['Task'].append({'attributes': attrs('Task', tid), 'Id': tid, 'AccountId': acc,
                                    'ActivityDate': d.isoformat(), 'Type': 'Call', 'TaskSubtype': 'Call' if i % 3 else 'Email',
                                    'Subject': '[SYNTHETIC] check-in', 'Status': 'Completed', 'Owner': {'Name': owner},
                                    'IsClosed': True})
                d -= dt.timedelta(14 + R.randint(-3, 3)); i += 1
            tid = sid('00T', name, 'open')   # an open, future task: not contact
            rec['Task'].append({'attributes': attrs('Task', tid), 'Id': tid, 'AccountId': acc,
                                'ActivityDate': (last + dt.timedelta(10)).isoformat(), 'Type': 'Call', 'TaskSubtype': 'Task',
                                'Subject': '[SYNTHETIC] follow up', 'Status': 'Not Started', 'Owner': {'Name': owner},
                                'IsClosed': False})
        if exec_ is not None:
            eid = sid('00U', name, 'qbr')
            rec['Event'].append({'attributes': attrs('Event', eid), 'Id': eid, 'AccountId': acc,
                                 'ActivityDate': (last - dt.timedelta(exec_)).isoformat(), 'Type': 'Meeting',
                                 'EventSubtype': 'Event', 'Subject': '[SYNTHETIC] QBR', 'Owner': {'Name': owner}})
    with open(a.out, 'w') as f:
        json.dump(rec, f, indent=1)
    print('synthetic Salesforce records:', ' '.join(f'{k}={len(v)}' for k, v in rec.items()))


if __name__ == '__main__':
    main()
