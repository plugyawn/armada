/* INTERIM BETA (README.md): threads, opened in place. */

import {$, $$, el, esc, T} from "./util.js";
import {state, stats} from "./state.js";
import {age, now} from "./time.js";
import {api} from "./connection.js";
import {addMsg} from "./routing.js";
import {feed, lane} from "./frame.js";
import {faceOf, nameOf} from "./faces.js";
import {echoHTML, plainOf} from "./parts.js";
import {ready} from "./composer.js";
import {appendMessage, dist, edge, grown, haltLane, hugAll, releaseRows, sectionOf, syncJump} from "./feed.js";
import {wireOf} from "./mention_text.js";
import {transmit} from "./outbox.js";
import {localSubmessages} from "./widgets.js";
import {agentCanRun} from "./runs.js";

/* ----- threads: the root says how many replies it has; a press opens them under it, in the conversation ----- */
const repliesOf = (c, id) => c.msgs.filter((m) => m.thread_root_message_id === id);
/* under my own right-aligned bubbles a thread line starts where its words end, at a fraction of a CSS px. Its width is rounded
   up to a whole px once it is laid out (before it is painted, with no forced layout), so its faces land on whole CSS px
   (SPRITES.md section 5) */
const wholeWidth = typeof ResizeObserver === "function" ? new ResizeObserver((es) => { for (const e of es) { const t = e.target, w = e.borderBoxSize && e.borderBoxSize[0] ? e.borderBoxSize[0].inlineSize : t.getBoundingClientRect().width; if (w % 1) t.style.width = Math.ceil(w) + "px"; wholeWidth.unobserve(t); } }) : null;
function drawThreadLine(cl, m) {
  $$(`.tl[data-mid="${m.id}"]`, cl).forEach((x) => x.remove()); const c = state.P, loaded = repliesOf(c, m.id), s = m.reply_summary, n = Math.max(loaded.length, s ? s.reply_count || 0 : 0); if (!n) return;
  const last = loaded[loaded.length - 1], who = s && s.participants ? s.participants.map((p) => p.user_id) : [...new Set(loaded.map((x) => x.sender_id))];
  const working = last && c.runs && c.runs.find((r) => r.result_message_id === last.id && (r.status === "queued" || r.status === "running"));   /* a reply still being written says what Onix is thinking about, not the server's placeholder */
  const said = working ? working.overview || "working" : last ? plainOf(last.content).replace(/\s+/g, " ").trim().slice(0, 80) : "";
  const snippet = last ? `${nameOf(last.sender_id).split(/[ ,]/)[0]}: ${said}` : s && s.last_sender ? `${nameOf(s.last_sender.user_id).split(/[ ,]/)[0]} replied ${age(s.last_reply_at)} ago` : "";
  const tl = el("div", "tl", `<span class="avs">${who.slice(0, 3).map((i) => faceOf(i, "s18")).join("")}</span><button class="lk${c.open.has(m.id) ? " on" : ""}" data-thread="${m.id}">${n} ${n === 1 ? "reply" : "replies"}</button><span class="sn">${esc(snippet)}</span>`); tl.dataset.mid = m.id;
  const own = $$(`:scope > [data-mid="${m.id}"]:not(.tl):not(.thr-in)`, cl); (own[own.length - 1] || cl.lastElementChild).after(tl); cl.dataset.closed = 1; host(tl); if (wholeWidth && cl.classList.contains("me")) wholeWidth.observe(tl);
}
/* a thread opens where it is, under its root, with its own flat field at its foot; a reply goes to the thread */
function openThread(id, quiet, o = {}) { const cl = sectionOf(id); if (!cl) return; const tl = $(`.tl[data-mid="${id}"]`, cl); let box = $(`.thr-in[data-mid="${id}"]`, cl);
  if (box) { if (o.focus) { const f = $(":scope > .cz", box); if (f) { state.threadTx = $(".tx", f); ready(); } return; }
    state.P.open.delete(id); if (state.threadTx && box.contains(state.threadTx)) state.threadTx = null; const write = () => box.remove(); if (quiet) write(); else grown(write, "touch"); if (tl) $(".lk", tl).classList.remove("on"); ready(); return; }
  state.P.open.add(id); box = el("div", "thr-in"); box.dataset.mid = id; const list = repliesOf(state.P, id), m = state.P.byId.get(id), want = m && m.reply_summary ? m.reply_summary.reply_count || 0 : list.length;
  const anchor = tl || $$(`:scope > [data-mid="${id}"]:not(.thr-in)`, cl).pop() || cl.lastElementChild;
  const write = () => { anchor.after(box); if (want > list.length) { box.append(el("div", "note more-replies", `Loading ${want - list.length} earlier ${want - list.length === 1 ? "reply" : "replies"}`)); fetchThread(state.P, id); } for (const r of list) appendMessage(box, r, {thread: true}); box.append(threadField(id)); };
  if (quiet) { write(); hugAll(); } else {   /* the root stays where it was read; the view moves only to bring the thread's field into sight, never the root out of it */
    haltLane(); state.pinned = false; const y = feed.scrollTop; write(); hugAll(); feed.scrollTop = y;
    const f = $(":scope > .cz", box).getBoundingClientRect(), fr = feed.getBoundingClientRect(), rr = (tl || anchor).getBoundingClientRect(), up = Math.min(f.bottom - (fr.bottom - 8), rr.top - (fr.top + 28));
    if (up > 0) { feed.scrollTop += up; releaseRows(up, "move"); } state.lastSet = feed.scrollTop; state.pinned = dist() < 4; edge(); syncJump(); M.release(box, {y: -(T.rise || 6)}, "touch"); M.appear(box, 0.3); }
  if (tl) $(".lk", tl).classList.add("on");
  if (o.focus) { state.threadTx = $(":scope > .cz .tx", box); ready(); } }
/* replies further back than the conversation's window: the thread's own narrow fetches them, once */
async function fetchThread(c, id) { try { const r = await api("GET", "messages", {narrow: [{operator: "thread", operand: id}], anchor: "newest", num_before: 200, num_after: 0, apply_markdown: true, client_gravatar: false}); for (const m of r.messages) if (m.id !== id) addMsg(c, m); } catch (e) { return; }
  if (state.P !== c) return; const box = $(`.thr-in[data-mid="${id}"]`, lane); if (!box) return; const f = $(":scope > .cz", box), had = f && document.activeElement === $(".tx", f);
  grown(() => { $$(":scope > :not(.cz)", box).forEach((x) => x.remove()); for (const r of repliesOf(c, id)) appendMessage(box, r, {thread: true}); if (f) box.append(f); }, "touch"); if (had) ready(); }
/* a reply that arrives or that I send goes above the thread's field */
function threadAppend(box, m) { const f = $(":scope > .cz", box); appendMessage(box, m, {thread: true}); if (f) box.append(f); }
function threadField(id) {
  const t = el("template"); t.innerHTML = composerHTML({plain: 1, verb: "Reply", focus: false}, "", null); const cz_ = t.content.firstElementChild, tx_ = $(".tx", cz_), go_ = $(".disc", cz_);
  cz_.classList.remove("is-focus", "has-text"); tx_.innerHTML = ""; tx_.contentEditable = "plaintext-only"; tx_.classList.add("m-caret"); go_.classList.remove("armed"); $$(".sz", cz_).forEach((x) => x.classList.add("sz")); host(cz_);
  const sync = () => { const on = !!tx_.textContent.trim(); cz_.classList.toggle("has-text", on); go_.classList.toggle("armed", on); };
  tx_.addEventListener("input", sync); tx_.addEventListener("focus", () => { state.threadTx = tx_; cz_.classList.add("is-focus"); }); tx_.addEventListener("blur", () => cz_.classList.remove("is-focus"));
  tx_.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); replyThread(id, tx_, e.timeStamp); sync(); } else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); state.threadTx = null; ready(); } });
  go_.addEventListener("click", (e) => { replyThread(id, tx_, e.timeStamp); sync(); });
  return cz_;
}
function replyThread(id, tx_, at = performance.now()) {
  const t0 = performance.now(), text = wireOf(tx_, state.agentMention), c = state.P, root = c.byId.get(id), box = tx_.closest(".thr-in");
  if (!text || !root || !box) { M.refuse(tx_.closest(".sh")); S.play("nope", 0, at); return; }
  /* the words as written, mentions in place: a reply that mentions Onix is for it */
  const m = {id: "L" + ++state.localSeq, local: true, pending: true, sender_id: state.ME, timestamp: Math.floor(now() / 1000), content: echoHTML(text), submessages: localSubmessages(text), raw: text, toOnix: text.includes(state.agentMention) && agentCanRun(), thread_root_message_id: id, reactions: []};
  root.reply_summary = root.reply_summary || {reply_count: 0, participants: []}; root.reply_summary.reply_count = (root.reply_summary.reply_count || 0) + 1;
  if (!root.reply_summary.participants.some((x) => x.user_id === state.ME)) root.reply_summary.participants.push({user_id: state.ME});
  const row = grown(() => { addMsg(c, m); tx_.textContent = ""; const cl = sectionOf(id); if (cl) drawThreadLine(cl, root); const r = {cl: null, parts: []}; const f = $(":scope > .cz", box); Object.assign(r, appendMessage(box, m, {thread: true})); if (f) box.append(f); return r; }, "touch");
  const b = $$(".b", row.parts[row.parts.length - 1] || row.cl).pop(); if (b) M.appear(b, 0.6); S.play("send", 0, at);
  state.threadTx = tx_; ready();
  stats.sends.push({task: Math.round((performance.now() - t0) * 100) / 100, sinceKey: null, painted: null, thread: true});
  transmit(c, m, null);
}

export {drawThreadLine, openThread, repliesOf, threadAppend};
