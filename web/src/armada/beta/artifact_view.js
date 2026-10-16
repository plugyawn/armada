/* INTERIM BETA (README.md): an artifact's document, drawn (artifacts.js opens it). A chunk of its own (chunks.js), loaded the
   first time an artifact is opened, or when the page is idle; a scene's drawing (scene.js) is handed in, loaded the same
   way, when the document has one. */

/* What it uses of the page is handed in when it arrives (chunks.js): a chunk imports nothing the first load holds. */
export default function artifactViewChunk({$, $$, el, esc, katexSheet, loadPics, picHTML, bound, isObj, text, words}) {

  /* ---------- the blocks, drawn ---------- */
  /* a block's state, as the run marks say it: passed, live, needs a person, failed; neutral, selected and muted are no mark */
  const STATE_MARK = {success: ["done", "c-ok"], running: ["live", ""], warning: ["needs", "c-att"], error: ["failed", "c-fail"]};
  const stateMark = (s) => (STATE_MARK[s] ? mk(STATE_MARK[s][0], STATE_MARK[s][1]) : "");
  const isDark = () => { const t = document.documentElement.dataset.theme; return t === "dark" || (t !== "light" && typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches); };
  const themed = (b, field) => (isDark() && typeof b[field + "Dark"] === "string" ? b[field + "Dark"] : text(b[field]));
  function figure(v) { const n = typeof v === "number" ? v : NaN; return Number.isFinite(n) ? n.toLocaleString("en", {maximumFractionDigits: 6}).replace(/^-/, "−") : text(v); }
  const ASPECT = {wide: [16, 9], standard: [4, 3], square: [1, 1], tall: [3, 4]};
  function blockHTML(doc, b, i, S) {
    const head = (label, extra = "") => `<div class="ah">${stateMark(b.state)}<span>${esc(label)}</span>${extra}</div>`;
    if (b.type === "copy") return `${b.eyebrow ? `<div class="ae">${esc(b.eyebrow)}</div>` : ""}${b.heading ? `<h3 class="ahd">${esc(b.heading)}</h3>` : ""}<div class="ab">${words(text(b.body))}</div>`;
    if (b.type === "metric") { const d = typeof b.delta === "number" ? b.delta : null;
      return head(text(b.label)) + `<div class="av2"><span class="fig">${esc(figure(bound(doc, b, "value")))}</span>${b.unit ? `<span class="u">${esc(b.unit)}</span>` : ""}${d !== null ? `<span class="dl">${d > 0 ? "+" : ""}${esc(figure(d))}</span>` : ""}</div>`; }
    if (b.type === "meter") { const total = typeof b.total === "number" && b.total > 0 ? b.total : 100, cur = Number(bound(doc, b, "value")), v = Number.isFinite(cur) ? cur : 0, f = Math.max(0, Math.min(1, v / total));
      return head(text(b.label), `<span class="e">${esc(figure(v))} of ${esc(figure(total))}${b.unit ? " " + esc(b.unit) : ""}</span>`) + `<div class="amt" role="meter" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${v}" aria-label="${esc(text(b.label))}"><i style="--f:${f}"></i></div>${b.detail ? `<div class="ad">${esc(b.detail)}</div>` : ""}`; }
    if (b.type === "series") { const raw = bound(doc, b, "values"), vs = (Array.isArray(raw) ? raw : []).map(Number).filter(Number.isFinite).slice(0, 256);
      const lo = Math.min(...vs, 0), hi = Math.max(...vs, lo + 1e-9), pts = vs.map((v, k) => `${vs.length === 1 ? 50 : (k / (vs.length - 1)) * 100},${(38 - ((v - lo) / (hi - lo)) * 34).toFixed(2)}`).join(" ");
      const range = vs.length ? `${figure(Math.min(...vs))}–${figure(Math.max(...vs))}${b.unit ? " " + b.unit : ""}` : text(b.unit);   /* the line's lowest and highest, at the label's end */
      return head(text(b.label), `<span class="e">${esc(range)}</span>`) + `<div class="asr" role="img" aria-label="${esc(`${text(b.label)}: ${vs.length} values, ${range}`)}"><svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}"/></svg></div>`; }
    if (b.type === "log") { const raw = bound(doc, b, "lines"), lines = (Array.isArray(raw) ? raw : []).map(text);
      return head(text(b.label), `<button class="c sq s acp" data-art-copy="${i}" aria-label="Copy the log" data-tip="Copy">${ic("copy")}</button>`) + `<pre class="alog"><code>${esc(lines.join("\n"))}</code></pre>`; }
    if (b.type === "key_value") return head(text(b.label)) + `<table class="akv"><tbody>${(Array.isArray(b.items) ? b.items : []).filter(isObj).slice(0, 16).map((x) => `<tr><th scope="row">${esc(text(x.key))}</th><td>${esc(text(x.value))}</td></tr>`).join("")}</tbody></table>`;
    if (b.type === "media") { const src = themed(b, "src"), cap = b.caption ? `<div class="ad">${words(text(b.caption))}</div>` : "", [aw, ah] = ASPECT[b.aspect] || [0, 0];
      if (!src) return `<div class="ad">${esc(text(b.alt))}</div>`;
      if (b.kind === "video") { const poster = themed(b, "poster"), auto = b.motion !== "manual" && !(typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
        return `<video class="avd" src="${esc(src)}"${poster ? ` poster="${esc(poster)}"` : ""} controls muted playsinline preload="metadata"${auto ? " autoplay" : ""}${b.motion === "loop" ? " loop" : ""} aria-label="${esc(text(b.alt))}"${aw ? ` style="aspect-ratio:${aw}/${ah}"` : ""}></video>${cap}`; }
      return `<div class="blk pic">${picHTML([{href: src, src, w: aw * 100, h: ah * 100, name: text(b.alt), full: src}])}</div>${cap}`; }
    if (b.type === "scene") return S ? S.sceneHTML(doc, b, i) : "";
    return "";
  }
  /* the whole document: its blocks, then its actions, each a request to Onix in this place */
  function documentHTML(doc, S) {
    return `<div class="ablocks">${doc.blocks.map((b, i) => `<section class="ablk" data-type="${esc(b.type)}"${b.state ? ` data-state="${esc(b.state)}"` : ""}>${blockHTML(doc, b, i, S)}</section>`).join("")}</div>`
      + (doc.actions.length ? `<div class="aact">${doc.actions.map((a, i) => `<button class="btn" data-art-act="${i}" data-tip="${esc(a.prompt)}">${esc(a.label)}</button>`).join("")}</div>` : "");
  }

  /* display maths: KaTeX is fetched the first time an artifact needs it; until then (or if it cannot be had) the formula
     stands in the code face, as written */
  let katexP = null;
  function typeset(root) { const ms = $$(".am[data-tex]", root); if (!ms.length) return;
    katexP = katexP || Promise.all([import(/* webpackChunkName: "katex" */ "katex"), katexSheet()]).then(([k]) => k.default || k).catch(() => null);
    katexP.then((k) => { if (!k) return; for (const x of ms) { if (!x.isConnected) continue; try { k.render(x.dataset.tex, x, {displayMode: x.hasAttribute("data-display"), throwOnError: true, trust: false, maxExpand: 1000, maxSize: 10, strict: "ignore"}); x.classList.add("on"); } catch (e) { /* the formula stays as written */ } } }); }

  /* the panel: its heading line (the title in Strong, Close at its end), then the document, which scrolls in its own bounds */
  function panel(doc, title, inColumn, S) {
    const e = el("div", inColumn ? "apanel" : "apanel sheet-chk raised float"); e.setAttribute("role", "region"); e.setAttribute("aria-label", title);
    e.innerHTML = `<div class="aph"><b>${esc(title)}</b><button class="c sq" data-art-close aria-label="Close" data-tip="Close" data-key="Esc">${ic("x")}</button></div><div class="abody">${documentHTML(doc, S)}</div>`;
    return e;
  }
  /* once it is in the page: its controls, pictures, scenes and maths come alive */
  function mount(e, doc, S) { host(e); loadPics(e); if (S) S.wireScene(e, doc); typeset(e); }
  /* a later artifact on the same message, drawn in the open panel in place */
  function replace(e, doc, title, S) { const body = $(".abody", e); body.innerHTML = documentHTML(doc, S); $(".aph b", e).textContent = title; e.setAttribute("aria-label", title); host(body); loadPics(body); if (S) S.wireScene(e, doc); typeset(e); }

  return {blockHTML, documentHTML, mount, panel, replace};
}
