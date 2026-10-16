/* INTERIM BETA (README.md): an artifact's scene (artifacts.js): the diagram an agent describes as a tree of nodes (groups laid
   out in a row, a column, a grid, a graph or on top of each other; words; shapes; connectors; paths; symbols; small
   charts; controls), bound to the document's data, with steps that change its nodes one after another. Ported from the
   old renderer (web/src/legacy_artifact.ts: render_scene and what it calls) to the design's language and to the column's
   width: a graph is laid out in ranks from top to bottom, so a chain of any length reads down the column; connectors are
   drawn after layout, between the nodes' edges; nothing loops, and steps play once, when the scene is opened, unless
   motion is reduced. */

/* What it uses of the page is handed in when it arrives (chunks.js): a chunk imports nothing the first load holds. */
export default function sceneChunk({$, $$, el, esc, dataAt, isObj, text, words}) {

  /* ---------- values: a binding, or a small computation over the data (the contract's `derive`) ---------- */
  function derive(d, data) {
    if (!isObj(d) || typeof d.op !== "string" || !Array.isArray(d.args)) return undefined;
    const args = d.args.map((a) => (typeof a === "string" && a.startsWith("data.") ? dataAt(data, a) : a)), n = args.map(Number);
    if (["add", "subtract", "multiply", "divide", "min", "max", "clamp", "greater", "less"].includes(d.op) && n.some((x) => !Number.isFinite(x))) return undefined;
    switch (d.op) {
      case "add": return n.reduce((a, b) => a + b, 0);
      case "subtract": return n.slice(1).reduce((a, b) => a - b, n[0] ?? 0);
      case "multiply": return n.reduce((a, b) => a * b, 1);
      case "divide": return n.slice(1).some((x) => x === 0) ? undefined : n.slice(1).reduce((a, b) => a / b, n[0] ?? 0);
      case "min": return Math.min(...n);
      case "max": return Math.max(...n);
      case "clamp": return Math.min(n[2] ?? Infinity, Math.max(n[1] ?? -Infinity, n[0] ?? 0));
      case "equal": return args[0] === args[1];
      case "greater": return (n[0] ?? 0) > (n[1] ?? 0);
      case "less": return (n[0] ?? 0) < (n[1] ?? 0);
      case "and": return args.every(Boolean);
      case "or": return args.some(Boolean);
      case "not": return !args[0];
      default: return undefined;
    }
  }

  /* ---------- the nodes ---------- */
  /* a symbol is a glyph of the kit's where one says the same thing; play, pause and a clock are drawn in the glyphs' own hand */
  const own = (d) => `<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  const SYMBOL = {arrow: () => ic("right"), check: () => ic("check"), warning: () => mk("needs", "c-att"), error: () => mk("failed", "c-fail"), play: () => own("M5 3.5v9l7-4.5z"), pause: () => own("M5.5 3.5v9M10.5 3.5v9"),
    clock: () => own("M8 2.5a5.5 5.5 0 1 1 0 11a5.5 5.5 0 1 1 0-11zM8 5v3.25l2 1.25"), file: () => ic("file"), folder: () => ic("folder"), code: () => ic("term"), person: () => ic("person"), agent: () => agentFace(16)};
  /* a small chart: bars, a line, points, an area, or the parts of a whole in the first ink at falling strengths */
  function markHTML(node, data) {
    const raw = dataAt(data, node.bind), vs = (Array.isArray(raw) ? raw : Array.isArray(node.values) ? node.values : []).map(Number).filter(Number.isFinite).slice(0, 256);
    const lo = Math.min(...vs, 0), hi = Math.max(...vs, 1), span = Math.max(hi - lo, Number.EPSILON);
    if (node.mark === "bar") return `<div class="sn-bars">${vs.map((v) => `<i style="--v:${Math.max(0, (v - lo) / span)}"></i>`).join("")}</div>`;
    const pts = vs.map((v, i) => [vs.length === 1 ? 50 : (i / (vs.length - 1)) * 100, 38 - ((v - lo) / span) * 34]);
    let inner = "";
    if (node.mark === "point") inner = pts.map(([x, y]) => `<circle cx="${x}" cy="${y.toFixed(2)}" r="2.5"/>`).join("");
    else if (node.mark === "arc") { const pos = vs.map((v) => Math.max(0, v)), total = pos.reduce((a, b) => a + b, 0); let off = 0;
      inner = total > 0 ? pos.map((v, i) => { const f = v / total, c = `<circle class="arc" cx="50" cy="20" r="15" pathLength="1" stroke-dasharray="${f} ${1 - f}" stroke-dashoffset="${-off}" style="--k:${i % 4}"/>`; off += f; return c; }).join("") : ""; }
    else { const p = pts.map(([x, y]) => `${x},${y.toFixed(2)}`).join(" "); inner = node.mark === "area" ? `<polygon class="area" points="0,40 ${p} 100,40"/>` : `<polyline points="${p}"/>`; }
    return `<svg class="sn-chart" viewBox="0 0 100 40"${node.mark === "arc" ? "" : ' preserveAspectRatio="none"'} aria-hidden="true">${inner}</svg>`;   /* the parts of a whole keep their circle */
  }
  function controlHTML(node) {
    const label = text(node.label), c = node.control;
    if (c === "range") return `<label class="sn-range"><span>${esc(label)}</span><input type="range" min="${esc(text(node.min ?? 0))}" max="${esc(text(node.max ?? 100))}" step="${esc(text(node.step ?? 1))}" value="${esc(text(node.value ?? node.min ?? 0))}"></label>`;
    if (c === "toggle") return `<button class="btn" data-sn-toggle aria-pressed="${!!node.value}">${esc(label)}</button>`;
    if (c === "stepper") { const v = Number(node.value ?? node.min ?? 0); return `<button class="btn" data-sn-step="${Number.isFinite(v) ? v : 0}" data-sn-by="${Number(node.step ?? 1) || 1}" data-sn-max="${Number.isFinite(Number(node.max)) ? Number(node.max) : ""}" data-sn-label="${esc(label)}">${esc(label)} <span class="fig">${Number.isFinite(v) ? v : ""}</span></button>`; }
    return `<button class="btn" data-sn-press>${esc(label)}</button>`;
  }
  /* one node: its id (a step's target, a connector's end), its state, its label for assistive technology; a node people can
     choose (interaction) is a button that keeps whether it is chosen */
  function nodeHTML(node, data, cx) {
    if (!isObj(node)) return "";
    if (isObj(node.repeat)) { const items = dataAt(data, node.repeat.source), limit = Number.isInteger(node.repeat.limit) ? node.repeat.limit : 16, tpl = {...node}; delete tpl.repeat;
      return `<div class="sn-rep">${(Array.isArray(items) ? items.slice(0, limit) : []).map((item, index) => nodeHTML(tpl, {...data, item, index}, cx)).join("")}</div>`; }
    if (node.type === "connector") { cx.connectors.push({id: typeof node.id === "string" ? node.id : null, from: text(node.from), to: text(node.to), directed: node.directed !== false, label: typeof node.label === "string" ? node.label : "", state: typeof node.state === "string" ? node.state : null}); return ""; }
    const pick = typeof node.interaction === "string" && node.interaction !== "none";
    const attrs = [`class="sn sn-${esc(node.type)}"`, typeof node.id === "string" ? `data-node="${esc(node.id)}"` : "", typeof node.state === "string" ? `data-state="${esc(node.state)}"` : "", typeof node.ariaLabel === "string" ? `aria-label="${esc(node.ariaLabel)}"` : "",
      pick ? `tabindex="0" role="button" aria-pressed="false" data-sn-pick` : ""].filter(Boolean);
    let inner = "";
    if (node.type === "group") { attrs.push(`data-layout="${esc(text(node.layout) || "column")}"`); if (typeof node.align === "string") attrs.push(`data-align="${esc(node.align)}"`); if (Number.isInteger(node.columns)) attrs.push(`style="--cols:${node.columns}"`);
      inner = (Array.isArray(node.children) ? node.children : []).map((ch) => nodeHTML(ch, data, cx)).join(""); }
    else if (node.type === "text") { attrs.push(`data-role="${esc(text(node.textRole) || "body")}"`); const v = derive(node.derive, data) ?? dataAt(data, node.bind) ?? node.text; inner = words(text(v)); }
    else if (node.type === "shape") { attrs.push(`data-shape="${esc(text(node.shape) || "rectangle")}"`); if (typeof node.label === "string") inner = `<span class="sn-l">${words(node.label)}</span>`; }
    else if (node.type === "symbol") { const s = SYMBOL[node.symbol]; inner = s ? s() : ""; if (!node.ariaLabel) attrs.push('aria-hidden="true"'); }
    else if (node.type === "mark") inner = markHTML(node, data);
    else if (node.type === "path") { const pts = (Array.isArray(node.points) ? node.points : []).filter((p) => Array.isArray(p) && typeof p[0] === "number" && typeof p[1] === "number").map((p) => `${Math.max(0, Math.min(100, p[0]))},${Math.max(0, Math.min(100, p[1]))}`).join(" ");
      inner = `<svg viewBox="0 0 100 100" aria-hidden="true">${node.closed ? `<polygon points="${pts}"/>` : `<polyline points="${pts}"/>`}</svg>`; }
    else if (node.type === "control") inner = controlHTML(node);
    return `<div ${attrs.join(" ")}>${inner}</div>`;
  }
  const connectorsOf = new WeakMap();   /* a drawn scene -> its connectors */
  /* the scene block: the diagram on the block's fill, its connectors drawn over it, then its steps if it has them */
  function sceneHTML(doc, b, i) {
    const cx = {connectors: []}, root = nodeHTML(b.root, doc.data, cx), steps = Array.isArray(b.steps) ? b.steps.filter((s) => isObj(s) && Array.isArray(s.changes)) : [];
    const interactive = steps.length > 1 || /data-sn-|<input/.test(root);
    return `<div class="ah"><span>${esc(text(b.label))}</span></div><div class="scn" data-scene="${i}" role="${interactive ? "group" : "img"}" aria-label="${esc(text(b.label))}" data-connectors="${esc(JSON.stringify(cx.connectors))}"><svg class="sn-lines" aria-hidden="true"></svg>${root}</div>`
      + (steps.length > 1 ? `<div class="sn-steps" role="group" aria-label="Steps"><button class="c sq s" data-sn-prev aria-label="Previous step" data-tip="Previous">${ic("right")}</button><button class="lk" data-sn-play><span class="cell"><span>Play</span><i class="sz">Pause</i></span></button><span class="sn-at"></span><button class="c sq s" data-sn-next aria-label="Next step" data-tip="Next">${ic("right")}</button></div><div class="sn-track" tabindex="0" role="slider" aria-label="Step" aria-valuemin="1" aria-valuemax="${steps.length}"><i></i></div><div class="sn-say" aria-live="polite" aria-atomic="true"></div>` : "");
  }

  /* ---------- a graph's layout: ranks from its connectors, top to bottom ---------- */
  function ranksOf(ids, cons) {
    const ranks = new Map(), into = new Map(ids.map((id) => [id, 0])), out = new Map(ids.map((id) => [id, []]));
    for (const c of cons) { into.set(c.to, (into.get(c.to) || 0) + 1); out.get(c.from).push(c.to); }
    if (cons.every((c) => c.directed)) {   /* a directed graph: each node a rank after everything that leads to it */
      const q = ids.filter((id) => !into.get(id)); q.forEach((id) => ranks.set(id, 0));
      for (let k = 0; k < q.length; k++) for (const t of out.get(q[k])) { ranks.set(t, Math.max(ranks.get(t) || 0, ranks.get(q[k]) + 1)); into.set(t, into.get(t) - 1); if (!into.get(t)) q.push(t); }
      if (ranks.size === ids.length) return ranks; ranks.clear(); }
    /* undirected, or a cycle: by distance from the first node of each part */
    const near = new Map(ids.map((id) => [id, []])); for (const c of cons) { near.get(c.from).push(c.to); near.get(c.to).push(c.from); }
    const left = new Set(ids); let base = 0;
    while (left.size) { const root = left.values().next().value, q = [root]; ranks.set(root, base); left.delete(root); let top = base;
      for (let k = 0; k < q.length; k++) { const r = ranks.get(q[k]); top = Math.max(top, r); for (const t of near.get(q[k])) if (left.has(t)) { ranks.set(t, r + 1); left.delete(t); q.push(t); } }
      base = top + 2; }
    return ranks;
  }
  function layoutGraph(g, connectors) {
    const nodes = $$(":scope > [data-node]", g), byId = new Map(nodes.map((n) => [n.dataset.node, n])); if (!byId.size) return;
    const cons = connectors.filter((c) => byId.has(c.from) && byId.has(c.to)), ranks = ranksOf([...byId.keys()], cons), rows = [];
    for (const [id, n] of byId) (rows[ranks.get(id) || 0] = rows[ranks.get(id) || 0] || []).push({id, n});
    const filled = rows.filter(Boolean);
    /* within a rank, nodes stand under what they connect to in the rank above, so fewer lines cross */
    for (let r = 1; r < filled.length; r++) { const above = new Map(filled[r - 1].map(({id}, k) => [id, k]));
      const score = (id) => { const ks = cons.flatMap((c) => (c.from === id && above.has(c.to) ? [above.get(c.to)] : c.to === id && above.has(c.from) ? [above.get(c.from)] : [])); return ks.length ? ks.reduce((a, b) => a + b, 0) / ks.length : Infinity; };
      filled[r].sort((a, b) => { const d = score(a.id) - score(b.id); return Number.isNaN(d) ? 0 : d; }); }
    const cols = Math.max(1, ...filled.map((r) => r.length)); g.style.setProperty("--gcols", cols);
    filled.forEach((row, r) => row.forEach(({n}, k) => { n.style.gridRow = String(r + 1); n.style.gridColumn = row.length === 1 ? `1 / span ${cols}` : String(k + 1); }));
  }

  /* ---------- connectors, drawn once the nodes are laid out, from edge to edge ---------- */
  const nodeOf = (scn, id) => $$("[data-node]", scn).find((x) => x.dataset.node === id) || null;
  function drawLines(scn) {
    if (!scn.isConnected) return; const svg = $(":scope > .sn-lines", scn), box = scn.getBoundingClientRect(), cons = connectorsOf.get(scn) || [];
    svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`); svg.setAttribute("width", box.width); svg.setAttribute("height", box.height);
    const id = scn.dataset.markerId; let h = `<defs><marker id="${id}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L8 4L0 8z"/></marker></defs>`;
    for (const c of cons) { const a = nodeOf(scn, c.from), b = nodeOf(scn, c.to); if (!a || !b) continue;
      const s = a.getBoundingClientRect(), e = b.getBoundingClientRect(), sx = s.left + s.width / 2 - box.left, sy = s.top + s.height / 2 - box.top, ex = e.left + e.width / 2 - box.left, ey = e.top + e.height / 2 - box.top, dx = ex - sx, dy = ey - sy;
      const cut = (r) => Math.min(dx ? Math.max(0, r.width / 2 - 2) / Math.abs(dx) : Infinity, dy ? Math.max(0, r.height / 2 - 2) / Math.abs(dy) : Infinity), ks = cut(s), ke = cut(e);
      const x1 = sx + dx * (Number.isFinite(ks) ? ks : 0), y1 = sy + dy * (Number.isFinite(ks) ? ks : 0), x2 = ex - dx * (Number.isFinite(ke) ? ke : 0), y2 = ey - dy * (Number.isFinite(ke) ? ke : 0);
      h += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"${c.state ? ` data-state="${esc(c.current || c.state)}"` : c.current ? ` data-state="${esc(c.current)}"` : ""}${c.directed ? ` marker-end="url(#${id})"` : ""}/>`;
      if (c.label) { const len = Math.max(Math.hypot(dx, dy), 1); let nx = -dy / len, ny = dx / len; if (ny > 0) { nx = -nx; ny = -ny; } h += `<text x="${((x1 + x2) / 2 + nx * 10).toFixed(1)}" y="${((y1 + y2) / 2 + ny * 10).toFixed(1)}" text-anchor="middle">${esc(c.label)}</text>`; } }
    svg.innerHTML = h;
  }

  /* ---------- steps: each changes some nodes' state, label or words; a step is everything up to it applied ---------- */
  const STEP_MS = 1200;   /* the old renderer's pace (1050 ms), slowed a little for the column's smaller nodes */
  let markerSeq = 0;
  function wireScene(root, doc) {
    for (const scn of $$(".scn[data-scene]", root)) {
      const b = doc.blocks[+scn.dataset.scene]; if (!b) continue; let cons = []; try { cons = JSON.parse(scn.dataset.connectors || "[]"); } catch (e) { /* none */ }
      connectorsOf.set(scn, cons); scn.dataset.markerId = `sn-arrow-${++markerSeq}`;
      for (const g of $$('.sn-group[data-layout="graph"]', scn)) layoutGraph(g, cons);
      const redraw = () => requestAnimationFrame(() => drawLines(scn)); redraw();
      if (typeof ResizeObserver === "function") { const ro = new ResizeObserver(() => { if (!scn.isConnected) { ro.disconnect(); return; } redraw(); }); ro.observe(scn); }
      wirePicks(scn); wireSteps(scn, b, cons, redraw);
    }
  }
  /* a node people can choose, a toggle, a stepper, a button: they change only what is shown here, as in the old renderer */
  function wirePicks(scn) {
    const flip = (n) => n.setAttribute("aria-pressed", String(n.getAttribute("aria-pressed") !== "true"));
    scn.addEventListener("click", (e) => { const t = e.target.closest("[data-sn-pick], [data-sn-toggle], [data-sn-step], [data-sn-press]"); if (!t || !scn.contains(t)) return;
      if (t.matches("[data-sn-pick], [data-sn-toggle]")) flip(t);
      else if (t.matches("[data-sn-step]")) { const max = t.dataset.snMax === "" ? Infinity : +t.dataset.snMax, v = Math.min(max, +t.dataset.snStep + +t.dataset.snBy); t.dataset.snStep = v; $(".fig", t).textContent = v; }
      else t.dataset.state = "selected"; });
    scn.addEventListener("keydown", (e) => { const t = e.target; if ((e.key === "Enter" || e.key === " ") && t.matches && t.matches("[data-sn-pick]")) { e.preventDefault(); e.stopPropagation(); flip(t); } });
  }
  function wireSteps(scn, b, cons, redraw) {
    const steps = (Array.isArray(b.steps) ? b.steps : []).filter((s) => isObj(s) && Array.isArray(s.changes)); if (steps.length < 2) return;
    const bar = scn.nextElementSibling, track = bar.nextElementSibling, say = track.nextElementSibling, at = $(".sn-at", bar), play = $("[data-sn-play]", bar), prev = $("[data-sn-prev]", bar), next = $("[data-sn-next]", bar);
    const targets = new Map(); for (const s of steps) for (const c of s.changes) if (isObj(c) && typeof c.target === "string" && !targets.has(c.target)) { const n = nodeOf(scn, c.target); if (n) targets.set(c.target, {n, state: n.dataset.state, label: $(".sn-l", n) ? $(".sn-l", n).innerHTML : null, text: n.classList.contains("sn-text") ? n.innerHTML : null}); }
    const byId = new Map(cons.filter((c) => c.id).map((c) => [c.id, c]));
    let i = 0, timer = 0, playing = false;
    const apply = (k) => { i = Math.max(0, Math.min(steps.length - 1, k));
      for (const t of targets.values()) { if (t.state === undefined) t.n.removeAttribute("data-state"); else t.n.dataset.state = t.state; if (t.label !== null) $(".sn-l", t.n).innerHTML = t.label; if (t.text !== null) t.n.innerHTML = t.text; }
      for (const c of byId.values()) c.current = null;
      for (const s of steps.slice(0, i + 1)) for (const c of s.changes) { if (!isObj(c)) continue; const con = byId.get(c.target); if (con && typeof c.state === "string") con.current = c.state;
        const t = targets.get(c.target); if (!t) continue; if (typeof c.state === "string") t.n.dataset.state = c.state;
        if (typeof c.label === "string") { let l = $(".sn-l", t.n); if (!l) { l = el("span", "sn-l"); t.n.append(l); } l.innerHTML = words(c.label); }
        if (typeof c.text === "string" && t.text !== null) t.n.innerHTML = words(c.text); }
      const label = text(steps[i].label); at.textContent = `${i + 1} of ${steps.length} · ${label}`;
      say.textContent = `Step ${i + 1} of ${steps.length}: ${label}.`;
      track.firstElementChild.style.setProperty("--f", (i + 1) / steps.length); track.setAttribute("aria-valuenow", i + 1); track.setAttribute("aria-valuetext", label);
      prev.disabled = i === 0; next.disabled = i === steps.length - 1; redraw(); };
    const setPlaying = (on) => { clearTimeout(timer); playing = on && scn.isConnected; $(".cell > span", play).textContent = playing ? "Pause" : "Play"; play.setAttribute("aria-pressed", String(playing)); if (playing) tick(); };
    const tick = () => { timer = setTimeout(() => { if (!scn.isConnected || document.hidden) { setPlaying(false); return; } if (i >= steps.length - 1) { setPlaying(false); return; } apply(i + 1); tick(); }, STEP_MS); };
    const choose = (k) => { setPlaying(false); apply(k); };
    prev.addEventListener("click", () => choose(i - 1)); next.addEventListener("click", () => choose(i + 1));
    play.addEventListener("click", () => { if (playing) { setPlaying(false); return; } if (i === steps.length - 1) apply(0); setPlaying(true); });
    track.addEventListener("keydown", (e) => { const k = {ArrowLeft: i - 1, ArrowDown: i - 1, ArrowRight: i + 1, ArrowUp: i + 1, Home: 0, End: steps.length - 1}[e.key]; if (k === undefined) return; e.preventDefault(); e.stopPropagation(); choose(k); });
    apply(0);
    /* opened, it plays once through, unless motion is reduced; any press on its controls takes over */
    if (!(typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) && document.documentElement.dataset.motion !== "reduced") setPlaying(true);
  }

  return {derive, ranksOf, sceneHTML, wireScene};
}
