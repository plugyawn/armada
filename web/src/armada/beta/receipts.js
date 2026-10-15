/* INTERIM BETA (README.md): read receipts and Info. */

import {$, $$, clamp, el, esc, T} from "./util.js";
import {armadaBots, DATA, msgIndex, people, state, stats} from "./state.js";
import {hhmm} from "./time.js";
import {api} from "./connection.js";
import {feed, lane, main} from "./frame.js";
import {faceOf, nameOf, presOf} from "./faces.js";
import {ready} from "./composer.js";
import {closeMenu, overHide} from "./hover.js";

/* ----- read receipts: the r4 receipts study (design/r4/receipts/RECEIPTS.md), pushed by the server (contract C2) -----
   My turns carry ticks beside their time, in a 16 px cell that exists from the frame of the send and changes only what it
   holds: nothing for the first 400 ms of a send, then a ring while it is in flight; one tick once the server has it (it is
   then in everyone's feed: Zulip's "delivered"); two in the second ink once someone counted has read it; two in my colour
   once everyone counted has. Opening a place asks who has read my latest messages there, in one request to GET
   armada/read_receipts (never more than RCPT_CAP times a minute); after that the server says when someone reads one of my
   messages (a read_receipts event), and the page asks nothing. Info (pressing the ticks, More > Info, or I on a message)
   asks for its message in full and lists who has read it and who has not yet. None of it is drawn when the organization has read receipts off. No sound: someone reading is
   neither my action nor something that needs me. */
const RECEIPTS = DATA.readReceipts === "on";
const RCPT_CAP = 12;                                       /* background requests for receipts in any 60 s (opening places) */
const RCPT_INFO_CAP = 12;                                  /* Info's own budget: what I asked for answers at once, whatever the background spent */
const RCPT_FRESH = 10000, RCPT_BATCH = 20;                 /* RCPT_BATCH: my latest messages a place asks about (the endpoint takes 100) */
const rcpt = new Map();                                    /* message id -> {read: Set of user ids, at, err} */
const rcptLog = [], rcptInfoLog = [], rcptInflight = new Map(), members = new Map();
let info = null;
stats.receipts = {requests: 0, refused: 0, maxPerMinute: 0};
function rcptAllowed(forInfo) { const t = performance.now(), log = forInfo ? rcptInfoLog : rcptLog; while (log.length && t - log[0] >= 60000) log.shift();
  if (log.length >= (forInfo ? RCPT_INFO_CAP : RCPT_CAP)) { stats.receipts.refused++; return false; }
  log.push(t); stats.receipts.requests++; if (!forInfo) stats.receipts.maxPerMinute = Math.max(stats.receipts.maxPerMinute, log.length); else stats.receipts.info = (stats.receipts.info || 0) + 1; return true; }
function fetchReceipts(id, o = {}) { if (!RECEIPTS || typeof id !== "number") return Promise.resolve(null); const had = rcpt.get(id);
  if (had && !o.force && performance.now() - had.at < RCPT_FRESH) return Promise.resolve(had);
  if (rcptInflight.has(id)) return rcptInflight.get(id);
  if (!rcptAllowed(!!o.retry)) { if (o.retry) setTimeout(() => { if (info && info.id === id) fetchReceipts(id, o); }, 60000 - (performance.now() - rcptInfoLog[0]) + 50); return Promise.resolve(had || null); }   /* Info has its own budget; past twelve Infos in a minute, it waits for the minute to free one */
  const p = api("GET", `messages/${id}/read_receipts`).then((r) => ({read: new Set(r.user_ids), at: performance.now()}), (e) => ({read: had ? had.read : new Set(), at: performance.now(), err: e.message}))
    .then((x) => { rcpt.set(id, x); rcptInflight.delete(id); drawTicks(id); if (info && info.id === id) drawInfo(); return x; });
  rcptInflight.set(id, p); return p; }
function loadMembers(sid) { if (members.has(sid)) return Promise.resolve(members.get(sid));
  const p = api("GET", `streams/${sid}/members`).then((r) => { const s = new Set(r.subscribers); members.set(sid, s); $$(".rt[data-info]", lane).forEach((b) => drawTicks(+b.dataset.info)); if (info) drawInfo(); return s; }, () => { members.delete(sid); return null; });
  members.set(sid, p); return p; }
/* who a message's receipts count, as far as the page can know: the channel's subscribers now (or the conversation's
   people), less the sender, bots (Onix among them: it answers, it does not read), deactivated people and people I muted.
   Null until the channel's subscribers are known. */
function audience(m) { const c = msgIndex.get(m.id), p = c && c.place; if (!p || p.kind === "home") return null;
  let ids; if (p.kind === "topic") { const s = members.get(p.stream_id); if (!(s instanceof Set)) return null; ids = [...s]; } else ids = [state.ME, ...p.ids];
  return [...new Set(ids)].filter((i) => i !== m.sender_id && !state.muted.has(i) && people.has(i) && !people.get(i).bot && people.get(i).active); }
function tickState(m) { if (!m || m.failed) return "none"; if (m.local || m.pending || typeof m.id !== "number") return "pending";
  const x = rcpt.get(m.id), who = audience(m); if (!x) return "sent";
  const others = [...x.read].some((i) => i !== m.sender_id); if (!who || !who.length) return others ? "read" : "sent";   /* a reader the page cannot count (not subscribed now) still reads */
  return who.every((i) => x.read.has(i)) ? "all" : others ? "read" : "sent"; }
const TICKS = {
  pending: '<circle cx="8.5" cy="8.5" r="4.5"/><path d="M8.5 6v3h2"/>',
  sent: '<path d="M3.5 8.5l3 3 6-6"/>',
  read: '<path d="M1.5 8.5l3 3 6-6"/><path d="M7.5 10.5l1 1 6-6"/>'};
TICKS.all = TICKS.read;
const tickSVG = (st) => (TICKS[st] ? `<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><g class="n">${TICKS[st]}</g></svg>` : "");
function tickWords(m, st) { const x = rcpt.get(m.id), who = audience(m);
  if (st === "pending") return "Sending"; if (st === "none") return "Not sent";
  if (st === "sent") return "Sent. Not read yet";
  if (who && who.length === 1) return "Read";
  return who && who.length ? `Read by ${who.filter((i) => x.read.has(i)).length} of ${who.length}` : `Read by ${[...x.read].filter((i) => i !== m.sender_id).length}`; }
/* the time and the ticks: one button, which opens Info */
function receiptButton(t, mid) { const b = el("button", "rt"); b.dataset.info = mid; b.tabIndex = -1; const cell = el("span", "tk"); b.append(t, cell);
  const id = /^\d+$/.test(mid) ? +mid : mid, c = msgIndex.get(id); paintTick(b, c && c.byId.get(id), false); return b; }
function paintTick(b, m, motion) { const cell = $(".tk", b), st = tickState(m), was = cell.dataset.s; if (was === st) return;
  cell.dataset.s = st; const same = (was === "read" && st === "all") || (was === "all" && st === "read"); if (!same) cell.innerHTML = tickSVG(st);
  const words = tickWords(m, st); b.setAttribute("aria-label", `${words}. Message info`); b.dataset.tip = words;
  const svg = $("svg", cell); if (!motion || !svg || same) return;
  if (was === "pending" && st === "sent") M.land(svg); else if (st === "read" || st === "all") M.appear(svg, 0, T["fade-in"]); }
function drawTicks(id) { if (!lane) return; for (const b of $$(`.rt[data-info="${id}"]`, lane)) { const c = msgIndex.get(id), m = c && c.byId.get(id); if (m) paintTick(b, m, true); } }
/* on opening a place: who has read my latest messages there that are not yet read by everyone, in one request */
function loadReceipts() { if (!RECEIPTS || !state.P || !state.place || state.place.kind === "home") return; const c = state.P;
  const ids = c.msgs.filter((m) => m.sender_id === state.ME && typeof m.id === "number" && !m.thread_root_message_id).slice(-RCPT_BATCH).filter((m) => tickState(m) !== "all").map((m) => m.id);
  if (!ids.length || !rcptAllowed(false)) return;
  api("GET", "armada/read_receipts", {message_ids: ids}).then((r) => { const at = performance.now(), got = r.read_receipts || {};
    for (const id of ids) { const had = rcpt.get(id); rcpt.set(id, {read: new Set([...(got[id] || []), ...(had ? had.read : [])]), at}); drawTicks(id); }   /* a reader the event told of meanwhile stays */
    if (info && ids.includes(info.id)) drawInfo(); }, () => {}); }
/* someone read my messages (the read_receipts event, sent to their sender): a tick lands where it stands */
function onReadReceipts(ev) { if (!RECEIPTS) return; const at = performance.now();
  for (const id of ev.message_ids) { const x = rcpt.get(id); if (x) x.read.add(ev.user_id); else rcpt.set(id, {read: new Set([ev.user_id]), at}); drawTicks(id); }
  if (info && ev.message_ids.includes(info.id)) drawInfo(); }
/* Info: who has read a message, and who has not yet. Above the message it describes (below when there is no room), never
   over the field; the conversation stays live under it. Rows keep the order they had when it opened: a change rewrites a
   row's trailing cell and the heading's figure. */
function toggleInfo(id, from, kbd) { if (info && info.id === id) { closeInfo(); return; } openInfo(id, from, kbd); }
function openInfo(id, from, kbd) { closeInfo(true); closeMenu(); overHide(); const m = state.P && state.P.byId.get(id); if (!RECEIPTS || !m || typeof id !== "number") { ready(); return; }
  const box = el("div", "info raised float"); box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Message info"); box.tabIndex = -1;
  info = {id, el: box, from, kbd, order: null}; main.append(box); if (state.place.kind === "topic") loadMembers(state.place.stream_id); drawInfo(); placeInfo();
  $$(`.rt[data-info="${id}"]`, lane).forEach((b) => b.classList.add("open")); box.focus({preventScroll: true}); M.release(box, {y: T["pane-rise"] || 4}, "move");
  fetchReceipts(id, {force: true, retry: true}); }
function placeInfo() { if (!info) return; const id = info.id, box = info.el, mr = main.getBoundingClientRect(), fr = feed.getBoundingClientRect(), dock = $("main > .dock").getBoundingClientRect();
  const parts = $$(`[data-mid="${id}"]`, lane).filter((x) => x.matches(".bl, [data-part], .cl")), first = parts[0], last = parts[parts.length - 1]; if (!first) return;
  const rt = $(`.rt[data-info="${id}"]`, lane), a = first.getBoundingClientRect(), z = last.getBoundingClientRect(), h = box.offsetHeight, w = box.offsetWidth;
  const left = clamp((rt ? rt.getBoundingClientRect().left : a.left) - mr.left, 8, mr.width - w - 8);
  let top = a.top - h - 8; if (top < fr.top + 8) top = z.bottom + 8; if (top + h > dock.top - 8) top = Math.max(fr.top + 8, a.top - h - 8);
  box.style.cssText = `position:absolute;left:${Math.round(left)}px;top:${Math.round(top - mr.top)}px`; }
function drawInfo() { if (!info) return; const c = msgIndex.get(info.id), m = c && c.byId.get(info.id); if (!m) { closeInfo(); return; }
  const x = rcpt.get(info.id), who = audience(m), mine = m.sender_id === state.ME, solo = !!who && who.length === 1 && state.place.kind === "dm";
  const read = x ? [...x.read].filter((i) => i !== m.sender_id) : [], counted = who || [];
  if (!info.order && x) { const byName = (a, b) => nameOf(a).localeCompare(nameOf(b)); info.order = [...read.sort(byName), ...counted.filter((i) => !x.read.has(i)).sort(byName)]; }
  const n = counted.filter((i) => x && x.read.has(i)).length;
  const head = !x ? "Loading" : solo ? (n ? "Read" : "Not read yet") : !n && !read.length ? "No one has read this yet" : counted.length ? `Read by <span class="fig">${fig(n)}</span> of ${counted.length}` : `Read by <span class="fig">${fig(read.length)}</span>`;
  const end = mine ? `Sent ${hhmm(m.timestamp)}` : `${esc(nameOf(m.sender_id).split(/[ ,]/)[0])}, ${hhmm(m.timestamp)}`;
  const trail = (i) => (x && x.read.has(i) ? "Read" : solo ? (presOf(i) === "on" ? "Here now" : presOf(i) === "away" ? "Away" : "Not yet") : "Not yet");
  const rows = (info.order || []).map((i) => `<li class="${x && x.read.has(i) ? "" : "not"}">${faceOf(i, "s20", presOf(i))}<span class="t">${esc(nameOf(i))}</span><span class="e">${trail(i)}</span></li>`).join("");
  const run = c.runs && c.runs.find((r) => r.trigger_message_id === info.id), runWord = run ? ({queued: "Queued", running: run.waiting ? "Asks you" : "Working", done: `Answered ${hhmm(run.date_finished || run.date_updated || run.date_created)}`, failed: "Failed", cancelled: "Stopped"}[run.status] || "") : "";
  const err = x && x.err ? `<p class="bad">Could not load who read this. <button class="lk" data-info-retry>Retry</button></p>` : "";
  info.el.innerHTML = `<div class="ih"><b>${head}</b><span class="e">${end}</span></div>${err}<ul class="ls">${rows}</ul>${run ? `<ul class="ls agent"><li>${faceOf([...armadaBots][0], "s20")}<span class="t">Onix</span><span class="e">${esc(runWord)}</span></li></ul>` : ""}`;
  host(info.el); }
function closeInfo(quiet) { if (!info) return; const {el: box, from, kbd} = info; info = null; $$(".rt.open", lane).forEach((b) => b.classList.remove("open")); box.inert = true; M.leave(box, () => box.remove());
  if (quiet) return; if (kbd && from && from.isConnected) M.focusKbd(from); else ready(); }

export {closeInfo, drawTicks, fetchReceipts, info, loadMembers, loadReceipts, onReadReceipts, openInfo, receiptButton,
  RECEIPTS, toggleInfo};
