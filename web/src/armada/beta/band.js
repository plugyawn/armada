/* INTERIM BETA (README.md): the band: what the run is doing, its clock, Stop. */

import {$} from "./util.js";
import {state} from "./state.js";
import {dayLabel, hhmm, mmss, now} from "./time.js";
import {api} from "./connection.js";
import {band} from "./frame.js";
import {note, onixHere, onixNow, ready, setVerb, switchHolds} from "./composer.js";
import {show} from "./feed.js";
import {askedAt, drawAsks, liveRun, runMark, waitingRun} from "./runs.js";
import {editing} from "./hover.js";

/* ---------- the band, the verb, Stop ---------- */
let stopping = 0;
/* the band's far end says what the run is doing: its own overview line, else its current activity when that is a thing
   it is doing (a command, a file) and not the runtime's bookkeeping or the request repeated */
function actOf(c, r) { if (r.overview) return r.overview; const a = (r.current_activity || "").trim(), trig = c.byId.get(r.trigger_message_id), asked = trig ? (trig.raw || (trig.content || "").replace(/<[^>]+>/g, "")).replace(/^\s*@?\**Onix\**\s*/i, "").trim() : "";
  if (!a || /^(Codex\b.*|Completed\.?|Working\.?|Rate limit.*|Queued.*)$/i.test(a) || (asked && asked.startsWith(a.replace(/…$|\.\.\.$/, "").slice(0, 40)))) return ""; return a; }
function drawRunState(cut) {
  const r = liveRun(state.P), ask = waitingRun(state.P), wt = state.place.kind === "topic" && state.place.repo, raised = !!r && (!!ask || now() / 1000 - r.date_created > 0.4 || (r.plan && r.plan.length) || r.tool_count > 0), b = band;
  if (r && !raised) setTimeout(() => { if (state.P && liveRun(state.P) === r) drawRunState(); }, 450);
  b.run.classList.toggle("att", !!ask); const lead = $(":scope > .mk", b.run), want = r ? runMark(r) : "live"; if (lead && !lead.classList.contains(want)) lead.outerHTML = want === "live" ? wk(true) : mk(want);
  const quiet = !!r && !ask && quietRun(r);
  if (raised) { const word = ask ? "Onix asks" : stopping === r.id ? "Stopping" : r.status === "queued" ? "Queued" : quiet ? "Quiet" : "Working", cur = $(":scope > :not(.gone):not(.sz)", b.st);
    if (!cur || cur.textContent !== word) { if (cut || b.run.classList.contains("off")) b.st.innerHTML = `<span>${word}</span><i class="sz">Stopping</i>`; else M.swap(b.st, `<span>${word}</span>`, {quiet: true, seq: true}); }
    tick(true); const act = quiet ? `No word from this run since ${dayLabel(lastSign(r))} ${hhmm(lastSign(r))}. The server still lists it as running.` : actOf(state.P, r); if (b.act.textContent !== act) b.act.textContent = act; b.stop.setAttribute("aria-disabled", stopping === r.id); }
  if (cut) { b.run.classList.toggle("off", !raised); b.run.inert = !raised; b.run.style.opacity = ""; } else show(b.run, raised);
  b.ty.classList.toggle("chip", raised && state.P.typers.size > 0);
  setVerb(state.place.kind === "partner" ? "Choose" : editing ? "Save" : ask && onixNow() && switchHolds() ? "Answer" : raised && wt && onixHere() && switchHolds() ? "Steer" : "Send", cut);   /* with the switch off a waiting run is answered by its options or by naming @Onix */
  if (!r) stopping = 0;
  drawAsks();
}
/* a run the server still calls running that has given no sign of life for a long while (an old fixture, a worker that died):
   the band says it is quiet and for how long, not that it has been working all that time */
const QUIET_AFTER = 15 * 60;
const lastSign = (r) => Math.max(r.date_updated || 0, r.date_created || 0, r.resumedAt || 0);
const quietRun = (r) => r.status === "running" && !r.waiting && now() / 1000 - lastSign(r) > QUIET_AFTER;
function tick(force) { if (!state.P) return; const r = liveRun(state.P); if (!r || band.run.classList.contains("off") && !force) return; if (!r.waiting && quietRun(r) && !/Quiet/.test(band.st.textContent)) { drawRunState(); return; }
  const v = mmss(Math.max(0, Math.floor(now() / 1000 - (r.waiting ? askedAt(state.P, r) : quietRun(r) ? lastSign(r) : r.resumedAt || r.date_created)))); if (force) band.clock.innerHTML = fig(v); else M.figure(band.clock, v, {cut: true}); }   /* asking: since the question; working: since the run started or went on after a question */
async function stop() { const r = state.P && liveRun(state.P); if (!r || stopping === r.id) { ready(); return; } stopping = r.id; drawRunState(); ready();
  try { await api("POST", `armada/runs/${r.id}/cancel`); } catch (e) { stopping = 0; drawRunState(); note(e.message); } }

export {drawRunState, stop, tick};
