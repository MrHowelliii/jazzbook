// songform.js - turn a parsed lead sheet (leads/*.json) into what the band engine plays: the form unrolled
// (repeats, endings, D.S. / D.C., Coda, Fine), a chord per beat, and the melody on the band's grid.
// Pure functions: used by the page and by tests under Node.

export const TPQ = 48;                                   // the lead sheets' ticks per quarter note
export const Q = { MAJ7: 0, MIN7: 1, DOM7: 2, HALFDIM: 3, DIM7: 4, MIN6: 5, MAJ6: 6, DOM7B9: 7, MINMAJ7: 8 };
const PCS = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

// an iReal-style token ('Bb^7', 'C-7', 'Ah7', 'F7b9') -> {root, q}; the same mapping as the Jazz Band engine
export function parseToken(tok) {
  if (!tok || !(tok[0] in PCS)) return null;
  let pc = PCS[tok[0]], i = 1;
  if (tok[i] === 'b') { pc--; i++; } else if (tok[i] === '#') { pc++; i++; }
  let q = tok.slice(i); const sl = q.indexOf('/'); if (sl >= 0) q = q.slice(0, sl);
  const is = (...a) => a.includes(q);
  let qq;
  if (is('^7', 'maj7', '^', '^9')) qq = Q.MAJ7;
  else if (is('-7', 'm7', '-9', '-11')) qq = Q.MIN7;
  else if (is('h7', 'h', 'm7b5', '-7b5')) qq = Q.HALFDIM;
  else if (is('o7', 'o', 'dim7', 'dim')) qq = Q.DIM7;
  else if (is('-6', 'm6', '-', 'm')) qq = Q.MIN6;
  else if (is('-^7', 'mMaj7')) qq = Q.MINMAJ7;
  else if (is('7b9', '7alt')) qq = Q.DOM7B9;
  else if (is('', '6', '69')) qq = Q.MAJ6;
  else if (q[0] === '7' || q[0] === '9' || q === '13') qq = Q.DOM7;
  else qq = Q.MAJ7;
  return { root: ((pc % 12) + 12) % 12, q: qq };
}

// ---------------------------------------------------------------------------------------------- the form
// Returns the indices of `bars` in the order they are played once through (the "head").
export function unroll(bars) {
  const n = bars.length;
  const signs = (b) => (b && b.sg) || [];
  const dsAt = bars.findIndex(b => b.ds || b.dc);
  const segno = bars.findIndex(b => signs(b).includes('segno'));
  const codaBars = bars.map((b, i) => signs(b).includes('coda') ? i : -1).filter(i => i >= 0);
  const fineAt = bars.findIndex(b => b.fn);
  let mode = dsAt >= 0 ? (bars[dsAt].ds || bars[dsAt].dc) : null;   // what the jump is "al": coda, fine, or the end
  if (mode === 'end') mode = codaBars.length ? 'coda' : fineAt >= 0 ? 'fine' : 'end';
  const toCoda = codaBars.length && codaBars[0] < dsAt ? codaBars[0] : -1;
  let codaStart = codaBars.filter(i => i > dsAt)[0];
  if (codaStart === undefined) codaStart = dsAt + 1;
  // each run of bars under volta brackets: its last ending number (how many passes the repeat takes)
  const lastEnding = new Array(n).fill(0);
  for (let i = 0; i < n;) {
    if (!bars[i].e) { i++; continue; }
    let j = i, mx = 0;
    while (j < n && bars[j].e) { mx = Math.max(mx, ...bars[j].e); j++; }
    for (let k = i; k < j; k++) lastEnding[k] = mx;
    i = j;
  }
  const out = [];
  let i = 0, pass = 1, repStart = 0, jumped = false;
  const taken = new Map();
  while (i < n && out.length < 2000) {
    const b = bars[i];
    if (b.rs && i !== repStart) { repStart = i; pass = 1; }
    if (b.e && !b.e.includes(jumped ? lastEnding[i] : pass)) { i++; continue; }
    out.push(i);
    if (jumped && mode === 'coda' && i === toCoda && codaStart < n) { i = codaStart; continue; }
    if (jumped && mode === 'fine' && b.fn) break;
    if (b.re && !jumped) {
      const t = taken.get(i) || 0;
      if (t + 1 < Math.max(2, lastEnding[i])) { taken.set(i, t + 1); pass++; i = repStart; continue; }
    }
    if (i === dsAt && !jumped) { jumped = true; i = b.ds ? Math.max(0, segno) : 0; continue; }
    i++;
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- one song
// song: an entry of leads/*.json. opts: {feel: 0 swing / 1 ballad / 2 bossa, swing: ratio (2 = triplet), bpm}
export function prepare(song, opts = {}) {
  const pick = song.pickup ? song.bars[0] : null;
  const body = song.pickup ? song.bars.slice(1) : song.bars.slice();
  const meter = song.time || [4, 4];
  const beats = meter[0] === 3 ? 3 : 4;                 // the band plays 4 or 3 beats a bar
  const beatLen = TPQ * 4 / (meter[1] || 4);
  const barLen = beats * beatLen;
  const form = unroll(body);
  // chords: one per beat, carried over bar lines; the first chord also fills the start
  const firstChord = body.flatMap(b => b.c).find(c => parseToken(c[2]));
  let cur = firstChord ? parseToken(firstChord[2]) : { root: 0, q: Q.MAJ6 };
  let curSym = firstChord ? firstChord[1] : 'C6';
  const chordsBySrc = body.map(b => {
    const beatsOut = [];
    for (let k = 0; k < beats; k++) {
      for (const c of b.c) {
        const beat = Math.floor(c[0] / beatLen);
        if (beat === k || (k === beats - 1 && beat > k)) { const p = parseToken(c[2]); if (p) { cur = p; curSym = c[1]; } }
      }
      beatsOut.push({ ...cur, sym: curSym });
    }
    return beatsOut;
  });
  // sections: a new letter after a double bar, a repeat start, or a segno / coda
  const section = new Array(body.length).fill(0);
  let letter = 65;
  body.forEach((b, i) => {
    if (i === 0 || b.rs || (i > 0 && body[i - 1].db) || (b.sg && b.sg.length)) { section[i] = letter; letter = Math.min(letter + 1, 90); }
  });
  const barBytes = new Uint8Array(form.length * 9);
  form.forEach((src, j) => {
    const cs = chordsBySrc[src];
    for (let k = 0; k < 4; k++) { const c = cs[Math.min(k, beats - 1)]; barBytes[9 * j + 2 * k] = c.root; barBytes[9 * j + 2 * k + 1] = c.q; }
    barBytes[9 * j + 8] = section[src] && (j === 0 || form[j - 1] !== src - 1 || section[src]) ? section[src] : 0;
  });
  // melody: ties joined (also across bar lines), swing on 8th notes, a little accent on strong beats
  const swing = opts.feel === 2 ? 1 : (opts.swing || (opts.bpm > 220 ? 1.5 : opts.bpm < 90 ? 1.7 : 2));
  const sw = (x) => {                                      // x: 48ths in the bar -> swung position
    if (swing === 1) return x;
    const beat = Math.floor(x / 48), f = x - beat * 48, mid = 48 * swing / (1 + swing);
    return beat * 48 + (f <= 24 ? f * mid / 24 : mid + (f - 24) * (48 - mid) / 24);
  };
  const notes = [];                                        // {bar, t, d, p, v} with t, d in 48ths (after swing)
  let lastNote = null;
  form.forEach((src, j) => {
    const n = body[src].n;
    for (let k = 0; k < n.length; k += 3) {
      const t = n[k], d = n[k + 1], raw = n[k + 2];
      if (raw === 0) { lastNote = null; continue; }
      const tie = raw >= 128, p = raw & 127;
      const absEnd = j * barLen + sw(t + d);
      if (tie && lastNote && lastNote.p === p) { lastNote.end = absEnd; continue; }
      const st = sw(t);
      const onBeat = t % 48 === 0, strong = t % (beatLen * 2) === 0;
      const v = Math.min(120, 84 + (strong ? 10 : onBeat ? 5 : -4) + (d >= 96 ? 4 : 0));
      lastNote = { bar: j, t: Math.round(st), p, v, end: absEnd };
      notes.push(lastNote);
    }
  });
  const lead = new Uint8Array(notes.length * 8), dv = new DataView(lead.buffer);
  notes.forEach((x, i) => {
    const d = Math.max(4, Math.round(x.end - (x.bar * barLen + x.t)));
    dv.setUint16(8 * i, x.bar, true); dv.setUint16(8 * i + 2, x.t, true); dv.setUint16(8 * i + 4, Math.min(65535, d), true);
    lead[8 * i + 6] = x.p; lead[8 * i + 7] = x.v;
  });
  // the pickup: played over the end of the count-in bar
  const pk = [];
  if (pick) {
    const len = pick.n.reduce((s, _, k) => (k % 3 === 0 ? Math.max(s, pick.n[k] + pick.n[k + 1]) : s), 0);
    for (let k = 0; k < pick.n.length; k += 3) {
      if (!pick.n[k + 2]) continue;
      pk.push({ t: barLen - len + pick.n[k], d: pick.n[k + 1], p: pick.n[k + 2] & 127, v: 90 });
    }
  }
  const pickBytes = new Uint8Array(Math.min(32, pk.length) * 8), pdv = new DataView(pickBytes.buffer);
  pk.slice(0, 32).forEach((x, i) => { pdv.setUint16(8 * i + 2, x.t, true); pdv.setUint16(8 * i + 4, x.d, true); pickBytes[8 * i + 6] = x.p; pickBytes[8 * i + 7] = x.v; });
  return { body, form, beats, barLen, chordsBySrc, section, barBytes, lead, pick: pickBytes, notes, pickup: pick };
}

// ---------------------------------------------------------------------------------------------- chord names
const SHARP_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const FLAT_NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
export const KEY_NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
// key signature (fifths) + minor guess -> tonic pitch class of the major key
export function keyTonic(fifths) { return ((fifths * 7) % 12 + 12) % 12; }
export function transposeSym(sym, semis, flats = true) {
  if (!semis) return sym;
  const names = flats ? FLAT_NAMES : SHARP_NAMES;
  return sym.replace(/(^|\/)([A-G])([b#]?)/g, (m, pre, l, acc) => {
    const pc = (PCS[l] + (acc === 'b' ? -1 : acc === '#' ? 1 : 0) + semis + 120) % 12;
    return pre + names[pc];
  });
}
// pretty: b -> ♭, # -> ♯, maj -> Δ when short
export function prettySym(sym) {
  return sym.replace(/([A-G])b/g, '$1♭').replace(/([A-G])#/g, '$1♯').replace(/\(b/g, '(♭').replace(/\(#/g, '(♯')
            .replace(/([^A-G(])b(\d)/g, '$1♭$2').replace(/([^A-G(])#(\d)/g, '$1♯$2');
}

// ---------------------------------------------------------------------------------------------- titles
export function normTitle(t) {
  return String(t || '').toLowerCase().replace(/\(.*?\)/g, ' ').replace(/^the\s+|,\s*the$/g, ' ').replace(/\binstrumental\b/g, ' ')
    .replace(/[^a-z0-9]+/g, ' ').trim();
}
