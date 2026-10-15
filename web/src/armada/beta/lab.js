/* INTERIM BETA (README.md): the Lab: what is real, what is not, and the few real controls. */

import {$, el, esc, R, store} from "./util.js";
import {state, stats} from "./state.js";
import {ready} from "./composer.js";

/* ---------- the Lab: what is real, what is not, and the few real controls ---------- */
const NOT = ["New, the workspace menu, More in the header; settings", "The keyboard selection of messages; a reaction picker (React and a double press give 👍; a chip toggles)", "Queue (⌘Return), sub-agents, the trace, artifacts, monitors", "Diffs, Commit, pull requests, file editing, syntax colour in Files", "The checklist as a sheet below 1408 px", "Sending my own typing; presence after load; moved or renamed topics; channels I am not subscribed to", "The typing bubble growing into the message (it appears, it does not morph)"];
let lab;
function buildLab() {
  /* the standing label opens the beta's panel; beside it, a plain link back to the old interface */
  lab = el("div", "", `<button id="lab-t">Beta</button><div class="pn"></div>`); lab.id = "lab"; const wk_ = $("nav .top .wk-name"); if (wk_) wk_.after(lab); else document.body.append(lab);   /* fixed in place; in the DOM after the workspace's name, where it stands */
  $("#lab-t").onclick = () => { lab.classList.toggle("open"); drawLab(); };
  lab.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
  lab.addEventListener("click", (e) => { const b = e.target.closest("[data-set]"); if (!b) return; const [k, v] = b.dataset.set.split(":");
    if (k === "theme") { if (v === "auto") delete R.dataset.theme; else R.dataset.theme = v; store.set("theme", v); }
    if (k === "sound") S.set(!S.on());
    if (k === "motion") { if (R.dataset.motion === "reduced") { delete R.dataset.motion; store.set("motion", ""); } else { R.dataset.motion = "reduced"; store.set("motion", "reduced"); } M.stretch(); }
    drawLab(); ready(); });
}
function drawLab() { if (!lab || !lab.classList.contains("open")) return; const th = R.dataset.theme || "auto", last = stats.sends[stats.sends.length - 1], fw = stats.firstWords[stats.firstWords.length - 1], rn = stats.runs[stats.runs.length - 1];
  const btn = (set, label, on) => `<button data-set="${set}"${on ? ' class="on"' : ""}>${label}</button>`;
  $(".pn", lab).innerHTML = `<p>The beta of Armada's new interface, on this server's real data, signed in as <b>${esc(state.meName)}</b>. Settings and everything listed below stay in the <a href="/">old interface</a>.</p>
    <h4>Theme</h4>${["auto", "light", "dark"].map((v) => btn("theme:" + v, v[0].toUpperCase() + v.slice(1), th === v)).join("")}
    <h4>Sound and motion</h4>${btn("sound:x", S.on() ? "Sound is on" : "Sound is off", S.on())}${btn("motion:x", R.dataset.motion === "reduced" ? "Motion is reduced" : "Reduce motion", R.dataset.motion === "reduced")}
    <h4>Measured in this page</h4><p class="num">Last send: task ${last ? last.task + " ms" : "none yet"}, Return to bubble painted ${last && last.painted != null ? last.painted + " ms" : "none yet"}<br>First streamed event to its words painted: ${fw != null ? fw + " ms" : "none yet"}<br>Last run: ${rn ? `${rn.events} events in ${rn.seconds} s (${rn.perSecond} per second)` : "none yet"}<br>Events handled: ${stats.events}. Requests made: ${stats.api}.</p>
    <h4>Not in the beta yet</h4><ul>${NOT.map((x) => `<li>${x}</li>`).join("")}</ul>
    <p style="color:var(--ink-2)">Return sends. In a repository the Onix switch in the field is off until you turn it on there; on, Return starts a run, and while a run is live it steers it (⌘. stops it). Writing @Onix asks it either way. ⌘K jumps anywhere.</p>`; }

export {buildLab, drawLab, lab};
