#!/usr/bin/env python3
import os, re, json, sys
import pdfplumber

BOOKS_JSON = os.path.join(os.path.dirname(__file__), 'books.json')
SONGS_JSON = os.path.join(os.path.dirname(__file__), 'songs.json')

SIMPLE_RE   = re.compile(r'^([A-Z\'"\u2018\u201C][^\n]{2,70?}?)\s*[\.·•]{3,}\s*(\d{1,4})\s*$')
PAREN_RE    = re.compile(r'^(.+?)\s+\(([^)]{3,40})\)\s*[\.·•\s\-]{2,}(\d{1,4})\s*$')
TAB_RE      = re.compile(r'^([^\t]{3,60})\t([^\t]*)\t(\d{1,4})\s*$')
DASH_END_RE = re.compile(r'\s[-\u2013]\s([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)+)\s*$')
LOOSE_RE    = re.compile(r'^([A-Z\'"][A-Za-z\s\'\-\(\),\.&!?]{2,60?}?)\s{2,}(\d{1,4})\s*$')

# Fakebook: "PAGE TITLE — PERFORMER  PAGE TITLE — PERFORMER ..."
ENTRY_RE = re.compile(
    r'(\d{1,4})'
    r'\s+'
    r'([A-Z][A-Za-z0-9\s\',\.\(\)&!?/\u2019]{1,70}?)'
    r'(?:\s*[—–\-]+\s*([A-Za-z][A-Za-z\s\',\.\(\)&/\.]{2,60}?))?'
    r'(?=\s*\d{1,4}\s+[A-Z]|$)',
)

SKIP = {'page','contents','index','section','chapter','introduction',
        'foreword','appendix','preface','table of contents','song list',
        'bors','waz'}

def clean(t):
    if not t: return ''
    return re.sub(r'\s+', ' ', t).strip().rstrip('.,;:/\u2013-').strip()

def split_composer(title):
    m = re.search(r'\(([A-Z][a-zA-Z\s.\-\'&,]{3,40})\)\s*$', title)
    if m:
        return title[:m.start()].strip(), m.group(1).strip()
    m = DASH_END_RE.search(title)
    if m and len(m.group(1).split()) >= 2:
        return title[:title.rfind(m.group(0))].strip(), m.group(1).strip()
    return title, None

def is_garbage(text):
    if not text or len(text) < 3: return True
    alpha = sum(1 for c in text if c.isalpha())
    if alpha < len(text) * 0.5: return True
    if len(text.split()) == 1 and text.isupper() and len(text) < 6: return True
    return False

def parse_standard_line(line, loose=False):
    line = line.strip()
    if len(line) < 4: return None
    title = composer = page = None
    m = TAB_RE.match(line)
    if m:
        title, composer, page = clean(m.group(1)), m.group(2).strip() or None, int(m.group(3))
    if not title:
        m = PAREN_RE.match(line)
        if m:
            title, composer, page = clean(m.group(1)), m.group(2).strip(), int(m.group(3))
    if not title:
        m = SIMPLE_RE.match(line)
        if m:
            title, page = clean(m.group(1)), int(m.group(2))
            title, composer = split_composer(title)
    if not title and loose:
        m = LOOSE_RE.match(line)
        if m:
            title, page = clean(m.group(1)), int(m.group(2))
            title, composer = split_composer(title)
    if not title or len(title) < 3: return None
    if title.lower() in SKIP or re.match(r'^\d+$', title): return None
    return {'title': title, 'composer': composer, 'page': page}

def parse_fakebook_line(line):
    results = []
    line = line.strip()
    if not line: return results
    for m in ENTRY_RE.finditer(line):
        page      = int(m.group(1))
        title     = clean(m.group(2))
        performer = clean(m.group(3)) if m.group(3) else None
        if page < 1 or page > 999: continue
        if not title or len(title) < 3: continue
        if title.lower() in SKIP: continue
        if is_garbage(title): continue
        results.append({'title': title, 'composer': performer or None, 'page': page})
    return results

def has_text(pdf):
    sample = min(10, len(pdf.pages))
    hits = sum(1 for i in range(sample) if len((pdf.pages[i].extract_text() or '').strip()) > 20)
    return hits >= 2

def get_candidates(pdf):
    total = len(pdf.pages)
    limit = min(max(int(total * 0.20), 5), 30)
    cands = set(range(limit))
    for i in range(int(total * 0.90), total):
        cands.add(i)
    for i in range(min(limit, 15)):
        first = (pdf.pages[i].extract_text() or '').strip().split('\n')[0].lower()
        if re.match(r'^\s*(table of\s+)?contents?\s*$|song\s+list|index\s*$', first):
            for j in range(i, min(i+12, total)):
                cands.add(j)
            break
    return sorted(cands)

def ocr_page_text(page):
    try:
        import pytesseract
        img = page.to_image(resolution=200).original
        return pytesseract.image_to_string(img, config='--psm 6')
    except Exception as e:
        print(f"     OCR error: {e}")
        return ''

def get_text_lines(page):
    try:
        rows = page.extract_text_lines(layout=True, strip_whitespace=True)
        if rows:
            return [r.get('text','').strip() for r in rows if r.get('text','').strip()]
    except Exception:
        pass
    return [l.strip() for l in (page.extract_text() or '').split('\n') if l.strip()]

def index_pdf(filepath, book_title):
    print(f"  Parsing: {book_title}")
    songs, seen = [], set()

    def add(song):
        if not song: return
        key = (song['title'].lower(), song['page'])
        if key not in seen:
            seen.add(key)
            songs.append(song)

    with pdfplumber.open(filepath) as pdf:
        total   = len(pdf.pages)
        use_ocr = not has_text(pdf)
        if use_ocr:
            print(f"     No text layer — using OCR...")
        for idx, i in enumerate(get_candidates(pdf)):
            if use_ocr:
                if idx % 5 == 0:
                    print(f"     OCR: page {i+1}/{total}...")
                raw = ocr_page_text(pdf.pages[i])
                page_songs = []
                for line in raw.split('\n'):
                    page_songs.extend(parse_fakebook_line(line))
                if page_songs:
                    for s in page_songs: add(s)
                else:
                    for line in raw.split('\n'):
                        add(parse_standard_line(line, loose=True))
            else:
                for line in get_text_lines(pdf.pages[i]):
                    add(parse_standard_line(line))

    print(f"     Done: {len(songs)} songs ({total} pages)")
    return songs, total

def main():
    if not os.path.exists(BOOKS_JSON):
        print("ERROR: books.json not found.", file=sys.stderr)
        sys.exit(1)
    with open(BOOKS_JSON) as f:
        books = json.load(f)
    print(f"\nJazz Library Indexer — {len(books)} book(s)\n")
    output, total_songs = [], 0
    for book in books:
        bid, btitle, bfile = book['id'], book['title'], book['file']
        if not os.path.exists(bfile):
            print(f"  Skipping '{btitle}' — not found: {bfile}")
            output.append({'id':bid,'title':btitle,'file':bfile,'pageCount':0,'songs':[],'error':f'Not found: {bfile}'})
            continue
        try:
            songs, pages = index_pdf(bfile, btitle)
            output.append({'id':bid,'title':btitle,'file':bfile,'pageCount':pages,'songs':songs})
            total_songs += len(songs)
        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)
            output.append({'id':bid,'title':btitle,'file':bfile,'pageCount':0,'songs':[],'error':str(e)})
    with open(SONGS_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',',':'))
    kb = os.path.getsize(SONGS_JSON) // 1024
    print(f"\nDone! {total_songs} songs across {len(books)} books.")
    print(f"songs.json: {kb} KB\n")

if __name__ == '__main__':
    main()
