/* INTERIM BETA (README.md): the agent's turn: one element for the life of an answer. */

import {$, $$, el, T} from "./util.js";
import {msgIndex, state, stats} from "./state.js";
import {dur} from "./time.js";
import {feed, lane} from "./frame.js";
import {loadPics, partEl, partsOf, streamHTML, tpl} from "./parts.js";
import {dist, edge, finish, grown, hugAll, hush, pin, sectionOf, syncJump, toHug} from "./feed.js";
import {runOfMsg} from "./runs.js";
import {drawTurnPlan} from "./checklist.js";
import {appendCode, codeBlock, setCode} from "./code_blocks.js";
import {drawReactions} from "./reactions.js";
import {drawLab} from "./lab.js";
import {wordsChanged} from "./hover.js";
import {api} from "./connection.js";

/* ----- Onix's turn: one element for the life of an answer ----- */
/* where the working mark rides: the end of the last words, in a paragraph or in a list's last item */
function tailOf(ans) { let e = ans.lastElementChild; for (let i = 0; i < 6 && e && /^(UL|OL)$/.test(e.tagName); i++) { const li = e.lastElementChild; if (!li) return null; const s = li.lastElementChild; if (!s || s.tagName !== "SPAN") return li; const inner = s.lastElementChild; if (inner && /^(UL|OL)$/.test(inner.tagName)) { e = inner; continue; } return s; } return e && !/^(UL|OL)$/.test(e.tagName) ? e : null; }
function placeholderAns(m) { const a = el("div", "ans"); a.dataset.mid = m.id; a.dataset.part = "ans"; a._src = ""; const p = el("p"); p._src = ""; a.append(p); return a; }
const WORKING = (m) => /^·\s*Working\s*$/.test((m.content || "").replace(/<[^>]+>/g, "").trim());
const msgLive = (c, m) => { if (m.local) return true; const r = runOfMsg(c, m.id); return r ? r.status === "queued" || r.status === "running" : !!m.maybeLive; };   /* a message whose run is not known yet is live if it arrived as the server's placeholder */
/* the working mark rides the end of the words while the run is live; a settled run leaves its record. The footer line is
   there from the run's start (statement 26: "Working · 2 tools" becomes "Ran 41s · 3 tools"), so the turn's height does
   not change when the run settles, under the words or under anything said after them. */
function syncOnix(cl, m) {
  const c = msgIndex.get(m.id) || state.P, live = msgLive(c, m), r = runOfMsg(c, m.id); let head = $(".head:not(.gone)", cl);
  if (!live && cl._stream) { cl._stream = null; endStream(m.id); morphOnix(cl, m); head = $(".head:not(.gone)", cl); }   /* the run is over: the server's rendering of the answer is the answer */
  cl.classList.toggle("live", live); drawTurnPlan(cl, r);
  if (live) { const s = liveText.get(m.id); if (s && s.base) { if (cl._stream !== s) writeStream(cl, m, s); else streamHead(cl, m, s.w); } else placeHead(cl, m); watchGrowth(cl); }
  else { if (head) { head.classList.add("gone"); M.leave(head); }   /* the mark fades where it stood: it takes no room in its line */
    /* a run stopped before it said anything says so on the one line its words would have had, and the footer says for how long */
    const said = $$(":scope > .ans", cl).some((a) => a.textContent.trim() || a.querySelector("img")) || !!$(":scope > [data-part]:not(.ans)", cl);
    if (r && r.status === "cancelled" && !said) { const a = $$(":scope > .ans", cl).pop() || cl.appendChild(placeholderAns(m)); let p = $("p", a); if (!p) { p = el("p"); a.append(p); } p.prepend(document.createTextNode("Stopped.")); p._src = "Stopped."; a._src = "<p>Stopped.</p>"; }
    $$(":scope > .ans", cl).forEach((a) => { if (!a.textContent.trim() && !a.querySelector("img")) a.remove(); }); }
  drawFooter(cl, live, r); wordsChanged(cl);
}
/* the working mark rides the end of the newest words: it moves only when they start a new line of their own (a
   paragraph, a list's item), so its loop runs on */
function placeHead(cl, m) { let head = $(".head:not(.gone)", cl); const ans = $$(":scope > .ans", cl).pop() || cl.appendChild(placeholderAns(m)); let p = tailOf(ans); if (!p) { p = el("p"); p._src = ""; ans.append(p); }
  if (!head) { head = el("span", "head", wk(true)); p.append(head); } else if (head.parentNode !== p) p.append(head); else if (head.nextSibling && !(head.nextSibling.classList && head.nextSibling.classList.contains("rca"))) p.append(head);
  head.classList.toggle("first", !p.textContent.trim()); }
const toolsOf = (r) => (r && r.tool_count ? ` · ${r.tool_count} ${r.tool_count === 1 ? "tool" : "tools"}` : "");
function footHTML(live, r) {
  if (live) return `<span>${r && r.waiting ? "Waiting for an answer" : r && r.status === "queued" ? "Queued" : "Working"}${toolsOf(r)}</span>`;
  if (!r) return "";
  const secs = (r.usage && r.usage.duration_seconds) || r.duration_seconds || ((r.date_finished || r.date_updated) - r.date_created);
  if (r.status === "failed") return `${mk("failed")}<span class="c-fail">Failed after ${dur(secs)}</span>`;
  if (r.status === "cancelled") return `${mk("cancelled")}<span>Stopped after ${dur(secs)}</span>`;
  return `<span>Ran ${dur(secs)}${toolsOf(r)}</span>`;
}
/* right after the words. A turn whose run is not known (its runs are not read yet, or it answered outside a run) has no
   footer until it is; one that had a footer keeps its line, saying only that it is done. */
function drawFooter(cl, live, r) { const old = $(":scope > .line.foot", cl), html = footHTML(live, r) || (old && !live ? "<span>Done</span>" : "");
  if (!html) return null;
  if (old) { if (old._sig !== html) { old.innerHTML = html; old._sig = html; } return old; }
  const f = el("div", "line foot", html); f._sig = html; const tl = $(":scope > .tl", cl), lastPart = $$(":scope > [data-part]", cl).pop(); if (tl) tl.before(f); else if (lastPart) lastPart.after(f); else cl.append(f); cl.classList.add("run"); return f; }
/* the place's runs have been read: every turn takes its footer and its checklist. They land above what is in view, where
   the browser's scroll anchoring would move the scroll position under a pinned feed and unpin it; a pinned feed is
   pinned again in the same task, so its end stays its end. */
function drawFooters() { for (const cl of $$(".cl.onix[data-mid]", lane)) { const m = state.P.byId.get(+cl.dataset.mid) || state.P.byId.get(cl.dataset.mid); if (m) syncOnix(cl, m); }
  if (state.pinned && state.tab === "Chat") pin(); }
/* the server edits Onix's message as it writes. The new rendering is followed into the same elements: a paragraph that
   grew gets only its new words (they arrive on the ink's fade); a paragraph that changed is rewritten in place; parts
   that are new are added after the last. The row is never replaced. */
function morphOnix(cl, m) {
  const parts = partsOf(m), ex = $$(`:scope > [data-part][data-mid="${m.id}"]`, cl), head = $(".head:not(.gone)", cl), rca = $(".rca", cl); if (rca) rca.remove();
  let wrote = false, lastEl = null;
  parts.forEach((p, i) => { const old = ex[i];
    if (old && old.dataset.part === p.t) { lastEl = old; if (old._src === p.html) return; wrote = true; if (p.t === "ans") morphAns(old, p.html, head); else if (p.t === "pre") setCode(old, p.html); else { old.innerHTML = p.html; loadPics(old); } old._src = p.html; return; }
    const e = partEl(p, m); wrote = true; if (old) { if (head && old.contains(head)) head.remove(); old.replaceWith(e); } else if (lastEl) lastEl.after(e); else { const anchor = $(":scope > .plan", cl) || $(":scope > .who", cl) || $(":scope > .sigil", cl); anchor ? anchor.after(e) : cl.prepend(e); } lastEl = e; if (!M.reduced()) M.appear(e, 0.45); });
  ex.slice(parts.length).forEach((x) => { if (head && x.contains(head)) head.remove(); x.remove(); });
  return wrote;
}
function morphAns(ans, html, head) {
  tpl.innerHTML = html; const neu = [...tpl.content.children], old = [...ans.children];
  neu.forEach((n, j) => { const o = old[j], nh = n.innerHTML;
    if (o && o.tagName === n.tagName) { const oh = o._src ?? ""; if (oh === nh) return;
      if (n.tagName === "P" && nh.startsWith(oh)) { const s = el("span", "wn", nh.slice(oh.length)); if (head && head.parentNode === o) head.before(s); else o.append(s); if (M.reduced()) s.replaceWith(...s.childNodes); else M.appear(s, 0.45).finished.then(() => s.parentNode && s.replaceWith(...s.childNodes), () => {}); }
      else { if (head && o.contains(head)) head.remove(); o.innerHTML = nh; }
      o._src = nh; return; }
    n._src = nh; if (o) { if (head && o.contains(head)) head.remove(); o.replaceWith(n); } else ans.append(n); });
  old.slice(neu.length).forEach((x) => { if (head && x.contains(head)) head.remove(); x.remove(); });
}
/* a message's new rendering, from the server: a person's edit, or Onix's message saved while it writes and at the end.
   While its words stream (below) the saved copy is only kept: it is older than what is shown. Height changes reach the
   ResizeObserver below, which follows them; nothing here reads layout. */
function updateMessage(c, m, firstWordsAt) {
  if (c !== state.P) return; const cl = sectionOf(m.id); if (!cl) return;   /* a reply in a thread that is not open */
  if (!cl.classList.contains("onix")) {   /* a person's edit: that message's parts are rebuilt in place */
    const write = () => { const old = $$(`:scope > [data-part][data-mid="${m.id}"]`, cl), parts = partsOf(m).map((p) => partEl(p, m)); if (old.length) { old[0].before(...parts); old.forEach((x) => x.remove()); } else cl.append(...parts); drawReactions(cl, m); finish(cl); hush(cl); toHug.add(cl); };
    if (cl.closest(".thr-in")) { write(); hugAll(); } else grown(write, "move"); return; }
  const s = liveText.get(m.id); if (s && s.base && msgLive(c, m)) { if (cl._stream !== s || s.dirty) writeStream(cl, m, s); return; }
  const hadWords = !!cl.querySelector(".ans") && cl.querySelector(".ans").textContent.trim().length > 0;
  const wrote = morphOnix(cl, m); syncOnix(cl, m); drawReactions(cl, m); loadPics(cl);
  if (wrote && firstWordsAt && !hadWords && cl.textContent.trim()) firstWords(firstWordsAt);
}
const firstWords = (t) => requestAnimationFrame(() => setTimeout(() => { stats.firstWords.push(Math.round((performance.now() - t) * 10) / 10); drawLab(); }, 0));
/* a live answer's row as it grows: the conversation follows it, from the size the browser has just laid out. Made at
   the first live row, after the page's own observers, so theirs read the lane before this one moves it */
let grows = null;
function watchGrowth(cl) { grows ??= new ResizeObserver((entries) => { for (const e of entries) { const row = e.target, h = e.borderBoxSize ? e.borderBoxSize[0].blockSize : e.contentRect.height, was = row._h; row._h = h;
  if (!row.isConnected || !row.classList.contains("live")) { grows.unobserve(row); row._h = undefined; }
  if (was !== undefined && h !== was && row.isConnected) follow(h - was, row); } });
  grows.observe(cl); }

/* ----- Onix's words as they stream (agent_stream events) -----
   Each live answer's text, by message id, for every place: what arrives for a place that is not shown is kept, and
   written when its row is drawn. An event only appends; one animation frame writes everything that has arrived. The
   text is cut into blocks (a blank line outside a fence ends one; a fence is one): a block that has ended is written
   once and not again, so a frame writes only the open block, and only its new words where it can. */
const liveText = new Map();   /* message id -> {run, item, text, base, dirty, at, held} */
let frameDue = 0;
const writeSoon = () => { if (!frameDue) frameDue = requestAnimationFrame(writeStreams); };
function onAgentStream(ev, at) {
  let s = liveText.get(ev.message_id);
  if (ev.offset === 0) { if (!s || s.item !== ev.item || s.text !== ev.text) { s = {run: ev.run_id, item: ev.item, text: ev.text, base: true, dirty: true, at, w: null, held: []}; liveText.set(ev.message_id, s); } }
  else if (s && s.base && s.item === ev.item && ev.offset === s.text.length) { s.text += ev.text; s.dirty = true; }
  else if (s && s.base && s.item === ev.item && ev.offset < s.text.length) {   /* had some already: a start joined from the server's copy runs ahead */
    if (ev.offset + ev.text.length <= s.text.length) return; s.text += ev.text.slice(s.text.length - ev.offset); s.dirty = true; }
  else {   /* words with nothing to follow: the page opened after the item began, or some were missed. The saved message is
              shown, and these are kept for the start the server holds (join) */
    if (!s || s.item !== ev.item) { s = {run: ev.run_id, item: ev.item, text: "", base: false, dirty: false, at, w: null, held: []}; liveText.set(ev.message_id, s); }
    else if (s.base) { s.base = false; s.held = []; s.w = null; }   /* the saved copies draw the row now; the writer starts again */
    if (s.held.length < 2000) s.held.push([ev.offset, ev.text]);
    join(ev.message_id, s); return; }
  writeSoon();
}
/* the start of an item a page did not see begin: its text as of the run's last save, which the live run holds (GET
   armada/runs, the run's "live"), with the words kept since put after it. Asked at most every 2 s (a save is that
   often) and five times an item; a start the kept words do not follow from is not taken. */
async function join(id, s) {
  if (s.joining || s.tries >= 5 || performance.now() - (s.triedAt || -1e9) < 2000) return;
  s.joining = true; s.tries = (s.tries || 0) + 1; s.triedAt = performance.now(); let r = null;
  try { r = await api("GET", "armada/runs", {run_ids: [s.run]}); } catch (e) {}
  s.joining = false; if (!r || liveText.get(id) !== s || s.base) return;
  const run = (r.armada_runs || []).find((x) => x.id === s.run), st = run && run.live && run.live.stream;
  if (!st || st.message_id !== id || st.item !== s.item) return;
  let text = st.text;
  for (const [o, t] of s.held) { if (o > text.length || text.slice(o, o + t.length) !== t.slice(0, text.length - o)) return;
    if (o + t.length > text.length) text += t.slice(text.length - o); }
  s.text = text; s.base = true; s.held = []; s.dirty = true; writeSoon();
}
function writeStreams() { frameDue = 0;
  for (const [id, s] of liveText) { if (!s.dirty || !s.base) continue; const c = msgIndex.get(id); if (c !== state.P) continue;   /* drawn when its place is */
    const m = c.byId.get(id), cl = s.w && s.w.cl.isConnected ? s.w.cl : sectionOf(id); if (m && cl && msgLive(c, m)) writeStream(cl, m, s); } }
/* the run is over (or the page's queue was lost): its words are the saved message's from now on */
function endStream(id) { liveText.delete(id); }
function endRun(runId) { for (const [id, s] of liveText) if (s.run === runId) liveText.delete(id); }
function resetStreams() { liveText.clear(); }

/* the writer, for one row: `scan` is how far the text's whole lines have been read, `start` where the open block starts
   (in a fence when `fence` is its marker), `parts` the answer's parts written, in order, `ans` the prose part words go
   into, and the open block's own elements: its prose (`kids`, inside `ans`), its code (`blk`), or its other parts
   (`open`: a table, a picture) */
function writeStream(cl, m, s) {
  let w = s.w; s.dirty = false;
  if (!w || w.cl !== cl || w.item !== s.item) {   /* a row the writer has not written, or a new item: its words start over */
    const head = $(".head:not(.gone)", cl); if (head) head.remove(); const rca = $(".rca", cl); if (rca) rca.remove();
    $$(`:scope > [data-part][data-mid="${m.id}"]`, cl).forEach((x) => x.remove());
    w = s.w = {cl, item: s.item, scan: 0, start: 0, fence: null, parts: [], ans: null, kids: [], blk: null, open: [], code: 0}; cl._stream = s; if (!s.started) { s.started = true; firstWords(s.at); } }
  const text = s.text;
  for (let nl = text.indexOf("\n", w.scan); nl >= 0; nl = text.indexOf("\n", w.scan)) { const line = text.slice(w.scan, nl);
    if (w.fence) { if (new RegExp(`^\\s{0,3}${w.fence[0] === "`" ? "`" : "~"}{${w.fence.length},}\\s*$`).test(line)) { closeBlock(cl, m, w, text.slice(w.start, nl + 1)); w.start = nl + 1; w.fence = null; } }
    else if (!line.trim()) { if (text.slice(w.start, w.scan).trim()) closeBlock(cl, m, w, text.slice(w.start, w.scan)); w.start = nl + 1; }
    else { const f = /^\s{0,3}(`{3,}|~{3,})/.exec(line); if (f) { if (text.slice(w.start, w.scan).trim()) closeBlock(cl, m, w, text.slice(w.start, w.scan)); w.start = w.scan; w.fence = f[1]; } }
    w.scan = nl + 1; }
  const open = text.slice(w.start), f = w.fence || (/^\s{0,3}(`{3,}|~{3,})/.exec(open) || [])[1];
  if (f) writeFence(cl, m, w, open); else if (open.trim()) writeBlock(cl, m, w, open, false);
  streamHead(cl, m, w); wordsChanged(cl);
}
/* the working mark stands after the newest words: the end of the prose, or of the code being written */
function streamHead(cl, m, w) { const last = w.parts[w.parts.length - 1], p = !last ? null : last.classList.contains("ans") ? tailOf(last) : last.tagName === "PRE" ? $(":scope > .code", last) : null;
  if (!p) { placeHead(cl, m); return; }
  let head = $(".head:not(.gone)", cl); if (!head) head = el("span", "head", wk(true)); if (head.parentNode !== p || head.nextSibling) p.append(head); head.classList.remove("first"); }
/* a block that has ended: written a last time in place, then never again */
function closeBlock(cl, m, w, src) { if (/^\s{0,3}(`{3,}|~{3,})/.test(src)) writeFence(cl, m, w, src, true); else writeBlock(cl, m, w, src, true); }
function writeBlock(cl, m, w, src, final) {
  const parts = partsOf({sender_id: m.sender_id, content: streamHTML(src)});
  const head = $(".head:not(.gone)", cl);
  if (parts.length === 1 && parts[0].t === "ans") {   /* prose: into the answer's prose part, after the prose before it */
    if (w.open.length) { w.open.forEach((e) => drop(w, e, head)); w.open = []; if (w.ans && !w.ans.isConnected) w.ans = null; }   /* what it was drawn as while it looked like a table */
    if (!w.ans) { w.ans = el("div", "ans"); w.ans.dataset.mid = m.id; w.ans.dataset.part = "ans"; w.ans._src = ""; put(cl, w, w.ans); }
    tpl.innerHTML = parts[0].html; const neu = [...tpl.content.children];
    neu.forEach((n, j) => { const o = w.kids[j]; if (o && o.tagName === n.tagName) extend(o, n, head); else { n._src = n.innerHTML; if (o) { if (head && o.contains(head)) head.remove(); o.replaceWith(n); } else w.ans.append(n); w.kids[j] = n; } });
    w.kids.splice(neu.length).forEach((x) => { if (head && x.contains(head)) head.remove(); x.remove(); });
  } else {   /* a block of its own (a table, a picture), or prose and a block together: drawn whole each frame until it ends */
    w.kids.forEach((x) => { if (head && x.contains(head)) head.remove(); x.remove(); }); w.kids = [];
    const els = parts.map((p) => partEl(p, m)), was = w.open;
    if (was.length === 1 && els.length === 1) { was[0].replaceWith(els[0]); w.parts[w.parts.indexOf(was[0])] = els[0]; }   /* in place: no fade again */
    else { was.forEach((e) => drop(w, e, head)); els.forEach((e) => put(cl, w, e, !was.length)); }
    w.open = els; w.ans = els.length && parts[parts.length - 1].t === "ans" ? els[els.length - 1] : null;
  }
  if (final) { w.kids = []; w.open = []; }
}
/* a fence: one code block; while it is open its new characters are appended as text */
function writeFence(cl, m, w, src, final = false) {
  const nl = src.indexOf("\n"), code = nl < 0 ? "" : src.slice(nl + 1).replace(final ? /\n?\s{0,3}(`{3,}|~{3,})\s*\n?$/ : /$^/, ""), head = $(".head:not(.gone)", cl);
  if (!w.blk) {   /* the fence opens: what its first line was drawn as goes */
    w.kids.forEach((x) => { if (head && x.contains(head)) head.remove(); x.remove(); }); w.kids = []; w.open.forEach((e) => drop(w, e, head)); w.open = []; w.ans = null;
    w.blk = codeBlock(code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")); w.blk.dataset.mid = m.id; w.blk.dataset.part = "pre"; put(cl, w, w.blk); w.code = code.length; }
  else if (final) { const html = partsOf({sender_id: m.sender_id, content: streamHTML(src)}).find((p) => p.t === "pre"); if (head && w.blk.contains(head)) head.remove(); setCode(w.blk, html ? html.html : code); }
  else if (code.length > w.code) { appendCode(w.blk, code.slice(w.code), head && head.parentNode === $(":scope > .code", w.blk) ? head : null); w.code = code.length; }
  if (final) { w.blk = null; w.code = 0; }
}
/* a new part goes after the last one written, or where the answer starts */
function put(cl, w, e, fade = true) { const after = w.parts[w.parts.length - 1];
  if (after) after.after(e); else { const anchor = $(":scope > .plan", cl) || $(":scope > .who", cl) || $(":scope > .sigil", cl); anchor ? anchor.after(e) : cl.prepend(e); }
  w.parts.push(e); if (fade && !M.reduced()) M.appear(e, 0.45); }
function drop(w, e, head) { if (head && e.contains(head)) head.remove(); e.remove(); w.parts.splice(w.parts.indexOf(e), 1); }
/* o is on the page and n is its new rendering, of the same tag: what only grew gets its new words in before the mark (on
   the ink's fade); a list grows by its last item and new ones; anything else is written again */
const srcOf = (x, head) => x._src ?? (head && x.contains(head) ? x.innerHTML.replace(head.outerHTML, "") : x.innerHTML);
function extend(o, n, head, depth = 0) {
  const oh = srcOf(o, head), nh = n.innerHTML; if (oh === nh) return;
  if (nh.startsWith(oh)) { const sp = el("span", "wn", nh.slice(oh.length)); if (head && head.parentNode === o) head.before(sp); else o.append(sp); if (!M.reduced()) M.appear(sp, 0.45); o._src = nh; return; }
  const ok = [...o.children].filter((x) => x !== head && !x.classList.contains("wn")), nk = [...n.children];
  if (depth < 4 && /^(UL|OL|LI)$/.test(o.tagName) && o.childNodes.length === ok.length + (head && head.parentNode === o ? 1 : 0) && nk.length >= ok.length && ok.length && ok.slice(0, -1).every((x, i) => srcOf(x, head) === nk[i].innerHTML && x.tagName === nk[i].tagName) && ok[ok.length - 1].tagName === nk[ok.length - 1].tagName) {
    extend(ok[ok.length - 1], nk[ok.length - 1], head, depth + 1); for (const x of nk.slice(ok.length)) { x._src = x.innerHTML; o.append(x); } o._src = nh; return; }
  if (head && o.contains(head)) head.remove(); o.innerHTML = nh; o._src = nh;
}
/* a pinned feed follows the stream, never by more than a line's spring, until the answer's first line would leave the
   top; then it holds, and Jump to latest says there is more */
function follow(d, cl) { if (!state.pinned || state.tab !== "Chat") { syncJump(); return; }
  const top = M.rest(cl).top, limit = feed.getBoundingClientRect().top + 20;
  if (feed.scrollHeight > feed.clientHeight + 1 && top - d < limit) { const part = Math.max(0, Math.round(top - limit)); if (part > 0) { feed.scrollTop += part; state.lastSet = feed.scrollTop; edge(); M.release(lane, {y: part}, "touch", {clamp: {y: T.follow || 24}}); } state.pinned = dist() < 4; syncJump(); return; }
  pin(); edge(); M.release(lane, {y: d}, "touch", {clamp: {y: Math.max(T.follow || 24, 28)}}); }

export {drawFooters, endRun, follow, onAgentStream, placeholderAns, resetStreams, syncOnix, updateMessage, WORKING};
