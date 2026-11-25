// player.js - the play-along player: the Jazz Band engine (WebAssembly, in an AudioWorklet) plays the chords with a
// band and the melody with a lead voice, from lead sheets parsed out of the fake-book PDFs (leads/*.json).
import { prepare, transposeSym, prettySym, KEY_NAMES, keyTonic, normTitle } from './songform.js';

const INST = ['Drums', 'Bass', 'Piano', 'Guitar'];
const PLAYERS = [
  ['Jimmy Cobb', 'Philly Joe Jones', 'Elvin Jones', 'Paul Motian', 'Art Blakey', 'Tony Williams', 'Roy Haynes', 'Billy Higgins',
   'Jo Jones', 'Jack DeJohnette', 'Max Roach', 'Airto'],
  ['Paul Chambers', 'Ray Brown', 'Ron Carter', 'Scott LaFaro', 'Charles Mingus', 'Sam Jones', 'Charlie Haden', 'Jimmy Garrison',
   'Walter Page', 'Oscar Pettiford'],
  ['Bill Evans', 'Red Garland', 'McCoy Tyner', 'Herbie Hancock', 'Thelonious Monk', 'Wynton Kelly', 'Count Basie', 'Ahmad Jamal',
   'Horace Silver', 'Erroll Garner'],
  ['Freddie Green', 'Jim Hall', 'Wes Montgomery', 'Kenny Burrell', 'Django Reinhardt', 'Grant Green', 'Herb Ellis', 'Barney Kessel',
   'Charlie Christian', 'João Gilberto (regular guitar here)'],
];
const LVL_DEFAULT = [23593, 26214, 20971, 11796];       // the plugin's defaults (Q15) for drums, bass, piano, guitar
const LEAD_DEFAULT = 24000;
const PACKS = ['jbdrums', 'jbbass', 'jbpiano', 'jbguitar'];
const ROLES = [['none', 'Just listen'], ['lead', 'Melody'], ['piano', 'Piano'], ['guitar', 'Guitar'], ['bass', 'Bass'], ['drums', 'Drums']];
const HORNS = [['C', 0, 'Concert'], ['Bb', 2, 'B♭ instrument'], ['Eb', 9, 'E♭ instrument'], ['F', 7, 'F instrument']];

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem('jb.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('jb.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------------------------------------------------------------------------------------------- the engine
let ctx = null, node = null, gain = null, engineReady = null, packsOk = [0, 0, 0, 0];
let onPos = () => {};
async function ensureEngine(status) {
  if (engineReady) return engineReady;
  engineReady = (async () => {
    status('Loading the band…');
    ctx = new AudioContext({ sampleRate: 44100, latencyHint: 'interactive' });
    await ctx.audioWorklet.addModule('engine/worklet.js');
    node = new AudioWorkletNode(ctx, 'jazzband', { numberOfInputs: 0, outputChannelCount: [2] });
    gain = ctx.createGain(); gain.gain.value = 0.9;
    node.connect(gain).connect(ctx.destination);
    const ready = new Promise(res => {
      node.port.onmessage = (e) => {
        const d = e.data;
        if (d.t === 'ready') res();
        else if (d.t === 'pos') onPos(d);
        else if (d.t === 'packs') packsOk = d.ok;
      };
    });
    const wasm = await (await fetch('engine/jazzband.wasm')).arrayBuffer();
    node.port.postMessage({ t: 'wasm', bytes: wasm }, [wasm]);
    await ready;
    let got = 0;
    await Promise.all(PACKS.map(async (p) => {
      try {
        const b = await (await fetch(`engine/packs/${p}.jbp`)).arrayBuffer();
        node.port.postMessage({ t: 'pack', bytes: b }, [b]);
        got++; status(`Loading the band… ${got}/${PACKS.length} instruments`);
      } catch { /* that instrument plays its stand-in synth */ }
    }));
    // the engine checks each pack (a checksum, a few KB per audio block): wait for it, at most a few seconds
    await ctx.resume();
    for (let i = 0; i < 40 && packsOk.reduce((a, b) => a + b, 0) < got; i++) {
      node.port.postMessage({ t: 'packs' });
      await new Promise(r => setTimeout(r, 100));
    }
    status('');
  })();
  return engineReady;
}
const post = (m) => node && node.port.postMessage(m);

// ---------------------------------------------------------------------------------------------- the player
export class Player {
  constructor(root, books) {
    this.root = root;
    this.books = books;                                  // id -> {title, file, offset}
    this.song = null; this.prep = null;
    this.state = { playing: false, bar: -1, beat: 0, chorus: 1 };
    this.startSrc = -1; this.loop = null;
    this.s = Object.assign({
      tempo: null, key: 0, feel: 0, choruses: 3, intro: 0, ending: 0, role: 'none', guide: false, leadEvery: false,
      players: [0, 0, 0, 0], levels: [100, 100, 100, 100], lead: 100, leadSound: 0, leadOct: 0, horn: 'C', view: 'chart',
    }, store.get('settings', {}));
    this.s.tempo = null; this.s.key = 0;
    onPos = (d) => this.position(d);
    document.addEventListener('keydown', (e) => {
      if (!document.body.classList.contains('playing-view')) return;
      if (e.code === 'Space' && !/INPUT|SELECT|TEXTAREA|BUTTON/.test(document.activeElement.tagName)) { e.preventDefault(); this.toggle(); }
    });
  }
  save() { const { tempo, key, ...rest } = this.s; store.set('settings', rest); }

  open(song) {
    if (this.state.playing) this.stop();
    this.song = song;
    this.s.tempo = song.tempo || defaultTempo(song);
    this.s.key = 0; this.startSrc = -1; this.loop = null;
    this.s.feel = song.feel ?? guessFeel(song);
    this.render();
    this.reprep();
    document.body.classList.add('playing-view');
    window.scrollTo(0, 0);
  }
  close() { this.stop(); document.body.classList.remove('playing-view'); }

  reprep() {
    this.prep = prepare(this.song, { feel: this.s.feel, bpm: this.s.tempo });
    this.drawChart();
    if (node) this.sendSong();
  }
  sendSong() {
    const p = this.prep;
    post({ t: 'song', bars: p.barBytes, nbars: p.form.length, beats: p.beats, bpm: this.s.tempo, feel: this.s.feel, lead: p.lead, pick: p.pick });
    this.sendAll();
  }
  sendAll() {
    if (!node) return;
    const s = this.s;
    post({ t: 'tempo', bpm: s.tempo });
    post({ t: 'feel', feel: s.feel });
    post({ t: 'transpose', semis: s.key });
    s.players.forEach((who, inst) => post({ t: 'player', inst, who }));
    this.sendLevels();
    post({ t: 'form', choruses: s.choruses, ending: s.ending, intro: s.intro, turn: 0 });
    post({ t: 'leadmask', mask: this.leadMask() });
    this.sendLoop();
  }
  sendLevels() {
    const s = this.s, muted = { piano: 2, guitar: 3, bass: 1, drums: 0 }[s.role];
    LVL_DEFAULT.forEach((d, i) => post({ t: 'level', inst: i, value: i === muted ? 1 : Math.min(32767, Math.round(d * s.levels[i] / 100)) }));
    let lead = Math.round(LEAD_DEFAULT * s.lead / 100);
    if (s.role === 'lead') lead = s.guide ? Math.round(lead * 0.22) : 0;
    post({ t: 'leadsound', sound: s.leadSound, level: lead, oct: s.leadOct });
  }
  leadMask() {
    const n = this.s.choruses;
    if (this.s.leadEvery || n === 1) return 0xFFFFFFFF;
    if (n === 0) return 1;                               // until "finish": the head at the start
    return (1 | (1 << Math.min(31, n - 1))) >>> 0;       // the head in and out; the choruses between are for solos
  }
  sendLoop() {
    if (!node) return;
    const f = this.prep.form;
    if (this.loop) {
      const a = f.indexOf(this.loop[0]);
      let b = -1; for (let j = Math.max(0, a); j < f.length; j++) if (f[j] === this.loop[1]) { b = j; break; }
      post({ t: 'loop', from: a, to: b < 0 ? a : b, kstep: 0 });
    } else post({ t: 'loop', from: -1, to: -1, kstep: 0 });
  }

  async toggle() { if (this.state.playing) this.stop(); else await this.play(); }
  async play() {
    try {
      await ensureEngine((m) => this.status(m));
    } catch (err) {
      this.status('This browser cannot run the band (Web Audio worklets are needed: Chrome, Edge, Firefox or Safari 15+).');
      return;
    }
    await ctx.resume();
    this.sendSong();
    const from = this.loop ? this.prep.form.indexOf(this.loop[0]) : this.startSrc >= 0 ? this.prep.form.indexOf(this.startSrc) : -1;
    post({ t: 'play', from });
    this.state.playing = true;
    this.updateTransport();
  }
  stop() { post({ t: 'stop' }); this.state.playing = false; this.state.bar = -1; this.updateTransport(); this.highlight(-1); }
  finish() { post({ t: 'finish' }); }

  position(d) {
    const was = this.state.playing;
    this.state = { playing: d.state !== 0, bar: d.bar, beat: d.beat, chorus: d.chorus, kind: d.kind, state: d.state };
    if (was !== this.state.playing) this.updateTransport();
    if (!this.state.playing) { this.highlight(-1); this.status(''); return; }
    const src = d.state === 2 ? (this.prep.pickup ? 'pickup' : -1) : d.kind === 0 ? this.prep.form[d.bar] : -2;
    const delay = Math.max(0, ((ctx.outputLatency || 0) + (ctx.baseLatency || 0)) * 1000);
    setTimeout(() => this.highlight(src, d.beat), delay);
    const n = this.s.choruses;
    const what = d.state === 2 ? 'Count-in' : d.kind === 5 ? 'Intro' : d.kind === 0 ? `Chorus ${d.chorus}${n ? ' of ' + n : ''}` : 'Ending';
    const solo = d.kind === 0 && !((this.leadMask() >>> (Math.min(32, d.chorus) - 1)) & 1);
    this.status(`${what}${d.kind === 0 ? ' · bar ' + (this.prep.form[d.bar] + 1) : ''}${solo ? ' · solos' : d.kind === 0 ? ' · head' : ''}`);
  }
  status(m) { const el = $('.pl-status', this.root); if (el) el.textContent = m; }
  updateTransport() {
    const b = $('.pl-play', this.root);
    if (!b) return;
    b.textContent = this.state.playing ? '■ Stop' : '▶ Play';
    b.classList.toggle('stop', this.state.playing);
    $('.pl-finish', this.root).disabled = !this.state.playing;
  }

  // ------------------------------------------------------------------------------------------ view
  render() {
    const song = this.song, s = this.s, book = this.books[song.book] || {};
    const tonic = keyTonic(song.key || 0);
    const keyOpts = [];
    for (let k = -6; k <= 6; k++) keyOpts.push(`<option value="${k}" ${k === s.key ? 'selected' : ''}>${KEY_NAMES[(tonic + k + 120) % 12]}${k === 0 ? ' (written)' : (k > 0 ? ' +' : ' ') + k}</option>`);
    const problems = song.problems && song.problems.length;
    this.root.innerHTML = `
      <div class="pl-top">
        <button class="pl-back" type="button">← Library</button>
        <div class="pl-title">
          <h2>${esc(song.title)}</h2>
          <div class="meta">${esc(song.composer || '')}${song.composer ? ' · ' : ''}${esc(book.title || song.book)}, page
            <a href="${encodeURI(book.file || '')}#page=${song.page}" target="_blank" rel="noopener">${song.page - (book.offset || 0)}</a>
            · ${song.time[0]}/${song.time[1]} · ${this.prep ? '' : ''}${song.bars.length} bars
            ${problems ? `<span class="warn" title="${esc(song.problems.join('\n'))}"> · ${problems} bar${problems > 1 ? 's' : ''} may be misread</span>` : ''}</div>
        </div>
      </div>
      <div class="pl-bar">
        <button class="pl-play" type="button">▶ Play</button>
        <button class="pl-btn pl-finish" type="button" disabled title="Play the ending after this chorus">Finish</button>
        <span class="pl-status" role="status"></span>
        <label>Tempo <input type="number" class="in-tempo" min="40" max="320" value="${s.tempo}">
          <input type="range" class="in-tempo-r" min="40" max="320" value="${s.tempo}" aria-label="Tempo"></label>
        <label>Key <select class="in-key">${keyOpts.join('')}</select></label>
        <label>Feel <select class="in-feel"><option value="0">Swing</option><option value="1">Ballad</option><option value="2">Bossa / straight</option></select></label>
      </div>
      <div class="pl-bar">
        <span class="pl-field">You play</span>
        <span class="pl-seg seg-role">${ROLES.map(([v, t]) => `<button type="button" data-v="${v}" class="${s.role === v ? 'on' : ''}">${t}</button>`).join('')}</span>
        <label class="lbl-guide" title="The melody stays in, quietly, to guide you"><input type="checkbox" class="in-guide" ${s.guide ? 'checked' : ''}> Guide melody</label>
        <label>Choruses <select class="in-chor">${[1, 2, 3, 4, 5, 6, 8].map(n => `<option value="${n}">${n}</option>`).join('')}<option value="0">Until Finish</option></select></label>
        <label title="Off: the melody is played in the first and last chorus, the choruses between are for solos"><input type="checkbox" class="in-every" ${s.leadEvery ? 'checked' : ''}> Melody every chorus</label>
      </div>
      <details class="pl-more">
        <summary>Band, mixer and arrangement</summary>
        <div class="pl-grid">
          ${INST.map((n, i) => `<label>${n}<select class="in-player" data-i="${i}">${PLAYERS[i].map((p, k) => `<option value="${k}" ${s.players[i] === k ? 'selected' : ''}>${p}</option>`).join('')}</select></label>`).join('')}
          <label>Lead sound<select class="in-leadsound"><option value="0">Horn (synth)</option><option value="1">Piano</option><option value="2">Guitar</option><option value="3">Vibes</option></select></label>
          <label>Lead octave<select class="in-leadoct"><option value="-1">Down</option><option value="0">As written</option><option value="1">Up</option></select></label>
          <label>Count-in<select class="in-intro"><option value="0">One bar count</option><option value="1">Last 4 bars</option><option value="2">Turnaround intro</option></select></label>
          <label>Ending<select class="in-ending"><option value="0">Final chord</option><option value="1">Tag (3 times)</option></select></label>
          <label>Chords for<select class="in-horn">${HORNS.map(([v, , t]) => `<option value="${v}">${t}</option>`).join('')}</select></label>
          <label>Melody<input type="range" class="in-level" data-i="lead" min="0" max="150" value="${s.lead}"><output>${s.lead}%</output></label>
          ${INST.map((n, i) => `<label>${n}<input type="range" class="in-level" data-i="${i}" min="0" max="150" value="${s.levels[i]}"><output>${s.levels[i]}%</output></label>`).join('')}
        </div>
      </details>
      <div class="pl-tabs">
        <span class="pl-seg seg-view"><button type="button" data-v="chart">Chart</button><button type="button" data-v="page">Original page</button></span>
        <button class="pl-btn btn-loop" type="button" title="Loop the bars you selected">Loop</button>
        <button class="pl-btn btn-clear" type="button">Clear selection</button>
        <span class="pl-hint">Click a bar to start there · Shift-click a second bar to choose a loop · Space plays / stops</span>
      </div>
      <div class="pl-view"></div>`;
    const r = this.root;
    $('.in-feel', r).value = s.feel; $('.in-chor', r).value = s.choruses; $('.in-leadsound', r).value = s.leadSound;
    $('.in-leadoct', r).value = s.leadOct; $('.in-intro', r).value = s.intro; $('.in-ending', r).value = s.ending; $('.in-horn', r).value = s.horn;
    $('.lbl-guide', r).style.display = s.role === 'lead' ? '' : 'none';
    $('.pl-back', r).onclick = () => { this.close(); history.pushState(null, '', location.pathname + location.search); this.onclose?.(); };
    $('.pl-play', r).onclick = () => this.toggle();
    $('.pl-finish', r).onclick = () => this.finish();
    const setTempo = (v) => {
      v = Math.max(40, Math.min(320, Math.round(+v || 120))); s.tempo = v;
      $('.in-tempo', r).value = v; $('.in-tempo-r', r).value = v; post({ t: 'tempo', bpm: v });
    };
    $('.in-tempo', r).onchange = (e) => setTempo(e.target.value);
    $('.in-tempo-r', r).oninput = (e) => setTempo(e.target.value);
    $('.in-tempo-r', r).onchange = () => { if (!this.state.playing) this.reprep(); };
    $('.in-key', r).onchange = (e) => { s.key = +e.target.value; post({ t: 'transpose', semis: s.key }); this.drawChart(); };
    $('.in-feel', r).onchange = (e) => { s.feel = +e.target.value; this.save(); this.reprep(); };
    $('.seg-role', r).onclick = (e) => {
      const b = e.target.closest('button'); if (!b) return;
      s.role = b.dataset.v; this.save();
      r.querySelectorAll('.seg-role button').forEach(x => x.classList.toggle('on', x === b));
      $('.lbl-guide', r).style.display = s.role === 'lead' ? '' : 'none';
      this.sendLevels();
    };
    $('.in-guide', r).onchange = (e) => { s.guide = e.target.checked; this.save(); this.sendLevels(); };
    $('.in-chor', r).onchange = (e) => { s.choruses = +e.target.value; this.save(); this.sendAll(); };
    $('.in-every', r).onchange = (e) => { s.leadEvery = e.target.checked; this.save(); post({ t: 'leadmask', mask: this.leadMask() }); };
    r.querySelectorAll('.in-player').forEach(el => el.onchange = () => { s.players[+el.dataset.i] = +el.value; this.save(); post({ t: 'player', inst: +el.dataset.i, who: +el.value }); });
    $('.in-leadsound', r).onchange = (e) => { s.leadSound = +e.target.value; this.save(); this.sendLevels(); };
    $('.in-leadoct', r).onchange = (e) => { s.leadOct = +e.target.value; this.save(); this.sendLevels(); };
    $('.in-intro', r).onchange = (e) => { s.intro = +e.target.value; this.save(); this.sendAll(); };
    $('.in-ending', r).onchange = (e) => { s.ending = +e.target.value; this.save(); this.sendAll(); };
    $('.in-horn', r).onchange = (e) => { s.horn = e.target.value; this.save(); this.drawChart(); };
    r.querySelectorAll('.in-level').forEach(el => el.oninput = () => {
      const v = +el.value; el.nextElementSibling.textContent = v + '%';
      if (el.dataset.i === 'lead') s.lead = v; else s.levels[+el.dataset.i] = v;
      this.save(); this.sendLevels();
    });
    $('.seg-view', r).onclick = (e) => { const b = e.target.closest('button'); if (!b) return; s.view = b.dataset.v; this.save(); this.drawChart(); };
    $('.btn-loop', r).onclick = () => {
      if (this.loop) { this.loopOff(); return; }
      if (this.sel) { this.loop = this.sel.slice(); this.sendLoop(); this.drawChart(); }
    };
    $('.btn-clear', r).onclick = () => { this.sel = null; this.startSrc = -1; this.loopOff(); };
    this.updateTransport();
  }
  loopOff() { this.loop = null; this.sendLoop(); this.drawChart(); }

  chordLabel(sym) {
    const horn = HORNS.find(h => h[0] === this.s.horn) || HORNS[0];
    const semis = this.s.key + horn[1];
    const flats = ![2, 4, 7, 9, 11].includes(((keyTonic(this.song.key || 0) + semis) % 12 + 12) % 12) || (this.song.key || 0) < 0;
    return prettySym(transposeSym(sym, ((semis % 12) + 12) % 12, flats));
  }

  drawChart() {
    const r = this.root, view = $('.pl-view', r);
    if (!view || !this.prep) return;
    r.querySelectorAll('.seg-view button').forEach(b => b.classList.toggle('on', b.dataset.v === this.s.view));
    const lb = $('.btn-loop', r);
    lb.classList.toggle('on', !!this.loop); lb.textContent = this.loop ? 'Loop on' : 'Loop';
    lb.disabled = !this.loop && !this.sel;
    if (this.s.view === 'page') { this.drawPages(view); return; }
    const p = this.prep, song = this.song, barLen = p.barLen;
    // pitch range for the little melody rolls
    let lo = 127, hi = 0;
    for (const b of song.bars) for (let k = 2; k < b.n.length; k += 3) if (b.n[k]) { const q = b.n[k] & 127; lo = Math.min(lo, q); hi = Math.max(hi, q); }
    if (lo > hi) { lo = 60; hi = 72; }
    const span = Math.max(8, hi - lo + 1);
    const barHTML = (b, i, cls, len) => {
      const L = len || barLen;
      const chords = b.c.map(c => `<span style="left:${(c[0] / L * 100).toFixed(1)}%">${esc(this.chordLabel(c[1]))}</span>`).join('');
      let rects = '';
      for (let k = 0; k < b.n.length; k += 3) {
        const t = b.n[k], d = b.n[k + 1], q = b.n[k + 2];
        if (!q) continue;
        const y = (hi - (q & 127)) / span * 93;
        rects += `<rect x="${(t / L * 100).toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(0.8, d / L * 100 - 0.8).toFixed(2)}" height="${Math.max(7, 100 / span * 0.9).toFixed(2)}" rx="0.6"/>`;
      }
      const tags = [b.sg && b.sg.includes('segno') ? '𝄋' : '', b.sg && b.sg.includes('coda') ? '𝄌' : '', b.ds ? 'D.S.' : '', b.dc ? 'D.C.' : '', b.fn ? 'Fine' : ''].filter(Boolean).join(' ');
      const volta = b.e ? `<div class="volta">${b.e.join(', ')}.</div>` : '';
      const classes = ['bar', cls, b.rs ? 'rs' : '', b.re ? 're' : '', b.db ? 'db' : ''].filter(Boolean).join(' ');
      return `<div class="${classes}" data-src="${i}">${volta}<div class="chords">${chords}</div>
        <svg class="roll" viewBox="0 0 100 100" preserveAspectRatio="none">${rects}</svg>${tags ? `<span class="tag">${tags}</span>` : ''}</div>`;
    };
    // rows: the systems of the printed page
    const rows = [];
    const bodyStart = song.pickup ? 1 : 0;
    let cur = null, lastKey = '';
    song.bars.forEach((b, k) => {
      const key = b.box[0] + ':' + b.box[2];
      if (key !== lastKey) { cur = []; rows.push(cur); lastKey = key; }
      cur.push(k);
    });
    let html = '<div class="chart">';
    for (const row of rows) {
      const first = row.find(k => k >= bodyStart) - bodyStart;
      const sec = first >= 0 && p.section[first] ? String.fromCharCode(p.section[first]) : '';
      html += `<div class="chart-row"><span class="sec">${sec}</span>`;
      for (const k of row) {
        if (k < bodyStart) html += barHTML(song.bars[k], 'pickup', 'pickup', song.bars[k].len || barLen);
        else html += barHTML(song.bars[k], k - bodyStart, '');
      }
      html += '</div>';
    }
    html += '</div>';
    view.innerHTML = html;
    view.querySelectorAll('.bar').forEach(el => el.onclick = (e) => this.barClick(el.dataset.src, e.shiftKey));
    this.markSelection();
  }
  barClick(src, shift) {
    if (src === 'pickup') { this.startSrc = -1; this.sel = null; this.markSelection(); return; }
    src = +src;
    if (shift && this.sel) this.sel = [Math.min(this.sel[0], src), Math.max(this.sel[0], src)];
    else this.sel = [src, src];
    this.startSrc = this.sel[0];
    const lb = $('.btn-loop', this.root); lb.disabled = false;
    if (this.loop) { this.loop = this.sel.slice(); this.sendLoop(); }
    this.markSelection();
  }
  markSelection() {
    const view = $('.pl-view', this.root); if (!view) return;
    const range = this.loop || this.sel;
    view.querySelectorAll('.bar, .clickbar').forEach(el => {
      const s = +el.dataset.src;
      el.classList.toggle('inloop', !!range && el.dataset.src !== 'pickup' && s >= range[0] && s <= range[1]);
      el.classList.toggle('start', el.dataset.src !== 'pickup' && s === this.startSrc && !this.loop);
    });
  }
  highlight(src, beat) {
    const view = $('.pl-view', this.root); if (!view) return;
    view.querySelectorAll('.bar.now').forEach(el => { el.classList.remove('now'); const b = el.querySelector('.beat'); if (b) b.remove(); });
    if (this.s.view === 'page') { this.pageHighlight(src); return; }
    if (src === -1 || src === -2) return;
    const el = view.querySelector(`.bar[data-src="${src}"]`);
    if (!el) return;
    el.classList.add('now');
    const beats = this.prep.beats;
    const bt = document.createElement('div'); bt.className = 'beat';
    bt.style.left = ((beat || 0) / beats * 100) + '%'; bt.style.width = (100 / beats) + '%';
    el.appendChild(bt);
    const rect = el.getBoundingClientRect();
    if (this.state.playing && (rect.top < 80 || rect.bottom > innerHeight - 20)) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  // ------------------------------------------------------------------------------------------ the original page
  async drawPages(view) {
    const song = this.song, book = this.books[song.book];
    view.innerHTML = '<div class="pl-loading">Opening the page…</div>';
    try {
      const pdfjs = await loadPdfJs();
      const doc = await getDoc(pdfjs, book.file);
      if (this.s.view !== 'page' || this.song !== song) return;
      const wrap = document.createElement('div'); wrap.className = 'pdfwrap';
      view.innerHTML = ''; view.appendChild(wrap);
      this.pageEls = {};
      for (const pg of song.pages) {
        const page = await doc.getPage(pg);
        const vp1 = page.getViewport({ scale: 1 });
        const box = document.createElement('div'); box.className = 'pdfpage';
        const canvas = document.createElement('canvas');
        const w = Math.min(860, view.clientWidth || 860), scale = (w / vp1.width) * (window.devicePixelRatio || 1);
        const vp = page.getViewport({ scale });
        canvas.width = vp.width; canvas.height = vp.height;
        box.appendChild(canvas); wrap.appendChild(box);
        await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
        this.pageEls[pg] = { box, w: vp1.width, h: vp1.height };
        // clickable bars on the page
        const bodyStart = song.pickup ? 1 : 0;
        song.bars.forEach((b, k) => {
          if (b.box[0] !== pg || k < bodyStart) return;
          const el = document.createElement('div'); el.className = 'clickbar'; el.dataset.src = k - bodyStart;
          place(el, b.box, vp1.width, vp1.height);
          el.onclick = (e) => this.barClick(k - bodyStart, e.shiftKey);
          box.appendChild(el);
        });
      }
      this.markSelection();
    } catch (err) {
      view.innerHTML = `<div class="pl-loading">Could not open the PDF (${esc(err.message || err)}).</div>`;
    }
  }
  pageHighlight(src) {
    if (!this.pageEls) return;
    Object.values(this.pageEls).forEach(p => { const h = p.box.querySelector('.hl'); if (h) h.remove(); });
    if (src === -1 || src === -2) return;
    const bar = src === 'pickup' ? this.song.bars[0] : this.prep.body[src];
    if (!bar) return;
    const p = this.pageEls[bar.box[0]]; if (!p) return;
    const h = document.createElement('div'); h.className = 'hl';
    place(h, bar.box, p.w, p.h);
    p.box.appendChild(h);
    const r = h.getBoundingClientRect();
    if (this.state.playing && (r.top < 60 || r.bottom > innerHeight - 20)) h.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}
function place(el, box, w, h) {
  const [, x0, y0, x1, y1] = box, sp = (y1 - y0) / 4;
  const top = y0 - sp * 5.5, bot = y1 + sp * 2.2;
  el.style.left = (x0 / w * 100) + '%'; el.style.width = ((x1 - x0) / w * 100) + '%';
  el.style.top = (top / h * 100) + '%'; el.style.height = ((bot - top) / h * 100) + '%';
}
let pdfjsP = null; const docs = {};
function loadPdfJs() {
  if (pdfjsP) return pdfjsP;
  pdfjsP = new Promise((res, rej) => {
    const s = document.createElement('script'); s.src = 'vendor/pdfjs/pdf.min.js';
    s.onload = () => { window.pdfjsLib.GlobalWorkerOptions.workerSrc = 'vendor/pdfjs/pdf.worker.min.js'; res(window.pdfjsLib); };
    s.onerror = () => rej(new Error('PDF viewer did not load'));
    document.head.appendChild(s);
  });
  return pdfjsP;
}
function getDoc(pdfjs, file) {
  if (!docs[file]) docs[file] = pdfjs.getDocument({ url: file, disableAutoFetch: true, rangeChunkSize: 262144 }).promise;
  return docs[file];
}
function defaultTempo(song) {
  const t = (song.title || '').toLowerCase();
  if (/ballad|ruby my dear|body and soul|'round midnight|lush life|in a sentimental|my funny|misty|stardust|nature boy|naima|skylark|you don't know what/.test(t)) return 72;
  if (/bossa|samba|blue bossa|girl from|desafinado|corcovado|felicidade|black or|wave|meditation|insensatez|so danço|triste|manha/.test(t)) return 132;
  return 140;
}
function guessFeel(song) {
  const t = (song.title || '').toLowerCase();
  if (/bossa|samba|girl from|desafinado|corcovado|felicidade|black or|wave|meditation|insensatez|manha|triste/.test(t)) return 2;
  if (/ballad|ruby my dear|body and soul|lush life|my funny|misty|stardust|skylark|naima|you don't know what|round midnight/.test(t)) return 1;
  return 0;
}
export { normTitle };
