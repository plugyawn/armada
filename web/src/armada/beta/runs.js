/* INTERIM BETA (README.md): runs: their marks, the live runs at load, the questions they wait on. */

import {$, $$} from "./util.js";
import {LIVE_RUN_IDS, marks, places, QUESTION_TIMEOUT, state, streams} from "./state.js";
import {now} from "./time.js";
import {api} from "./connection.js";
import {tkey} from "./routing.js";
import {lane} from "./frame.js";
import {isOnix} from "./faces.js";
import {QUESTION} from "./parts.js";
import {drawTrail} from "./nav.js";
import {drawRunState} from "./band.js";
import {drawHomeRow, questionOf} from "./home.js";

const MARK_RANK = {needs: 5, live: 4, queued: 3, failed: 2, paused: 1};
/* a live run's mark: waiting on me (the balloon), working, or queued behind another */
const runMark = (r, status = r.status) => (r.waiting ? "needs" : status === "queued" ? "queued" : "live");
const LIVE_MARKS = new Set(["needs", "live", "queued"]);
/* which worktrees have a run going: the page was given the live runs in my channels at load (zerver/views/armada_beta.py),
   read here in one request for every twenty; run events keep them current after that. For each, the last few messages
   of its worktree say whether it waits on a question the server's live state no longer holds. */
async function scanLive() {
  for (let i = 0; i < LIVE_RUN_IDS.length; i += 20) { let r;
    try { r = await api("GET", "armada/runs", {run_ids: LIVE_RUN_IDS.slice(i, i + 20)}); } catch (e) { continue; }
    if (r.runtime_config) state.runtimeCfg = r.runtime_config;
    await Promise.all(r.armada_runs.map(adoptLive)); } }
async function adoptLive(r0) { if (r0.session_kind === "side" || !liveNow(r0)) return; const s = [...streams.values()].find((x) => x.name.toLowerCase() === String(r0.stream || "").toLowerCase()); if (!s) return;
  const k = tkey(s.stream_id, r0.topic || ""), c = places.get(k); let list = c && c.runsLoaded ? c.runs : runsSeen.get(k); if (!list) runsSeen.set(k, (list = []));
  let r = list.find((x) => x.id === r0.id); if (!r) { r = askState(r0); list.unshift(r); }   /* known already (the place was opened, or an event came): that copy is newer */
  if (c && c.loaded) inferAsk(r, c.msgs); else { try { const mr = await api("GET", "messages", {narrow: [{operator: "channel", operand: s.stream_id}, {operator: "topic", operand: r.topic}], anchor: "newest", num_before: 6, num_after: 0, apply_markdown: true, client_gravatar: false}); inferAsk(r, mr.messages); } catch (e) {} }
  if (!liveNow(r)) return; const want = runMark(r); if (marks.get(k) !== want) { marks.set(k, want); drawTrail(k); } }
/* a run that waits for a person: the server's live state says it is on a question (request_user_input); the answer is
   any message that names the agent while the run waits (zerver/lib/armada.py, request_user_answers) */
function askState(r) { if (r.waiting === undefined) r.waiting = !!(r.live && r.live.operation && r.live.operation.kind === "question" && (r.status === "running" || r.status === "queued")); return r; }
/* the live state can be gone (it is a cache entry), so the conversation says it too: a run waits when Onix's question is
   the last word after its trigger and no one has named Onix since */
const namesOnix = (m) => [...(m.content || "").matchAll(/data-user-id="(\d+)"/g)].some((x) => isOnix(+x[1]));
function inferAsk(r, msgs) { if (!r || !(r.status === "running" || r.status === "queued")) return r; let q = null;
  let lastQ = null; for (const m of msgs) { if (typeof m.id !== "number" || m.id <= r.trigger_message_id) continue; if (isOnix(m.sender_id) && QUESTION.test(m.content || "")) q = lastQ = m; else if (q && (isOnix(m.sender_id) || namesOnix(m))) q = null; }   /* answered, or Onix went on */
  if (q && !(r.answeredAt && r.answeredAt >= q.timestamp)) { r.waiting = true; r.askedAt = q.timestamp; r.question = questionOf(q); }
  else if (r.waiting && !r.askedAt && lastQ) { r.askedAt = lastQ.timestamp; r.question = questionOf(lastQ); }   /* the server's live state says it waits but not since when: the question's message does */
  return settleAsk(r); }
/* the server waits QUESTION_TIMEOUT for an answer and then goes on with defaults (request_user_answers): past that, the
   question is spent wherever it is read (the band, the nav, Home), and the run is working again from that moment. The
   grace covers the gap between the question's message and the server starting its clock. */
function settleAsk(r) { if (r && r.waiting && r.askedAt && now() / 1000 - r.askedAt > QUESTION_TIMEOUT + 30) { r.waiting = false; r.resumedAt = r.askedAt + QUESTION_TIMEOUT; } return r; }
function answered(r) { r.waiting = false; r.answeredAt = r.resumedAt = now() / 1000; r.askedAt = 0; }
const liveNow = (r) => r.status === "queued" || r.status === "running";
/* a question can pass its time while no event comes: every few seconds the waiting runs are read against the clock */
function sweepAsks() { let any = false;
  for (const [k, runs] of runsSeen) for (const r of runs) { if (!r.waiting || !liveNow(r)) continue; settleAsk(r); if (r.waiting) continue; any = true; if (marks.get(k) === "needs") { marks.set(k, runMark(r)); drawTrail(k); } }
  if (any) { drawHomeRow(); if (state.P && state.place && state.place.kind !== "home") drawRunState(); } }
const waitingRun = (c) => { const r = c && liveRun(c); return r && r.waiting ? r : null; };
const runsSeen = new Map();                                /* place key -> runs, from the nav's scan and from places opened: what Home reads */
const askedAt = (c, r) => { if (r.askedAt) return r.askedAt; const q = c.msgs.filter((m) => isOnix(m.sender_id) && typeof m.id === "number" && m.id > r.trigger_message_id && QUESTION.test(m.content || "")).pop(); return q ? q.timestamp : r.date_updated || r.date_created; };
/* the options under the question in sight are live only while its run waits */
function drawAsks() { if (!state.P || !lane) return; const r = waitingRun(state.P), all = $$(".blk.ask", lane), last = all[all.length - 1]; all.forEach((q) => { const on = !!r && q === last; $$(".opt", q).forEach((o) => o.setAttribute("aria-disabled", String(!on)));   /* not disabled: a control disabled under the pointer drops the focus to the page */ q.classList.toggle("done", !on);
    /* the caption under the options says what is true now: a question the run no longer waits on does not say it waits */
    const cap = q.nextElementSibling && $("p.askcap > em", q.nextElementSibling); if (cap) { if (cap.dataset.live == null) cap.dataset.live = cap.textContent; const t = on ? cap.dataset.live : "The run no longer waits for an answer here."; if (cap.textContent !== t) cap.textContent = t; } }); }
const liveRun = (c) => c.runs.find((r) => r.status === "queued" || r.status === "running");
const runOfMsg = (c, id) => c.runs.find((r) => r.result_message_id === id);
const hasWorktree = (c) => !!(c.ctx && c.ctx.workspace_uuid && c.ctx.git && c.ctx.git.available);

export {answered, askedAt, askState, drawAsks, hasWorktree, inferAsk, LIVE_MARKS, liveRun, MARK_RANK, namesOnix,
  runMark, runOfMsg, runsSeen, scanLive, settleAsk, sweepAsks, waitingRun};
