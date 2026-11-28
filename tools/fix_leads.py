#!/usr/bin/env python3
"""fix_leads.py - corrections read by eye, applied to lead sheets parsed from a vector PDF (vector_lead.py output),
then each bar checked again.

  python3 tools/fix_leads.py parsed.json leads-overrides/djangofake.json out.json

Overrides: {song id: {"notes": {"<bar>": "C5/4 r/8 ~E5/8. Bb4/16t ..."}, "time": [3, 4], "pickup": false, "drop": [bar, ...],
"skip": "why"}}. Bars count from 1 as printed by vector_lead.py --debug (the pickup bar, if any, is bar 1).
Note syntax as in scan_book.py: pitch (r = rest) / value (1 2 4 8 16 32), "." dotted, "t" triplet, "~" before:
tied from the previous note, "~" after: tied into the next.
"""
import json, re, sys

NOTE_RE = re.compile(r'^(~?)(r|[A-G](?:#|b)?-?\d)/(1|2|4|8|16|32)(\.?)(t?)(~?)$')
VALUE = {'1': 192, '2': 96, '4': 48, '8': 24, '16': 12, '32': 6}
PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def parse_notes(line, tie_from=None):
    n, t, tied = [], 0, tie_from
    for tok in line.split():
        m = NOTE_RE.match(tok)
        if not m:
            raise ValueError(f'bad note {tok!r}')
        tie_in, name, val, dot, trip, tie_out = m.groups()
        d = VALUE[val] * (3 if dot else 2) // 2
        if trip:
            d = d * 2 // 3
        p = 0
        if name != 'r':
            pm = re.match(r'([A-G])(#|b)?(-?\d)', name)
            p = 12 * (int(pm.group(3)) + 1) + PC[pm.group(1)] + {'#': 1, 'b': -1, None: 0}[pm.group(2)]
            if tie_in or tied == p:
                p += 128
        n += [t, d, p]
        tied = (p & 127) if tie_out else None
        t += d
    return n, t, tied


def bar_len(time):
    return time[0] * 192 // time[1]


def fix(song, ov):
    if ov.get('time'):
        song['time'] = list(ov['time'])
    if 'pickup' in ov:
        song['pickup'] = ov['pickup']
    for bar, line in sorted(ov.get('notes', {}).items(), key=lambda kv: int(kv[0])):
        k = int(bar) - 1
        prev = song['bars'][k - 1]['n'] if k > 0 else []
        tie_from = None
        if prev and ' ' not in line[:1] and line.startswith('~'):
            tie_from = prev[-1] & 127
        song['bars'][k]['n'] = parse_notes(line, tie_from)[0]
    for k in sorted(ov.get('drop', []), reverse=True):
        del song['bars'][k - 1]
    # check again: every bar's notes add up to the bar (the first may be a pickup, the last may be short)
    probs, blen = [], bar_len(song['time'])
    for i, b in enumerate(song['bars']):
        n = b['n']; L = b.get('len', blen)
        filled = max((n[k] + n[k + 1] for k in range(0, len(n), 3)), default=0)
        if filled != L and not (i == 0 and song.get('pickup')) and not (i == len(song['bars']) - 1 and filled < L):
            probs.append(f'bar {i + 1}: {filled} of {L} ticks')
    song['problems'] = probs + [p for p in song['problems'] if not p.startswith('bar')]
    song['reviewed'] = True
    return song


if __name__ == '__main__':
    songs = json.load(open(sys.argv[1]))
    ovs = json.load(open(sys.argv[2]))
    out = []
    for s in songs:
        ov = ovs.get(s['id'])
        if ov and ov.get('skip'):
            continue
        out.append(fix(s, ov) if ov else s)
    json.dump(out, open(sys.argv[3], 'w'), separators=(',', ':'))
    fixed = [s for s in out if s['id'] in ovs]
    for s in fixed:
        b = [p for p in s['problems'] if p.startswith('bar')]
        print(f"{s['id']} {s['title']}: {len(b)} bars still off {b[:3]}")
