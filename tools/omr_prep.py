#!/usr/bin/env python3
"""Render scanned fake-book pages for Audiveris: upscale (the scans have about 9 px between staff lines at 200 dpi;
Audiveris reads best at 20+), trim the margins, and stay under Audiveris's 20-megapixel limit.

  python3 tools/omr_prep.py books/jazzfake.pdf 40 41 42 --out /tmp/pages
"""
import argparse, os, subprocess, tempfile
from PIL import Image, ImageOps, ImageFilter

MAX_PIXELS = 19_500_000


def prep(pdf, page, out, dpi=500, binarize=False):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(['pdftoppm', '-r', str(dpi), '-gray', '-png', '-f', str(page), '-l', str(page), pdf, os.path.join(td, 'p')], check=True)
        src = [f for f in os.listdir(td) if f.endswith('.png')][0]
        im = Image.open(os.path.join(td, src)).convert('L')
    ink = ImageOps.invert(im).point(lambda v: 255 if v > 100 else 0)
    # ignore thin marks in the outer 6% (binding / scanner edge marks)
    w, h = im.size
    ink.paste(0, (0, 0, int(w * 0.06), h)); ink.paste(0, (int(w * 0.94), 0, w, h))
    bb = ink.getbbox() or (0, 0, w, h)
    m = 50
    im = im.crop((max(0, bb[0] - m), max(0, bb[1] - m), min(w, bb[2] + m), min(h, bb[3] + m)))
    if im.size[0] * im.size[1] > MAX_PIXELS:
        f = (MAX_PIXELS / (im.size[0] * im.size[1])) ** 0.5
        im = im.resize((int(im.size[0] * f), int(im.size[1] * f)), Image.LANCZOS)
    if binarize:                                          # smooth the 200 dpi stair-steps, then back to black and white
        im = im.filter(ImageFilter.GaussianBlur(dpi / 400)).point(lambda v: 0 if v < 150 else 255).convert('1')
    path = os.path.join(out, f'p{page:03d}.png')
    im.save(path, optimize=False)
    return path, im.size


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf'); ap.add_argument('pages', nargs='+', type=int); ap.add_argument('--out', required=True)
    ap.add_argument('--dpi', type=int, default=500)
    ap.add_argument('--binarize', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for p in a.pages:
        print(*prep(a.pdf, p, a.out, a.dpi, a.binarize))
