#!/usr/bin/env python3
"""review_sheet.py - images for reading a scanned song by eye: each line of music zoomed in, with homr's bar numbers
drawn over the bars, so chords (and wrong bars) can be written down by bar number into
leads-overrides/<book>.json, which scan_book.py applies when it assembles the songs.

  python3 tools/review_sheet.py /home/user/fakebook-work 40 --out /tmp/review     # the song(s) starting on page 40

Overrides file:
  { "<song id>": { "chords": { "<bar>": "Am7 . Am6 .", ... },        bar numbers from 1 (the pickup is bar 0);
                                                                       one token per beat, "." = no new chord
                   "notes":  { "<bar>": "E4/4 E4/4 E4/8t F4/8t C4/8t", ... } } }   pitch/value, t = triplet, . = dotted
"""
import argparse, json, os, sys
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mxl_lead as M
import scan_layout as L


def song_lines(work, page, nth=0):
    """[(layout, staff, [bar numbers], edges)] for the song(s) whose title is on `page`, following its pages."""
    lays = {int(f[7:10]): json.load(open(os.path.join(work, f))) for f in os.listdir(work) if f.startswith('layout-')}
    out, bar, started = [], 0, False
    for p in sorted(lays):
        if p < page:
            continue
        lay = lays[p]
        for pc in lay.get('pieces', []):
            if pc['title'] and started:
                return out
            if pc['title'] and p == page:
                if nth > 0:
                    nth -= 1
                    continue
                started = True
            if not started:
                continue
            import scan_book as B
            systems = B.page_systems(work, p) or []
            systems = [systems[k] for k in pc['staves'] if k < len(systems)]
            for k, sysm in zip(pc['staves'], systems):
                st = lay['staves'][k]
                n = len(sysm)
                start = st['x0'] + st['sp'] * 5
                edges = [b for b in st['bars'] if b > start]
                if not edges or edges[-1] < st['x1'] - st['sp'] * 2:
                    edges.append(st['x1'])
                if len(edges) != n:
                    w = (st['x1'] - start) / n
                    edges = [start + w * (i + 1) for i in range(n)]
                out.append((lay, st, list(range(bar + 1, bar + n + 1)), [start] + edges))
                bar += n
        if started and p > page + 3:
            break
    return out


def render(pdf, lines, outdir):
    os.makedirs(outdir, exist_ok=True)
    pages = {}
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 26)
    except OSError:
        font = ImageFont.load_default()
    files = []
    for i, (lay, st, bars, edges) in enumerate(lines):
        if lay['page'] not in pages:
            pages[lay['page']] = L.render(pdf, lay['page']).convert('RGB')
        img = pages[lay['page']]
        sp = st['sp']
        y0, y1 = int(st['top'] - sp * 6), int(st['bot'] + sp * 3.5)
        crop = img.crop((0, max(0, y0), img.width, y1)).copy()
        d = ImageDraw.Draw(crop)
        for k, x in enumerate(edges):
            d.line([(x, 0), (x, crop.height)], fill=(220, 40, 40), width=2)
            if k < len(bars):
                d.text((x + 6, 2), str(bars[k]), fill=(220, 40, 40), font=font)
        crop = crop.resize((int(crop.width * 0.8), int(crop.height * 0.8)))
        f = os.path.join(outdir, f'line-{i + 1:02d}.png')
        crop.save(f)
        files.append((f, bars[0], bars[-1]))
    return files


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('work'); ap.add_argument('page', type=int)
    ap.add_argument('--pdf', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'books/jazzfake.pdf'))
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    for f, b0, b1 in render(a.pdf, song_lines(a.work, a.page), a.out):
        print(f, f'bars {b0}-{b1}')
