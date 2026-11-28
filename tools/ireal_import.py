#!/usr/bin/env python3
"""ireal_import.py - turn iReal Pro playlists (irealb:// links, as shared on the iReal Pro forums) into chord charts
for the play-along (leads/<collection>.json, the same format as the charts read from the books, without a melody).

  python3 tools/ireal_import.py ireal/jazz1460.url --book ireal --out leads/ireal.json

Inputs: files holding irealb:// or irealbook:// links (one or more; an HTML page with links works too).
Each chart: the chords, where they fall in the bar, repeats, 1st/2nd endings, segno / coda / D.S. / D.C. / Fine,
time signature, key, and the alternative chords iReal prints small above the main ones (as the bars' `ca`).
Tempo and feel: the playlist's own tempo when it gives one, else the usual tempo for the tune from leads-tempos/
(matched by title), else one from the iReal style ("Medium Swing", "Bossa Nova", ...).
Charts in meters the band can't play (2/4, 5/4, 7/4, ...) are left out and listed.
"""
import argparse, glob, html, json, os, re, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPQ = 48
FEEL = {'swing': 0, 'ballad': 1, 'latin': 2, 'two': 3, 'shuffle': 5, 'samba': 6, 'even': 7}
# iReal style -> (feel, tempo)
STYLE = {
    'medium swing': ('swing', 140), 'ballad': ('ballad', 72), 'up tempo swing': ('swing', 230),
    'medium up swing': ('swing', 180), 'waltz': ('swing', 160), 'bossa nova': ('latin', 130), 'slow swing': ('swing', 100),
    'even 8ths': ('even', 120), 'latin': ('latin', 170), 'funk': ('even', 100), 'samba': ('samba', 200),
    'rock pop': ('even', 110), 'latin-swing': ('latin', 170), 'afro': ('latin', 160), 'medium slow': ('swing', 110),
    'slow rock': ('even', 76), 'even 16ths': ('even', 96), 'calypso': ('latin', 150), 'shuffle': ('shuffle', 120),
    'gypsy jazz': ('swing', 200), 'bebop': ('swing', 240), 'blues': ('shuffle', 110), 'jazz waltz': ('swing', 160),
}
PCS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
MAJOR_FIFTHS = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 5: -1, 10: -2, 3: -3, 8: -4, 1: -5}
METERS = {'44': [4, 4], '34': [3, 4], '22': [2, 2], '68': [6, 8], '12': [4, 4], '64': None, '24': None, '54': None,
          '74': None, '32': None, '58': None, '78': None, '98': None}


# ------------------------------------------------------------------------------------------- the link format
def unscramble(s):
    """iReal's obfuscation: each 50-character block (except a short last one) has some characters swapped end for end."""
    def block(b):
        c = list(b)
        for i in list(range(5)) + list(range(10, 24)):
            c[i], c[49 - i] = b[49 - i], b[i]
        return ''.join(c)
    out = ''
    while len(s) > 51:
        out += block(s[:50]); s = s[50:]
    return out + s


def songs_in(text):
    text = html.unescape(text)
    for link in re.findall(r'irealb(?:ook)?://[^"\'<>\s]+', text):
        body = urllib.parse.unquote(link.split('://', 1)[1])
        for raw in body.split('==='):
            f = raw.split('=')
            if len(f) >= 7 and f[6].startswith('1r34LbKcu7'):
                yield {'title': f[0], 'composer': f[1], 'style': f[3], 'key': f[4], 'music': unscramble(f[6][10:]),
                       'bpm': int(f[8]) if len(f) > 8 and f[8].isdigit() else 0}
            elif len(f) >= 6 and f[4] == 'n' and re.search(r'[|\[{]', f[5]):
                # the older irealbook:// format: title=composer=style=key=n=chords (not scrambled)
                yield {'title': f[0], 'composer': f[1], 'style': f[2], 'key': f[3], 'music': f[5], 'bpm': 0}


# ------------------------------------------------------------------------------------------- chords
CHORD_RE = re.compile(r'([A-GW][b#]?)((?:\^|-|\+|o|h|sus|alt|add|b|#|\d)*)(/[A-G][b#]?)?')


def engine_token(root, q):
    """the chord as the band engine reads it (songform.js parseToken): one of the qualities it voices"""
    if q.startswith('-'):
        t = '-^7' if '^' in q else 'h7' if 'b5' in q else '-6' if q in ('-', '-6', '-69', '-b6') else '-7'
    elif q.startswith('h'):
        t = 'h7'
    elif q.startswith('o'):
        t = 'o7'
    elif q.startswith('^'):
        t = '^7'
    elif re.match(r'(7|9|11|13)', q):
        t = '7alt' if 'alt' in q else '7b9' if 'b9' in q else '7'
    elif q.startswith('+'):
        t = '7' if '7' in q else '^7'
    elif q in ('', '6', '69', '2', 'add9', '5'):
        t = '6'
    elif q.startswith('sus'):
        t = '7'
    else:
        t = '^7'
    return root + t


def display(root, q, bass):
    """the chord as the charts print it: Cmaj7, Cm7, Cm7(b5), C7(b9), Cdim7, C/E"""
    d = q
    d = re.sub(r'^-\^', 'm(maj)', d)
    d = re.sub(r'^-', 'm', d)
    d = re.sub(r'^\^', 'maj', d)
    if d == 'maj':
        d = 'maj7'
    d = re.sub(r'^h7?', 'm7(b5)', d)
    d = re.sub(r'^o', 'dim', d)
    if d == 'dim':
        d = 'dim'
    m = re.match(r'^(m\(maj\)|m|maj|dim|\+)?(\d+|sus\d*)?((?:[b#]\d+|alt|sus\d*|add\d+)+)$', d)
    if m and m.group(2):
        d = (m.group(1) or '') + m.group(2) + '(' + m.group(3) + ')'
    d = re.sub(r'\(sus\)$', 'sus', d)
    d = re.sub(r'\(([^)]*?)sus(add3)?\)$', lambda m: 'sus' + (f'({m.group(1)})' if m.group(1) else '') , d)
    return root + d + (bass or '')


# ------------------------------------------------------------------------------------------- one chart
TOKEN_RE = re.compile(r'''
    (?P<time>T\d\d) | (?P<section>\*[A-Za-z]) | (?P<comment><[^>]*>) |
    (?P<alt>\((?:[A-GW][b#]?(?:\^|-|\+|o|h|sus|alt|add|b|\#|\d)*(?:/[A-G][b#]?)?)?\)) |
    (?P<chord>[A-GW][b#]?(?:\^|-|\+|o|h|sus|alt|add|b|\#|\d)*(?:/[A-G][b#]?)?) |
    (?P<ending>N[0-9]) | (?P<bar>[|\[\]{}Z]) | (?P<cell>[ pxnr]) | (?P<mark>[SQfsluY,U]) | (?P<other>.)
''', re.X)


def parse_chart(music):
    """-> (bars, meter, problems). bars: [{'cells': [...], 'flags'...}] before timing"""
    music = music.replace('XyQ', '   ').replace('LZ', ' |').replace('Kcl', '| x')
    bars, cur, meter, problems = [], None, None, []
    meters = set()
    pending = {}                                              # flags for the next bar (repeat start, ending, section)
    ending_left = 0

    def new_bar():
        return {'cells': [], 'flags': {}}

    cur = new_bar()
    outside = False                                           # after } ] Z: blank layout cells until a bar line opens a bar

    jump = []                                                  # a D.S. / D.C.: taken at the next section's end

    def close(kind):
        nonlocal cur, outside
        outside = kind in ('}', ']', 'Z')
        if jump and kind in ('}', ']', 'Z') and (cur['cells'] or cur['flags'].get('content') or bars):
            (cur if cur['cells'] or cur['flags'].get('content') else bars[-1])['flags'][jump[0]] = jump[1]
            jump.clear()
        if cur['cells'] or cur['flags'].get('content'):
            if kind in ('}',):
                cur['flags']['re'] = True
            if kind in (']', 'Z', '['):
                cur['flags']['db'] = True
            bars.append(cur)
        elif kind == '}' and bars:
            bars[-1]['flags']['re'] = True
        elif kind in (']', 'Z', '[') and bars:
            bars[-1]['flags']['db'] = True
        cur = new_bar()
        cur['flags'].update(pending); pending.clear()

    for m in TOKEN_RE.finditer(music):
        k, v = m.lastgroup, m.group()
        if k == 'time':
            meters.add(v[1:])
            if meter is None:
                meter = v[1:]
        elif k == 'section':
            cur['flags']['section'] = v[1]
        elif k == 'comment':
            c = v[1:-1].lower()
            c = re.sub(r'^\*\d+', '', c)
            if 'd.s.' in c or 'd.c.' in c:
                mode = 'coda' if 'coda' in c else 'fine' if 'fine' in c else 'ending' if 'end' in c and re.search(r'\d|ending', c) else 'end'
                jump[:] = ['ds' if 'd.s.' in c else 'dc', mode]
            elif c.strip() == 'fine' or c.startswith('fine'):
                tgt = bars[-1] if outside and bars else cur
                tgt['flags']['fn'] = True
        elif k == 'alt':
            if cur['cells'] and cur['cells'][-1][0] == 'chord':
                cur['cells'][-1] = cur['cells'][-1] + (v[1:-1],)
        elif k == 'chord':
            outside = False
            cur['cells'].append(('chord', v))
            cur['flags']['content'] = True
        elif k == 'cell':
            if outside:
                continue
            cur['cells'].append(('cell', v))
            if v in 'xnr':
                cur['flags']['content'] = True
        elif k == 'ending':
            cur['flags']['e'] = int(v[1])
        elif k == 'mark':
            if v == 'S':
                cur['flags'].setdefault('sg', []).append('segno')
            elif v == 'Q':
                tgt = bars[-1] if outside and bars else cur
                tgt['flags'].setdefault('sg', []).append('coda')
        elif k == 'bar':
            if outside and v in '|[{':                         # the bar line that opens the next bar
                outside = False
                if v == '{':
                    cur['flags']['rs'] = True
                continue
            if v == '{':
                close('{'); cur['flags']['rs'] = True
            elif v == '[':
                close('[')
            else:
                close(v)
    close('Z')
    if jump and bars:
        bars[-1]['flags'][jump[0]] = jump[1]
    return bars, meters, meter, problems


def timed(bars, beats):
    """cells -> chords on beats (ticks), x / r repeats expanded"""
    out = []
    for b in bars:
        f = b['flags']
        cells = [c for c in b['cells']]
        kinds = [c[1] for c in cells if c[0] == 'cell' and c[1] in 'xr']
        if 'r' in kinds and len(out) >= 2:                   # repeat the last two bars
            for prev in out[-2:]:
                out.append({'c': [list(x) for x in prev['c']], 'ca': [list(x) for x in prev.get('ca', [])], 'f': {}})
            continue
        if 'x' in kinds and out:                             # repeat the last bar
            prev = out[-1]
            out.append({'c': [list(x) for x in prev['c']], 'ca': [list(x) for x in prev.get('ca', [])], 'f': f})
            continue
        # trailing empty cells don't count when they only pad a bar with a lone chord
        n = len(cells)
        c, ca = [], []
        if n:
            for i, cell in enumerate(cells):
                if cell[0] != 'chord':
                    continue
                beat = min(beats - 1, i * beats // n) if n > 1 else 0
                c.append((beat * TPQ, cell[1]))
                if len(cell) > 2 and cell[2]:
                    ca.append((beat * TPQ, cell[2]))
        out.append({'c': c, 'ca': ca, 'f': f})
    return out


def chord_entry(t, sym, prev_root):
    m = CHORD_RE.fullmatch(sym)
    if not m:
        return None, prev_root
    root, q, bass = m.groups()
    if root == 'W':                                          # a bass change under the chord before
        if not prev_root:
            return None, prev_root
        root, q = prev_root
    return [t, display(root, q, bass), engine_token(root, q)], (root, q)


def fix_jumps(bars):
    """iReal habits the form unroller (songform.js) reads differently"""
    jat = next((i for i, b in enumerate(bars) if b.get('ds') or b.get('dc')), -1)
    if jat < 0:
        return
    key = 'ds' if bars[jat].get('ds') else 'dc'

    def group_start(i):                                      # first bar of the run of endings bar i is in
        while i > 0 and bars[i - 1].get('e'):
            i -= 1
        return i

    # "D.C. al 3rd ending" with that ending after the jump: play to the bar before the endings, then go to it
    if bars[jat][key] == 'ending':
        after = [i for i in range(jat + 1, len(bars)) if bars[i].get('e')]
        before = [i for i in range(jat) if bars[i].get('e')]
        if after and before:
            to = group_start(before[0] if key == 'dc' else next((i for i in before if i >= next((k for k, b in enumerate(bars) if 'segno' in b.get('sg', [])), 0)), before[0])) - 1
            if to >= 0:
                bars[to].setdefault('sg', []).append('coda')
                bars[after[0]].setdefault('sg', []).append('coda')
                for i in after:
                    bars[i].pop('e', None)
                bars[jat][key] = 'coda'
                return
        bars[jat][key] = 'end'
    # "to Coda" written on a 1st-ending bar: after the jump the endings are skipped, so leave from the bar before them
    if bars[jat][key] == 'coda':
        for i in range(jat):
            if 'coda' in bars[i].get('sg', []) and bars[i].get('e'):
                s = group_start(i) - 1
                bars[i]['sg'].remove('coda')
                if not bars[i]['sg']:
                    del bars[i]['sg']
                if s >= 0:
                    bars[s].setdefault('sg', []).append('coda')
                break


def key_fifths(k):
    m = re.match(r'([A-G])([b#]?)(-?)', k or 'C')
    if not m:
        return 0
    pc = (PCS[m.group(1)] + {'b': -1, '#': 1, '': 0}[m.group(2)]) % 12
    if m.group(3):
        pc = (pc + 3) % 12                                   # a minor key: its relative major's signature
    return MAJOR_FIFTHS[pc]


def style_of(style):
    """(feel, tempo) for an iReal style name, the many forum variants included ("Gypsy Bossa", "Dixieland", ...)"""
    s = style.lower().strip()
    if s in STYLE:
        return STYLE[s]
    has = lambda *w: any(x in s for x in w)
    if has('samba'): return ('samba', 200)
    if has('bossa'): return ('latin', 130)
    if has('ballad', 'slow'): return ('ballad', 72) if not has('swing', 'waltz') else ('swing', 100) if has('swing') else ('ballad', 92)
    if has('waltz', 'valse'): return ('swing', 170)
    if has('dixie', 'trad', 'two-beat', 'two beat', '2 beat', 'march', 'ragtime', 'rag', 'new orleans', 'stomp'): return ('two', 180)
    if has('latin', 'afro', 'calypso', 'rumba', 'mambo', 'bolero', 'tango', 'cha', 'cuban', 'son', 'salsa', 'reggae', 'ska'): return ('latin', 160)
    if has('shuffle', 'blues'): return ('shuffle', 120)
    if has('rock', 'pop', 'funk', 'hip', 'r&b', 'soul', 'fusion', 'groove', '8th', '16th', 'even', 'straight', 'gospel'): return ('even', 110)
    if has('up', 'fast', 'bright', 'gypsy fire'): return ('swing', 230)
    if has('gypsy', 'manouche', 'django'): return ('swing', 190)
    if has('medium', 'swing'): return ('swing', 150)
    return ('swing', 140)


def nice_title(t):
    t = re.sub(r'\s*\((?:dixieland|trad)[^)]*\)$', '', t.strip(), flags=re.I)
    t = re.sub(r'\s+-\s+[a-z]{1,3}$|\s{2,}[A-Z]{2,4}$', '', t.strip())                      # a poster's initials
    t = re.sub(r'\s+\((?:[A-G][b#]?-?|w(?:ith)? intro)\)$|\s+\d$', '', t.strip())   # "(G)": the key; "Avalon 1"
    m = re.match(r'^(.*), (The|A|An)$', t)
    return f'{m.group(2)} {m.group(1)}' if m else t


def nice_composer(c):
    c = c.strip()
    parts = c.split(' ')
    if len(parts) == 2 and '-' not in c:                     # iReal writes "Last First"
        return f'{parts[1]} {parts[0]}'
    return c or None


def norm(t):
    t = str(t or '').lower()
    t = re.sub(r'\(.*?\)', ' ', t); t = re.sub(r'^the\s+|,\s*the$', ' ', t)
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()


def convert(song, idx, book, tempos, swing_feel='swing'):
    raw, meters, meter, _ = parse_chart(song['music'])
    meter = meter or '44'
    if any(METERS.get(x) is None for x in meters | {meter}):
        return None, 'meter ' + '/'.join(sorted(meters))
    if len({tuple(METERS[x]) for x in meters | {meter}}) > 1:
        return None, 'changes meter'
    time = METERS[meter]
    beats = 3 if time in ([3, 4], [6, 8]) else 4
    tb = timed(raw, beats)
    bars, prev = [], None
    ending = None
    for i, b in enumerate(tb):
        f = b['f']
        c = []
        for t, sym in b['c']:
            e, prev = chord_entry(t, sym, prev)
            if e:
                c.append(e)
        bar = {'c': c, 'n': []}
        if b['ca']:
            ca, p2 = [], prev
            alts = dict(b['ca'])
            for e in c:
                if e[0] in alts:
                    a, p2 = chord_entry(e[0], alts[e[0]], p2)
                    ca.append(a or e)
                else:
                    ca.append(e)
            bar['ca'] = ca
        if f.get('rs'): bar['rs'] = True
        if f.get('re'): bar['re'] = True
        if f.get('db'): bar['db'] = True
        if f.get('sg'): bar['sg'] = sorted(set(f['sg']))
        if f.get('ds'): bar['ds'] = f['ds']
        if f.get('dc'): bar['dc'] = f['dc']
        if f.get('fn'): bar['fn'] = True
        if f.get('e'):
            ending = [f['e'], 0]
        if ending:
            bar['e'] = [ending[0]]
            ending[1] += 1
            if bar.get('re') or bar.get('db') or (ending[0] > 1 and ending[1] >= 8):
                ending = None
        bars.append(bar)
    # a 1st/2nd ending pair: the 2nd runs no longer than the 1st
    i = 0
    while i < len(bars):
        if bars[i].get('e') == [1]:
            j = i
            while j < len(bars) and bars[j].get('e') == [1]: j += 1
            n1, k = j - i, j
            while k < len(bars) and bars[k].get('e') and bars[k]['e'][0] > 1: k += 1
            for x in range(j + n1, k): bars[x].pop('e', None)
            i = k
        else:
            i += 1
    fix_jumps(bars)
    if not bars or not any(b['c'] for b in bars):
        return None, 'no chords'
    title = nice_title(song['title'])
    st = style_of(song['style'])
    feel, tempo = st
    known = tempos.get(norm(title))
    if song['bpm'] > 0 and not (feel != 'ballad' and song['bpm'] < 80 and not re.search(r'slow|ballad', song['style'], re.I)):
        tempo = song['bpm']
    elif known:
        tempo = known['tempo']
        if known['feel'] != 'swing' or feel == 'swing':
            feel = known['feel'] if feel not in ('latin', 'samba', 'even') else feel
    if feel == 'swing':
        feel = swing_feel
    out = {'id': f'{book}-{idx}', 'book': book, 'title': title, 'composer': nice_composer(song['composer']),
           'page': 0, 'pages': [], 'key': key_fifths(song['key']), 'time': time, 'pickup': False,
           'bars': bars, 'problems': [], 'chordsOnly': True, 'style': song['style'],
           'tempo': tempo, 'feel': FEEL[feel]}
    return out, None


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+')
    ap.add_argument('--book', default='ireal')
    ap.add_argument('--out', required=True)
    ap.add_argument('--swing-feel', default='swing', choices=['swing', 'two'], help="'two' for a two-beat collection (Dixieland)")
    a = ap.parse_args()
    tempos = {}
    for f in glob.glob(os.path.join(ROOT, 'leads-tempos', '*.json')):
        for v in json.load(open(f)).values():
            if v.get('title'):
                tempos.setdefault(norm(v['title']), v)
    songs, skipped, seen = [], [], set()
    for path in a.inputs:
        for s in songs_in(open(path, encoding='utf-8', errors='replace').read()):
            key = re.sub(r'\s+\d$', '', norm(s['title']))
            if key in seen:
                continue
            seen.add(key)
            out, why = convert(s, len(songs) + len(skipped) + 1, a.book, tempos, a.swing_feel)
            (songs.append(out) if out else skipped.append((s['title'], why)))
    json.dump(songs, open(a.out, 'w'), separators=(',', ':'), ensure_ascii=False)
    print(f'{len(songs)} charts written to {a.out}; {len(skipped)} left out')
    for t, why in skipped:
        print(f'  left out: {t} ({why})')
