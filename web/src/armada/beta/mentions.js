/* INTERIM BETA (README.md): the field's mentions and links: the @ and # pickers, pills, the switch's pill, a reply's quote.
   The words are mention_text.js's; this module is what a person sees and presses. */

import {$, el, esc, press} from "./util.js";
import {armadaBots, marks, people, repoStreams, state, streams, topicsOf, worktrees} from "./state.js";
import {cz, feed, lane, main, tx} from "./frame.js";
import {faceOf, nameOf, presOf} from "./faces.js";
import {note, onixNow, ready, syncField} from "./composer.js";
import {loadTopics, subs} from "./nav.js";
import {fetchMsgs, tkey} from "./routing.js";
import {catchUp, dist, edge, haltLane, syncJump} from "./feed.js";
import {editing} from "./hover.js";
import {fillField, pillFor, pillNode, quoteLine, quoteOf, quoteWire, rankPeople, rankPlaces, seedOf, triggerAt} from "./mention_text.js";

const agentId = () => [...armadaBots][0];
const inWorktree = () => !!state.place && state.place.kind === "topic" && !!state.place.repo;

/* ---------- the switch's pill: with the switch on, the field starts with @Onix, so what is sent is what is seen ---------- */
const seedWanted = () => inWorktree() && onixNow() && !editing && armadaBots.size > 0;
function seedField() {
  const s = seedOf(tx), want = seedWanted();
  if (want && !s && !tx.textContent.trim()) {
    tx.textContent = ""; const pill = pillNode(document, {wire: state.agentMention, label: "@Onix", uid: agentId(), agent: true, seed: true}), sp = document.createTextNode(" "); tx.append(pill, sp);
    if (document.activeElement === tx) getSelection().collapse(sp, 1);
    requestAnimationFrame(() => { if (!pill.isConnected) return; const r = document.createRange(); r.setStartBefore(pill); r.setEnd(sp, 1); cz.style.setProperty("--seed-w", Math.ceil(r.getBoundingClientRect().width) + "px"); });   /* the placeholder stands after the pill */
  } else if (!want && s) { const next = s.nextSibling; s.remove(); if (next && next.nodeType === 3) next.nodeValue = next.nodeValue.replace(/^ /, ""); }
  syncField();
}
/* an emptied field takes the switch's pill back: it goes with the switch, not with Backspace */
const reseed = () => { if (!tx.textContent && !seedOf(tx) && seedWanted()) seedField(); };

/* ---------- the pickers ---------- */
let pk = null, dismissed = null;
const team = () => [...people.values()].filter((p) => !p.bot && p.active && p.id !== state.ME).map((p) => ({id: p.id, name: p.name, pres: presOf(p.id)}));
const topicList = (sid) => (topicsOf.get(sid) || []).map((t) => ({...t, worktree: !!(worktrees.get(sid) && worktrees.get(sid).has(t.name.toLowerCase()))}));
function readTrigger(f) {
  const s = getSelection(); if (!s.rangeCount || !s.isCollapsed) return null; const n = s.anchorNode, off = s.anchorOffset; if (!n || n.nodeType !== 3 || !f.contains(n)) return null;
  const t = triggerAt(off > 80 ? "x" + n.nodeValue.slice(off - 80, off) : n.nodeValue.slice(0, off)); return t && {...t, node: n, start: off - t.length, end: off};
}
function candidates(t) {
  const p = state.place, topic = !!p && p.kind === "topic";
  if (t.kind === "@") return rankPeople(t.q, team(), {agent: inWorktree() && armadaBots.size ? {id: agentId(), name: "Onix"} : null, topic});
  const all = subs(), gt = t.q.indexOf(">");
  if (gt >= 0) for (const s of all) if (s.name.toLowerCase() === t.q.slice(0, gt).trim().toLowerCase() && !topicsOf.has(s.stream_id)) loadTopics(s.stream_id).then(() => { if (pk) drawPk(); });
  return rankPlaces(t.q, all, topicList);
}
const dot = '<span class="pk-at" aria-hidden="true">@</span>';
function rowHTML(c, i) {
  let lead, lab, k = "";
  if (c.kind === "person") { lead = faceOf(c.id, "s20", c.pres); lab = esc(c.name); k = c.pres === "on" ? "Here now" : c.pres === "away" ? "Away" : ""; }
  else if (c.kind === "agent") { lead = dot; lab = "Onix"; k = "Agent"; }
  else if (c.kind === "topic") { lead = dot; lab = "topic"; k = "Everyone in this topic"; }
  else if (c.kind === "stream") { lead = ic(repoStreams.has(c.stream.stream_id) ? "repo" : "hash"); lab = esc(c.stream.name); }
  else { lead = ic(c.worktree ? "branch" : "thread"); lab = `${esc(c.stream.name)} <span class="sep">›</span> ${esc(c.topic)}`; k = marks.get(tkey(c.stream.stream_id, c.topic)) === "live" ? "Running" : ""; }
  return `<button class="mi${i === pk.hi ? " hl" : ""}" role="option" id="mpk-${i}" aria-selected="${i === pk.hi}" data-i="${i}" tabindex="-1">${lead}<span class="lab">${lab}</span><span class="k">${k}</span></button>`;
}
function drawPk() { const t = readTrigger(pk.field); if (!t || t.node !== pk.node || t.start !== pk.start) { closePk(); return; }
  pk.q = t.q; pk.end = t.end; pk.rows = candidates(t); if (!pk.rows.length) { closePk(); return; } pk.hi = Math.min(pk.hi, pk.rows.length - 1);
  drawRows(); }
/* the rows, and the field naming the highlighted one: focus stays in the field, so a screen reader follows the arrows there */
function drawRows() { pk.el.innerHTML = pk.rows.map(rowHTML).join(""); host(pk.el); pk.field.setAttribute("aria-activedescendant", "mpk-" + pk.hi); }
function openPk(f, t) {
  if (pk && pk.field === f && pk.node === t.node && pk.start === t.start) { drawPk(); return; } closePk();
  if (!candidates(t).length) return;
  pk = {field: f, node: t.node, start: t.start, end: t.end, kind: t.kind, q: t.q, rows: [], hi: 0, el: el("div", "pop raised float mpk")};
  pk.el.id = "mpk"; pk.el.setAttribute("role", "listbox"); f.setAttribute("aria-controls", "mpk"); pk.el.setAttribute("aria-label", t.kind === "@" ? "Mention" : "Link a repository or worktree"); pk.el.style.visibility = "hidden"; main.append(pk.el); drawPk(); if (!pk) return;
  const box = pk.el; press(box, ".mi", (b) => choose(+b.dataset.i));
  requestAnimationFrame(() => { if (!pk || pk.el !== box) return; const sh = f.closest(".sh") || f, r = sh.getBoundingClientRect(), mr = main.getBoundingClientRect();   /* placed above its field, once, after layout */
    box.style.cssText = `position:absolute;left:${Math.round(r.left - mr.left)}px;bottom:${Math.round(mr.bottom - r.top + 6)}px`; M.release(box, {y: 4}, "touch"); });
}
function closePk() { if (!pk) return; const x = pk.el, f = pk.field; pk = null; x.inert = true; x.remove(); f.removeAttribute("aria-activedescendant"); f.removeAttribute("aria-controls"); }
function choose(i) {
  if (!pk) return; const c = pk.rows[i], {field, node} = pk; if (!c || !node.isConnected) { closePk(); return; }
  const s = getSelection(), end = s.anchorNode === node ? Math.max(s.anchorOffset, pk.start) : pk.end, r = document.createRange(); r.setStart(node, pk.start); r.setEnd(node, end); r.deleteContents();
  const pill = pillNode(document, {...pillFor(c, state.agentMention), you: c.id === state.ME}); r.insertNode(pill);
  let next = pill.nextSibling; if (!(next && next.nodeType === 3 && /^[  ]/.test(next.nodeValue))) { next = document.createTextNode(" "); pill.after(next); }
  closePk(); field.focus({preventScroll: true}); getSelection().collapse(next, 1); field.dispatchEvent(new Event("input", {bubbles: true}));
}
const fieldOf = (t) => (t && t.closest ? t.closest(".cz .tx") : null);
function onInput(e) { const f = fieldOf(e.target); if (!f || e.isComposing) return;
  if (state.place && state.place.kind === "partner" && f === tx) { closePk(); return; }   /* there the field is the partner search */
  const t = readTrigger(f); if (!t) { closePk(); return; } if (dismissed && dismissed.node === t.node && dismissed.start === t.start) return; dismissed = null; openPk(f, t); }
function onKey(e) {
  if (pk && e.target === pk.field && !e.isComposing && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const d = {ArrowDown: 1, ArrowUp: -1}[e.key], stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    if (d) { stop(); pk.hi = (pk.hi + d + pk.rows.length) % pk.rows.length; drawRows(); return; }
    if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey) { stop(); choose(pk.hi); return; }
    if (e.key === "Escape") { stop(); dismissed = {node: pk.node, start: pk.start}; closePk(); return; }
  }
  if (e.key === "Escape" && replyTo && e.target === tx && !editing && !e.defaultPrevented) { e.preventDefault(); e.stopImmediatePropagation(); clearReply(); ready(); }   /* Esc gives the reply up first */
}

/* ---------- a reply: one line of a message, quoted above my words ---------- */
let replyTo = null;
const tplQ = document.createElement("template");
/* the message's words without its own reply quote and with no mention marks */
function wordsOf(m) { tplQ.innerHTML = m.content || ""; const kids = [...tplQ.content.children]; if (quoteOf(kids)) { kids[0].remove(); kids[1].remove(); }
  tplQ.content.querySelectorAll(".user-mention, .topic-mention, .user-group-mention").forEach((s) => s.replaceWith(s.textContent.replace(/^@/, "")));
  return [...tplQ.content.children].map((x) => x.textContent).join("\n") || tplQ.content.textContent; }
function linkOf(m) { const p = state.place; return p.kind === "topic" ? `${location.origin}/#narrow/channel/${p.stream_id}/topic/${encodeURIComponent(p.topic)}/near/${m.id}` : `${location.origin}/#narrow/near/${m.id}`; }
function startReply(m) { if (!m || typeof m.id !== "number") return; replyTo = {id: m.id, uid: m.sender_id, name: nameOf(m.sender_id), line: quoteLine(wordsOf(m)), link: linkOf(m), key: state.place.key}; state.threadTx = null; drawReply(); ready(); }
function clearReply() { if (!replyTo) return; replyTo = null; drawReply(); }
/* a reply belongs to its place: another place's field does not carry it */
const syncReply = () => { if (replyTo && (!state.place || state.place.key !== replyTo.key)) clearReply(); };
function drawReply() { let box = $(":scope .txw > .rq", cz); cz.classList.toggle("replying", !!replyTo);
  if (!replyTo) { if (box) box.remove(); return; } if (!box) { box = el("span", "rq"); $(".txw", cz).prepend(box); }
  box.innerHTML = `${ic("answer")}<span class="rqn">${esc(replyTo.name)}</span><span class="q">${esc(replyTo.line)}</span><button class="x" data-unreply aria-label="Cancel the reply" data-tip="Cancel the reply" data-key="Esc">${ic("x")}</button>`; host(box); }
/* the quote to send before my words, once: the reply is used up */
function takeReply() { if (!replyTo) return ""; const q = quoteWire(replyTo); clearReply(); return q; }
/* editing a reply: its quote stands in the strip while its words are in the field, and goes back before them on save; the
   reply the draft had before comes back when the edit ends */
function holdReply(quote) { const was = replyTo; replyTo = quote ? {...quote, key: state.place.key} : null; drawReply(); return was; }
function restoreReply(was) { replyTo = was && state.place && was.key === state.place.key ? was : null; drawReply(); }
/* the quote above a bubble: pressed, the conversation goes to the message it quotes */
async function jumpTo(id) {
  const c = state.P, find = () => $(`[data-part][data-mid="${id}"]:not(.rq)`, lane);
  let n = find(); for (let i = 0; !n && i < 4 && c.loaded && !c.oldest && state.P === c; i++) { await fetchMsgs(c, true); if (state.P !== c) return; catchUp(c, {keep: true}); n = find(); }
  if (state.P !== c) return; if (!n) { note("That message is not in this conversation"); ready(); return; }
  haltLane(); const r = n.getBoundingClientRect(), fr = feed.getBoundingClientRect(); feed.scrollTop += r.top - fr.top - fr.height / 3; state.lastSet = feed.scrollTop; state.pinned = dist() < 4; edge(); syncJump(); M.appear(n, 0.3); ready();
}
const quoteHTML = (q) => `${ic("answer")}<span class="rqn">${esc(nameOf(q.uid) || q.name)}</span><span class="q">${esc(q.line)}</span>`;

/* ---------- an edit: wire text back into pills; what the echo looks names up ---------- */
const personByName = (name) => { const low = name.toLowerCase(); if (state.agentMention.toLowerCase() === `@**${low}**` || low === "onix") return agentId() ?? null; for (const p of people.values()) if (p.name.toLowerCase() === low) return p.id; return null; };
const streamByName = (name) => { const low = name.toLowerCase(); for (const s of streams.values()) if (s.name.toLowerCase() === low) return s.stream_id; return null; };
const refLook = {personByName, streamByName};
function resolveWire(r) {
  if (r.kind === "place") { const gt = r.name.indexOf(">"), sid = streamByName(gt >= 0 ? r.name.slice(0, gt) : r.name); if (sid == null) return null; const s = streams.get(sid);
    return gt >= 0 ? pillFor({kind: "place", stream: s, topic: r.name.slice(gt + 1)}) : pillFor({kind: "stream", stream: s}); }
  if (r.wire === state.agentMention) return {wire: state.agentMention, label: "@Onix", uid: agentId(), agent: true};
  if (/^(topic|all|everyone|channel|stream)$/i.test(r.name) && r.id == null) return {wire: r.wire, label: "@" + r.name};
  const id = r.id ?? personByName(r.name); if (id == null || !people.has(id)) return null;
  return {wire: r.wire, label: "@" + people.get(id).name, uid: id, you: id === state.ME};
}
const fillFromWire = (f, wire) => fillField(f, wire, resolveWire);

/* ---------- once, at boot (host.js) ---------- */
function wireMentions() {
  document.addEventListener("input", onInput);
  document.addEventListener("keydown", onKey, true);
  /* the caret moved away from what the picker is for (an arrow, a click): it closes; typing redraws it from input */
  document.addEventListener("selectionchange", () => { if (!pk) return; const t = readTrigger(pk.field); if (!t || t.node !== pk.node || t.start !== pk.start) closePk(); });
  document.addEventListener("focusout", (e) => { if (pk && e.target === pk.field) closePk(); });
  press(cz, "[data-unreply]", () => { clearReply(); ready(); });
  press(lane, ".rq[data-quote]", (b) => jumpTo(+b.dataset.quote));
}

export {fillFromWire, holdReply, jumpTo, quoteHTML, refLook, reseed, restoreReply, seedField, startReply, syncReply, takeReply,
  wireMentions};
