#!/usr/bin/env python3
"""review_next.py - the next scanned songs to read by eye: one stacked image per song (every line, homr's bar numbers
over the bars) and a summary of what homr read. Skips songs already in leads-overrides/<book>.json, songs whose tune
is in the 462 Jazz Standards (by title: the app plays that exact chart) and songs homr has not read yet.

  python3 tools/review_next.py /home/user/fakebook-work2 /tmp/review 6 [--book Jazz-Fakebook --pdf books/jazzfake.pdf]
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_book as B
import review_sheet as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def norm(t):
    t = re.sub(r'\(.*?\)', ' ', str(t).lower().replace('’', "'"))
    t = re.sub(r'^the\s+|,\s*the$', ' ', t).replace('instrumental', ' ')
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()


def compact(n):
    out = []
    for k in range(0, len(n), 3):
        t, d, p = n[k], n[k + 1], n[k + 2]
        out.append(('r' if not p else 'CDEFGAB'[[0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6][(p & 127) % 12]] + str((p & 127) // 12 - 1)
                    + ('#' if (p & 127) % 12 in (1, 3, 6, 8, 10) else '') + ('~' if p >= 128 else '')) + f'/{d}')
    return ' '.join(out)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('work'); ap.add_argument('out'); ap.add_argument('n', type=int)
    ap.add_argument('--book', default='Jazz-Fakebook'); ap.add_argument('--pdf', default='books/jazzfake.pdf')
    ap.add_argument('--ids', nargs='*', help='review these song ids instead of the next ones')
    ap.add_argument('--by-index', action='store_true')
    a = ap.parse_args()
    os.chdir(ROOT)
    ovf = os.path.join(ROOT, 'leads-overrides', a.book + '.json')
    ov = json.load(open(ovf)) if os.path.exists(ovf) else {}
    covered = {norm(l['title']) for l in json.load(open(os.path.join(ROOT, 'leads', '462jazz.json')))}
    idx = next(b for b in json.load(open(os.path.join(ROOT, 'songs.json'))) if b['id'] == a.book)['songs']
    songs = B.assemble(a.work, a.book, idx, -1, {}, by_index=a.by_index)
    todo, n_cov, n_done, n_wait = [], 0, 0, 0
    for s in songs:
        if a.ids is not None:
            if s['id'] in a.ids: todo.append(s)
            continue
        if s['id'] in ov: n_done += 1; continue
        if norm(s['title']) in covered: n_cov += 1; continue
        if any(B.page_systems(a.work, p) is None for p in s['pages']): n_wait += 1; continue
        todo.append(s)
    if a.ids is None:
        print(f'{len(songs)} songs: {n_done} reviewed, {n_cov} play the 462 chart, {n_wait} waiting for homr, {len(todo)} to review')
    os.makedirs(a.out, exist_ok=True)
    for s in todo[:a.n]:
        lines = R.parts_lines(a.work, s['parts'])
        path = os.path.join(a.out, s['id'] + '.png')
        R.render_stacked(a.pdf, lines, path)
        bad = {int(q.split()[1].rstrip(':')) for q in s['problems'] if q.startswith('bar')}
        print(f"== {s['id']} {s['title']!r} pages {s['pages']} time {s['time']} key {s['key']} bars {len(s['bars'])} -> {path}")
        print('   lines: ' + ', '.join(f'{b[0]}-{b[-1]}' for _, _, b, _ in lines))
        for q in s['problems']:
            if not q.startswith('bar'): print('   !', q)
        for k in sorted(bad):
            print(f'   bar {k} wrong length: {compact(s["bars"][k - 1]["n"])}')
