/* INTERIM BETA (README.md): the column: the checklist's standing home, Changes, Runs, People. */

import {esc, inlineMd, mid} from "./util.js";
import {armadaBots, people, state} from "./state.js";
import {age, dayKey, hhmm, now, shortDay} from "./time.js";
import {dkey, placeFromKey, topicLabel} from "./routing.js";
import {outline, side} from "./frame.js";
import {faceOf, isOnix, nameOf, presOf} from "./faces.js";
import {agentHere, runMark, runsSeen, runTitle} from "./runs.js";
import {planRun, stepMark} from "./checklist.js";
import {homeScanned} from "./home.js";
import {pack} from "./partners.js";
import {pickerSideHTML} from "./partner_picker.js";
import {pagesSideHTML} from "./pages.js";

/* ---------- the column: the checklist's standing home, Changes, Runs, People ---------- */
function drawSide() {
  const c = state.P, p = state.place, agent = agentHere(p, c); let h = "";   /* a topic where Onix can run, or has: its checklist and runs */
  if (p.kind === "home") { const live = []; for (const [k, rs] of runsSeen) { const r = rs.find((x) => x.status === "queued" || x.status === "running"), q = placeFromKey(k); if (r && q) live.push({k, r, q}); }
    h += '<section class="sec grow" id="sec-running"><ul class="ls">' + (live.length ? live.map(({k, r, q}) => `<li class="on" data-k="${esc(k)}">${mk(runMark(r))}<span class="t">${esc(topicLabel(q.topic))}</span><span class="e"><span class="d">${esc(q.stream)}</span></span></li>`).join("") : `<li>${ic("check")}<span class="t">${homeScanned ? "No run is live" : "Looking"}</span><span class="e"></span></li>`) + "</ul></section>";
    const here = [...people.values()].filter((u) => !u.bot && u.active && u.id !== state.ME && presOf(u.id) === "on").slice(0, 8);
    h += '<section class="sec foot" id="sec-people"><div class="sh"><span>People</span></div><ul class="ls">' + `<li>${faceOf(state.ME, "s20", "on")}<span class="t">${esc(state.meName)}</span><span class="e"></span></li>` + here.map((u) => `<li data-k="${esc(dkey([u.id]))}">${faceOf(u.id, "s20", "on")}<span class="t">${esc(u.name)}</span><span class="e"></span></li>`).join("") + "</ul></section>";
    if (outline._sig === h) return; outline._sig = h; outline.innerHTML = h; host(outline); fitLists(side); return; }
  if (p.kind === "page") { h = pagesSideHTML(); if (outline._sig === h) return; outline._sig = h; outline.innerHTML = h; host(outline); fitLists(side); return; }
  if (p.kind === "partner") { h = pack ? pickerSideHTML() : ""; if (outline._sig === h) return; outline._sig = h; outline.innerHTML = h; host(outline); return; }
  if (agent) { const run = planRun(c);
    if (run) { const live = run.status === "queued" || run.status === "running";
      /* the checklist reserves the rows it has held in this run, up to seven: a plan that grows pushes the lists below once */
      c.chkRows = c.chkRows || {}; const rows = Math.min(7, Math.max(c.chkRows[run.id] || 0, run.plan.length + (live && run.overview ? 1 : 0))); c.chkRows[run.id] = rows;
      h += `<ol class="chk" id="steps" style="--rows:${rows}">` + run.plan.map((s) => { const mkn = stepMark(s, run), cls = mkn === "live" ? "now" : mkn; return `<li class="step ${cls}">${mk(mkn, mkn === "live" ? "live" : "")}<span class="t">${inlineMd(s.step)}</span><span class="e"></span></li>` + (mkn === "live" && live && run.overview ? `<li class="ev act"><i></i><span class="t cell"><span>${esc(run.overview)}</span></span><span class="e"></span></li>` : ""); }).join("") + "</ol>"; }
    else h += `<div class="note">${c.runsLoaded ? (c.runs.length ? "The runs here had no plan. A run that plans its work shows its steps here." : "No run here yet.") : "Loading"}</div>`;
    /* Changes, where there are git facts to show: a channel with a repository (a channel gains one with its first run) */
    if (p.repo) { const g = c.ctx && c.ctx.git && c.ctx.git.available ? c.ctx.git : null, files = g ? g.changed_files || [] : [];
    c.changeRows = Math.min(4, Math.max(1, files.length, c.changeRows || 0));   /* the rows Changes has held while the place is open, up to four */
    h += `<section class="sec slots" id="sec-changes" style="--slots:${c.changeRows}"><div class="sh"><span>Changes</span><span class="e"><button class="lk" data-goto="Changes">Review</button></span></div><ul class="ls">` + (files.length ? files.map((f) => `<li>${ic("file")}<span class="t"><code>${esc(mid(f.path, 26))}</code></span><span class="e"><span class="kd">${esc(f.kind)}</span></span></li>`).join("") : `<li>${ic(g ? "check" : "file")}<span class="t">${g ? "No uncommitted changes" : c.ctx ? "No worktree yet" : "Loading"}</span><span class="e"></span></li>`) + "</ul></section>"; }
    h += '<section class="sec grow" id="sec-runs"><div class="sh"><span>Runs</span></div><ul class="ls">' + c.runs.slice(0, 30).map((r) => { const live = r.status === "queued" || r.status === "running", m = live ? "live" : r.status === "done" ? "done" : r.status === "failed" ? "failed" : r.status === "cancelled" ? "cancelled" : "left", title = runTitle(c, r);
      return `<li class="${live ? "on" : ""}">${mk(live ? runMark(r) : m)}<span class="t">${esc(title.slice(0, 80))}</span><span class="e"><span class="d">${live ? esc(age(r.date_created)) : esc(dayKey(r.date_created) === dayKey(now() / 1000) ? hhmm(r.date_created) : shortDay(r.date_created))}</span></span></li>`; }).join("") + (c.runs.length ? "" : `<li>${ic("trace")}<span class="t">${c.runsLoaded ? "No runs yet" : "Loading"}</span><span class="e"></span></li>`)
      + "</ul></section>"; }
  const ids = p.kind === "dm" ? [state.ME, ...p.ids] : [...new Set(c.msgs.filter((m) => !isOnix(m.sender_id)).map((m) => m.sender_id))].slice(-8);
  /* in a topic Onix has no part in, the column's heading already says People (headCfg) */
  h += `<section class="sec ${agent ? "foot" : "grow"}" id="sec-people">${p.kind === "topic" && !agent ? "" : '<div class="sh"><span>People</span></div>'}<ul class="ls">` + (agent ? `<li>${faceOf([...armadaBots][0], "s20")}<span class="t">Onix</span><span class="e"></span></li>` : "") + ids.map((i) => `<li>${faceOf(i, "s20", presOf(i))}<span class="t">${esc(nameOf(i))}</span><span class="e"></span></li>`).join("") + "</ul></section>";
  if (outline._sig === h) return; outline._sig = h; outline.innerHTML = h; host(outline); fitLists(side);
}

export {drawSide};
