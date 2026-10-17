/* INTERIM BETA (README.md): the run's checklist, as the header, the column and the run's turn show it. */

import {$, el, inlineMd} from "./util.js";
import {state} from "./state.js";
import {hhmm} from "./time.js";
import {side} from "./frame.js";
import {liveRun} from "./runs.js";

function progOf(c) { const run = planRun(c); if (!run) return liveRun(c) ? {title: "Checklist", cap: "Starting"} : {title: "Checklist"}; const steps = run.plan, live = run.status === "queued" || run.status === "running";
  const marksOf = steps.map((s) => stepMark(s, run)), done = marksOf.filter((x) => x === "done").length;
  return {marks: marksOf, cap: live || done < steps.length ? `${done} of ${steps.length}` : `Finished ${hhmm(run.date_finished || run.date_updated || run.date_created)}`}; }
const planRun = (c) => { const l = liveRun(c); if (l && l.plan && l.plan.length) return l; return c.runs.find((r) => r.plan && r.plan.length) || null; };
/* a step's mark: a tick only for a step Codex marked completed, so the marks agree with the count ("1 of 4" counts completed
   steps). The step in hand takes the star while its run lives; when the run ends with it unfinished, the cross if the run
   failed, the bar if it was stopped, and else the dashed ring: it waits for a next turn. Steps not reached keep the ring
   ("left"), or take the bar in a run that was stopped. */
const stepMark = (s, run) => { if (s.status === "completed") return "done"; const live = run.status === "queued" || run.status === "running";
  if (s.status === "in_progress") return live ? "live" : run.status === "failed" ? "failed" : run.status === "cancelled" ? "cancelled" : "queued";
  return run.status === "cancelled" ? "cancelled" : "left"; };
function drawProg() { const cfg = progOf(state.P); const a = $("#prog", state.hd), b = $("#prog-side"); const sig = JSON.stringify(cfg); if (drawProg.sig === sig + state.place.key) return; drawProg.sig = sig + state.place.key;
  if (a) { const t = el("template"); t.innerHTML = progHTML(cfg, "prog"); a.replaceWith(t.content.firstElementChild); host(state.hd); } if (b) { const t = el("template"); t.innerHTML = progHTML(cfg, "prog-side"); b.replaceWith(t.content.firstElementChild); host(side); } if (state.hd) state.hd._sig = ""; }

/* the checklist in the run's turn (statement 28): the steps Codex keeps, followed live, at the head of the turn. One element
   for the life of the run, and one row and one mark for the life of each step: the mark's state moves it from ring to star
   to tick (kit.js smk), so a step that starts or finishes is seen to. It stays when the run ends, as the record of what
   was done; a plan, once shown, never leaves the turn, so nothing under it moves back up. */
function drawTurnPlan(cl, run) {
  const steps = run && run.plan && run.plan.length ? run.plan : null; if (!steps) return; let box = $(":scope > .plan", cl);
  if (!box) { box = el("div", "plan", '<div class="ph"><span>Checklist</span><span class="pc"></span></div><ol class="chk"></ol>'); box.setAttribute("role", "group"); box.setAttribute("aria-label", "Checklist");
    const first = $(":scope > [data-part], :scope > .line", cl); if (first) first.before(box); else cl.append(box); }
  const ol = $("ol", box), rows = [...ol.children];
  steps.forEach((s, i) => { const want = stepMark(s, run); let li = rows[i];
    if (!li) { li = el("li", "step", `${smk(want)}<span class="t"></span>`); ol.append(li); if (rows.length && !M.reduced()) M.appear(li); }
    const t = $(".t", li), words = inlineMd(s.step); if (t._src !== words) { t.innerHTML = words; t._src = words; }
    const mark = $(".sm", li); if (mark.dataset.s !== want) mark.dataset.s = want;
    const cls = "step " + (want === "live" ? "now" : want); if (li.className !== cls) li.className = cls;
    if (want === "live") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current"); });
  rows.slice(steps.length).forEach((x) => x.remove());
  const done = steps.filter((s) => s.status === "completed").length, cap = `${done} of ${steps.length}`, pc = $(".pc", box); if (pc.textContent !== cap) pc.textContent = cap; }

export {drawProg, drawTurnPlan, planRun, progOf, stepMark};
