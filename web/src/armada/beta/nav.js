/* INTERIM BETA (README.md): the nav: channels, their topics, direct messages. */

import {$, $$, esc, store} from "./util.js";
import {allKids, expanded, marks, repoStreams, state, streams, topicsOf, unread, worktrees} from "./state.js";
import {api} from "./connection.js";
import {dkey, tkey, topicLabel} from "./routing.js";
import {navScroll} from "./frame.js";
import {faceOf, nameOf, presOf} from "./faces.js";
import {MARK_RANK} from "./runs.js";

/* ---------- navigation ---------- */
const subs = () => [...streams.values()].sort((a, b) => (repoStreams.has(b.stream_id) - repoStreams.has(a.stream_id)) || a.name.localeCompare(b.name));
const countOf = (key) => (unread.get(key) || {size: 0}).size;
const streamCount = (sid) => { let n = 0; for (const [k, s] of unread) if (k.startsWith(`s:${sid}:`)) n += s.size; return n; };
const streamMark = (sid) => { let m = null; for (const [k, v] of marks) if (k.startsWith(`s:${sid}:`) && (!m || MARK_RANK[v] > MARK_RANK[m])) m = v; return m; };
const trailHTML = (key, sel) => { const m = marks.get(key), n = countOf(key); return m ? mk(m) : n && !sel ? `<span class="n fig">${fig(n)}</span>` : ""; };   /* one trailing thing by priority: needs you, failed, live, unread */
function rowHTML(key, cls, lead, label, trail, extra = "") { return `<button class="row ${cls}" data-k="${esc(key)}"${extra}>${lead}<span class="lab">${esc(label)}</span>${trail}</button>`; }
function drawNav() {
  const y = navScroll.scrollTop, cur = state.place && state.place.key; let h = "";
  for (const s of subs()) { const sid = s.stream_id, repo = repoStreams.has(sid), open = expanded.has(sid), list = topicsOf.get(sid);
    const glyph = repo ? (open ? "repo-open" : "repo") : s.invite_only ? "lock" : "hash", cm = streamMark(sid), cn = streamCount(sid);
    const trail = open ? "" : cm ? mk(cm) : cn ? `<span class="n fig">${fig(cn)}</span>` : "";
    h += `<div class="grp" data-sid="${sid}">` + rowHTML("c:" + sid, (repo ? "repo" : "chan") + (!open && cn ? " new-in" : "") + (!open && cur && state.place.stream_id === sid ? " sel" : ""), ic(glyph), s.name, trail, ` aria-expanded="${open}"`);
    if (open) { h += '<div class="kids">';
      if (!list) h += '<span class="none">Loading</span>';
      else { let kids = list; const cap = 12; if (!allKids.has(sid) && kids.length > cap) { kids = kids.slice(0, cap); if (cur && state.place.stream_id === sid && !kids.some((t) => tkey(sid, t.name) === cur)) { const c = list.find((t) => tkey(sid, t.name) === cur); if (c) kids.push(c); } }
        if (cur && state.place.stream_id === sid && !list.some((t) => tkey(sid, t.name) === cur)) kids = [{name: state.place.topic, max_id: 0}, ...kids];
        for (const t of kids) { const k = tkey(sid, t.name), sel = k === cur; h += rowHTML(k, "t" + (sel ? " sel" : "") + (countOf(k) || marks.get(k) === "failed" ? " new-in" : ""), "", topicLabel(t.name), trailHTML(k, sel)); }
        if (!kids.length) h += '<span class="none">No topics yet</span>';
        if (list.length > kids.length) h += `<button class="mo" data-more="${sid}">${list.length - kids.length} more</button>`; }
      h += "</div>"; }
    h += "</div>"; }
  if (state.dms.length) h += '<div class="gap"></div>';
  for (const d of state.dms) { const k = dkey(d.user_ids), sel = k === cur, ids = d.user_ids.length ? d.user_ids : [state.ME];
    const lead = ids.length > 1 ? `<span class="avs">${ids.slice(0, 2).map((i) => faceOf(i, "s14")).join("")}</span>` : faceOf(ids[0], "s20", presOf(ids[0]));
    h += rowHTML(k, (sel ? "sel" : "") + (countOf(k) ? " new-in" : ""), lead, (ids.length === 1 ? nameOf(ids[0]) : ids.map((i) => nameOf(i).split(/[ ,]/)[0]).join(", ")) + (d.user_ids.length ? "" : " (you)"), trailHTML(k, sel)); }
  navScroll.innerHTML = h; host(navScroll); navScroll.scrollTop = y; navMore();
}
/* the nav's list fades under the me row while there is more of it below */
const navMore = () => navScroll.classList.toggle("more", navScroll.scrollTop + navScroll.clientHeight < navScroll.scrollHeight - 1);
const rowOf = (key) => $$(".row", navScroll).find((r) => r.dataset.k === key);
function drawTrail(key) { const r = rowOf(key);
  if (!r) { if (key.startsWith("s:")) { const sid = +key.split(":")[1]; if (!expanded.has(sid)) drawChannelTrail(sid); } return; }
  r.classList.toggle("new-in", !!countOf(key) || marks.get(key) === "failed"); setTrail(r, trailHTML(key, r.classList.contains("sel")), countOf(key), marks.get(key)); }
/* a collapsed channel's row carries its topics' strongest mark, else their unread count (as drawNav writes it), in place */
function drawChannelTrail(sid) { const r = rowOf("c:" + sid); if (!r) return; const cm = streamMark(sid), cn = streamCount(sid);
  r.classList.toggle("new-in", !!cn); setTrail(r, cm ? mk(cm) : cn ? `<span class="n fig">${fig(cn)}</span>` : "", cn, cm); }
/* a row's one trailing thing, changed where it stands: a count rolls in its cell, a mark crosses into another; else the old
   one leaves and the new one appears */
function setTrail(r, html, n, mark) { const old = $(":scope > .mk, :scope > .n", r), was = old ? old.outerHTML : "";
  if (old && html && old.matches(".n") && /class="n fig"/.test(html)) { M.figure(old, n, {quiet: true}); return; }
  if (old && html && old.matches(".mk") && /class="mk/.test(html)) { if (!old.classList.contains(mark)) M.cross(old, html); return; }
  if (was === html) return; if (old) { old.classList.remove("mk", "n"); old.inert = true; M.leave(old, () => old.remove()); }
  if (html) { r.insertAdjacentHTML("beforeend", html); M.appear(r.lastElementChild); } }
const topicLoads = new Map();
/* a channel's topics, once */
function loadTopics(sid) { if (!topicLoads.has(sid)) topicLoads.set(sid, loadTopics_(sid)); return topicLoads.get(sid); }
async function loadTopics_(sid) {
  if (topicsOf.has(sid)) return; topicsOf.set(sid, null);
  try { const r = await api("GET", `users/me/${sid}/topics`); const list = r.topics.map((t) => ({name: t.name, max_id: t.max_id}));
    if (repoStreams.has(sid)) { const w = await api("GET", "armada/workspace-topics", {stream: streams.get(sid).name}).catch(() => ({armada_workspace_topics: []})); const set = new Set(); for (const t of w.armada_workspace_topics) { set.add(t.topic.toLowerCase()); if (!list.some((x) => x.name.toLowerCase() === t.topic.toLowerCase())) list.push({name: t.topic, max_id: 0}); } worktrees.set(sid, set); }
    topicsOf.set(sid, list); } catch (e) { topicsOf.set(sid, []); }
  if (expanded.has(sid) || (state.place && state.place.stream_id === sid)) drawNav();
}
function toggleStream(sid) { if (expanded.has(sid)) expanded.delete(sid); else { expanded.add(sid); loadTopics(sid); } store.set("open", JSON.stringify([...expanded])); drawNav(); }

export {drawNav, drawTrail, loadTopics, navMore, subs, toggleStream};
