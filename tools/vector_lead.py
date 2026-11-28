#!/usr/bin/env python3
"""
vector_lead.py - read lead sheets (melody + chord symbols) straight out of a
vector PDF engraved with Finale's Maestro music font.

Nothing is guessed from pixels: noteheads, rests, clefs, accidentals and flags
are font glyphs (Maestro, Mac Roman codes in the 0xF000 private area), staff
lines / stems / barlines are stroked lines, beams are filled quadrilaterals and
ties are filled curves.  Chord symbols are text in a Finale chord font.

Every bar is checked: its note values must add up to the time signature, and
the bar count must agree with the measure numbers printed at the left margin.

Usage:
  python3 tools/vector_lead.py books/462jazzstandardsS.pdf --first 35 --out leads/462jazz.json
  python3 tools/vector_lead.py books/462jazzstandardsS.pdf --pages 60 --debug
"""
import argparse, json, math, re, sys
from collections import defaultdict

import pymupdf as fitz

TPQ = 48                                    # ticks per quarter note (3 and 16 divide it)

# Engraving profile: Finale's Maestro (the 462 book) by default; --font inkpen for Finale's Jazz/Inkpen2 (the Django
# Fakebook): the same glyph codes, but thicker stems and barlines, and chord symbols in plain ASCII (- for minor,
# 0 for diminished) in a font whose name ends in "Chords".
PROFILE = {'stem_max': 0.6, 'bar_min': 0.6, 'thick': 1.5, 'chord_font': None, 'ascii_chords': False, 'tuplet_max': 12,
           'title_font': None, 'stem_tol': 1.4}

# Maestro glyphs (Mac Roman byte -> meaning)
G = lambda b: 0xF000 + b
HEAD_FILLED, HEAD_HALF, HEAD_WHOLE = G(0xCF), G(0xFA), G(0x77)
SHARP, FLAT, NATURAL, DSHARP, DFLAT = G(0x23), G(0x62), G(0x6E), G(0xDC), G(0xBA)
CLEF_G, CLEF_F = G(0x26), G(0x3F)
DOT = G(0xAA)
FLAG8_UP, FLAG8_DN, FLAG16_UP, FLAG16_DN = G(0x6A), G(0x4A), G(0x72), G(0x52)
REST = {G(0xB7): 'whole', G(0xEE): 2 * TPQ, G(0xCE): TPQ, G(0xE4): TPQ // 2, G(0xC5): TPQ // 4, G(0xA8): TPQ // 8}
TIME_C, TIME_CUT = G(0x43), G(0xA2)
SEGNO, CODA = G(0x25), G(0xDE)
ACC_ALTER = {SHARP: 1, FLAT: -1, NATURAL: 0, DSHARP: 2, DFLAT: -2}
STEP_PC = [0, 2, 4, 5, 7, 9, 11]           # C D E F G A B

# Finale chord font -> plain text
CHORD_MAP = [('Œ„Š', 'maj'), ('‹', 'm'), ('¨', 'b'), ('©', '#'), ('º', '°'), ('°', '°'), ('Ø', 'ø'), ('&', '+'),
             ('“', 'sus'), ('„ˆˆ', 'add'), ('ˆ', ''), ('½', '+'), ('%', '6/9')]
BRACKET_ALT = re.compile(r'\[[^\]]*\]')


def chord_text(raw):
    t = raw
    if PROFILE['ascii_chords']:                 # Inkpen2Chords: C-7, C#07 (dim), C07b5? -> Cm7, C#°7
        t = re.sub(r'^([A-G][b#]?)-', r'\1m', t)
        t = re.sub(r'^([A-G][b#]?)(m?)0', r'\1\2°', t)
        return t.strip()
    for a, b in CHORD_MAP:
        t = t.replace(a, b)
    t = BRACKET_ALT.sub('alt', t)
    return t.strip()


CHORD_RE = re.compile(r'^\(?([A-G])([b#]?)([^/\s]*?)(?:/([A-G][b#]?))?\)?$')


def parse_chord(sym):
    """'Ebm7(b5)/Db' -> dict(root=3, bass=1, quality='m7b5', ireal='Eb-7b5') or None."""
    m = CHORD_RE.match(sym)
    if not m:
        return None
    root = (STEP_PC['CDEFGAB'.index(m.group(1))] + {'b': -1, '#': 1, '': 0}[m.group(2)]) % 12
    q = m.group(3)
    if not re.fullmatch(r"(maj|m|°|ø|\+|sus|add|alt|aug|dim|[0-9]|[b#]|\(|\)|,|/)*", q):
        return None
    bass = None
    if m.group(4):
        b = m.group(4)
        bass = (STEP_PC['CDEFGAB'.index(b[0])] + (1 if b[1:] == '#' else -1 if b[1:] == 'b' else 0)) % 12
    return {'sym': sym, 'root': root, 'bass': bass, 'ireal': ireal_token(m.group(1) + m.group(2), q)}


def ireal_token(root, q):
    """Map a chord quality onto the spellings the Jazz Band engine understands."""
    s = q.replace('(', '').replace(')', '')
    if s.startswith('maj') or s.startswith('ma'):
        return root + '^7'
    if s.startswith('mmaj') or s.startswith('m(maj') or 'mmaj' in s:
        return root + '-^7'
    if s.startswith('m') and ('7b5' in s):
        return root + 'h7'
    if s.startswith('ø'):
        return root + 'h7'
    if s.startswith('°'):
        return root + 'o7'
    if s.startswith('m'):
        rest = s[1:]
        if rest.startswith('6') or rest == '':
            return root + '-6'
        return root + '-7'
    if s.startswith('+') or s.startswith('aug'):
        return root + '7'
    if s.startswith('sus'):
        return root + '7'
    if s == '' or s in ('6', '69', '6/9', 'add9', 'add2'):
        return root + '6'
    if s[0] in '79' or s.startswith('13') or s.startswith('11'):
        if 'b9' in s or 'alt' in s:
            return root + '7b9'
        return root + '7'
    return root + '^7'


def L(*a):
    print(*a, file=sys.stderr)


class Glyph:
    __slots__ = ('code', 'x', 'y', 'size', 'used')

    def __init__(self, code, x, y, size):
        self.code, self.x, self.y, self.size, self.used = code, x, y, size, False

    def __repr__(self):
        return f"{bytes([self.code - 0xF000]).decode('mac_roman')}@{self.x:.1f},{self.y:.1f}"


class Text:
    __slots__ = ('text', 'x', 'y', 'x1', 'size', 'font', 'chars')

    def __init__(self, text, x, y, x1, size, font, chars):
        self.text, self.x, self.y, self.x1, self.size, self.font, self.chars = text, x, y, x1, size, font, chars

    def __repr__(self):
        return f"{self.text!r}@{self.x:.1f},{self.y:.1f}/{self.size:.1f}"


# --------------------------------------------------------------------------------------------- page extraction
def page_items(page):
    glyphs, texts = [], []
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                run = []
                for c in s['chars']:
                    o = ord(c['c'])
                    if o >= 0xF000 and PROFILE['ascii_chords'] and 'Special' in s['font'] and o not in (DOT, CODA, SEGNO):
                        continue                 # Inkpen2Special: articulations and boxes share Maestro's codes
                    if o >= 0xF000:
                        glyphs.append(Glyph(o, c['origin'][0], c['origin'][1], s['size']))
                    else:
                        run.append(c)
                # split text runs at spaces so each chord / word keeps its own x
                word = []
                for c in run + [None]:
                    if c is None or c['c'] == ' ':
                        if word:
                            t = ''.join(w['c'] for w in word)
                            texts.append(Text(t, word[0]['origin'][0], word[0]['origin'][1], word[-1]['bbox'][2],
                                              s['size'], s['font'], word))
                        word = []
                    else:
                        word.append(c)
    hl, vl, fills, curves = [], [], [], []
    for d in page.get_drawings():
        items = d['items']
        if d['type'] in ('s', 'fs') and all(i[0] == 'l' for i in items):
            for i in items:
                a, b = i[1], i[2]
                w = d.get('width') or 0
                if abs(a.y - b.y) < 0.2:
                    hl.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2, w))
                elif abs(a.x - b.x) < 0.2:
                    vl.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y), w))
        if d['type'] in ('f', 'fs'):
            kinds = ''.join(i[0] for i in items)
            r = d['rect']
            if 'c' in kinds:
                sub, last = [], None
                for i in items:
                    pts = list(i[1:]) if i[0] in ('l', 'c') else []
                    pts = [q for q in pts if hasattr(q, 'x')]
                    if not pts:
                        continue
                    if last is not None and (abs(pts[0].x - last.x) > 0.01 or abs(pts[0].y - last.y) > 0.01):
                        if sub:
                            curves.append((min(q.x for q in sub), min(q.y for q in sub), max(q.x for q in sub), max(q.y for q in sub)))
                        sub = []
                    sub += [pts[0], pts[-1]] if i[0] == 'l' else pts
                    last = pts[-1]
                if sub:
                    curves.append((min(q.x for q in sub), min(q.y for q in sub), max(q.x for q in sub), max(q.y for q in sub)))
            else:
                polys = []
                cur = []
                for i in items:
                    if i[0] == 're':
                        q = i[1]
                        polys.append([fitz.Point(q.x0, q.y0), fitz.Point(q.x1, q.y0), fitz.Point(q.x0, q.y1), fitz.Point(q.x1, q.y1)])
                    elif i[0] == 'qu':
                        q = i[1]
                        polys.append([q.ul, q.ur, q.ll, q.lr])
                    elif i[0] == 'l':
                        if cur and (abs(i[1].x - cur[-1].x) > 0.01 or abs(i[1].y - cur[-1].y) > 0.01):
                            polys.append(cur); cur = []
                        if not cur:
                            cur.append(i[1])
                        cur.append(i[2])
                        if len(cur) >= 4 and abs(cur[-1].x - cur[0].x) < 0.01 and abs(cur[-1].y - cur[0].y) < 0.01:
                            polys.append(cur); cur = []
                if cur:
                    polys.append(cur)
                for pts in polys:
                    x0, x1 = min(p.x for p in pts), max(p.x for p in pts)
                    if x1 - x0 < 2:
                        continue
                    left = [p.y for p in pts if abs(p.x - x0) < 0.3]
                    right = [p.y for p in pts if abs(p.x - x1) < 0.3]
                    if left and right and max(left) - min(left) < 6:
                        fills.append({'x0': x0, 'x1': x1, 'yl': sum(left) / len(left), 'yr': sum(right) / len(right),
                                      'th': (max(left) - min(left))})
    return glyphs, texts, hl, vl, fills, curves


def find_staves(hl):
    """Group full-width horizontal lines into 5-line staves."""
    lines = [h for h in hl if h[1] - h[0] > 150]
    byy = {}
    for x0, x1, y, w in sorted(lines, key=lambda h: h[2]):
        k = round(y, 1)
        if k in byy:
            a = byy[k]
            byy[k] = (min(a[0], x0), max(a[1], x1), y, w)
        else:
            byy[k] = (x0, x1, y, w)
    ys = sorted(byy.values(), key=lambda h: h[2])
    staves, i = [], 0
    while i + 4 < len(ys):
        grp = ys[i:i + 5]
        gaps = [grp[k + 1][2] - grp[k][2] for k in range(4)]
        if max(gaps) - min(gaps) < 0.6 and 3 < gaps[0] < 9:
            staves.append({'top': grp[0][2], 'bot': grp[4][2], 'sp': (grp[4][2] - grp[0][2]) / 4,
                           'x0': min(g[0] for g in grp), 'x1': max(g[1] for g in grp)})
            i += 5
        else:
            i += 1
    return staves


# --------------------------------------------------------------------------------------------- one staff
def staff_of(staves, y, above=4.0, below=4.0):
    best = None
    for k, s in enumerate(staves):
        lo, hi = s['top'] - above * s['sp'] * 2, s['bot'] + below * s['sp'] * 2
        if lo <= y <= hi:
            d = 0 if s['top'] <= y <= s['bot'] else min(abs(y - s['top']), abs(y - s['bot']))
            if best is None or d < best[0]:
                best = (d, k)
    return None if best is None else best[1]


class Event:
    __slots__ = ('x', 'kind', 'heads', 'dur', 'dots', 'tie_start', 'tie_end', 'tuplet', 'pitch', 'stem', 'beams', 'flags',
                 'onset', 'grace', 'whole_rest', 'midi_alter', 'midi_acc')

    def __init__(self, x, kind):
        self.x, self.kind = x, kind
        self.heads, self.dur, self.dots = [], 0, 0
        self.tie_start = self.tie_end = False
        self.tuplet = None
        self.pitch = None
        self.stem = None
        self.beams = self.flags = 0
        self.onset = 0
        self.grace = False
        self.whole_rest = False

    def __repr__(self):
        return f"{self.kind}@{self.x:.0f} d={self.dur} p={self.pitch}{' ~' if self.tie_start else ''}"


def analyse_staff(st, glyphs, texts, vl, fills, curves, hl, warn):
    sp = st['sp']
    half = sp / 2
    top, bot = st['top'], st['bot']
    mid = (top + bot) / 2
    big = [g for g in glyphs if g.size >= 17]

    # ---- barlines (thin and thick), repeat dots
    bars = []
    for x, y0, y1, w in vl:
        if w >= PROFILE['bar_min'] and y0 <= top + 1.0 and y1 >= bot - 1.0 and y1 - y0 < (bot - top) * 1.6 + 2 and st['x0'] - 2 <= x <= st['x1'] + 2:
            bars.append((x, w))
    bars.sort()
    groups = []
    for x, w in bars:
        if groups and x - groups[-1]['xs'][-1] < 6:
            groups[-1]['xs'].append(x); groups[-1]['ws'].append(w)
        else:
            groups.append({'xs': [x], 'ws': [w]})
    dots = [g for g in big if g.code == DOT]
    for gr in groups:
        gr['x0'], gr['x1'] = min(gr['xs']) - 0.5, max(gr['xs']) + 0.5
        gr['thick'] = any(w > PROFILE['thick'] for w in gr['ws'])
        gr['double'] = len(gr['xs']) >= 2
        rd_left = [d for d in dots if gr['x0'] - 9 < d.x < gr['x0'] and abs(d.y - mid) < sp * 1.2]
        rd_right = [d for d in dots if gr['x1'] < d.x < gr['x1'] + 9 and abs(d.y - mid) < sp * 1.2]
        gr['rep_end'] = len(rd_left) >= 2
        gr['rep_start'] = len(rd_right) >= 2
        for d in rd_left + rd_right if (gr['thick'] or gr['double']) else []:
            d.used = True

    # ---- clefs, time signatures, key signatures
    clefs = sorted([(g.x, 'G' if g.code == CLEF_G else 'F') for g in big if g.code in (CLEF_G, CLEF_F)])
    for g in big:
        if g.code in (CLEF_G, CLEF_F):
            g.used = True
    times = []
    digits = [g for g in big if 0xF030 <= g.code <= 0xF039]
    for g in digits:
        if g.used:
            continue
        mate = [h for h in digits if h is not g and not h.used and abs(h.x - g.x) < 3 and abs(h.y - g.y) > sp]
        if mate:
            h = mate[0]
            up, lo = (g, h) if g.y < h.y else (h, g)
            # multi-digit numerators (12/8): neighbour digits on the same row
            num = ''.join(chr(d.code - 0xF000) for d in sorted([d for d in digits if abs(d.y - up.y) < 1 and abs(d.x - up.x) < 12], key=lambda d: d.x))
            den = ''.join(chr(d.code - 0xF000) for d in sorted([d for d in digits if abs(d.y - lo.y) < 1 and abs(d.x - lo.x) < 12], key=lambda d: d.x))
            for d in digits:
                if (abs(d.y - up.y) < 1 and abs(d.x - up.x) < 12) or (abs(d.y - lo.y) < 1 and abs(d.x - lo.x) < 12):
                    d.used = True
            try:
                times.append((min(up.x, lo.x), int(num), int(den)))
            except ValueError:
                pass
    for g in big:
        if g.code == TIME_C:
            times.append((g.x, 4, 4)); g.used = True
        elif g.code == TIME_CUT:
            times.append((g.x, 2, 2)); g.used = True
    times.sort()

    if PROFILE['ascii_chords']:                     # Inkpen2: a few notes use the round "O" head; play them as filled
        for g in big:
            if g.code == G(0x4F): g.code = HEAD_FILLED
            elif g.code == G(0xC0): g.code = G(0xCE)     # the old-style (Z) quarter rest
    heads = [g for g in big if g.code in (HEAD_FILLED, HEAD_HALF, HEAD_WHOLE)]
    accs = [g for g in big if g.code in ACC_ALTER]
    note_acc, keysig_acc = {}, []
    for a in accs:
        tgt = [h for h in heads if 2.5 < h.x - a.x < 16 and abs(h.y - a.y) < 0.8]
        if tgt:
            h = min(tgt, key=lambda h: h.x - a.x)
            # the closest accidental to a head wins (chords stack accidentals leftwards)
            if id(h) not in note_acc or note_acc[id(h)].x < a.x:
                note_acc[id(h)] = a
        else:
            keysig_acc.append(a)
    keys = []          # (x, fifths)
    keysig_acc.sort(key=lambda g: g.x)
    run = []
    for a in keysig_acc + [None]:
        if a is not None and run and a.x - run[-1].x < 8:
            run.append(a); continue
        if run:
            sh = sum(1 for g in run if g.code == SHARP)
            fl = sum(1 for g in run if g.code == FLAT)
            keys.append((run[0].x, sh if sh else -fl))
        run = [a] if a is not None else []

    def clef_at(x):
        c = 'G'
        for cx, k in clefs:
            if cx <= x:
                c = k
        return c

    def step_of(y, x):
        steps = round((bot - y) / half)
        base = 30 if clef_at(x) == 'G' else 18      # diatonic index of the bottom line: E4 / G2
        return base + steps

    # ---- stems
    stems = [(x, y0, y1) for x, y0, y1, w in vl if w < PROFILE['stem_max'] and y1 - y0 > sp * 1.8 and top - sp * 8 < y0 and y1 < bot + sp * 8]
    head_w = 0.333 * 19.8
    for h in heads:
        h.used = True
    ev_by_stem = {}
    events = []
    for h in heads:
        if h.code == HEAD_WHOLE:
            continue
        hw = 0.333 * h.size
        cand = []
        for k, (x, y0, y1) in enumerate(stems):
            if y0 - 2 <= h.y <= y1 + 2:
                dirn = 'up' if abs(h.y - y1) < abs(h.y - y0) else 'down'
                # a head sits left of an up stem / right of a down stem, or on the other side in a second
                if abs(x - (h.x + hw)) < PROFILE['stem_tol']:
                    cand.append((abs(x - (h.x + hw)) + (0 if dirn == 'up' else 0.5), k, dirn))
                elif abs(x - h.x) < PROFILE['stem_tol']:
                    cand.append((abs(x - h.x) + (0 if dirn == 'down' else 0.5), k, dirn))
        if not cand:
            # a stemless filled/half head: slash or cue, keep it as a quarter / half
            e = Event(h.x, 'note'); e.heads.append(h); events.append(e)
            warn(f"stemless head {h!r}")
            continue
        cand.sort()
        _, k, dirn = cand[0]
        if k in ev_by_stem:
            ev_by_stem[k].heads.append(h)
        else:
            x, y0, y1 = stems[k]
            e = Event(h.x, 'note'); e.heads.append(h); e.stem = (x, y0, y1, dirn)
            ev_by_stem[k] = e; events.append(e)
    wholes = sorted([h for h in heads if h.code == HEAD_WHOLE], key=lambda h: h.x)
    for h in wholes:
        for e in events:
            if e.kind == 'note' and e.heads[0].code == HEAD_WHOLE and abs(e.x - h.x) < 3:
                e.heads.append(h); break
        else:
            e = Event(h.x, 'note'); e.heads.append(h); events.append(e)

    # ---- flags and beams -> note values
    flags = [g for g in big if g.code in (FLAG8_UP, FLAG8_DN, FLAG16_UP, FLAG16_DN)]
    for e in events:
        h = e.heads[0]
        if h.code == HEAD_WHOLE:
            e.dur = 4 * TPQ; continue
        if h.code == HEAD_HALF:
            e.dur = 2 * TPQ; continue
        if not e.stem:
            e.dur = TPQ; continue
        sx, y0, y1, dirn = e.stem
        tip = y0 if dirn == 'up' else y1
        nfl = 0
        for f in flags:
            if abs(f.x - sx) < 1.6 and abs(f.y - tip) < sp * 2.2:
                nfl = max(nfl, 1 if f.code in (FLAG8_UP, FLAG8_DN) else 2)
                f.used = True
        nb = 0
        seen = []
        for b in fills:
            if b['x0'] - 0.9 <= sx <= b['x1'] + 0.9:
                t = 0 if b['x1'] == b['x0'] else (sx - b['x0']) / (b['x1'] - b['x0'])
                by = b['yl'] + (b['yr'] - b['yl']) * t
                d = (by - tip) if dirn == 'up' else (tip - by)
                if -2.2 <= d <= sp * 2.6 and all(abs(by - s) > 1.6 for s in seen):
                    seen.append(by); nb += 1
        e.flags, e.beams = nfl, nb
        e.dur = TPQ >> min(3, nfl + nb)

    # ---- rests
    for g in big:
        if g.code in REST:
            v = REST[g.code]
            e = Event(g.x, 'rest'); e.heads.append(g); g.used = True
            if v == 'whole':
                e.whole_rest = True; e.dur = 4 * TPQ
            else:
                e.dur = v
            events.append(e)

    # ---- augmentation dots
    for e in events:
        h = e.heads[0]
        cand = [d for d in dots if not d.used and 3 < d.x - h.x < 17 and abs(d.y - h.y) < sp * 0.8]
        cand += [d for d in dots if not d.used and 3 < d.x - h.x < 17 and any(abs(d.y - hh.y) < sp * 0.8 for hh in e.heads[1:])]
        if cand:
            xs = sorted(set(round(d.x) for d in cand))
            ndots = 1 if len(xs) == 1 or xs[1] - xs[0] > 6 else 2
            e.dots = ndots
            for d in cand:
                d.used = True
            e.dur = e.dur * 3 // 2 if ndots == 1 else e.dur * 7 // 4

    events.sort(key=lambda e: e.x)

    # ---- pitches (top note of each chord), accidentals carried through the bar
    bar_xs = [g['x1'] for g in groups]

    def bar_index(x):
        return sum(1 for b in bar_xs if b < x)

    def key_at(x):
        k = None
        for kx, f in keys:
            if kx <= x:
                k = f
        return k

    for e in events:
        if e.kind != 'note':
            continue
        e.heads.sort(key=lambda h: h.y)
        h = e.heads[0]                    # highest notehead = melody
        e.pitch = (step_of(h.y, h.x), note_acc.get(id(h)))

    # ---- ties: curves joining two heads on the same step
    notes = [e for e in events if e.kind == 'note']
    for (cx0, cy0, cx1, cy1) in curves:
        if cy1 - cy0 > sp * 2.4 or cx1 - cx0 < 4:
            continue
        cy = (cy0 + cy1) / 2
        left = [e for e in notes if -4 < cx0 - e.heads[0].x < 14 and abs(e.heads[0].y - cy) < sp * 1.6]
        right = [e for e in notes if -6 < e.heads[0].x - cx1 < 6 and abs(e.heads[0].y - cy) < sp * 1.6]
        if left:
            a = min(left, key=lambda e: abs(cx0 - e.heads[0].x - 6))
            if right:
                b = min(right, key=lambda e: abs(e.heads[0].x - cx1))
                if b is not a and b.pitch[0] == a.pitch[0]:
                    a.tie_start = True; b.tie_end = True
            elif cx1 > st['x1'] - 14 and a is notes[-1] if notes else False:
                a.tie_start = True              # continues on the next system
        elif right and cx0 < st['x0'] + 90:
            b = min(right, key=lambda e: e.heads[0].x)
            if b is notes[0]:
                b.tie_end = True

    # ---- tuplets: a small "3" over a bracket or a beam
    tup_marks = [t for t in texts if t.text in ('3', '6', '5') and t.size < PROFILE['tuplet_max'] and top - sp * 9 < t.y < bot + sp * 9 and t.x > st['x0'] + 20]
    for t in tup_marks:
        n = int(t.text)
        tx = (t.x + t.x1) / 2
        span = None
        brk = [h for h in hl if abs(h[2] - (t.y - 3)) < 7 and h[1] - h[0] < 200 and (h[0] <= tx + 2 or h[1] >= tx - 2)]
        lefts = [h for h in brk if h[1] <= tx + 1 and tx - h[1] < 30]
        rights = [h for h in brk if h[0] >= tx - 1 and h[0] - tx < 30]
        if lefts and rights:
            span = (min(h[0] for h in lefts) - 1.5, max(h[1] for h in rights) + 1.5)
        else:
            bm = [b for b in fills if b['x0'] - 2 <= tx <= b['x1'] + 2 and abs((b['yl'] + b['yr']) / 2 - t.y) < sp * 3.5]
            if bm:
                b = min(bm, key=lambda b: abs((b['x0'] + b['x1']) / 2 - tx))     # the number is centred on its group
                span = (b['x0'] - 1.5, b['x1'] + 1.5)
        if span:
            grp = [e for e in events if span[0] <= (e.stem[0] if e.stem else e.x + 3.3) <= span[1]]
        else:
            grp = sorted(events, key=lambda e: abs(e.x - tx))[:3]
            grp.sort(key=lambda e: e.x)
        if grp:
            for e in grp:
                if e.tuplet is None:
                    e.tuplet = n
                    e.dur = e.dur * (2 if n == 3 else 4) // n

    return {'events': events, 'bars': groups, 'keys': keys, 'times': times, 'clefs': clefs, 'note_acc': note_acc}


# --------------------------------------------------------------------------------------------- whole book
def classify_text(t):
    s = t.text.strip()
    return s


def song_pages(doc, first, last):
    """Yield (pageno, page data) with staves and titles."""
    for pn in range(first, last):
        page = doc[pn]
        glyphs, texts, hl, vl, fills, curves = page_items(page)
        staves = find_staves(hl)
        yield pn, page, glyphs, texts, hl, vl, fills, curves, staves


def build(pdf, first, last, debug=False, only_pages=None):
    doc = fitz.open(pdf)
    last = min(last or len(doc), len(doc))
    songs, cur = [], None
    pages = only_pages or range(first, last)
    for pn in pages:
        page = doc[pn]
        glyphs, texts, hl, vl, fills, curves = page_items(page)
        staves = find_staves(hl)
        if not staves:
            continue
        # titles: the biggest plain text above the first staff
        tops = [t for t in texts if t.size >= 17 and t.y < staves[0]['top'] - 10 and
                (not PROFILE['title_font'] or (PROFILE['title_font'] in t.font and t.size > 30))]
        title_y = None
        if tops:
            ty = min(t.y for t in tops)
            row = sorted([t for t in tops if abs(t.y - ty) < 3], key=lambda t: t.x)
            title = ' '.join(t.text for t in row)
            title_y = ty
            # composer: right-aligned text on the lines just above the first staff
            comp = [t for t in texts if t.size < 17 and ty < t.y < staves[0]['top'] - 20 and t.x > page.rect.width * 0.55]
            comp_rows = defaultdict(list)
            for t in comp:
                comp_rows[round(t.y)].append(t)
            composer = None
            if comp_rows:
                row = sorted(comp_rows[max(comp_rows)], key=lambda t: t.x)
                composer = ' '.join(t.text for t in row)
            cur = {'title': title, 'composer': composer, 'page': pn + 1, 'pages': [pn + 1], 'systems': []}
            songs.append(cur)
        elif cur is not None:
            cur['pages'].append(pn + 1)
        else:
            continue
        for k, st in enumerate(staves):
            msgs = []
            sg = [g for g in glyphs if staff_of(staves, g.y) == k]
            stx = [t for t in texts if staff_of(staves, t.y, above=7, below=6) == k]
            a = analyse_staff(st, sg, stx, vl, fills, curves, hl, msgs.append)
            # text above the staff: chords, endings, navigation; left margin: measure number
            above = [t for t in stx if st['top'] - st['sp'] * 9 < t.y < st['top'] - 1]
            meas_no = [t for t in stx if t.x < st['x0'] + 12 and t.text.isdigit() and st['top'] - 30 < t.y < st['top'] + 2]
            chords, other = [], []
            for t in above:
                if PROFILE['chord_font'] and PROFILE['chord_font'] not in t.font:
                    other.append(t); continue
                c = parse_chord(chord_text(t.text))
                if c and t.size >= 9:
                    c['x'] = t.x
                    chords.append(c)
                else:
                    other.append(t)
            # volta brackets: horizontal 0.72 lines above the staff with "1." / "2." text
            voltas = []
            for t in other:
                m = re.fullmatch(r'(\d)(?:[.,]\s*(\d)\.?)?\.?', t.text)
                if m:
                    lines = [h for h in hl if h[3] >= 0.6 and abs(h[2] - (t.y - 8)) < 9 and h[0] - 8 <= t.x <= h[1]]
                    if lines:
                        h = max(lines, key=lambda h: h[1] - h[0])
                        nums = [int(m.group(1))] + ([int(m.group(2))] if m.group(2) else [])
                        voltas.append({'x0': h[0], 'x1': h[1], 'nums': nums})
            nav = [t.text for t in other if re.search(r'D\.\s*[SC]\.|Coda|CODA|Fine|FINE', t.text)]
            navtext = ' '.join(t.text for t in sorted(other, key=lambda t: (round(t.y), t.x)))
            signs = [('segno', g.x) for g in sg if g.code == SEGNO] + [('coda', g.x) for g in sg if g.code == CODA]
            marks = [(t.text, t.x) for t in other] + [(t.text, t.x) for t in stx if st['bot'] + 4 < t.y < st['bot'] + 40 and
                                                       re.search(r'^(D\.|Fine|FINE|al|Coda|CODA)', t.text)]
            cur['systems'].append({'page': pn + 1, 'staff': st, 'a': a, 'chords': chords, 'voltas': voltas, 'marks': marks,
                                   'navtext': navtext, 'signs': signs, 'measure_no': int(meas_no[0].text) if meas_no else None,
                                   'warn': msgs})
    return songs


def measures_of(song, warns):
    """Cut systems into bars, keep accidentals, ties, chords, voltas and repeats."""
    out = []
    key = 0
    time = (4, 4)
    pending_tie = None
    first_bar = True
    for si, sys_ in enumerate(song['systems']):
        st, a = sys_['staff'], sys_['a']
        bars = a['bars']
        edges = [st['x0']] + [b['x1'] for b in bars]
        # make sure the last bar is closed by the staff end
        if not bars or bars[-1]['x1'] < st['x1'] - 10:
            edges.append(st['x1'] + 5)
        mno = sys_['measure_no']
        if mno is not None and out:
            numbered = sum(1 for m in out if m['numbered'])
            if numbered + (1 if out and not out[0]['numbered'] else 0) != mno - 1 + (1 if out and not out[0]['numbered'] else 0):
                pass
        sys_first = len(out)
        for bi in range(len(edges) - 1):
            x0, x1 = edges[bi], edges[bi + 1]
            evs = [e for e in a['events'] if x0 < e.x < x1]
            for kx, f in a['keys']:
                if kx < x1 and (kx >= x0 or bi == 0):
                    if not evs or kx < evs[0].x:
                        key = f
            for tx, n, d in a['times']:
                if x0 - 2 <= tx < x1:
                    time = (n, d)
            if not evs:
                continue
            bar_len = time[0] * TPQ * 4 // time[1]
            left = bars[bi - 1] if bi > 0 else None
            right = bars[bi] if bi < len(bars) else None
            # a repeat-start group sits at the staff's first barline; voltas by x overlap
            m = {'notes': [], 'chords': [], 'key': key, 'time': list(time), 'len': bar_len,
                 'rep_start': bool(left and left['rep_start']) or bool(right is None and False),
                 'rep_end': bool(right and right['rep_end']),
                 'double': bool(right and (right['double'] or right['thick'])),
                 'final': bool(right and right['thick'] and not right['rep_end'] and bi == len(edges) - 2 and si == len(song['systems']) - 1),
                 'ending': None, 'numbered': True, 'page': sys_['page'], 'system': si,
                 'box': [sys_['page'], round(x0, 1), round(st['top'], 1), round(x1, 1), round(st['bot'], 1)]}
            if bi == 0 and bars and bars[0]['x1'] - st['x0'] < 60 and bars[0]['rep_start']:
                m['rep_start'] = True
            for v in sys_['voltas']:
                if v['x0'] - 4 < (x0 + x1) / 2 < v['x1'] + 4 or (v['x0'] - 6 <= x0 + 6 and v['x1'] >= x0 + (x1 - x0) * 0.5):
                    m['ending'] = v['nums']
            notes, t, pending_tie = bar_melody(evs, key, bar_len, pending_tie, sys_['staff'])
            m['notes'] = notes
            # navigation: segno / coda signs and D.S. / D.C. / Fine words that fall in this bar
            for kind, gx in sys_['signs']:
                if x0 - 6 < gx <= x1 - 2:
                    m.setdefault('signs', []).append(kind)
            words = ' '.join(w for w, wx in sorted(sys_['marks'], key=lambda w: w[1]) if x0 - 6 < wx <= x1)
            if re.search(r'D\.\s*S', words):
                m['ds'] = 'fine' if re.search(r'fine', words, re.I) else 'coda' if re.search(r'coda', words, re.I) else 'end'
            elif re.search(r'D\.\s*C', words):
                m['dc'] = 'fine' if re.search(r'fine', words, re.I) else 'coda' if re.search(r'coda', words, re.I) else 'end'
            if re.search(r'(^|\s)(Fine|FINE)(\s|$)', words) and not re.search(r'al\s+(Fine|FINE)', words):
                m['fine'] = True
            # chords: onset of the event under the chord, else proportional
            raw = []
            for c in sys_['chords']:
                if x0 < chord_anchor(c, a['events']) <= x1:
                    raw.append((chord_onset(chord_anchor(c, a['events']), evs, x0, x1, bar_len, time), c))
            raw.sort(key=lambda r: r[0])
            beat = TPQ * 4 // time[1]
            for on, c in raw:                    # on a beat; on the 8th between when two would share a beat
                ct = min(bar_len - beat, int(round(on / beat)) * beat)
                if m['chords'] and ct <= m['chords'][-1]['t']:
                    ct = int(round(on / (beat // 2))) * (beat // 2)
                    if ct <= m['chords'][-1]['t']:
                        ct = m['chords'][-1]['t'] + beat // 2
                if ct >= bar_len:
                    continue
                m['chords'].append({'t': ct, 'sym': c['sym'], 'ireal': c['ireal']})
            m['filled'] = t
            out.append(m)
        # measure-number check against the printed number for this system
        if mno is not None:
            sys_['first_index'] = sys_first
    return out


def seq_onsets(seq, bar_len):
    t = 0
    for e in seq:
        e.onset = t
        t += bar_len if e.whole_rest else e.dur
    return t


def voice_of(e, evs, mid):
    if e.kind == 'rest':
        return 1 if e.heads[0].y <= mid else 2
    if e.stem:
        return 1 if e.stem[3] == 'up' else 2
    others = [o for o in evs if o is not e and o.kind == 'note' and abs(o.x - e.x) < 3]
    return 1 if all(e.heads[0].y <= o.heads[0].y for o in others) else 2


def bar_melody(evs, key, bar_len, pending_tie, st):
    """The top line of one bar: (notes, ticks filled, tie carried to the next bar)."""
    if any(e.kind == 'note' for e in evs) and any(getattr(e, 'whole_rest', False) for e in evs):
        evs = [e for e in evs if not getattr(e, 'whole_rest', False)]   # a bar rest in an upper voice over notes
    alter = {}
    for e in evs:                                   # accidentals hold to the bar line, in every voice
        if e.kind != 'note':
            continue
        step, acc = e.pitch
        if acc is not None:
            alter[step] = ACC_ALTER[acc.code]
        e.midi_alter = alter[step] if step in alter else keysig_alter(key, step)
        e.midi_acc = acc is not None
    seq = list(evs)
    total = seq_onsets(seq, bar_len)
    multi = any(abs(x.x - y.x) < 3 and (x.kind == 'note' or y.kind == 'note') for i, x in enumerate(evs) for y in evs[i + 1:])
    if multi:
        mid = (st['top'] + st['bot']) / 2
        v1 = [e for e in evs if voice_of(e, evs, mid) == 1]
        v2 = [e for e in evs if voice_of(e, evs, mid) == 2]
        t1, t2 = seq_onsets(v1, bar_len), seq_onsets(v2, bar_len)
        n1 = sum(1 for e in v1 if e.kind == 'note')
        n2 = sum(1 for e in v2 if e.kind == 'note')
        if n1 <= 1 and n2 >= 3:                  # a held note over a moving line: the line is the tune
            v1, v2, t1, t2 = v2, v1, t2, t1
        if v1 and t1 <= bar_len and t2 <= bar_len:
            # voice 1 is the tune; voice 2 sounds only where voice 1 has no note
            cover = [(e.onset, e.onset + (bar_len if e.whole_rest else e.dur)) for e in v1 if e.kind == 'note']
            fill = [e for e in v2 if e.kind == 'note' and not any(lo <= e.onset < hi for lo, hi in cover)]
            seq = sorted(v1 + fill, key=lambda e: (e.onset, e.kind != 'note'))
            total = max(t1, t2)
        else:
            seq_onsets(seq, bar_len)
    notes = []
    for e in seq:
        d = bar_len if e.whole_rest else e.dur
        if e.kind == 'note':
            step = e.pitch[0]
            al = e.midi_alter
            tie_cont = e.tie_end and pending_tie is not None and pending_tie[0] == step
            if tie_cont and not e.midi_acc:
                al = pending_tie[1]
            midi = 12 * (step // 7 + 1) + STEP_PC[step % 7] + al
            notes.append({'t': e.onset, 'd': d, 'p': midi, 'tie': tie_cont})
            pending_tie = (step, al) if e.tie_start else None
        else:
            notes.append({'t': e.onset, 'd': d, 'p': None})
            pending_tie = None
    # overlapping (fill) notes: cut each note at the next onset
    notes.sort(key=lambda n: n['t'])
    for a, b in zip(notes, notes[1:]):
        if a['t'] + a['d'] > b['t']:
            a['d'] = b['t'] - a['t']
    notes = [n for n in notes if n['d'] > 0]
    return notes, total, pending_tie


def keysig_alter(key, step):
    letter = step % 7                        # 0=C .. 6=B
    sharps = [3, 0, 4, 1, 5, 2, 6]           # F C G D A E B
    flats = [6, 2, 5, 1, 4, 0, 3]            # B E A D G C F
    if key > 0 and letter in sharps[:key]:
        return 1
    if key < 0 and letter in flats[:-key]:
        return -1
    return 0


def chord_anchor(c, events):
    """Finale sets a chord symbol a few points left of its note: the x of that note, else x + 6."""
    if 'anchor' not in c:
        near = [e for e in events if -3 <= e.x - c['x'] <= 14 and not e.whole_rest]   # a bar rest sits mid-bar
        c['anchor'] = min(near, key=lambda e: abs(e.x - c['x'] - 6)).x if near else c['x'] + 6
    return c['anchor']


def chord_onset(cx, evs, x0, x1, bar_len, time):
    beat = TPQ * 4 // time[1]
    best = None
    for e in evs:
        d = abs(e.x - cx)
        if d < 1 and not e.whole_rest and (best is None or d < best[0]):
            best = (d, e.onset)
    if best:
        on = best[1]
    elif all(e.whole_rest for e in evs):
        on = bar_len * (cx - x0) / max(1e-6, x1 - x0)
    else:
        # between two events: interpolate by x
        prev = [e for e in evs if e.x <= cx]
        nxt = [e for e in evs if e.x > cx]
        if prev and nxt:
            a, b = prev[-1], nxt[0]
            on = a.onset + (b.onset - a.onset) * (cx - a.x) / max(1e-6, b.x - a.x)
        elif nxt:
            on = 0
        elif prev:
            a = prev[-1]
            on = a.onset + a.dur * (cx - a.x) / max(1e-6, x1 - a.x)
        else:
            on = bar_len * (cx - x0) / max(1e-6, x1 - x0)
    return max(0.0, min(bar_len - 1.0, on))


def check(song, meas):
    problems = []
    for i, m in enumerate(meas):
        if m['filled'] != m['len']:
            if i == 0 and m['filled'] < m['len']:
                m['pickup'] = True
                continue
            if i == len(meas) - 1 and m['filled'] < m['len']:
                continue
            if m.get('ending') and m['filled'] < m['len']:
                continue
            problems.append(f"bar {i + 1} (p{m['page']}): {m['filled']} of {m['len']} ticks")
    # printed measure numbers
    n = 0
    pick = 1 if meas and meas[0].get('pickup') else 0
    for si, s in enumerate(song['systems']):
        if s.get('measure_no') is not None and 'first_index' in s:
            want = s['measure_no']
            have = s['first_index'] + 1 - pick
            if want != have:
                problems.append(f"system {si + 1} (p{s['page']}): starts at printed bar {want}, parsed bar {have}")
    return problems


def to_json(song, meas, problems, book):
    """Compact form for the web app. Per bar: c = chords [t, symbol, iReal token], n = notes flattened [t, d, p]
    (t, d in 48ths of a quarter note; p MIDI, +128 when it continues a tie, 0 for a rest), box = [page, x0, y0, x1, y1]
    in PDF points, and only the flags that are set."""
    bars = []
    for m in meas:
        o = {'c': [[c['t'], c['sym'], c['ireal']] for c in m['chords']], 'n': [], 'box': m['box']}
        for n in m['notes']:
            o['n'] += [n['t'], n['d'], 0 if n['p'] is None else n['p'] + (128 if n.get('tie') else 0)]
        if m['len'] != (meas[0]['len'] if meas else 192):
            o['len'] = m['len']
        for k, short in (('rep_start', 'rs'), ('rep_end', 're'), ('ending', 'e'), ('double', 'db'), ('signs', 'sg'),
                         ('ds', 'ds'), ('dc', 'dc'), ('fine', 'fn'), ('key', 'k'), ('time', 'ts')):
            v = m.get(k)
            if k in ('key', 'time'):
                prev = bars and meas[len(bars) - 1].get(k)
                if bars and v == prev:
                    continue
                if not bars:
                    continue
            if v:
                o[short] = 1 if v is True else v
        bars.append(o)
    return {
        'id': f"{book}-{song['page']}", 'book': book, 'title': song['title'], 'composer': song['composer'],
        'page': song['page'], 'pages': song['pages'],
        'key': meas[0]['key'] if meas else 0, 'time': meas[0]['time'] if meas else [4, 4],
        'pickup': bool(meas and meas[0].get('pickup')),
        'bars': bars, 'problems': problems,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--first', type=int, default=1, help='first page (1-based) with music')
    ap.add_argument('--last', type=int, default=0)
    ap.add_argument('--pages', type=str, help='comma list of 1-based pages to parse alone')
    ap.add_argument('--out')
    ap.add_argument('--book', default='462jazz', help='book id in books.json')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--font', choices=['maestro', 'inkpen'], default='maestro')
    ap.add_argument('--titles', help='JSON [[page, title], ...]: song titles by first page (when the title font is not text)')
    args = ap.parse_args()
    only = [int(p) - 1 for p in args.pages.split(',')] if args.pages else None
    if args.font == 'inkpen':
        PROFILE.update(stem_max=1.3, bar_min=1.4, thick=2.5, chord_font='Chords', ascii_chords=True, tuplet_max=13.5, title_font='Script', stem_tol=2.2)
    songs = build(args.pdf, args.first - 1, args.last, args.debug, only)
    if args.titles:                              # titles (and no composer) from a list by first page
        names = {int(p): t for p, t in json.load(open(args.titles))}
        for s in songs:
            s['title'] = names.get(s['page'], s['title']); s['composer'] = None
    res, clean = [], 0
    for s in songs:
        warns = []
        meas = measures_of(s, warns)
        probs = check(s, meas)
        if not probs:
            clean += 1
        res.append(to_json(s, meas, probs, args.book))
        if args.debug:
            L(f"== {s['title']} ({s['composer']}) p{s['pages']} bars={len(meas)} problems={len(probs)}")
            for p in probs[:12]:
                L('   ', p)
            for i, m in enumerate(meas):
                L(f"  {i + 1:3d} k{m['key']} {m['time']} {'|:' if m['rep_start'] else '  '}{':|' if m['rep_end'] else '  '} end={m['ending']} "
                  + ' '.join(c['sym'] + '@' + str(c['t'] // TPQ) for c in m['chords']) + ' :: '
                  + ' '.join(('r' if n['p'] is None else str(n['p'])) + ('~' if n.get('tie') else '') + '/' + str(n['d']) for n in m['notes']))
    L(f"{len(res)} songs, {clean} with every bar checked clean")
    if args.out:
        with open(args.out, 'w') as f:
            json.dump(res, f, separators=(',', ':'))


if __name__ == '__main__':
    main()
