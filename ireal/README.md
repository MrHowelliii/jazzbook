# iReal Pro playlists

Chord charts shared on the iReal Pro forums (forums.irealpro.com), as the `irealb://` links they are posted as.
`tools/ireal_import.py` turns them into `leads/ireal.json`: chords and form only (no melody), played by the band like
the charts read from the books.

- `jazz1460.url`: "Jazz 1460 Standards" (forum thread 12753).

To add a playlist: save its link (or the forum page holding it) in this folder, then

    python3 tools/ireal_import.py ireal/*.url --out leads/ireal.json

A tune that is in more than one playlist is kept once (the first file read wins).
