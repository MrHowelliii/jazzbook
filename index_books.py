#!/usr/bin/env python3
"""
index_books.py — Jazz Library Indexer
Reads books.json, parses each PDF's table of contents,
and writes songs.json for the frontend to consume.
"""

import os
import re
import json
import sys
import pdfplumber

BOOKS_JSON = os.path.join(os.path.dirname(__file__), 'books.json')
SONGS_JSON = os.path.join(os.path.dirname(__file__), 'songs.json')

# ── Parsing patterns ──────────────────────────────────────────────────────────

SIMPLE_RE   = re.compile(r'^([A-Z\'"\u2018\u201C][^\n]{2,70?}?)\s*[\.·•]{3,}\s*(\d{1,4})\s*$')
PAREN_RE    = re.compile(r'^(.+?)\s+\(([^)]{3,40})\)\s*[\.·•\s\-]{2,}(\d{1,4})\s*$')
TAB_RE      = re.compile(r'^([^\t]{3,60})\t([^\t]*)\t(\d{1,4})\s*$')
DASH_END_RE = re.compile(r'\s[-–]\s([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)+)\s*$')

SKIP_TITLES = {
    'page', 'contents', 'index', 'section', 'chapter',
    'introduction', 'foreword', 'appendix', 'preface',
    'table of contents', 'song list', 'alphabetical index',
}


def clean(t):
    return re.sub(r'\s+', ' ', t.strip().rstrip('.,;:–-')).strip()


def split_composer(title):
    m = re.search(r'\(([A-Z][a-zA-Z\s.\-\'&,]{3,40})\)\s*$', title)
    if m:
        return title[:m.start()].strip(), m.group(1).strip()
    m = DASH_END_RE.search(title)
    if m and len(m.group(1).split()) >= 2:
        return title[:title.rfind(m.group(0))].strip(), m.group(1).strip()
    return title, None


def parse_line(line):
    line = line.strip()
    if len(line) < 4:
        return None

    title = composer = page = None

    # Tab-separated
    m = TAB_RE.match(line)
    if m:
        title, composer, page = clean(m.group(1)), m.group(2).strip() or None, int(m.group(3))

    # "Title (Composer) ..... page"
    if not title:
        m = PAREN_RE.match(line)
        if m:
            title, composer, page = clean(m.group(1)), m.group(2).strip(), int(m.group(3))

    # Simple dot-leader
    if not title:
        m = SIMPLE_RE.match(line)
        if m:
            title, page = clean(m.group(1)), int(m.group(2))
            title, composer = split_composer(title)

    if not title or len(title) < 3:
        return None
    if title.lower() in SKIP_TITLES:
        return None
    if re.match(r'^\d+$', title):
        return None

    return {'title': title, 'composer': composer, 'page': page}


def extract_lines_from_page(page):
    """Group text items by Y position to reconstruct lines."""
    content = page.extract_text_lines(layout=True, strip_whitespace=True)
    if content:
        return [l.get('text', '').strip() for l in content]
    # Fallback: raw extract_text
    text = page.extract_text() or ''
    return text.split('\n')


def index_pdf(filepath, book_title):
    print(f"  📖 Parsing: {book_title}")
    songs = []
    seen = set()

    with pdfplumber.open(filepath) as pdf:
        total = len(pdf.pages)
        toc_limit = min(max(int(total * 0.20), 5), 30)

        # Candidate page indices (0-based)
        candidates = set(range(toc_limit))
        # Also scan last 10%
        for i in range(int(total * 0.90), total):
            candidates.add(i)

        # First pass: detect explicit Contents page
        for i in range(min(toc_limit, 15)):
            text = pdf.pages[i].extract_text() or ''
            first_line = text.strip().split('\n')[0].lower()
            if re.match(r'^\s*(table of\s+)?contents?\s*$|song\s+list|index\s*$', first_line):
                for j in range(i, min(i + 12, total)):
                    candidates.add(j)
                break

        for i in sorted(candidates):
            lines = extract_lines_from_page(pdf.pages[i])
            for line in lines:
                song = parse_line(line)
                if song:
                    key = (song['title'].lower(), song['page'])
                    if key not in seen:
                        seen.add(key)
                        songs.append(song)

    print(f"     ✓ {len(songs)} songs found ({total} pages)")
    if len(songs) == 0:
        print("\n--- DEBUG: raw text from first 10 pages ---")
        with pdfplumber.open(filepath) as pdf2:
            for i in range(min(10, len(pdf2.pages))):
                print(f"\n=== Page {i+1} ===")
                print(pdf2.pages[i].extract_text() or "(no text)")
        print("--- END DEBUG ---")

    return songs, total


def main():
    if not os.path.exists(BOOKS_JSON):
        print("ERROR: books.json not found.", file=sys.stderr)
        sys.exit(1)

    with open(BOOKS_JSON) as f:
        books = json.load(f)

    print(f"\n🎷 Jazz Library Indexer — {len(books)} book(s)\n")

    output = []
    total_songs = 0

    for book in books:
        book_id    = book['id']
        book_title = book['title']
        pdf_path   = book['file']

        if not os.path.exists(pdf_path):
            print(f"  ⚠ Skipping '{book_title}' — file not found: {pdf_path}")
            output.append({
                'id': book_id,
                'title': book_title,
                'file': pdf_path,
                'pageCount': 0,
                'songs': [],
                'error': f'File not found: {pdf_path}'
            })
            continue

        try:
            songs, page_count = index_pdf(pdf_path, book_title)
            output.append({
                'id': book_id,
                'title': book_title,
                'file': pdf_path,
                'pageCount': page_count,
                'songs': songs,
            })
            total_songs += len(songs)
        except Exception as e:
            print(f"  ✗ Error indexing '{book_title}': {e}", file=sys.stderr)
            output.append({
                'id': book_id,
                'title': book_title,
                'file': pdf_path,
                'pageCount': 0,
                'songs': [],
                'error': str(e)
            })

    with open(SONGS_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    size_kb = os.path.getsize(SONGS_JSON) // 1024
    print(f"\n✅ Done! {total_songs} songs across {len(books)} books.")
    print(f"   songs.json written ({size_kb} KB)\n")


if __name__ == '__main__':
    main()
