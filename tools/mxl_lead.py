#!/usr/bin/env python3
"""mxl_lead.py - MusicXML (from Audiveris) -> the play-along lead-sheet format (same as vector_lead.py), with checks.

Keeps the top line (voice 1, the highest note of a chord), chord symbols (<harmony>) at their offsets, key, time,
repeats and endings. Each bar is checked: the melody must fill the time signature (a pickup and the last bar may be
short). Used for the scanned books, where recognition is not exact, so the check decides what is offered to play.

  python3 tools/mxl_lead.py page.xml [more.xml ...] --title "All Or Nothing At All" --page 41
"""
import argparse, json, re, sys, zipfile
import xml.etree.ElementTree as ET

TPQ = 48
STEP_PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
KIND = {  # MusicXML kind -> (symbol suffix, iReal token suffix)
    'major': ('', '6'), 'minor': ('m', '-'), 'augmented': ('+', '7'), 'diminished': ('°', 'o7'),
    'dominant': ('7', '7'), 'major-seventh': ('maj7', '^7'), 'minor-seventh': ('m7', '-7'),
    'diminished-seventh': ('°7', 'o7'), 'augmented-seventh': ('+7', '7'), 'half-diminished': ('m7(b5)', 'h7'),
    'major-minor': ('m(maj7)', '-^7'), 'major-sixth': ('6', '6'), 'minor-sixth': ('m6', '-6'), 'dominant-ninth': ('9', '7'),
    'major-ninth': ('maj9', '^7'), 'minor-ninth': ('m9', '-7'), 'dominant-11th': ('11', '7'), 'major-11th': ('maj11', '^7'),
    'minor-11th': ('m11', '-7'), 'dominant-13th': ('13', '7'), 'major-13th': ('maj13', '^7'), 'minor-13th': ('m13', '-7'),
    'suspended-second': ('sus2', '7'), 'suspended-fourth': ('sus4', '7'), 'power': ('5', '6'),
}


def load(path):
    if path.endswith('.mxl'):
        z = zipfile.ZipFile(path)
        name = [n for n in z.namelist() if n.endswith('.xml') and not n.startswith('META-INF')][0]
        return ET.fromstring(z.read(name))
    return ET.parse(path).getroot()


def alter_txt(a):
    return {'-1': 'b', '1': '#', '-2': 'bb', '2': '##'}.get(str(int(float(a))) if a else '', '')


def harmony(h):
    step = h.findtext('root/root-step')
    if not step:
        return None
    root = step + alter_txt(h.findtext('root/root-alter'))
    k = h.find('kind')
    kind = (k.text or 'major') if k is not None else 'major'
    sym, tok = KIND.get(kind, ('', '6'))
    text = k.get('text') if k is not None else None
    degrees = ''
    for d in h.findall('degree'):
        v, a, t = d.findtext('degree-value'), d.findtext('degree-alter'), d.findtext('degree-type')
        if v and t in ('add', 'alter'):
            degrees += '(' + alter_txt(a) + v + ')'
            if v == '9' and a and float(a) < 0 and tok == '7':
                tok = '7b9'
    bass = h.findtext('bass/bass-step')
    s = root + (text if text is not None and text != '' else sym) + degrees
    if bass:
        s += '/' + bass + alter_txt(h.findtext('bass/bass-alter'))
    return s, root + tok


def measures(root):
    part = root.find('part')
    if part is None:
        return []
    out, div, key, time = [], 1, 0, (4, 4)
    pending_newsys = False
    for m in part.findall('measure'):
        pr = m.find('print')
        newsys = (pr is not None and pr.get('new-system') == 'yes') or pending_newsys
        pending_newsys = False
        pos, events, harms = 0, [], []
        rs = re_ = False; ending = None
        for e in m:
            if e.tag == 'attributes':
                div = int(e.findtext('divisions') or div)
                if e.find('key/fifths') is not None:
                    key = int(e.findtext('key/fifths'))
                if e.find('time/beats') is not None:
                    try:
                        time = (int(e.findtext('time/beats')), int(e.findtext('time/beat-type')))
                    except ValueError:
                        pass
            elif e.tag == 'harmony':
                h = harmony(e)
                if h:
                    off = int(e.findtext('offset') or 0)
                    harms.append((pos + off, h))
            elif e.tag == 'backup':
                pos -= int(e.findtext('duration') or 0)
            elif e.tag == 'forward':
                pos += int(e.findtext('duration') or 0)
            elif e.tag == 'note':
                if e.find('grace') is not None:
                    continue
                d = int(e.findtext('duration') or 0)
                voice = e.findtext('voice') or '1'
                chord = e.find('chord') is not None
                p = e.find('pitch')
                midi = None
                if p is not None:
                    midi = 12 * (int(p.findtext('octave')) + 1) + STEP_PC[p.findtext('step')] + int(float(p.findtext('alter') or 0))
                ties = [t.get('type') for t in e.findall('tie')]
                start = pos - (events[-1][1] if chord and events else 0) if chord else pos
                if chord and events:
                    start = events[-1][0]
                events.append((start, d, midi, voice, chord, 'stop' in ties))
                if not chord:
                    pos += d
            elif e.tag == 'barline':
                rep = e.find('repeat')
                if rep is not None:
                    if rep.get('direction') == 'forward':
                        rs = True
                    else:
                        re_ = True
                en = e.find('ending')
                if en is not None and en.get('type') == 'start':
                    ending = [int(x) for x in re.findall(r'\d+', en.get('number') or '')] or None
        bar_len = time[0] * TPQ * 4 // time[1]
        q = lambda x: round(x * TPQ / div)
        if not events and not harms:                     # homr adds an empty bar at some repeat signs: not a bar
            if newsys and out is not None:
                pending_newsys = True
            continue
        # top line: voice 1; a chord keeps its highest note
        top = {}
        for (st, d, midi, voice, chord, tie) in events:
            if voice != '1':
                continue
            if st not in top or (midi is not None and (top[st][1] is None or midi > top[st][1])):
                top[st] = (d, midi, tie)
        notes, filled = [], 0
        for st in sorted(top):
            d, midi, tie = top[st]
            notes += [q(st), q(d), 0 if midi is None else midi + (128 if tie else 0)]
            filled = max(filled, q(st) + q(d))
        chords = []
        for st, (sym, tok) in sorted(harms):
            t = min(bar_len - 1, max(0, q(st)))
            beat = TPQ * 4 // time[1]
            t = round(t / beat) * beat
            if chords and t <= chords[-1][0]:
                t = chords[-1][0] + beat // 2
            if t < bar_len:
                chords.append([t, sym, tok])
        out.append({'c': chords, 'n': notes, 'key': key, 'time': list(time), 'len': bar_len, 'filled': filled,
                    'rs': rs, 're': re_, 'e': ending, 'newsys': newsys})
    return out


def to_song(meas, title, composer, book, page, pages):
    probs = []
    pickup = bool(meas) and 0 < meas[0]['filled'] < meas[0]['len']
    for i, m in enumerate(meas):
        if m['filled'] != m['len'] and not (i == 0 and pickup) and not (i == len(meas) - 1 and m['filled'] < m['len']):
            probs.append(f"bar {i + 1}: {m['filled']} of {m['len']} ticks")
        if not any(m['n'][k + 2] for k in range(0, len(m['n']), 3)) and m['filled'] == 0:
            probs.append(f"bar {i + 1}: empty")
    bars = []
    for i, m in enumerate(meas):
        o = {'c': m['c'], 'n': m['n'], 'box': None}
        for k in ('rs', 're', 'e'):
            if m[k]:
                o[k] = 1 if m[k] is True else m[k]
        if i and m['key'] != meas[i - 1]['key']:
            o['k'] = m['key']
        bars.append(o)
    nchords = sum(len(m['c']) for m in meas)
    if meas and nchords < len(meas) / 4:
        probs.append(f'only {nchords} chord symbols read in {len(meas)} bars')
    return {'id': f'{book}-{page}', 'book': book, 'title': title, 'composer': composer, 'page': page, 'pages': pages,
            'key': meas[0]['key'] if meas else 0, 'time': meas[0]['time'] if meas else [4, 4], 'pickup': pickup,
            'bars': bars, 'problems': probs, 'omr': True}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--title', default='?'); ap.add_argument('--composer'); ap.add_argument('--book', default='Jazz-Fakebook')
    ap.add_argument('--page', type=int, default=0)
    a = ap.parse_args()
    meas = []
    for f in a.files:
        meas += measures(load(f))
    s = to_song(meas, a.title, a.composer, a.book, a.page, [a.page])
    ok = len(meas) - len([p for p in s['problems'] if p.startswith('bar')])
    print(json.dumps({'bars': len(meas), 'bars_ok': ok, 'chords': sum(len(b['c']) for b in s['bars']), 'problems': s['problems'][:8]}))
