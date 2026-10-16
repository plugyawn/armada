/* INTERIM BETA (README.md): Later and Send when (BRIEF statement 41; design/r5/MICRO-FEATURES.md). Later puts a message off:
   it comes back in an hour, tomorrow, when the run in its topic ends, or is just kept, and Home lists what is put off. Send
   when holds a message of mine until a time or until the run here ends, as a quiet pending row at the conversation's foot.
   The server's own reminders, stars and scheduled messages hold them all; a run's end is Armada's (zerver/lib/armada_later.py,
   agent_run_id). A reminder comes back as Zulip's does, Notification Bot's direct message, and stands at the top of Home until
   it is read. */

import {$, $$, el, esc, mid} from "./util.js";
import {msgIndex, people, state, unread} from "./state.js";
import {hhmm, now} from "./time.js";
import {api} from "./connection.js";
import {dkey, placeFromKey, placeOfMsg, tkey, topicLabel} from "./routing.js";
import {feed, go, main, sh, tx} from "./frame.js";
import {faceOf, nameOf} from "./faces.js";
import {echoHTML, partEl, partsOf, plainOf, tpl} from "./parts.js";
import {drawAtts, note, ready, syncField} from "./composer.js";
import {liveRun} from "./runs.js";
import {pin} from "./feed.js";
import {caretAtEnd, editing, endEdit, openEdit} from "./hover.js";
import {fillFromWire, takeReply} from "./mentions.js";
import {wireOf} from "./mention_text.js";
import {drawHomeRow} from "./home.js";
import {drawTrail} from "./nav.js";

/* ---------- what the server holds for me ---------- */
const held = new Map();                                    /* scheduled message id -> the server's scheduled message: Send when, not sent yet */
const reminders = new Map();                               /* reminder id -> the server's reminder: Later, with a time or a run */
const kept = new Set();                                    /* starred message ids: Later's "Just keep it" */
const laterMsgs = new Map();                               /* message id -> message, for the Later list's rows */
const back = new Map();                                    /* Notification Bot's message id -> {id, at, target, quote}: a reminder that came back */
const notBack = new Set();                                 /* Notification Bot's message ids that are no reminder: not asked for again */
const dropping = new Set();                                /* reminder ids I deleted myself: their removal is not a return */
let localSeq = 0;
function applyLater(r) {
  held.clear(); for (const s of r.scheduled_messages || []) held.set(s.scheduled_message_id, s);
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
function onScheduled(ev) {
  if (ev.op === "add") for (const s of ev.scheduled_messages) { for (const [k, x] of held) if (x.local && x.content === s.content && placeKey(x) === placeKey(s)) held.delete(k); held.set(s.scheduled_message_id, s); }   /* the server's copy takes my row's place */
  else if (ev.op === "update") held.set(ev.scheduled_message.scheduled_message_id, ev.scheduled_message);
  else if (ev.op === "remove") held.delete(ev.scheduled_message_id);
  drawPending(); }
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
/* what a held message or a reminder waits for, said in one line */
const condOf = (x) => (x.failed ? "Not sent" : x.agent_run_id ? RUN_ENDS : x.local ? x.cond : x.scheduled_delivery_timestamp * 1000 <= now() ? "Sending" : whenOf(x.scheduled_delivery_timestamp));

/* ---------- a small menu of choices: the times of Later and of Send when ---------- */
let choices = null;
/* items: [{k, label, hint}]; at: the rectangle it stands below, or above with {above: true}, its left edge on at's left, or
   its right edge on at's right with {end: true}. The keyboard stays where it was: the arrows move the highlight, Return
   chooses, Esc closes; a press outside closes it. */
function openChoices(items, at, pick, o = {}) { closeChoices();
  const box = el("div", "menu raised float later-menu", items.map((x) => `<button class="mi" data-choice="${esc(x.k)}" tabindex="-1">${esc(x.label)}<span class="k">${esc(x.hint || "")}</span></button>`).join("")); box.setAttribute("role", "menu"); box.setAttribute("aria-label", o.label || "When");
  const mr = main.getBoundingClientRect(); box.style.cssText = "position:absolute;left:0;top:0"; main.append(box); host(box);
  box.style.left = Math.round((o.end ? at.right - box.offsetWidth : at.left) - mr.left) + "px";
  box.style.top = Math.round(o.above ? at.top - mr.top - box.offsetHeight - 4 : at.bottom - mr.top + 4) + "px";
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
  M.release(box, {y: o.above ? 4 : -4}, "touch"); }
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

/* ---------- Send when: hold what the field holds until a time or the run here ends ---------- */
const placeKey = (s) => (s.type === "stream" ? tkey(s.to, s.topic) : dkey(s.to.filter((i) => i !== state.ME)));
let pend = null;
function pendBox() { if (!pend || !pend.isConnected) { pend = el("div", "pend"); pend.id = "pend"; $(".clip", feed).append(pend); } return pend; }
/* the menu on Send: a long press, a right press, or Option-Return in the field; Return alone sends at once */
function openSendWhen() { if (!state.place || (state.place.kind !== "topic" && state.place.kind !== "dm") || editing || state.threadTx) return false;
  const r = state.place.kind === "topic" ? liveRun(state.P) : null, items = [{k: "hour", label: "In 1 hour", hint: hhmm(inAnHour())}, {k: "tomorrow", label: "Tomorrow 9:00"}];
  if (r) items.push({k: "run", label: "When this run ends"});
  openChoices(items, go.getBoundingClientRect(), (k) => sendWhen(k, r), {above: true, end: true, label: "Send when"}); return true; }
function sendWhen(k, r) { const p = state.place;
  if (state.atts.some((a) => !a.url && !a.failed)) { note("Still uploading"); M.refuse(sh); return; }
  const files = state.atts.filter((a) => a.url), typed = wireOf(tx, state.agentMention), body = [typed, ...files.map((a) => `[${a.name}](${a.url})`)].filter(Boolean).join("\n");
  if (!body) { M.refuse(sh); S.play("nope"); ready(); return; }
  const text = takeReply() + body, ts = k === "hour" ? inAnHour() : k === "tomorrow" ? tomorrowAt9() : null, to = p.kind === "topic" ? {type: "stream", to: p.stream_id, topic: p.topic} : {type: "direct", to: p.ids.length ? p.ids : [state.ME]};
  const local = {scheduled_message_id: "L" + ++localSeq, local: true, content: text, rendered_content: echoHTML(text), cond: k === "run" ? RUN_ENDS : whenOf(ts), ...to};
  held.set(local.scheduled_message_id, local); tx.textContent = ""; state.atts = []; drawAtts(); syncField(); go.classList.remove("armed"); drawPending({appear: local.scheduled_message_id}); S.play("send"); ready();
  api("POST", "scheduled_messages", {...to, content: text, read_by_sender: true, ...(k === "run" ? {agent_run_id: r.id} : {scheduled_delivery_timestamp: ts})}).then(
    (res) => { if (held.has(local.scheduled_message_id) && !held.has(res.scheduled_message_id)) { held.delete(local.scheduled_message_id); held.set(res.scheduled_message_id, {...local, local: false, scheduled_message_id: res.scheduled_message_id, ...(k === "run" ? {agent_run_id: r.id} : {scheduled_delivery_timestamp: ts}), failed: false}); } else held.delete(local.scheduled_message_id); drawPending(); },
    (e) => { held.delete(local.scheduled_message_id); drawPending(); giveBack(text); note("Not held: " + e.message); M.refuse(sh); S.play("nope"); }); }
/* words given back to the field: after what it holds, on a line of their own */
function giveBack(text) { const was = !!tx.textContent.trim(); if (was) tx.append(document.createTextNode("\n")); const box = el("div"); fillFromWire(box, text); tx.append(...box.childNodes); syncField(); go.classList.add("armed"); ready(); caretAtEnd(); }

/* the pending rows: my bubbles, half their colour, each with a line under it (as a thread's line stands) saying what it
   waits for, with Edit and Cancel; at the conversation's foot above the band (sticky, beta.css), in the order they go */
function drawPending(o = {}) { const box = pendBox(), p = state.place;
  const rows = p && (p.kind === "topic" || p.kind === "dm") ? [...held.values()].filter((s) => placeKey(s) === p.key).sort((a, b) => (a.agent_run_id ? 1 : 0) - (b.agent_run_id ? 1 : 0) || (a.scheduled_delivery_timestamp || 0) - (b.scheduled_delivery_timestamp || 0)) : [];
  const sig = JSON.stringify(rows.map((s) => [s.scheduled_message_id, s.rendered_content, condOf(s)])); if (box._sig === sig) return; box._sig = sig;
  box.textContent = ""; for (const s of rows) { const cl = el("section", "cl me"); cl.dataset.sched = s.scheduled_message_id;
    cl.append(...partsOf({content: s.rendered_content, sender_id: state.ME}).map((x) => partEl(x, {id: "S" + s.scheduled_message_id, sender_id: state.ME})));
    $$(".b", cl).forEach((b) => b.classList.add("unsent"));   /* half its colour, as a message on its way is */
    cl.append(el("div", "tl pc", `<span class="sn${s.failed ? " bad" : ""}">${esc(condOf(s))}</span>` + (s.local ? "" : `<button class="lk" data-pend-edit="${s.scheduled_message_id}">Edit</button><button class="lk" data-pend-cancel="${s.scheduled_message_id}">Cancel</button>`)));
    box.append(cl); if (o.appear === s.scheduled_message_id) M.appear(cl, 0.6); }
  host(box); if (state.pinned) pin(); }
/* Edit: its words go to the field as an edit does (hover.js), and Return saves them into the same held message */
function editPending(id) { const s = held.get(+id); if (!s) return; if (s.failed) { cancelPending(id); return; }   /* one the server could not send waits for nothing: its words come back to be sent again */
  if (editing) endEdit(); state.threadTx = null; openEdit({id: "S" + s.scheduled_message_id, scheduled: s}, s.content); ready(); caretAtEnd(); }
async function savePending(text, at) { const {m} = editing, s = m.scheduled; if (!text) { endEdit(); cancelPending(s.scheduled_message_id); return; }   /* no words: it is cancelled */
  const raw = takeReply() + text, was = {content: s.content, rendered_content: s.rendered_content}; endEdit(); Object.assign(s, {content: raw, rendered_content: echoHTML(raw)}); drawPending(); S.play("send", 0, at); ready();
  try { await api("PATCH", `scheduled_messages/${s.scheduled_message_id}`, {content: raw}); }
  catch (e) { if (s.content === raw) { Object.assign(s, was); drawPending(); } openEdit(m, raw); caretAtEnd(); note("Not saved: " + e.message); M.refuse(sh); S.play("nope"); } }
/* Cancel: it is not sent, and its words come back to the field, so nothing is lost */
async function cancelPending(id) { const s = held.get(+id); if (!s) return; held.delete(s.scheduled_message_id); drawPending(); giveBack(s.content);
  try { await api("DELETE", `scheduled_messages/${s.scheduled_message_id}`); } catch (e) { held.set(s.scheduled_message_id, s); drawPending(); note("Not cancelled: " + e.message); } }

/* ---------- wiring, once at boot (host.js) ---------- */
/* a press of my own, in the capture phase: with stop, nothing else takes it (Home's row under a Done does not open its place) */
function grab(root, sel, fn, stop) { const run = (e) => { const t = e.target.closest(sel); if (!t || !root.contains(t)) return; if (stop) { e.stopImmediatePropagation(); e.preventDefault(); } fn(t); };
  root.addEventListener("pointerdown", (e) => { if (e.button === 0) run(e); }, true); root.addEventListener("click", (e) => { if (e.detail === 0) run(e); }, true); }
function wireLater() {
  const home = $("#home");
  grab(home, "[data-later-done]", (b) => { done(+b.dataset.laterDone).catch((e) => note(e.message)).finally(drawHomeRow); }, true);
  grab(home, "[data-later-seen]", (b) => { markSeen(+b.dataset.laterSeen); drawHomeRow(); }, true);
  grab(home, ".hrow[data-back]", (r) => markSeen(+r.dataset.back), false);   /* opening what came back reads it; the row's own press opens its place */
  grab(feed, "[data-pend-edit]", (b) => editPending(b.dataset.pendEdit), true);
  grab(feed, "[data-pend-cancel]", (b) => cancelPending(b.dataset.pendCancel), true);
  /* Send when on the Send button: held for half a second, or a right press; the click that ends a hold does not send */
  let timer = 0, opened = false;
  go.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; opened = false; clearTimeout(timer); timer = setTimeout(() => { opened = openSendWhen(); }, 450); });
  for (const t of ["pointerup", "pointerleave", "pointercancel"]) go.addEventListener(t, () => clearTimeout(timer));
  go.addEventListener("click", (e) => { if (opened) { opened = false; e.stopImmediatePropagation(); e.preventDefault(); } }, true);
  go.addEventListener("contextmenu", (e) => { e.preventDefault(); openSendWhen(); }); }

export {applyLater, backHTML, choices, closeChoices, drawPending, held, kept, laterHTML, loadLater, onReminders, onScheduled, onStarred,
  openLater, openSendWhen, reminders, savePending, wireLater};
