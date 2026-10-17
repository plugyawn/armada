/* INTERIM BETA (README.md): a chunk (chunks.js): what the nav says of a channel or a topic when the pointer rests on its
   row (statement 53). A card raised beside the nav, its head level with the row's: for a channel, what it is for, who is
   in it (which opens its members, and Add people), its repository, and "+" for a new topic (a worktree, in a
   repository); for a topic, who has spoken there, the latest word, its run, and "+" for a new thread. It comes up as a
   tooltip does: after the pointer rests on a row for the tooltip's delay, at once while a card is up, and it waits for
   the pointer crossing to it. A row that takes focus from the keyboard brings its card up too; Tab goes into it, and on
   past it to the next row. It never stands in flow: it is over the conversation's edge, beside the pointer, so nothing
   under the pointer moves. Opened further (a new topic's name, the members), it is a dialog that holds focus until Esc,
   a press outside or focus going elsewhere.
   It imports nothing the first load holds: the page is handed to its default export, which wires the nav when the chunk
   arrives (prefetched once the page is idle; before that a row has no card). Who may add people, and whom Add offers, is
   channel_rules.js, which arrives with it. */

import {addable, addRefusal, mayAddPeople, memberOrder} from "./channel_rules.js";

export default function channelViewChunk(P) {
  const {$, $$, el, esc, T, streams, repoStreams, members, marks, places, topicsOf, worktrees, runsSeen, people, age,
    api, faceOf, nameOf, presOf, isOnix, openPlace, placeFromKey, tkey, topicLabel, loadTopics, loadMembers, repoPath, nav,
    navScroll, ready, state, startThread, holds, membersChanged: changed} = P;

  /* ---------- the card: one at a time, for one row, known by the row's key (the nav is drawn again under it) ---------- */
  let card = null, pending = "", showTimer = 0, hideTimer = 0, goneAt = -1e9, quiet = "";
  const DELAY = T["tip-delay"] || 500, LINGER = T["tip-linger"] || 300;
  const rowAt = (t) => { const r = t && t.closest && t.closest(".row[data-k]"); return r && navScroll.contains(r) && /^[cs]:/.test(r.dataset.k) ? r : null; };
  const rowOf = (key) => $$(".row[data-k]", navScroll).find((r) => r.dataset.k === key) || null;
  const keyOf = (t) => { const r = rowAt(t); return r ? r.dataset.k : ""; };
  /* a row's place: a channel ("c:<id>") or a topic ("s:<id>:<topic>") in a channel I am in */
  function placeOfKey(k) { if (k.startsWith("c:")) { const sid = +k.slice(2); return streams.has(sid) ? {kind: "channel", sid, key: k} : null; }
    const p = placeFromKey(k); return p ? {kind: "topic", sid: p.stream_id, key: k, topic: p.topic} : null; }
  const holding = () => !!card && (card.el.contains(document.activeElement) || document.activeElement === rowOf(card.key));

  /* rested on (the pointer) or focused (the keyboard): the card comes after the tooltip's delay, or at once while one is up
     or has only just gone */
  function want(k) {
    if (!k) { pending = ""; clearTimeout(showTimer); hideSoon(); return; }
    if (k === quiet) return; clearTimeout(hideTimer);
    if ((card && card.key === k) || pending === k) return;
    clearTimeout(showTimer); pending = ""; const at = placeOfKey(k); if (!at) { hideSoon(); return; }
    if (at.kind === "channel") loadMembers(at.sid);   /* asked for while the pointer rests, so the count is there with the card */
    if (card || performance.now() - goneAt < LINGER) { show(at); return; }
    pending = k; showTimer = setTimeout(() => { pending = ""; if (rowOf(k)) show(at); }, DELAY); }
  function hideSoon() { clearTimeout(hideTimer); if (card && !holding()) hideTimer = setTimeout(() => { if (!holding()) hide(); }, LINGER); }
  function show(at) { clearTimeout(hideTimer); const before = card ? card.el.getBoundingClientRect() : null;
    if (!card) { const box = el("div", "ncard raised"); box.setAttribute("role", "dialog"); box.tabIndex = -1;
      box.addEventListener("pointerenter", () => clearTimeout(hideTimer)); box.addEventListener("pointerleave", hideSoon); box.addEventListener("keydown", onCardKey);
      box.addEventListener("mousedown", (e) => { if (!e.target.closest("input")) e.preventDefault(); });   /* a press here leaves the caret where it was */
      box.addEventListener("input", (e) => { if (card && card.view === "add") { card.query = e.target.value; card.rows = offer(); card.said = ""; drawSheet(); } });   /* Add's search */
      box.addEventListener("focusout", (e) => { if (card && card.el === box && !box.contains(e.relatedTarget) && !navScroll.contains(e.relatedTarget)) hide({stay: true}); });   /* focus gone elsewhere takes it */
      P.press(box, "[data-card-new]", newTopic); P.press(box, "[data-card-thread]", newThread); P.press(box, "[data-card-members]", (b, e, ptr) => openSheet("members", !ptr));
      P.press(box, "[data-add-people]", (b, e, ptr) => openSheet("add", !ptr)); P.press(box, "[data-members-back]", (b, e, ptr) => openSheet("members", !ptr)); P.press(box, "[data-add]", (b) => addOne(b));
      document.body.append(box); card = {el: box}; }
    Object.assign(card, at, {back: null, view: ""}); card.el.setAttribute("aria-label", at.kind === "channel" ? streams.get(at.sid).name : topicLabel(at.topic));
    draw(); place(); if (before) M.flip(card.el, before, "touch"); else { M.fade(card.el, 1); M.release(card.el, {x: -(T["pane-rise"] || 4)}, "touch"); }
    if (at.kind === "topic") loadTopicFacts(at); }
  /* gone; focus that was in it goes back where it came from: the row it was entered from, else the field */
  function hide(o = {}) { clearTimeout(showTimer); clearTimeout(hideTimer); pending = ""; if (!card) return; const c = card, held = c.el.contains(document.activeElement); card = null; goneAt = performance.now();
    if (held) document.activeElement.blur();   /* a closing card is inert but holds focus until the browser moves it: it lets go first */
    M.leave(c.el); if (held && !o.stay) { const r = c.back && rowOf(c.key); if (r) M.focusKbd(r); else ready(); } }
  /* beside the nav, its head level with the row's, inside the window */
  function place() { const r = rowOf(card.key); if (!r) return; const a = r.getBoundingClientRect(), n = nav.getBoundingClientRect(), h = card.el.offsetHeight, gap = 8;
    card.el.style.left = `${Math.round(n.right + gap)}px`; card.el.style.top = `${Math.round(Math.max(gap, Math.min(innerHeight - h - gap, a.top - 4)))}px`; }

  /* ---------- what a card says ---------- */
  const fact = (name, html, key) => `<dt>${name}</dt><dd data-fact="${key}">${html}</dd>`;
  const plusHTML = (data, label, tip) => `<button class="c sq" ${data} aria-label="${esc(label)}" data-tip="${tip}">${ic("plus")}</button>`;
  const glyphOf = (s) => ic(repoStreams.has(s.stream_id) ? "repo" : s.invite_only ? "lock" : "hash");
  function draw() { const s = streams.get(card.sid); if (!s) { hide(); return; }
    card.el.innerHTML = card.kind === "channel" ? channelHTML(s) : topicHTML(s); host(card.el); }
  /* a channel: its glyph and name, "+", what it is for, who is in it and its repository */
  function channelHTML(s) { const sid = s.stream_id, repo = repoStreams.has(sid);
    return `<div class="ih">${glyphOf(s)}<b>${esc(s.name)}</b>${plusHTML("data-card-new", `New topic in ${s.name}`, "New topic")}</div>`
      + (s.description ? `<p class="say">${esc(s.description)}</p>` : "")
      + `<dl class="facts">${fact("Members", `<button class="lk" data-card-members aria-label="Members of ${esc(s.name)}">${membersHTML(sid)}</button>`, "members")}${repo ? fact("Repository", esc(repoOf(sid)), "repo") : ""}</dl>`; }
  /* how many are in it, with the faces of the first three people, those here now first */
  function membersHTML(sid) { const set = members.get(sid); if (!(set instanceof Set)) return "…";
    const ids = [...set].filter((i) => people.has(i) && people.get(i).active), here = ids.filter((i) => !people.get(i).bot).sort((a, b) => (presOf(b) === "on") - (presOf(a) === "on"));
    return `<span class="avs">${here.slice(0, 3).map((i) => faceOf(i, "s18")).join("")}</span><span><span class="fig">${fig(ids.length)}</span> ${ids.length === 1 ? "member" : "members"}</span>`; }
  /* a repository's owner/name: from the context of any place of the channel the page holds, else asked once of one of its
     worktrees, without git (which reads nothing on disk) */
  const repos = new Map();
  function repoOf(sid) { if (repos.has(sid)) return repos.get(sid) || "…";
    for (const c of places.values()) if (c.place.stream_id === sid && c.ctx && (c.ctx.repository_url || c.ctx.github_repo)) { repos.set(sid, repoPath(c.ctx.repository_url || c.ctx.github_repo, streams.get(sid).name)); return repos.get(sid); }
    repos.set(sid, ""); askRepo(sid); return "…"; }
  async function askRepo(sid) { await loadTopics(sid); const s = streams.get(sid), wt = worktrees.get(sid), t = (topicsOf.get(sid) || []).find((x) => wt && wt.has(x.name.toLowerCase()));
    let path = s ? s.name : ""; if (s && t) try { const ctx = (await api("GET", "armada/topic-context", {stream: s.name, topic: t.name, include_git: false})).armada_topic_context; path = repoPath(ctx.repository_url || ctx.github_repo, s.name); } catch (e) {}
    repos.set(sid, path); if (card && card.sid === sid && card.kind === "channel") { const d = $('[data-fact="repo"]', card.el); if (d) d.textContent = path; } }
  /* a topic: its glyph (a worktree's branch, else a thread's), its name and "+", then its people, its latest word and its run.
     What is not known yet is "…" in a cell that is already there, so the card keeps its size as the facts arrive. */
  const RUN_WORDS = {needs: "Onix asks you", live: "Onix is working", queued: "Queued", failed: "The last run failed", paused: "Paused"};
  function topicHTML() { const {key, topic, sid} = card, wt = worktrees.get(sid), mark = marks.get(key), f = facts.get(key);
    return `<div class="ih">${ic(wt && wt.has(topic.toLowerCase()) ? "branch" : "thread")}<b>${esc(topicLabel(topic))}</b>${plusHTML("data-card-thread", `New thread in ${topicLabel(topic)}`, "New thread")}</div>`
      + `<dl class="facts">${fact("People", f ? peopleHTML(f.who) : "…", "people")}${fact("Latest", f ? latestHTML(f.last) : "…", "latest")}${mark ? fact("Run", `${mk(mark)}<span class="t">${esc((RUN_WORDS[mark] || "") + (mark === "live" ? overviewOf(key) : ""))}</span>`, "run") : ""}</dl>`; }
  const overviewOf = (key) => { const r = (runsSeen.get(key) || []).find((x) => x.status === "running" && x.overview); return r ? `: ${r.overview}` : ""; };
  const first = (id) => nameOf(id).split(/[ ,]/)[0];
  /* who has spoken there: people, latest first, then Onix; the faces of three people (Onix has none), two names and how many
     more */
  function peopleHTML(ids) { if (!ids.length) return "No one yet"; const who = [...ids.filter((i) => !isOnix(i)), ...ids.filter(isOnix)], names = who.slice(0, 2).map(first), more = who.length - names.length;
    return `<span class="avs">${who.filter((i) => !isOnix(i)).slice(0, 3).map((i) => faceOf(i, "s18")).join("")}</span><span class="t">${esc(more ? `${names.join(", ")} and ${more} ${more === 1 ? "other" : "others"}` : names.join(" and "))}</span>`; }
  function latestHTML(m) { if (!m) return "Nothing yet"; const a = age(m.timestamp); return `<span class="t">${esc(`${first(m.sender_id)}, ${a === "now" ? "just now" : a + " ago"}`)}</span>`; }

  /* a topic's people and latest word: from its place when the page holds it, else from its latest messages, asked once a
     minute at most */
  const facts = new Map();
  function topicFacts(msgs) { const who = []; for (let i = msgs.length - 1; i >= 0; i--) { const id = msgs[i].sender_id; if (!who.includes(id) && (people.has(id) || isOnix(id))) who.push(id); }
    return {who, last: msgs[msgs.length - 1] || null, at: performance.now()}; }
  async function loadTopicFacts(at) { const c = places.get(at.key);
    if (c && c.loaded) { facts.set(at.key, topicFacts(c.msgs.filter((m) => typeof m.id === "number"))); fillFacts(at.key); return; }
    const had = facts.get(at.key); if (had && performance.now() - had.at < 60000) return;
    let r; try { r = await api("GET", "messages", {anchor: "newest", num_before: 40, num_after: 0, narrow: [{operator: "channel", operand: at.sid}, {operator: "topic", operand: at.topic}], apply_markdown: false, client_gravatar: true}); }
    catch (e) { return; }
    facts.set(at.key, topicFacts(r.messages)); fillFacts(at.key); }
  function fillFacts(key) { if (!card || card.key !== key) return; const f = facts.get(key), p = $('[data-fact="people"]', card.el), l = $('[data-fact="latest"]', card.el);
    if (p) { p.innerHTML = peopleHTML(f.who); host(p); } if (l) l.innerHTML = latestHTML(f.last); }

  /* ---------- "+" on a channel: a new topic, named in the card. Return opens it; what is sent there starts it ---------- */
  function newTopic() { if (!card || card.kind !== "channel") return; const s = streams.get(card.sid), repo = repoStreams.has(s.stream_id);
    if (card.el.contains(document.activeElement)) card.el.focus({preventScroll: true});   /* the "+" goes with what it drew: focus stays in the card */
    card.el.innerHTML = `<div class="ih">${glyphOf(s)}<b>${esc(s.name)}</b></div>`
      + `<input class="chn-in" maxlength="60" placeholder="Name the topic" aria-label="New topic in ${esc(s.name)}" autocomplete="off" spellcheck="false">`
      + `<p class="cap">${repo ? "Its own worktree, once Onix first runs there." : "What you send there starts it."}</p>`;
    host(card.el); place(); $("input", card.el).focus(); }
  function openTopic(input) { const sid = card.sid, name = input.value.trim().replace(/\s+/g, " ");
    if (!name) { M.refuse(input); S.play("nope"); return; }
    const known = (topicsOf.get(sid) || []).find((t) => t.name.toLowerCase() === name.toLowerCase()), p = placeFromKey(tkey(sid, name));
    p.topic = known ? known.name : name; hide({stay: true}); openPlace(p); }

  /* ---------- "+" on a topic: a new thread there. The topic opens (it may be open already) and what I send next starts the
     thread, which opens under it (mentions.js, startThread) ---------- */
  function newThread() { if (!card || card.kind !== "topic") return; const p = placeFromKey(card.key); hide({stay: true}); if (!p) return;
    if (!state.place || state.place.key !== p.key) openPlace(p); startThread(); }

  /* ---------- a channel's members, from its card's count: a list of who is in it, and Add people when the server would let
     me (channel_rules.js): the active people not in it, found by name, each added with one press (POST
     users/me/subscriptions with principals, as the old interface adds them). A row says Adding at once and Added once the
     server has them, and keeps its place; a refusal is said in plain words under the list. Both views are drawn again as
     the subscription event says who joins and leaves. ---------- */
  const PRES = {on: "Here now", away: "Away"};
  const personOf = (i) => { const u = people.get(i); return {id: i, name: isOnix(i) ? "Onix" : u.name, bot: !!u.bot, active: !!u.active, pres: presOf(i)}; };
  /* a dialog from here on: focus goes into it, on Add's search, or (from the keyboard) on its first control */
  function openSheet(view, kbd) { if (!card || card.kind !== "channel") return; card.view = view; card.query = ""; card.said = ""; card.rows = view === "add" ? offer() : null; card.states = card.states || {};
    card.el.focus({preventScroll: true}); drawSheet(); place();
    const first = $(view === "add" ? "input" : "[data-add-people]", card.el); if (first && (kbd || view === "add")) enter(first); }
  /* the people Add offers: worked out when it opens and when the query changes, so a row stays where it is as people join */
  function offer() { const set = members.get(card.sid); return addable([...people.values()].filter((u) => u.id !== state.ME).map((u) => personOf(u.id)), set instanceof Set ? set : new Set(), card.query).map((u) => u.id); }
  function drawSheet() { const s = streams.get(card.sid); if (!s) { hide(); return; } const at = document.activeElement, focusKey = at && at.dataset && at.dataset.add, onAdd = !!at && at.matches("[data-add-people]");
    if (card.view === "members") { if (card.el.contains(at)) card.el.focus({preventScroll: true}); card.el.innerHTML = membersSheetHTML(s); const b = onAdd && $("[data-add-people]", card.el); if (b) b.focus({preventScroll: true}); }   /* drawn again, focus stays where it was */
    else { const input = $("input", card.el); if (focusKey) card.el.focus({preventScroll: true});
      if (!input) card.el.innerHTML = addSheetHTML(s); else { $(".ls", card.el).innerHTML = addRowsHTML(s); $(".cap", card.el).textContent = card.said; $(".cap", card.el).toggleAttribute("data-bad", !!card.said); } }
    host(card.el); if (focusKey) { const b = $(`[data-add="${focusKey}"]`, card.el); if (b) { for (const x of $$("[data-add]", card.el)) x.tabIndex = x === b ? 0 : -1; b.focus({preventScroll: true}); } } }
  function membersSheetHTML(s) { const set = members.get(s.stream_id), may = mayAddPeople(s, state.realm, holds);
    const ids = set instanceof Set ? memberOrder([...set].filter((i) => people.has(i) && people.get(i).active).map(personOf)) : null;
    const rows = !ids ? '<li class="none">Loading</li>' : ids.map((u) => `<li>${faceOf(u.id, "s20", u.pres)}<span class="t">${esc(u.name)}${u.id === state.ME ? " (you)" : ""}</span><span class="e">${u.bot ? (isOnix(u.id) ? "Agent" : "Bot") : PRES[u.pres] || ""}</span></li>`).join("");
    return `<div class="ih">${glyphOf(s)}<b>${esc(s.name)}</b>${may ? '<button class="lk" data-add-people>Add people</button>' : ""}</div>`
      + `<p class="cap" aria-live="polite">${ids ? `<span class="fig">${fig(ids.length)}</span> ${ids.length === 1 ? "member" : "members"}` : "…"}</p><ul class="ls mem" aria-label="Members of ${esc(s.name)}">${rows}</ul>`; }
  function addSheetHTML(s) { return `<div class="ih">${glyphOf(s)}<b>Add to ${esc(s.name)}</b><button class="lk" data-members-back>Done</button></div>`
    + `<input class="chn-in" placeholder="Find someone" aria-label="Find someone to add to ${esc(s.name)}" autocomplete="off" spellcheck="false">`
    + `<ul class="ls add" aria-label="People not in ${esc(s.name)}">${addRowsHTML(s)}</ul><p class="cap" aria-live="polite"></p>`; }
  /* a row's verb in a cell as wide as "Adding", so its changes move nothing: Add, Adding, Added */
  function addRowsHTML(s) { const set = members.get(s.stream_id); if (!card.rows.length) return `<li class="none">${card.query ? "No one by that name" : `Everyone is in ${esc(s.name)}`}</li>`;
    return card.rows.map((i, n) => { const u = personOf(i), st = set instanceof Set && set.has(i) ? "added" : card.states[i] === "adding" ? "adding" : "";
      return `<li data-state="${st}">${faceOf(i, "s20", u.pres)}<span class="t">${esc(u.name)}</span><button class="lk" data-add="${i}" tabindex="${n ? -1 : 0}" aria-label="${st === "added" ? `${esc(u.name)} is in ${esc(s.name)}` : `Add ${esc(u.name)} to ${esc(s.name)}`}"${st ? " aria-disabled=\"true\"" : ""}><span>${st === "added" ? "Added" : st === "adding" ? "Adding" : "Add"}</span><i aria-hidden="true">Adding</i></button></li>`; }).join(""); }
  function addOne(b) { const id = +b.dataset.add, sid = card.sid; if (card.states[id] === "adding" || b.closest("li").dataset.state === "added") return;
    card.states[id] = "adding"; card.said = ""; drawSheet();
    addPeople(sid, [id]).then(() => { if (card && card.sid === sid) { delete card.states[id]; drawSheet(); } S.play("tick"); },
      (e) => { if (!card || card.sid !== sid) return; delete card.states[id]; card.said = addRefusal(e, streams.get(sid).name); drawSheet(); const r = $(`[data-add="${id}"]`, card.el); if (r) M.refuse(r); S.play("nope"); }); }
  /* people added to a channel: the server is asked; once it has them they are counted at once (its event says so too) */
  async function addPeople(sid, ids) { const s = streams.get(sid); await api("POST", "users/me/subscriptions", {subscriptions: [{name: s.name}], principals: ids});
    const set = members.get(sid); if (set instanceof Set) for (const i of ids) set.add(i); changed(); }
  function onSheetKey(e) { const input = e.target.matches("input"), row = e.target.closest("[data-add]");
    if (input && e.key === "Enter" && !e.isComposing) { e.preventDefault(); const b = card.query && $("[data-add]", card.el); if (b) addOne(b); else M.refuse(e.target); return true; }   /* Return in the search adds the first one found */
    if ((input || row) && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); const all = $$("[data-add]", card.el); if (!all.length) return true;
      if (row && e.key === "ArrowUp" && row === all[0]) { $("input", card.el).focus(); return true; }   /* Up from the first row is the search again */
      const n = input ? all[0] : all[Math.max(0, Math.min(all.length - 1, all.indexOf(row) + (e.key === "ArrowDown" ? 1 : -1)))]; for (const x of all) x.tabIndex = x === n ? 0 : -1; M.focusKbd(n);
      const ls = n.closest("ul"), li = n.closest("li"); if (li.offsetTop < ls.scrollTop) ls.scrollTop = li.offsetTop; else if (li.offsetTop + li.offsetHeight > ls.scrollTop + ls.clientHeight) ls.scrollTop = li.offsetTop + li.offsetHeight - ls.clientHeight; return true; }
    return false; }

  /* ---------- the keyboard: Tab from the row goes into its card, and from its last control to the next row; Shift-Tab from
     its first goes back to the row; Esc closes it and gives the row its focus; Return in the topic's name opens it ---------- */
  const stops = () => $$("button, input", card.el).filter((x) => !x.disabled && x.tabIndex >= 0);
  const enter = (x) => (x.matches("input") ? x.focus() : M.focusKbd(x));
  function nextStop(r) { const all = $$("button", nav).filter((x) => !x.closest("[hidden], [inert]")), i = all.indexOf(r); return all[i + 1] || null; }
  function onRowKey(e) { if (!card || e.key !== "Tab" || e.shiftKey || e.metaKey || e.ctrlKey || e.altKey || e.target !== rowOf(card.key)) return;
    const s = stops(); if (!s.length) return; e.preventDefault(); e.stopPropagation(); card.back = e.target; enter(s[0]); }
  function onCardKey(e) { if (e.metaKey || e.ctrlKey || e.altKey) return; e.stopPropagation();   /* a letter typed here stays here: the page would hand it to the field */
    if (e.key === "Escape") { e.preventDefault(); if (card.view === "add") { openSheet("members", true); return; } quiet = card.key; hide(); return; }   /* Add goes back to the members; else the row it gives focus back to keeps it closed */
    if (card.view === "add" && onSheetKey(e)) return;
    if (e.key === "Enter" && e.target.matches("input") && !e.isComposing) { e.preventDefault(); openTopic(e.target); return; }
    if (e.key !== "Tab") return; e.preventDefault(); const s = stops(), i = s.indexOf(e.target), r = rowOf(card.key);
    if (e.shiftKey) { if (i > 0) enter(s[i - 1]); else if (card.view) enter(s[s.length - 1]); else if (r) M.focusKbd(r); else hide(); return; }
    if (i < s.length - 1) { enter(s[i + 1]); return; } if (card.view) { enter(s[0]); return; }   /* the members go round */
    const n = r && nextStop(r); hide({stay: true}); if (n) M.focusKbd(n); else ready(); }

  /* ---------- wired once, when the chunk arrives ---------- */
  navScroll.addEventListener("pointerover", (e) => { if (!(card && card.el.contains(document.activeElement))) want(keyOf(e.target)); });   /* a card the keyboard is in stays */
  navScroll.addEventListener("pointerleave", () => { quiet = ""; want(""); });
  /* a press on a row is for the row: its card goes, and stays away until the pointer leaves the row */
  navScroll.addEventListener("pointerdown", (e) => { quiet = keyOf(e.target); clearTimeout(showTimer); pending = ""; if (card) hide({stay: true}); }, true);
  navScroll.addEventListener("pointerout", (e) => { if (quiet && keyOf(e.relatedTarget) !== quiet) quiet = ""; });
  navScroll.addEventListener("focusin", (e) => { const k = keyOf(e.target); if (k !== quiet) quiet = ""; if (k) want(k); });
  navScroll.addEventListener("focusout", (e) => { if (!keyOf(e.relatedTarget) && !(card && card.el.contains(e.relatedTarget))) want(""); });
  navScroll.addEventListener("keydown", onRowKey);
  navScroll.addEventListener("scroll", () => { if (card && !holding()) hide({stay: true}); }, {passive: true});
  /* a press anywhere else closes it, and keeps what was pressed */
  addEventListener("pointerdown", (e) => { if (card && !card.el.contains(e.target) && !navScroll.contains(e.target)) hide({stay: true}); }, true);

  /* what the first load tells this chunk: a channel's members changed (channels.js) */
  function membersChanged() { if (!card || card.kind !== "channel") return; if (card.view) { drawSheet(); return; } const b = $("[data-card-members]", card.el); if (b) { b.innerHTML = membersHTML(card.sid); host(b); } }
  return {membersChanged, get card() { return card && {key: card.key, el: card.el}; }};
}
