/* INTERIM BETA (README.md): Later (BRIEF statement 41; design/r5/MICRO-FEATURES.md). Later puts a message off: it comes
   back in an hour, tomorrow, when the run in its topic ends, or is just kept, and Home lists what is put off. The server's own
   reminders and stars hold them; a run's end is Armada's (zerver/lib/armada_later.py, agent_run_id). A reminder comes back as
   Zulip's does, Notification Bot's direct message, and stands at the top of Home until it is read. */

import {$, $$, el, esc, mid} from "./util.js";
import {msgIndex, people, unread} from "./state.js";
import {hhmm, now} from "./time.js";
import {api} from "./connection.js";
import {dkey, placeFromKey, placeOfMsg, topicLabel} from "./routing.js";
import {main, sh} from "./frame.js";
import {faceOf, nameOf} from "./faces.js";
import {plainOf, tpl} from "./parts.js";
import {note, ready} from "./composer.js";
import {liveRun} from "./runs.js";
import {drawHomeRow} from "./home.js";
import {drawTrail} from "./nav.js";

/* ---------- what the server holds for me ---------- */
const reminders = new Map();                               /* reminder id -> the server's reminder: Later, with a time or a run */
const kept = new Set();                                    /* starred message ids: Later's "Just keep it" */
const laterMsgs = new Map();                               /* message id -> message, for the Later list's rows */
const back = new Map();                                    /* Notification Bot's message id -> {id, at, target, quote}: a reminder that came back */
const notBack = new Set();                                 /* Notification Bot's message ids that are no reminder: not asked for again */
const dropping = new Set();                                /* reminder ids I deleted myself: their removal is not a return */
function applyLater(r) {
  reminders.clear(); for (const x of r.reminders || []) reminders.set(x.reminder_id, x);
  kept.clear(); for (const id of r.starred_messages || []) kept.add(id); }
const notificationBot = () => { for (const p of people.values()) if (p.bot && /^notification-bot@/.test(p.email)) return p.id; return null; };
const msgOf = (id) => laterMsgs.get(id) || (msgIndex.get(id) && msgIndex.get(id).byId.get(id)) || null;
/* the messages the Later list and Home's returned rows name, asked for by id once each (GET messages?message_ids) */
async function fetchMsgs(ids) { const want = [...new Set(ids)].filter((id) => typeof id === "number" && !laterMsgs.has(id)); if (!want.length) return;
  try { const r = await api("GET", "messages", {message_ids: want, apply_markdown: true, client_gravatar: false}); for (const m of r.messages) laterMsgs.set(m.id, m); } catch (e) {} }
/* at load: the rows of the Later list, and the reminders that came back while I was away (unread) */
async function loadLater() { await Promise.all([fetchMsgs([...[...reminders.values()].map((x) => x.reminder_target_message_id), ...kept]), scanBack()]); drawHomeRow(); }
/* a reminder that came back is Notification Bot's direct message to me, quoting the message with a link to it (Zulip's
   get_reminder_formatted_content); it stands at the top of Home while it is unread */
async function scanBack() { const nb = notificationBot(); if (!nb) return; const ids = [...(unread.get(dkey([nb])) || [])].filter((id) => typeof id === "number" && !back.has(id) && !notBack.has(id)); if (!ids.length) return;
  let r; try { r = await api("GET", "messages", {message_ids: ids, apply_markdown: true, client_gravatar: false}); } catch (e) { return; }
  const targets = []; for (const m of r.messages) { tpl.innerHTML = m.content || ""; const a = [...tpl.content.querySelectorAll("a[href]")].find((x) => /\/near\/\d+/.test(x.getAttribute("href"))); if (!a) { notBack.add(m.id); continue; }
    const target = +/\/near\/(\d+)/.exec(a.getAttribute("href"))[1], q = tpl.content.querySelector("blockquote"); back.set(m.id, {id: m.id, at: m.timestamp, target, quote: q ? q.textContent.trim() : ""}); targets.push(target); }
  await fetchMsgs(targets); }

/* ---------- live: the server's events ---------- */
function onReminders(ev) {
  if (ev.op === "add") { for (const x of ev.reminders) reminders.set(x.reminder_id, x); fetchMsgs(ev.reminders.map((x) => x.reminder_target_message_id)).then(drawHomeRow); }
  else if (ev.op === "remove") { reminders.delete(ev.reminder_id); if (dropping.delete(ev.reminder_id)) drawHomeRow(); else scanBack().then(drawHomeRow); }   /* not deleted by me: it came back */
  drawHomeRow(); }
function onStarred(ev) { for (const id of ev.messages) if (ev.op === "add") kept.add(id); else kept.delete(id); if (ev.op === "add") fetchMsgs(ev.messages).then(drawHomeRow); drawHomeRow(); }

/* ---------- when: the times the menus offer, and how a time is said ---------- */
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const inAnHour = () => Math.floor(now() / 1000) + 3600;
const tomorrowAt9 = () => { const d = new Date(now()); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); return Math.floor(d.getTime() / 1000); };
/* a time to come: its hour today, Tomorrow and its hour, the weekday within a week, else the date */
function whenOf(ts) { const d = new Date(ts * 1000), t = new Date(now()), days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - new Date(t.getFullYear(), t.getMonth(), t.getDate())) / 864e5);
  return days <= 0 ? hhmm(ts) : days === 1 ? `Tomorrow ${hhmm(ts)}` : days < 7 ? `${DOW[d.getDay()]} ${hhmm(ts)}` : `${d.getDate()} ${MON[d.getMonth()]} ${hhmm(ts)}`; }
const RUN_ENDS = "When the run ends";
/* what a reminder waits for, said in one line */
const condOf = (x) => (x.failed ? "Not sent" : x.agent_run_id ? RUN_ENDS : x.scheduled_delivery_timestamp * 1000 <= now() ? "Sending" : whenOf(x.scheduled_delivery_timestamp));

/* ---------- a small menu of choices: Later's times ---------- */
let choices = null;
/* items: [{k, label, hint}]; at: the rectangle it stands below, its left edge on at's left. The keyboard stays where it was:
   the arrows move the highlight, Return chooses, Esc closes; a press outside closes it. */
function openChoices(items, at, pick, o = {}) { closeChoices();
  const box = el("div", "menu raised float later-menu", items.map((x) => `<button class="mi" data-choice="${esc(x.k)}" tabindex="-1">${esc(x.label)}<span class="k">${esc(x.hint || "")}</span></button>`).join("")); box.setAttribute("role", "menu"); box.setAttribute("aria-label", o.label || "When");
  const mr = main.getBoundingClientRect(); box.style.cssText = "position:absolute;left:0;top:0"; main.append(box); host(box);
  box.style.left = Math.round(at.left - mr.left) + "px"; box.style.top = Math.round(at.bottom - mr.top + 4) + "px";
  let hi = 0; const mark = () => $$(".mi", box).forEach((x, i) => x.classList.toggle("hl", i === hi)); mark();
  const choose = (k) => { closeChoices(); pick(k); };
  const key = (e) => { const d = {ArrowDown: 1, ArrowUp: -1}[e.key];
    if (d) { hi = (hi + d + items.length) % items.length; mark(); } else if (e.key === "Enter") choose(items[hi].k); else if (e.key === "Escape") { closeChoices(); ready(); } else return;
    e.preventDefault(); e.stopPropagation(); };
  const outside = (e) => { if (!box.contains(e.target)) closeChoices(); };
  box.addEventListener("pointerdown", (e) => { const b = e.target.closest("[data-choice]"); if (b && e.button === 0) { e.preventDefault(); choose(b.dataset.choice); } });
  box.addEventListener("click", (e) => { const b = e.target.closest("[data-choice]"); if (b && e.detail === 0) choose(b.dataset.choice); });   /* a click with no press: assistive technology */
  addEventListener("keydown", key, true); addEventListener("pointerdown", outside, true);
  choices = {box, close: () => { removeEventListener("keydown", key, true); removeEventListener("pointerdown", outside, true); }};
  M.release(box, {y: -4}, "touch"); }
function closeChoices() { if (!choices) return; const {box, close} = choices; choices = null; close(); box.inert = true; M.leave(box, () => box.remove()); }

/* ---------- Later: a message put off, from its More menu ---------- */
const remindersOf = (id) => [...reminders.values()].filter((x) => x.reminder_target_message_id === id);
const onLater = (id) => kept.has(id) || remindersOf(id).length > 0;
/* the times, and When this run ends only while a run is live in the message's own topic */
function openLater(m, at) { const c = msgIndex.get(m.id), r = c && c.place.kind === "topic" ? liveRun(c) : null;
  const items = [{k: "hour", label: "In 1 hour", hint: hhmm(inAnHour())}, {k: "tomorrow", label: "Tomorrow", hint: whenOf(tomorrowAt9()).replace("Tomorrow ", "")}];
  if (r) items.push({k: "run", label: "When this run ends"}); items.push({k: "keep", label: "Just keep it"}); if (onLater(m.id)) items.push({k: "done", label: "Done with it"});
  openChoices(items, at, (k) => putOff(m, k, r), {label: "Later"}); }
async function putOff(m, k, r) { laterMsgs.set(m.id, m); ready();
  try {
    if (k === "keep") { await api("POST", "messages/flags", {messages: [m.id], op: "add", flag: "starred"}); note("Kept for later"); }
    else if (k === "done") await done(m.id);
    else { const ts = k === "hour" ? inAnHour() : tomorrowAt9(); await api("POST", "reminders", k === "run" ? {message_id: m.id, agent_run_id: r.id} : {message_id: m.id, scheduled_delivery_timestamp: ts}); note(k === "run" ? "Back when the run ends" : `Back ${whenOf(ts)}`); }
  } catch (e) { note("Not put off: " + e.message); M.refuse(sh); S.play("nope"); }
  drawHomeRow(); }
/* done with a message put off: its reminders are deleted and its star taken off */
async function done(id) { const xs = remindersOf(id); xs.forEach((x) => dropping.add(x.reminder_id));
  await Promise.all([...xs.map((x) => api("DELETE", `reminders/${x.reminder_id}`)), kept.has(id) ? api("POST", "messages/flags", {messages: [id], op: "remove", flag: "starred"}) : null]); }

/* ---------- Home: what is put off, and what came back ---------- */
const whereOf = (m) => { const k = placeOfMsg(m), p = placeFromKey(k); return {k, where: !p ? "" : p.kind === "topic" ? `${p.stream} / ${topicLabel(p.topic)}` : p.ids.length ? `Direct message with ${p.ids.map((i) => nameOf(i).split(/[ ,]/)[0]).join(", ")}` : "A note to yourself"}; };
const firstLine = (m) => mid((plainOf(m.content).trim().split("\n")[0] || "").trim(), 120) || "A message";
/* one row per message put off: when it comes back first, a time before a run, and kept last */
function laterRows() { const ids = new Set([...[...reminders.values()].map((x) => x.reminder_target_message_id), ...kept]), rows = [];
  for (const id of ids) { const m = msgOf(id); if (!m) continue; const xs = remindersOf(id).sort((a, b) => a.scheduled_delivery_timestamp - b.scheduled_delivery_timestamp), x = xs[0];
    rows.push({id, m, ...whereOf(m), when: x ? condOf(x) : "Kept", order: x ? x.scheduled_delivery_timestamp : Infinity}); }
  return rows.sort((a, b) => a.order - b.order || b.id - a.id); }
function backRows() { const nb = notificationBot(), set = nb && unread.get(dkey([nb])), rows = [];
  for (const b of back.values()) { if (!set || !set.has(b.id)) continue; const m = msgOf(b.target); rows.push({...b, ...(m ? whereOf(m) : {k: dkey([nb]), where: "Reminder"}), text: m ? firstLine(m) : mid(b.quote, 120) || "A reminder", face: m ? faceOf(m.sender_id) : ""}); }
  return rows.sort((a, b) => b.at - a.at); }
const rowHTML = (r, trail, attr) => `<div class="hrow later-row" role="button" tabindex="0" data-k="${esc(r.k)}" ${attr}><span class="g">${r.face}</span><span class="tx"><p>${esc(r.text)}</p><span class="wh">${esc(r.where)}</span></span><span class="v">${trail}</span></div>`;
/* the rows that came back, for the top of Home */
const backHTML = () => backRows().map((r) => rowHTML(r, `<time>Back ${esc(hhmm(r.at))}</time><button class="lk" data-later-seen="${r.id}">Done</button>`, `data-back="${r.id}"`)).join("");
/* the Later list, last on Home: quiet, the message's first line, where, and when it comes back */
function laterHTML() { const rows = laterRows(); if (!rows.length) return "";
  return '<div class="gh">Later</div>' + rows.map((r) => rowHTML({...r, text: firstLine(r.m), face: faceOf(r.m.sender_id)}, `<time>${esc(r.when)}</time><button class="lk" data-later-done="${r.id}">Done</button>`, `data-later="${r.id}"`)).join(""); }
const markSeen = (id) => { const nb = notificationBot(), k = nb && dkey([nb]), set = k && unread.get(k); if (set && set.delete(id)) { if (!set.size) unread.delete(k); drawTrail(k); }
  api("POST", "messages/flags", {messages: [id], op: "add", flag: "read"}).catch(() => {}); };

/* ---------- wiring, once at boot (host.js) ---------- */
/* a press of my own, in the capture phase: with stop, nothing else takes it (Home's row under a Done does not open its place) */
function grab(root, sel, fn, stop) { const run = (e) => { const t = e.target.closest(sel); if (!t || !root.contains(t)) return; if (stop) { e.stopImmediatePropagation(); e.preventDefault(); } fn(t); };
  root.addEventListener("pointerdown", (e) => { if (e.button === 0) run(e); }, true); root.addEventListener("click", (e) => { if (e.detail === 0) run(e); }, true); }
function wireLater() {
  const home = $("#home");
  grab(home, "[data-later-done]", (b) => { done(+b.dataset.laterDone).catch((e) => note(e.message)).finally(drawHomeRow); }, true);
  grab(home, "[data-later-seen]", (b) => { markSeen(+b.dataset.laterSeen); drawHomeRow(); }, true);
  grab(home, ".hrow[data-back]", (r) => markSeen(+r.dataset.back), false); }   /* opening what came back reads it; the row's own press opens its place */

export {applyLater, backHTML, choices, closeChoices, kept, laterHTML, loadLater, onReminders, onStarred, openLater, reminders,
  wireLater};
