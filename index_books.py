#!/usr/bin/env python3
"""
index_books.py — Jazz Library Indexer (multi-format)

Supported formats (set per book in books.json):
  "fakebook"  — entries like: 42 Song Title — Performer Name
  "dotleader" — entries like: Song Title .............. 42  (mixed case)
  "realbook"  — ALL CAPS, dot leaders, two columns (splits page in half for OCR)
  "auto"      — tries all parsers, uses whichever finds most songs
"""

import os, re, json, sys
import pdfplumber

BOOKS_JSON = os.path.join(os.path.dirname(__file__), 'books.json')
SONGS_JSON = os.path.join(os.path.dirname(__file__), 'songs.json')

SKIP = {
    'page', 'contents', 'index', 'section', 'chapter', 'introduction',
    'foreword', 'appendix', 'preface', 'table of contents', 'song list',
    'alphabetical index', 'songs', 'title', 'a', 'b', 'c', 'd', 'e', 'f',
    'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't',
    'u', 'v', 'w', 'x', 'y', 'z', 'a cont', 'b cont', 'c cont', 'eb cont',
    'cg cont', 'g cont', 'i cont', 'd cont', 'e cont', 'f cont',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean(t):
    return re.sub(r'\s+', ' ', t.strip().rstrip('.,;:–—-/')).strip()

def is_skip(title):
    return (not title or len(title) < 2
            or title.lower().strip('.') in SKIP
            or re.match(r'^[\d\s\W]+$', title))

def has_text(pdf):
    sample = min(10, len(pdf.pages))
    hits = sum(1 for i in range(sample)
               if len((pdf.pages[i].extract_text() or '').strip()) > 20)
    return hits >= 2

def get_candidates(pdf, max_index_pages=None):
    total = len(pdf.pages)
    if max_index_pages:
        # Only scan the specified number of pages for the index
        return list(range(min(max_index_pages, total)))
    limit = min(max(int(total * 0.20), 5), 30)
    cands = set(range(limit))
    for i in range(int(total * 0.90), total):
        cands.add(i)
    for i in range(min(limit, 15)):
        first = (pdf.pages[i].extract_text() or '').strip().split('\n')[0].lower()
        if re.match(r'^\s*(table of\s+)?contents?\s*$|song\s+list|index\s*$', first):
            for j in range(i, min(i + 12, total)):
                cands.add(j)
            break
    return sorted(list(cands))

def ocr_page(page):
    try:
        import pytesseract
        img = page.to_image(resolution=300).original
        return pytesseract.image_to_string(img, config='--psm 6')
    except Exception as e:
        print(f"     OCR error: {e}")
        return ''

# Common OCR letter→digit confusions at end of lines
DIGIT_MAP = {
    'O':'0','o':'0','I':'1','l':'1','i':'1','Z':'2','z':'2',
    'S':'5','s':'5','G':'6','g':'6','B':'8','b':'8','D':'0',
    'T':'7','t':'7','q':'9','Q':'9'
}

def fix_ocr_digits(text):
    """Fix letter/digit confusions at end of each line (e.g. BI→81, BS→85)."""
    fixed = []
    for line in text.split('\n'):
        m = re.search(r'([A-Za-z0-9]{1,3})\s*$', line)
        if m:
            tail = m.group(1)
            converted = ''.join(DIGIT_MAP.get(c, c) for c in tail)
            if converted.isdigit():
                line = line[:m.start()] + converted
        fixed.append(line)
    return '\n'.join(fixed)

def ocr_page_split_columns(page, idx=None):
    """Split page in half, OCR each column separately."""
    try:
        import pytesseract
        from PIL import ImageEnhance
        img = page.to_image(resolution=300).original
        img = img.convert('L')
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = ImageEnhance.Sharpness(img).enhance(2.0)

        w, h = img.size
        mid = w // 2
        left_col  = img.crop((0,   0, mid, h))
        right_col = img.crop((mid, 0, w,   h))

        left_text   = pytesseract.image_to_string(left_col,  config='--psm 4')
        right_text  = pytesseract.image_to_string(right_col, config='--psm 4')
        right_text2 = pytesseract.image_to_string(right_col, config='--psm 11')

        right_text  = fix_ocr_digits(right_text)
        right_text2 = fix_ocr_digits(right_text2)

        return left_text + '\n' + right_text + '\n' + right_text2
    except Exception as e:
        print(f"     OCR error: {e}")
        return ''

def get_text_lines(page):
    try:
        rows = page.extract_text_lines(layout=True, strip_whitespace=True)
        if rows:
            return [r.get('text', '').strip() for r in rows if r.get('text', '').strip()]
    except Exception:
        pass
    return [l.strip() for l in (page.extract_text() or '').split('\n') if l.strip()]

# ── Format: fakebook ──────────────────────────────────────────────────────────

def parse_fakebook(full_text):
    songs = []
    text = full_text
    for dash in ['—', '–', '\u2014', '\u2013']:
        text = text.replace(dash, '|')

    entry_re = re.compile(r'\b(\d{1,3})\s+([A-Z])')
    matches  = list(entry_re.finditer(text))

    for idx, m in enumerate(matches):
        page_num = int(m.group(1))
        if page_num == 0 or page_num > 800:
            continue
        start = m.start()
        end   = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        chunk = re.sub(r'^\d{1,3}\s+', '', chunk)

        if '|' in chunk:
            parts    = chunk.split('|', 1)
            title    = clean(parts[0])
            composer = clean(re.sub(r'\s*\d.*$', '', parts[1]))
            if len(composer) < 2:
                composer = None
        else:
            title    = clean(chunk)
            composer = None

        if is_skip(title):
            continue
        songs.append({'title': title, 'composer': composer or None, 'page': page_num})

    return songs

# ── Format: dotleader (mixed case) ───────────────────────────────────────────

DOTLEADER_RE     = re.compile(r'^([A-Z\'"\u2018\u201C][^\n]{2,70}?)\s*[\.·•]{3,}\s*(\d{1,4})\s*$')
PAREN_RE         = re.compile(r'^(.+?)\s+\(([^)]{3,40})\)\s*[\.·•\s\-]{2,}(\d{1,4})\s*$')
TAB_RE           = re.compile(r'^([^\t]{3,60})\t([^\t]*)\t(\d{1,4})\s*$')
DASH_COMPOSER_RE = re.compile(r'\s[-–]\s([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)+)\s*$')

def split_composer(title):
    m = re.search(r'\(([A-Z][a-zA-Z\s.\-\'&,]{3,40})\)\s*$', title)
    if m:
        return title[:m.start()].strip(), m.group(1).strip()
    m = DASH_COMPOSER_RE.search(title)
    if m and len(m.group(1).split()) >= 2:
        return title[:title.rfind(m.group(0))].strip(), m.group(1).strip()
    return title, None

def parse_dotleader_line(line):
    line = line.strip()
    if len(line) < 4:
        return None
    m = TAB_RE.match(line)
    if m:
        title = clean(m.group(1)); composer = m.group(2).strip() or None; page = int(m.group(3))
        return {'title': title, 'composer': composer, 'page': page} if not is_skip(title) else None
    m = PAREN_RE.match(line)
    if m:
        title = clean(m.group(1)); composer = m.group(2).strip(); page = int(m.group(3))
        return {'title': title, 'composer': composer, 'page': page} if not is_skip(title) else None
    m = DOTLEADER_RE.match(line)
    if m:
        title, page = clean(m.group(1)), int(m.group(2))
        title, composer = split_composer(title)
        return {'title': title, 'composer': composer, 'page': page} if not is_skip(title) else None
    return None

def parse_dotleader(text):
    return [s for s in (parse_dotleader_line(l) for l in text.split('\n')) if s]

# ── Format: realbook ─────────────────────────────────────────────────────────

REALBOOK_TITLE_RE = re.compile(r'^([A-Z][A-Z0-9 \'\(\),&!?/\-]*)')
REALBOOK_PAGE_RE  = re.compile(r'(\d{1,3})\s*$')

def parse_realbook(full_text):
    songs = []
    for line in full_text.split('\n'):
        line = line.strip()
        if not line or len(line) < 6:
            continue
        if not line[0].isupper():
            continue
        page_match = REALBOOK_PAGE_RE.search(line)
        if not page_match:
            continue
        page_num = int(page_match.group(1))
        if page_num == 0 or page_num > 600:
            continue
        title_match = REALBOOK_TITLE_RE.match(line)
        if not title_match:
            continue
        title = clean(title_match.group(1))
        title = re.sub(r'\s*\d+\s*$', '', title).strip()
        title = clean(title)
        if is_skip(title) or len(title) < 3:
            continue
        letters = re.sub(r'[^A-Za-z]', '', title)
        if not letters or sum(1 for c in letters if c.isupper()) / len(letters) < 0.6:
            continue
        songs.append({'title': title, 'composer': None, 'page': page_num})
    return songs

# ── Core indexer ──────────────────────────────────────────────────────────────

def best_parse(raw, fmt):
    if fmt == 'fakebook':
        return parse_fakebook(raw)
    if fmt == 'dotleader':
        return parse_dotleader(raw)
    if fmt == 'realbook':
        return parse_realbook(raw)
    results = [parse_fakebook(raw), parse_dotleader(raw), parse_realbook(raw)]
    return max(results, key=len)

def index_pdf(filepath, book_title, fmt='auto', max_index_pages=None):
    print(f"  Parsing: {book_title} (format: {fmt})")
    songs, seen = [], set()
    use_split = (fmt == 'realbook')

    def add(song):
        if not song:
            return
        key = (song['title'].lower(), song['page'])
        if key not in seen:
            seen.add(key)
            songs.append(song)

    with pdfplumber.open(filepath) as pdf:
        total      = len(pdf.pages)
        use_ocr    = not has_text(pdf)
        candidates = get_candidates(pdf, max_index_pages=max_index_pages)

        if use_ocr:
            mode = 'split-column OCR' if use_split else 'OCR'
            print(f"     No text layer — using {mode}...")

        for idx, i in enumerate(candidates):
            if use_ocr and idx % 5 == 0:
                print(f"     OCR: page {i+1}/{total}...")

            if use_ocr:
                raw = ocr_page_split_columns(pdf.pages[i], idx=idx) if use_split else ocr_page(pdf.pages[i])
            else:
                raw = '\n'.join(get_text_lines(pdf.pages[i]))

            if not raw.strip():
                continue

            for s in best_parse(raw, fmt):
                add(s)

    print(f"     Done: {len(songs)} songs ({total} pages)")
    return songs, total

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(BOOKS_JSON):
        print("ERROR: books.json not found.", file=sys.stderr)
        sys.exit(1)

    with open(BOOKS_JSON) as f:
        books = json.load(f)

    print(f"\nJazz Library Indexer — {len(books)} book(s)\n")
    output, total_songs = [], 0

    for book in books:
        bid    = book['id']
        btitle = book['title']
        bfile  = book['file']
        fmt    = book.get('format', 'auto')
        offset = book.get('offset', 0)
        max_index_pages = book.get('index_pages', None)

        if not os.path.exists(bfile):
            print(f"  Skipping '{btitle}' — not found: {bfile}")
            output.append({'id': bid, 'title': btitle, 'file': bfile,
                           'pageCount': 0, 'songs': [], 'offset': offset,
                           'error': f'Not found: {bfile}'})
            continue

        try:
            songs, pages = index_pdf(bfile, btitle, fmt=fmt, max_index_pages=max_index_pages)
            output.append({'id': bid, 'title': btitle, 'file': bfile,
                           'pageCount': pages, 'songs': songs, 'offset': offset})
            total_songs += len(songs)
        except Exception as e:
            print(f"  Error indexing '{btitle}': {e}", file=sys.stderr)
            output.append({'id': bid, 'title': btitle, 'file': bfile,
                           'pageCount': 0, 'songs': [], 'offset': offset,
                           'error': str(e)})

    with open(SONGS_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    size_kb = os.path.getsize(SONGS_JSON) // 1024
    print(f"\nDone! {total_songs} songs across {len(books)} books.")
    print(f"songs.json: {size_kb} KB\n")

if __name__ == '__main__':
    main()
