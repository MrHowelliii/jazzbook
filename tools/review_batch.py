#!/usr/bin/env python3
"""Review sheets for several songs at once, with the bars that fail the checks listed (to correct them by eye).
  python3 tools/review_batch.py /home/user/fakebook-work /tmp/review 87 102 119
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_sheet as R
import scan_book as B

work, out, ids = sys.argv[1], sys.argv[2], sys.argv[3:]          # song ids: 87, 63b (the second song on page 63)
idx = next(b for b in json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'songs.json')))
           if b['id'] == 'Jazz-Fakebook')['songs']
songs = {s['id'].split('-')[-1]: s for s in B.assemble(work, 'Jazz-Fakebook', idx, -1, {})}
for p in ids:
    s = songs.get(p)
    if not s:
        print(p, 'no song starts here'); continue
    nth = 'abcdef'.index(p[-1]) if p[-1].isalpha() else 0
    files = R.render('books/jazzfake.pdf', R.song_lines(work, int(p.rstrip('bcdef')), nth), os.path.join(out, p))
    bad = [q for q in s['problems'] if q.startswith('bar')]
    print(f"== {s['id']} {s['title']}: {len(s['bars'])} bars, time {s['time']}, key {s['key']}, pages {s['pages']}; lines: "
          + ', '.join(f'{os.path.basename(f)}={a}-{b}' for f, a, b in files))
    for q in bad:
        k = int(q.split()[1].rstrip(':')) - 1
        print('    ', q, '| homr:', s['bars'][k]['n'])
