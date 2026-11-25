#!/usr/bin/env python3
"""scan_layout.py - the layout of a scanned lead-sheet page: staves, barlines, song titles and chord symbols.

Works on the page image (300 dpi): staff lines from the row profile, barlines as dark columns that run exactly from
the top to the bottom staff line, titles as tall upper-case text lines between staves (Tesseract), chord symbols as
the text in the band just above each staff (Tesseract, word boxes). homr reads the notes; this file says where things
are, so chords land in the right bar and the player can highlight bars on the original page.

  python3 tools/scan_layout.py books/jazzfake.pdf 425        # prints what it finds on page 425
"""
import json, os, re, subprocess, sys, tempfile
TESS_ENV = dict(os.environ, OMP_THREAD_LIMIT='1')        # one thread each: the pages run in parallel
import numpy as np
from PIL import Image

DPI = 300


def render(pdf, page, dpi=DPI):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(['pdftoppm', '-r', str(dpi), '-gray', '-png', '-f', str(page), '-l', str(page), pdf,
                        os.path.join(td, 'p')], check=True)
        f = [x for x in os.listdir(td) if x.endswith('.png')][0]
        return deskew(Image.open(os.path.join(td, f)).convert('L'))


def deskew(img):
    """Scans are often tilted by up to a degree or two: straighten so that staff lines are rows."""
    small = img.resize((img.width // 3, img.height // 3))
    best, ang = -1, 0.0
    for a10 in range(-25, 26):
        a = a10 / 10
        r = np.array(small.rotate(a, resample=Image.BILINEAR, fillcolor=255)) < 160
        prof = r.mean(axis=1)
        score = float((prof ** 2).sum())
        if score > best:
            best, ang = score, a
    return img.rotate(ang, resample=Image.BICUBIC, fillcolor=255) if ang else img


def staves(ink):
    """ink: bool array (dark). Returns staves [{top, bot, sp, x0, x1, lines}] sorted by y."""
    h, w = ink.shape
    band = ink[:, int(w * 0.08):int(w * 0.92)]
    rowfrac = band.mean(axis=1)
    # a slightly tilted line spreads over 2-3 rows: also look at each row OR its neighbours
    spread = (band[:-2] | band[1:-1] | band[2:]).mean(axis=1)
    rowfrac = np.maximum(rowfrac, np.concatenate([[0], spread, [0]]) * 0.8)
    cand = np.where(rowfrac > 0.3)[0]
    lines, run = [], []
    for y in cand:
        if run and y - run[-1] > 1:
            lines.append(sum(run) / len(run)); run = []
        run.append(y)
    if run:
        lines.append(sum(run) / len(run))
    out, i = [], 0
    while i + 4 < len(lines):
        g = lines[i:i + 5]
        gaps = np.diff(g)
        if gaps.max() - gaps.min() <= max(2.5, gaps.mean() * 0.25) and 6 < gaps.mean() < 40:
            top, bot = g[0], g[4]
            # horizontal extent: columns where at least 3 of the 5 lines are dark (gaps of broken lines bridged)
            cov = np.zeros(w)
            for y in g:
                yy = int(round(y)); cov += ink[max(0, yy - 3):yy + 4, :].any(axis=0)
            cols = np.where(cov >= 3)[0]
            runs, s0 = [], cols[0] if len(cols) else 0
            for a, b in zip(cols, cols[1:]):
                if b - a > gaps.mean() * 3:
                    runs.append((s0, a)); s0 = b
            if len(cols):
                runs.append((s0, cols[-1]))
            x0, x1 = max(runs, key=lambda r: r[1] - r[0]) if runs else (0, w)
            out.append({'top': float(top), 'bot': float(bot), 'sp': float(gaps.mean()), 'x0': int(x0), 'x1': int(x1),
                        'lines': [float(v) for v in g]})
            i += 5
        else:
            i += 1
    return out


def barlines(ink, st):
    """x positions of barlines in one staff (thick/double barlines merged)."""
    top, bot, sp = int(round(st['top'])), int(round(st['bot'])), st['sp']
    h = ink.shape[0]
    seg = ink[top - 1:bot + 2, st['x0']:st['x1'] + 1]
    full = seg.mean(axis=0) > 0.92
    above = ink[max(0, top - int(sp * 0.9)):top - int(sp * 0.35), st['x0']:st['x1'] + 1].mean(axis=0)
    below = ink[bot + int(sp * 0.35):min(h, bot + int(sp * 0.9)), st['x0']:st['x1'] + 1].mean(axis=0)
    xs = [i for i in np.where(full)[0] if above[i] < 0.5 and below[i] < 0.5]   # stops at the outer lines: not a stem
    # a stem that happens to span the staff has a notehead at one end: ink beside the line at the top or bottom
    r = max(3, int(sp * 0.9))
    def head_at(x, y):
        box = ink[max(0, y - r // 2):y + r // 2 + 1, st['x0'] + x - r:st['x0'] + x + r + 1]
        side = np.concatenate([box[:, :max(1, r - 2)], box[:, r + 3:]], axis=1)
        return side.size and side.mean() > 0.38
    xs = [x for x in xs if not head_at(x, top) and not head_at(x, bot)]
    groups = []
    for x in xs:
        if groups and x - groups[-1][-1] <= max(3, sp * 0.9):
            groups[-1].append(x)
        else:
            groups.append([x])
    return [int(st['x0'] + (g[0] + g[-1]) / 2) for g in groups]


def tesseract_words(img, psm=11, extra=()):
    """Word boxes: [(text, x, y, w, h, conf)]."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'i.png'); img.save(p)
        try:
            r = subprocess.run(['tesseract', p, 'stdout', '--psm', str(psm), *extra, 'tsv'], capture_output=True, text=True,
                               env=TESS_ENV, timeout=30)
        except subprocess.TimeoutExpired:
            return []
    words = []
    for line in r.stdout.splitlines()[1:]:
        f = line.split('\t')
        if len(f) == 12 and f[11].strip():
            words.append((f[11].strip(), int(f[6]), int(f[7]), int(f[8]), int(f[9]), float(f[10])))
    return words


def text_lines(img):
    """Lines of text with their box and median word height (for titles)."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'i.png'); img.save(p)
        try:
            r = subprocess.run(['tesseract', p, 'stdout', '--psm', '3', 'tsv'], capture_output=True, text=True,
                               env=TESS_ENV, timeout=60)
        except subprocess.TimeoutExpired:
            return []
    lines = {}
    for line in r.stdout.splitlines()[1:]:
        f = line.split('\t')
        if len(f) == 12 and f[11].strip():
            k = (f[2], f[3], f[4])
            lines.setdefault(k, []).append((f[11].strip(), int(f[6]), int(f[7]), int(f[8]), int(f[9]), float(f[10])))
    out = []
    for ws in lines.values():
        out.append({'text': ' '.join(w[0] for w in ws), 'x0': min(w[1] for w in ws), 'y0': min(w[2] for w in ws),
                    'x1': max(w[1] + w[3] for w in ws), 'y1': max(w[2] + w[4] for w in ws),
                    'h': float(np.median([w[4] for w in ws])), 'conf': float(np.mean([w[5] for w in ws]))})
    return out


CHORD_OK = re.compile(r'^\(?[A-G](b|#)?(maj|M|m|min|dim|aug|sus|o|°|ø|\+|-|[0-9]|b|#|\(|\)|/|[A-G])*\)?$')


def clean_chord(t):
    t = re.sub(r'^([A-G])o(?=[0-9M+]|$)', r'\1b', t)    # the flat sign read as "o" right after the root
    t = re.sub(r'/([A-G])o$', r'/\1b', t)
    t = t.replace('♭', 'b').replace('♯', '#').replace('º', 'o').replace('°', 'o').replace('|', '').replace('’', '').replace("'", '')
    t = t.replace('Ml', 'M7').replace('M/', 'M7').replace('mi', 'm')
    t = re.sub(r'(?<=[A-G])h', 'b', t)              # a flat sign read as h
    t = re.sub(r'(?<=\d)l', '1', t)
    t = re.sub(r'([A-G][b#]?)M7', r'\1maj7', t)
    t = re.sub(r'([A-G][b#]?)M9', r'\1maj9', t)
    return t


WHITELIST = 'ABCDEFGabdgijmnosu0123456789#+-()/'


SUFFIXES = ['', 'm', '7', 'm7', 'maj7', '6', 'm6', '9', 'm9', 'maj9', '69', '6/9', '11', 'm11', '13', 'm13', 'maj13',
            '7b9', '7#9', '7b5', '7#5', '7+5', '7-5', 'm7b5', 'm7-5', 'dim', 'dim7', 'o', 'o7', '+', '+7', 'aug', 'aug7', '7+',
            'sus', 'sus4', '7sus', '7sus4', '9sus4', 'sus2', '13b9', '13#11', '9#11', '7#11', '9b5', '9+5', '7b9b5', '7#9#5',
            'm(maj7)', 'mmaj7', 'm9b5', 'add9', '7b13', 'm7b9', 'maj7#11', '7alt', '6add9', 'm6/9']
ROOTS = [r + a for r in 'ABCDEFG' for a in ('', 'b', '#')]


def _dist(a, b):
    """Edit distance where OCR look-alikes are cheap (5/b/o, 1/l/7, 0/o, m/n)."""
    near = {frozenset(p) for p in ('5b', 'ob', '0o', '17', '1l', 'mn', 'oe', '+t', '#H', '9g', '6b', '2z', 's5')}
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 0.4 if frozenset((ca, cb)) in near else 1
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + cost)
    return d[-1]


def snap_chord(t):
    """OCR text -> the closest real chord spelling (root, suffix, optional /bass), or None."""
    t = clean_chord(t)
    m = re.match(r'^\(?([A-G])([b#o]?)(.*?)(?:/([A-G][b#o]?))?\)?$', t)
    if not m:
        return None
    root = m.group(1) + ('b' if m.group(2) == 'o' else m.group(2))
    body = m.group(3).replace('M7', 'maj7').replace('M9', 'maj9').replace('Ma7', 'maj7').replace('mi', 'm')
    if len(body) > 9:
        return None
    best = min(SUFFIXES, key=lambda x: (_dist(body, x), len(x)))
    if _dist(body, best) > max(1.0, len(best) * 0.4):
        return None
    bass = m.group(4)
    if bass:
        bass = bass[0] + ('b' if bass[1:] == 'o' else bass[1:])
    return root + best + ('/' + bass if bass else '')


def chords_above(img, st, prev_bot=None):
    """Chord symbols over one staff: [(x, text)] in page pixels. The band above the staff is cut into words (dark
    column runs), each word read alone by Tesseract with chord characters only, then snapped to a real chord."""
    from PIL import ImageFilter
    sp = st['sp']
    y0 = st['top'] - sp * 5.5
    if prev_bot is not None:
        y0 = max(y0, prev_bot + sp * 5)
    y1 = st['top'] - sp * 0.6
    x0, x1 = max(0, st['x0'] - int(sp * 3)), min(img.width, st['x1'] + int(sp * 2))
    crop = img.crop((x0, int(y0), x1, int(y1)))
    a = np.array(crop) < 150
    # rows of text: the tallest dark row band is the chord line (tuplet brackets and marks are thin)
    cols = a.any(axis=0)
    words, start = [], None
    for x in range(len(cols) + 1):
        on = x < len(cols) and cols[x]
        if on and start is None:
            start = x
        if not on and start is not None:
            words.append([start, x]); start = None
    merged = []
    for w in words:                                    # letters of one chord are close; chords are apart
        if merged and w[0] - merged[-1][1] < sp * 0.7:
            merged[-1][1] = w[1]
        else:
            merged.append(w)
    out = []
    for wx0, wx1 in merged:
        sub = a[:, wx0:wx1]
        ys = np.where(sub.any(axis=1))[0]
        if not len(ys) or ys[-1] - ys[0] < sp * 0.8 or wx1 - wx0 < sp * 0.6:
            continue                                   # too small for a chord (a dot, a bracket end)
        if (sub.mean(axis=0) > 0.9).sum() > (wx1 - wx0) * 0.6:
            continue                                   # a horizontal line (bracket, extender)
        piece = crop.crop((max(0, wx0 - 6), max(0, ys[0] - 6), min(crop.width, wx1 + 6), min(crop.height, ys[-1] + 7)))
        big = piece.resize((piece.width * 3, piece.height * 3)).filter(ImageFilter.GaussianBlur(1.2))
        pad = Image.new('L', (big.width + 40, big.height + 40), 255); pad.paste(big, (20, 20))
        ws = tesseract_words(pad, 7, ('-c', 'tessedit_char_whitelist=' + WHITELIST))
        text = ''.join(w[0] for w in ws)
        c = snap_chord(text) if text else None
        if c and x0 + wx0 > st['x0'] + sp * 2.6:          # not the top of the clef
            out.append((x0 + wx0, c))
    return out


def analyse(pdf, page):
    img = render(pdf, page)
    a = np.array(img)
    ink = a < 150
    sts = staves(ink)
    for st in sts:
        st['bars'] = barlines(ink, st)
    # titles sit above the first staff or in a wide gap between staves: read only those bands
    sp = float(np.median([st['sp'] for st in sts])) if sts else 14.0
    bands, prev = [], 0
    for st in sts + [{'top': img.height + sp * 10, 'bot': img.height}]:
        y0, y1 = prev, st['top'] - sp * 4
        if y1 - y0 > sp * 6 and (prev == 0 or st['top'] - prev > sp * 16):
            bands.append((int(max(0, y0)), int(min(img.height, y1))))
        prev = st['bot'] + sp * 5
    tl = []
    for y0, y1 in bands:
        for t in text_lines(img.crop((0, y0, img.width, y1))):
            t['y0'] += y0; t['y1'] += y0; tl.append(t)
    titles = [t for t in tl if t['h'] > sp * 2.0 and t['conf'] > 40 and sum(c.isalpha() for c in t['text']) >= 3
              and sum(c.isupper() for c in t['text']) >= 0.6 * sum(c.isalpha() for c in t['text'])]
    return img, sts, titles


if __name__ == '__main__':
    pdf, page = sys.argv[1], int(sys.argv[2])
    img, sts, titles = analyse(pdf, page)
    print('titles', [(t['text'], t['y0'], round(t['h'])) for t in titles])
    prev = None
    for st in sts:
        ch = chords_above(img, st, prev)
        prev = st['bot']
        print(f"staff y {st['top']:.0f}-{st['bot']:.0f} sp {st['sp']:.1f} x {st['x0']}-{st['x1']} bars {st['bars']}")
        print('    chords', ch)
