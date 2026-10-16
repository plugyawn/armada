/* Armada motion kit, script half. The one module that animates: no other file calls element.animate.
   Everything here moves transform and opacity only, starts in the task that calls it, and never holds a result back:
   callers write the new state first, then ask for the difference to be released.

   M.release(el, {x, y, sx, sy}, "move")   the one helper. Displace el from its rest by this much and let it go on the
                                           named spring. If el is already in flight it starts from where it visibly is
                                           and at the speed it is moving, so nothing jumps and nothing queues.
   M.flip(el, before, "move")              release the difference between a rect read before a write and el's rest now
   M.rest(el)                              el's rect with every spring in flight on it and its ancestors taken out
   M.fade(el, to)  M.leave(el, then, from) opacity, from its current value (or `from`); leave removes el after --fade-out
   M.thumb(thumb, target)                  slide a selection thumb to target
   M.figure(cell, "13", {quiet})           change a figure in its fixed cells; only the characters that changed move
   M.swap(cell, node, {dir})               change a glyph or a word in a fixed cell
   M.grow(el, from)                        el appears to grow out of the rect `from` without its corners bending
   M.fly(el, from, "move")                 el travels from rect `from` to its place, above every pane (top layer)
   M.land(el)  M.kick(el, {x, y})  M.squash(el)  M.burst(at, text)  M.refuse(el)
   M.cross(old, html)                      one mark becomes another in place: never a frame with neither
   M.say(target, "Copied")                 the tooltip object, used once as a short confirmation
   M.ring / M.tip                          the window's one focus ring and one tooltip (wired by M.wire). The ring
                                           follows its control: it is placed from M.rest and re-placed while it shows.
   M.warm()                                at idle after load: the first animation and the top layer are paid for early */
(() => {
  const R = document.documentElement, Q = new URLSearchParams(location.search);
  const SLOW = +Q.get("slow") || 1;                       /* ?slow=N stretches time for filmstrips; values below stay true */
  const cs = getComputedStyle(R), num = (n) => parseFloat(cs.getPropertyValue(n)), str = (n) => cs.getPropertyValue(n).trim();
  const T = {}, SPR = {};
  function read() {
    for (const k of ["touch", "move", "settle", "land"]) SPR[k] = {r: num(`--${k}-r`), z: num(`--${k}-z`), t: num(`--${k}-t`), css: str(`--spring-${k}`)};
    for (const k of ["fade-in", "fade-out", "trail", "step", "burst", "shake", "tip-delay", "tip-linger", "caret-hold", "caret-period", "pending", "rise", "pane-rise", "dip", "from", "follow", "ring-near"]) T[k] = num("--" + k);
  }
  read();
  const TIMES = ["touch-t", "move-t", "settle-t", "land-t", "fade-in", "fade-out", "trail", "step", "burst", "shake", "tip-delay", "tip-linger", "caret-hold", "caret-period"];
  function stretch() { for (const n of TIMES) R.style.removeProperty("--" + n); read(); if (SLOW > 1) { const c = getComputedStyle(R); for (const n of TIMES) R.style.setProperty("--" + n, parseFloat(c.getPropertyValue("--" + n)) * SLOW + "ms"); } }
  stretch();
  R.classList.add("m-on");
  const now = () => performance.now() / SLOW;             /* prototype time, ms */
  const after = (ms, fn) => setTimeout(fn, ms * SLOW);    /* for simulated remote events and thresholds only: never between an input and its state */
  const reduced = () => SPR.move.t <= 1;
  /* a page nobody sees does not animate: Web Animations do not advance while it is hidden, so an animation started then is
     finished at once, its end state written and its promise kept, instead of waiting, with whatever it holds, to be seen */
  const play = (e, kf, o) => { const a = e.animate(kf, o); if (document.hidden) a.finish(); return a; };

  /* ---------- the spring: closed form, sampled into keyframes, state kept per element and channel set ---------- */
  const springs = new WeakMap();
  function sample(s, t) {
    const x = {}, v = {};
    for (const k in s.x0) { const a = s.x0[k], b = s.v0[k];
      if (s.z >= 1) { const B = b + s.w * a, e = Math.exp(-s.w * t); x[k] = (a + B * t) * e; v[k] = (B - s.w * (a + B * t)) * e; }
      else { const wd = s.w * Math.sqrt(1 - s.z * s.z), C = (b + s.z * s.w * a) / wd, e = Math.exp(-s.z * s.w * t), c = Math.cos(wd * t), n = Math.sin(wd * t);
        x[k] = e * (a * c + C * n); v[k] = e * (-s.z * s.w * (a * c + C * n) - a * wd * n + C * wd * c); } }
    return [x, v];
  }
  const fmt = (d) => `translate(${(d.x || 0).toFixed(2)}px, ${(d.y || 0).toFixed(2)}px) scale(${(1 + (d.sx || 0)).toFixed(4)}, ${(1 + (d.sy || 0)).toFixed(4)})`;
  function current(e, id = "m") { const s = (springs.get(e) || {})[id]; if (!s) return [{}, {}]; const t = now() - s.t0; return t >= s.dur ? [{}, {}] : sample(s, t / 1000); }
  function halt(e, id = "m") { const m = springs.get(e); if (m && m[id]) { m[id].a.cancel(); delete m[id]; } }
  function release(e, add, token = "move", o = {}) {
    const id = o.id || "m", [cur, vel] = current(e, id), sp = SPR[token], x0 = {}, v0 = {}; let far = 0;
    for (const k of new Set([...Object.keys(cur), ...Object.keys(add), ...Object.keys(o.kick || {})])) {
      x0[k] = (cur[k] || 0) + (add[k] || 0); v0[k] = (vel[k] || 0) + ((o.kick || {})[k] || 0);
      if (o.clamp && k in o.clamp) x0[k] = Math.max(-o.clamp[k], Math.min(o.clamp[k], x0[k]));
      far = Math.max(far, (Math.abs(x0[k]) + Math.abs(v0[k]) / 20) * (k[0] === "s" ? 300 : 1)); }
    halt(e, id);
    if (sp.t <= 1 || far < 0.5 || document.hidden) return null;   /* reduced motion, nothing to release, or nobody to see it: the state is already there */
    const s = {t0: now(), dur: sp.t, w: 2 * Math.PI / sp.r, z: sp.z, x0, v0}, n = Math.max(2, Math.round(sp.t / 12)), kf = [];
    for (let i = 0; i <= n; i++) kf.push({transform: fmt(i === n ? {} : sample(s, sp.t / 1000 * i / n)[0])});
    s.a = e.animate(kf, {duration: sp.t * SLOW, easing: "linear", composite: "add", pseudoElement: o.pseudo});
    let m = springs.get(e); if (!m) springs.set(e, m = {}); m[id] = s;
    return s.a;
  }
  /* el's rect at rest: what layout says, with springs in flight on it and above it removed */
  function rest(e) { const r = e.getBoundingClientRect(); let x = r.left, y = r.top;
    for (let a = e; a && a !== R; a = a.classList.contains("m-fly") ? null : a.parentElement) { const m = springs.get(a); if (m) for (const id in m) { const [c] = current(a, id); x -= c.x || 0; y -= c.y || 0; } }
    return {left: x, top: y, width: r.width, height: r.height, right: x + r.width, bottom: y + r.height}; }
  function flip(e, before, token = "move", o = {}) { const a = rest(e), d = {x: before.left - a.left, y: before.top - a.top};
    if (o.size) { d.sx = before.width / a.width - 1; d.sy = before.height / a.height - 1; } return release(e, d, token, o); }

  /* ---------- fades ---------- */
  const fades = new WeakMap();
  function fade(e, to, ms) { const from = +getComputedStyle(e).opacity; const old = fades.get(e); if (old) old.cancel();
    e.style.opacity = to; if (from === to) return null;
    const a = play(e, {opacity: [from, to]}, {duration: (ms ?? (to > from ? T["fade-in"] : T["fade-out"])) * SLOW, easing: "linear"}); fades.set(e, a); return a; }
  const appear = (e, from = 0, ms, o = {}) => play(e, {opacity: [from, 1]}, {duration: (ms ?? T["fade-in"]) * SLOW, easing: "linear", delay: (o.delay || 0) * SLOW, fill: o.delay ? "backwards" : "none", pseudoElement: o.pseudo});
  /* the thing is already gone as far as the app is concerned (inert, out of the tab order); this lets it be seen leaving */
  function leave(e, then, from = +getComputedStyle(e).opacity) { e.inert = true; const a = play(e, {opacity: [from, 0]}, {duration: T["fade-out"] * SLOW, easing: "linear", fill: "forwards"});
    a.finished.then(() => { e.remove(); then && then(); }, () => {}); return a; }
  const hold = (e, n = 1) => reduced() ? null : play(e, {opacity: [0, 0]}, {duration: T.step * n * SLOW});   /* the agent's forms change in steps: this part appears n steps late, as a cut */

  /* ---------- selection thumb ---------- */
  function thumb(th, target, token = "touch") {
    if (!target) { th.dataset.off = 1; return; }
    const was = th.dataset.off ? null : {x: parseFloat(th.style.getPropertyValue("--x")), y: parseFloat(th.style.getPropertyValue("--y")), w: parseFloat(th.style.getPropertyValue("--w")), h: parseFloat(th.style.getPropertyValue("--h"))};
    const to = {x: target.offsetLeft, y: target.offsetTop, w: target.offsetWidth, h: target.offsetHeight};
    for (const k in to) th.style.setProperty("--" + k, to[k] + "px"); delete th.dataset.off;
    if (!was || !was.w || !to.w) return appear(th);
    return release(th, {x: was.x - to.x, y: was.y - to.y, sx: was.w / to.w - 1, sy: was.h / to.h - 1}, token);
  }

  /* ---------- a figure in fixed cells ---------- */
  const cells = (v) => [...String(v)].map((c) => `<span${/\d/.test(c) ? "" : ' class="p"'}>${c === "<" ? "&lt;" : c === "&" ? "&amp;" : c}</span>`).join("");   /* a figure's characters are text */
  function figure(cell, v, o = {}) {
    v = String(v); const old = cell.textContent, up = o.dir ?? (parseFloat(v.replace(/\D/g, "")) >= parseFloat(old.replace(/\D/g, "")) ? 1 : -1);
    if (old === v) return; if (old.length !== v.length || !cell.firstElementChild) { cell.innerHTML = cells(v); if (old) appear(cell, 0.4); return; }
    [...cell.children].forEach((b, i) => { if (b.firstChild.nodeValue === v[i]) return;
      const g = document.createElement("i"); g.textContent = b.firstChild.nodeValue; b.firstChild.nodeValue = v[i]; b.querySelectorAll("i").forEach((x) => x.remove()); b.append(g); leave(g, null, 1);   /* a ghost just made is whole: reading its opacity would force a style recalculation each tick */
      if (o.cut) return;                                                         /* a clock: the digit changes in a step, nothing travels */
      if (!o.quiet) release(b, {y: T.rise * up}, "touch"); });
  }
  /* ---------- a glyph or word in a fixed cell ---------- */
  function swap(cell, html, o = {}) {
    const old = cell.querySelector(":scope > :not(.gone):not(.sz)"); cell.querySelectorAll(":scope > .gone").forEach((g) => g.remove());
    const neu = document.createElement("span"); neu.innerHTML = html; const el = neu.firstElementChild || neu;
    if (old) { old.classList.add("gone"); leave(old); if (!o.quiet && !reduced()) release(old, {y: 0}, "touch", {kick: {y: -(o.dir ?? 1) * 160}}); }
    cell.append(el);
    if (o.seq && old) appear(el, 0, undefined, {delay: T["fade-out"]});   /* words read where the eye is: the old one goes, then the new one comes; never two at once */
    else appear(el, 0.3);
    if (!o.quiet) release(el, {y: T.rise * (o.dir ?? 1)}, "touch"); return el;
  }

  /* ---------- one mark becomes another, in place: the new one is whole in the frame and the old one fades off it ---------- */
  function cross(old, html) {
    const r = old.getBoundingClientRect(), ink = getComputedStyle(old).color; old.insertAdjacentHTML("afterend", html); const neu = old.nextElementSibling; old.remove();
    if (reduced() || !r.width) return neu;
    old.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;margin:0;color:${ink};pointer-events:none;z-index:60`; document.body.append(old);
    old.getAnimations().forEach((a) => a.cancel()); leave(old); appear(neu, 0.3); return neu;
  }

  /* ---------- grow: nine slices, so a fill can change size on a spring with its corners intact ---------- */
  function grow(e, from, token = "land", r = 18) {
    const to = rest(e), dx = to.width - from.width, dy = to.height - from.height;
    if (reduced() || (Math.abs(dx) < 1 && Math.abs(dy) < 1)) return null;
    const c = getComputedStyle(e), rad = [c.borderTopLeftRadius, c.borderTopRightRadius, c.borderBottomRightRadius, c.borderBottomLeftRadius];
    e.querySelectorAll(":scope > .m-nine").forEach((n) => n.remove());
    const nine = document.createElement("span"); nine.className = "m-nine"; nine.style.setProperty("--nine", c.backgroundColor);
    const W = to.width, H = to.height, mw = W - 2 * r + 2, mh = H - 2 * r + 2, P = [];
    const cols = [[0, r, 0], [r - 1, mw, 1], [W - r, r, 2]], rows = [[0, r, 0], [r - 1, mh, 1], [H - r, r, 2]];
    for (const [y, h, j] of rows) for (const [x, w, i] of cols) { if (w <= 0 || h <= 0) continue;
      const s = document.createElement("i"); s.style.cssText = `left:${x}px;top:${y}px;width:${w}px;height:${h}px`;
      if (i !== 1 && j !== 1) s.style[["borderTopLeftRadius", "borderTopRightRadius", "borderBottomRightRadius", "borderBottomLeftRadius"][j === 0 ? (i === 0 ? 0 : 1) : (i === 0 ? 3 : 2)]] = rad[j === 0 ? (i === 0 ? 0 : 1) : (i === 0 ? 3 : 2)];
      const d = {}; if (i === 2) d.x = -dx; if (i === 1) d.sx = -dx / mw; if (j === 2) d.y = -dy; if (j === 1) d.sy = -dy / mh;
      nine.append(s); P.push([s, d]); }
    e.classList.add("growing"); e.prepend(nine);
    let last = null; for (const [s, d] of P) last = release(s, d, token) || last;
    const done = () => { if (nine.isConnected) { nine.remove(); if (!e.querySelector(":scope > .m-nine")) e.classList.remove("growing"); } };
    if (last) last.finished.then(done, done); else done();
    return last;
  }

  /* ---------- fly: travel from a rect to the element's place, in the top layer so no scroller or pane clips it ---------- */
  const flying = new Set();
  function fly(e, from, token = "move", o = {}) {
    const to = rest(e), d = {x: from.left - to.left, y: from.top - to.top};
    if (reduced() || !e.showPopover) return null;
    /* what keeps its room while it is away is its own picture, unseen: the same box and the same baseline, so nothing set beside it (a time) sits anywhere else during the flight */
    const spacer = e.cloneNode(true); spacer.removeAttribute("id"); spacer.querySelectorAll("[id]").forEach((x) => x.removeAttribute("id")); spacer.style.visibility = "hidden"; spacer.style.width = to.width + "px"; spacer.setAttribute("aria-hidden", "true"); spacer.inert = true; spacer.classList.add("m-spacer");
    if (!o.inplace) e.after(spacer);                      /* an absolutely placed thing holds no room, so it needs none kept */ e.setAttribute("popover", "manual"); e.classList.add("m-fly");
    e.style.left = to.left + "px"; e.style.top = to.top + "px"; e.style.width = to.width + "px";
    try { e.showPopover(); } catch (x) { ground(e, spacer); return null; }
    const f = {e, spacer}; flying.add(f);
    const a = release(e, d, token, o);
    const end = () => { if (!flying.has(f)) return; flying.delete(f); ground(e, spacer); };
    f.end = end; if (a) a.finished.then(end, () => {}); else end();
    return a;
  }
  function ground(e, spacer) { try { e.hidePopover(); } catch (x) {} e.removeAttribute("popover"); e.classList.remove("m-fly"); e.style.left = e.style.top = e.style.width = ""; spacer.remove(); }
  /* the pane under a flying thing moved by dy (a second send, a line of text): its place moves, and it is retargeted there */
  function carry(dy, token = "move") { for (const f of flying) { f.e.style.top = parseFloat(f.e.style.top) - dy + "px"; const a = release(f.e, {y: dy}, token); if (a) a.finished.then(f.end, () => {}); else f.end(); } }

  /* ---------- arrivals and knocks ---------- */
  const land = (e, o = {}) => { appear(e, 0.4); return release(e, {sx: T.from - 1, sy: T.from - 1}, "land", o); };   /* transform-origin is the caller's: its centre */
  const kick = (e, v, token = "touch") => release(e, {}, token, {kick: v, id: "k"});   /* an impulse, px/s: it leaves and comes back. A thing that holds words takes it on a critically damped spring */
  const squash = (e, by = 0.03) => release(e, {sx: -by, sy: -by}, "touch", {id: "sq"});   /* struck: it is smaller in the frame of the blow and recovers without passing its size */
  function burst(at, text, n = 3) {   /* text, never markup: it is a reaction's emoji, which comes from the server */ if (reduced()) return; const r = at.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    for (let i = 0; i < n; i++) { const p = document.createElement("i"); p.className = "m-bit"; p.textContent = text; document.body.append(p);
      const ang = (-150 + i * 60 + (i % 2) * 14) * Math.PI / 180, d = 22 + i * 4, x = cx - 6, y = cy - 6;
      play(p, {transform: [`translate(${x}px, ${y}px) scale(0.9)`, `translate(${x + Math.cos(ang) * d}px, ${y + Math.sin(ang) * d}px) scale(0.6)`], opacity: [1, 1, 0], offset: [0, 0.55, 1]}, {duration: T.burst * SLOW, easing: SPR.move.css, delay: i * 20 * SLOW, fill: "both"}).finished.then(() => p.remove()); } }
  /* a refused action: the thing shakes once. With motion reduced its edge takes the failure ink and lets it go (--shake), so the refusal still reads without sound */
  function refuse(e) { if (!reduced()) return shake(e); const n = document.createElement("i"); n.className = "m-no"; e.append(n); const a = play(n, {opacity: [1, 1, 0], offset: [0, 0.3, 1]}, {duration: 200 * SLOW, easing: "linear"}); a.finished.then(() => n.remove(), () => n.remove()); return a; }
  const shake = (e) => reduced() ? null : play(e, {transform: ["translateX(0)", "translateX(-3px)", "translateX(3px)", "translateX(-2px)", "translateX(0)"]}, {duration: T.shake * SLOW, easing: "linear", composite: "add"});

  /* ---------- the focus ring: one object. It arrives from 3 px out; between neighbours it travels ---------- */
  let ringEl, ringAt = null, ringFor = null, ringDue = 0, kbd = false;
  const ringBox = (target) => { const r = rest(target), g = 2; return {x: r.left - g, y: r.top - g, w: r.width + 2 * g, h: r.height + 2 * g}; };
  const ringPlace = (to, target) => { const rad = parseFloat(getComputedStyle(target).borderTopLeftRadius) || 0; ringEl.style.width = to.w + "px"; ringEl.style.height = to.h + "px"; ringEl.style.translate = `${to.x}px ${to.y}px`; ringEl.style.borderRadius = Math.min(rad + 2, to.h / 2) + "px"; };
  /* while the ring shows it is re-placed when its control's resting place may have changed: a scroll, a resize, the control's
     own size, the page's elements or words changing, a picture or a typeface loading, a transition ending. One frame for each
     burst of them, and none at rest. A spring on the control or above it is not a change of place. */
  function ringSoon() { if (!ringFor || ringDue) return; ringDue = requestAnimationFrame(() => { ringDue = 0; if (!ringFor) return;
    if (!ringFor.isConnected || ringFor.closest("[hidden], [inert]")) { ring(null); return; }
    const to = ringBox(ringFor), a = ringAt; if (Math.abs(to.x - a.x) > 0.25 || Math.abs(to.y - a.y) > 0.25 || Math.abs(to.w - a.w) > 0.25 || Math.abs(to.h - a.h) > 0.25) { ringPlace(to, ringFor); ringAt = to; } }); }
  const ringMoved = new MutationObserver(ringSoon), ringSized = new ResizeObserver(ringSoon);
  function ring(target) {
    if (!ringEl) { ringEl = document.createElement("i"); ringEl.id = "m-ring"; document.body.append(ringEl); }
    if (ringFor && ringFor !== target) ringSized.unobserve(ringFor);
    if (!target) { ringAt = null; ringFor = null; cancelAnimationFrame(ringDue); ringDue = 0; ringMoved.disconnect(); fade(ringEl, 0); return; }
    const to = ringBox(target), was = ringAt;
    ringPlace(to, target);
    if (!ringFor) ringMoved.observe(document.body, {childList: true, subtree: true, attributes: true, characterData: true});
    ringAt = to; ringFor = target; ringSized.observe(target); fade(ringEl, 1);
    const near = was && Math.hypot(was.x + was.w / 2 - to.x - to.w / 2, was.y + was.h / 2 - to.y - to.h / 2) <= T["ring-near"];
    if (near) release(ringEl, {x: was.x - to.x, y: was.y - to.y, sx: was.w / to.w - 1, sy: was.h / to.h - 1}, "touch");
    else { halt(ringEl); release(ringEl, {x: -3, y: -3, sx: 6 / to.w, sy: 6 / to.h}, "touch"); }
  }

  /* ---------- the tooltip: one object. It waits before the first, never between neighbours ---------- */
  let tipEl, tipFor = null, tipAt = null, tipTimer = 0, tipGone = -1e9;
  function tipShow(target) {
    if (!tipEl) { tipEl = document.createElement("div"); tipEl.id = "m-tip"; tipEl.setAttribute("role", "tooltip"); document.body.append(tipEl); }
    const t = target.dataset.tip, k = target.dataset.key; tipEl.textContent = t; if (k) { const kb = document.createElement("kbd"); kb.textContent = k; tipEl.append(kb); }
    const r = target.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight, x = Math.round(Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))), y = Math.round(r.top - h - 6);
    const was = tipFor && tipAt; tipEl.style.translate = `${x}px ${y}px`; tipFor = target; tipAt = {x, y}; fade(tipEl, 1);
    if (was && Math.abs(was.x - x) <= T["ring-near"]) release(tipEl, {x: was.x - x, y: was.y - y}, "touch"); else { halt(tipEl); release(tipEl, {y: T["pane-rise"]}, "touch"); }
  }
  function tip(target, o = {}) {
    clearTimeout(tipTimer);
    if (!target) { if (tipFor) { tipFor = null; tipGone = now(); fade(tipEl, 0); } return; }
    if (target === tipFor) return;
    if (tipFor || o.now || now() - tipGone < T["tip-linger"]) tipShow(target); else tipTimer = after(T["tip-delay"], () => tipShow(target));
  }

  function say(target, text, ms = 900) { const was = [target.dataset.tip, target.dataset.key]; target.dataset.tip = text; delete target.dataset.key; tipFor = null; clearTimeout(tipTimer); tipShow(target);
    if (was[0]) target.dataset.tip = was[0]; else delete target.dataset.tip; if (was[1]) target.dataset.key = was[1]; tipTimer = after(ms, () => tip(null)); }
  /* the first element.animate and the first trip to the top layer cost several ms each; pay at idle, not on the first send */
  function warm() { const go = () => { const p = document.createElement("i"); p.style.cssText = "position:fixed;left:-99px;top:-99px;width:1px;height:1px;opacity:0;pointer-events:none"; document.body.append(p);
      try { p.setAttribute("popover", "manual"); p.showPopover(); p.hidePopover(); } catch (x) {} release(p, {y: 4, sx: 0.02}, "touch"); appear(p); p.getAnimations().forEach((a) => a.finish()); p.remove(); ring(null); };
    (window.requestIdleCallback || setTimeout)(go, {timeout: 600}); }

  /* ---------- wiring: what every page gets without asking ---------- */
  function wire(root = document) {
    root.addEventListener("keydown", (e) => { if (e.key === "Tab" || e.key.startsWith("Arrow")) kbd = true; }, true);
    root.addEventListener("pointerdown", () => { kbd = false; ring(null); tip(null); }, true);
    root.addEventListener("focusin", (e) => { if (kbd && !e.target.isContentEditable && !e.target.dataset.noring && e.target.matches("button, a, [tabindex]")) ring(e.target); else ring(null); });
    root.addEventListener("focusout", (e) => { if (!e.relatedTarget) ring(null); });
    root.addEventListener("pointerover", (e) => { const t = e.target.closest && e.target.closest("[data-tip]"); tip(t || null); });
    addEventListener("blur", () => { ring(null); tip(null); });
    for (const t of ["scroll", "load", "transitionend", "animationend"]) root.addEventListener(t, ringSoon, {capture: true, passive: true});
    addEventListener("resize", ringSoon); if (document.fonts) document.fonts.addEventListener("loadingdone", ringSoon);
  }

  window.M = {SLOW, T, SPR, now, after, reduced, stretch, release, current, halt, rest, flip, fade, appear, leave, hold, thumb, figure, cells, swap, cross, grow, fly, carry, flying, land, kick, squash, burst, shake, refuse, ring, tip, say, warm, wire,
    kbd: (v) => (v === undefined ? kbd : (kbd = v)),
    focusKbd(el) { kbd = true; el.focus({preventScroll: true}); ring(el); }};
})();
