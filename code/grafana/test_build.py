#!/usr/bin/env python3
"""Tests for build_dashboards.py's building blocks (backlog B4).

    python3 code/grafana/test_build.py                       # settings checks only, no database
    python3 code/grafana/test_build.py --chdb /tmp/n360      # also run the helpers' queries (test_panels.py)
    python3 code/grafana/test_build.py --url ... --user grafana --password ...

1. One place per setting: build a sample dashboard that uses every helper with COLOUR replaced by
   marker values; every colour in the output must be a marker, so no colour is written twice.
   No dashboard sets thresholds: pace colours come from n360.v_line_pace's label (B5).
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
               l.pace_gap AS `Pace gap`, l.pace AS Pace
        FROM n360.v_line_pace AS l INNER JOIN n360.account AS a ON a.account = l.account
        WHERE l.{b.ACCT}""", 0, 3, 24, 8,
        b.describe('Sample of the pace helpers.', 'n360.v_line_pace', 'Customer', 'start date to data-through day'),
        field=b.field_config([
            b.pct('% of billed'),
            b.override('Pace gap', ('decimals', 1)),
            b.pace_chip('Pace'),
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
    saved = dict(b.COLOUR)
    try:
        for k in b.COLOUR:
            b.COLOUR[k] = 'MARK-' + k
        d = sample()
    finally:
        b.COLOUR.clear(); b.COLOUR.update(saved)
    bad = [f'{path} = {v!r}' for path, k, v in walk(d) if k == 'color' and not str(v).startswith('MARK-')]
    assert any(k == 'color' for _, k, _ in walk(d)), 'sample uses no colours; test is empty'
    assert not bad, 'colour not taken from COLOUR:\n  ' + '\n  '.join(bad)
    print('OK   every colour comes from COLOUR')


def test_no_thresholds():
    """Pace thresholds live in n360.v_line_pace only (decided 24 Nov 2026): no dashboard sets threshold steps."""
    bad = [(d['uid'], path) for d in [f() for f in b.DASHBOARDS] + [sample()]
           for path, k, v in walk(d) if k == 'thresholds']
    assert not bad, 'threshold steps in a dashboard (the rule belongs in a view):\n  ' + '\n  '.join(map(str, bad))
    print('OK   no thresholds in dashboards: colours come from view labels')


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
    test_no_thresholds()
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
