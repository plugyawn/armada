/* INTERIM BETA (README.md): Jump to (⌘K). */

import {$, $$, clamp, el, esc, press, T} from "./util.js";
import {people, repoStreams, state, topicsOf} from "./state.js";
import {dkey, openPlace, placeFromKey, tkey, topicLabel} from "./routing.js";
import {main} from "./frame.js";
import {faceOf, nameOf} from "./faces.js";
import {ready} from "./composer.js";
import {loadTopics, subs} from "./nav.js";
import {liveRun} from "./runs.js";
import {stop} from "./band.js";
import {setTab} from "./views.js";
import {closeMenu, overHide} from "./hover.js";
import {closePicker} from "./model_picker.js";
import {me_, pack} from "./partners.js";
import {openPicker} from "./partner_picker.js";

/* ---------- Jump to (⌘K): every place I can go to, by name ---------- */
let pal = null;
async function loadAllTopics() { const list = subs().filter((s) => !topicsOf.has(s.stream_id)); let i = 0; const work = async () => { while (i < list.length) await loadTopics(list[i++].stream_id, {quiet: true}); }; await Promise.all([work(), work()]); if (pal) pal._draw(); }
function places_() { const out = [{key: "home", label: "Home", glyph: ic("home"), cap: ""}];
  for (const s of subs()) { const repo = repoStreams.has(s.stream_id); for (const t of topicsOf.get(s.stream_id) || []) out.push({key: tkey(s.stream_id, t.name), label: topicLabel(t.name), glyph: ic(repo ? "repo" : s.invite_only ? "lock" : "hash"), cap: s.name, hay: `${s.name} ${t.name}`}); }
  const seen = new Set(); for (const d of state.dms) { const k = dkey(d.user_ids); seen.add(k); const ids = d.user_ids.length ? d.user_ids : [state.ME]; out.push({key: k, label: ids.map(nameOf).join(", "), glyph: faceOf(ids[0], "s20"), cap: "Direct message"}); }
  for (const u of people.values()) { if (u.bot || u.id === state.ME || !u.active) continue; const k = dkey([u.id]); if (seen.has(k)) continue; out.push({key: k, label: u.name, glyph: faceOf(u.id, "s20"), cap: "Direct message"}); }
  if (state.place && state.place.repo) for (const v of ["README", "Files", "Changes"]) out.push({cmd: () => setTab(v), label: `Open ${v}`, glyph: ic(v === "README" ? "doc" : v === "Files" ? "folder" : "diff"), cap: topicLabel(state.place.topic)});
  if (state.P && liveRun(state.P)) out.push({cmd: stop, label: "Stop the run", glyph: ic("stop"), cap: "⌘."});
  if (pack) out.push({cmd: openPicker, label: me_() && me_().species ? "Change partner" : "Choose your partner", glyph: faceOf(state.ME, "s20"), cap: ""});
  out.push({cmd: () => S.set(!S.on()), label: S.on() ? "Turn sound off" : "Turn sound on", glyph: ic(S.on() ? "mute" : "sound"), cap: ""}, {cmd: () => (location.href = "/"), label: "Open the old interface", glyph: ic("open"), cap: ""});
  return out; }
function score(it, q) { if (!q) return it.key === "home" ? 0 : 1; const l = it.label.toLowerCase(), h = (it.hay || it.label + " " + it.cap).toLowerCase(); if (l.startsWith(q)) return 0; if (new RegExp("(^|[\\s/_-])" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(l)) return 1; if (l.includes(q)) return 2; if (h.includes(q)) return 3; const words = q.split(/\s+/).filter(Boolean); return words.length > 1 && words.every((w) => h.includes(w)) ? 4 : -1; }
function palette(open = !pal) { if (!open) { if (pal) { const p = pal; pal = null; p.inert = true; M.leave(p, () => p.remove()); ready(); } return; } if (pal) return; closePicker(); closeMenu(); overHide();
  const p = pal = el("div", "pal raised float", `<div class="in">${ic("search", "i2")}<span contenteditable="plaintext-only" class="pin m-caret" role="combobox" aria-label="Jump to" aria-expanded="true"></span><span class="pph">Jump to a worktree, channel or person</span></div><div class="list" role="listbox"></div>`);
  p.style.cssText = "position:absolute;left:calc(50% - 280px);top:96px"; main.append(p); host(p);
  const inp = $(".pin", p), list = $(".list", p); let hi = 0, shown = [], all = places_();
  const draw = () => { const onRow = list.contains(document.activeElement), q = inp.textContent.trim().toLowerCase(); $(".pph", p).hidden = !!q; all = places_(); shown = all.map((it, i) => [it, score(it, q), i]).filter((x) => x[1] >= 0).sort((a, b) => a[1] - b[1] || a[2] - b[2]).slice(0, 8).map((x) => x[0]); hi = clamp(hi, 0, Math.max(0, shown.length - 1));
    list.innerHTML = shown.length ? shown.map((it, i) => `<button class="mi${i === hi ? " hl" : ""}" tabindex="${i === hi ? 0 : -1}" role="option" aria-selected="${i === hi}">${it.glyph}<span class="lab">${esc(it.label)}</span><span class="k">${esc(it.cap || "")}</span></button>`).join("") : '<span class="none">Nothing by that name</span>'; host(list);
    if (onRow) { const r = $$(".mi", list)[hi]; if (r) r.focus({preventScroll: true}); else inp.focus(); } };   /* a redraw (more topics arrived) keeps the row I stand on */
  const mark = (focus) => $$(".mi", list).forEach((x, i) => { x.classList.toggle("hl", i === hi); x.tabIndex = i === hi ? 0 : -1; x.setAttribute("aria-selected", String(i === hi)); if (focus && i === hi) M.focusKbd(x); });
  const toField = () => { inp.focus(); const sel = getSelection(); sel.selectAllChildren(inp); sel.collapseToEnd(); };
  p._draw = draw;
  const go_ = (it) => { palette(false); if (!it) return; if (it.cmd) it.cmd(); else openPlace(placeFromKey(it.key)); };
  draw(); inp.focus(); M.release(p, {y: -(T["pane-rise"] || 4)}, "touch"); loadAllTopics();
  inp.addEventListener("input", () => { hi = 0; draw(); }); press(list, ".mi", (m) => go_(shown[$$(".mi", list).indexOf(m)]));
  /* the keyboard: arrows move through the rows from the field or from a row; Tab goes between the field and the row in
     hand; Return goes; a letter typed on a row goes on into the field */
  p.addEventListener("keydown", (e) => { const onRow = !!(e.target.closest && e.target.closest(".mi")), d = {ArrowDown: 1, ArrowUp: -1}[e.key];
    if (d) { e.preventDefault(); hi = clamp(hi + d, 0, Math.max(0, shown.length - 1)); mark(onRow); return; }
    if (e.key === "Tab") { e.preventDefault(); if (onRow) toField(); else if (shown.length) mark(true); return; }
    if (e.key === "Enter") { e.preventDefault(); go_(shown[hi]); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); palette(false); return; }
    if (onRow && e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) toField(); }); }

export {pal, palette};
