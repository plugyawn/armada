/* INTERIM BETA (README.md): the workspace menu, opened from the workspace's name: who I am and the few real controls. */

import {$, el, esc, R, store} from "./util.js";
import {state} from "./state.js";
import {ready} from "./composer.js";
import {signOut} from "./connection.js";

/* ---------- the workspace menu ---------- */
let lab;
function buildLab() {
  /* the workspace's name opens the menu; beside the name, a word only while the connection is in trouble (connection.js) */
  const wk = $("nav .top .wk-name");
  lab = el("div", "", `<span id="lab-t" hidden></span><div class="pn"></div>`); lab.id = "lab"; if (wk) wk.after(lab); else document.body.append(lab);
  const show = (open) => { lab.classList.toggle("open", open); if (wk) wk.setAttribute("aria-expanded", String(open)); drawLab(); };
  if (wk) { wk.setAttribute("aria-haspopup", "dialog"); wk.setAttribute("aria-expanded", "false"); wk.onclick = () => show(!lab.classList.contains("open")); }
  lab.addEventListener("keydown", (e) => { if (e.key === "Escape" && lab.classList.contains("open")) { e.stopPropagation(); show(false); if (wk) wk.focus(); } });
  document.addEventListener("mousedown", (e) => { if (lab.classList.contains("open") && !lab.contains(e.target) && !(wk && wk.contains(e.target))) show(false); });
  lab.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
  lab.addEventListener("click", (e) => { if (e.target.closest("[data-sign-out]")) { signOut(); return; } const b = e.target.closest("[data-set]"); if (!b) return; const [k, v] = b.dataset.set.split(":");
    if (k === "theme") { if (v === "auto") delete R.dataset.theme; else R.dataset.theme = v; store.set("theme", v); }
    if (k === "sound") S.set(!S.on());
    if (k === "motion") { if (R.dataset.motion === "reduced") { delete R.dataset.motion; store.set("motion", ""); } else { R.dataset.motion = "reduced"; store.set("motion", "reduced"); } M.stretch(); }
    drawLab(); ready(); });
}
function drawLab() { if (!lab || !lab.classList.contains("open")) return; const th = R.dataset.theme || "auto";
  const btn = (set, label, on) => `<button data-set="${set}"${on ? ' class="on"' : ""}>${label}</button>`;
  $(".pn", lab).innerHTML = `<p>Signed in as <b>${esc(state.meName)}</b>. <button data-sign-out>Sign out</button></p>
    <h4>Theme</h4>${["auto", "light", "dark"].map((v) => btn("theme:" + v, v[0].toUpperCase() + v.slice(1), th === v)).join("")}
    <h4>Sound and motion</h4>${btn("sound:x", S.on() ? "Sound is on" : "Sound is off", S.on())}${btn("motion:x", R.dataset.motion === "reduced" ? "Motion is reduced" : "Reduce motion", R.dataset.motion === "reduced")}
    <p style="color:var(--ink-2)">Return sends. In a repository the Onix switch in the field is off until you turn it on there; on, Return starts a run, and while a run is live it steers it (⌘. stops it). Writing @Onix asks it either way. ⌘K jumps anywhere.</p>`; }

/* the connection's trouble, beside the workspace's name: a word while it lasts, nothing otherwise */
function showTrouble(status) { const t = $("#lab-t"); if (!t) return; t.textContent = status === "reconnecting" ? "Reconnecting…" : status === "offline" ? "Offline" : ""; t.hidden = !t.textContent; }

export {buildLab, drawLab, lab, showTrouble};
