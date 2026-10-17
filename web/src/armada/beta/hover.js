/* INTERIM BETA (README.md): the message's controls: the hover overlay, its menu, edit and delete. */

import {$, $$, clamp, el, press, T} from "./util.js";
import {groups, msgIndex, people, state, streams} from "./state.js";
import {now} from "./time.js";
import {api} from "./connection.js";
import {feed, go, lane, main, sh, tx} from "./frame.js";
import {isOnix} from "./faces.js";
import {echoHTML} from "./parts.js";
import {note, ready, syncField} from "./composer.js";
import {react} from "./reactions.js";
import {openInfo, RECEIPTS} from "./receipts.js";
import {openThread} from "./threads.js";
import {updateMessage} from "./agent_turn.js";
import {fillFromWire, holdReply, restoreReply, startReply, takeReply} from "./mentions.js";
import {fieldEmpty, splitQuoteWire} from "./mention_text.js";
import {drawRunState} from "./band.js";
import {openCard} from "./partner_card.js";
import {openLater, savePending} from "./later.js";

/* ---------- the message's controls: the hover overlay, its menu, edit and delete ----------
   One raised object with four controls (React, Reply in thread, Copy text, More) on the bubble's open side, or at the
   top right of words on the plane (Onix's, a bot's). It waits for the pointer to rest, travels between messages while it is up, never sits in
   flow. Onix's row is as wide as the lane but its words are not: only its words and blocks bring the overlay up, never
   the empty width beside them (statement 27). */
let dialogOpen = false, menu = null, editing = null;
const OVER = {react: "React", thread: "Reply in thread", copy: "Copy text", more: "More"};
let over = null, overAt = null, overTimer = 0, overWant = null, px_ = 0, py_ = 0;
const msgOf = (t) => { const n = t && t.closest("[data-mid]"); if (!n) return null; const id = n.dataset.mid; return state.P.byId.get(+id) || state.P.byId.get(id) || null; };
function buildOver() {
  over = el("span", "over", Object.keys(OVER).map((n) => `<button class="c" aria-label="${OVER[n]}" data-act="${n}" data-tip="${OVER[n]}">${ic(n)}</button>`).join("")); host(over); over.inert = true;
  feed.addEventListener("pointermove", (e) => { if (menu) return; if (e.target.closest && e.target.closest(".rcs, .cz, .blk.ask")) return;
    let t = e.target.closest && (e.target.closest(".over") ? overAt : e.target.closest(".b, .cl:not(.onix):not(.bot) > :is(.blk.pic, .blk.wdg), .cl:is(.onix, .bot)")); if (t && t !== overAt && t.matches(".cl:is(.onix, .bot)") && !onWords(t, e.clientX, e.clientY)) t = null;
    if (!t || !msgOf(t)) { if (!e.target.closest(".over")) { clearTimeout(overTimer); overWant = null; } return; }
    if (t === overAt && !over.inert) return; if (!over.inert) { overShow(t); return; }
    if (t === overWant && Math.hypot(e.clientX - px_, e.clientY - py_) < 40) return; overWant = t; px_ = e.clientX; py_ = e.clientY; clearTimeout(overTimer); overTimer = setTimeout(() => { if (overWant === t) overShow(t); }, 120); }, {passive: true});
  feed.addEventListener("pointerleave", () => { if (!menu) overHide(); });
  press(over, ".c", (a, e, ptr) => act(a.dataset.act, overAt, a, !ptr && e.detail === 0));
  /* the keyboard: one message at a time is in the Tab order (the one I last stood on, else the latest); Up and Down move
     between messages; standing on one brings its overlay up inside it, so Tab goes on to React, Reply in thread, Copy text
     and More, then out to the band and the field */
  lane.addEventListener("keydown", (e) => { const t = e.target, d = {ArrowUp: -1, ArrowDown: 1}[e.key]; if (!d || !t.dataset || !t.dataset.door || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault(); const all = doors(), n = all[clamp(all.indexOf(t) + d, 0, all.length - 1)]; if (!n || n === t) return; door = n; syncDoor(); n.focus({preventScroll: true});
    const r = n.getBoundingClientRect(), fr = feed.getBoundingClientRect(), up = fr.top + 48 - r.top, down = r.bottom - (fr.bottom - 72); if (up > 0) feed.scrollTop -= up; else if (down > 0) feed.scrollTop += Math.min(down, r.top - fr.top - 48); });
  lane.addEventListener("focusin", (e) => { const t = e.target; if (t.dataset && t.dataset.door) { door = t; overShow(t); } });
  addEventListener("keydown", (e) => { if (e.key === "Tab") syncDoor(); }, {capture: true});   /* the stop is chosen as Tab moves to it, before the browser does */
  lane.addEventListener("focusout", (e) => { if (!overAt || menu) return; const to = e.relatedTarget; if (to && (overAt.contains(to) || over.contains(to) || (to.dataset && to.dataset.door))) return; overHide(); });
  new ResizeObserver(() => wordsVer++).observe(lane); document.fonts.addEventListener("loadingdone", () => wordsVer++);   /* the lane, not the window: hiding the nav widens it too, and the words wrap again */
}
/* Onix's words in a row changed (agent_turn.js says so where it writes them): their boxes are read again, and an overlay
   standing on them moves with their corner */
function wordsChanged(cl) { cl._words = null; if (over && overAt === cl && !over.inert && !placeOver.due) { placeOver.due = true; requestAnimationFrame(placeOver); } }
/* where Onix's words are, in its row's own coordinates: each part measured to its ink (a paragraph, a heading or a list is as
   wide as its longest line; a block is its box). Read once until the lane next changes, so a pointer that moves reads one
   rectangle, the row's. */
let wordsVer = 0;
function wordsOf(cl) { if (cl._words && cl._words.v === wordsVer) return cl._words;
  const base = cl.getBoundingClientRect(), range = document.createRange(), rects = [];
  const add = (r) => { if (r.width || r.height) rects.push({l: r.left - base.left, t: r.top - base.top, r: r.right - base.left, b: r.bottom - base.top}); };
  for (const part of $$(":scope > [data-part]", cl)) { if (!part.classList.contains("ans")) { add(part.getBoundingClientRect()); continue; }
    for (const c of part.children) { range.selectNodeContents(c); add(range.getBoundingClientRect()); } }
  const head = $(".head:not(.gone) > .mk", cl); if (head) add(head.getBoundingClientRect());   /* the working mark rides the words' last line */
  const box = rects.length ? {l: Math.min(...rects.map((x) => x.l)), t: Math.min(...rects.map((x) => x.t)), r: Math.max(...rects.map((x) => x.r)), b: Math.max(...rects.map((x) => x.b))} : null;
  return (cl._words = {v: wordsVer, rects, box}); }
/* a pointer is on the words within a few pixels of a line's ink, so it does not fall off between two lines or at a word's end */
function onWords(cl, x, y) { const base = cl.getBoundingClientRect(), px = x - base.left, py = y - base.top;
  return wordsOf(cl).rects.some((r) => px >= r.l - 8 && px <= r.r + 8 && py >= r.t - 4 && py <= r.b + 4); }
let door = null;
/* a person's pictures, poll or to-do list are a stop of their own: such a message has no bubble */
const doors = () => $$(".bl > .b, .cl:not(.onix):not(.bot) > :is(.blk.pic, .blk.wdg), .cl:is(.onix, .bot)[data-mid]", lane).filter((t) => !t.closest(".cz"));
function syncDoor() { const all = doors(); if (!door || !door.isConnected || !all.includes(door)) door = all[all.length - 1] || null;
  for (const x of $$("[data-door]", lane)) if (x !== door) { x.removeAttribute("tabindex"); delete x.dataset.door; }
  if (door && !door.dataset.door) { door.tabIndex = 0; door.dataset.door = "1"; } }
function overShow(t) { if (!t || !t.isConnected) return; if (overAt === t && !over.inert) return; const up = !over.inert && over.isConnected, before = up && over.getBoundingClientRect(); overAt = t; (t.matches(".b") ? t.parentElement : t).append(over); over.inert = false;   /* a bubble's overlay stands in its row, past its time and reactions (beta.css) */
  const mine = (msgOf(t) || {}).sender_id === state.ME; $('[data-act="more"]', over).hidden = false; void mine;
  placeOver();
  if (up) { M.fade(over, 1); M.flip(over, before, "touch"); } else { M.fade(over, 1); M.release(over, {y: T["pane-rise"] || 4}, "touch"); } }
/* on Onix's words: beside the top right of the words, or at the row's end when the words reach it (a block does); on a
   bubble, where the stylesheet puts it */
function placeOver() { placeOver.due = false; const t = overAt; if (!t || !over.isConnected) return; const box = t.matches(".cl:is(.onix, .bot)") && wordsOf(t).box;
  if (box) { over.style.left = Math.round(Math.max(0, Math.min(box.r + 6, t.getBoundingClientRect().width - over.offsetWidth))) + "px"; over.style.top = Math.round(box.t - 6) + "px"; over.style.right = "auto"; }
  else over.style.left = over.style.top = over.style.right = ""; }
function overHide() { clearTimeout(overTimer); overWant = null; if (!over || over.inert) return; over.inert = true; overAt = null; M.fade(over, 0); }
const plainText = (t) => { const x = t.cloneNode(true); $$(".rcs, .rca, .over, .head, time, .who, .sigil, .face, .plan, .cp, .fold, .tl, .thr-in, .line", x).forEach((y) => y.remove()); return x.textContent.trim(); };
function act(name, t, btn, kbd) { const m = msgOf(t); if (!m) return;   /* from the keyboard, focus stays on the control: the field is not where I was */
  if (name === "react") { react(m, {emoji_name: "+1", emoji_code: "1f44d", reaction_type: "unicode_emoji"}); if (!kbd) ready(); }
  else if (name === "thread") { overHide(); openThread(m.thread_root_message_id || m.id, false, {focus: true}); }
  else if (name === "copy") { try { navigator.clipboard.writeText(plainText(t.matches(".b") ? t : t)).catch(() => {}); } catch (e) {} M.say(btn, "Copied", 1200); if (!kbd) ready(); }
  else if (name === "more") openMenu(m, btn); }
/* More: what can be done to this message. Any sent one can be replied to and put off until later (later.js: Later is here,
   not a fifth control on the overlay); mine can be edited, and any deleted, while the realm allows it; any can be linked or
   copied. */
function openMenu(m, btn) { closeMenu(); const items = [];
  if (typeof m.id === "number") items.push(["reply", "Reply", "R"], ["later", "Later", "L"]);
  if (canEdit(m)) items.push(["edit", "Edit", "E"]); if (canDelete(m)) items.push(["delete", "Delete", ""]);
  items.push(["copy", "Copy text", "C"], ["link", "Copy link", ""]); if (RECEIPTS && typeof m.id === "number") items.push(["info", "Info", "I"]);
  if (m.sender_id > 0 && m.sender_id !== state.ME && !isOnix(m.sender_id)) items.push(["card", "Card", ""]);   /* the sender's card, which a face opens to the pointer */
  menu = el("div", "menu raised float", items.map(([k, label, key]) => `<button class="mi" data-mi="${k}" tabindex="-1">${label}<span class="k">${key}</span></button>`).join("")); menu.setAttribute("role", "menu");
  const r = btn.getBoundingClientRect(), mr = main.getBoundingClientRect(); menu.style.cssText = `position:absolute;left:${Math.round(r.left - mr.left)}px;top:${Math.round(r.bottom - mr.top + 4)}px`; main.append(menu); host(menu);
  let hi = 0; const mark = () => $$(".mi", menu).forEach((x, i) => x.classList.toggle("hl", i === hi)); mark();
  /* the arrows go round, as a menu's do: Up from the first item is the last */
  menu._key = (e) => { const d = {ArrowDown: 1, ArrowUp: -1}[e.key]; if (d) { e.preventDefault(); hi = (hi + d + items.length) % items.length; mark(); return true; } if (e.key === "Enter") { e.preventDefault(); $$(".mi", menu)[hi].click(); return true; } if (e.key === "Escape") { e.preventDefault(); closeMenu(); ready(); return true; }
    const hot = items.findIndex(([, , key]) => key && key.toLowerCase() === e.key.toLowerCase()); if (hot >= 0 && !e.metaKey && !e.ctrlKey) { e.preventDefault(); $$(".mi", menu)[hot].click(); return true; } return false; };
  menu._t = overAt;
  press(menu, ".mi", (b, e, ptr) => { const k = b.dataset.mi, t = menu._t; closeMenu(); overHide();
    if (k === "reply") startReply(m); else if (k === "later") openLater(m, btn.getBoundingClientRect()); else if (k === "edit") startEdit(m); else if (k === "delete") confirmDelete(m); else if (k === "info") openInfo(m.id, t, !ptr); else if (k === "card") openCard(m.sender_id, t);
    else if (k === "copy") { try { navigator.clipboard.writeText(plainText(t)).catch(() => {}); } catch (e) {} note("Copied"); ready(); }
    else if (k === "link") { const p = state.place, u = p.kind === "topic" ? `${location.origin}/#narrow/channel/${p.stream_id}/topic/${encodeURIComponent(p.topic)}/near/${m.id}` : `${location.origin}/#narrow/near/${m.id}`; try { navigator.clipboard.writeText(u).catch(() => {}); } catch (e) {} note("Link copied"); ready(); } });
  M.release(menu, {y: -(T["pane-rise"] || 4)}, "touch"); }
function closeMenu() { if (!menu) return; const x = menu; menu = null; x.inert = true; M.leave(x, () => x.remove()); }
/* editing my message: its words go back to the field, Return saves them into the same message, Esc gives the draft up */
/* whether the realm lets me change this message's words now, by the server's own rule (validate_user_can_edit_message):
   editing is on, the message is mine, and it is inside the limit when there is one. An administrator gets no more time
   than anyone. `grace` is the seconds Zulip's own client allows Up past the limit (5), for a key pressed as it passes. */
function canEdit(m, grace = 0) { const {allowEdit, editLimit} = state.realm;
  return !!m && typeof m.id === "number" && m.sender_id === state.ME && !isOnix(m.sender_id) && allowEdit && (editLimit == null || now() / 1000 - m.timestamp < editLimit + grace); }
/* whether the realm lets me delete this message now, by the server's own rule (validate_can_delete_message): null when it
   does, else the server's words for why not. Whoever the realm's can_delete_any_message_group holds (never a guest) or the
   channel's may delete any message. Anyone else may delete a message of their own or of a bot they own, when the realm's
   can_delete_own_message_group holds them (in a channel, the channel's will do), inside the limit when there is one.
   Onix's words are the run's record and are not offered, as before. A channel the page does not hold grants nothing. */
const NO_RIGHT = "You don't have permission to delete this message", TOO_LATE = "The time limit for deleting this message has passed";
function whyNoDelete(m) { if (!m || typeof m.id !== "number" || isOnix(m.sender_id)) return NO_RIGHT;
  const {deleteAny, deleteOwn, deleteLimit, guest} = state.realm, s = m.type === "stream" ? streams.get(m.stream_id) : null, sender = people.get(m.sender_id);
  if ((!guest && holds(deleteAny)) || (s && holds(s.can_delete_any_message_group))) return null;
  if (m.sender_id !== state.ME && !(sender && sender.bot && sender.owner === state.ME)) return NO_RIGHT;
  if (!holds(deleteOwn) && !(s && holds(s.can_delete_own_message_group))) return NO_RIGHT;
  return deleteLimit == null || now() / 1000 - m.timestamp < deleteLimit ? null : TOO_LATE; }
const canDelete = (m) => !whyNoDelete(m);
/* whether a group setting holds me: a group's id, or {direct_members, direct_subgroups}; a group holds its members and its
   subgroups' members, however deep (Zulip's is_user_in_setting_group) */
function holds(setting) { if (setting == null) return false; if (typeof setting === "object" && setting.direct_members.includes(state.ME)) return true;
  const todo = typeof setting === "number" ? [setting] : [...setting.direct_subgroups], seen = new Set();
  while (todo.length) { const id = todo.pop(); if (seen.has(id)) continue; seen.add(id); const g = groups.get(id); if (!g) continue; if (g.members.has(state.ME)) return true; todo.push(...g.subgroups); }
  return false; }
/* Up in an empty field: my latest message here, while I may still edit it; true when it opened */
function editLast() { const m = state.P && state.P.msgs.filter((x) => x.sender_id === state.ME && typeof x.id === "number" && !x.thread_root_message_id).pop();
  if (!canEdit(m, 5)) return false; startEdit(m); return true; }
async function startEdit(m) { if (!canEdit(m, 5)) return; if (editing) endEdit();
  let raw; try { raw = (await api("GET", `messages/${m.id}`, {apply_markdown: false})).message.content; } catch (e) { note(e.message); return; }
  state.threadTx = null; openEdit(m, raw); ready(); caretAtEnd(); }
/* the draft (with its pills) is kept as it was, and comes back when the edit ends; the message's mentions come back as pills;
   a reply's quote goes to the strip above the field, not into it as Zulip's markup */
function openEdit(m, raw) { const split = splitQuoteWire(raw);
  editing = {m, was: [...tx.childNodes], reply: holdReply(split && split.quote)}; $$(`[data-mid="${m.id}"] .b`, lane).forEach((b) => b.classList.add("unsent"));
  fillFromWire(tx, split ? split.rest : raw); syncField(); go.classList.add("armed"); drawRunState(); }
/* written after its words, as Up promises */
function caretAtEnd() { if (document.activeElement !== tx) return; const s = getSelection(); s.selectAllChildren(tx); s.collapseToEnd(); }
function endEdit() { if (!editing) return; const {m, was, reply} = editing; editing = null; $$(`[data-mid="${m.id}"] .b`, lane).forEach((b) => b.classList.remove("unsent")); tx.replaceChildren(...was); restoreReply(reply); syncField(); go.classList.toggle("armed", !fieldEmpty(tx)); drawRunState(); }
/* `text` is the field's wire text (send.js), mentions in Zulip's syntax where they were written. The message shows its new
   words at once. A save the server refuses, or cannot be asked, puts back what the message last saved (its words and its
   Edited mark), says why, and gives my words back to the field as the same edit, to save again or to copy; whatever the
   field held by then is kept as an edit's draft is. With another edit in the field by then, my words join that edit's
   draft and come back when it ends. */
async function saveEdit(text, at) { const {m} = editing; if (m.scheduled) { savePending(text, at); return; }   /* a held message of mine (Send when) */
  if (!text) { const why = whyNoDelete(m); if (!why) { endEdit(); confirmDelete(m); } else refuse("Not deleted: " + why); return; }   /* no words: delete it, when I may; else the edit stays open */
  const raw = takeReply() + text, c = msgIndex.get(m.id) || state.P, saved = {content: m.content, edited: m.edited}, shown = echoHTML(raw);
  endEdit(); m.content = shown; m.edited = true; updateMessage(c, m); S.play("send", 0, at); ready();
  try { await api("PATCH", `messages/${m.id}`, {content: raw}); }
  catch (e) { if (m.content === shown) { m.content = saved.content; m.edited = saved.edited; updateMessage(c, m); }   /* unless the server's own event has written it since */
    if (editing) { const held = el("div"); fillFromWire(held, (editing.was.length ? " " : "") + raw); editing.was.push(...held.childNodes); }
    else { openEdit(m, raw); caretAtEnd(); }
    refuse("Not saved: " + e.message); } }
/* a refusal, said as every refusal of the page is: the words beside Send, the field's shake, the sound */
function refuse(words) { note(words); M.refuse(sh); S.play("nope"); }
/* deleting cannot be undone, so it asks once, in a dialog; the server's event takes the message away. A delete the server
   refuses, or cannot be asked, leaves the message as it was and says why. */
function confirmDelete(m) { if (dialogOpen || !canDelete(m)) return; dialogOpen = true; const d = el("div", "dlg raised float", `<p style="font:var(--t-strong)">Delete this message?</p><p style="color:var(--ink-2);margin-top:var(--s-2)">It is removed for everyone, and cannot be brought back.</p><div class="btns"><button class="btn" data-no>Cancel</button><button class="go" data-yes>Delete</button></div>`); d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true");
  d.style.cssText = "position:absolute;left:calc(50% - 200px);top:160px"; main.append(d); host(d); M.release(d, {y: -(T["pane-rise"] || 4)}, "touch");
  const close = () => { dialogOpen = false; d.inert = true; M.leave(d, () => d.remove()); removeEventListener("keydown", key, true); ready(); };
  const yes = async () => { close(); try { await api("DELETE", `messages/${m.id}`); } catch (e) { refuse("Not deleted: " + e.message); } };
  const key = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); (document.activeElement === $("[data-no]", d) ? close : yes)(); } else if (e.key === "Tab") { e.preventDefault(); const b = $$("button", d), i = b.indexOf(document.activeElement); b[(i + 1) % b.length].focus(); } };
  addEventListener("keydown", key, true); press(d, "[data-no]", close); press(d, "[data-yes]", yes); $("[data-no]", d).focus(); }   /* it stands on Cancel: Return alone never deletes */

export {wordsChanged, buildOver, canDelete, canEdit, caretAtEnd, closeMenu, confirmDelete, dialogOpen, editLast, editing, endEdit, holds, menu,
  msgOf, openEdit, over, overAt, overHide, saveEdit, startEdit};
