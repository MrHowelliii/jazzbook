#!/usr/bin/env python3
"""scan_book.py - a scanned fake book -> play-along lead sheets (leads/<book>.json).

  phase 1  layout   per page: staves, barlines, titles, chord symbols (tools/scan_layout.py); each song's region of
                    the page is cut out as an image
  phase 2  notes    homr (pip install homr) reads every region -> MusicXML
  phase 3  songs    regions joined into songs; chords placed in homr's bars by x (via the detected barlines);
                    each bar checked against the time signature; bar boxes kept for highlighting on the page

Each phase skips work already done, so it can be stopped and restarted.

  python3 tools/scan_book.py books/jazzfake.pdf --book Jazz-Fakebook --work /tmp/fakebook --first 1 --last 448
"""
import argparse, difflib, json, os, re, subprocess, sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_layout as L
import mxl_lead as M

PT = 72 / L.DPI                                         # page pixels -> PDF points


def layout_page(args):
    pdf, page, work = args
    out = os.path.join(work, f'layout-{page:03d}.json')
    if os.path.exists(out):
        return out
    try:
        return _layout_page(pdf, page, work, out)
    except Exception as e:                               # one odd page must not stop the book
        json.dump({'page': page, 'error': repr(e), 'staves': [], 'titles': [], 'pieces': []}, open(out, 'w'))
        return out


def _layout_page(pdf, page, work, out):
    try:
        img, sts, titles = L.analyse(pdf, page)
    except Exception as e:                               # an empty or odd page
        json.dump({'page': page, 'error': str(e), 'staves': [], 'titles': []}, open(out, 'w')); return out
    prev = None
    for st in sts:
        st['chords'] = L.chords_above(img, st, prev)
        prev = st['bot']
    # song pieces: staves under each title; staves above the first title continue the previous page's song
    titles = sorted(titles, key=lambda t: t['y0'])
    pieces = []
    bounds = [(-1, None)] + [(t['y0'], t) for t in titles]
    for i, (y, t) in enumerate(bounds):
        y_end = bounds[i + 1][0] if i + 1 < len(bounds) else img.height
        group = [k for k, st in enumerate(sts) if (y if y >= 0 else 0) < st['top'] < y_end]
        if not group:
            continue
        top = max(0, int(sts[group[0]]['top'] - sts[group[0]]['sp'] * 7))
        bot = min(img.height, int(sts[group[-1]]['bot'] + sts[group[-1]]['sp'] * 6))
        if bot - top < 50:
            continue
        name = f'piece-{page:03d}-{len(pieces)}.png'
        img.crop((0, top, img.width, bot)).save(os.path.join(work, name))
        pieces.append({'title': t['text'] if t else None, 'staves': group, 'image': name, 'y': top})
    json.dump({'page': page, 'width': img.width, 'height': img.height, 'staves': sts, 'titles': titles, 'pieces': pieces},
              open(out, 'w'))
    return out


def homr_piece(path):
    xml = path[:-4] + '.musicxml'
    if not os.path.exists(xml) and not os.path.exists(path + '.failed'):
        r = subprocess.run(['homr', path], capture_output=True, text=True, timeout=900)
        if not os.path.exists(xml):
            open(path + '.failed', 'w').write(r.stdout[-2000:] + r.stderr[-2000:])
    return xml


def norm(t):
    return re.sub(r'[^a-z0-9]+', ' ', str(t).lower().replace('’', "'")).strip()


def match_title(ocr, index):
    """The index entry whose title is closest to the OCR'd title."""
    if not ocr:
        return None
    n = norm(ocr)
    best = max(index, key=lambda s: difflib.SequenceMatcher(None, n, norm(s['title'])).ratio())
    r = difflib.SequenceMatcher(None, n, norm(best['title'])).ratio()
    return best if r > 0.72 else None


def chord_tok(sym):
    """'Bbm7b5' -> the iReal token the band understands."""
    m = re.match(r'^([A-G][b#]?)(.*?)(?:/[A-G][b#]?)?$', sym)
    if not m:
        return None
    root, q = m.group(1), m.group(2)
    if q.startswith('maj') or q.startswith('M'):
        return root + '^7'
    if q.startswith('m') and ('7b5' in q or '7-5' in q):
        return root + 'h7'
    if q.startswith('m(maj') or q.startswith('mmaj'):
        return root + '-^7'
    if q.startswith('m'):
        return root + ('-6' if q in ('m', 'm6', 'm6/9') else '-7')
    if q.startswith('dim') or q.startswith('o'):
        return root + 'o7'
    if q in ('', '6', '69', '6/9', 'add9', '6add9'):
        return root + '6'
    if 'b9' in q or 'alt' in q:
        return root + '7b9'
    return root + '7'


def assemble(work, book, index, offset):
    pages = sorted(f for f in os.listdir(work) if f.startswith('layout-'))
    songs, cur = [], None
    for f in pages:
        lay = json.load(open(os.path.join(work, f)))
        for pc in lay.get('pieces', []):
            if pc['title']:
                hit = match_title(pc['title'], index)
                cur = {'title': hit['title'] if hit else pc['title'].title(), 'composer': hit.get('composer') if hit else None,
                       'page': lay['page'], 'pages': [], 'parts': []}
                songs.append(cur)
            if cur is None:
                continue
            if lay['page'] not in cur['pages']:
                cur['pages'].append(lay['page'])
            cur['parts'].append((lay, pc))
    out = []
    for s in songs:
        meas, problems = [], []
        for lay, pc in s['parts']:
            xml = os.path.join(work, pc['image'][:-4] + '.musicxml')
            if not os.path.exists(xml):
                problems.append(f"page {lay['page']}: no notes read"); continue
            ms = M.measures(M.load(xml))
            # homr's systems -> the staves found on the page
            systems, curs = [], []
            for m in ms:
                if m['newsys'] and curs:
                    systems.append(curs); curs = []
                curs.append(m)
            if curs:
                systems.append(curs)
            staves = [lay['staves'][k] for k in pc['staves']]
            if len(systems) != len(staves):
                problems.append(f"page {lay['page']}: {len(systems)} lines of music read, {len(staves)} staves found")
            for sysm, st in zip(systems, staves):
                place_chords(sysm, st, lay['page'], problems)
                meas += sysm
            for sysm in systems[len(staves):]:
                for m in sysm:
                    m['box'] = None
                meas += sysm
        if not meas:
            continue
        song = M.to_song(meas, s['title'], s['composer'], book, s['page'], s['pages'])
        for b, m in zip(song['bars'], meas):
            b['box'] = m.get('box')
        song['problems'] = problems + song['problems']
        song['scanned'] = True
        out.append(song)
    return out


def place_chords(sysm, st, page, problems):
    """Chords of one staff into its bars. Bar edges: the barlines found on the page when their count matches homr's
    bars, else the staff divided evenly."""
    sp = st['sp']
    start = st['x0'] + sp * 5                            # after the clef (and key / time signature)
    edges = [b for b in st['bars'] if b > start]
    if not edges or edges[-1] < st['x1'] - sp * 2:
        edges.append(st['x1'])
    n = len(sysm)
    if len(edges) != n:
        w = (st['x1'] - start) / n
        edges = [start + w * (k + 1) for k in range(n)]
        problems.append(f'page {page}: chords placed by position on one line')
    lefts = [start] + edges[:-1]
    for m in sysm:
        m['c'] = []
    for x, sym in st.get('chords', []):
        k = next((i for i, e in enumerate(edges) if x < e - sp * 0.5), n - 1)
        m = sysm[k]
        beat = 48 * 4 // m['time'][1]
        frac = max(0.0, min(0.999, (x - lefts[k] - sp * 0.5) / max(1.0, edges[k] - lefts[k] - sp)))
        t = int(frac * m['len'] / beat) * beat
        if m['c'] and t <= m['c'][-1][0]:
            t = m['c'][-1][0] + beat
        if t < m['len']:
            tok = chord_tok(sym)
            if tok:
                m['c'].append([t, sym, tok])
    for k, m in enumerate(sysm):
        m['box'] = [page, round(lefts[k] * PT, 1), round(st['top'] * PT, 1), round(edges[k] * PT, 1), round(st['bot'] * PT, 1)]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf'); ap.add_argument('--book', required=True); ap.add_argument('--work', required=True)
    ap.add_argument('--first', type=int, default=1); ap.add_argument('--last', type=int, default=0)
    ap.add_argument('--phase', default='123'); ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--homr-jobs', type=int, default=2); ap.add_argument('--out')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    books = json.load(open(os.path.join(root, 'books.json')))
    binfo = next(b for b in books if b['id'] == a.book)
    last = a.last or int(subprocess.run(['pdfinfo', a.pdf], capture_output=True, text=True).stdout.split('Pages:')[1].split()[0])
    if '1' in a.phase:
        with ProcessPoolExecutor(a.jobs) as ex:
            for i, f in enumerate(ex.map(layout_page, [(a.pdf, p, a.work) for p in range(a.first, last + 1)])):
                print('layout', f, flush=True)
    if '2' in a.phase:
        imgs = sorted(os.path.join(a.work, f) for f in os.listdir(a.work) if f.startswith('piece-') and f.endswith('.png'))
        with ProcessPoolExecutor(a.homr_jobs) as ex:
            for f in ex.map(homr_piece, imgs):
                print('homr', f, flush=True)
    if '3' in a.phase:
        idx = next(b for b in json.load(open(os.path.join(root, 'songs.json'))) if b['id'] == a.book)['songs']
        songs = assemble(a.work, a.book, idx, binfo.get('offset', 0))
        ok = sum(1 for s in songs if not [p for p in s['problems'] if p.startswith('bar')])
        print(f'{len(songs)} songs, {ok} with every bar adding up', flush=True)
        if a.out:
            json.dump(songs, open(a.out, 'w'), separators=(',', ':'))
