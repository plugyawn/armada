/* INTERIM BETA (README.md): the views: README, Files, Changes. */

import {$, $$, el, esc, R, T} from "./util.js";
import {state} from "./state.js";
import {api} from "./connection.js";
import {feed, lane, panes, side} from "./frame.js";
import {blockPart, clean, loadPics, tpl} from "./parts.js";
import {setVerb, verb} from "./composer.js";
import {edge, haltLane, pin, releaseRows, syncJump} from "./feed.js";
import {hasWorktree} from "./runs.js";
import {overHide} from "./hover.js";

/* ---------- the views: README, Files, Changes ---------- */
function setTab(name, quiet) { if (name === state.tab || !panes[name]) return; if (state.tab === "Chat") { haltLane(); state.P && (state.P.scroll = feed.scrollTop); }
  $$(".tab", state.hd).forEach((x) => { const on = x.dataset.tab === name; x.classList.toggle("on", on); x.setAttribute("aria-selected", on); });
  panes[state.tab].hidden = true; state.tab = name; const pn = panes[name]; pn.hidden = false; if (state.hd) state.hd._sig = "";
  const wide = name === "Files" || name === "Changes"; R.classList.toggle("widepane", wide); side.classList.toggle("folded", wide); R.classList.toggle("folded", wide);
  /* the header, the band and the field are the same on every view: what I type on README goes to the conversation */
  overHide(); setVerb(verb, true);
  if (name === "Chat") { if (state.pinned) pin(); else if (state.P) feed.scrollTop = state.P.scroll || 0; edge(); syncJump(); }
  if (!quiet) { if (name === "Chat") releaseRows(T["pane-rise"] || 4, "touch"); else M.release(pn.firstElementChild, {y: T["pane-rise"] || 4}, "touch", {id: "pane"}); }
  if (name === "README") drawReadme(); if (name === "Files") drawFiles(); if (name === "Changes") drawChanges();
}
const ws = (path, q) => api("GET", `armada/workspaces/${state.P.ctx.workspace_uuid}${path}`, q);
async function drawReadme() { const doc = $("#readme"), c = state.P; if (!hasWorktree(c)) { doc.innerHTML = '<p class="note">There is no worktree here, so there is no README.</p>'; return; }
  if (c.readme) { doc.innerHTML = c.readme; return; } doc.innerHTML = '<p class="note">Loading</p>';
  try { const d = (await ws("")).armada_workspace, e = d.default_entry; if (!e) { doc.innerHTML = '<p class="note">This worktree has no README.</p>'; return; }
    const f = (await ws("/file", {entry_id: e.entry_id})).armada_workspace_file; c.readmePath = f.path; let src = f.content || "", cutNote = "";
    { let fence = false; src = src.split("\n").map((l) => { if (/^\s*(```|~~~)/.test(l)) fence = !fence; return fence || !/^\s*</.test(l) ? l : l.replace(/<\/?[a-zA-Z][^>]*>/g, ""); }).join("\n"); }   /* a line of raw HTML outside code keeps its words and loses its tags: the server's renderer would print them */
    if (src.length > 9000) { const k = src.lastIndexOf("\n\n", 9000); src = src.slice(0, k > 2000 ? k : 9000); cutNote = '<p class="note">The rest of this file is not shown: the beta renders a README through the server\'s message renderer, which takes 10,000 characters.</p>'; }
    const r = await api("POST", "messages/render", {content: src}); if (state.P !== c) return;
    tpl.innerHTML = r.rendered; const root = clean(tpl.content), box = el("div"); let title = esc(e.name);
    for (const n of [...root.children]) { if (n.matches("div.codehilite, pre")) box.append(el("pre", "blk", blockPart(n).html)); else if (n.matches("table")) box.append(el("div", "blk tbl", blockPart(n).html)); else if (n.tagName === "H1" && title === esc(e.name)) { title = n.innerHTML; } else if (/^H[1-2]$/.test(n.tagName)) { const h = el("h2"); h.append(...n.childNodes); box.append(h); } else if (/^H[3-6]$/.test(n.tagName)) { const h = el("h3"); h.append(...n.childNodes); box.append(h); } else box.append(n); }
    $$("ul > li", box).forEach((li) => { const s = el("span"); s.append(...li.childNodes); li.append(s); });
    c.readme = `<div class="dh"><h1>${title}</h1><code style="color:var(--ink-2)">${esc(f.path)}</code></div>${box.innerHTML}${cutNote}<p class="note">Read only. Rendered by the server's Markdown; raw HTML in the file (a centred logo, say) is dropped. Editing is in the old interface.</p>`; doc.innerHTML = c.readme; loadPics(doc); }
  catch (e) { doc.innerHTML = `<p class="note">${esc(e.message)}</p>`; } }
async function drawFiles() { const tree = $("#tree"), fv = $("#fv"), c = state.P; if (!hasWorktree(c)) { tree.innerHTML = ""; fv.innerHTML = '<p class="note">There is no worktree here, so there are no files.</p>'; return; }
  if (!c.tree) { c.tree = {rows: [], open: new Set(), kids: new Map()}; tree.innerHTML = '<span class="note">Loading</span>'; fv.innerHTML = '<p class="note">Choose a file.</p>'; try { const r = (await ws("/tree")).armada_workspace_tree; c.tree.kids.set("", r.entries); } catch (e) { fv.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; } }
  if (state.P !== c) return; const rows = []; const walk = (id, depth) => { for (const e of c.tree.kids.get(id) || []) { const open = c.tree.open.has(e.entry_id); rows.push(`<button class="row${depth ? " d" + Math.min(depth, 2) : ""}${c.file === e.entry_id ? " sel" : ""}" data-entry="${esc(e.entry_id)}" data-kind="${esc(e.kind)}"${depth > 2 ? ` style="padding-left:${64 + (depth - 2) * 24}px"` : ""}>${ic(e.kind === "directory" ? (open ? "folder-open" : "folder") : /\.(md|rst|txt)$/i.test(e.name) ? "doc" : "file")}<span class="lab">${esc(e.name)}</span>${e.status && e.status !== "clean" ? `<span class="m">${esc(e.status)}</span>` : ""}</button>`); if (open) walk(e.entry_id, depth + 1); } }; walk("", 0);
  tree.innerHTML = rows.join(""); host(tree); if (!c.file) fv.innerHTML = '<p class="note">Choose a file.</p>'; }
async function pickEntry(row) { const c = state.P, id = row.dataset.entry;
  if (row.dataset.kind === "directory") { if (c.tree.open.has(id)) c.tree.open.delete(id); else { c.tree.open.add(id); if (!c.tree.kids.has(id)) { try { const r = (await ws("/tree", {directory_id: id})).armada_workspace_tree; c.tree.kids.set(id, r.entries); } catch (e) { c.tree.kids.set(id, []); } } } drawFiles(); return; }
  c.file = id; c.fileName = row.querySelector(".lab") ? row.querySelector(".lab").textContent : ""; setVerb(verb, true); drawFiles(); const fv = $("#fv"); fv.innerHTML = '<p class="note">Loading</p>';
  try { const f = (await ws("/file", {entry_id: id})).armada_workspace_file; if (state.P !== c || c.file !== id) return; c.filePath = f.path; const lines = (f.content || "").split("\n"), cap = 1500, shown = lines.slice(0, cap);
    fv.innerHTML = `<div class="fn"><code>${esc(f.path)}</code><span class="m">${lines.length} lines</span></div><div class="src">${shown.map((l, i) => `<div class="sl"><i>${i + 1}</i><span>${esc(l) || " "}</span></div>`).join("")}</div>` + (lines.length > cap ? `<p class="note">${lines.length - cap} more lines are not shown in the beta.</p>` : "") + '<p class="note">Read only, without syntax colour. Editing and Diff are in the old interface.</p>'; M.release($(".src", fv), {y: T["pane-rise"] || 4}, "touch"); }
  catch (e) { fv.innerHTML = `<p class="note">${esc(e.message)} (the beta shows text files only)</p>`; } }
function drawChanges() { const box = $("#changes"), c = state.P, g = c.ctx && c.ctx.git && c.ctx.git.available ? c.ctx.git : null; if (!g) { box.innerHTML = '<p class="note">There is no worktree here, so there are no changes.</p>'; return; }
  const f = g.changed_files || [], base = esc((c.ctx && c.ctx.base_ref) || "main");
  box.innerHTML = `<div class="prow"><b class="t-heading">${f.length ? `${g.changed_count} ${g.changed_count === 1 ? "file" : "files"} changed` : "No uncommitted changes"}</b><span class="i2">${g.ahead} ${g.ahead === 1 ? "commit" : "commits"} ahead of ${base}${g.behind ? `, ${g.behind} behind` : ""}, on <code>${esc(g.branch)}</code></span></div><div class="ch-list">` + f.map((x) => `<div class="fh">${ic("file")}<code>${esc(x.path)}</code><span class="k">${esc(x.kind)}</span></div>`).join("") + (g.changed_files_truncated ? '<p class="note">The server lists only the first files.</p>' : "") + '</div><p class="note">The list is the worktree\'s real state. Diffs, Viewed, Commit and Open pull request are not in the beta: the server has no endpoint that returns a diff.</p>'; }

export {drawChanges, pickEntry, setTab};
