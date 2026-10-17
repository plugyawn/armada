/* INTERIM BETA (README.md): plugins (design/r9/plugins, sections 4 and 5). A plugin draws no HTML of its own: each of
   its widgets is a document in Armada's artifact vocabulary, with `rest` (its one line in the nav) and `list` blocks
   (tools/armada/plugin_contract.json), which the plugin pushes and the server forwards as armada_widget events; the
   installs come as armada_plugin events, and my own view of each widget (in my nav or not, its place in the group, my
   label, its figure at rest, the rows I keep, my alerts) as armada_widget_preference events, drawn through
   widget_view.js. All three are in /register's snapshot (zerver/lib/armada_plugin_events.py). What is drawn at load is
   here: the nav's widget group at rest (a glyph, the label, one trailing thing in a cell as wide as the widget's sizer;
   the "+" its last row), the slots of the list a linked widget adds to its place's column (from the snapshot), a
   plugin's words on the plane, and the place for its commands in Jump to. The list's rows and those commands are the
   chunk's, fetched when the page is idle. What a press opens is plugin_view.js, a chunk (chunks.js): the sheet beside
   the nav, an item that borrows the column, and the Plugins place with each plugin's page. An update patches what is
   drawn in place: a figure rolls in its cell, a mark changes into another, and nothing else moves. */

import {$, $$, esc, press} from "./util.js";
import {pluginBots, state, streams} from "./state.js";
import {hhmm, now} from "./time.js";
import {api} from "./connection.js";
import {main, nav, outline, side} from "./frame.js";
import {send} from "./send.js";
import {chunk, loadedChunk} from "./chunks.js";
import {drawSide} from "./column.js";
import {openPlace, placeFromKey} from "./routing.js";
import {drawHeader} from "./header.js";
import {faceOf} from "./faces.js";
import {closeArtifact, dataAt, isObj, text} from "./artifacts.js";
import {byMyOrder, inMyColumn, inMyNav, viewOf} from "./widget_view.js";

/* ---------- what the server holds ---------- */
const installs = new Map();   /* install id -> the install as everyone sees it: name, glyph, state, widgets declared, widget_defaults, links */
const widgets = new Map();    /* scope -> the latest document for one widget and scope: {install_id, widget, link_id, item, document, fed_at, stale, ...} */
const scopeOf = (install_id, widget, link_id, item) => `${install_id}/${widget}/${link_id || 0}/${item || ""}`;
const scopeOfState = (s) => scopeOf(s.install_id, s.widget, s.link_id, s.item);
const prefs = new Map();      /* "<install>/<widget>" -> my own view of the widget (armada_widget_preference), mine alone */
const prefOf = (install_id, widget) => prefs.get(`${install_id}/${widget}`) || null;
const STALE_CADENCES = 3, DEFAULT_CADENCE = 60;   /* zerver/lib/armada_widgets.py: a widget not fed for three times its cadence is stale */

function keepInstall(p) { installs.set(p.id, p); for (const [bot, x] of pluginBots) if (x.install_id === p.id && bot !== p.bot_user_id) pluginBots.delete(bot);
  if (p.bot_user_id) pluginBots.set(p.bot_user_id, {install_id: p.id, name: p.name, glyph: p.glyph}); }
function dropInstall(id) { installs.delete(id); for (const [bot, x] of pluginBots) if (x.install_id === id) pluginBots.delete(bot); for (const [k, s] of widgets) if (s.install_id === id) widgets.delete(k); }
/* /register's snapshot (connection.js): every install I see and the documents of the places I can see */
function applyPlugins(r) {
  installs.clear(); pluginBots.clear(); widgets.clear(); prefs.clear();
  for (const p of r.armada_plugins || []) keepInstall(p);
  for (const s of r.armada_widgets || []) widgets.set(scopeOfState(s), s);
  for (const x of r.armada_widget_preferences || []) prefs.set(`${x.install_id}/${x.widget}`, x);
  if (groupEl) { drawWidgets(); watchStale(); }
}

/* ---------- a widget's document, read ---------- */
/* a list item's state is one of the run marks, by the sprite's names; the beta's kit names two of them as the conversation does */
const MARK = {done: "done", live: "live", waiting: "queued", needs: "needs", failed: "failed", paused: "paused", stopped: "cancelled"};
const markOf = (state_) => MARK[state_] || null;
/* a row in the first ink: what is going on now; done, waiting, stopped and paused rows are in the second */
const rowOn = (row) => !["done", "waiting", "stopped", "paused"].includes(row.state);
/* a list block's rows, from its own items or from the data it is bound to, as the server reads them (armada_widgets.py _rows) */
function rowsOf(doc, block) {
  const bound = block.bind ? dataAt(doc.data || {}, block.bind) : undefined, items = Array.isArray(bound) ? bound : Array.isArray(block.items) ? block.items : [];
  return items.filter((x) => isObj(x) && typeof x.key === "string" && typeof x.label === "string").slice(0, 32);
}
const listsOf = (doc) => (doc && Array.isArray(doc.blocks) ? doc.blocks.filter((b) => isObj(b) && b.type === "list") : []);
const figureText = (v) => (typeof v === "number" ? v.toLocaleString("en", {maximumFractionDigits: 6}).replace(/^-/, "−") : text(v));
const glyphOf = (name) => (GLYPHS[name] ? name : "plugin");   /* the contract names only the sprite's glyphs; one this page lacks is a plugin's */

/* whether a document may be out of date: the server says so, or it has not been fed for three times its cadence */
function staleOf(s, declared, at = now() / 1000) { return !!s.stale || at - s.fed_at > STALE_CADENCES * ((declared && declared.cadence) || DEFAULT_CADENCE); }
/* the one trailing thing of a widget's nav row, by the nav's own priority: needs you, failed, then the paused mark of a
   widget not heard from, then its figure. An install that failed says so before any document; one waiting for its
   settings shows the dashed ring until it has sent one. */
function restOf(p, declared, s, at, alert = null) {
  if (p.state === "failed") return {kind: "failed", text: "", tip: `${p.name} stopped. Its page has its last lines.`};
  if (!s) return p.state === "waiting" ? {kind: "queued", text: "", tip: `${p.name} waits for its settings`} : {kind: "none", text: "", tip: ""};
  const doc = s.document || {}, rest = isObj(doc.rest) ? doc.rest : null, news = rest && rest.news ? dataAt(doc.data || {}, rest.news) : null;
  if (news === "needs") return {kind: "needs", text: "", tip: ""};
  if (alert) return {kind: "needs", text: "", tip: `${alert.title}: ${alert.threshold}`};   /* one of my alerts is met: news for me alone */
  if (news === "failed") return {kind: "failed", text: "", tip: ""};
  if (staleOf(s, declared, at)) return {kind: "paused", text: "", tip: `Last heard ${hhmm(s.fed_at)}`};
  const v = rest ? figureText(dataAt(doc.data || {}, rest.figure)) : "";
  return v ? {kind: "figure", text: v, tip: ""} : {kind: "none", text: "", tip: ""};
}

/* ---------- the nav's widget group ---------- */
/* the widgets in everyone's nav: an install that runs (or is getting there), each widget its team shows there, the newest
   install first, so that one that arrives takes the group's top and leaves every other row where it was */
function navWidgets() {
  const out = [];
  for (const x of teamWidgets()) if (inMyNav(x.w, (x.p.widget_defaults || {})[x.w.key], x.pref)) out.push(seen(x, null));
  return byMyOrder(out, (x) => x.pref);
}
/* every widget of every install that runs, newest install first, with my preference for it */
function teamWidgets() {
  const out = [];
  for (const p of [...installs.values()].sort((a, b) => b.id - a.id)) { if (p.disabled || p.state === "draft") continue;
    for (const w of p.widgets || []) out.push({p, w, id: `${p.id}/${w.key}`, pref: prefOf(p.id, w.key)}); }
  return out;
}
/* a widget as I see it in one scope: its document passed through my view (zerver keeps one document; my label, figure,
   rows and alerts are drawn from it here), and my label */
function seen(x, link) {
  const raw = widgets.get(scopeOf(x.p.id, x.w.key, link ? link.id : null, "")), v = viewOf(x.w, raw ? raw.document : null, x.pref);
  return {...x, link, label: v.label, alert: v.alert, s: raw ? {...raw, document: v.doc} : undefined};
}
const sizerOf = (x) => { const r = x.s && x.s.document && isObj(x.s.document.rest) ? x.s.document.rest : null; return r && typeof r.sizer === "string" ? r.sizer : "88"; };
const restCell = (rest) => (rest.kind === "figure" ? `<span class="wv fig">${fig(rest.text)}</span>` : rest.kind === "none" ? "" : mk(rest.kind));
const restLabel = (label, rest) => `${label}${rest.kind === "figure" ? `: ${rest.text}` : rest.kind === "needs" ? ": needs you" : rest.kind === "failed" ? ": failed" : rest.kind === "paused" ? `: ${rest.tip}` : ""}`;
let groupEl = null, groupSig = "";
function rowHTML(x, at) { const rest = restOf(x.p, x.w, x.s, at, x.alert);
  return `<button class="row wg" data-w="${esc(x.id)}" aria-haspopup="dialog" aria-expanded="${openWidget === x.id}" aria-label="${esc(restLabel(x.label, rest))}"${rest.tip ? ` data-tip="${esc(rest.tip)}"` : ""}>${ic(glyphOf(x.p.glyph))}<span class="lab">${esc(x.label)}</span><span class="wf"><i class="sz">${esc(sizerOf(x))}</i>${restCell(rest)}</span></button>`; }
/* the group's last row, always there (ROUND2.md, section E): the "+" and the word Widgets in the second ink, with no count,
   which would read as unread; its menu holds what I hid, what the team offers, and making one */
const plusRowHTML = () => `<button class="row wg plus" data-w="plus" aria-haspopup="menu" aria-expanded="${openWidget === "plus"}">${ic("plus")}<span class="lab">Widgets</span></button>`;
/* the group, drawn whole when its rows change (an install, a widget shown or hidden, a new sizer); else each row's trailing
   thing changes where it stands */
function drawWidgets() {
  if (!groupEl) return; const list = navWidgets(), at = now() / 1000, sig = list.map((x) => `${x.id}|${x.label}|${x.p.glyph}|${sizerOf(x)}`).join(";");
  if (sig !== groupSig) { groupSig = sig; groupEl.innerHTML = list.map((x) => rowHTML(x, at)).join("") + plusRowHTML(); host(groupEl); snapCells(); return; }
  for (const x of list) patchRow(x, at);
}
const rowEl = (id) => $$(".row.wg", groupEl).find((r) => r.dataset.w === id) || null;
/* a figure rolls in its cell; a mark changes into another where it stands; a figure and a mark cross over in the cell */
function patchRow(x, at) {
  const r = rowEl(x.id); if (!r) return; const rest = restOf(x.p, x.w, x.s, at, x.alert), cell = $(".wf", r), was = r._rest || readRest(cell);
  r.setAttribute("aria-label", restLabel(x.label, rest)); if (rest.tip) r.dataset.tip = rest.tip; else delete r.dataset.tip; r._rest = rest;
  if (was.kind === rest.kind && was.text === rest.text) return;
  const old = $(":scope > .wv, :scope > .mk", cell);
  if (was.kind === "figure" && rest.kind === "figure" && old) { M.figure(old, rest.text); return; }
  if (old && old.matches(".mk") && rest.kind !== "figure" && rest.kind !== "none") { M.cross(old, mk(rest.kind)); return; }
  if (old) { old.classList.remove("wv", "mk"); old.inert = true; M.leave(old, () => old.remove()); }
  if (rest.kind !== "none") { cell.insertAdjacentHTML("beforeend", restCell(rest)); M.appear(cell.lastElementChild); }
}
function readRest(cell) { const v = $(":scope > .wv", cell), m = $(":scope > .mk", cell); return v ? {kind: "figure", text: v.textContent} : m ? {kind: [...m.classList].find((c) => MARK_NAMES.has(c)) || "none", text: ""} : {kind: "none", text: ""}; }
const MARK_NAMES = new Set(["needs", "failed", "paused", "queued"]);
/* the cell is a whole number of pixels wide, read from its trailing edge, so the label beside it starts on a pixel (as the
   kit's snap() does); measured once its faces have loaded, after the row is written */
function snapCells() { const cells = $$(".wf", groupEl); if (!cells.length || !document.fonts) return; document.fonts.ready.then(() => requestAnimationFrame(() => { for (const c of cells) { if (!c.isConnected) continue; c.style.width = ""; const w = c.getBoundingClientRect().width; if (w % 1) c.style.width = Math.ceil(w) + "px"; } })); }

/* a widget that goes unheard turns paused at the moment it does, with no event to say so: one timer, for the soonest */
let staleTimer = 0;
function watchStale() {
  clearTimeout(staleTimer); let soonest = Infinity; const at = now() / 1000;
  for (const s of widgets.values()) { if (s.stale) continue; const p = installs.get(s.install_id), w = p && (p.widgets || []).find((x) => x.key === s.widget); if (!w) continue;
    const due = s.fed_at + STALE_CADENCES * (w.cadence || DEFAULT_CADENCE); if (due > at) soonest = Math.min(soonest, due); }
  if (soonest < Infinity) staleTimer = setTimeout(() => { drawWidgets(); patchColumn(); watchStale(); }, Math.min(2 ** 31 - 1, Math.ceil((soonest - at) * 1000) + 50));
}

/* ---------- the column: the list a linked widget adds to its place ---------- */
/* the links that hold in a topic: a link to its channel holds for every worktree under it; one to the topic, for it alone,
   and it is the one shown when both are there */
function linkHere(p, place) { const links = (p.links || []).filter((l) => l.stream_id === place.stream_id && (l.topic == null || l.topic.toLowerCase() === place.topic.toLowerCase()));
  return links.find((l) => l.topic != null) || links[0] || null; }
function columnWidgets(place) {
  const out = []; if (!place || place.kind !== "topic") return out;
  for (const x of teamWidgets().sort((a, b) => a.p.id - b.p.id)) { const link = linkHere(x.p, place); if (!link || !inMyColumn(x.w, (x.p.widget_defaults || {})[x.w.key], x.pref)) continue; out.push(seen(x, link)); }
  return out;
}
/* how many rows a linked list holds in the column, at most three: its document's, else its declaration's, from the
   snapshot */
const slotsOf = (x) => { const block = listsOf(x.s && x.s.document)[0]; return Math.max(1, Math.min(3, (block && block.slots) || x.w.column_slots || 3)); };
/* the column's sections for the place in view (column.js draws them after Changes): a section like Changes, its slots
   fixed whatever it holds. Its rows are the plugin chunk's (plugin_view.js): until the chunk is here, each section holds
   its label and its slots, taken from the snapshot, so the rows arrive into a space already there and nothing moves. */
let sectionsAsked = false;
function pluginSectionsHTML(place) {
  const v = view(); if (v) return v.sectionsHTML(place);
  const list = columnWidgets(place); if (!list.length) return "";
  if (!sectionsAsked) { sectionsAsked = true; viewReady().then(() => { sectionsAsked = false; drawSide(); }, () => { sectionsAsked = false; }); }
  return list.map((x) => `<section class="sec slots plug" data-wscope="${esc(scopeOf(x.p.id, x.w.key, x.link.id, ""))}" style="--slots:${slotsOf(x)}"><div class="sh"><span>${esc(x.label)}</span><span class="e"></span></div><ul class="ls"></ul></section>`).join("");
}
/* a document for a list in view: the chunk patches its rows; until it is here, the column is drawn again, from the
   snapshot */
function patchColumn(scope) { const v = view(); if (v) v.patchColumn(scope); else if (columnWidgets(state.place).length) drawSide(); }

/* ---------- what is open: the sheet from a nav row, an item in the column (plugin_view.js draws them) ---------- */
let openWidget = null;   /* the nav widget whose sheet is open, "<install>/<key>" */
const setOpenWidget = (id) => { openWidget = id; for (const r of $$(".row.wg", groupEl || document.createElement("div"))) r.setAttribute("aria-expanded", String(r.dataset.w === id)); };
/* what the chunk is handed when it arrives (chunks.js hands it the page's helpers; this, the store and its reading): an
   artifact's blocks are drawn by the artifact's own chunk (and a scene by the scene's), which arrive with it */
const pluginsPage = () => ({installs, widgets, scopeOf, rowsOf, listsOf, markOf, rowOn, figureText, glyphOf, staleOf, restOf, navWidgets, linkHere,
  setOpenWidget, get openWidget() { return openWidget; }, rowEl, columnWidgets, slotsOf, showWidget, outline, get place() { return state.place; }, drawWidgets, refreshPlace,
  prefOf, setPreference, teamWidgets, seen, inMyNav, inMyColumn,
  artifactView: () => loadedChunk("artifact"), sceneView: () => loadedChunk("scene"),
  api, press, send, openPlace, placeFromKey, side, main, streams, drawSide, drawHeader, closeArtifact, faceOf});
/* the chunk's view, made once it has arrived (null before: a press then waits for it, and does nothing if another press,
   Esc or leaving the place came first, as chunks.js says for every chunk) */
let viewApi = null;
const view = () => { if (!viewApi) { const bind = loadedChunk("plugins"); if (bind && loadedChunk("artifact") && loadedChunk("scene")) viewApi = bind(pluginsPage()); } return viewApi; };
let wanted = 0;
const forgetWanted = () => { wanted++; };
const viewReady = () => Promise.all([chunk("plugins"), chunk("artifact"), chunk("scene")]);
function withView(fn) { const v = view(); if (v) { fn(v); return; } const ask = ++wanted; viewReady().then(() => { if (ask === wanted) fn(view()); }, () => {}); }

/* ---------- a plugin's words on the plane ---------- */
/* its bot speaks as Onix does and as the minis do (statement 42): no bubble, and its glyph before its name (feed.js) */
const pluginWho = (id) => { const x = pluginBots.get(id); return x ? `${ic(glyphOf(x.glyph))}<span>${esc(x.name)}</span>` : ""; };

/* ---------- live ---------- */
function onPluginEvent(ev) {
  const id = ev.op === "remove" ? ev.install_id : ev.plugin && ev.plugin.id; if (!id) return;
  if (ev.op === "remove") { dropInstall(id); const v = view(); if (v) v.forget(id); } else keepInstall(ev.plugin);
  drawWidgets(); watchStale(); if (state.place && state.place.kind === "topic") drawSide(); refreshPlace();
  const v = view(); if (v) v.update("");   /* the "+" menu, if it is open: a widget the team now offers, or one that went */
}
function onWidgetEvent(ev) {
  const scope = scopeOf(ev.install_id, ev.widget, ev.link_id, ev.item);
  if (ev.op === "remove") widgets.delete(scope); else { const {type, op, id, ...s} = ev; widgets.set(scope, s); }
  if (!ev.link_id && !ev.item) drawWidgets(); else if (!ev.item) patchColumn(scope);
  const v = view(); if (v) v.update(scope); watchStale();
}
/* my own view of a widget changed (here or on another of my devices): the nav, the column and the open sheet are
   drawn from it again, each in place where its rows are the same */
function onPreferenceEvent(ev) {
  if (ev.op === "remove") prefs.delete(`${ev.install_id}/${ev.widget}`); else prefs.set(`${ev.preference.install_id}/${ev.preference.widget}`, ev.preference);
  drawPreferences(ev.op === "remove" ? ev.install_id : ev.preference.install_id, ev.op === "remove" ? ev.widget : ev.preference.widget);
}
function drawPreferences(install_id, widget) {
  drawWidgets(); if (state.place && state.place.kind === "topic") drawSide();
  const v = view(); if (v) v.update(scopeOf(install_id, widget, null, ""));
}
/* a change to my view, whole in the frame of the press: written here at once, then sent; the server's event confirms it,
   and a refusal puts back what was there and says why */
async function setPreference(install_id, widget, changes, from) {
  const key = `${install_id}/${widget}`, was = prefs.get(key) || null, reset = [], params = {};
  const next = {install_id, widget, shown: null, position: null, label: "", rest_figure: "", filters: {}, alerts: {}, in_column: null, ...was};
  for (const [field, value] of Object.entries(changes)) { next[field] = value;
    if (value === null) reset.push(field); else params[field] = typeof value === "string" ? value : JSON.stringify(value); }
  if (reset.length) params.reset = JSON.stringify(reset);
  prefs.set(key, next); drawPreferences(install_id, widget);
  try { await api("PATCH", `armada/widgets/${install_id}/${widget}/preference`, params); }
  catch (e) { if (prefs.get(key) === next) { if (was) prefs.set(key, was); else prefs.delete(key); drawPreferences(install_id, widget); } M.say(from && from.isConnected ? from : nav, e.message, 2400); }
}

/* the Plugins place in view, drawn again when an install changes */
function refreshPlace() { if (!state.place || state.place.kind !== "plugins") return; const v = view(); if (v) v.refresh(); }

/* ---------- the Plugins place and a plugin's page: keys, addresses, the header and the column ---------- */
function pluginPlaceFromKey(key) { if (key === "plugins") return {key, kind: "plugins", view: "store", install_id: 0, repo: false};
  const m = /^plugin:(\d+)$/.exec(key); return m ? {key, kind: "plugins", view: "page", install_id: +m[1], repo: false} : null; }
const pluginHash = (p) => (p.view === "store" ? "#plugins" : `#plugin/${p.install_id}`);
function pluginPlaceFromHash(h) { if (h === "#plugins") return pluginPlaceFromKey("plugins"); const m = /^#plugin\/(\d+)$/.exec(h); return m ? pluginPlaceFromKey(`plugin:${m[1]}`) : null; }
/* routing.js has drawn the frame for the place and hidden the field; the place's pane is drawn once its chunk is here */
function openPlugins(p) { withView((v) => { if (state.place === p) { v.openPlace(p); drawHeader(); drawSide(); } }); }
function pluginsHead() { const v = view(); return v ? v.head(state.place) : {kind: "plain", title: "Plugins", say: "", checklist: {title: state.place.view === "store" ? "Installed" : "Recent"}}; }
function pluginsSideHTML() { const v = view(); return v ? v.sideHTML(state.place) : ""; }

/* Jump to: Plugins, each plugin by name, and one command a widget in the nav ("Show GPUs"), from the plugin chunk,
   which is fetched when the page is idle; until it is here, none */
function pluginItems() { const v = view(); return v ? v.items() : []; }
/* leaving a place: the column an item borrowed is given back, the sheet closes, and a press still waiting for the chunk is forgotten */
function closeWidgetViews() { forgetWanted(); const v = viewApi; if (v) v.leave(); }
/* a widget's sheet, opened from its row or from Jump to; a press on the open one closes it */
function showWidget(id, kbd) { if (openWidget === id) { const v = view(); if (v) v.closeSheet(); return; } withView((v) => v.openSheet(id, kbd)); }

/* ---------- wired once at boot, after the frame (host.js) ---------- */
function wirePlugins() {
  groupEl = document.createElement("div"); groupEl.className = "wgs"; groupEl.setAttribute("role", "group"); groupEl.setAttribute("aria-label", "Widgets"); nav.insertBefore(groupEl, $(":scope > .row.me", nav));
  groupSig = ""; drawWidgets(); watchStale();
  press(groupEl, ".row.wg:not(.plus)", (r, e, ptr) => showWidget(r.dataset.w, !ptr));
  /* a widget row's own menu: a right press, or the menu key or Shift F10 on it */
  groupEl.addEventListener("contextmenu", (e) => { const r = e.target.closest(".row.wg:not(.plus)"); if (!r) return; e.preventDefault(); withView((v) => v.openRowMenu(r.dataset.w, false)); });
  press(groupEl, ".row.wg.plus", (r, e, ptr) => { if (openWidget === "plus") { const v = view(); if (v) v.closeSheet(); return; } withView((v) => v.openMenu(!ptr)); });
  /* the rows are nav rows: Up and Down move between them, Home and End go to the ends; Return and Space press one */
  groupEl.addEventListener("keydown", (e) => { const rows = $$(".row.wg", groupEl), i = rows.indexOf(document.activeElement); if (i < 0) return;
    if ((e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) && !rows[i].matches(".plus")) { e.preventDefault(); const id = rows[i].dataset.w; withView((v) => v.openRowMenu(id, true)); return; }
    const to = {ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: rows.length - 1}[e.key]; if (to === undefined || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault(); const r = rows[Math.max(0, Math.min(rows.length - 1, to))]; M.focusKbd(r); });
}

export {applyPlugins, closeWidgetViews, glyphOf, installs, listsOf, markOf, onPluginEvent, onPreferenceEvent, onWidgetEvent, prefs, setPreference, openPlugins, pluginHash, pluginItems,
  pluginPlaceFromHash, pluginPlaceFromKey, pluginsHead, pluginSectionsHTML, pluginsSideHTML, pluginWho, restOf, rowsOf, view as pluginView, widgets,
  wirePlugins};
