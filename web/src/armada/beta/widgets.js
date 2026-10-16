/* INTERIM BETA (README.md): polls and to-do lists (Zulip's message widgets: zerver/lib/widget.py, web/src/poll_data.ts and
   todo_data.ts), in the design's language and live for everyone. A message that starts with /poll or /todo carries a
   `widget` submessage from its sender ({widget_type, extra_data}); every vote, option, task, tick and title after it is a
   submessage of its own, and the `submessage` event brings each to everyone who can see the message. The widget's state is
   those submessages replayed in order, with mine that the server has not yet confirmed on top, so what I press is whole in
   the frame of the press. Nothing moves when someone else votes or ticks: a count changes inside a cell as wide as its
   longest form, faces stand in a cell three faces wide, and rows are never reordered or removed. */

import {$, $$, el, esc, press as pressOn} from "./util.js";
import {msgIndex, state} from "./state.js";
import {api} from "./connection.js";
import {faceOf, isOnix, nameOf} from "./faces.js";
import {note} from "./composer.js";

const MAX_IDX = 1000;

/* the widget a message's text makes, as the server parses it (widget.py, get_widget_data): my echo shows it before the server
   answers, and the server's own copy then replaces it with nothing to redraw */
function widgetFromText(text) {
  const m = /^\/(poll|todo)(?=\s|$)/.exec(text || ""); if (!m) return null;
  const lines = text.slice(m[0].length).split(/\r?\n/), head = (lines.shift() || "").trim(), items = lines.map((l) => l.trim().replace(/^\s*[-*]?\s*/, "")).filter(Boolean);
  if (m[1] === "poll") return {widget_type: "poll", extra_data: {question: head, options: items}};
  return {widget_type: "todo", extra_data: {task_list_title: head, tasks: items.map((t) => { const i = t.indexOf(": "); return i < 0 ? {task: t.trim(), desc: ""} : {task: t.slice(0, i).trim(), desc: t.slice(i + 2).trim()}; })}};
}
const localSubmessages = (text) => { const w = widgetFromText(text); return w ? [{id: 0, sender_id: state.ME, msg_type: "widget", content: JSON.stringify(w)}] : []; };

/* ---------- state: the submessages replayed ---------- */
const pending = new Map();   /* message id -> my submessages the server has not confirmed yet, in the order sent */
function widgetOf(m) {
  const subs = (m.submessages || []).filter((s) => s.msg_type === "widget").sort((a, b) => a.id - b.id), events = [];
  for (const s of subs) { try { events.push({sender_id: s.sender_id, data: JSON.parse(s.content)}); } catch (e) { return null; } }
  for (const data of pending.get(m.id) || []) events.push({sender_id: state.ME, data});
  const first = events[0]; if (!first || !first.data || first.sender_id !== m.sender_id) return null;
  const type = first.data.widget_type, x = first.data.extra_data || {};
  if (type === "poll") return replayPoll(m, x, events.slice(1));
  if (type === "todo") return replayTodo(m, x, events.slice(1));
  return null;
}
function replayPoll(m, x, events) {
  const w = {type: "poll", sender: m.sender_id, question: typeof x.question === "string" ? x.question : "", options: new Map(), next: 1};
  const add = (sender, idx, text) => { if (typeof text !== "string" || !text.trim() || !(idx >= 0 && idx <= MAX_IDX) || [...w.options.values()].some((o) => o.text === text)) return;
    w.options.set(`${sender},${idx}`, {key: `${sender},${idx}`, text, votes: new Set()}); if (sender === state.ME && w.next <= idx) w.next = idx + 1; };
  (Array.isArray(x.options) ? x.options : []).forEach((o, i) => add("canned", i, o));
  for (const {sender_id, data} of events) {
    if (data.type === "new_option") add(sender_id, data.idx, data.option);
    else if (data.type === "question" && sender_id === w.sender && typeof data.question === "string") w.question = data.question;
    else if (data.type === "vote") { const o = w.options.get(data.key); if (!o) continue; if (data.vote === 1) o.votes.add(sender_id); else if (data.vote === -1) o.votes.delete(sender_id); } }
  return w;
}
function replayTodo(m, x, events) {
  const w = {type: "todo", sender: m.sender_id, title: typeof x.task_list_title === "string" ? x.task_list_title : "", tasks: new Map(), next: 1};
  const add = (sender, idx, task, desc, done) => { if (typeof task !== "string" || !task.trim() || !(Number.isInteger(idx) && idx >= 0 && idx <= MAX_IDX) || [...w.tasks.values()].some((t) => t.task === task)) return;
    w.tasks.set(`${idx},${sender}`, {key: `${idx},${sender}`, task, desc: typeof desc === "string" ? desc : "", done: !!done}); if (sender === state.ME && w.next <= idx) w.next = idx + 1; };
  (Array.isArray(x.tasks) ? x.tasks : []).forEach((t, i) => add("canned", i, t && t.task, t && t.desc, false));
  for (const {sender_id, data} of events) {
    if (data.type === "new_task") add(sender_id, data.key, data.task, data.desc, data.completed);
    else if (data.type === "new_task_list_title" && sender_id === w.sender && typeof data.title === "string") w.title = data.title;
    else if (data.type === "strike") { const t = w.tasks.get(data.key); if (t) t.done = !t.done; } }
  return w;
}

/* ---------- drawing ---------- */
/* a poll: the question in Strong with who has voted in a caption; an option a row, its box ticked when it is mine, the faces of
   who chose it and their count at its end; then Add an option. A to-do list: its title with how many are done, a task a row
   with its box, its words and what it means in the second ink; then Add a task. The author can change the question or title. */
const facesHTML = (ids) => `<span class="wf" aria-hidden="true">${[...ids].slice(0, 3).map((i) => (isOnix(i) ? agentFace(18) : faceOf(i, "s18"))).join("")}</span>`;   /* Onix votes as itself: its mark */
function widgetHTML(m, w) {
  const mine = w.sender === state.ME;
  if (w.type === "poll") { const voters = new Set(); for (const o of w.options.values()) for (const v of o.votes) voters.add(v);
    const q = w.question || (mine ? "" : "Poll");
    return `<div class="wh">${mine ? `<button class="wq" data-w="title" aria-label="Change the question">${esc(q) || '<span class="ph">Add a question</span>'}</button>` : `<span class="wq">${esc(q)}</span>`}<span class="wc">${voters.size ? `${voters.size} voted` : "No votes yet"}</span></div>`
      + `<div class="wl" role="group" aria-label="${esc(w.question || "Poll")}">${[...w.options.values()].map((o) => rowHTML(o.key, o.votes.has(state.ME), esc(o.text), "", facesHTML(o.votes) + `<span class="wn">${o.votes.size || ""}</span>`, `${o.text}: ${o.votes.size} ${o.votes.size === 1 ? "vote" : "votes"}${o.votes.size ? ` (${[...o.votes].map(nameOf).join(", ")})` : ""}`, "vote")).join("")}</div>`
      + `<button class="lk wa" data-w="add">Add an option</button>`; }
  const all = [...w.tasks.values()], done = all.filter((t) => t.done).length, title = w.title || "Task list";
  return `<div class="wh">${mine ? `<button class="wq" data-w="title" aria-label="Change the title">${esc(title)}</button>` : `<span class="wq">${esc(title)}</span>`}<span class="wc">${all.length ? `${done} of ${all.length} done` : "No tasks yet"}</span></div>`
    + `<div class="wl" role="group" aria-label="${esc(title)}">${all.map((t) => rowHTML(t.key, t.done, esc(t.task), t.desc ? esc(t.desc) : "", "", `${t.task}${t.desc ? ": " + t.desc : ""}`, "tick")).join("")}</div>`
    + `<button class="lk wa" data-w="add">Add a task</button>`;
}
function rowHTML(key, on, text, desc, end, label, act) {
  return `<button class="wr" data-key="${esc(key)}" data-w="${act}" role="${act === "tick" ? "checkbox" : "button"}" ${act === "tick" ? `aria-checked="${on}"` : `aria-pressed="${on}"`} aria-label="${esc(label)}" data-on="${on}"><span class="check${on ? " on" : ""}">${ic("check")}</span><span class="wt">${text}${desc ? `<span class="wd">${desc}</span>` : ""}</span>${end}</button>`;
}
/* the part a widget's message is drawn as: the widget alone, in place of the words that made it */
function widgetPart(m) { const w = widgetOf(m); return w ? {t: "wdg", html: widgetHTML(m, w)} : null; }
/* someone voted, added or ticked: the drawn widget changes in place. A row is matched by its key and changes only what
   differs inside it, so the row under my pointer or keyboard stays the element it was; new rows are added at the end. */
function drawWidget(block, m) {
  const w = widgetOf(m); if (!w || !block) return; const html = widgetHTML(m, w); if (block._src === html) return;
  const t = document.createElement("template"); t.innerHTML = html;
  const swap = (sel) => { const a = $(sel, block), b = $(sel, t.content); if (a && b && a.outerHTML !== b.outerHTML) { a.innerHTML = b.innerHTML; for (const n of b.getAttributeNames()) a.setAttribute(n, b.getAttribute(n)); } };   /* in place: it may hold focus */
  swap(".wh > .wq"); swap(".wh > .wc");
  const list = $(".wl", block); for (const row of $$(".wr", t.content)) { const old = [...list.children].find((x) => x.dataset.key === row.dataset.key);
    if (!old) { list.append(row); host(row); continue; } if (old.outerHTML === row.outerHTML) continue;
    /* each cell changes in place: a press's own click must still find the nodes it went down on */
    for (const n of row.getAttributeNames()) old.setAttribute(n, row.getAttribute(n));
    for (const sel of [".check", ".wt", ".wf", ".wn"]) { const a = $(sel, old), b = $(sel, row); if (!a || !b || a.outerHTML === b.outerHTML) continue;
      if (sel === ".check") a.className = b.className; else if (sel === ".wn") { if (a.textContent !== b.textContent) M.figure(a, b.textContent); } else a.innerHTML = b.innerHTML; } }
  block._src = html;
}

/* ---------- acting ---------- */
/* a change of mine: shown at once, then sent; the server's event confirms it (it is then one of the message's submessages),
   and a refusal takes it back and says why */
async function act(m, data) {
  if (typeof m.id !== "number") { note("Wait until the message is sent"); return; }
  let list = pending.get(m.id); if (!list) pending.set(m.id, (list = [])); list.push(data); redraw(m);
  try { await api("POST", "submessage", {message_id: m.id, msg_type: "widget", content: JSON.stringify(data)}); }
  catch (e) { const i = list.indexOf(data); if (i >= 0) list.splice(i, 1); redraw(m); note("Not saved: " + e.message); S.play("nope"); }
}
const blocksOf = (id) => $$(`[data-part="wdg"][data-mid="${id}"]`);
function redraw(m) { for (const b of blocksOf(m.id)) drawWidget(b, m); }
/* the `submessage` event: one of a widget's changes, from anyone (mine too, once the server has it) */
function onSubmessage(ev) {
  if (ev.msg_type !== "widget") return; const c = msgIndex.get(ev.message_id), m = c && c.byId.get(ev.message_id); if (!m) return;
  m.submessages = m.submessages || []; if (m.submessages.some((s) => s.id === ev.submessage_id)) return;
  m.submessages.push({id: ev.submessage_id, sender_id: ev.sender_id, msg_type: ev.msg_type, content: ev.content});
  if (ev.sender_id === state.ME) { const list = pending.get(m.id) || [], i = list.findIndex((d) => JSON.stringify(d) === ev.content); if (i >= 0) list.splice(i, 1); }
  redraw(m);
}
function press(row, m) {
  const w = widgetOf(m); if (!w) return; const k = row.dataset.key;
  if (w.type === "poll") { const o = w.options.get(k); if (o) act(m, {type: "vote", key: k, vote: o.votes.has(state.ME) ? -1 : 1}); }
  else if (w.tasks.has(k)) act(m, {type: "strike", key: k});
}
/* Add an option, Add a task, or the author's question or title: the control becomes a field in its own row, of the row's
   height; Return keeps what was written, Esc or leaving it empty gives it up */
function edit(block, m, what) {
  const w = widgetOf(m); if (!w) return; const title = what === "title", ctl = title ? $(".wq", block) : $(".wa", block); if (!ctl) return;
  const f = el("input", "wi"); f.type = "text"; f.maxLength = title ? 200 : 300; f.value = title ? (w.type === "poll" ? w.question : w.title) : "";
  f.placeholder = title ? (w.type === "poll" ? "The question" : "The list's title") : w.type === "poll" ? "A new option" : "A task, or a task: what it means"; f.setAttribute("aria-label", f.placeholder);
  ctl.hidden = true; ctl.after(f); f.focus(); if (title) f.select();
  const end = (keep) => { if (!f.isConnected) return; const v = f.value.trim(); f.remove(); ctl.hidden = false; if (keep && v) commit(m, what, v); ctl.focus({preventScroll: true}); };
  f.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); end(true); } else if (e.key === "Escape") { e.preventDefault(); end(false); } });
  f.addEventListener("blur", () => setTimeout(() => end(false), 0));   /* only Return adds: leaving the field never sends what is in it */
}
function commit(m, what, v) {
  const w = widgetOf(m); if (!w) return;
  if (what === "title") { if (w.sender !== state.ME) return; act(m, w.type === "poll" ? {type: "question", question: v} : {type: "new_task_list_title", title: v}); return; }
  if (w.type === "poll") { if ([...w.options.values()].some((o) => o.text === v)) { note("That option is already there"); return; } act(m, {type: "new_option", idx: w.next, option: v}); return; }
  const i = v.indexOf(": "), task = (i < 0 ? v : v.slice(0, i)).trim(), desc = i < 0 ? "" : v.slice(i + 2).trim();
  if ([...w.tasks.values()].some((t) => t.task === task)) { note("That task is already there"); return; }
  act(m, {type: "new_task", key: w.next, task, desc, completed: false});
}
/* the page's presses and keys on widgets, on the press as every control here answers; Up and Down move between a widget's rows */
function wireWidgets(root, msgOf) {
  pressOn(root, "[data-part='wdg'] [data-w]", (t) => { const block = t.closest("[data-part='wdg']"), m = msgOf(block); if (!m) return;
    const k = t.dataset.w; if (k === "vote" || k === "tick") press(t, m); else if (k === "add") edit(block, m, "add"); else if (k === "title") edit(block, m, "title"); });
  root.addEventListener("keydown", (e) => { const t = e.target; if (!t.classList || !t.classList.contains("wr") || (e.key !== "ArrowUp" && e.key !== "ArrowDown") || e.metaKey || e.ctrlKey || e.altKey) return;
    const rows = $$(".wr, .wa", t.closest("[data-part='wdg']")), n = rows[rows.indexOf(t) + (e.key === "ArrowUp" ? -1 : 1)]; e.preventDefault(); e.stopPropagation(); if (n) n.focus(); });
}

export {drawWidget, localSubmessages, onSubmessage, widgetFromText, widgetOf, widgetPart, wireWidgets};
