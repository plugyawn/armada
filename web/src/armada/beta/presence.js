/* INTERIM BETA (README.md): presence, reported as the old interface reports it (web/src/activity.ts, activity_ui.ts).
   The page tells the server it is here when it loads, and every server_presence_ping_interval_seconds after: "active"
   while the window has focus and the person has done something in the last five minutes, else "idle"; coming back to an
   idle page says "active" at once. A person reported active is looking, and the server sends them no missed-message
   email or push for it. Each report brings everyone's changes since the last (the modern format: by user id, the last
   times each was active and idle), the presence event brings a person who comes back at once, and the faces that show
   presence are drawn again only when someone's state as a face shows it has changed. */

import {DATA, people, state} from "./state.js";
import {api} from "./connection.js";
import {presOf} from "./faces.js";
import {drawNav} from "./nav.js";
import {drawSide} from "./column.js";
import {drawHeader} from "./header.js";
import {card, drawCard} from "./partner_card.js";

const PING_MS = (+DATA.presencePing || 60) * 1000;
const IDLE_AFTER_MS = 5 * 60 * 1000;
/* as activity.ts: a page opened in a focused window is in use; a page the server reloaded unseen is not until a sign */
let lastInput = document.hasFocus() ? performance.now() : -Infinity, input = true, shown = "";

const status = () => (performance.now() - lastInput < IDLE_AFTER_MS ? "active" : "idle");
/* what register, a report's answer or an event says, kept by user id */
function applyPresences(presences) { for (const [id, p] of Object.entries(presences || {})) state.presences[id] = p; }
async function report(pingOnly) {
  let r; try { r = await api("POST", "users/me/presence", {status: status(), ping_only: pingOnly, new_user_input: input, last_update_id: state.presenceId}); } catch (e) { return; }
  input = false;
  if (!pingOnly) { applyPresences(r.presences); if (r.presence_last_update_id != null) state.presenceId = r.presence_last_update_id; }
  redraw();
}
/* the faces that show presence: the nav's direct messages, the column's people, a direct message's header, a card */
function redraw() {
  if (document.hidden) return;
  const now = [...people.keys()].map((id) => presOf(id)).join(); if (now === shown) return; shown = now;
  drawNav(); if (state.place && state.place.kind !== "home" && state.P) { drawSide(); if (state.place.kind === "dm") { state.hd._sig = ""; drawHeader(); } } if (card) drawCard();
}
function onPresence(ev) { applyPresences(ev.presences); redraw(); }
/* a sign of the person (as activity.ts counts them): the page is in use; one that comes after five quiet minutes says so at once */
function used() { const was = status(); lastInput = performance.now(); input = true; if (was === "idle") report(true); }
function startPresence() {
  for (const t of ["keydown", "mousedown", "mousemove", "touchmove", "touchstart", "wheel"]) addEventListener(t, used, {capture: true, passive: true});
  addEventListener("focus", used);
  document.addEventListener("visibilitychange", redraw);
  shown = [...people.keys()].map((id) => presOf(id)).join();
  report(true);
  setInterval(() => report(false), PING_MS);
}

export {onPresence, startPresence};
