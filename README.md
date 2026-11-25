# 🎷 Jazz Library: search and play along

Search your jazz fake-book PDFs by title or composer, and **play along with a band**: for every song in
*462 Jazz Standards* the app knows the melody and the chords, so a jazz band (drums, bass, piano, guitar) plays the
changes while a lead voice plays the tune. Mute the melody to practise the head and your solos, or mute the piano,
guitar, bass or drums to comp in their place. Everything runs in the browser; GitHub Pages serves it as static files.

## Play along

Open a song with **▶ Play along** (or turn on the **▶ Play-along** filter to list them all).

* **You play**: *Just listen*, *Melody* (the lead drops out; *Guide melody* keeps it quietly), *Piano*, *Guitar*,
  *Bass* or *Drums* (that instrument drops out).
* **Choruses**: the melody plays the head in the first and last chorus; the choruses between are for solos
  (or tick *Melody every chorus*). *Finish* plays the ending after the current chorus.
* **Tempo**, **key** (the band and the melody move together), **feel** (swing, ballad, bossa / straight 8ths).
* **Band, mixer and arrangement**: 42 players (12 drummers, 10 each of bassists, pianists and guitarists), lead
  sound (horn, piano, guitar, vibes) and octave, count-in or intro, final chord or tag, levels, and chord names for
  B♭, E♭ or F instruments.
* **Chart** shows the bars as printed (repeats, endings, D.S. / Coda) with the chords and a melody outline.
  **Original page** shows the PDF page itself with the bar being played highlighted. Click a bar to start there;
  Shift-click another to select bars, then **Loop**. Space plays and stops.

The melody and chords were read straight out of the PDF (`tools/vector_lead.py`): that book is engraved in Finale,
so notes, rests, accidentals, beams, ties, triplets, repeats, endings and chord symbols are font glyphs and vector
lines rather than pixels, and they are read exactly. Every bar is checked: its notes must add up to the time
signature and the bar count must match the printed bar numbers. 453 of 462 songs pass both checks; the player
marks the others ("n bars may be misread"). The two scanned books (*Real Book*, *Jazz Fakebook*) are images and are
not play-along themselves; their songs that are also in *462 Jazz Standards* (by title: 177 in the Jazz Fakebook,
56+ in the Real Book) play that chart, marked **▶ Play along \***.

Experimental, for the scanned books: `tools/omr_prep.py` renders pages for [Audiveris](https://github.com/Audiveris/audiveris)
(5.6.3 builds with Java 21; it also needs `repository.jboss.org` for jai-core and the legacy `eng.traineddata` from
tesseract-ocr/tessdata), and `tools/mxl_lead.py` turns its MusicXML into this lead-sheet format with the same bar
checks. On the Jazz Fakebook scans (200 dpi, about 9 px between staff lines) only about 30% of bars come out right,
so none of its songs is offered from recognition yet.

The band is the Jazz Band engine (github.com/MrHowelliii/jazzband-op1r, the OP-1 plugin's C code compiled to
WebAssembly) with its sampled instruments; credits for the samples are in `engine/packs/CREDITS.md`.

| Path | |
|---|---|
| `app/` | the player (`player.js`, `player.css`) and `songform.js` (form unrolling, chords per beat, melody, swing) |
| `engine/` | `jazzband.wasm`, the AudioWorklet (`worklet.js`), sound packs (`packs/*.jbp`); rebuilt by jazzband-op1r's `web/sync-jazzbook.sh` |
| `leads/462jazz.json` | the parsed lead sheets |
| `tools/vector_lead.py` | the PDF reader: `python3 tools/vector_lead.py books/462jazzstandardsS.pdf --first 35 --out leads/462jazz.json` (needs `pip install pymupdf`) |
| `vendor/pdfjs/` | PDF.js 3.11.174 (Apache 2.0) for the original pages |
| `tests/songform.test.mjs` | `node tests/songform.test.mjs` |

---

## Search: setup (GitHub Pages)

A fully static, zero-cost web app to search your jazz sheet music PDFs by title or composer.
Runs entirely in the browser. No server, no database, no cost.

## Repository structure

```
your-repo/
├── index.html       ← the whole app (HTML + CSS + JS)
├── books.json       ← your book list (edit this!)
└── books/
    ├── real-book-v1.pdf
    ├── new-real-book-v1.pdf
    └── ...
```

## Setup steps

### 1. Edit books.json
List your PDF files:
```json
[
  {
    "id": "real-book-v1",
    "title": "The Real Book Vol. 1",
    "file": "books/real-book-v1.pdf"
  }
]
```
- `id` — a unique slug, no spaces (used for caching)
- `title` — display name shown in the UI
- `file` — path to the PDF relative to index.html

### 2. Upload your PDFs
Put your PDF files in the `books/` folder on GitHub.
GitHub supports files up to 100MB. If a file is larger, use Git LFS.

### 3. Enable GitHub Pages
- Go to your repo → Settings → Pages
- Set Source to: **Deploy from a branch**
- Branch: `main`, folder: `/ (root)`
- Save — your site will be live at `https://YOUR_USERNAME.github.io/REPO_NAME`

## How it works

1. On first load, the app fetches and parses each PDF's Table of Contents using PDF.js
2. The song index is cached in your browser's IndexedDB — subsequent visits are instant
3. Use the **↺ re-index** button next to a book if you replace a PDF

## Tips
- Works best with text-based PDFs (not scanned images)
- The parser looks for TOC patterns like: `Autumn Leaves ............ 34`
- You can filter by title, composer, or a specific book using the pills under the search bar
