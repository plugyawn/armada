/* Armada sound, as specified in SPEC-DESIGN.md section 9. One bar of hardwood struck with felt for people's events; the
   same pitches gated on and off for Onix. Pure DSP, rendered once into buffers when sound is turned on and normalised so
   that the loudest 100 ms of each sound sits at its tier. The engine does not exist while muted. Recipes are the round-2
   ones, unchanged, with two moves that keep the default set at eight events: my reaction landing joins it, and the
   acknowledgement leaves it for Full (the mark is drawn in the send task, and with an agent that answers in 300 ms a
   second sound 120 ms after send is chatter). Nobody has heard them yet.
   S.set(true)  S.play("send")  S.play("ack", 0.12)  S.table()  S.log()

   The limiter, as built (third final):
   - What I did always sounds: send, steer, queue, nope and my reaction are never refused, whatever else is playing.
   - Of everything else, at most 3 in any 2 s. When a fourth is due, the lowest priority gives way: a newcomer that is
     the lowest is not played; otherwise the lowest one still sounding is faded out in 10 ms and the newcomer plays.
     My own sounds are not counted and are never the one that gives way.
   - Under a fast exchange it is the consequent that goes quiet, on a rule the person can predict: after two
     exchanges inside 4 s, done-short is silent until there have been 4 s without a send.
   - Repeats of my own send inside 4 s fall 4 dB each, to a floor of -8 dB.
   - The 30 ms rule: a sound that cannot start within 30 ms of its event is not played at all (a suspended context, a
     long task). It is never played late, and nothing is queued for when the context wakes. */
(() => {
const SR = 44100, db = (x) => 10 ** (x / 20), NOTE = {D4: 293.66, F4: 349.23, A4: 440, D5: 587.33, E5: 659.25, Fs5: 739.99, A5: 880, B5: 987.77, D6: 1174.66};
let rseed = 1; const rnd = () => { rseed = (rseed * 1664525 + 1013904223) >>> 0; return rseed / 2 ** 31 - 1; };
function biquad(type, f0, q) { const w = 2 * Math.PI * f0 / SR, c = Math.cos(w), a = Math.sin(w) / (2 * q); let b0, b1, b2; if (type === "lp") { b0 = (1 - c) / 2; b1 = 1 - c; b2 = (1 - c) / 2; } else { b0 = a; b1 = 0; b2 = -a; }
  const a0 = 1 + a, a1 = -2 * c, a2 = 1 - a; let x1 = 0, x2 = 0, y1 = 0, y2 = 0; return (x) => { const y = (b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0; x2 = x1; x1 = x; y2 = y1; y1 = y; return y; }; }
function bar(f, D, {lp = 0, mallet = -16} = {}) { const n = Math.round((D + 0.02) * SR), out = new Float32Array(n), P = [[1, 0, 1], [4, -15, 0.3], [9.2, -27, 0.12]].filter((p) => p[0] * f < 7000);
  for (let i = 0; i < n; i++) { const t = i / SR, att = t < 0.003 ? 0.5 - 0.5 * Math.cos(Math.PI * t / 0.003) : 1; let s = 0; for (const [r, g, d] of P) s += db(g) * Math.sin(2 * Math.PI * f * r * t) * Math.exp(-6.9 * t / (D * d)); out[i] = s * att; }
  if (mallet > -90) { const bp = biquad("bp", Math.min(3000, Math.max(1200, 3 * f)), 1.2); for (let i = 0; i < Math.round(0.02 * SR); i++) { const t = i / SR; out[i] += db(mallet) * 3 * bp(rnd()) * Math.min(1, t / 0.001) * Math.exp(-6.9 * t / 0.014); } }
  if (lp) { const F = biquad("lp", lp, 0.7); for (let i = 0; i < n; i++) out[i] = F(out[i]); }
  return out; }
function chip(f, len) { const n = Math.round((len + 0.02) * SR), out = new Float32Array(n), F = biquad("lp", 2200, 0.7);
  for (let i = 0; i < n; i++) { const t = i / SR, ph = (f * t) % 1, tri = 4 * Math.abs(ph - 0.5) - 1, sq = ph < 0.5 ? 1 : -1, env = t < 0.002 ? t / 0.002 : t < len ? 1 : Math.max(0, 1 - (t - len) / 0.012); out[i] = F((tri + db(-16) * sq) * env); }
  return out; }
const RECIPE = {   /* [tier dBFS, priority, set, parts: [make, start s, relative dB]] */
  send: [-32, 1, "default", [[() => bar(NOTE.E5, 0.11), 0, 0]]],
  steer: [-32, 1, "default", [[() => bar(NOTE.E5, 0.11), 0, 0], [() => chip(NOTE.A5, 0.04), 0.055, -8]]],
  queue: [-32, 1, "default", [[() => bar(NOTE.E5, 0.11), 0, 0], [() => bar(NOTE.A4, 0.09, {lp: 1400, mallet: -90}), 0.075, -5]]],   /* the send note, then the same bar set down a fifth below: put aside, not delivered */
  ack: [-34, 0, "full", [[() => chip(NOTE.D5, 0.04), 0, 0], [() => chip(NOTE.A5, 0.05), 0.06, 0]]],
  nope: [-32, 1, "default", [[() => bar(NOTE.D4, 0.06, {lp: 700, mallet: -10}), 0, 0], [() => bar(NOTE.D4, 0.06, {lp: 700, mallet: -10}), 0.09, -2]]],
  ask: [-22, 3, "default", [[() => bar(NOTE.E5, 0.26), 0, 0], [() => bar(NOTE.B5, 0.30), 0.13, 0]]],
  fail: [-22, 3, "default", [[() => bar(NOTE.A4, 0.24, {lp: 1200}), 0, 0], [() => bar(NOTE.F4, 0.28, {lp: 1200}), 0.14, -2]]],
  done: [-22, 2, "default", [[() => chip(NOTE.D5, 0.045), 0, -6], [() => chip(NOTE.Fs5, 0.045), 0.07, -6], [() => chip(NOTE.A5, 0.045), 0.14, -6], [() => bar(NOTE.D6, 0.42), 0.21, 0]]],
  "done-short": [-32, 0, "default", [[() => chip(NOTE.A5, 0.04), 0, -6], [() => bar(NOTE.D6, 0.2), 0.07, 0]]],
  mention: [-20, 3, "default", [[() => bar(NOTE.D5, 0.38), 0, 0], [() => bar(NOTE.A5, 0.38), 0, -3], [() => bar(NOTE.D6, 0.40), 0.12, -2]]],
  arrive: [-36, 0, "full", [[() => bar(NOTE.Fs5, 0.12, {mallet: -90}), 0, 0]]],
  react: [-36, 0, "default", [[() => bar(NOTE.B5, 0.06), 0, 0]]],
  /* a partner evolving (only for its person): one soft tick for each trade of outlines, faster and faster, then D5, F#5, A5 and a
     quiet D6 as the new form lands; about 1.4 s, between done and send. Its own drawing, none of the games' audio. */
  evolve: [-28, 2, "default", [...[0.534, 0.696, 0.826, 0.923, 0.996, 1.053, 1.102].map((t) => [() => bar(NOTE.A5, 0.03, {lp: 2400, mallet: -90}), t, -16]),   /* the trades (partner_card.js, playMoment) */
    [() => bar(NOTE.D5, 0.3), 1.38, 0], [() => bar(NOTE.Fs5, 0.3), 1.44, -1], [() => bar(NOTE.A5, 0.32), 1.5, -2], [() => bar(NOTE.D6, 0.42), 1.56, -8]]],
};
const AUDITION = 12;   /* the reel's +12 dB, so laptop speakers carry it; the shipped levels are the tiers */
const FULL = new URLSearchParams(location.search).get("sounds") === "full";
function loudest100(b) { const W = Math.round(0.1 * SR); let best = 0, acc = 0; for (let i = 0; i < b.length; i++) { acc += b[i] * b[i]; if (i >= W) acc -= b[i - W] * b[i - W]; if (acc > best) best = acc; } return Math.sqrt(best / W); }
function renderSound(name) { rseed = 1; const [tier, , , parts] = RECIPE[name], b = new Float32Array(Math.round(Math.max(0.8, Math.max(...parts.map((x) => x[1])) + 0.6) * SR));
  for (const [make, t0, g] of parts) { const a = make(), o = Math.round(t0 * SR), k = db(g); for (let i = 0; i < a.length && o + i < b.length; i++) b[o + i] += a[i] * k; }
  const gain = db(tier) / loudest100(b); for (let i = 0; i < b.length; i++) b[i] *= gain; return b; }
function soundTable() { return Object.keys(RECIPE).map((n) => { const b = renderSound(n); let peak = 0, last = 0; for (let i = 0; i < b.length; i++) { const a = Math.abs(b[i]); if (a > peak) peak = a; } for (let i = 0; i < b.length; i++) if (Math.abs(b[i]) > peak * 0.001) last = i;
  return {name: n, set: RECIPE[n][2], tier: RECIPE[n][0], rms100: +(20 * Math.log10(loudest100(b))).toFixed(1), peak: +(20 * Math.log10(peak)).toFixed(1), ms: Math.round(last / SR * 1000)}; }); }
let AC = null, BUF = {}, soundOn = false, sends = [], exch = [], recent = [], LOG = [], lastSend = 0, hushed = false;
const MINE = {send: 1, steer: 1, queue: 1, nope: 1, react: 1}, LATE = 30;
/* the engine runs only while sound is on: off suspends it (an audio context left running processes silence for as long as
   the tab lives), on resumes it, and the page going away closes it */
function setSound(on) { soundOn = on;
  if (on && !AC) { AC = new AudioContext({sampleRate: SR}); for (const n in RECIPE) { const d = renderSound(n), b = AC.createBuffer(1, d.length, SR); b.copyToChannel(d, 0); BUF[n] = b; } }
  if (!AC) return;
  if (!on) { if (AC.state === "running") AC.suspend(); return; }
  if (AC.state !== "running") AC.resume();
  play("send"); }
addEventListener("pagehide", (e) => { if (!AC) return; if (e.persisted) AC.suspend(); else { AC.close(); AC = null; BUF = {}; } });
addEventListener("pageshow", (e) => { if (e.persisted && AC && soundOn) AC.resume(); });
const note = (name, why, x = {}) => { LOG.push({name, t: Math.round(performance.now()), played: !why, why: why || "", ...x}); if (LOG.length > 400) LOG.shift(); return !why; };
/* play(name, delay s, at): `at` is the time of the event that caused it (performance.now()); the sound starts at at + delay or not at all */
function play(name, delay = 0, at = performance.now()) {
  const rec = RECIPE[name]; if (!soundOn || !AC || !rec || (rec[2] === "full" && !FULL)) return false;
  const t = performance.now(), mine = !!MINE[name], pri = rec[1];
  if (name === "send" || name === "steer" || name === "queue") { if (lastSend && t - lastSend > 4000) { exch = []; hushed = false; } lastSend = t; exch = exch.filter((x) => t - x < 4000); exch.push(t); if (exch.length > 2) hushed = true; }
  else if (hushed && t - lastSend > 4000) hushed = false;
  if (AC.state !== "running") { AC.resume(); return note(name, "context " + AC.state); }
  if (t - at > LATE) return note(name, `late ${Math.round(t - at)} ms`);
  if (name === "done-short" && hushed) return note(name, "fast exchange");                     /* from the third exchange inside 4 s, until 4 s without a send */
  recent = recent.filter((x) => t - x.t < 2000 && !x.cut);
  if (!mine && recent.length >= 3) {
    const low = recent.reduce((a, b) => (b.pri < a.pri || (b.pri === a.pri && b.t < a.t) ? b : a));
    if (pri <= low.pri) return note(name, "limiter: lowest priority");
    if (low.end > t) { try { low.g.gain.setTargetAtTime(0, AC.currentTime, 0.004); low.src.stop(AC.currentTime + 0.03); } catch (x) {} LOG.push({name: low.name, t: Math.round(t), played: true, cut: "gave way to " + name}); }
    low.cut = true;
  }
  let drop = 0; if (name === "send" || name === "steer" || name === "queue") { sends = sends.filter((x) => t - x < 4000); drop = Math.max(-8, -4 * sends.length); sends.push(t); }
  const src = AC.createBufferSource(), g = AC.createGain(); src.buffer = BUF[name]; g.gain.value = db(AUDITION + drop); src.connect(g).connect(AC.destination);
  src.start(AC.currentTime + delay + Math.max(0, 0.005 - (t - at) / 1000));
  if (!mine) recent.push({name, t, pri, src, g, end: t + delay * 1000 + 400}); return note(name, "", {gain: drop, mine});
}
  window.S = {set: setSound, play, table: soundTable, on: () => soundOn, names: () => Object.keys(RECIPE), log: () => LOG, clear: () => { LOG = []; sends = []; exch = []; recent = []; lastSend = 0; hushed = false; }, state: () => AC && AC.state, ctx: () => AC};
})();
