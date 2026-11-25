#!/usr/bin/env python3
"""Tests for build_dashboards.py: its building blocks and the v2 gates (dashboard review, 25 Nov 2026).

    python3 code/grafana/test_build.py                       # checks on the generated JSON, no database
    python3 code/grafana/test_build.py --chdb /tmp/n360      # also run the sample's queries (test_panels.py)
    python3 code/grafana/test_build.py --url ... --user grafana --password ...

1. One palette: every colour in every dashboard comes from COLOUR (or is transparent), and a sample built with COLOUR
   replaced by markers carries only markers, so no colour is written twice.
2. No status rules in dashboards: no threshold steps except a reference line (one step, the reference colour).
3. Every panel has a description in the template's shape (Source, Filter, Window).
4. Plain words: no table names, internal keys or underscores in panel titles.
5. Every page starts with the sentence and the freshness stamp (row 1); the v2 pages put 4 tiles in row 2 and the
   lead chart at row 3, so rows 1 to 3 are on screen without scrolling at 1440 x 900.
6. Layout: no gap and no overlap in any dashboard's grid (no orphan panel in a row).
7. Contract statuses: every status the view can store has a word and a colour; renewal risk is never red.
8. Links in the 04 conventions form.
"""
import json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_dashboards as b  # noqa: E402

V2_PAGES = {'n360-overview', 'n360-contracts', 'n360-customer', 'n360-health', 'n360-adoption', 'n360-requests', 'n360-risk'}


def sample():
    """A dashboard using the building blocks the way the dashboards do."""
    p = b.page_header(1, "SELECT 'sample' AS sentence", b.describe('Sample.', 'none', 'none', 'none'))
    p.append(b.tile(3, 'Tile (sample)', b.status_night_count("status = 'over billed'"), 0, 3,
                    b.describe('Sample tile.', 'n360.v_status_night', 'none', 'the last 30 nights')))
    p.append(b.panel(4, 'Status (sample)', 'table', f"""
        SELECT a.display_name AS Customer, s.account AS account, s.line_label AS Line, s.pct_billed AS `% of billed`,
               {b.STATUS_SHOWN} AS Status
        FROM n360.v_contract_status AS s INNER JOIN n360.account AS a ON a.account = s.account
        WHERE s.{b.ACCT}""", 0, 8, 24, 8,
        b.describe('Sample of the status helpers.', 'n360.v_contract_status', 'Customer', 'the latest load'),
        field=b.field_config([b.pct('% of billed'), b.status_chip('Status'),
                              b.with_links('Customer', b.drill('n360-customer', 'account', 'account', 'Open customer'))])))
    p.append(b.panel(5, 'Statuses (sample)', 'barchart', f"SELECT 'x' AS Book, {b.status_columns()} FROM n360.v_contract_status",
                     0, 16, 24, 6, b.describe('Sample.', 'n360.v_contract_status', 'none', 'the latest load'),
                     field=b.field_config(b.status_overrides(link=True), **b.reference_line(100))))
    p[-1]['n360Reference'] = True
    return b.dashboard('n360-sample', 'N360 · helper sample', p, [b.var_account()], [b.back_link()])


def walk(o, path=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + '/' + k, k, v
            yield from walk(v, path + '/' + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f'{path}[{i}]')


def dashboards():
    return [f() for f in b.DASHBOARDS]


def test_one_palette():
    allowed = set(b.COLOUR.values()) | {'transparent'}
    bad = [f"{d['uid']}{path} = {v!r}" for d in dashboards() for path, k, v in walk(d)
           if k in ('color', 'fixedColor') and isinstance(v, str) and v not in allowed]
    assert not bad, 'colour not in COLOUR:\n  ' + '\n  '.join(bad[:20])
    saved = dict(b.COLOUR)
    try:
        for k in b.COLOUR:
            b.COLOUR[k] = 'MARK-' + k
        d = sample()
    finally:
        b.COLOUR.clear(); b.COLOUR.update(saved)
    bad = [f'{path} = {v!r}' for path, k, v in walk(d) if k in ('color', 'fixedColor') and isinstance(v, str)
           and v != 'transparent' and not v.startswith('MARK-')]
    assert any(k == 'color' for _, k, _ in walk(d)), 'sample uses no colours; test is empty'
    assert not bad, 'colour not taken from COLOUR:\n  ' + '\n  '.join(bad)
    print('OK   one palette: every colour comes from COLOUR')


def test_no_rules():
    """Statuses are decided in views; a dashboard may only draw a reference line (one step, the reference colour)."""
    bad = []
    for d in dashboards() + [sample()]:
        for p in d['panels']:
            for path, k, v in walk(p):
                if k != 'thresholds':
                    continue
                steps = v.get('steps', [])
                ref = (p.get('n360Reference') and len(steps) == 2 and steps[0]['color'] == 'transparent'
                       and steps[1]['color'] == b.COLOUR['reference'])
                if not ref:
                    bad.append(f"{d['uid']} panel {p['id']} {path}")
    assert not bad, 'threshold steps in a dashboard (rules belong in a view):\n  ' + '\n  '.join(bad)
    print('OK   no status rules in dashboards (reference lines only)')


def test_descriptions():
    for d in dashboards() + [sample()]:
        for p in d['panels']:
            if p['type'] == 'text':
                continue
            for part in ('**Source:**', '**Filter:**', '**Window:**'):
                assert part in p['description'], f"{d['uid']} panel {p['id']} description lacks {part}"
    print('OK   every panel description has Source, Filter, Window')


def test_plain_words():
    bad = [f"{d['uid']}: {p['title']!r}" for d in dashboards() for p in d['panels']
           if re.search(r'n360\.|\w_\w|\buuid\b|\bid\b|\bbot\b', p['title'], re.I)]
    assert not bad, 'internal names in panel titles:\n  ' + '\n  '.join(bad)
    print('OK   plain words in panel titles')


def test_page_shape():
    for d in dashboards():
        top = {p['title']: p['gridPos'] for p in d['panels'] if p['gridPos']['y'] == 0}
        assert 'In one sentence' in top and 'Data' in top, f"{d['uid']}: row 1 is not sentence + stamp: {list(top)}"
        if d['uid'] in V2_PAGES:
            tiles = [p for p in d['panels'] if p['gridPos']['y'] == 3]
            assert len(tiles) == 4 and all(p['type'] == 'stat' and p['gridPos']['h'] == 5 for p in tiles), \
                f"{d['uid']}: row 2 is not 4 tiles"
            assert any(p['gridPos']['y'] == 8 for p in d['panels']), f"{d['uid']}: no lead panel at row 3"
            assert all(p['gridPos']['y'] + p['gridPos']['h'] <= 8 for p in d['panels'] if p['gridPos']['y'] < 8), \
                f"{d['uid']}: rows 1-2 spill into row 3"
    print('OK   every page: sentence and stamp first; v2 pages: 4 tiles, lead at row 3 (on screen at 1440 x 900)')


def test_layout():
    for d in dashboards():
        grid = {}
        for p in d['panels']:
            g = p['gridPos']
            assert g['x'] >= 0 and g['x'] + g['w'] <= 24, f"{d['uid']} panel {p['id']} outside the grid"
            for y in range(g['y'], g['y'] + g['h']):
                for x in range(g['x'], g['x'] + g['w']):
                    assert (x, y) not in grid, f"{d['uid']}: panels {grid[(x, y)]} and {p['id']} overlap at {x},{y}"
                    grid[(x, y)] = p['id']
        bottom = max(p['gridPos']['y'] + p['gridPos']['h'] for p in d['panels'])
        gaps = sorted({y for y in range(bottom) for x in range(24) if (x, y) not in grid})
        assert not gaps, f"{d['uid']}: gaps in rows {gaps[:10]} (an orphan panel or a hole)"
    print('OK   no gap and no overlap in any dashboard')


def test_statuses():
    """Every status n360.v_contract_status can store (its multiIf) has a word and a colour; renewal risk is never red."""
    schema = open(os.path.join(os.path.dirname(HERE), 'schema.sql')).read()
    view = schema[schema.index('CREATE OR REPLACE VIEW n360.v_contract_status'):]
    block = view[view.index("multiIf(p.measure = 'not implemented'"):view.index('AS status,')]
    stored = set(re.findall(r"'([a-z ]+)'(?=,\n|\))", block)) - {'not implemented', 'not measurable', 'line', 'active'}
    stored |= {'renewal risk'}
    missing = stored - set(b.STATUS)
    assert len(stored) >= 12 and not missing, f'statuses with no word or colour: {missing} (found {sorted(stored)})'
    assert 'red' not in b.COLOUR and b.STATUS['renewal risk'][1] == 'risk_blue', 'no red in the palette'
    print(f'OK   {len(stored)} stored statuses each have a word and a colour; no red')


def test_links():
    assert b.drill('n360-line', 'line', 'line', 'x')['url'] == '/d/n360-line?var-line=${__data.fields.line}&${__url_time_range}'
    assert b.drill('n360-line', 'line', '% of billed', 'x')['url'].startswith('/d/n360-line?var-line=${__data.fields["% of billed"]}')
    assert b.back_link()['url'] == '/d/n360-overview' and b.back_link()['keepTime']
    print('OK   data links and Back link in the 04 conventions form')


def main():
    test_one_palette()
    test_no_rules()
    test_descriptions()
    test_plain_words()
    test_page_shape()
    test_layout()
    test_statuses()
    test_links()
    if len(sys.argv) > 1:   # database arguments: run the sample's queries
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'n360-sample.json'), 'w') as f:
                json.dump(sample(), f, indent=2)
            r = subprocess.run([sys.executable, os.path.join(HERE, 'test_panels.py'), '--dashboards', tmp, *sys.argv[1:]])
            if r.returncode:
                return r.returncode
    print('BUILD TESTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
