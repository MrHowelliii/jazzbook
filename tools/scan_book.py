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
    for st in sts:                                       # SCAN_OCR_CHORDS=0: chords are read by eye (leads-overrides)
        st['chords'] = L.chords_above(img, st, prev) if os.environ.get('SCAN_OCR_CHORDS', '1') != '0' else []
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
        pieces.append({'title': t['text'] if t else None, 'staves': group})
    img.save(os.path.join(work, f'page-{page:03d}.png'))      # homr reads the whole (straightened) page
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


NOTE_RE = re.compile(r'^(r|[A-G](?:#|b)?-?\d)/(1|2|4|8|16|32)(\.?)(t?)(~?)$')
VALUE = {'1': 192, '2': 96, '4': 48, '8': 24, '16': 12, '32': 6}


def apply_override(meas, ov):
    """Chords (one token per beat, '.' = none) and corrected bars from leads-overrides/<book>.json."""
    if 'chords' in ov:
        for m in meas:
            m['c'] = []
        for bar, line in ov['chords'].items():
            k = int(bar) - 1
            if not 0 <= k < len(meas):
                continue
            m = meas[k]
            toks = line.split()
            unit = m['len'] // max(1, len(toks))               # the tokens divide the bar evenly
            for i, tok in enumerate(toks):
                if tok != '.' and chord_tok(tok):
                    m['c'].append([i * unit, tok, chord_tok(tok)])
    for bar, flags in ov.get('bars', {}).items():           # repeats and endings the notes reader missed
        k = int(bar) - 1
        if 0 <= k < len(meas):
            for f, v in flags.items():
                meas[k][f] = v
    for bar, line in ov.get('notes', {}).items():
        k = int(bar) - 1
        if not 0 <= k < len(meas):
            continue
        n, t, tied = [], 0, None
        for tok in line.split():
            mm = NOTE_RE.match(tok)
            if not mm:
                raise ValueError(f'bad note {tok!r} in bar {bar}')
            d = VALUE[mm.group(2)] * (3 if mm.group(3) else 2) // 2
            if mm.group(4):
                d = d * 2 // 3
            p = 0
            if mm.group(1) != 'r':
                pm = re.match(r'([A-G])(#|b)?(-?\d)', mm.group(1))
                p = 12 * (int(pm.group(3)) + 1) + M.STEP_PC[pm.group(1)] + {'#': 1, 'b': -1, None: 0}[pm.group(2)]
                if (k and t == 0 and meas[k - 1].get('tie_out') == p) or (tied == p and t > 0):
                    p += 128
            n += [t, d, p]
            tied = (p & 127) if mm.group(5) else None
            if mm.group(5):
                meas[k]['tie_out'] = p & 127
            t += d
        meas[k]['n'] = n
        meas[k]['filled'] = t


_SYS = {}


def page_systems(work, page):
    """homr's bars of one page, grouped by line of music (None when homr read nothing)."""
    if page not in _SYS:
        xml = os.path.join(work, f'page-{page:03d}.musicxml')
        if not os.path.exists(xml):
            _SYS[page] = None
        else:
            systems, cur = [], []
            for m in M.measures(M.load(xml)):
                if m['newsys'] and cur:
                    systems.append(cur); cur = []
                cur.append(m)
            if cur:
                systems.append(cur)
            _SYS[page] = systems
    return _SYS[page]


def assemble(work, book, index, offset, overrides=None, by_index=False):
    """by_index: songs start at the pages the book's index gives (for books whose titles the layout cannot read)."""
    overrides = overrides or {}
    starts = {}
    if by_index:
        for e in sorted(index, key=lambda e: e['page']):
            starts.setdefault(e['page'] + offset, e)
    pages = sorted(f for f in os.listdir(work) if f.startswith('layout-'))
    songs, cur = [], None
    for f in pages:
        lay = json.load(open(os.path.join(work, f)))
        pieces = lay.get('pieces', [])
        if by_index:                                     # one piece per page; a new song where the index says so
            pieces = [{'title': starts[lay['page']]['title'] if lay['page'] in starts else None,
                       'staves': list(range(len(lay.get('staves', []))))}] if lay.get('staves') else []
        for pc in pieces:
            if pc['title']:
                hit = starts.get(lay['page']) if by_index else match_title(pc['title'], index)
                nth = sum(1 for x in songs if x['page'] == lay['page'])
                cur = {'title': hit['title'] if hit else pc['title'].title(), 'composer': hit.get('composer') if hit else None,
                       'page': lay['page'], 'pages': [], 'parts': [], 'sid': f"{book}-{lay['page']}" + 'bcdef'[nth - 1:nth]}
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
            systems = page_systems(work, lay['page'])
            if systems is None:
                problems.append(f"page {lay['page']}: no notes read"); continue
            if len(systems) != len(lay['staves']):
                problems.append(f"page {lay['page']}: {len(systems)} lines of music read, {len(lay['staves'])} staves found")
            for k in pc['staves']:
                if k >= len(systems):
                    continue
                sysm = [dict(m) for m in systems[k]]
                place_chords(sysm, lay['staves'][k], lay['page'], problems)
                meas += sysm
        if not meas:
            continue
        sid = s['sid']
        ov = overrides.get(sid)
        if ov:
            apply_override(meas, ov)
        song = M.to_song(meas, s['title'], s['composer'], book, s['page'], s['pages'])
        if ov:
            song['reviewed'] = ov.get('read', True)
            song['problems'] = [p for p in song['problems'] if not p.startswith('only ') and 'placed by position' not in p]
            if ov.get('title'):
                song['title'] = ov['title']
        for b, m in zip(song['bars'], meas):
            b['box'] = m.get('box')
        song['id'] = sid
        song['parts'] = [[lay['page'], pc['staves']] for lay, pc in s['parts']]
        song['problems'] = problems + song['problems']
        if ov:                                           # chords read by eye: their placement is not a problem
            song['problems'] = [p for p in song['problems'] if 'placed by position' not in p and not p.startswith('only ')]
        song['scanned'] = True
        out.append(song)
    return out


def publishable(s):
    """Offered in the app: enough bars, nearly every bar adds up, chords read by eye (Tesseract alone misses too many)."""
    n = len(s['bars'])
    bad = sum(1 for p in s['problems'] if p.startswith('bar'))
    return bool(s.get('reviewed')) and n >= 8 and bad <= max(1, n // 20) and not any('no notes read' in p for p in s['problems'])


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
    ap.add_argument('--publish', help='write the songs that pass the checks here (leads/<book>.json)')
    ap.add_argument('--by-index', action='store_true', help='song boundaries from the index pages (no readable titles)')
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
        imgs = sorted(os.path.join(a.work, f) for f in os.listdir(a.work) if f.startswith('page-') and f.endswith('.png'))
        with ProcessPoolExecutor(a.homr_jobs) as ex:
            for f in ex.map(homr_piece, imgs):
                print('homr', f, flush=True)
    if '3' in a.phase:
        idx = next(b for b in json.load(open(os.path.join(root, 'songs.json'))) if b['id'] == a.book)['songs']
        ovf = os.path.join(root, 'leads-overrides', a.book + '.json')
        ovs = json.load(open(ovf)) if os.path.exists(ovf) else {}
        songs = assemble(a.work, a.book, idx, binfo.get('offset', 0), ovs, by_index=a.by_index)
        ok = sum(1 for s in songs if not [p for p in s['problems'] if p.startswith('bar')])
        print(f'{len(songs)} songs, {ok} with every bar adding up', flush=True)
        if a.out:
            json.dump(songs, open(a.out, 'w'), separators=(',', ':'))
        if a.publish:
            keep = [s for s in songs if publishable(s)]
            json.dump(keep, open(a.publish, 'w'), separators=(',', ':'))
            print(f'{len(keep)} songs published to {a.publish} ({sum(1 for s in keep if s.get("reviewed"))} with chords read by eye)')
