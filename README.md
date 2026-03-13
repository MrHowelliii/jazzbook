# 🎷 Jazz Library Search — GitHub Pages Edition

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
