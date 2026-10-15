/* INTERIM BETA (README.md): personal pages (statement 29, contract C5). Each person has a page: their face, a line about
   them, and their posts, newest first; a post is set like the conversation, at reading width; my own page has an editor
   (Markdown, a preview, draft or publish, edit, delete). Everyone here can read a page, and so can the agent, which drafts
   a post on the page of the person who asked (never publishes it): such a draft says so. Reached from a person's card
   (partner_card.js opens "page:<id>"), from Jump to, and from the column, which lists everyone's pages.
   Places: "page:<user id>" a person's page, "post:<id>" one post, "edit:<id>" or "edit:new" the editor; their addresses are
   #page/<id>, #post/<id>, #edit/<id> and #edit/new. routing.js hands every such key, address and opening to this module.
   The page has no band and no field (like Home): the editor's title and Markdown are their own inputs. Live: an armada_post
   event redraws whatever page, post or list shows the post, after asking the server again. */

import {$, $$, el, esc, press, store, T} from "./util.js";
import {armadaBots, people, state} from "./state.js";
import {now} from "./time.js";
import {api} from "./connection.js";
import {openPlace} from "./routing.js";
import {main} from "./frame.js";
import {faceOf, nameOf} from "./faces.js";
import {blockPart, clean, echoHTML, tpl} from "./parts.js";
import {drawHeader} from "./header.js";
import {drawSide} from "./column.js";

const BIO_MAX = 280, TITLE_MAX = 200;                       /* the server's limits (zerver/models/armada.py, Page and Post) */
const pages = new Map();                                    /* user id -> {bio, posts: [{id, title, created, updated, draft}]}, newest first */
const posts = new Map();                                    /* post id -> the post in full: {id, user_id, title, content, rendered, created, updated, draft, drafted_by_agent}; stale once it changed elsewhere */
const failed = new Map();                                   /* place key -> why it could not be read */
let index = null;                                           /* everyone's pages, for the column: [{user_id, post_count, updated}], most recently changed first */
let indexFailed = false;
let ed = null;                                              /* the editor in view: {key, id, base: {title, content}, mode} */

/* ---------- places ---------- */
function pageFromKey(key) {
  const m = /^(page|post|edit):(\d+|new)$/.exec(key); if (!m) return null; const [, kind, arg] = m, id = arg === "new" ? 0 : +arg;
  if (kind === "page") { const u = people.get(id); return u && !u.bot ? {key, kind: "page", view: "index", uid: id, post_id: 0, repo: false} : null; }
  if (kind === "post") return id ? {key, kind: "page", view: "post", uid: posts.has(id) ? posts.get(id).user_id : 0, post_id: id, repo: false} : null;
  return {key, kind: "page", view: "edit", uid: state.ME, post_id: id, repo: false};
}
const pageHash = (p) => "#" + p.key.replace(":", "/");
function pageFromHash(h) { const m = /^#(page|post|edit)\/(\d+|new)$/.exec(h); return m ? pageFromKey(`${m[1]}:${m[2]}`) : null; }
const go = (key, o) => openPlace(pageFromKey(key), o);
/* whose page is in view: a post's author once the post is known */
const ownerOf = (p) => (p.view === "post" ? (posts.has(p.post_id) ? posts.get(p.post_id).user_id : 0) : p.uid);
const inView = () => (state.place && state.place.kind === "page" ? state.place : null);

/* ---------- words ---------- */
const agentWord = () => { const id = [...armadaBots][0]; return id ? nameOf(id) : "the agent"; };
const longDay = (ts) => new Date(ts * 1000).toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"});
const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const note = (text) => `<p class="note">${esc(text)}</p>`;

/* ---------- what the server holds ---------- */
async function loadPage(uid) {
  try { const r = await api("GET", `armada/pages/${uid}`); pages.set(uid, {bio: r.bio, posts: r.posts}); failed.delete(`page:${uid}`); }
  catch (e) { failed.set(`page:${uid}`, e.message); }
}
async function loadPost(id) {
  try { const r = await api("GET", `armada/posts/${id}`); posts.set(id, r.post); failed.delete(`post:${id}`); failed.delete(`edit:${id}`); }
  catch (e) { if (!e.net) posts.delete(id); failed.set(`post:${id}`, e.message); failed.set(`edit:${id}`, e.message); }   /* a post that is gone (or never mine to see) leaves; one the network kept from me stays as I had it */
}
async function loadIndex() { try { index = (await api("GET", "armada/pages")).pages; indexFailed = false; } catch (e) { indexFailed = true; } }
/* a post's summary in its author's list, kept in step with what I just did, before the server says it again */
function putSummary(x) {
  const pg = pages.get(x.user_id); if (!pg) return; const i = pg.posts.findIndex((y) => y.id === x.id), had = i >= 0 ? pg.posts[i] : null;
  const s = {id: x.id, title: x.title, created: x.created != null ? x.created : had ? had.created : x.updated, updated: x.updated, draft: x.draft};
  if (had) pg.posts[i] = s; else pg.posts.unshift(s);
}
function dropPost(uid, id) { posts.delete(id); const pg = pages.get(uid); if (pg) pg.posts = pg.posts.filter((y) => y.id !== id); }

/* ---------- opening a place (routing.js has drawn the header, the column and hidden the field) ---------- */
async function openPage(p) {
  const bioField = $("#page textarea.bio"); if (bioField) bioField.blur();   /* leaving my line while it is written saves it, before the next place is drawn */
  const box = $("#page"); box.parentElement.scrollTop = 0; box._sig = ""; if (p.view !== "edit") ed = null;
  drawPage(); loadIndex().then(redraw);   /* what is held is drawn at once; the server's copy follows */
  if (p.view === "index") { await loadPage(p.uid); if (inView() === p) redraw(); return; }
  if (p.view === "edit") { if (!p.post_id) return; const had = posts.get(p.post_id);
    if (!had || had.content == null || had.stale) { await loadPost(p.post_id); if (inView() !== p) return; }   /* changed elsewhere: never edit the words as they were */
    const x = posts.get(p.post_id); if (x && x.user_id !== state.ME) { go(`post:${x.id}`, {replace: true}); return; }   /* only its author edits a post */
    redraw(); return; }
  await loadPost(p.post_id); if (inView() !== p) return; const x = posts.get(p.post_id); if (x) p.uid = x.user_id;
  redraw();
}
function redraw() { const p = inView(); if (!p) return; drawPage(); drawHeader(); drawSide(); }
function drawPage() {
  const p = inView(), box = $("#page"); if (!p || !box) return;
  if (p.view === "edit") { drawEditor(p, box); return; }
  if (p.view === "index" && $("textarea.bio", box)) return;   /* my line is being written: news waits until it is saved */
  const h = p.view === "index" ? indexHTML(p.uid) : postHTML(p.post_id); if (box._sig === h) return; box._sig = h; box.innerHTML = h; host(box);
}

/* ---------- a person's page: their face and line, their posts newest first, and at its foot what the page is ---------- */
function indexHTML(uid) {
  const pg = pages.get(uid), mine = uid === state.ME; if (!pg) return `<div class="site">${note(failed.get(`page:${uid}`) || "Loading")}</div>`;
  const shown = pg.posts.filter((x) => !x.draft), drafts = pg.posts.length - shown.length;
  const bio = mine ? `<div class="bioed"><button class="bio${pg.bio ? "" : " empty"}" data-bio data-tip="Edit the line about you">${esc(pg.bio || "Add a line about yourself")}</button></div>` : pg.bio ? `<p class="bio">${esc(pg.bio)}</p>` : "";
  const list = pg.posts.length
    ? `<ul class="posts">${pg.posts.map((x) => `<li${x.draft ? ' class="draft"' : ""}><time datetime="${new Date(x.created * 1000).toISOString()}">${esc(longDay(x.created))}</time><a href="#post/${x.id}">${esc(x.title)}</a>${x.draft ? '<span class="tag">Draft</span>' : ""}</li>`).join("")}</ul>`
    : `<p class="none">${mine ? "Nothing here yet. Your first post is a press of New post away." : `${esc(nameOf(uid))} has not posted yet.`}</p>`;
  const what = `<span>${count(shown.length, "post", "posts")}${mine && drafts ? `, ${count(drafts, "draft", "drafts")} only you can see` : ""}</span><span>Readable by everyone here and by ${esc(agentWord())}</span>`;
  return `<div class="site"><div class="top">${faceOf(uid)}${bio}</div>${list}<div class="foot">${what}${mine ? '<a class="edit" href="#edit/new">New post</a>' : ""}</div></div>`;
}

/* ---------- a post: its title, when, whether the agent drafted it, then its words as the conversation sets them ---------- */
/* the server's rendering (or, until it arrives, the words as typed) cut as a README is: code, tables and images stand as
   blocks; headings sit under the post's title */
function bodyHTML(html) {
  tpl.innerHTML = html || ""; const root = clean(tpl.content), box = el("div");
  for (const n of [...root.children]) {
    if (n.matches("div.codehilite, pre")) box.append(el("pre", "blk", blockPart(n).html));
    else if (n.matches("table")) box.append(el("div", "blk tbl", blockPart(n).html));
    else if (n.matches("div.message_inline_image")) box.append(el("div", "blk pic", blockPart(n).html));
    else if (/^H[1-2]$/.test(n.tagName)) { const h = el("h2"); h.append(...n.childNodes); box.append(h); }
    else if (/^H[3-6]$/.test(n.tagName)) { const h = el("h3"); h.append(...n.childNodes); box.append(h); }
    else box.append(n);
  }
  $$("ul > li", box).forEach((li) => { const s = el("span"); s.append(...li.childNodes); li.append(s); });
  return box.innerHTML;
}
function postHTML(id) {
  const x = posts.get(id); if (!x) return `<div class="site">${note(failed.get(`post:${id}`) || "Loading")}</div>`; const mine = x.user_id === state.ME;
  const meta = [longDay(x.created), x.draft ? "Draft: only you can see it" : "", x.drafted_by_agent ? `drafted by ${agentWord()}` : ""].filter(Boolean).join(" · ");
  const acts = mine ? `${x.draft ? '<button class="lk" data-publish>Publish</button>' : ""}<a class="lk" href="#edit/${id}">Edit</a><button class="lk danger" data-delete>Delete</button>` : "";
  return `<div class="site"><article class="doc post"><h1>${esc(x.title)}</h1><p class="meta">${esc(meta)}</p>${bodyHTML(x.rendered != null ? x.rendered : echoHTML(x.content))}</article>`
    + `<div class="foot"><a href="#page/${x.user_id}">All posts</a>${acts ? `<span class="acts">${acts}</span>` : ""}</div></div>`;
}

/* ---------- the editor: a title, the Markdown and its preview, and what saving does; for my own posts only ----------
   What I typed and did not save is kept in this browser until I save or cancel, so leaving the editor loses nothing. */
const keptKey = (key) => "page-edit:" + key;
function kept(key) { try { return JSON.parse(store.get(keptKey(key)) || "null"); } catch (e) { return null; } }
function drawEditor(p, box) {
  if (ed && ed.key === p.key && $(".editor", box)) return;
  const x = p.post_id ? posts.get(p.post_id) : null;
  if (p.post_id && (!x || x.content == null || x.stale)) { const h = `<div class="site">${note(failed.get(p.key) || "Loading")}</div>`; if (box._sig !== h) { box._sig = h; box.innerHTML = h; } return; }
  if (x && x.user_id !== state.ME) return;
  const base = {title: x ? x.title : "", content: x ? x.content : ""}, k = kept(p.key), back = !!(k && (k.title !== base.title || k.content !== base.content));
  ed = {key: p.key, id: p.post_id, base, mode: "write"}; box._sig = "";
  box.innerHTML = `<div class="site editor"><input class="ttl" type="text" maxlength="${TITLE_MAX}" placeholder="Title" aria-label="Title">`
    + `<div class="seg" role="tablist" aria-label="Write or preview"><button class="on" role="tab" aria-selected="true" data-mode="write">Write</button><button role="tab" aria-selected="false" data-mode="preview">Preview</button></div>`
    + `<textarea class="src" placeholder="Write in Markdown" aria-label="The post, in Markdown"></textarea><article class="doc post prev" hidden></article>`
    + `<p class="say" role="status"></p><div class="foot">${edButtons(x)}</div></div>`;
  $(".ttl", box).value = back ? k.title : base.title; $(".src", box).value = back ? k.content : base.content; host(box);
  if (back) say("What you had not saved is back. Cancel drops it.");
  const f = $(x ? ".src" : ".ttl", box); f.focus({preventScroll: true}); if (x) f.setSelectionRange(f.value.length, f.value.length);
}
function edButtons(x) {
  const del = x ? '<button class="lk danger" data-delete>Delete</button>' : "";
  const second = !x || x.draft ? '<button class="btn" data-save="draft">Save draft</button>' : '<button class="btn" data-save="draft">Move to drafts</button>';
  return `${del}<span class="acts"><button class="lk" data-cancel>Cancel</button>${second}<button class="go" data-save="publish" data-key="⌘Return">${x && !x.draft ? "Save" : "Publish"}</button></span>`;
}
const fields = () => { const box = $("#page"); return {title: $(".ttl", box).value, content: $(".src", box).value}; };
function say(text) { const s = $("#page .say"); if (s) s.textContent = text; }
function keep() { if (!ed) return; const f = fields(); if (f.title === ed.base.title && f.content === ed.base.content) store.del(keptKey(ed.key)); else store.set(keptKey(ed.key), JSON.stringify(f)); }
function setMode(mode) {
  if (!ed) return; ed.mode = mode; const box = $("#page"), src = $(".src", box), prev = $(".prev", box);
  $$(".seg button", box).forEach((b) => { const on = b.dataset.mode === mode; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
  src.hidden = mode !== "write"; prev.hidden = mode !== "preview"; if (mode === "write") { src.focus({preventScroll: true}); return; }
  const content = src.value; if (!content.trim()) { prev.innerHTML = note("Nothing to preview yet."); return; }
  prev.innerHTML = bodyHTML(echoHTML(content)); host(prev);   /* at once, as typed; the server's rendering replaces it */
  api("POST", "messages/render", {content}).then((r) => { if (ed && ed.mode === "preview" && src.value === content) { prev.innerHTML = bodyHTML(r.rendered); host(prev); } }, (e) => say(e.message));
}
let busy = false;
/* what the server answers lands on the editor that asked; if I have gone elsewhere meanwhile, I stay there */
async function save(publish) {
  const p = inView(), mine = ed; if (!mine || !p || busy) return; const {title, content} = fields(), draft = !publish;
  if (!title.trim()) { say("A post needs a title."); $("#page .ttl").focus(); return; }
  busy = true; $$("#page .foot button").forEach((b) => (b.disabled = true)); say(publish ? "Publishing" : "Saving");
  try {
    let id = mine.id; const at = Math.floor(now() / 1000);
    if (id) await api("PATCH", `armada/posts/${id}`, {title, content, draft}); else id = (await api("POST", "armada/posts", {title, content, draft})).id;
    store.del(keptKey(mine.key)); const was = posts.get(id);
    const x = {drafted_by_agent: false, created: at, ...was, id, user_id: state.ME, title: title.trim(), content, rendered: was && was.content === content ? was.rendered : null, updated: at, draft, stale: false};
    posts.set(id, x); putSummary(x);
    if (ed === mine) ed = null; if (inView() === p) go(`post:${id}`, {replace: true}); else redraw();
  } catch (e) { if (ed === mine && inView() === p) { say(e.message); $$("#page .foot button").forEach((b) => (b.disabled = false)); } else M.say(main, e.message, 2400); }
  finally { busy = false; }
}
function cancel() { if (!ed) return; store.del(keptKey(ed.key)); const id = ed.id; ed = null; go(id ? `post:${id}` : `page:${state.ME}`, {replace: true}); }

/* publishing a draft from the post itself */
async function publish(id) {
  const x = posts.get(id); if (!x || busy) return; busy = true;
  try { await api("PATCH", `armada/posts/${id}`, {draft: false}); x.draft = false; x.updated = Math.floor(now() / 1000); putSummary(x); redraw(); }
  catch (e) { M.say($("#page [data-publish]") || main, e.message, 2400); }
  finally { busy = false; }
}
/* deleting: asked first, standing on Cancel, so Return alone never deletes */
let asking = null;
function confirmDelete(id) {
  if (asking) return; const p = inView(), from = document.activeElement, d = asking = el("div", "dlg raised float postdel", `<p class="q">Delete this post?</p><p class="d">It leaves your page for everyone, and cannot be brought back.</p><div class="btns"><button class="btn" data-no>Cancel</button><button class="go" data-yes>Delete</button></div>`);
  d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.setAttribute("aria-label", "Delete this post?"); main.append(d); host(d); M.release(d, {y: -(T["pane-rise"] || 4)}, "touch");
  const close = (back = true) => { asking = null; d.inert = true; M.leave(d, () => d.remove()); removeEventListener("keydown", key, true); if (back && from && from.isConnected) from.focus({preventScroll: true}); };
  const yes = async () => { close(false); try { await api("DELETE", `armada/posts/${id}`); store.del(keptKey(`edit:${id}`)); dropPost(state.ME, id); if (ed && ed.id === id) ed = null; if (inView() === p) go(`page:${state.ME}`, {replace: true}); else redraw(); } catch (e) { say(e.message); M.say(main, e.message, 2400); if (from && from.isConnected) from.focus({preventScroll: true}); } };
  const key = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); (document.activeElement === $("[data-yes]", d) ? yes : () => close())(); } else if (e.key === "Tab") { e.preventDefault(); const b = $$("button", d), i = b.indexOf(document.activeElement); b[(i + 1) % b.length].focus(); } };
  addEventListener("keydown", key, true); press(d, "[data-no]", () => close()); press(d, "[data-yes]", yes); $("[data-no]", d).focus();
}

/* ---------- the line about me: pressed, it is a field; Return or leaving it saves, Esc puts it back ----------
   The field lies over the line in the same grid cell, and the line stays there, hidden, holding the words as typed: the
   cell keeps the height it had (a bio of three lines stays three lines tall), and grows only when the words need it */
function editBio(b) {
  const pg = pages.get(state.ME); if (!pg) return; const inp = el("textarea", "bio"); inp.rows = 1; inp.maxLength = BIO_MAX; inp.value = pg.bio; inp.placeholder = "A line about you";
  inp.setAttribute("aria-label", "A line about you"); b.dataset.state = "mirror"; b.tabIndex = -1; b.setAttribute("aria-hidden", "true"); b.after(inp);
  inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); let done = false;
  inp.addEventListener("input", () => { b.textContent = inp.value; });
  /* the new line is drawn at once and put back if the server refuses it */
  const end = async (keepIt) => { if (done) return; done = true; const bio = inp.value.replace(/\s+/g, " ").trim(), was = pg.bio, changed = keepIt && bio !== was;   /* one line: a pasted line break is a space */
    if (changed) pg.bio = bio; const back = document.activeElement === inp; inp.remove(); $("#page")._sig = ""; drawPage();
    const nb = $("#page [data-bio]"); if (nb && back) nb.focus({preventScroll: true});
    if (changed) { try { await api("PATCH", "armada/pages/me", {bio}); } catch (e) { pg.bio = was; $("#page")._sig = ""; drawPage(); M.say(main, e.message, 2400); } } };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); end(true); } else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(false); } });
  inp.addEventListener("blur", () => end(true));
}

/* ---------- a file dropped or pasted into the Markdown: uploaded, and linked where the caret was ---------- */
function attach(file, src) {
  const name = file.name || "pasted file", words = (n) => n.replace(/[[\]]/g, ""), hold = `[Uploading ${words(name)}…]()`, at = src.selectionStart;   /* a bracket in a name would end the link's words early */
  src.setRangeText(hold, at, src.selectionEnd, "end"); keep();
  const fd = new FormData(); fd.append("file", file, name);
  api("POST", "user_uploads", {}, {body: fd}).then((r) => link(`[${words(r.filename || name)}](${r.url || r.uri})`), (e) => { link(""); say(e.message); });
  function link(text) { const i = src.value.indexOf(hold); if (i < 0) return; src.setRangeText(text, i, i + hold.length, "preserve"); keep(); }
}

/* ---------- the column: everyone's pages, mine first, then the most recently changed ---------- */
function pagesSideHTML() {
  const p = inView(), cur = p ? ownerOf(p) : 0;
  /* until the server says the order (most recently changed first), nothing is listed: rows that came first and then
     reordered would move under the pointer. If it cannot be asked, everyone is listed as they are known */
  const rows = (index || (indexFailed ? [...people.values()].filter((u) => !u.bot && u.active).map((u) => ({user_id: u.id, post_count: 0})) : [])).filter((r) => people.has(r.user_id));
  rows.sort((a, b) => (b.user_id === state.ME) - (a.user_id === state.ME));
  return `<section class="sec grow" id="sec-pages"><ul class="ls">${rows.map((r) => `<li data-k="page:${r.user_id}"${r.user_id === cur ? ' class="on" aria-current="page"' : ""}>${faceOf(r.user_id, "s20")}<span class="t">${esc(r.user_id === state.ME ? "Your page" : nameOf(r.user_id))}</span><span class="e"><span class="d">${r.post_count ? count(r.post_count, "post", "posts") : ""}</span></span></li>`).join("")}</ul></section>`;
}
/* the header: whose page, and a sentence on what is in view */
function pageHead() {
  const p = state.place, uid = ownerOf(p), mine = uid === state.ME, x = p.post_id ? posts.get(p.post_id) : null; let say_ = "";
  if (p.view === "index") { const pg = pages.get(uid), n = pg ? pg.posts.filter((y) => !y.draft).length : 0;
    say_ = mine ? "Your page. Everyone here can read what you publish; drafts are yours alone." : !pg ? "" : n ? `${count(n, "post", "posts")}, newest first.` : "No posts yet."; }
  else if (p.view === "post") say_ = !x ? "" : x.draft ? "A draft only you can see." : `Posted ${longDay(x.created)}.`;
  else say_ = p.post_id ? "Editing a post on your page." : "A new post on your page.";
  return {kind: "plain", title: esc(uid ? nameOf(uid) : "Pages"), say: esc(say_), checklist: {title: "Pages"}};
}
/* Jump to: my page, a new post, and everyone's page by their name */
function pageItems() {
  const out = [{key: `page:${state.ME}`, label: "Your page", glyph: faceOf(state.ME, "s20"), cap: "Page"}, {cmd: () => go("edit:new"), label: "New post", glyph: ic("edit"), cap: "Your page"}];
  for (const u of people.values()) if (!u.bot && u.active && u.id !== state.ME) out.push({key: `page:${u.id}`, label: `${u.name}'s page`, glyph: faceOf(u.id, "s20"), cap: "Page"});
  return out;
}

/* ---------- live: a post added, changed or removed anywhere. What it changes is asked again, once, and drawn ---------- */
const stale = new Set(); let staleTimer = 0;
function onPost(ev) {
  const x = ev.post; if (!x) return; const p = inView(), full = posts.get(x.id);
  if (ev.op === "remove") { dropPost(x.user_id, x.id); failed.set(`post:${x.id}`, "This post is no longer here."); }   /* deleted, or taken back into its author's drafts */
  else { if (full) Object.assign(full, {title: x.title, draft: x.draft, updated: x.updated, stale: true}); putSummary({...x, created: full ? full.created : null}); stale.add(`post:${x.id}`); }
  stale.add(`page:${x.user_id}`);
  if (ed && ed.id === x.id) say(ev.op === "remove" ? "This post was deleted elsewhere." : "This post changed elsewhere. Saving keeps your version.");
  if (p) redraw(); clearTimeout(staleTimer); staleTimer = setTimeout(refresh, 250);
}
/* what is in view is asked again; anything else is asked again when it is next opened */
async function refresh() {
  const p = inView(), jobs = []; if (p) { jobs.push(loadIndex());
    if (p.view === "index" && stale.has(`page:${p.uid}`)) jobs.push(loadPage(p.uid));
    if (p.view === "post" && stale.has(`post:${p.post_id}`)) jobs.push(loadPost(p.post_id)); }
  stale.clear(); await Promise.all(jobs); redraw();
}

/* ---------- the pane's controls, wired once at boot ---------- */
function wirePages() {
  const box = $("#page"); if (!box) return;
  press(box, "[data-bio]", (b) => editBio(b));
  press(box, "[data-mode]", (b) => setMode(b.dataset.mode));
  press(box, "[data-save]", (b) => save(b.dataset.save === "publish"));
  press(box, "[data-cancel]", () => cancel());
  press(box, "[data-publish]", () => publish(inView().post_id));
  press(box, "[data-delete]", () => confirmDelete(inView().post_id));
  box.addEventListener("input", (e) => { if (e.target.matches(".ttl, .src")) keep(); });
  box.addEventListener("keydown", (e) => { if (!ed || !e.target.closest(".editor")) return;
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.isComposing) { e.preventDefault(); e.stopPropagation(); save(true); }
    else if (e.key === "Enter" && !e.isComposing && e.target.matches(".ttl")) { e.preventDefault(); $(".src", box).focus(); } });   /* Return in the title goes on to the words */
  box.addEventListener("paste", (e) => { const f = [...((e.clipboardData && e.clipboardData.files) || [])]; if (!f.length || !e.target.matches(".src")) return; e.preventDefault(); f.forEach((x) => attach(x, e.target)); });
  box.addEventListener("drop", (e) => { const f = [...((e.dataTransfer && e.dataTransfer.files) || [])]; if (!f.length) return; e.preventDefault(); e.stopPropagation(); const src = $(".src", box); if (src && !src.hidden) f.forEach((x) => attach(x, src)); });
}

export {drawPage, ed, onPost, openPage, pageFromHash, pageFromKey, pageHash, pageHead, pageItems, pages, pagesSideHTML,
  posts, wirePages};
