/* The Jazz Band engine (jazzband.wasm, built from jazzband-op1r/web/jb_web.c) inside an AudioWorklet.
 * The page posts the wasm bytes, the sound packs and the song; the band renders 128 frames per call at 44.1 kHz and
 * posts its position whenever the bar, beat, chorus or state changes. */
class JazzBand extends AudioWorkletProcessor {
  constructor() {
    super();
    this.X = null;
    this.last = '';
    this.port.onmessage = (e) => this.msg(e.data);
  }
  heap() { return new Uint8Array(this.X.memory.buffer); }
  msg(d) {
    if (d.t === 'wasm') {
      const mod = new WebAssembly.Module(d.bytes);
      this.X = new WebAssembly.Instance(mod, {}).exports;
      this.X.jbw_init();
      this.port.postMessage({ t: 'ready', players: [0, 1, 2, 3].map(i => this.X.jbw_nplayers(i)) });
      return;
    }
    const X = this.X;
    if (!X) return;
    switch (d.t) {
      case 'pack': {
        const p = X.jbw_alloc(d.bytes.byteLength);
        if (!p) { this.port.postMessage({ t: 'error', msg: 'sound pack does not fit' }); return; }
        this.heap().set(new Uint8Array(d.bytes), p);
        X.jbw_add_pack(p);
        break;
      }
      case 'song': {
        this.heap().set(d.bars, X.jbw_bars());
        X.jbw_load(d.nbars, d.beats, d.bpm, d.feel);
        const n = Math.min(d.lead.length / 8, X.jbw_lead_max());
        this.heap().set(d.lead.subarray(0, n * 8), X.jbw_lead_buf());
        this.heap().set(d.pick, X.jbw_pick_buf());
        X.jbw_lead_set(n, d.pick.length / 8);
        this.last = '';
        break;
      }
      case 'play': X.jbw_play(d.from ?? -1); break;
      case 'stop': X.jbw_stop(); break;
      case 'finish': X.jbw_finish(); break;
      case 'tempo': X.jbw_tempo(d.bpm); break;
      case 'feel': X.jbw_feel(d.feel); break;
      case 'transpose': X.jbw_transpose(d.semis); break;
      case 'player': X.jbw_player(d.inst, d.who); break;
      case 'level': X.jbw_level(d.inst, d.value); break;
      case 'form': X.jbw_form(d.choruses, d.ending, d.intro, d.turn); break;
      case 'loop': X.jbw_loop(d.from, d.to, d.kstep || 0); break;
      case 'leadmask': X.jbw_lead_mask(d.mask >>> 0); break;
      case 'leadsound': X.jbw_lead_sound(d.sound, d.level, d.oct); break;
      case 'packs': this.port.postMessage({ t: 'packs', ok: [0, 1, 2, 3].map(i => X.jbw_pack_ok(i)) }); break;
    }
  }
  process(inputs, outputs) {
    const out = outputs[0];
    const X = this.X;
    if (!X || !out || !out[0] || out[0].length !== 128) return true;
    X.jbw_render();
    const buf = X.memory.buffer;
    out[0].set(new Float32Array(buf, X.jbw_out_l(), 128));
    if (out[1]) out[1].set(new Float32Array(buf, X.jbw_out_r(), 128));
    const st = X.jbw_state(), bar = X.jbw_bar(), shift = X.jbw_shift();
    const key = st + ':' + bar + ':' + shift;
    if (key !== this.last) {
      this.last = key;
      this.port.postMessage({ t: 'pos', state: st & 15, kind: (st >> 4) & 15, beat: (st >> 8) & 15, chorus: (st >> 16) & 255,
                              bar, shift, time: currentTime });
    }
    return true;
  }
}
registerProcessor('jazzband', JazzBand);
