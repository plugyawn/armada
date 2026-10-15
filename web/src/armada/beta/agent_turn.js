/* INTERIM BETA (README.md): the agent's turn: one element for the life of an answer. */

import {$, $$, el, T} from "./util.js";
import {msgIndex, state, stats} from "./state.js";
import {dur} from "./time.js";
import {feed, lane} from "./frame.js";
import {loadPics, partEl, partsOf, tpl} from "./parts.js";
import {dist, drawLane, edge, finish, grown, hugAll, hush, pin, sectionOf, syncJump, toHug} from "./feed.js";
import {runOfMsg} from "./runs.js";
import {drawReactions} from "./reactions.js";
import {drawLab} from "./lab.js";

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
  cl.classList.toggle("live", live);
  if (live) { const ans = $$(":scope > .ans", cl).pop() || cl.appendChild(placeholderAns(m)); let p = tailOf(ans); if (!p) { p = el("p"); p._src = ""; ans.append(p); }
    if (!head) { head = el("span", "head", wk(true)); p.append(head); } else if (head.parentNode !== p) p.append(head); else if (head.nextSibling && !(head.nextSibling.classList && head.nextSibling.classList.contains("rca"))) p.append(head);
    head.classList.toggle("first", !p.textContent.trim()); }
  else { if (head) { head.classList.add("gone"); M.leave(head); }   /* the mark fades where it stood: it takes no room in its line */
    /* a run stopped before it said anything says so on the one line its words would have had, and the footer says for how long */
    const said = $$(":scope > .ans", cl).some((a) => a.textContent.trim() || a.querySelector("img")) || !!$(":scope > [data-part]:not(.ans)", cl);
    if (r && r.status === "cancelled" && !said) { const a = $$(":scope > .ans", cl).pop() || cl.appendChild(placeholderAns(m)); let p = $("p", a); if (!p) { p = el("p"); a.append(p); } p.prepend(document.createTextNode("Stopped.")); p._src = "Stopped."; a._src = "<p>Stopped.</p>"; }
    $$(":scope > .ans", cl).forEach((a) => { if (!a.textContent.trim() && !a.querySelector("img")) a.remove(); }); }
  drawFooter(cl, live, r);
}
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
function drawFooters() { for (const cl of $$(".cl.onix[data-mid]", lane)) { const m = state.P.byId.get(+cl.dataset.mid) || state.P.byId.get(cl.dataset.mid); if (m) syncOnix(cl, m); } }
/* the server edits Onix's message as it writes. The new rendering is followed into the same elements: a paragraph that
   grew gets only its new words (they arrive on the ink's fade); a paragraph that changed is rewritten in place; parts
   that are new are added after the last. The row is never replaced. */
function morphOnix(cl, m) {
  const parts = partsOf(m), ex = $$(`:scope > [data-part][data-mid="${m.id}"]`, cl), head = $(".head:not(.gone)", cl), rca = $(".rca", cl); if (rca) rca.remove();
  let wrote = false, lastEl = null;
  parts.forEach((p, i) => { const old = ex[i];
    if (old && old.dataset.part === p.t) { lastEl = old; if (old._src === p.html) return; wrote = true; if (p.t === "ans") morphAns(old, p.html, head); else { old.innerHTML = p.html; loadPics(old); } old._src = p.html; return; }
    const e = partEl(p, m); wrote = true; if (old) { if (head && old.contains(head)) head.remove(); old.replaceWith(e); } else if (lastEl) lastEl.after(e); else { const anchor = $(":scope > .who", cl) || $(":scope > .sigil", cl); anchor ? anchor.after(e) : cl.prepend(e); } lastEl = e; if (!M.reduced()) M.appear(e, 0.45); });
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
function updateMessage(c, m, firstWordsAt) {
  if (c !== state.P) return; const cl = sectionOf(m.id); if (!cl) { if (!m.thread_root_message_id) drawLane({keep: !state.pinned}); return; }
  if (!cl.classList.contains("onix")) {   /* a person's edit: that message's parts are rebuilt in place */
    const write = () => { const old = $$(`:scope > [data-part][data-mid="${m.id}"]`, cl), parts = partsOf(m).map((p) => partEl(p, m)); if (old.length) { old[0].before(...parts); old.forEach((x) => x.remove()); } else cl.append(...parts); drawReactions(cl, m); finish(cl); hush(cl); toHug.add(cl); };
    if (cl.closest(".thr-in")) { write(); hugAll(); } else grown(write, "move"); return; }
  const h0 = feed.scrollHeight, hadWords = !!cl.querySelector(".ans") && cl.querySelector(".ans").textContent.trim().length > 0;
  const wrote = morphOnix(cl, m); syncOnix(cl, m); drawReactions(cl, m); loadPics(cl);
  if (wrote && firstWordsAt && !hadWords && cl.textContent.trim()) { const t = firstWordsAt; requestAnimationFrame(() => setTimeout(() => { stats.firstWords.push(Math.round((performance.now() - t) * 10) / 10); drawLab(); }, 0)); }
  const h1 = feed.scrollHeight; if (h1 !== h0) follow(h1 - h0, cl);
}
/* a pinned feed follows the stream, never by more than a line's spring, until the answer's first line would leave the
   top; then it holds, and Jump to latest says there is more */
function follow(d, cl) { if (!state.pinned || state.tab !== "Chat") { syncJump(); return; }
  const top = M.rest(cl).top, limit = feed.getBoundingClientRect().top + 20;
  if (feed.scrollHeight > feed.clientHeight + 1 && top - d < limit) { const part = Math.max(0, Math.round(top - limit)); if (part > 0) { feed.scrollTop += part; state.lastSet = feed.scrollTop; edge(); M.release(lane, {y: part}, "touch", {clamp: {y: T.follow || 24}}); } state.pinned = dist() < 4; syncJump(); return; }
  pin(); edge(); M.release(lane, {y: d}, "touch", {clamp: {y: Math.max(T.follow || 24, 28)}}); }

export {drawFooters, follow, placeholderAns, syncOnix, updateMessage, WORKING};
