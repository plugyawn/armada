/* INTERIM BETA (README.md): Onix's artifacts. A run that calls its `armada_artifact` tool attaches a document to its final
   message as a submessage (msg_type "artifact": tools/armada/artifact_contract.json, zerver/lib/armada_artifact.py): a
   title, data, up to six blocks (copy, metric, meter, series, log, key_value, media, scene) and up to four actions. In the
   conversation it is the design's block, beside Onix's words: a kind glyph, a title, one caption, one verb, 36 high. Open
   shows the whole document in the column, which it borrows and gives back (Close, Esc, or Open again); below 1408, where
   there is no column, in a raised sheet. The last artifact of the message's own sender is the one shown, and one that
   arrives later replaces it in place. The document's drawing (artifact_view.js) and a scene's (scene.js) are chunks of
   their own, loaded on first use (chunks.js). */

import {$, $$, esc, press, T} from "./util.js";
import {msgIndex, state} from "./state.js";
import {side} from "./frame.js";
import {ready} from "./composer.js";
import {send} from "./send.js";
import {updateMessage} from "./agent_turn.js";
import {chunk, loadedChunk} from "./chunks.js";
import {closeWidgetViews} from "./plugins.js";

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

/* words an agent wrote: as written, line breaks kept; display maths ($$...$$ or ```math) in the code face until KaTeX is here */
const MATH = /```math[\t ]*\n([\s\S]*?)```|\$\$([\s\S]*?)\$\$/gu;
const words = (s) => esc(s).replace(MATH, (all, a, b) => `<span class="am"${a !== undefined ? " data-display" : ""} data-tex="${esc((a ?? b ?? "").trim())}">${esc((a ?? b ?? "").trim())}</span>`);

/* ---------- open: the column borrowed, or a sheet ---------- */
let opened = null;   /* {mid, el, back, doc} */
const artifactOpen = () => !!opened;
function messageOf(id) { const c = msgIndex.get(id); return c ? c.byId.get(id) : null; }
/* Open: whole in the frame of the press once the document's chunk (and a scene's) has arrived, which the page fetches when
   idle (chunks.js); a press before that shows nothing new and opens it when they arrive, unless I have pressed Open on
   another, Esc, or left the place meanwhile */
let wanted = null;
function openArtifact(m, back, kbd) {
  if (opened && opened.mid === m.id) { closeArtifact(); return; }
  const doc = artifactOf(m); if (!doc) return; const scene = doc.blocks.some((b) => b.type === "scene");
  const V = loadedChunk("artifact"), S = scene ? loadedChunk("scene") : null;
  if (V && (S || !scene)) { show(m, doc, V, S, back, kbd); return; }
  const ask = (wanted = {mid: m.id});
  Promise.all([chunk("artifact"), scene ? chunk("scene") : null]).then(([V2, S2]) => { if (wanted !== ask) return; wanted = null; const d = artifactOf(m); if (d) show(m, d, V2, S2, back, kbd); }, () => { if (wanted === ask) wanted = null; });
}
function show(m, doc, V, S, back, kbd) {
  if (opened) closeArtifact(true); closeWidgetViews();   /* a plugin's item gives the column back first */
  const inColumn = !!side && getComputedStyle(side).display !== "none", e = V.panel(doc, titleOf(doc), inColumn, S); e.dataset.mid = m.id;
  if (inColumn) { side.append(e); side.dataset.borrowed = "artifact"; } else $("main").append(e);
  V.mount(e, doc, S);
  opened = {mid: m.id, el: e, back: back || null, doc};
  redrawRow(m); M.release(e, {y: T["pane-rise"] || 4}, "touch"); M.appear(e, 0.6);
  if (kbd) $("[data-art-close]", e).focus();   /* from the keyboard, the next key is the panel's */
}
function closeArtifact(quiet) {
  wanted = null; if (!opened) return; const {el: e, mid, back} = opened; opened = null; e.inert = true;
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
  if (opened && opened.mid === m.id) { const doc = artifactOf(m), was = opened; if (!doc || doc.id === was.doc.id) return;
    const scene = doc.blocks.some((b) => b.type === "scene");
    Promise.all([chunk("artifact"), scene ? chunk("scene") : null]).then(([V, S]) => { if (opened !== was) return; V.replace(was.el, doc, titleOf(doc), S); was.doc = doc; }, () => {}); }
}
/* the page's presses: Open and Close on the row, Close in the panel, Copy on a log, an action; Esc closes what is open */
function wireArtifacts(root, msgOf) {
  press(root, "[data-art-open]", (b, e, ptr) => { const m = msgOf(b); if (m) openArtifact(m, b, !ptr); });
  press(document.body, ".apanel [data-art-close]", () => closeArtifact());
  press(document.body, ".apanel [data-art-copy]", (b) => { const pre = b.closest(".ablk").querySelector("pre"); try { navigator.clipboard.writeText(pre.textContent).catch(() => {}); } catch (e) { /* no clipboard */ } M.say(b, "Copied", 1200); });
  press(document.body, ".apanel [data-art-act]", (b) => { if (!opened) return; const a = opened.doc.actions[+b.dataset.artAct]; if (!a) return; send({text: a.prompt, onix: true, at: performance.now()}); });
  document.addEventListener("keydown", (e) => { if (e.key !== "Escape") return; wanted = null; if (opened && opened.el.contains(document.activeElement)) { e.preventDefault(); e.stopPropagation(); closeArtifact(); } }, true);
}

export {artifactOf, artifactOpen, artifactPart, bound, captionOf, closeArtifact, dataAt, isObj, onArtifactEvent, openArtifact, text, titleOf, wireArtifacts, words};
