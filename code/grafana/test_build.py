#!/usr/bin/env python3
"""Tests for build_dashboards.py's building blocks (backlog B4).

    python3 code/grafana/test_build.py                       # settings checks only, no database
    python3 code/grafana/test_build.py --chdb /tmp/n360      # also run the helpers' queries (test_panels.py)
    python3 code/grafana/test_build.py --url ... --user grafana --password ...

1. One place per setting: build a sample dashboard that uses every helper with PACE and COLOUR replaced
   by marker values; every colour and threshold in the output must be a marker, so nothing is
   written twice.
2. Every panel has a description in the template's shape (Source, Filter, Window).
3. With a database: write the sample dashboard (header, links, colours) to a scratch folder and run
   every query in it with test_panels.py.
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_dashboards as b  # noqa: E402


def sample():
    """A dashboard using every building block, the way B6-B10 will."""
    p = b.header(1, 0)
    p.append(b.panel(3, 'Pace (sample)', 'table', f"""
        SELECT a.display_name AS Customer, l.account AS account, l.line_key AS line, l.pct_billed AS `% of billed`,
               round(l.pct_billed - l.pct_term_elapsed, 1) AS `Pace gap`
        FROM n360.v_line_status AS l INNER JOIN n360.account AS a ON a.account = l.account
        WHERE l.pool_id IS NULL AND l.{b.ACCT}""", 0, 3, 24, 8,
        b.describe('Sample of the pace helpers.', 'n360.v_line_status', 'Customer', 'start date to data-through day'),
        field=b.field_config([
            b.pct('% of billed'),
            b.coloured('% of billed', b.pct_billed_colours()),
            b.coloured('Pace gap', b.pace_gap_colours()),
            b.override('Pace gap', ('decimals', 1)),
            b.with_links('Customer', b.drill('n360-customer', 'account', 'account', 'Open customer')),
            b.with_links('% of billed', b.drill('n360-line', 'line', '% of billed', 'Open line')),
        ])))
    return b.dashboard('n360-b4-sample', 'N360 · B4 helper sample', p, [b.var_account()], [b.back_link()])


def walk(o, path=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + '/' + k, k, v
            yield from walk(v, path + '/' + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f'{path}[{i}]')


def test_one_place():
    saved = dict(b.PACE), dict(b.COLOUR)
    try:
        for k in b.PACE:
            b.PACE[k] = 9000 + len(k)
        for k in b.COLOUR:
            b.COLOUR[k] = 'MARK-' + k
        d = sample()
    finally:
        b.PACE.clear(); b.PACE.update(saved[0]); b.COLOUR.clear(); b.COLOUR.update(saved[1])
    bad = []
    for path, k, v in walk(d):
        if k == 'color' and not str(v).startswith('MARK-'):
            bad.append(f'{path} = {v!r}')
        if k == 'value' and '/steps' in path and v is not None and not (isinstance(v, int) and v >= 9000):
            bad.append(f'{path} = {v!r}')
    used = {k for _, k, _ in walk(d)}
    assert 'color' in used and 'steps' in used, 'sample uses no colours or thresholds; test is empty'
    assert not bad, 'colour or threshold not taken from PACE/COLOUR:\n  ' + '\n  '.join(bad)
    print('OK   every colour and threshold comes from PACE / COLOUR')


def test_descriptions():
    for d in [f() for f in b.DASHBOARDS] + [sample()]:
        for p in d['panels']:
            for part in ('**Source:**', '**Filter:**', '**Window:**'):
                assert part in p['description'], f"{d['uid']} panel {p['id']} description lacks {part}"
    print('OK   every panel description has Source, Filter, Window')


def test_links():
    assert b.drill('n360-line', 'line', 'line', 'x')['url'] == '/d/n360-line?var-line=${__data.fields.line}&${__url_time_range}'
    assert b.drill('n360-line', 'line', '% of billed', 'x')['url'].startswith('/d/n360-line?var-line=${__data.fields["% of billed"]}')
    assert b.back_link()['url'] == '/d/n360-overview' and b.back_link()['keepTime']
    print('OK   data links and Back link in the 04 conventions form')


def main():
    test_one_place()
    test_descriptions()
    test_links()
    if len(sys.argv) > 1:   # database arguments: run the sample's queries
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'n360-b4-sample.json'), 'w') as f:
                json.dump(sample(), f, indent=2)
            r = subprocess.run([sys.executable, os.path.join(HERE, 'test_panels.py'), '--dashboards', tmp, *sys.argv[1:]])
            if r.returncode:
                return r.returncode
    print('B4 BUILD TESTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
