/* INTERIM BETA (README.md): the connection: requests, registering, the event queue and its dispatch, signing out.
   The rules (one register in flight, backoff, the long poll's deadline, the CSRF token mended, the states that stop
   every request) are link.js's; this module gives them the page. */

import {$, el, esc} from "./util.js";
import {armadaBots, groups, people, places, repoStreams, state, stats, streams, unread} from "./state.js";
import {dkey, openPlace, tkey} from "./routing.js";
import {closeMenu, overHide} from "./hover.js";
import {closePicker} from "./model_picker.js";
import {palette} from "./palette.js";
import {fetchPartners} from "./partners.js";
import {tx} from "./frame.js";
import {liveRun} from "./runs.js";
import {resetStreams} from "./agent_turn.js";
import {createLink} from "./link.js";

/* the tabs of this browser tell each other whose session they hold, and when it ends (resilience.md 2.5) */
const tabs = typeof BroadcastChannel === "function" ? new BroadcastChannel("armada") : null;
const tell = (msg) => { if (tabs) tabs.postMessage(msg); };

const link = createLink({
  state,
  csrf: ($('input[name="csrfmiddlewaretoken"]') || {}).value || "",   /* the session's token, written into the page by the server */
  fetch: (url, init) => fetch(url, init),
  now: () => performance.now(),
  setTimeout: (f, ms) => setTimeout(f, ms),
  clearTimeout: (t) => clearTimeout(t),
  random: Math.random,
  hidden: () => document.hidden,
  online: () => navigator.onLine !== false,
  registerParams: () => ({fetch_event_types: ["muted_users", "subscription", "realm_user", "presence", "update_message_flags", "message", "recent_private_conversations", "realm_bot", "stream", "realm", "realm_user_groups"],
    event_types: ["message", "update_message", "reaction", "agent_run", "agent_stream", "workspace_changed", "typing", "delete_message", "update_message_flags", "armada_partner", "read_receipts", "restart", "armada_post", "realm", "user_group", "stream", "realm_user"], apply_markdown: true, client_gravatar: false,
    client_capabilities: {notification_settings_null: true, bulk_message_deletion: true, user_avatar_url_field_optional: false, stream_typing_notifications: true, user_settings_object: true, linkifier_url_template: true, user_list_incomplete: false, include_deactivated_groups: false, archived_channels: false, empty_topic_name: true}}),
  onRegister: applyRegister,
  onEvent: handle,
  onResync: resync,
  onStatus: drawStatus,
  onToken: (token) => tell({type: "session", user_id: state.ME, csrf_token: token}),
});
stats.link = link.stats;
const api = (method, path, params, o) => { stats.api++; return link.api(method, path, params, o); };

/* what each event type does: host.js names the handler for every type the page registers for (on() is called there,
   at load, after this module has run) */
const handlers = new Map();
function on(type, fn) { handlers.set(type, fn); }
function handle(ev, at) {
  stats.events++; if (ev.type === "agent_run" || ev.type === "update_message") { stats.log.push([Math.round(at), ev.type === "agent_run" ? `run:${ev.event_type}:${ev.status}` : `edit:${(ev.rendered_content || "").length}`]); if (stats.log.length > 600) stats.log.shift(); }
  try { const fn = handlers.get(ev.type); if (fn) fn(ev, at); }
  catch (e) { console.error("event", ev.type, e); }
}

/* at load: register (waiting out a server that is restarting), then say whose session this tab holds */
async function register() { const r = await link.connect(); tell({type: "session", user_id: state.ME, csrf_token: link.csrf}); return r; }
function applyRegister(r) {
  state.skew = r.server_timestamp * 1000 - Date.now(); state.ME = r.user_id; state.meName = r.full_name; state.presences = r.presences || {};
  people.clear(); for (const u of [...(r.realm_users || []), ...(r.cross_realm_bots || [])]) people.set(u.user_id, {id: u.user_id, name: u.full_name, email: u.email, bot: u.is_bot, active: u.is_active !== false, owner: u.bot_owner_id ?? null});
  armadaBots.clear(); (r.agent_user_ids || []).forEach((i) => armadaBots.add(i)); state.muted = new Set((r.muted_users || []).map((u) => u.id));
  /* the realm bundle ("realm" above): its rules for editing and deleting; the groups those rules name ("realm_user_groups") */
  state.realm = {allowEdit: r.realm_allow_message_editing !== false, editLimit: r.realm_message_content_edit_limit_seconds ?? null, deleteLimit: r.realm_message_content_delete_limit_seconds ?? null,
    deleteAny: r.realm_can_delete_any_message_group ?? null, deleteOwn: r.realm_can_delete_own_message_group ?? null, guest: !!r.is_guest};
  groups.clear(); for (const g of r.realm_user_groups || []) groups.set(g.id, {members: new Set(g.members), subgroups: new Set(g.direct_subgroup_ids)});
  streams.clear(); for (const s of r.subscriptions || []) if (!s.is_archived) streams.set(s.stream_id, s);
  repoStreams.clear(); (r.repository_stream_ids || []).forEach((i) => streams.has(i) && repoStreams.add(i));
  state.dms = (r.recent_private_conversations || []).slice(0, 12);
  unread.clear(); const u = r.unread_msgs || {}; for (const s of u.streams || []) unread.set(tkey(s.stream_id, s.topic), new Set(s.unread_message_ids)); for (const d of u.pms || []) unread.set(dkey([d.other_user_id]), new Set(d.unread_message_ids)); for (const d of u.huddles || []) unread.set(dkey(d.user_ids_string.split(",").map(Number).filter((i) => i !== state.ME)), new Set(d.unread_message_ids));
}
/* the queue was lost and registered again: what the page holds may have missed events, so every place is asked again
   (the open one now, the others when opened) and the team's partners with it. R5 makes this a diff in place. */
function resync() { resetStreams();
  for (const c of places.values()) { c.loaded = false; c.msgs = c.msgs.filter((m) => m.local); c.byId = new Map(c.msgs.map((m) => [m.id, m])); c.oldest = false; c.park = null; c.drawn = false; c.dirty.clear(); }
  if (state.place) openPlace(state.place, {force: true, keepHash: true});
  fetchPartners(false);
}
/* the queue, for as long as the session stands */
function poll() { return link.run(); }

/* the standing label says so only after 5 s of trouble (a graceful restart takes 2 to 3); signed out and signed in as
   someone else are a sheet over the page, which keeps the page, the field and what it holds */
function drawStatus(s) {
  const l = $("#lab"); if (l) { const down = s === "reconnecting" || s === "offline"; l.classList.toggle("down", down); $("#lab-t").textContent = s === "reconnecting" ? "Beta · reconnecting" : s === "offline" ? "Beta · offline" : "Beta"; }
  if (s === "signed_out") { tell({type: "signed_out"}); sheet('<p>You are signed out. <a href="/login/" target="_blank">Sign in again</a></p>'); }
  else if (s === "other_user") { const who = people.get(link.otherUserId); sheet(`<p>Signed in as ${esc(who ? who.name : "someone else")} in another tab. <a href="/">Switch to that account</a></p>`); }
  else unsheet();
}
function sheet(html) { palette(false); closePicker(); closeMenu(); overHide(); unsheet();
  const b = el("div", "", html); b.id = "boot"; b.dataset.connection = "1"; b.setAttribute("role", "alert"); document.body.append(b); $("a", b).focus(); }
function unsheet() { const b = $("#boot[data-connection]"); if (b) b.remove(); }
const signedOut = () => link.signedOut();
/* signing out here: the other tabs hear it first, then the session ends */
function signOut() { tell({type: "signed_out"}); const f = el("form"); f.method = "POST"; f.action = "/accounts/logout/"; f.hidden = true;
  const t = el("input"); t.type = "hidden"; t.name = "csrfmiddlewaretoken"; t.value = link.csrf; f.append(t); document.body.append(f); f.submit(); }
/* GET armada/session (contract C1): whose session this is, its CSRF token, and the agent's settings */
const session = () => link.session();

/* a server running new code: this page reloads when nobody would see it (hidden), or once it has been idle a minute
   with no live run or selection in view; never while the field holds words or files, or a send is on its way, which a
   reload would lose (nothing restores them yet). A restart of the same code needs nothing: the queue resumes. */
const RELOAD_IDLE_MS = 60000;
let lastInput = performance.now(), reloadDue = false;
function onRestart(ev) { if (!link.isNewBuild(ev) || reloadDue) return; reloadDue = true; reloadWhenUnseen(); }
function reloadWhenUnseen() {
  if (!reloadDue) return;
  const unsaved = (tx && tx.textContent.trim()) || state.atts.length || [...places.values()].some((c) => c.msgs.some((m) => m.pending));
  const inView = (state.P && liveRun(state.P)) || !getSelection().isCollapsed;
  if (!unsaved && (document.hidden || (!inView && performance.now() - lastInput >= RELOAD_IDLE_MS))) { location.reload(); return; }
  setTimeout(reloadWhenUnseen, 10000);
}

/* the signs that the network or the person is back; a machine that slept shows as a timer that ran late. A hidden tab's
   timers run late by design (Chrome wakes them once a minute), so there a late timer says nothing, and the clock starts
   again when the tab is seen */
let tickAt = Date.now();
function listen() {
  addEventListener("online", () => link.wake("online"));
  addEventListener("pageshow", (e) => { if (e.persisted) link.wake("restored"); });
  addEventListener("focus", () => link.wake("focus"));
  document.addEventListener("visibilitychange", () => { if (document.hidden) { reloadWhenUnseen(); return; } tickAt = Date.now(); link.wake("visible"); });
  for (const t of ["keydown", "pointerdown"]) addEventListener(t, () => { lastInput = performance.now(); }, {capture: true, passive: true});
  setInterval(() => { const t = Date.now(); if (t - tickAt > 30000 && !document.hidden) link.wake("resumed"); tickAt = t; }, 10000);
  /* the page going for good (not into the back-forward cache, from which it may come back to its queue) */
  addEventListener("pagehide", (e) => { if (!e.persisted) link.leave(); });
  /* a tab stopped before its first register has no page to resume (and no person to compare): another tab's session
     loads it again */
  if (tabs) tabs.onmessage = (e) => { const msg = e.data || {}; if (link.stopped && !state.ME && msg.type === "session") { location.reload(); return; } link.heard(msg); };
}
listen();

export {api, handle, on, onRestart, poll, register, session, signOut, signedOut};
