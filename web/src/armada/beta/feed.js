/* INTERIM BETA (README.md): the conversation: its scroll, its turns, the lane drawn. */

import {$$, clamp, el, esc} from "./util.js";
import {msgIndex, state, unread} from "./state.js";
import {dayKey, dayLabel, hhmm} from "./time.js";
import {band, feed, lane} from "./frame.js";
import {faceOf, isOnix, nameOf, stoneOf} from "./faces.js";
import {partEl, partsOf} from "./parts.js";
import {drawReactions} from "./reactions.js";
import {receiptButton, RECEIPTS, untick} from "./receipts.js";
import {drawThreadLine, openThread} from "./threads.js";
import {placeholderAns, syncOnix} from "./agent_turn.js";
import {failMark} from "./send.js";
import {evoCaption} from "./partners.js";

const dist = () => feed.scrollHeight - feed.scrollTop - feed.clientHeight;
const pin = () => { feed.scrollTop = 1e7; state.lastSet = feed.scrollTop; };
const edge = () => { const y = feed.scrollTop; feed.classList.toggle("cut", y > 2); feed.style.setProperty("--cut", Math.min(20, Math.round(y)) + "px"); };
const firstRow = () => lane.firstElementChild, lastCl = () => lane.lastElementChild;
/* ---------- the conversation moves as one piece, and only what is in view is moved ----------
   A write that shifts the conversation (a row added at its end, the field growing a line, Jump, a thread opening, a place
   opened) is released on the rows that are in view at some moment of the move, all on one spring, transform only. A row
   out of view is never animated: a spring on the lane itself made the compositor lift the whole conversation into a layer
   and paint it again, at a cost that grew with everything loaded (about 300 ms of dropped frames a message at 2,000). */
const moving = new Set();                                  /* the rows that carry the conversation's spring */
const springOf = (r) => M.current(r, "lane");
/* where the conversation is in its spring: [displacement, velocity] of the rows that carry it (they share one state) */
function laneMotion() { for (const r of moving) { const [x, v] = springOf(r); if (x.y || v.y) return [x.y || 0, v.y || 0]; moving.delete(r); } return [0, 0]; }
const laneMoving = () => laneMotion()[0] !== 0;
/* the lane's rows that a move displaced by `d` px at its start shows at some moment, read from layout (no transform): a
   row at rest from t to b is seen from t + d to b + d, then travels to t to b */
function rowsInView(d) { const kids = lane.children, n = kids.length; if (!n) return [];
  const fr = feed.getBoundingClientRect(), top = lane.getBoundingClientRect().top, from = fr.top - Math.max(0, d), to = fr.bottom - Math.min(0, d);
  let lo = 0, hi = n - 1; while (lo < hi) { const m = (lo + hi) >> 1, k = kids[m]; if (top + k.offsetTop + k.offsetHeight < from) lo = m + 1; else hi = m; }
  const out = []; for (let i = lo; i < n; i++) { const k = kids[i]; if (top + k.offsetTop > to) break; out.push(k); } return out; }
/* the conversation is shown `d` px from where layout put it and released on the spring `token`; a row that joins a move
   already under way takes the move's displacement and speed, so the rows never overlap or part */
function releaseRows(d, token = "move", o = {}) { const [y, v] = laneMotion(), cap = o.clamp && o.clamp.y;
  for (const r of rowsInView(cap ? clamp(y + d, -cap, cap) : y + d)) { const [x, w] = springOf(r); M.release(r, {y: y + d - (x.y || 0)}, token, {...o, id: "lane", kick: {y: v - (w.y || 0)}}); moving.add(r); } }
/* a hand on the conversation (a wheel, a touch, a send) stops its spring where it is seen */
function haltLane() { const [y] = laneMotion(); stillLane(); if (Math.abs(y) > 0.5) feed.scrollTop -= y; }
function stillLane() { for (const r of moving) M.halt(r, "lane"); moving.clear(); }
function show(e, on) { if (!e) return; if (e === band.jump) band.el.classList.toggle("more", on); if (on === !e.classList.contains("off")) return; e.inert = !on; if (on) { e.classList.remove("off"); M.appear(e); } else { M.fade(e, 0); e.classList.add("off"); e.style.opacity = ""; } }
const syncJump = () => show(band.jump, state.tab === "Chat" && dist() >= 4);
/* a write that adds to the feed: older rows are shown where they were and released to where they are. One forced layout. */
function grown(write, token = "move", o = {}) {
  const ref = firstRow(), before = ref ? M.rest(ref).top : 0; const out = write(); hugAll();
  if (state.pinned) pin();
  const d = ref && ref.isConnected ? before - M.rest(ref).top : 0;
  if (d) releaseRows(d, token, o);
  edge(); syncJump(); return out;
}

/* ---------- the conversation ---------- */
const kindOf = (m) => (isOnix(m.sender_id) ? "onix" : m.sender_id === state.ME ? "me" : "them");
function named(cl) { for (let x = cl.previousElementSibling; x; x = x.previousElementSibling) { if (!x.classList.contains("cl") || x.classList.contains("me")) continue; return x.dataset.sid !== cl.dataset.sid; } return true; }
function newSection(c, m, kind, o) {
  const cl = el("section", "cl " + kind); cl.dataset.sid = m.sender_id; cl.dataset.day = dayKey(m.timestamp); c.append(cl);
  if (kind === "onix") { const lab = named(cl); cl.classList.toggle("lab", lab); cl.innerHTML = faceOf(m.sender_id) + (lab ? '<div class="who">Onix</div>' : ""); }
  else if (kind === "them") { const solo = state.place.kind === "dm" && state.place.ids.length === 1, lab = !solo && named(cl); cl.classList.add("s-" + stoneOf(m.sender_id)); cl.classList.toggle("lab", lab);
    cl.innerHTML = o.thread ? `<div class="who">${faceOf(m.sender_id, "s20")}${esc(nameOf(m.sender_id))}</div>` : faceOf(m.sender_id) + (lab ? `<div class="who">${esc(nameOf(m.sender_id))}</div>` : ""); if (o.thread) cl.classList.add("lab"); }
  return cl;
}
/* a turn's bubbles: the first keeps its lower inner corner square, the last its upper one; the turn's time stands on its last bubble */
function finish(cl) { if (cl.classList.contains("onix")) return; const bs = $$(":scope > .bl > .b", cl); bs.forEach((b, i) => { b.classList.toggle("u", i < bs.length - 1); b.classList.toggle("t", i > 0); });
  $$(":scope > .bl > time, :scope > .bl > .rt, :scope > .rcpt.at", cl).forEach((t) => { for (const b of t.matches(".rt") ? [t] : $$(".rt", t)) untick(b); t.remove(); }); const lastPart = $$(":scope > [data-part]", cl).pop(); if (!lastPart || cl.dataset.pending) return;
  const edited = (mid) => { const c = msgIndex.get(+mid), x = c && c.byId.get(+mid); return !!(x && (x.edited || x.last_edit_timestamp)); };
  /* an edited message that is not the turn's last says so on its own last bubble; the last one says it with the time */
  const lastOf = new Map(); for (const b of $$(":scope > .bl[data-mid]", cl)) lastOf.set(b.dataset.mid, b);
  for (const [mid, b] of lastOf) if (mid !== lastPart.dataset.mid && edited(mid)) b.append(el("time", "ed", "Edited"));
  const ts = +cl.dataset.ts, t = el("time", "", (edited(lastPart.dataset.mid) ? "Edited " : "") + hhmm(ts)); t.title = new Date(ts * 1000).toLocaleString();
  const cell = RECEIPTS && cl.classList.contains("me") ? receiptButton(t, lastPart.dataset.mid) : t;   /* my turn: the time and its ticks, one button */
  if (lastPart.classList.contains("bl")) lastPart.append(cell); else { const r = el("div", "rcpt at" + (cl.classList.contains("me") ? " end" : "")); r.append(cell); lastPart.after(r); } }
function appendMessage(c, m, o = {}) {
  const kind = kindOf(m), d = dayKey(m.timestamp);
  if (!o.thread && c._day !== d) { c.append(el("div", "day", dayLabel(m.timestamp))); c._day = d; }
  if (o.unreadAt === m.id) c.append(el("div", "unread", "New"));
  let cl = c.lastElementChild;
  const join = kind !== "onix" && cl && cl.classList.contains("cl") && +cl.dataset.sid === m.sender_id && !cl.dataset.closed && m.timestamp - +cl.dataset.ts < 600 && !cl.classList.contains("onix");
  if (!join) cl = newSection(c, m, kind, o);
  cl.dataset.ts = m.timestamp; (msgIndex.get(m.id) || state.P).rows.set(m.id, cl);
  const parts = partsOf(m).map((p) => partEl(p, m)); cl.append(...parts); const evo = evoCaption(m); if (evo) cl.append(evo);
  if (kind === "onix") { cl.dataset.mid = m.id; if (!parts.length) cl.append(placeholderAns(m)); syncOnix(cl, m); }
  if (kind === "onix" && m.submessages && m.submessages.length) cl.append(el("div", "note nw", 'This answer carries an artifact, which the beta does not draw yet.'));
  if (m.failed) failMark(cl, m); if (m.pending || m.failed) $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.toggle("unsent", !!m.failed));
  drawReactions(cl, m); if (!o.thread) drawThreadLine(cl, m);
  finish(cl); hush(cl); toHug.add(cl); return {cl, parts};
}
/* a wrapped bubble is as wide as its longest line, as a chat bubble hugs its words: read every waiting bubble's line boxes
   after one write, then set the widths. A one-paragraph bubble only (a list's bullets stand outside its line boxes); a width
   that would change the bubble's lines is not kept. */
const toHug = new Set();
function hugAll() { if (!toHug.size) return; const bs = []; for (const cl of toHug) if (cl.isConnected && !cl.classList.contains("onix")) bs.push(...$$(":scope > .bl > p.b:not(.jumbo)", cl)); toHug.clear(); if (!bs.length) return;
  bs.forEach((b) => (b.style.width = "")); const range = document.createRange();
  const want = bs.map((b) => { const h = b.offsetHeight; if (h <= 40) return null; range.selectNodeContents(b); const rs = [...range.getClientRects()].filter((x) => x.width > 0); if (!rs.length) return null; const cs = getComputedStyle(b); return {h, w: Math.ceil(Math.max(...rs.map((x) => x.right)) - Math.min(...rs.map((x) => x.left)) + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight)) + 1}; });
  bs.forEach((b, i) => { if (want[i]) b.style.width = want[i].w + "px"; });
  bs.forEach((b, i) => { if (want[i] && b.offsetHeight !== want[i].h) b.style.width = ""; }); }
const hush = (root) => $$(".rc, .rt, [data-thread], .tl .lk, .line .lk, .bl > .lk", root).forEach((b) => (b.tabIndex = -1));
function drawLane(o = {}) {
  const c = state.P, h0 = feed.scrollHeight, y0 = feed.scrollTop; stillLane(); lane.textContent = ""; lane._day = null; c.rows.clear();
  const set = unread.get(state.place.key), firstUnread = set && set.size ? Math.min(...[...set].filter((i) => typeof i === "number")) : null;
  if (c.loaded && !c.oldest) lane.append(el("div", "more-up", c.loading ? "Loading earlier messages" : "Scroll up for earlier messages"));
  const list = c.msgs.filter((m) => !m.thread_root_message_id);
  for (const m of list) appendMessage(lane, m, {unreadAt: firstUnread});
  for (const id of c.open) openThread(id, true);
  if (!list.length) lane.append(el("div", "empty", c.error ? esc(c.error) : !c.loaded ? "Loading" : state.place.kind === "dm" ? "No messages yet." : "Nothing here yet. Say something."));
  hugAll();
  if (o.keep) { feed.scrollTop = y0 + (feed.scrollHeight - h0); state.lastSet = feed.scrollTop; } else if (state.pinned) pin();
  edge(); syncJump();
}
/* a message's turn in the place in view, from the place's own map (appendMessage keeps it): no search of the lane. A turn
   that has left the lane (a thread closed under it) is forgotten when it is asked for. */
function sectionOf(id) { const c = state.P, cl = c && c.rows && c.rows.get(id); if (!cl) return null; if (lane.contains(cl)) return cl; c.rows.delete(id); return null; }

export {appendMessage, dist, drawLane, edge, finish, firstRow, grown, haltLane, hugAll, hush, laneMoving, pin, releaseRows,
  sectionOf, show, syncJump, toHug};
