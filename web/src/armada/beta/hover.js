/* INTERIM BETA (README.md): the message's controls: the hover overlay, its menu, edit and delete. */

import {$, $$, clamp, el, press, T} from "./util.js";
import {msgIndex, state} from "./state.js";
import {api} from "./connection.js";
import {feed, go, lane, main, tx} from "./frame.js";
import {isOnix} from "./faces.js";
import {echoHTML} from "./parts.js";
import {note, ready, syncField} from "./composer.js";
import {react} from "./reactions.js";
import {openInfo, RECEIPTS} from "./receipts.js";
import {openThread} from "./threads.js";
import {updateMessage} from "./agent_turn.js";
import {addressedEcho, ONIX_AT} from "./send.js";
import {drawRunState} from "./band.js";

/* ---------- the message's controls: the hover overlay, its menu, edit and delete ----------
   One raised object with four controls (React, Reply in thread, Copy text, More) on the bubble's open side, or on
   Onix's first line. It waits for the pointer to rest, travels between messages while it is up, never sits in flow. */
let dialogOpen = false, menu = null, editing = null;
const OVER = {react: "React", thread: "Reply in thread", copy: "Copy text", more: "More"};
let over = null, overAt = null, overTimer = 0, overWant = null, px_ = 0, py_ = 0;
const msgOf = (t) => { const n = t && t.closest("[data-mid]"); if (!n) return null; const id = n.dataset.mid; return state.P.byId.get(+id) || state.P.byId.get(id) || null; };
function buildOver() {
  over = el("span", "over", Object.keys(OVER).map((n) => `<button class="c" aria-label="${OVER[n]}" data-act="${n}" data-tip="${OVER[n]}">${ic(n)}</button>`).join("")); host(over); over.inert = true;
  feed.addEventListener("pointermove", (e) => { if (menu) return; if (e.target.closest && e.target.closest(".rcs, .cz, .blk.ask")) return;
    const t = e.target.closest && (e.target.closest(".over") ? overAt : e.target.closest(".b, .cl.onix")); if (!t || !msgOf(t)) { if (!e.target.closest(".over")) { clearTimeout(overTimer); overWant = null; } return; }
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
  lane.addEventListener("focusout", (e) => { if (!overAt || menu) return; const to = e.relatedTarget; if (to && (overAt.contains(to) || over.contains(to) || (to.dataset && to.dataset.door))) return; overHide(); });
  new MutationObserver(() => { if (!syncDoor.due) { syncDoor.due = true; queueMicrotask(syncDoor); } }).observe(lane, {childList: true, subtree: true});
}
let door = null;
const doors = () => $$(".bl > .b, .cl.onix[data-mid]", lane).filter((t) => !t.closest(".cz"));
function syncDoor() { syncDoor.due = false; const all = doors(); if (!door || !door.isConnected || !all.includes(door)) door = all[all.length - 1] || null;
  for (const x of $$("[data-door]", lane)) if (x !== door) { x.removeAttribute("tabindex"); delete x.dataset.door; }
  if (door && !door.dataset.door) { door.tabIndex = 0; door.dataset.door = "1"; } }
function overShow(t) { if (!t || !t.isConnected) return; if (overAt === t && !over.inert) return; const up = !over.inert && over.isConnected, before = up && over.getBoundingClientRect(); overAt = t; (t.matches(".b") ? t.parentElement : t).append(over); over.inert = false;   /* a bubble's overlay stands in its row, past its time and reactions (beta.css) */
  const mine = (msgOf(t) || {}).sender_id === state.ME; $('[data-act="more"]', over).hidden = false; void mine;
  if (up) { M.fade(over, 1); M.flip(over, before, "touch"); } else { M.fade(over, 1); M.release(over, {y: T["pane-rise"] || 4}, "touch"); } }
function overHide() { clearTimeout(overTimer); overWant = null; if (!over || over.inert) return; over.inert = true; overAt = null; M.fade(over, 0); }
const plainText = (t) => { const x = t.cloneNode(true); $$(".rcs, .rca, .over, .head, time, .who, .sigil, .face, .tl, .thr-in, .line", x).forEach((y) => y.remove()); return x.textContent.trim(); };
function act(name, t, btn, kbd) { const m = msgOf(t); if (!m) return;   /* from the keyboard, focus stays on the control: the field is not where I was */
  if (name === "react") { react(m, {emoji_name: "+1", emoji_code: "1f44d", reaction_type: "unicode_emoji"}); if (!kbd) ready(); }
  else if (name === "thread") { overHide(); openThread(m.thread_root_message_id || m.id, false, {focus: true}); }
  else if (name === "copy") { try { navigator.clipboard.writeText(plainText(t.matches(".b") ? t : t)).catch(() => {}); } catch (e) {} M.say(btn, "Copied", 1200); if (!kbd) ready(); }
  else if (name === "more") openMenu(m, btn); }
/* More: what can be done to this message. Mine can be edited and deleted; any can be linked or copied. */
function openMenu(m, btn) { closeMenu(); const mine = m.sender_id === state.ME && typeof m.id === "number", items = [];
  if (mine && !isOnix(m.sender_id)) items.push(["edit", "Edit", "E"], ["delete", "Delete", ""]);
  items.push(["copy", "Copy text", "C"], ["link", "Copy link", ""]); if (RECEIPTS && typeof m.id === "number") items.push(["info", "Info", "I"]);
  menu = el("div", "menu raised float", items.map(([k, label, key]) => `<button class="mi" data-mi="${k}" tabindex="-1">${label}<span class="k">${key}</span></button>`).join("")); menu.setAttribute("role", "menu");
  const r = btn.getBoundingClientRect(), mr = main.getBoundingClientRect(); menu.style.cssText = `position:absolute;left:${Math.round(r.left - mr.left)}px;top:${Math.round(r.bottom - mr.top + 4)}px`; main.append(menu); host(menu);
  let hi = 0; const mark = () => $$(".mi", menu).forEach((x, i) => x.classList.toggle("hl", i === hi)); mark();
  menu._key = (e) => { const d = {ArrowDown: 1, ArrowUp: -1}[e.key]; if (d) { e.preventDefault(); hi = clamp(hi + d, 0, items.length - 1); mark(); return true; } if (e.key === "Enter") { e.preventDefault(); $$(".mi", menu)[hi].click(); return true; } if (e.key === "Escape") { e.preventDefault(); closeMenu(); ready(); return true; }
    const hot = items.findIndex(([, , key]) => key && key.toLowerCase() === e.key.toLowerCase()); if (hot >= 0 && !e.metaKey && !e.ctrlKey) { e.preventDefault(); $$(".mi", menu)[hot].click(); return true; } return false; };
  menu._t = overAt;
  press(menu, ".mi", (b, e, ptr) => { const k = b.dataset.mi, t = menu._t; closeMenu(); overHide();
    if (k === "edit") startEdit(m); else if (k === "delete") confirmDelete(m); else if (k === "info") openInfo(m.id, t, !ptr);
    else if (k === "copy") { try { navigator.clipboard.writeText(plainText(t)).catch(() => {}); } catch (e) {} note("Copied"); ready(); }
    else if (k === "link") { const p = state.place, u = p.kind === "topic" ? `${location.origin}/#narrow/channel/${p.stream_id}/topic/${encodeURIComponent(p.topic)}/near/${m.id}` : `${location.origin}/#narrow/near/${m.id}`; try { navigator.clipboard.writeText(u).catch(() => {}); } catch (e) {} note("Link copied"); ready(); } });
  M.release(menu, {y: -(T["pane-rise"] || 4)}, "touch"); }
function closeMenu() { if (!menu) return; const x = menu; menu = null; x.inert = true; M.leave(x, () => x.remove()); }
/* editing my message: its words go back to the field, Return saves them into the same message, Esc gives the draft up */
async function startEdit(m) { if (!m || m.sender_id !== state.ME || typeof m.id !== "number") return; if (editing) endEdit();
  let raw; try { raw = (await api("GET", `messages/${m.id}`, {apply_markdown: false})).message.content; } catch (e) { note(e.message); return; }
  raw = raw.split(state.agentMention).join("@Onix");
  editing = {m, was: tx.textContent}; $$(`[data-mid="${m.id}"] .b`, lane).forEach((b) => b.classList.add("unsent"));
  state.threadTx = null; tx.textContent = raw; syncField(); go.classList.add("armed"); drawRunState(); ready(); }
function endEdit() { if (!editing) return; const {m, was} = editing; editing = null; $$(`[data-mid="${m.id}"] .b`, lane).forEach((b) => b.classList.remove("unsent")); tx.textContent = was; syncField(); go.classList.toggle("armed", !!was.trim()); drawRunState(); }
async function saveEdit(text, at) { const {m} = editing; if (!text) { endEdit(); confirmDelete(m); return; }
  const named_ = ONIX_AT.test(text); ONIX_AT.lastIndex = 0; const raw = text.replace(ONIX_AT, state.agentMention), c = msgIndex.get(m.id) || state.P;
  tx.textContent = ""; editing.was = ""; endEdit(); m.content = named_ ? addressedEcho(text.replace(ONIX_AT, "").trim()) : echoHTML(text); m.edited = true; updateMessage(c, m); S.play("send", 0, at); ready();
  try { await api("PATCH", `messages/${m.id}`, {content: raw}); } catch (e) { note(e.message); } }
/* deleting cannot be undone, so it asks once, in a dialog; the server's event takes the message away */
function confirmDelete(m) { if (dialogOpen) return; dialogOpen = true; const d = el("div", "dlg raised float", `<p style="font:var(--t-strong)">Delete this message?</p><p style="color:var(--ink-2);margin-top:var(--s-2)">It is removed for everyone, and cannot be brought back.</p><div class="btns"><button class="btn" data-no>Cancel</button><button class="go" data-yes>Delete</button></div>`); d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true");
  d.style.cssText = "position:absolute;left:calc(50% - 200px);top:160px"; main.append(d); host(d); M.release(d, {y: -(T["pane-rise"] || 4)}, "touch");
  const close = () => { dialogOpen = false; d.inert = true; M.leave(d, () => d.remove()); removeEventListener("keydown", key, true); ready(); };
  const yes = async () => { close(); try { await api("DELETE", `messages/${m.id}`); } catch (e) { note(e.message); } };
  const key = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); (document.activeElement === $("[data-no]", d) ? close : yes)(); } else if (e.key === "Tab") { e.preventDefault(); const b = $$("button", d), i = b.indexOf(document.activeElement); b[(i + 1) % b.length].focus(); } };
  addEventListener("keydown", key, true); press(d, "[data-no]", close); press(d, "[data-yes]", yes); $("[data-no]", d).focus(); }   /* it stands on Cancel: Return alone never deletes */

export {buildOver, closeMenu, confirmDelete, dialogOpen, editing, endEdit, menu, msgOf, over, overAt, overHide,
  saveEdit, startEdit};
