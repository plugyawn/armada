/* INTERIM BETA (README.md): the connection: requests, registering, the event queue and its dispatch, signing out. */

import {$, el, sleep} from "./util.js";
import {armadaBots, people, places, repoStreams, state, stats, streams, unread} from "./state.js";
import {dkey, openPlace, refreshCtx, tkey} from "./routing.js";
import {onDelete, onFlags, onMessage, onReaction, onRun, onTyping, onUpdate} from "./events.js";
import {closeMenu, overHide} from "./hover.js";
import {closePicker} from "./model_picker.js";
import {palette} from "./palette.js";
import {partnersFirst} from "./partners.js";

/* ---------- the server ---------- */
const CSRF = ($('input[name="csrfmiddlewaretoken"]') || {}).value || "";   /* the session's token, written into the page by the server */
async function api(method, path, params = {}, o = {}) {
  const sp = new URLSearchParams(); for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) sp.set(k, typeof v === "string" ? v : JSON.stringify(v));
  let url = "/json/" + path, body = o.body; if (method === "GET" || method === "DELETE") { const s = sp.toString(); if (s) url += "?" + s; } else if (!body) body = sp;
  stats.api++;
  const headers = method === "GET" ? {} : {"X-CSRFToken": CSRF};
  let r; try { r = await fetch(url, {method, body, headers, credentials: "same-origin", signal: o.signal}); } catch (e) { throw Object.assign(new Error("The server cannot be reached"), {net: true}); }
  /* the server's rate limit (200 requests a minute a person): a read waits as long as the server says and asks again, up to
     three times, instead of showing "API usage exceeded rate limit" or calling the server offline. A write is not repeated. */
  if (r.status === 429 && method === "GET" && (o.tries || 0) < 3 && !(o.signal && o.signal.aborted)) { let wait = parseFloat(r.headers.get("Retry-After"));
    if (!(wait >= 0)) { try { wait = +(await r.json())["retry-after"]; } catch (e) {} } stats.rateLimited = (stats.rateLimited || 0) + 1;
    await sleep(Math.min(30, Math.max(0.5, wait || 1)) * 1000 + Math.random() * 250); return api(method, path, params, {...o, tries: (o.tries || 0) + 1}); }
  if (r.status === 401) { signedOut(); throw Object.assign(new Error("Signed out"), {status: 401}); }
  let j; try { j = await r.json(); } catch (e) { throw Object.assign(new Error(`The server answered ${r.status}`), {status: r.status, net: r.status >= 500}); }
  if (j.result !== "success") throw Object.assign(new Error(j.msg || "The request failed"), {code: j.code, status: r.status});
  return j;
}
function handle(ev, at) {
  stats.events++; if (ev.type === "agent_run" || ev.type === "update_message") { stats.log.push([Math.round(at), ev.type === "agent_run" ? `run:${ev.event_type}:${ev.status}` : `edit:${(ev.rendered_content || "").length}`]); if (stats.log.length > 600) stats.log.shift(); }
  try {
    if (ev.type === "message") onMessage(ev); else if (ev.type === "update_message") onUpdate(ev, at); else if (ev.type === "agent_run") onRun(ev); else if (ev.type === "reaction") onReaction(ev);
    else if (ev.type === "update_message_flags") onFlags(ev); else if (ev.type === "delete_message") onDelete(ev); else if (ev.type === "typing") onTyping(ev);
    else if (ev.type === "workspace_changed") { if (state.P && state.P.ctx && state.P.ctx.workspace_uuid === ev.workspace_id) refreshCtx(state.P); }
  } catch (e) { console.error("event", ev.type, e); }
}
async function poll() { let wait = 0;
  while (!signedOut.done) {
    const open = setTimeout(() => setDown(false), 3000);   /* a long-poll still open after 3 s is an answering server: without this the label said "not answering" until the next event or heartbeat */
    try { const r = await api("GET", "events", {queue_id: state.queue, last_event_id: state.lastEvent}); const at = performance.now(); clearTimeout(open); setDown(false); wait = 0; await partnersFirst(r.events); for (const ev of r.events) { state.lastEvent = Math.max(state.lastEvent, ev.id); handle(ev, at); } }
    catch (e) { clearTimeout(open); if (signedOut.done) return; if (e.code === "BAD_EVENT_QUEUE_ID") { try { await api("GET", "users/me"); await register(); for (const c of places.values()) { c.loaded = false; c.msgs = c.msgs.filter((m) => m.local); c.byId = new Map(c.msgs.map((m) => [m.id, m])); c.oldest = false; } if (state.place) openPlace(state.place, {force: true, keepHash: true}); } catch (x) {} } else { setDown(true); wait = Math.min(15000, wait ? wait * 2 : 1000); await sleep(wait); } }
  }
}
function setDown(d) { const l = $("#lab"); if (l && l.classList.contains("down") !== d) { l.classList.toggle("down", d); $("#lab-t").textContent = d ? "Beta · offline" : "Beta"; } }
/* signed out (here, in another tab, or the session ended): the page says so at once and offers sign-in; it never goes on as
   a spectator, which the realm would allow */
function signedOut() { if (signedOut.done) return; signedOut.done = true; palette(false); closePicker(); closeMenu(); overHide();
  const b = el("div", "", '<p>You are signed out. <a href="/login/?next=/beta/">Sign in again</a></p>'); b.id = "boot"; b.setAttribute("role", "alert"); document.body.append(b); $("a", b).focus(); }
/* coming back to the page asks whether the session still stands: a sign-out in another tab shows here at once, not when the
   long poll next returns */
let sessionAsked = -1e9;
function askSession() { if (signedOut.done || document.hidden || performance.now() - sessionAsked < 5000) return; sessionAsked = performance.now(); api("GET", "users/me").catch(() => {}); }
async function register() {
  const r = await api("POST", "register", {fetch_event_types: ["muted_users", "subscription", "realm_user", "presence", "update_message_flags", "message", "recent_private_conversations", "realm_bot", "stream"], event_types: ["message", "update_message", "reaction", "agent_run", "workspace_changed", "typing", "delete_message", "update_message_flags"], apply_markdown: true, client_gravatar: false,
    client_capabilities: {notification_settings_null: true, bulk_message_deletion: true, user_avatar_url_field_optional: false, stream_typing_notifications: true, user_settings_object: true, linkifier_url_template: true, user_list_incomplete: false, include_deactivated_groups: false, archived_channels: false, empty_topic_name: true}});
  if (!r.user_id || (state.ME && r.user_id !== state.ME)) { signedOut(); throw Object.assign(new Error("Signed out"), {status: 401}); }   /* no session: the server registered a spectator */
  state.queue = r.queue_id; state.lastEvent = r.last_event_id; state.skew = r.server_timestamp * 1000 - Date.now(); state.ME = r.user_id; state.meName = r.full_name; state.presences = r.presences || {};
  people.clear(); for (const u of [...(r.realm_users || []), ...(r.cross_realm_bots || [])]) people.set(u.user_id, {id: u.user_id, name: u.full_name, email: u.email, bot: u.is_bot, active: u.is_active !== false});
  armadaBots.clear(); (r.agent_user_ids || []).forEach((i) => armadaBots.add(i)); state.muted = new Set((r.muted_users || []).map((u) => u.id));
  streams.clear(); for (const s of r.subscriptions || []) if (!s.is_archived) streams.set(s.stream_id, s);
  repoStreams.clear(); (r.repository_stream_ids || []).forEach((i) => streams.has(i) && repoStreams.add(i));
  state.dms = (r.recent_private_conversations || []).slice(0, 12);
  unread.clear(); const u = r.unread_msgs || {}; for (const s of u.streams || []) unread.set(tkey(s.stream_id, s.topic), new Set(s.unread_message_ids)); for (const d of u.pms || []) unread.set(dkey([d.other_user_id]), new Set(d.unread_message_ids)); for (const d of u.huddles || []) unread.set(dkey(d.user_ids_string.split(",").map(Number).filter((i) => i !== state.ME)), new Set(d.unread_message_ids));
}

export {api, askSession, handle, poll, register, signedOut};
