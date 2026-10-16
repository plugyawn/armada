/* INTERIM BETA (README.md): places: their keys and addresses, their caches and what fills them, and opening one. */

import {$, $$, R, store, T} from "./util.js";
import {CAP, expanded, KEEP, marks, msgIndex, places, PLACES_KEPT, repoStreams, state, streams, topicsOf, unread} from "./state.js";
import {api} from "./connection.js";
import {band, feed, lane, main, navFixed, panes} from "./frame.js";
import {drawComposer, ready} from "./composer.js";
import {cutRows, drawLane, dropRows, ends, pin, releaseRows, showLane, shown} from "./feed.js";
import {drawNav, drawTrail, loadTopics} from "./nav.js";
import {askState, drawAsks, inferAsk, liveRun, runMark, runsSeen} from "./runs.js";
import {drawHeader} from "./header.js";
import {closeInfo, forgetReceipts, loadMembers, loadReceipts, RECEIPTS} from "./receipts.js";
import {drawFooters} from "./agent_turn.js";
import {drawTyping} from "./events.js";
import {drawRunState} from "./band.js";
import {drawSide} from "./column.js";
import {drawChanges, setTab} from "./views.js";
import {closeMenu, editing, endEdit, overHide} from "./hover.js";
import {closePicker} from "./model_picker.js";
import {drawHome, drawHomeTrail, fetchMentions, HOME, mentions} from "./home.js";
import {pack} from "./partners.js";
import {drawPicker, drawPickerBand, PARTNER} from "./partner_picker.js";
import {closeCard} from "./partner_card.js";
import {openPage, pageFromHash, pageFromKey, pageHash} from "./pages.js";
import {closeChoices} from "./later.js";

const tkey = (sid, topic) => `s:${sid}:${topic.toLowerCase()}`, dkey = (ids) => "d:" + [...ids].sort((a, b) => a - b).join(",");
const topicLabel = (t) => t || "general chat";

/* ---------- places ---------- */
function placeFromKey(key) {
  if (key === "home") return HOME;
  if (key === "partner") return pack ? PARTNER : null;
  if (/^(page|post|edit):/.test(key)) return pageFromKey(key);
  if (key.startsWith("s:")) { const i = key.indexOf(":", 2), sid = +key.slice(2, i), s = streams.get(sid); if (!s) return null; const lower = key.slice(i + 1), known = (topicsOf.get(sid) || []).find((t) => t.name.toLowerCase() === lower); return {key, kind: "topic", stream_id: sid, stream: s.name, topic: known ? known.name : lower, repo: repoStreams.has(sid)}; }
  if (key.startsWith("d:")) { const ids = key.slice(2).split(",").filter(Boolean).map(Number); return {key, kind: "dm", ids, repo: false}; }
  return null;
}
const hashOf = (p) => (p.kind === "home" ? "#home" : p.kind === "page" ? pageHash(p) : p.kind === "partner" ? "#partner" : p.kind === "topic" ? `#c/${p.stream_id}/${encodeURIComponent(p.topic)}` : `#d/${p.ids.join(",")}`);
function placeFromHash() { const h = location.hash; if (h === "#home") return HOME; if (h.startsWith("#narrow/")) return placeFromNarrow(h); if (h === "#partner") return pack ? PARTNER : null; if (/^#(page|post|edit)\//.test(h)) return pageFromHash(h); let m = /^#c\/(\d+)\/(.*)$/.exec(h); if (m && streams.has(+m[1])) { let t = m[2]; try { t = decodeURIComponent(t); } catch (e) {} const p = placeFromKey(tkey(+m[1], t)); if (p) p.topic = (topicsOf.get(+m[1]) || []).find((x) => x.name.toLowerCase() === t.toLowerCase())?.name ?? t; return p; }
  m = /^#d\/([\d,]*)$/.exec(h); if (m) return placeFromKey(dkey(m[1].split(",").filter(Boolean).map(Number))); return null; }
/* a link written for the old interface (a notification email, a link someone pasted): #narrow/channel/<id>-<name>/topic/<topic>
   (with /near/<id> or /with/<id> after it), or #narrow/dm/<ids>-<name>, opens the place it names; any other narrow, none */
function placeFromNarrow(h) { const parts = h.slice(1).split("/"), dec = (s) => { try { return decodeURIComponent(s.replaceAll(".", "%")); } catch (e) { return null; } };
  const term = (...ops) => { for (let i = 1; i + 1 < parts.length; i += 2) if (ops.includes(dec(parts[i]))) return dec(parts[i + 1]); return null; };
  const ch = term("channel", "stream"), topic = term("topic", "subject"), dm = term("dm", "pm-with");
  if (ch !== null && topic !== null) { const sid = parseInt(ch, 10); if (!streams.has(sid)) return null; const p = placeFromKey(tkey(sid, topic)); if (p) p.topic = (topicsOf.get(sid) || []).find((x) => x.name.toLowerCase() === topic.toLowerCase())?.name ?? topic; return p; }
  const ids = dm !== null && /^([\d,]+)/.exec(dm); return ids ? placeFromKey(dkey(ids[1].split(",").map(Number).filter((i) => i && i !== state.ME))) : null; }
/* a place's cache; `places` is kept in the order places were last opened, the least recent first (keep) */
const cacheOf = (p) => { let c = places.get(p.key); if (!c) c = {place: p, msgs: [], byId: new Map(), rows: new Map(), dirty: new Set(), park: null, drawn: false, loaded: false, oldest: false, loading: false, runs: [], ctx: null, typers: new Map(), open: new Set()}; else places.delete(p.key); places.set(p.key, c); return c; };
const narrowOf = (p) => (p.kind === "topic" ? [{operator: "channel", operand: p.stream_id}, {operator: "topic", operand: p.topic}] : [{operator: "dm", operand: p.ids.length ? p.ids : [state.ME]}]);
const placeOfMsg = (m) => (m.type === "stream" ? tkey(m.stream_id, m.subject ?? m.topic ?? "") : dkey((m.display_recipient || []).map((u) => u.id).filter((i) => i !== state.ME)));
function addMsg(c, m) { if (c.byId.has(m.id)) return false; c.byId.set(m.id, m); msgIndex.set(m.id, c); let i = c.msgs.length; if (typeof m.id === "number") while (i > 0 && typeof c.msgs[i - 1].id === "number" && c.msgs[i - 1].id > m.id) i--; c.msgs.splice(i, 0, m); return true; }
async function fetchMsgs(c, older) {
  if (c.loading || (older && c.oldest)) return; c.loading = true;
  try { const first = c.msgs.find((m) => typeof m.id === "number"); const q = {narrow: narrowOf(c.place), num_before: 50, num_after: 0, apply_markdown: true, client_gravatar: false};
    if (older && first) { q.anchor = String(first.id); q.include_anchor = false; } else q.anchor = "newest";
    const r = await api("GET", "messages", q); for (const m of r.messages) addMsg(c, m); if (r.found_oldest) c.oldest = true; c.loaded = true; c.error = null; }
  catch (e) { c.error = e.message; }
  c.loading = false;
}
/* ---------- what a place keeps (state.js: KEEP, CAP, PLACES_KEPT) ----------
   A message let go leaves its place's rows, msgIndex and the receipts; scrolling up asks the server for it again. A place
   let go is fetched again when it is opened; until then its live events change only its unread counts and marks. A
   message on its way is never let go, nor a place that holds one. */
function trim(c, keep) { const n = c.msgs.length - keep; if (n <= 0) return;
  const drop = new Set(c.msgs.slice(0, n).filter((m) => typeof m.id === "number" && !m.pending && !m.failed)); if (!drop.size) return;
  c.msgs = c.msgs.filter((m) => !drop.has(m)); c.oldest = false; const gone = new Set();
  for (const m of drop) { gone.add(m.id); c.byId.delete(m.id); if (msgIndex.get(m.id) === c) msgIndex.delete(m.id); forgetReceipts(m.id); }
  cutRows(c, c.msgs.find((m) => !m.thread_root_message_id), gone);
  for (const id of c.open) if (!c.byId.has(id)) c.open.delete(id);
  if (shown(c)) { ends(c); if (state.threadTx && !state.threadTx.isConnected) state.threadTx = null; } }
function letGo(c) { for (const m of c.msgs) { if (msgIndex.get(m.id) === c) msgIndex.delete(m.id); forgetReceipts(m.id); } for (const t of c.typers.values()) clearTimeout(t); c.typers.clear(); dropRows(c); }
/* after a place is opened: the places left keep KEEP messages each, and no more than PLACES_KEPT places are kept */
function keep() { for (const c of places.values()) if (c !== state.P) trim(c, KEEP);
  for (const [k, c] of places) { if (places.size <= PLACES_KEPT) break; if (c === state.P || c.msgs.some((m) => m.pending || m.failed)) continue; letGo(c); places.delete(k); } }
/* a message added to a place: a place left trims back to KEEP a page past it; the open one, while its end is in view (or was,
   when the page was hidden), past CAP. Rows being read further up are never let go. */
function keepEnd(c) { const here = c === state.P; if (here && !state.pinned) return; if (c.msgs.length <= (here ? CAP : KEEP + 50)) return; trim(c, KEEP); if (shown(c)) pin(); }
async function fetchRuns(c) { const p = c.place; if (p.kind !== "topic" || !p.repo) return; try { const r = await api("GET", "armada/runs", {stream: p.stream, topic: p.topic}); if (r.runtime_config) state.runtimeCfg = r.runtime_config; c.runs = r.armada_runs.filter((x) => x.session_kind !== "side"); c.runs.forEach(askState); runsSeen.set(p.key, c.runs); c.runsLoaded = true; } catch (e) {} }
async function fetchCtx(c) { const p = c.place; if (p.kind !== "topic" || !p.repo) return; try { const r = await api("GET", "armada/topic-context", {stream: p.stream, topic: p.topic}); c.ctx = r.armada_topic_context; } catch (e) { c.ctxError = e.message; } }

async function openPlace(p, o = {}) {
  if (!p) return; const same = state.place && state.place.key === p.key; if (same && !o.force) { ready(); return; }
  const prev = state.place; if (prev && prev.key !== p.key && places.get(prev.key)) places.get(prev.key).answering = false; state.place = p; state.P = cacheOf(p); state.P.place = p; state.pinned = true;
  if (!o.keepHash && location.hash !== hashOf(p)) history[o.replace ? "replaceState" : "pushState"](null, "", location.pathname + location.search + hashOf(p));
  store.set("place", p.key);
  if (p.kind === "topic" && !expanded.has(p.stream_id)) { expanded.add(p.stream_id); loadTopics(p.stream_id); store.set("open", JSON.stringify([...expanded])); }
  if (state.tab !== "Chat") setTab("Chat", true);
  if (state.threadTx && !lane.contains(state.threadTx)) state.threadTx = null; if (editing) endEdit(); closePicker(); closeMenu(); closeChoices(); closeInfo(true); overHide();
  showHome(p.kind === "home", p.kind === "partner", p.kind === "page"); $$(".row", navFixed).forEach((r) => r.classList.toggle("sel", r.dataset.k === "home" && p.kind === "home")); closeCard(); drawPickerBand();
  if (p.kind === "partner") { drawNav(); drawHeader(); drawRunState(true); drawPicker(); drawSide(); drawComposer(); if (prev && prev.key !== p.key) M.release($("#picker"), {y: T["pane-rise"] || 4}, "touch", {id: "pane"}); ready(); return; }
  if (p.kind === "page") { drawNav(); drawHeader(); drawRunState(true); drawSide(); drawComposer(); if (prev && prev.key !== p.key) M.release($("#page"), {y: T["pane-rise"] || 4}, "touch", {id: "pane"}); openPage(p); return; }
  if (p.kind === "home") { drawNav(); drawHeader(); drawRunState(true); drawSide(); drawComposer(); drawHome(); if (prev && prev.key !== p.key) M.release($("#home"), {y: T["pane-rise"] || 4}, "touch", {id: "pane"}); fetchMentions(); return; }
  drawNav(); drawHeader(); showLane(state.P); keep(); drawRunState(true); drawSide(); drawTyping(); drawComposer();
  if (prev && prev.key !== p.key) releaseRows(T["pane-rise"] || 4, "touch");
  ready();
  const c = state.P, fresh = !c.loaded;
  const jobs = [fresh ? fetchMsgs(c) : null, fetchRuns(c), c.ctx ? null : fetchCtx(c)];
  if (RECEIPTS && p.kind === "topic") loadMembers(p.stream_id);
  await jobs[0]; if (state.place !== p) return; if (fresh) { drawLane(); loadReceipts(); }
  await jobs[1]; if (state.place !== p) return; inferAsk(liveRun(c), c.msgs); drawRunState(true); drawFooters(); drawSide(); drawAsks();
  { const r = liveRun(c), k = p.key, m_ = r ? runMark(r) : null; if (m_ && marks.get(k) !== m_) { marks.set(k, m_); drawTrail(k); drawHomeTrail(); } }
  await jobs[2]; if (state.place !== p) return; drawHeader(); drawSide(); drawComposer(); syncPaused(c);
  markRead(c);
  if (!fresh) fetchCtx(c).then(() => { if (state.place === p) { drawHeader(); drawSide(); } });
}
/* a worktree its people paused (the topic's context says so) shows Zz in the nav while no run is live there */
function syncPaused(c) { const k = c.place.key, paused = !!(c.ctx && c.ctx.is_paused) && !liveRun(c), was = marks.get(k);
  if (paused && !was) { marks.set(k, "paused"); drawTrail(k); } else if (!paused && was === "paused") { marks.delete(k); drawTrail(k); } }
/* Home is a pane of its own: no conversation, no band, no field */
/* the picker is a place of its own too, with the band (its one sentence) and the field (its search) */
/* a person's page, a post and the editor are one pane (pages.js), with no band and no field, as Home */
function showHome(on, picker = false, page = false) { if (!feed) return; feed.hidden = on || picker || page; panes.Home.hidden = !on; if (panes.Partner) panes.Partner.hidden = !picker; if (panes.Page) panes.Page.hidden = !page; $("main > .dock").hidden = on || page; band.el.hidden = on || page; main.classList.toggle("bare", on || page); R.classList.toggle("picking", picker); }
function markRead(c) { const set = unread.get(c.place.key); if (!set || !set.size || document.hidden) return; const ids = [...set].filter((i) => typeof i === "number"); unread.delete(c.place.key); drawTrail(c.place.key); ids.forEach((i) => mentions.delete(i)); if (ids.length) api("POST", "messages/flags", {messages: ids, op: "add", flag: "read"}).catch(() => {}); }
/* messages read as they arrive in the open place: marked in one request a second after the first of them, and at once when
   the page is hidden or goes away (keepalive, so the request outlives the page) */
const READ_AFTER_MS = 1000, seen = new Set();
let seenTimer = 0;
function markSeen(id) { seen.add(id); if (!seenTimer) seenTimer = setTimeout(flushSeen, READ_AFTER_MS); }
function flushSeen() { clearTimeout(seenTimer); seenTimer = 0; if (!seen.size) return; const ids = [...seen]; seen.clear(); api("POST", "messages/flags", {messages: ids, op: "add", flag: "read"}, {keepalive: true}).catch(() => {}); }
addEventListener("pagehide", flushSeen); document.addEventListener("visibilitychange", () => { if (document.hidden) flushSeen(); });
let ctxTimer = 0;
function refreshCtx(c, ms = 1000) { clearTimeout(ctxTimer); ctxTimer = setTimeout(async () => { await fetchCtx(c); if (c === state.P) { drawHeader(); drawSide(); if (state.tab === "Changes") drawChanges(); } }, ms); }

export {addMsg, cacheOf, dkey, fetchMsgs, keepEnd, markRead, markSeen, openPlace, placeFromHash, placeFromKey, placeOfMsg, refreshCtx,
  tkey, topicLabel};
