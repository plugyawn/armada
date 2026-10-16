/* INTERIM BETA (README.md): the conversation: its scroll, its turns, the lane drawn, kept while a place is left, moved. */

import {$, $$, clamp, el, esc} from "./util.js";
import {msgIndex, state, unread} from "./state.js";
import {dayKey, dayLabel, hhmm, now} from "./time.js";
import {band, feed, lane} from "./frame.js";
import {faceOf, isOnix, nameOf, stoneOf} from "./faces.js";
import {partEl, partsOf} from "./parts.js";
import {drawReactions} from "./reactions.js";
import {receiptButton, RECEIPTS, untick} from "./receipts.js";
import {drawThreadLine, openThread, repliesOf, threadAppend} from "./threads.js";
import {placeholderAns, syncOnix, updateMessage} from "./agent_turn.js";
import {clearFailMark, failMark} from "./send.js";
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
/* a turn's bubbles: the first keeps its lower inner corner square, the last its upper one; the turn's time stands on its last bubble.
   A link's card belongs to the words above it, so the time stays with them: a preview that arrives, or a reserved card
   that closes, never moves the time (previews.js) */
function finish(cl) { if (cl.classList.contains("onix")) return; const bs = $$(":scope > .bl > .b", cl); bs.forEach((b, i) => { b.classList.toggle("u", i < bs.length - 1); b.classList.toggle("t", i > 0); });
  $$(":scope > .bl > time, :scope > .bl > .rt, :scope > .rcpt.at", cl).forEach((t) => { for (const b of t.matches(".rt") ? [t] : $$(".rt", t)) untick(b); t.remove(); }); const all = $$(":scope > [data-part]", cl); let lastPart = all.pop(); while (lastPart && /^pv/.test(lastPart.dataset.part) && all.length) lastPart = all.pop(); if (!lastPart || cl.dataset.pending) return;
  const edited = (mid) => { const c = msgIndex.get(+mid), x = c && c.byId.get(+mid); return !!(x && (x.edited || x.last_edit_timestamp)); };
  /* an edited message that is not the turn's last says so on its own last bubble; the last one says it with the time */
  const lastOf = new Map(); for (const b of $$(":scope > .bl[data-mid]", cl)) lastOf.set(b.dataset.mid, b);
  for (const [mid, b] of lastOf) if (mid !== lastPart.dataset.mid && edited(mid)) b.append(el("time", "ed", "Edited"));
  const ts = +cl.dataset.ts, t = el("time", "", (edited(lastPart.dataset.mid) ? "Edited " : "") + hhmm(ts)); t.title = new Date(ts * 1000).toLocaleString();
  const cell = RECEIPTS && cl.classList.contains("me") ? receiptButton(t, lastPart.dataset.mid) : t;   /* my turn: the time and its ticks, one button */
  if (lastPart.classList.contains("bl")) lastPart.append(cell); else { const r = el("div", "rcpt at" + (cl.classList.contains("me") ? " end" : "")); r.append(cell); lastPart.after(r); } }
function appendMessage(c, m, o = {}) {
  const kind = kindOf(m), d = dayKey(m.timestamp);
  if (!o.thread && c._day !== d) { const line = el("div", "day", dayLabel(m.timestamp)); line.dataset.day = d; c.append(line); c._day = d; }
  if (o.unreadAt === m.id) c.append(el("div", "unread", "New"));
  let cl = c.lastElementChild;
  const join = kind !== "onix" && cl && cl.classList.contains("cl") && +cl.dataset.sid === m.sender_id && !cl.dataset.closed && m.timestamp - +cl.dataset.ts < 600 && !cl.classList.contains("onix");
  if (!join) cl = newSection(c, m, kind, o);
  cl.dataset.ts = m.timestamp; (msgIndex.get(m.id) || state.P).rows.set(m.id, cl);
  const parts = partsOf(m).map((p) => partEl(p, m)); cl.append(...parts); const evo = evoCaption(m); if (evo) cl.append(evo);
  if (kind === "onix") { cl.dataset.mid = m.id; if (!parts.length) cl.append(placeholderAns(m)); syncOnix(cl, m); }
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
  const c = state.P, h0 = feed.scrollHeight, y0 = feed.scrollTop; stillLane(); lane.textContent = ""; lane._day = null; c.rows.clear(); c.dirty.clear();
  if (owner && owner !== c) { owner.drawn = false; owner.rows.clear(); } owner = c; c.drawn = true; c.today = dayKey(now() / 1000); c.hugGen = hugGen;
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
function sectionOf(id) { const c = state.P, cl = c && c.rows && c.rows.get(id); if (!cl) return null; if (lane.contains(cl)) return cl; if (c === owner) c.rows.delete(id); return null; }

/* ---------- the conversation changed in place: rows added above or below, a message taken out ----------
   What is written keeps the shape drawLane gives the same messages (a turn is one speaker within ten minutes, a day line
   heads each day once, a turn names its speaker unless the turn before it was theirs), and a row that is there is not
   built again. */
const idOf = (mid) => (/^\d+$/.test(mid) ? +mid : mid);
const isHead = (x) => x.matches(".av, .pk, .who");                 /* what a turn begins with: the face and the name */
const firstMsgOf = (c, cl) => { const p = $(":scope > [data-mid]", cl); return p ? c.byId.get(idOf(p.dataset.mid)) : null; };
/* whether drawLane would have written turn b's messages into turn a, the turn above it */
function joinable(c, a, b) { if (!a || !b || !a.matches(".cl:not(.onix)") || !b.matches(".cl:not(.onix)") || a.dataset.sid !== b.dataset.sid || a.dataset.closed) return false;
  const m = firstMsgOf(c, b); return !!m && m.timestamp - +a.dataset.ts < 600; }
/* turn `from` joins turn `into` ("before": its words go first, the earlier turn; "after": last); `into` stays */
function merge(c, into, from, where) { const moved = [...from.children].filter((x) => !isHead(x));
  if (where === "before") { const at = [...into.children].find((x) => !isHead(x)); if (at) at.before(...moved); else into.append(...moved); label(into, from.classList.contains("lab")); }
  else { into.append(...moved); into.dataset.ts = from.dataset.ts; if (from.dataset.closed) into.dataset.closed = from.dataset.closed; }
  for (const x of moved) if (x.dataset.mid && c.rows.get(idOf(x.dataset.mid)) === from) c.rows.set(idOf(x.dataset.mid), into);
  from.remove(); finish(into); hush(into); toHug.add(into); }
/* whether a turn names its speaker, set in place */
function label(cl, lab) { if (cl.classList.contains("me") || cl.classList.contains("lab") === lab) return; cl.classList.toggle("lab", lab); const who = $(":scope > .who", cl);
  if (!lab) { if (who) who.remove(); return; }
  const w = el("div", "who", cl.classList.contains("onix") ? "Onix" : esc(nameOf(+cl.dataset.sid))), face = $(":scope > .av, :scope > .pk", cl); if (face) face.after(w); else cl.prepend(w); }
/* the first turn from x on that names its speaker by the turn before it (mine never do) is named as drawLane would name it */
function relabelFrom(x) { for (; x; x = x.nextElementSibling) if (x.matches(".cl:not(.me)")) { const solo = x.classList.contains("them") && state.place.kind === "dm" && state.place.ids.length === 1; label(x, !solo && named(x)); return; } }
/* the turns written inside x forget their messages' rows and ticks */
function forgetRows(c, x) { for (const b of $$(".rt[data-info]", x)) untick(b); for (const s of [x, ...$$(".cl", x)]) if (s.matches(".cl")) for (const p of $$(":scope > [data-mid]", s)) { const i = idOf(p.dataset.mid); if (c.rows.get(i) === s) c.rows.delete(i); } }
/* a message taken out of its place's rows (deleted, dropped, let go): its parts leave its turn; a turn left with nothing
   leaves too, with the day line or the New line that headed only it, and the turns either side join where drawLane would
   join them. It reads no layout, and works on the rows of a place that is not in view as well. */
function removeRow(c, id) { const cl = c.rows.get(id); c.rows.delete(id); if (!cl || !cl.parentNode) return;
  for (const x of $$(`:scope > [data-mid="${id}"]`, cl)) { forgetRows(c, x); x.remove(); }
  if (cl.dataset.mid !== String(id) && $(":scope > [data-part]", cl)) { finish(cl); return; }
  const parent = cl.parentNode, next = cl.nextElementSibling; forgetRows(c, cl); cl.remove(); if (parent.classList && parent.classList.contains("thr-in")) return;
  for (let x = next ? next.previousElementSibling : parent.lastElementChild; x && x.matches(".day, .unread") && !(next && next.matches(".cl")); ) { const p = x.previousElementSibling; x.remove(); x = p; }
  if (!next) { let line = parent.lastElementChild; while (line && !line.matches(".day")) line = line.previousElementSibling; parent._day = line ? +line.dataset.day : null; }   /* a parked fragment has no :scope */
  if (next && joinable(c, next.previousElementSibling, next)) merge(c, next.previousElementSibling, next, "after"); else relabelFrom(next); }
/* older messages a page brought, written above the lane's first row: the page's last day line goes when the lane begins on
   that day, and its last turn joins the lane's first where drawLane would have written them as one */
function prepend(c, list, at) { const f = document.createDocumentFragment(); for (const m of list) appendMessage(f, m, {unreadAt: at});
  const up = $(":scope > .more-up", lane); let head = up ? up.nextElementSibling : lane.firstElementChild;
  if (head && head.matches(".day") && +head.dataset.day === f._day) { const line = head; head = head.nextElementSibling; line.remove(); }
  const tail = f.lastElementChild; if (up) up.after(f); else lane.prepend(f);
  if (joinable(c, tail, head)) merge(c, head, tail, "before"); else relabelFrom(head); }
/* the lane's two ends: the line that offers earlier messages, and what an empty place says */
function ends(c) { let up = $(":scope > .more-up", lane); const want = c.loaded && !c.oldest;
  if (want && !up) { up = el("div", "more-up"); lane.prepend(up); } else if (!want && up) { up.remove(); up = null; }
  if (up) up.textContent = c.loading ? "Loading earlier messages" : "Scroll up for earlier messages";
  const empty = $(":scope > .empty", lane); if (c.msgs.some((m) => !m.thread_root_message_id)) { if (empty) empty.remove(); return; }
  const words = c.error ? c.error : !c.loaded ? "Loading" : state.place.kind === "dm" ? "No messages yet." : "Nothing here yet. Say something.";
  if (empty) empty.textContent = words; else lane.append(el("div", "empty", esc(words))); }
/* the place in view's rows and its messages agree again: messages that a page brought, or that arrived while its rows were
   not being written, are added (older ones above the first row, newer ones after the last); no row that is there is touched.
   keep: the rows in view stay where they are on screen (a page added above them). */
function catchUp(c, o = {}) { if (c !== state.P || c !== owner) return;
  if (o.revisit) { $$(":scope > .unread", lane).forEach((x) => x.remove()); const today = dayKey(now() / 1000); if (c.today !== today) { for (const x of $$(":scope > .day", lane)) x.textContent = dayLabel(dayTs(+x.dataset.day)); c.today = today; } }   /* what was new last time is not now; "Today" may be yesterday */
  const h0 = o.keep ? feed.scrollHeight : 0, y0 = o.keep ? feed.scrollTop : 0, list = c.msgs.filter((m) => !m.thread_root_message_id); let first = -1, last = -1;
  list.forEach((m, i) => { if (c.rows.has(m.id)) { if (first < 0) first = i; last = i; } });
  if (first >= 0 && list.slice(first, last + 1).some((m) => !c.rows.has(m.id))) { drawLane({keep: o.keep}); return; }   /* a message between two written ones (never expected): drawn again whole */
  const set = unread.get(c.place.key), firstUnread = set && set.size ? Math.min(...[...set].filter((i) => typeof i === "number")) : null, at = $(":scope > .unread", lane) ? null : firstUnread;
  if (first > 0) prepend(c, list.slice(0, first), at);
  const newer = first < 0 ? list : list.slice(last + 1); for (const m of newer) appendMessage(lane, m, {unreadAt: at});
  for (const id of c.dirty) { const m = c.byId.get(id); if (m) refreshRow(c, m); } c.dirty.clear();
  if (c.hugGen !== hugGen) { $$(".cl", lane).forEach((x) => toHug.add(x)); c.hugGen = hugGen; }   /* the lane's width or the faces changed while these rows were parked */
  ends(c); hugAll();
  if (o.keep) { feed.scrollTop = y0 + (feed.scrollHeight - h0); state.lastSet = feed.scrollTop; } else if (state.pinned) pin();
  edge(); syncJump(); }

const dayTs = (k) => new Date(Math.floor(k / 10000), Math.floor(k / 100) % 100 - 1, k % 100, 12).getTime() / 1000;
/* a row whose message changed while its place was not shown (an edit, a reaction, a reply in its thread, a send that
   failed or arrived) is written again in place; its turn stays. Onix's turn is followed as the stream follows it. */
function refreshRow(c, m) { const cl = c.rows.get(m.id); if (!cl || !lane.contains(cl)) return;
  if (cl.classList.contains("onix")) { updateMessage(c, m); return; }
  const old = $$(`:scope > [data-part][data-mid="${m.id}"]`, cl), parts = partsOf(m).map((p) => partEl(p, m)); if (old.length) { old[0].before(...parts); old.forEach((x) => x.remove()); } else cl.append(...parts);
  if (m.failed) failMark(cl, m); else clearFailMark(cl, m.id); $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.toggle("unsent", !!m.failed));
  drawReactions(cl, m);
  if (!m.thread_root_message_id) { drawThreadLine(cl, m); const box = $(`:scope > .thr-in[data-mid="${m.id}"]`, cl); if (box) for (const r of repliesOf(c, m.id)) if (!c.rows.has(r.id)) threadAppend(box, r); }   /* replies that came while away join an open thread */
  finish(cl); hush(cl); toHug.add(cl); }

/* ---------- the lane holds one place's rows; a place left keeps its own ----------
   Leaving a place parks its rows, as they are, in a fragment of its cache; opening it again puts the same elements back and
   catches them up (what arrived, what changed while away), so a revisit builds no row. While a place is not shown (another
   is, or the page is hidden) its events change its messages and mark the rows they touch (c.dirty); nothing is written
   or moved where no one can see it. */
let owner = null;                                          /* the place whose rows the lane holds */
let hugGen = 0;                                            /* a lane width or a face loaded: bubbles hugged before it are hugged again */
const shown = (c) => !!c && c === state.P && c === owner && !document.hidden;
/* a row of a place not shown changed: it is written again when its place is shown */
function stale(c, id) { if (c && c.rows && c.rows.has(id)) c.dirty.add(id); }
function park(c) { const r = document.createRange(); r.selectNodeContents(lane); const f = r.extractContents(); f._day = lane._day; lane._day = null; c.park = f;
  if (state.threadTx && !lane.contains(state.threadTx)) state.threadTx = null; }
/* the lane shows place c: its parked rows come back and catch up; a place never drawn is drawn whole */
function showLane(c) { stillLane();
  if (owner !== c) { if (owner) park(owner); owner = c; if (c.park) { lane.append(c.park); lane._day = c.park._day; c.park = null; } else c.drawn = false; }
  if (!c.drawn) drawLane(); else catchUp(c, {revisit: true}); }
/* the oldest messages of a place let go (routing.js, trim): every row above the oldest message kept goes in one cut, a day
   line is written over that message's turn if its own went, and the turn loses the parts of the messages that went. A
   thousand rows cost one range removal; it reads no layout, and works on parked rows as well. */
function cutRows(c, first, gone) { for (const id of gone) c.rows.delete(id);
  const turn = first && c.rows.get(first.id), parent = turn ? turn.parentNode : owner === c ? lane : c.park; if (!parent) return;
  const up = parent.firstElementChild && parent.firstElementChild.matches(".more-up") ? parent.firstElementChild : null, from = up ? up.nextElementSibling : parent.firstElementChild;
  let start = turn; while (start && start.previousElementSibling && start.previousElementSibling.matches(".day, .unread")) start = start.previousElementSibling;
  if (from && from !== start) { const r = document.createRange(); r.setStartBefore(from); if (start) r.setEndBefore(start); else r.setEndAfter(parent.lastElementChild); r.deleteContents(); }
  unhug(); if (!turn) { parent._day = null; return; }
  if (!start.matches(".day")) { const line = el("div", "day", dayLabel(dayTs(+turn.dataset.day))); line.dataset.day = turn.dataset.day; start.before(line); }
  let cut = false; for (const x of [...turn.children]) if (x.dataset.mid && gone.has(idOf(x.dataset.mid))) { x.remove(); cut = true; }
  if (cut) finish(turn); relabelFrom(turn); }
/* a place let go (routing.js, keep): its rows go, parked or in the lane */
function dropRows(c) { if (owner === c) { stillLane(); lane.textContent = ""; lane._day = null; owner = null; unhug(); } c.park = null; c.rows.clear(); c.dirty.clear(); c.drawn = false; }
/* a turn that has left the lane is not held for hugging (a turn written while another view is up waits there for the next hug) */
function unhug() { for (const x of toHug) if (!x.isConnected) toHug.delete(x); }
/* every bubble hugs its words again: the lane's width changed, or the faces loaded */
function rehug() { hugGen++; $$(".cl", lane).forEach((x) => toHug.add(x)); hugAll(); if (owner) owner.hugGen = hugGen; }

export {appendMessage, catchUp, cutRows, dist, drawLane, dropRows, edge, ends, finish, firstRow, grown, haltLane, hugAll, hush, laneMoving, pin,
  rehug, releaseRows, removeRow, sectionOf, show, showLane, shown, stale, syncJump, toHug};
