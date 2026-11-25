# Patch for MrHowelliii/jazz-play-along

`0001-practice-experience.patch` holds one commit for the jazz-play-along repository (on top of its commit
`ffbebb8`, "Pause and resume the band at the current beat"). It is stored here because the session that wrote it
could push to jazzbook but not to jazz-play-along.

Apply it in a jazz-play-along checkout:

```sh
git checkout -b practice-experience ffbebb8
git am /path/to/jazzbook/handoff/jazz-play-along/0001-practice-experience.patch
```

Then push the branch and merge it when you are happy with it; merging to `main` deploys the site.

What it adds is listed in the patched `README.md` (instant start, per-tune memory, quick practice controls, count-in
and melody cues, stage view with screen wake, keys and pedals, loop speed-up, recording, my list, room reverb,
speaker delay, pause on interruptions, offline install). `feature-test.cjs` in the patch tests them in Chromium.

Once applied, this folder can be deleted.
