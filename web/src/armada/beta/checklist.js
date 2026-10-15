/* INTERIM BETA (README.md): the run's checklist, as the header and the column show it. */

import {$, el} from "./util.js";
import {state} from "./state.js";
import {hhmm} from "./time.js";
import {side} from "./frame.js";
import {liveRun} from "./runs.js";

function progOf(c) { const run = planRun(c); if (!run) return {title: "Checklist"}; const steps = run.plan, live = run.status === "queued" || run.status === "running";
  const marksOf = steps.map((s) => stepMark(s, run)), done = marksOf.filter((x) => x === "done").length;
  return {marks: marksOf, cap: live || done < steps.length ? `${done} of ${steps.length}` : `Finished ${hhmm(run.date_finished || run.date_updated || run.date_created)}`}; }
const planRun = (c) => { const l = liveRun(c); if (l && l.plan && l.plan.length) return l; return c.runs.find((r) => r.plan && r.plan.length) || null; };
const stepMark = (s, run) => (s.status === "completed" ? "done" : s.status === "in_progress" ? (run.status === "failed" ? "failed" : run.status === "cancelled" ? "cancelled" : run.status === "done" ? "done" : "live") : run.status === "cancelled" ? "cancelled" : "left");
function drawProg() { const cfg = progOf(state.P); const a = $("#prog", state.hd), b = $("#prog-side"); const sig = JSON.stringify(cfg); if (drawProg.sig === sig + state.place.key) return; drawProg.sig = sig + state.place.key;
  if (a) { const t = el("template"); t.innerHTML = progHTML(cfg, "prog"); a.replaceWith(t.content.firstElementChild); host(state.hd); } if (b) { const t = el("template"); t.innerHTML = progHTML(cfg, "prog-side"); b.replaceWith(t.content.firstElementChild); host(side); } if (state.hd) state.hd._sig = ""; }

export {drawProg, planRun, progOf, stepMark};
