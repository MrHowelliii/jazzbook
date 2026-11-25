// node tests/songform.test.mjs - checks the parsed lead sheets (leads/462jazz.json) and how they become band input.
import fs from 'node:fs';
import { unroll, prepare, parseToken, transposeSym, Q } from '../app/songform.js';
let fails = 0;
const check = (ok, msg) => { console.log((ok ? 'ok:   ' : 'FAIL: ') + msg); if (!ok) fails++; };
const songs = JSON.parse(fs.readFileSync(new URL('../leads/462jazz.json', import.meta.url)));
const by = (t) => songs.find(s => s.title === t);
const body = (s) => (s.pickup ? s.bars.slice(1) : s.bars);

check(songs.length >= 450, `${songs.length} lead sheets`);
const clean = songs.filter(s => !s.problems.length).length;
check(clean / songs.length > 0.95, `${clean} of ${songs.length} songs: every bar adds up and matches the printed bar numbers`);

// tokens the band engine understands
const t = (x) => { const p = parseToken(x); return p && [p.root, p.q].join(); };
check(t('Bb^7') === `10,${Q.MAJ7}` && t('C-7') === `0,${Q.MIN7}` && t('Ah7') === `9,${Q.HALFDIM}` && t('F#7b9') === `6,${Q.DOM7B9}`, 'iReal tokens map to the engine qualities');
check(transposeSym('Bbmaj7/F', 2) === 'Cmaj7/G' && transposeSym('F#m7(b5)', -1, true) === 'Fm7(b5)', 'chord symbols transpose (root and bass)');

// forms: repeats with endings, D.C. al Fine
const al = by('Autumn Leaves'), bs = by('Body And Soul'), wl = by('When Lights Are Low');
check(al.pickup && unroll(body(al)).length === 32, 'Autumn Leaves: pickup, then 32 bars with first and second endings');
check(unroll(body(bs)).length === 32, 'Body And Soul: AABA, 32 bars');
check(unroll(body(wl)).join(',') === [...Array(8).keys(), ...Array(16).keys(), ...Array(8).keys()].join(','), 'When Lights Are Low: D.C. al Fine');

// melody: Autumn Leaves starts G A Bb (pickup) then Eb
const p = prepare(al, { feel: 0, bpm: 140 });
const pick = [...p.pick].filter((_, i) => i % 8 === 6);
check(pick.join() === '67,69,70', `pickup G A Bb (${pick})`);
check(p.notes[0].p === 75 && p.notes[0].bar === 0 && p.notes[0].t === 0, 'bar 1 starts on Eb');
check(p.barBytes.length === 9 * p.form.length && p.barBytes[0] === 0 && p.barBytes[1] === Q.MIN7, 'bar 1 chord: Cm7');
// ties join into one note
const tied = p.notes.find(n => n.bar === 2 && n.p === 74 && n.t === 96);
check(tied && tied.end - (2 * 192 + tied.t) > 96, 'the tied D in bars 3-4 sounds as one long note');
// swing: the same 8th on the "and" sits half way when straight and on the last triplet when swung
const straight = prepare(bs, { feel: 2, bpm: 140 }), sw = prepare(bs, { feel: 0, bpm: 140, swing: 2 });
const k = straight.notes.findIndex(n => n.t % 48 === 24);
check(k >= 0 && sw.notes[k].t % 48 === 32, `an offbeat 8th: straight at +24, swung at +${sw.notes[k].t % 48} (48ths of a beat)`);

// every song prepares within the engine's limits
let worst = 0, notes = 0;
for (const s of songs) { const q = prepare(s, { feel: 0, bpm: 140 }); worst = Math.max(worst, q.form.length); notes = Math.max(notes, q.notes.length); }
check(worst <= 512 && notes <= 12000, `longest form ${worst} bars (engine: 512), most notes ${notes} (engine: 12000)`);
console.log(fails ? `${fails} FAILED` : 'all passed');
process.exit(fails ? 1 : 0);
