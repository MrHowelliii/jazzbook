# Patches for MrHowelliii/jazz-play-along

Two commits for the jazz-play-along repository, on top of its commit `ffbebb8` ("Pause and resume the band at the
current beat"). They are stored here because the session that wrote them could push to jazzbook but not to
jazz-play-along.

```sh
git checkout -b practice-and-sound ffbebb8
git am /path/to/jazzbook/handoff/jazz-play-along/000*.patch
```

Push the branch and merge it when you are happy with it; merging to `main` deploys the site.

1. `0001`: practice experience (instant start, stage view, cues, keys and pedals, loop speed-up, recording, my list,
   room, offline install).
2. `0002`: melody on a staff, chords as functions, 16-bit instruments with a recorded alto sax, human band feel,
   play / pause / stop symbols, compact phone layout.

The second patch moves the build's pins to commits on the `claude/jazzbook-jazzband-unified-kljyw4` branches of
jazzbook (`9e439b0`: new engine and sound packs) and jazzband-op1r (`23383d7`: engine source). Keep those branches,
or merge them and re-pin, before the Pages build runs.

Once applied, this folder can be deleted.
