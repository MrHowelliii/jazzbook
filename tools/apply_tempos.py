#!/usr/bin/env python3
"""apply_tempos.py - give each chart its usual tempo and feel from leads-tempos/<collection>.json, which the app uses
as the tune's starting tempo (your own saved tempo for a tune still wins). Run after rebuilding a leads/*.json file.

  python3 tools/apply_tempos.py 462jazz        # reads leads-tempos/462jazz.json, updates leads/462jazz.json

leads-tempos/<collection>.json: {song id: {"title", "tempo" (BPM, quarter notes), "feel" (swing | ballad | latin | samba),
"basis": "usual" (how the tune is commonly played, checked against search where unsure) | "guess" (little-known
tune: an estimate from the chart itself)}}
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEL = {'swing': 0, 'ballad': 1, 'latin': 2, 'samba': 6}

if __name__ == '__main__':
    name = sys.argv[1]
    tempos = json.load(open(os.path.join(ROOT, 'leads-tempos', name + '.json')))
    path = os.path.join(ROOT, 'leads', name + '.json')
    songs = json.load(open(path))
    n = 0
    for s in songs:
        t = tempos.get(s['id'])
        if t:
            s['tempo'], s['feel'] = t['tempo'], FEEL[t['feel']]
            n += 1
    json.dump(songs, open(path, 'w'), separators=(',', ':'))
    print(f'{n} of {len(songs)} songs in leads/{name}.json have a tempo and feel')
