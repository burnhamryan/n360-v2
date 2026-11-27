#!/usr/bin/env python3
"""Thumbnails for the Welcome page, from docs/screenshots (synthetic data only): the top of each page, 560 px wide.

    python3 code/grafana/make_thumbnails.py      # writes code/grafana/welcome/*.jpg (needs Pillow)

Run it after re-taking the screenshots. Grafana serves the folder as /public/img/n360/ (docker-compose.yml).
"""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'docs', 'screenshots')
OUT = os.path.join(HERE, 'welcome')
# thumbnail name: screenshot it is cut from
SHOTS = {'overview': '01-overview', 'contracts': '02-contracts', 'health': '03-customer-health', 'customer': '04-customer-harbor',
         'line': '06-line-harbor-chat', 'va': '13-va-lakeside', 'requests': '08-requests', 'forecast': '09-forecast',
         'anomalies': '10-anomalies', 'containment': '11-containment', 'risk': '16-risk', 'risk_model': '18-risk-model',
         'adoption': '07-adoption', 'adoption_customer': '19-adoption-customer', 'adoption_va': '20-adoption-va',
         'cortex': '21-cortex-by-netomians'}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, shot in SHOTS.items():
        im = Image.open(os.path.join(SRC, shot + '.png')).convert('RGB')
        top = im.crop((0, 0, im.width, min(im.height, int(im.width * 0.62))))      # the page's first screen
        top.resize((560, int(560 * top.height / top.width)), Image.LANCZOS).save(os.path.join(OUT, name + '.jpg'), quality=84, optimize=True)
    print(f'wrote {len(SHOTS)} thumbnails to {os.path.relpath(OUT)}')


if __name__ == '__main__':
    main()
