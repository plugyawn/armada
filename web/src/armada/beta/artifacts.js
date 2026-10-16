/* INTERIM BETA (README.md): Onix's artifacts. A run that calls its `armada_artifact` tool attaches a document to its final
   message as a submessage (msg_type "artifact": tools/armada/artifact_contract.json, zerver/lib/armada_artifact.py): a
   title, data, up to six blocks (copy, metric, meter, series, log, key_value, media, scene) and up to four actions. In the
   conversation it is the design's block, beside Onix's words: a kind glyph, a title, one caption, one verb, 36 high. Open
   shows the whole document in the column, which it borrows and gives back (Close, Esc, or Open again); below 1408, where
   there is no column, in a raised sheet. The last artifact of the message's own sender is the one shown, and one that
   arrives later replaces it in place. */

import {$, $$, el, esc, press, T} from "./util.js";
import {msgIndex, state} from "./state.js";
import {side} from "./frame.js";
import {picHTML, loadPics} from "./media.js";
import {ready} from "./composer.js";
import {send} from "./send.js";
import {updateMessage} from "./agent_turn.js";

/* ---------- the document ---------- */
const BLOCKS = new Set(["copy", "metric", "meter", "series", "log", "key_value", "media", "scene"]);
const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
/* the message's artifact: the latest one its own sender attached (the server allows no other), read as the contract's
   document; a block of a kind this page does not know is left out, and a document with none it knows is no artifact */
function artifactOf(m) {
  const subs = (m.submessages || []).filter((s) => s.msg_type === "artifact" && s.sender_id === m.sender_id).sort((a, b) => b.id - a.id);
  for (const s of subs) { let d; try { d = JSON.parse(s.content); } catch (e) { continue; }
    if (!isObj(d) || d.version !== 1 || !Array.isArray(d.blocks)) continue;
    const blocks = d.blocks.filter((b) => isObj(b) && BLOCKS.has(b.type)).slice(0, 6); if (!blocks.length) continue;
    return {title: typeof d.title === "string" ? d.title : "", data: isObj(d.data) ? d.data : {}, blocks, actions: Array.isArray(d.actions) ? d.actions.filter((a) => isObj(a) && typeof a.label === "string" && typeof a.prompt === "string").slice(0, 4) : [], id: s.id}; }
  return null;
}
const text = (v) => (typeof v === "string" || typeof v === "number" || typeof v === "boolean" ? String(v) : "");
/* a binding: data.a.b in the document's data */
function dataAt(data, path) { if (typeof path !== "string" || !path.startsWith("data.")) return undefined; let cur = data; for (const k of path.slice(5).split(".")) { if (!isObj(cur) || !(k in cur)) return undefined; cur = cur[k]; } return cur; }
const bound = (doc, b, field) => { const v = dataAt(doc.data, b.bind); return v === undefined ? b[field] : v; };

/* what the block in the conversation says: a title (the document's, else its first heading or label) and one caption for
   what it holds (a single block's kind and size, else how many parts), under the glyph of its first kind of block */
const GLYPH = {scene: "chart", series: "chart", metric: "chart", meter: "chart", media: "image", key_value: "table", log: "term", copy: "doc"};
function titleOf(doc) { if (doc.title) return doc.title; for (const b of doc.blocks) { const t = b.heading || b.label || b.alt; if (typeof t === "string" && t) return t; } return "Artifact"; }
function captionOf(doc) {
  const b = doc.blocks[0], more = doc.blocks.length - 1, n = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  const first = b.type === "scene" ? (Array.isArray(b.steps) && b.steps.length > 1 ? `Diagram, ${n(b.steps.length, "step", "steps")}` : "Diagram")
    : b.type === "media" ? (b.kind === "video" ? "Video" : "Image")
    : b.type === "series" ? `Chart, ${n((Array.isArray(bound(doc, b, "values")) ? bound(doc, b, "values") : []).length, "point", "points")}`
    : b.type === "key_value" ? n(Array.isArray(b.items) ? b.items.length : 0, "row", "rows")
    : b.type === "log" ? n((Array.isArray(bound(doc, b, "lines")) ? bound(doc, b, "lines") : []).length, "line", "lines")
    : b.type === "copy" ? "Text" : "Figures";
  return more ? n(doc.blocks.length, "part", "parts") : first;
}
/* the part its message is drawn with, after Onix's words (parts.js) */
function artifactPart(m) {
  const doc = artifactOf(m); if (!doc) return null; const open = !!opened && opened.mid === m.id;
  return {t: "art", html: `${ic(GLYPH[doc.blocks[0].type] || "doc")}<span class="at">${esc(titleOf(doc))}</span><span class="k">${esc(captionOf(doc))}</span><button class="lk" data-art-open aria-expanded="${open}"><span class="cell"><span>${open ? "Close" : "Open"}</span><i class="sz">Close</i></span></button>`};
}

/* ---------- the blocks, drawn ---------- */
/* a block's state, as the run marks say it: passed, live, needs a person, failed; neutral, selected and muted are no mark */
const STATE_MARK = {success: ["done", "c-ok"], running: ["live", ""], warning: ["needs", "c-att"], error: ["failed", "c-fail"]};
const stateMark = (s) => (STATE_MARK[s] ? mk(STATE_MARK[s][0], STATE_MARK[s][1]) : "");
const isDark = () => { const t = document.documentElement.dataset.theme; return t === "dark" || (t !== "light" && typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches); };
const themed = (b, field) => (isDark() && typeof b[field + "Dark"] === "string" ? b[field + "Dark"] : text(b[field]));
/* words an agent wrote: as written, line breaks kept; display maths ($$...$$ or ```math) in the code face until KaTeX is here */
const MATH = /```math[\t ]*\n([\s\S]*?)```|\$\$([\s\S]*?)\$\$/gu;
const words = (s) => esc(s).replace(MATH, (all, a, b) => `<span class="am"${a !== undefined ? " data-display" : ""} data-tex="${esc((a ?? b ?? "").trim())}">${esc((a ?? b ?? "").trim())}</span>`);
function figure(v) { const n = typeof v === "number" ? v : NaN; return Number.isFinite(n) ? n.toLocaleString("en", {maximumFractionDigits: 6}).replace(/^-/, "−") : text(v); }
const ASPECT = {wide: [16, 9], standard: [4, 3], square: [1, 1], tall: [3, 4]};
function blockHTML(doc, b, i) {
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
  if (b.type === "scene") return `<div class="ad">${esc(text(b.label))}</div>`;
  return "";
}
/* the whole document: its blocks, then its actions, each a request to Onix in this place */
function documentHTML(doc) {
  return `<div class="ablocks">${doc.blocks.map((b, i) => `<section class="ablk" data-type="${esc(b.type)}"${b.state ? ` data-state="${esc(b.state)}"` : ""}>${blockHTML(doc, b, i)}</section>`).join("")}</div>`
    + (doc.actions.length ? `<div class="aact">${doc.actions.map((a, i) => `<button class="btn" data-art-act="${i}" data-tip="${esc(a.prompt)}">${esc(a.label)}</button>`).join("")}</div>` : "");
}

/* ---------- open: the column borrowed, or a sheet ---------- */
let opened = null;   /* {mid, el, back, doc} */
const artifactOpen = () => !!opened;
function messageOf(id) { const c = msgIndex.get(id); return c ? c.byId.get(id) : null; }
function openArtifact(m, back, kbd) {
  if (opened && opened.mid === m.id) { closeArtifact(); return; }
  if (opened) closeArtifact(true);
  const doc = artifactOf(m); if (!doc) return;
  const inColumn = !!side && getComputedStyle(side).display !== "none";
  const e = el("div", inColumn ? "apanel" : "apanel sheet-chk raised float");
  e.setAttribute("role", "region"); e.setAttribute("aria-label", titleOf(doc)); e.dataset.mid = m.id;
  e.innerHTML = `<div class="aph"><b>${esc(titleOf(doc))}</b><button class="c sq" data-art-close aria-label="Close" data-tip="Close" data-key="Esc">${ic("x")}</button></div><div class="abody">${documentHTML(doc)}</div>`;
  if (inColumn) { side.append(e); side.dataset.borrowed = "artifact"; } else $("main").append(e);
  host(e); loadPics(e); typeset(e);
  opened = {mid: m.id, el: e, back: back || null, doc};
  redrawRow(m); M.release(e, {y: T["pane-rise"] || 4}, "touch"); M.appear(e, 0.6);
  if (kbd) $("[data-art-close]", e).focus();   /* from the keyboard, the next key is the panel's */
}
function closeArtifact(quiet) {
  if (!opened) return; const {el: e, mid, back} = opened; opened = null; e.inert = true;
  const done = () => { e.remove(); if (side && !side.querySelector(":scope > .apanel")) delete side.dataset.borrowed; };
  if (quiet) done(); else M.leave(e, done);
  const m = messageOf(mid); if (m) redrawRow(m);
  if (!quiet) { const b = back && back.isConnected ? back : document.querySelector(`[data-part="art"][data-mid="${mid}"] [data-art-open]`); if (b && !b.closest("[inert]")) b.focus({preventScroll: true}); else ready(); }
}
/* the row's verb says what a press will do: Open, or Close while it is open */
function redrawRow(m) { const open = !!opened && opened.mid === m.id, p = artifactPart(m);
  for (const b of $$(`[data-part="art"][data-mid="${m.id}"] [data-art-open]`)) { b.setAttribute("aria-expanded", open); $(".cell > span", b).textContent = open ? "Close" : "Open"; if (p) b.closest("[data-part]")._src = p.html; } }
/* a new artifact on a message: its row is drawn again with the message, and what is open of it is drawn again in place */
function onArtifactEvent(ev) {
  if (ev.msg_type !== "artifact") return; const c = msgIndex.get(ev.message_id), m = c && c.byId.get(ev.message_id); if (!m) return;
  m.submessages = m.submessages || []; if (m.submessages.some((s) => s.id === ev.submessage_id)) return;
  m.submessages.push({id: ev.submessage_id, sender_id: ev.sender_id, msg_type: ev.msg_type, content: ev.content});
  if (c === state.P) updateMessage(c, m);
  if (opened && opened.mid === m.id) { const doc = artifactOf(m); if (doc && doc.id !== opened.doc.id) { const body = $(".abody", opened.el); body.innerHTML = documentHTML(doc); $(".aph b", opened.el).textContent = titleOf(doc); opened.doc = doc; host(body); loadPics(body); typeset(opened.el); } }
}
/* display maths: KaTeX is fetched the first time an artifact needs it; until then (or if it cannot be had) the formula
   stands in the code face, as written */
let katexP = null;
function typeset(root) { const ms = $$(".am[data-tex]", root); if (!ms.length) return;
  katexP = katexP || Promise.all([import(/* webpackChunkName: "katex" */ "katex"), import(/* webpackChunkName: "katex" */ "katex/dist/katex.min.css")]).then(([k]) => k.default || k).catch(() => null);   /* not the old interface's katex.css: a module both bundles import is split out of its first load as a file of its own */
  katexP.then((k) => { if (!k) return; for (const x of ms) { if (!x.isConnected) continue; try { k.render(x.dataset.tex, x, {displayMode: x.hasAttribute("data-display"), throwOnError: true, trust: false, maxExpand: 1000, maxSize: 10, strict: "ignore"}); x.classList.add("on"); } catch (e) { /* the formula stays as written */ } } }); }

/* the page's presses: Open and Close on the row, Close in the panel, Copy on a log, an action; Esc closes what is open */
function wireArtifacts(root, msgOf) {
  press(root, "[data-art-open]", (b, e, ptr) => { const m = msgOf(b); if (m) openArtifact(m, b, !ptr); });
  press(document.body, ".apanel [data-art-close]", () => closeArtifact());
  press(document.body, ".apanel [data-art-copy]", (b) => { const pre = b.closest(".ablk").querySelector("pre"); try { navigator.clipboard.writeText(pre.textContent).catch(() => {}); } catch (e) { /* no clipboard */ } M.say(b, "Copied", 1200); });
  press(document.body, ".apanel [data-art-act]", (b) => { if (!opened) return; const a = opened.doc.actions[+b.dataset.artAct]; if (!a) return; send({text: a.prompt, onix: true, at: performance.now()}); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && opened && opened.el.contains(document.activeElement)) { e.preventDefault(); e.stopPropagation(); closeArtifact(); } }, true);
}

export {artifactOf, artifactOpen, artifactPart, captionOf, closeArtifact, onArtifactEvent, openArtifact, titleOf, wireArtifacts};
