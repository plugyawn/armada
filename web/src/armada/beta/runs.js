/* INTERIM BETA (README.md): runs: their marks, the live runs at load, the questions they wait on. */

import {$, $$} from "./util.js";
import {armadaBots, DATA, LIVE_RUN_IDS, marks, places, QUESTION_TIMEOUT, state, streams, topicsOf} from "./state.js";
import {now} from "./time.js";
import {api} from "./connection.js";
import {tkey} from "./routing.js";
import {lane} from "./frame.js";
import {isOnix} from "./faces.js";
import {QUESTION} from "./parts.js";
import {drawTrail, loadTopics} from "./nav.js";
import {drawRunState} from "./band.js";
import {drawHomeRow, questionOf} from "./home.js";

const MARK_RANK = {needs: 5, live: 4, queued: 3, failed: 2, paused: 1};
/* a live run's mark: waiting on me (the balloon), working, or queued behind another */
/* a run seen in a place (an event, the scan at load) is put first; a place keeps its newest RUNS_KEPT, and any older one still
   live (the column lists 30) */
const RUNS_KEPT = 30;
function seeRun(list, r) { list.unshift(r); for (let i = list.length - 1; i >= 0 && list.length > RUNS_KEPT; i--) if (!liveNow(list[i])) list.splice(i, 1); }
const runMark = (r, status = r.status) => (r.waiting ? "needs" : status === "queued" ? "queued" : "live");
const LIVE_MARKS = new Set(["needs", "live", "queued"]);
/* which worktrees have a run going: the page was given the live runs in my channels at load (zerver/views/armada_beta.py),
   read here in one request for every twenty; run events keep them current after that. The server's live state says
   whether each waits on a question; where that state is gone, or says it waits and the question's words are not known,
   the last word of each such worktree is read, all of them in one request. */
async function scanLive() { const unsure = [];
  for (let i = 0; i < LIVE_RUN_IDS.length; i += 20) { let r;
    try { r = await api("GET", "armada/runs", {run_ids: LIVE_RUN_IDS.slice(i, i + 20)}); } catch (e) { continue; }
    if (r.runtime_config) state.runtimeCfg = r.runtime_config;
    for (const r0 of r.armada_runs) { const u = adoptLive(r0); if (u) unsure.push(u); } }
  await lastWords(unsure); }
function adoptLive(r0) { if (r0.session_kind === "side" || !liveNow(r0)) return null; const s = [...streams.values()].find((x) => x.name.toLowerCase() === String(r0.stream || "").toLowerCase()); if (!s) return null;
  const k = tkey(s.stream_id, r0.topic || ""), c = places.get(k); let list = c && c.runsLoaded ? c.runs : runsSeen.get(k); if (!list) runsSeen.set(k, (list = []));
  let r = list.find((x) => x.id === r0.id); if (!r) { r = askState(r0); seeRun(list, r); }   /* known already (the place was opened, or an event came): that copy is newer */
  if (c && c.loaded) inferAsk(r, c.msgs); else if (!r0.live || (r.waiting && !r.question)) return {r, k, sid: s.stream_id};
  markLive(r, k); return null; }
async function lastWords(unsure) { if (!unsure.length) return;
  await Promise.all([...new Set(unsure.map((u) => u.sid))].map(loadTopics));
  const last = (u) => { const t = (topicsOf.get(u.sid) || []).find((x) => x.name.toLowerCase() === String(u.r.topic || "").toLowerCase()); return t ? t.max_id : 0; };
  const ids = [...new Set(unsure.map(last).filter(Boolean))]; let msgs = [];
  if (ids.length) try { msgs = (await api("GET", "messages", {message_ids: ids, apply_markdown: true, client_gravatar: true})).messages; } catch (e) {}
  for (const u of unsure) { const m = msgs.find((x) => x.id === last(u)); if (m) inferAsk(u.r, [m]); markLive(u.r, u.k); } }
function markLive(r, k) { if (!liveNow(r)) return; const want = runMark(r); if (marks.get(k) !== want) { marks.set(k, want); drawTrail(k); } }
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
/* where a mention of Onix starts a run, by the server's own rules (zerver/lib/armada.py, handle_message; armada_worker.py,
   start_blocked_reason): any channel's topic and never a direct message, with the agent set up, a person allowed to start
   its runs (the page's data, may-start-runs) and a topic its people have not paused. A channel with no repository is no
   exception: Onix's first run there makes one. */
const agentCanRun = (p = state.place, c = state.P) => !!p && p.kind === "topic" && armadaBots.size > 0 && DATA.mayStartRuns !== "off" && !(c && c.ctx && c.ctx.is_paused);
const runOfMsg = (c, id) => c.runs.find((r) => r.result_message_id === id);
const hasWorktree = (c) => !!(c.ctx && c.ctx.workspace_uuid && c.ctx.git && c.ctx.git.available);

export {agentCanRun, answered, askedAt, askState, drawAsks, hasWorktree, inferAsk, LIVE_MARKS, liveRun, MARK_RANK, namesOnix,
  runMark, runOfMsg, runsSeen, RUNS_KEPT, scanLive, seeRun, settleAsk, sweepAsks, waitingRun};
