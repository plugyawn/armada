/* INTERIM BETA, served at /beta/ (zerver/views/armada_beta.py) and deleted with this directory when the rebuild's
   conversation lands. The design prototype's interface (kit/: mock.css, kit.js, motion.js, sound.js) on this server's
   real data, grown from var/armada-overhaul/preview/. Nothing here is scripted: every row, mark, count and word on
   screen comes from the server, or from what the person just did and the server then confirms.
   Signed in by the session: reads and writes go to /json/ with the session cookie, and every write carries the
   page's CSRF token. No API key exists on this page.
   Rules kept from the prototype: read, write the whole new state, then ask the motion kit to release the difference;
   every handler ends with the field ready; nothing moves that the person did not act on. */

import {$, $$, clamp, el, esc, press, store, T} from "./util.js";
import {allKids, expanded, marks, QUESTION_TIMEOUT, state, stats, streams, topicsOf, unread} from "./state.js";
import {api, askSession, handle, on, poll, register, signedOut} from "./connection.js";
import {cacheOf, dkey, fetchMsgs, markRead, openPlace, placeFromHash, placeFromKey, tkey} from "./routing.js";
import {band, bindFrame, cz, feed, go, lane, main, nav, navFixed, navScroll, outline, panes, side, toBtn, tx} from "./frame.js";
import {answering, drawAtts, drawComposer, onixHere, onixKey, ready, syncField, upload, verb} from "./composer.js";
import {dist, drawLane, edge, firstRow, haltLane, hugAll, pin, syncJump, toHug} from "./feed.js";
import {drawNav, loadTopics, navMore, subs, toggleStream} from "./nav.js";
import {drawAsks, loadThemedMarks, runsSeen, sweepAsks, waitingRun} from "./runs.js";
import {drawHeader} from "./header.js";
import {react} from "./reactions.js";
import {closeInfo, fetchReceipts, info, openInfo, pollReceipts, RCPT_POLL, RECEIPTS, signOfLife, toggleInfo} from "./receipts.js";
import {openThread} from "./threads.js";
import {retry, send} from "./send.js";
import {onDelete, onFlags, onMessage, onReaction, onRun, onTyping, onUpdate, onWorkspaceChanged} from "./events.js";
import {drawRunState, stop, tick} from "./band.js";
import {pickEntry, setTab} from "./views.js";
import {buildOver, closeMenu, confirmDelete, editing, endEdit, menu, msgOf, over, overAt, startEdit} from "./hover.js";
import {closePicker, focusPop, pop, togglePicker} from "./model_picker.js";
import {pal, palette} from "./palette.js";
import {drawHome, drawHomeRow, fetchMentions, HOME, scanAll} from "./home.js";
import {applyPartners, hopOnce, loadPartners, me_, pack, partners, refreshPartners} from "./partners.js";
import {choosePick, drawPicker, drawPickerBand, drawPickerSide, movePick, openPicker, PARTNER, pick, putOff,
  selectCell, selectMine} from "./partner_picker.js";
import {card, closeCard, cutMoment, faceAt, holdMoment, moment, momentDue, openCard, playMoment, wireCard} from "./partner_card.js";
import {buildLab, lab} from "./lab.js";
import {wireMentions} from "./mentions.js";

/* event dispatch (connection.js): what each event type the page registers for does; a module's own types are named here */
on("message", onMessage); on("update_message", onUpdate); on("agent_run", onRun); on("reaction", onReaction);
on("update_message_flags", onFlags); on("delete_message", onDelete); on("typing", onTyping); on("workspace_changed", onWorkspaceChanged);
let fileIn = null;
/* what Tab stops on, in document order: what the browser would stop on, shown and not inert */
const tabStops = () => $$("a[href], button, input, [tabindex], [contenteditable]").filter((x) => x.tabIndex >= 0 && !x.disabled && !x.hidden && !x.closest("[inert], [hidden]") && x.getClientRects().length > 0 && getComputedStyle(x).visibility !== "hidden");

/* ---------- start ---------- */
async function boot() {
  store.del("onix");   /* the old switch for every place */
  const bootEl = el("div", "", "Loading"); bootEl.id = "boot"; document.body.append(bootEl);
  try { await Promise.all([register(), loadThemedMarks(), loadPartners()]); api("GET", "armada/session").then((r) => { if (r.armada_session && r.armada_session.agent_mention) state.agentMention = r.armada_session.agent_mention; }).catch(() => {}); }
  catch (e) { if (signedOut.done) { bootEl.remove(); return; } bootEl.innerHTML = `<p>The beta could not load: ${esc(e.message)}. <a href="/">Open the old interface</a></p>`; return; }
  bindFrame();
  /* my face opens my card, any other face that person's (partner_card.js); the picker's switch, Later, its cells; the card's controls */
  press(nav, ".row.me", () => { openCard(); ready(); }); wireCard();
  press(main, "[data-seg]", (b) => { pick.seg = b.dataset.seg; if (pick.q) { pick.q = ""; tx.textContent = ""; syncField(); } pick.sel = null; state.hd._sig = ""; drawHeader(); drawPicker(); drawPickerBand(); panes.Partner.scrollTop = 0; ready(); });
  press(main, "[data-later]", () => putOff());
  press(panes.Partner, ".pkc", (b) => { selectCell(b.dataset.sid); drawPickerBand(); ready(); });
  panes.Partner.addEventListener("dblclick", (e) => { const b = e.target.closest(".pkc"); if (b) { selectCell(b.dataset.sid); choosePick(); } });
  press(document.body, "[data-pick]", (b) => { pick.picks[b.dataset.pickfor] = b.dataset.pick; drawPickerSide(); drawPickerBand(); ready(); });
  press(document.body, ".pcard [data-change]", () => openPicker());
  press(document.body, ".pcard [data-evolve]", (b) => playMoment(b.dataset.evolve));
  press(document.body, ".pcard [data-stop]", () => { holdMoment(); ready(); });
  /* attach: the Plus opens the file chooser; a paste or a drop of files does the same */
  fileIn = el("input"); fileIn.type = "file"; fileIn.multiple = true; fileIn.hidden = true; document.body.append(fileIn); fileIn.addEventListener("change", () => { [...fileIn.files].forEach(upload); fileIn.value = ""; ready(); });
  tx.addEventListener("paste", (e) => { const f = [...(e.clipboardData && e.clipboardData.files || [])]; if (f.length) { e.preventDefault(); f.forEach(upload); } });
  addEventListener("dragover", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
  addEventListener("drop", (e) => { const f = [...(e.dataTransfer && e.dataTransfer.files || [])]; if (!f.length) return; e.preventDefault(); if (state.place && state.place.kind !== "home") f.forEach(upload); });
  tx.addEventListener("focus", () => { state.threadTx = null; });
  buildOver(); wireMentions();
  /* the field */
  toBtn.insertAdjacentHTML("beforeend", '<span class="tl">Onix</span>'); toBtn.classList.replace("sq", "w");   /* the switch says its name, in both states */
  cz.classList.remove("is-focus", "has-text", "is-off"); tx.innerHTML = ""; tx.contentEditable = "plaintext-only"; tx.classList.add("m-caret", "idle"); go.classList.remove("armed");
  let caret = 0; tx.addEventListener("input", () => { syncField(); go.classList.toggle("armed", !!tx.textContent.trim()); tx.classList.remove("idle"); clearTimeout(caret); caret = setTimeout(() => tx.classList.add("idle"), T["caret-hold"] || 600);
    if (state.place && state.place.kind === "partner") { pick.q = tx.textContent; pick.sel = null; state.hd._sig = ""; drawHeader(); drawPicker(); drawPickerBand(); } });   /* on the picker the field is its search, across every generation */
  tx.addEventListener("keydown", (e) => { if (state.place && state.place.kind === "partner" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {   /* the picker: arrows move through the grid, Return chooses, Esc clears the search */
      if (/^Arrow(Up|Down|Left|Right)$/.test(e.key)) { e.preventDefault(); movePick(e.key); drawPickerBand(); return; }
      if (e.key === "Escape" && tx.textContent) { e.preventDefault(); e.stopPropagation(); tx.textContent = ""; tx.dispatchEvent(new Event("input")); return; } }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send({at: e.timeStamp}); if (!(state.place && state.place.kind === "partner")) go.classList.remove("armed"); return; }
    if (e.key === "Escape" && editing) { e.preventDefault(); e.stopPropagation(); endEdit(); return; }
    if (e.key === "ArrowUp" && !tx.textContent.trim() && !editing && !e.shiftKey && !state.atts.length && !pop) { const m = state.P && state.P.msgs.filter((x) => x.sender_id === state.ME && typeof x.id === "number" && !x.thread_root_message_id).pop(); if (m) { e.preventDefault(); startEdit(m); } } });   /* Up in an empty field (the switch's pill alone is empty) edits my last message */
  go.addEventListener("click", (e) => { send({at: e.timeStamp}); go.classList.remove("armed"); });
  /* a press on a control does not take focus from the field */
  const KEEP = ".tab, nav .row, nav .mo, .cz button, .rc, .jump, .stop, .prog, .sec .lk, .ls > li, [data-thread], .tree .row, .line .lk, .bl > .lk, .hd button, .mo, .over .c, .menu .mi, .pop button, .pal .mi, .opt, .hrow, .dlg button, .rt, .info button, .pcard button, .pkc, .pkseg button, [data-later], [data-pick]";
  document.addEventListener("mousedown", (e) => { if (e.target.closest && e.target.closest(KEEP)) e.preventDefault(); }, true);
  /* the field is ready after everything: a press that ends anywhere outside a control or a selection hands the caret back */
  document.addEventListener("mouseup", (e) => setTimeout(() => { const a = document.activeElement; if (a === tx || (a && a.closest("#lab, input, textarea, .thr-in .tx, .pal, .dlg, .pop")) || (e.target.closest && e.target.closest("#lab")) || !getSelection().isCollapsed) return; ready(); }, 0));
  /* a press outside a menu, the picker or the palette closes it and keeps what was pressed */
  addEventListener("pointerdown", (e) => { state.pressing = true; if (moment && !e.target.closest(".pcard")) cutMoment(); else if (card && !e.target.closest(".pcard, .row.me") && !faceAt(e)) closeCard();
    if (info && !e.target.closest(".info, .rt")) closeInfo(true);
    if (menu && !e.target.closest(".menu")) closeMenu(); if (pop && !e.target.closest(".pop, .k.mode")) closePicker(); if (pal && !e.target.closest(".pal")) palette(false); }, true);
  addEventListener("pointerup", () => { state.pressing = false; }, true); addEventListener("pointercancel", () => { state.pressing = false; }, true);
  M.wire(); M.warm();
  press(navFixed, ".row[data-k]", (r) => { if (r.dataset.k === "jump") palette(); else openPlace(HOME); });
  press(navScroll, ".row", (r) => { const k = r.dataset.k; if (k.startsWith("c:")) { toggleStream(+k.slice(2)); ready(); return; } openPlace(placeFromKey(k)); });
  press(navScroll, ".mo", (b) => { allKids.add(+b.dataset.more); drawNav(); ready(); });
  press(main, ".tab", (t) => { setTab(t.dataset.tab); ready(); });
  press(main, "[data-goto]", (b) => { setTab(b.dataset.goto); ready(); });
  press(outline, "li[data-k]", (li) => { const p = placeFromKey(li.dataset.k); if (p) openPlace(p); });
  press(main, ".stop", () => stop());
  press(main, ".jump", () => { haltLane(); const ref = firstRow(), before = ref ? M.rest(ref).top : 0; state.pinned = true; pin(); syncJump(); edge(); const d = ref ? before - M.rest(ref).top : 0; if (d) M.release(lane, {y: clamp(d, -240, 240)}, "move"); ready(); });
  press(cz, ".k.to", () => { if (!state.place || !state.place.repo) return; if (onixHere()) store.del(onixKey(state.place)); else store.set(onixKey(state.place), "1"); drawComposer(); drawRunState(); S.play("tick"); ready(); });
  press(cz, ".k.mode", (b, e, ptr) => togglePicker({kbd: !ptr}));
  press(cz, ".out", () => fileIn.click());
  press(cz, "[data-unatt]", (b) => { state.atts = state.atts.filter((a) => String(a.id) !== b.dataset.unatt); drawAtts(); ready(); });
  press(lane, ".blk.ask .opt", (o, e) => { if (o.getAttribute("aria-disabled") === "true" || !waitingRun(state.P)) { ready(); return; } o.classList.add("sel"); send({text: o.dataset.answer, at: e.timeStamp, onix: true}); });   /* an option is the answer: it is sent as I would write it */
  $("#home").addEventListener("keydown", (e) => { const r = e.target; if ((e.key === "Enter" || e.key === " ") && r.matches && r.matches(".hrow[tabindex='0']")) { e.preventDefault(); r.click(); } });   /* a row with no verb is itself the button */
  press($("#home"), ".hrow, .hrow button", (r) => { const p = placeFromKey(r.dataset.k); if (!p) return; if (r.matches("[data-answer]") && p.repo) cacheOf(p).answering = true; openPlace(p); });   /* Answer: what I write there next answers Onix, once */
  press(lane, ".rc", (rc) => { const part = rc.closest(".rcs"), m = state.P.byId.get(+part.dataset.mid); if (m) react(m, {emoji_name: rc.dataset.name, emoji_code: rc.dataset.code, reaction_type: rc.dataset.type}); ready(); });
  press(lane, "[data-thread]", (b) => { openThread(+b.dataset.thread, false, {focus: true}); });
  press(lane, "[data-retry]", (b) => retry(b.dataset.retry));
  press(lane, ".rt[data-info]", (b, e, ptr) => { const id = +b.dataset.info; if (Number.isFinite(id)) toggleInfo(id, b, !ptr); else ready(); });
  press(main, "[data-info-retry]", () => { if (info) fetchReceipts(info.id, {force: true, retry: true}); });
  press($("#tree"), ".row", (r) => pickEntry(r));
  /* a double press on a message is my thumbs-up (React in the overlay does the same) */
  lane.addEventListener("dblclick", (e) => { const n = e.target.closest("[data-mid]"); if (!n || e.target.closest(".rc, button, a")) return; const m = state.P.byId.get(+n.dataset.mid); if (m) { getSelection().removeAllRanges(); react(m, {emoji_name: "+1", emoji_code: "1f44d", reaction_type: "unicode_emoji"}); } ready(); });
  feed.addEventListener("scroll", () => { edge(); if (state.tab !== "Chat") return; if (Math.abs(feed.scrollTop - state.lastSet) < 1) return; state.lastSet = -1; state.pinned = dist() < 4; syncJump();
    if (feed.scrollTop < 240 && state.P && state.P.loaded && !state.P.oldest && !state.P.loading) { const c = state.P; fetchMsgs(c, true).then(() => { if (state.P === c) drawLane({keep: true}); }); const mu = $(".more-up", lane); if (mu) mu.textContent = "Loading earlier messages"; } }, {passive: true});
  navScroll.addEventListener("scroll", navMore, {passive: true}); new ResizeObserver(navMore).observe(navScroll);
  /* a wrapped bubble keeps hugging its words when the lane's width changes, and once the faces have loaded */
  let laneW = 0; new ResizeObserver(() => { const w = lane.clientWidth; if (w === laneW) return; laneW = w; $$(".cl", lane).forEach((x) => toHug.add(x)); hugAll(); }).observe(lane);
  document.fonts.ready.then(() => { $$(".cl", lane).forEach((x) => toHug.add(x)); hugAll(); });
  feed.addEventListener("wheel", haltLane, {passive: true}); feed.addEventListener("touchstart", haltLane, {passive: true});
  band.el.addEventListener("wheel", (e) => { if (state.tab !== "Chat") return; haltLane(); feed.scrollTop += e.deltaY; }, {passive: true});
  new ResizeObserver(() => { if (state.tab === "Chat" && state.pinned) pin(); syncJump(); }).observe(feed);
  let dockH = 0; new ResizeObserver(([e]) => { const h = e.borderBoxSize ? e.borderBoxSize[0].blockSize : e.contentRect.height, was = dockH; dockH = h; if (!was || h === was || state.tab !== "Chat") return; if (state.pinned) { pin(); M.release(lane, {y: h - was}, "touch"); } M.release(band.el, {y: h - was}, "touch"); }).observe($("main > .dock"));
  new ResizeObserver(() => fitLists(side)).observe(outline);
  new ResizeObserver(() => { if (state.tab === "Chat" && state.pinned && !M.current(lane)[0].y) { pin(); edge(); } }).observe(lane, {box: "border-box"});   /* a face that loads late, an image that arrives, the room a live run keeps (the lane's padding): the end stays the end */
  addEventListener("keydown", (e) => { state.lastKey = performance.now();
    if (moment) { if (e.key === "Escape" && !moment.done && !tx.textContent.trim()) { e.preventDefault(); holdMoment(); return; } cutMoment(); }   /* any key ends the moment where it stands (the evolution happens) and goes on to the field; Esc with the field empty keeps the old form */
    if (card && e.key === "Escape") { e.preventDefault(); closeCard(); ready(); return; }
    const mod = e.metaKey || e.ctrlKey; if (mod && e.key === ".") { e.preventDefault(); stop(); return; }
    if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); palette(); return; }
    if (mod && e.key === "/") { e.preventDefault(); togglePicker({kbd: true}); return; }
    if (pop && (e.key === "ArrowDown" || e.key === "ArrowUp") && !pop.contains(document.activeElement)) { e.preventDefault(); focusPop(); return; }   /* opened with the pointer: an arrow goes into it */
    if (menu && menu._key(e)) return;
    /* Tab goes round the page: from the last stop to the first (the nav), never onto the page itself in between */
    if (e.key === "Tab" && !e.defaultPrevented && !mod && !e.altKey) { const all = tabStops(), i = all.indexOf(document.activeElement);
      if (all.length && !e.shiftKey && i === all.length - 1) { e.preventDefault(); all[0].focus(); return; }
      if (all.length && e.shiftKey && i === 0) { e.preventDefault(); all[all.length - 1].focus(); return; } }
    if (pop && e.key === "Escape") { e.preventDefault(); closePicker(); return; }
    if (e.key === "Escape" && info) { e.preventDefault(); closeInfo(); return; }
    /* I: Info on the message I stand on (its turn in the Tab order, or under the pointer with its controls up) */
    if (RECEIPTS && !mod && !e.altKey && (e.key === "i" || e.key === "I") && !info) { const a = document.activeElement, t = a && a.dataset && a.dataset.door ? a : a && over && over.contains(a) ? overAt : null, m = t && msgOf(t);
      if (m && typeof m.id === "number") { e.preventDefault(); openInfo(m.id, a, true); return; } }
    if (e.key === "Escape") { if (lab.classList.contains("open")) lab.classList.remove("open"); else if (editing) endEdit(); else if (state.threadTx) state.threadTx = null; else if (answering()) { state.P.answering = false; drawRunState(); } ready(); return; }
    const onControl = !!document.activeElement && document.activeElement !== document.body && document.activeElement.matches("button, a, [role=button], [tabindex]:not(#pane)");   /* Space there presses it */
    if (e.key === " " && onControl) return;
    if (!mod && !e.altKey && e.key.length === 1 && state.place && state.place.kind === "home" && !pal && !(document.activeElement && document.activeElement.closest("#lab, input, textarea, [contenteditable]"))) { e.preventDefault(); palette(); const inp = $(".pal .pin"); if (inp) { inp.textContent = e.key; inp.dispatchEvent(new Event("input")); const sel = getSelection(); sel.selectAllChildren(inp); sel.collapseToEnd(); } return; }   /* Home has no field: a letter typed there goes to Jump to */
    if (!mod && !e.altKey && e.key.length === 1 && state.tab === "Chat" && document.activeElement !== tx && !(document.activeElement && document.activeElement.closest("#lab, input, textarea"))) ready(); });   /* a letter typed anywhere goes to the field */
  addEventListener("popstate", () => { const p = placeFromHash(); if (p) openPlace(p, {keepHash: true}); });
  addEventListener("hashchange", () => { const p = placeFromHash(); if (p && (!state.place || p.key !== state.place.key)) openPlace(p, {keepHash: true}); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) return; askSession(); if (state.P) markRead(state.P); signOfLife(); refreshPartners({wait: 500}); });
  setInterval(() => refreshPartners({wait: 0}), 120000);   /* the team's partners: a choice or an evolution elsewhere shows here within two minutes */
  addEventListener("focus", () => { askSession(); signOfLife(); });
  setInterval(() => pollReceipts(), RCPT_POLL);
  setInterval(() => tick(false), 500);
  setInterval(sweepAsks, 5000);   /* a question past the server's wait stops reading as waiting, with no event to say so */
  setInterval(() => { if (state.P && state.P.ctx && state.hd) { state.hd._sig = ""; drawHeader(); } }, 60000);
  setInterval(() => { if (state.place && state.place.kind === "home") drawHome(); }, 60000);
  buildLab();
  /* where to: the address, then where I last was, then the first repository's latest topic */
  for (const sid of expanded) if (streams.has(sid)) loadTopics(sid); else expanded.delete(sid);
  let p = placeFromHash() || (store.get("place") && placeFromKey(store.get("place")));
  if (!p) { const s = subs()[0]; if (s) { expanded.add(s.stream_id); await loadTopics(s.stream_id); const t = (topicsOf.get(s.stream_id) || [])[0]; if (t) p = placeFromKey(tkey(s.stream_id, t.name)); } }
  if (p && p.kind === "topic") { await loadTopics(p.stream_id); const t = (topicsOf.get(p.stream_id) || []).find((x) => x.name.toLowerCase() === p.topic.toLowerCase()); if (t) p.topic = t.name; }
  if (!p && state.dms[0]) p = placeFromKey(dkey(state.dms[0].user_ids));
  /* the first visit after partners arrived: the page opens on the picker, and keeps where it was going for afterwards */
  if (pack && me_() && !me_().species && !me_().later && (!p || p.kind !== "partner")) { pick.back = p; p = PARTNER; }
  if (p && p.kind === "partner") selectMine();
  bootEl.remove();
  if (p) openPlace(p, {replace: true}); else drawNav();
  setTimeout(momentDue, 3000);   /* an evolution ready at load plays after the first idle 3 s */
  poll();
  /* Home needs every worktree's runs and my unread mentions: asked once, after the first place is drawn, then kept by events */
  setTimeout(() => { scanAll(); if (!state.place || state.place.kind !== "home") fetchMentions(); }, 300);   /* Home, opened first, has asked for them already */
}
window.armadaBeta = {get partners() { return partners; }, get pack() { return !!pack; }, applyPartners, refreshPartners, openCard, openPicker, playMoment, get moment() { return moment; }, hopOnce, stats, get place() { return state.place; }, get pinned() { return state.pinned; }, get cache() { return state.P; }, openPlace: (k) => openPlace(placeFromKey(k)), tkey, dkey, streams, send, stop, setTab, marks, unread, runsSeen, palette, togglePicker, openThread, startEdit, confirmDelete, get verb() { return verb; }, get atts() { return state.atts; }, QUESTION_TIMEOUT, sweepAsks, handle, redraw: () => { if (state.place && state.place.kind !== "home") { drawRunState(true); drawAsks(); } drawNav(); drawHomeRow(); }};   /* for checks from a driver */
document.addEventListener("DOMContentLoaded", boot);

/* the seams (README.md): what a module built on this page relies on; it imports each from the module that holds it */
export {api, handle, on, openPlace, ready, register, send, state, tx};
