/* INTERIM BETA (README.md): a chunk (chunks.js): what the nav says of a channel or a topic when the pointer rests on its
   row (statement 53). A card raised beside the nav, its head level with the row's: for a channel, what it is for, who is
   in it, its repository, and "+" for a new topic (a worktree, in a repository); for a topic, who has spoken there, the
   latest word and its run. It comes up as a tooltip does: after the pointer rests on a row for the tooltip's delay, at
   once while a card is up, and it waits for the pointer crossing to it. A row that takes focus from the keyboard brings
   its card up too; Tab goes into it, and on past it to the next row. It never stands in flow: it is over the
   conversation's edge, beside the pointer, so nothing under the pointer moves.
   It imports nothing the first load holds: the page is handed to its default export, which wires the nav when the chunk
   arrives (prefetched once the page is idle; before that a row has no card). */

export default function channelViewChunk(P) {
  const {$, $$, el, esc, T, streams, repoStreams, members, marks, places, topicsOf, worktrees, runsSeen, people, age,
    api, faceOf, nameOf, presOf, isOnix, openPlace, placeFromKey, tkey, topicLabel, loadTopics, loadMembers, repoPath, nav,
    navScroll, ready} = P;

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
      box.addEventListener("focusout", (e) => { if (card && card.el === box && !box.contains(e.relatedTarget) && !navScroll.contains(e.relatedTarget)) hide({stay: true}); });   /* focus gone elsewhere takes it */
      P.press(box, "[data-card-new]", newTopic);
      document.body.append(box); card = {el: box}; }
    Object.assign(card, at, {back: null}); card.el.setAttribute("aria-label", at.kind === "channel" ? streams.get(at.sid).name : topicLabel(at.topic));
    draw(); place(); if (before) M.flip(card.el, before, "touch"); else { M.fade(card.el, 1); M.release(card.el, {x: -(T["pane-rise"] || 4)}, "touch"); }
    if (at.kind === "topic") loadTopicFacts(at); }
  /* gone; focus that was in it goes back where it came from: the row it was entered from, else the field */
  function hide(o = {}) { clearTimeout(showTimer); clearTimeout(hideTimer); pending = ""; if (!card) return; const c = card, held = c.el.contains(document.activeElement); card = null; goneAt = performance.now();
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
      + `<dl class="facts">${fact("Members", membersHTML(sid), "members")}${repo ? fact("Repository", esc(repoOf(sid)), "repo") : ""}</dl>`; }
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
  /* a topic: its glyph (a worktree's branch, else a thread's) and its name, then its people, its latest word and its run.
     What is not known yet is "…" in a cell that is already there, so the card keeps its size as the facts arrive. */
  const RUN_WORDS = {needs: "Onix asks you", live: "Onix is working", queued: "Queued", failed: "The last run failed", paused: "Paused"};
  function topicHTML() { const {key, topic, sid} = card, wt = worktrees.get(sid), mark = marks.get(key), f = facts.get(key);
    return `<div class="ih">${ic(wt && wt.has(topic.toLowerCase()) ? "branch" : "thread")}<b>${esc(topicLabel(topic))}</b></div>`
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

  /* ---------- the keyboard: Tab from the row goes into its card, and from its last control to the next row; Shift-Tab from
     its first goes back to the row; Esc closes it and gives the row its focus; Return in the topic's name opens it ---------- */
  const stops = () => $$("button, input", card.el).filter((x) => !x.disabled);
  const enter = (x) => (x.matches("input") ? x.focus() : M.focusKbd(x));
  function nextStop(r) { const all = $$("button", nav).filter((x) => !x.closest("[hidden], [inert]")), i = all.indexOf(r); return all[i + 1] || null; }
  function onRowKey(e) { if (!card || e.key !== "Tab" || e.shiftKey || e.metaKey || e.ctrlKey || e.altKey || e.target !== rowOf(card.key)) return;
    const s = stops(); if (!s.length) return; e.preventDefault(); e.stopPropagation(); card.back = e.target; enter(s[0]); }
  function onCardKey(e) { if (e.metaKey || e.ctrlKey || e.altKey) return; e.stopPropagation();   /* a letter typed here stays here: the page would hand it to the field */
    if (e.key === "Escape") { e.preventDefault(); quiet = card.key; hide(); return; }   /* the row it gives focus back to keeps it closed */
    if (e.key === "Enter" && e.target.matches("input") && !e.isComposing) { e.preventDefault(); openTopic(e.target); return; }
    if (e.key !== "Tab") return; e.preventDefault(); const s = stops(), i = s.indexOf(e.target), r = rowOf(card.key);
    if (e.shiftKey) { if (i > 0) enter(s[i - 1]); else if (r) M.focusKbd(r); else hide(); return; }
    if (i < s.length - 1) { enter(s[i + 1]); return; } const n = r && nextStop(r); hide({stay: true}); if (n) M.focusKbd(n); else ready(); }

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
  function membersChanged() { if (!card || card.kind !== "channel") return; const d = $('[data-fact="members"]', card.el); if (d) { d.innerHTML = membersHTML(card.sid); host(d); } }
  return {membersChanged, get card() { return card && {key: card.key, el: card.el}; }};
}
