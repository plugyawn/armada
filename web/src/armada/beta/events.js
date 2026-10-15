/* INTERIM BETA (README.md): what each live event does to the page. */

import {$, $$, esc, T} from "./util.js";
import {marks, msgIndex, places, state, stats, streams, topicsOf, unread} from "./state.js";
import {now} from "./time.js";
import {api} from "./connection.js";
import {addMsg, dkey, placeOfMsg, refreshCtx, tkey} from "./routing.js";
import {band, feed, lane} from "./frame.js";
import {faceOf, isOnix, nameOf, stoneOf} from "./faces.js";
import {QUESTION} from "./parts.js";
import {appendMessage, drawLane, grown, sectionOf, show} from "./feed.js";
import {drawNav, drawTrail} from "./nav.js";
import {answered, LIVE_MARKS, namesOnix, runMark, runsSeen, settleAsk} from "./runs.js";
import {drawProg} from "./checklist.js";
import {applyReaction} from "./reactions.js";
import {signOfLife} from "./receipts.js";
import {drawThreadLine, repliesOf, threadAppend} from "./threads.js";
import {follow, syncOnix, updateMessage, WORKING} from "./agent_turn.js";
import {adopt} from "./outbox.js";
import {drawRunState} from "./band.js";
import {drawSide} from "./column.js";
import {drawHomeRow, mentions, questionOf} from "./home.js";
import {hopOnce, refreshPartners} from "./partners.js";
import {drawLab} from "./lab.js";

/* ---------- live events: one queue ---------- */
let runWindow = null;
function onMessage(ev) {
  const m = ev.message, key = placeOfMsg(m), c = places.get(key), mine = m.sender_id === state.ME, flags = ev.flags || m.flags || [];
  if (m.type === "stream") { const list = topicsOf.get(m.stream_id), name = m.subject ?? ""; if (list && !list.some((t) => t.name.toLowerCase() === name.toLowerCase())) { list.unshift({name, max_id: m.id}); drawNav(); } }
  else if (!state.dms.some((d) => dkey(d.user_ids) === key)) { state.dms.unshift({user_ids: key.slice(2).split(",").filter(Boolean).map(Number), max_message_id: m.id}); drawNav(); }
  const seenNow = state.place && state.place.key === key && !document.hidden && state.tab === "Chat";
  if (!mine && !flags.includes("read")) { if (seenNow) api("POST", "messages/flags", {messages: [m.id], op: "add", flag: "read"}).catch(() => {}); else { let s = unread.get(key); if (!s) unread.set(key, (s = new Set())); s.add(m.id); drawTrail(key); if (flags.includes("mentioned") || flags.includes("stream_wildcard_mentioned") || flags.includes("topic_wildcard_mentioned")) mentions.set(m.id, m); drawHomeRow(key); } }
  { const runs = (c && c.runsLoaded ? c.runs : runsSeen.get(key)) || [], r = runs.find((x) => x.status === "queued" || x.status === "running");   /* a question asked, or answered by someone */
    if (r && isOnix(m.sender_id) && QUESTION.test(m.content || "")) { r.waiting = true; r.answeredAt = 0; r.askedAt = m.timestamp; r.question = questionOf(m); marks.set(key, "needs"); drawTrail(key); drawHomeRow(key); if (c === state.P) drawRunState(); }
    else if (r && r.waiting && !isOnix(m.sender_id) && namesOnix(m)) { answered(r); marks.set(key, runMark(r)); drawTrail(key); drawHomeRow(key); if (c === state.P) drawRunState(); } }
  if (!c || !c.loaded) return;
  if (ev.local_message_id && c.byId.has(ev.local_message_id)) { adopt(c, c.byId.get(ev.local_message_id), m.id, m); return; }
  if (c.byId.has(m.id)) { const mine_ = c.byId.get(m.id); adopt(c, mine_, m.id, m); return; }
  if (isOnix(m.sender_id) && WORKING(m)) m.maybeLive = true;
  if (isOnix(m.sender_id)) { const ph = c.msgs.find((x) => x.local && x.onix); if (ph) { ph.maybeLive = true; adopt(c, ph, m.id, m); if (c === state.P) { const cl = sectionOf(m.id); if (cl) { cl.dataset.mid = m.id; syncOnix(cl, ph); } } return; } }
  c.typers.delete(m.sender_id); addMsg(c, m);
  if (m.thread_root_message_id) { const root = c.byId.get(m.thread_root_message_id); if (root) { root.reply_summary = root.reply_summary || {reply_count: 0, participants: []}; root.reply_summary.reply_count = Math.max((root.reply_summary.reply_count || 0) + 1, repliesOf(c, root.id).length); if (c === state.P) { const cl = sectionOf(root.id); if (cl) grown(() => { drawThreadLine(cl, root); const box = $(`.thr-in[data-mid="${root.id}"]`, cl); if (box) threadAppend(box, m); }, "settle"); } } return; }
  if (c !== state.P) return;
  drawTyping(); drawSide();
  if (state.tab !== "Chat") { $$(":scope > .empty", lane).forEach((x) => x.remove()); appendMessage(lane, m); return; }
  const row = grown(() => { $$(":scope > .empty", lane).forEach((x) => x.remove()); return appendMessage(lane, m); }, "settle");
  if (state.pinned) { const last = row.parts[row.parts.length - 1], b = last && ($(".b", last) || last); if (b && !isOnix(m.sender_id)) { M.release(b, {y: T.rise || 6}, "settle", {id: "in"}); M.appear(b); } else if (isOnix(m.sender_id)) M.appear(row.cl, 0.3); }
  if (!mine && !isOnix(m.sender_id)) { S.play("arrive"); signOfLife(); hopOnce(row.cl); refreshPartners({wait: 4000, force: true}); }
}
function onUpdate(ev, at) {
  const c = msgIndex.get(ev.message_id); if (!c) return; const m = c.byId.get(ev.message_id); if (!m) return;
  if (ev.rendered_content == null) return;
  m.content = ev.rendered_content; if (!ev.rendering_only && ev.edit_timestamp) m.last_edit_timestamp = ev.edit_timestamp;   /* a person's edit, live: the turn says Edited (finish) */
  if (runWindow && runWindow.msg === m.id) runWindow.updates++;
  updateMessage(c, m, at);
}
function onRun(ev) {
  if (!ev.stream) return; const s = [...streams.values()].find((x) => x.name.toLowerCase() === ev.stream.toLowerCase()); if (!s) return; const key = tkey(s.stream_id, ev.topic || ""), c = places.get(key), live = ev.status === "queued" || ev.status === "running";
  const list = c ? c.runs : runsSeen.get(key) || []; if (!runsSeen.has(key)) runsSeen.set(key, list);
  let r = list.find((x) => x.id === ev.run_id); const fresh = !r; if (!r) { r = {id: ev.run_id, session_kind: "shared", date_created: ev.run_date_created, plan: null, overview: null, tool_count: 0, waiting: false}; list.unshift(r); }
  if (!live) r.waiting = false; else if (ev.event_type === "question_waiting" && !(r.answeredAt && now() / 1000 - r.answeredAt < 20)) { if (!r.waiting) r.askedAt = r.askedAt || ev.date_created; r.waiting = true; settleAsk(r); }   /* the server's heartbeat while it waits */
  const was = marks.get(key), want = live ? runMark(r, ev.status) : null; if (live) { if (was !== want) { marks.set(key, want); drawTrail(key); } } else if (LIVE_MARKS.has(was)) { if (ev.status === "failed" && !(state.place && state.place.key === key)) marks.set(key, "failed"); else marks.delete(key); drawTrail(key); }
  drawHomeRow(key);
  if (!c) return;
  const wasLive = r.status === "queued" || r.status === "running";
  Object.assign(r, {status: ev.status, trigger_message_id: ev.trigger_message_id, result_message_id: ev.result_message_id ?? r.result_message_id, current_activity: ev.current_activity, tool_count: ev.tool_count ?? r.tool_count, date_updated: ev.date_created, date_created: ev.run_date_created ?? r.date_created});
  if (ev.plan) r.plan = ev.plan; if (ev.overview) r.overview = ev.overview; if (ev.usage) r.usage = {...(r.usage || {}), ...ev.usage}; if (ev.duration_seconds != null) { r.duration_seconds = ev.duration_seconds; r.date_finished = ev.date_created; }
  if (ev.agents) r.agents = ev.agents;
  if (live && (!runWindow || runWindow.run !== r.id)) runWindow = {run: r.id, msg: r.result_message_id, t0: performance.now(), events: 0, updates: 0};
  if (runWindow && runWindow.run === r.id) { runWindow.events++; runWindow.msg = r.result_message_id; if (!live) { const secs = (performance.now() - runWindow.t0) / 1000, n = runWindow.events + runWindow.updates; stats.runs.push({run: r.id, seconds: Math.round(secs * 10) / 10, events: n, perSecond: Math.round(n / Math.max(secs, 0.001) * 10) / 10}); runWindow = null; drawLab(); } }
  if (c !== state.P) return;
  drawRunState(fresh); drawSide(); drawProg();
  if (r.result_message_id) { const cl = sectionOf(r.result_message_id), m = c.byId.get(r.result_message_id); if (cl && m) { const before = feed.scrollHeight; syncOnix(cl, m); const d = feed.scrollHeight - before; if (d) follow(d, cl); } }   /* what the conversation grew by: a footer that lands in the room the live run kept grows nothing */
  if (wasLive && !live) { S.play(ev.status === "failed" ? "fail" : "done"); refreshCtx(c, 600); }
}
function onReaction(ev) { const c = msgIndex.get(ev.message_id); if (!c) return; const m = c.byId.get(ev.message_id); if (m) applyReaction(c, m, ev, false); if (c === state.P && ev.user_id !== state.ME) signOfLife(); }
function onFlags(ev) { if (ev.flag !== "read" || ev.op !== "add") return; if (ev.all) { unread.clear(); drawNav(); return; } const ids = new Set(ev.messages); for (const [k, s] of unread) { let ch = false; for (const i of ids) if (s.delete(i)) ch = true; if (ch) { if (!s.size) unread.delete(k); drawTrail(k); } } }
function onDelete(ev) { const ids = ev.message_ids || [ev.message_id]; let redraw = false; for (const id of ids) { const c = msgIndex.get(id); if (!c) continue; const m = c.byId.get(id); c.byId.delete(id); msgIndex.delete(id); c.msgs = c.msgs.filter((x) => x !== m); if (c === state.P) redraw = true; } if (redraw) drawLane({keep: !state.pinned}); }
function onTyping(ev) { const key = ev.message_type === "stream" ? tkey(ev.stream_id, ev.topic || "") : dkey((ev.recipients || []).map((u) => u.user_id).filter((i) => i !== state.ME)), c = places.get(key), id = ev.sender.user_id; if (!c || id === state.ME || isOnix(id)) return;   /* Onix's own sign that it is working is its mark, not a typing bubble */
  clearTimeout(c.typers.get(id)); if (ev.op === "start" && c === state.P) signOfLife(); if (ev.op === "start") c.typers.set(id, setTimeout(() => { c.typers.delete(id); if (c === state.P) drawTyping(); }, 16000)); else c.typers.delete(id); if (c === state.P) drawTyping(); }
function drawTyping() { const ids = [...state.P.typers.keys()], ty = band.ty, on = ids.length > 0, sig = ids.join(","); if (ty._sig === sig) return; ty._sig = sig;
  if (on) { ty.innerHTML = (ids.length > 1 ? `<span class="avs">${ids.map((i) => faceOf(i)).join("")}</span>` : faceOf(ids[0])) + `<p class="tyb${ids.length === 1 ? " s-" + stoneOf(ids[0]) : ""}" id="tyb">${dots()}</p><span class="nm">${esc(ids.map((i) => nameOf(i).split(/[ ,]/)[0]).join(" and "))}</span>`; }
  ty.classList.toggle("chip", on && !band.run.classList.contains("off")); show(ty, on); }

export {drawTyping, onDelete, onFlags, onMessage, onReaction, onRun, onTyping, onUpdate};
