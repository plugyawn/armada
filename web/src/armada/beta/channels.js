/* INTERIM BETA (README.md): New, from the nav's "+": a raised surface under it in which a channel is made, with its name, a
   description and who can see it, and under that the public channels I am not in, each with Join; and the channels I am
   in, kept in the nav as I join and leave them (the `subscription` event). A channel is made by POST channels/create,
   Zulip's own, which refuses a name that is taken; POST users/me/subscriptions would quietly subscribe me to the channel
   that has it. Joining is that endpoint, by name, as the old interface joins. Its sheet is channels.css. */

import {$, $$, el, esc, press, T} from "./util.js";
import {repoStreams, state, streams, topicsOf} from "./state.js";
import {api} from "./connection.js";
import {openPlace, placeFromKey, tkey} from "./routing.js";
import {nav} from "./frame.js";
import {ready} from "./composer.js";
import {drawNav, loadTopics} from "./nav.js";

/* ---------- the channels I am in, as the server says they change ---------- */
const repositories = new Set();   /* the channels bound to a repository that I can see, in them or not (register) */
let opening = 0;                  /* a channel I made: its first place opens once the server says I am in it */
function applyChannels(r) { repositories.clear(); for (const id of r.repository_stream_ids || []) repositories.add(id); }
function onSubscription(ev) {
  if (ev.op === "add") for (const s of ev.subscriptions) { streams.set(s.stream_id, s); if (repositories.has(s.stream_id)) repoStreams.add(s.stream_id); }
  else if (ev.op === "remove") for (const s of ev.subscriptions) { streams.delete(s.stream_id); repoStreams.delete(s.stream_id); }
  else return;   /* someone else joining or leaving, a setting of mine: nothing the nav shows */
  drawNav();
  if (box && ev.op === "add") for (const s of ev.subscriptions) { const b = $(`[data-join="${s.stream_id}"]`, box); if (b) mark(b, "joined"); }   /* joined from another tab */
  if (opening && streams.has(opening)) openFirst(opening);
}
/* a channel's first place, once I am in it */
function openWhenIn(sid) { opening = sid; if (streams.has(sid)) openFirst(sid); }
/* a channel's first place: its latest topic, as the first visit opens the first repository's; with none yet, its general chat */
async function openFirst(sid) { opening = 0; await loadTopics(sid); const t = (topicsOf.get(sid) || [])[0]; openPlace(placeFromKey(tkey(sid, t ? t.name : ""))); }

/* ---------- New: a raised surface under the "+", over the nav and no further. Return creates, Tab goes round its fields
   and the list, the arrows choose who can see it and move along the list; Esc, a press outside or focus going elsewhere
   closes it, and focus goes back to where it was. A refusal is said in plain words in its caption, and what I typed
   stays. ---------- */
let box = null, plus = null;
const HINTS = {public: "Anyone here can find it and join.", private: "Only the people in it can see it."};
const whoHTML = (v, on) => `<button role="radio" data-who="${v}" aria-checked="${on}" tabindex="${on ? 0 : -1}"${on ? ' class="on"' : ""}>${v === "public" ? "Public" : "Private"}</button>`;
const who = () => $("[data-who][aria-checked='true']", box).dataset.who;
const fieldOf = (f) => $(`[data-f="${f}"]`, box);
function wireChannels() { plus = $(".top .new", nav); plus.setAttribute("aria-haspopup", "dialog"); plus.setAttribute("aria-expanded", "false");
  plus.addEventListener("mousedown", (e) => e.preventDefault());   /* the press leaves focus where it was, to be given back there */
  press(nav, ".top .new", () => (box ? closeNew() : openNew())); }
function openNew() {
  box = el("div", "chn raised", `<h2 class="chn-h">New channel</h2>
    <input class="chn-in" data-f="name" maxlength="60" placeholder="Name" aria-label="Name" autocomplete="off" spellcheck="false">
    <input class="chn-in" data-f="description" maxlength="1024" placeholder="What it is for" aria-label="Description" autocomplete="off">
    <div class="chn-act"><div class="seg" role="radiogroup" aria-label="Who can see it">${whoHTML("public", true)}${whoHTML("private", false)}</div><button class="go" data-create><span>Create</span><i aria-hidden="true">Creating</i></button></div>
    <p class="chn-say" aria-live="polite">${HINTS.public}</p>
    <h2 class="chn-h">Channels to join</h2><ul class="chn-ls" aria-label="Channels to join"><li class="chn-none">Loading</li></ul>`);
  box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "New channel"); box._back = document.activeElement;
  document.body.append(box); host(box);
  press(box, "[data-who]", (b) => choose(b.dataset.who));
  press(box, "[data-create]", create);
  press(box, "[data-join]", pressRow);
  box.addEventListener("input", () => say(""));
  box.addEventListener("keydown", onKey);
  /* a press on anything but a field keeps the caret where it is, so the page's own press rules (host.js) leave it there */
  box.addEventListener("mousedown", (e) => { if (!e.target.closest("input")) e.preventDefault(); });
  addEventListener("pointerdown", outside, true); document.addEventListener("focusin", away);
  plus.classList.add("open"); plus.setAttribute("aria-expanded", "true");
  M.release(box, {y: -(T["pane-rise"] || 4)}, "move");
  fieldOf("name").focus(); loadJoinable(box); }
const outside = (e) => { if (!box.contains(e.target) && !e.target.closest(".top .new")) closeNew(); };
const away = (e) => { if (!box.contains(e.target)) closeNew({stay: true}); };
function closeNew(o = {}) { const b = box, held = b.contains(document.activeElement); box = null;
  removeEventListener("pointerdown", outside, true); document.removeEventListener("focusin", away);
  plus.classList.remove("open"); plus.setAttribute("aria-expanded", "false"); b.inert = true; M.leave(b);
  if (held && !o.stay) giveBack(b._back); }
/* focus back where it was: a control (the "+" from the keyboard) with its ring; the field, or nothing, as the page hands it back */
function giveBack(back) { if (back && back.isConnected && back.matches("button")) M.focusKbd(back); else ready(); }
function onKey(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;   /* ⌘K, ⌘. and the rest are the page's */
  e.stopPropagation();   /* a letter typed on a control here stays here: the page would hand it to the conversation's field */
  const radio = e.target.closest("[data-who]"), row = e.target.closest("[data-join]");
  if (e.key === "Escape") closeNew();
  else if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { if (row) pressRow(row); else create(); }
  else if (e.key === "Tab") { const stops = $$("input, button", box).filter((x) => x.tabIndex >= 0), i = stops.indexOf(e.target), n = stops[(i + (e.shiftKey ? stops.length - 1 : 1)) % stops.length];
    if (n.matches("button")) M.focusKbd(n); else n.focus(); }
  else if (radio && /^Arrow(Left|Right|Up|Down)$/.test(e.key)) choose(radio.dataset.who === "public" ? "private" : "public", true);
  else if (row && (e.key === "ArrowDown" || e.key === "ArrowUp")) step(row, e.key === "ArrowDown" ? 1 : -1);
  else return;
  e.preventDefault(); }
function choose(v, focus) { for (const b of $$("[data-who]", box)) { const on = b.dataset.who === v; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; if (on && focus) M.focusKbd(b); }
  say(""); }
/* the caption: what the choice means, or why Create was refused, in the failure ink */
function say(text) { const p = $(".chn-say", box); p.textContent = text || HINTS[who()]; p.toggleAttribute("data-bad", !!text); }
function refuse(target, text) { say(text); M.refuse(target); S.play("nope"); }
const refusal = (e, name, priv) => (e.code === "CHANNEL_ALREADY_EXISTS" ? `A channel named “${name}” already exists.`
  : e.message === "Insufficient permission" ? `You are not allowed to create ${priv ? "private" : "public"} channels.`
  : e.message === "Not allowed for guest users" ? "Guests cannot create channels." : "Not created: " + e.message);
async function create() { const b = box; if (b._busy) return;
  const name = fieldOf("name").value.trim(), description = fieldOf("description").value.trim(), priv = who() === "private", verb = $("[data-create] > span", b);
  if (!name) { refuse(fieldOf("name"), "Give the channel a name."); fieldOf("name").focus(); return; }
  b._busy = true; verb.textContent = "Creating";
  try { const r = await api("POST", "channels/create", {name, description, invite_only: priv, subscribers: [state.ME]});
    if (box === b) closeNew();
    openWhenIn(r.id); }
  catch (e) { b._busy = false; verb.textContent = "Create"; if (box !== b) return; refuse($("[data-create]", b), refusal(e, name, priv)); fieldOf("name").focus(); } }

/* ---------- the public channels I am not in: asked for each time the surface opens (GET streams), a row each, its verb in
   a cell as wide as "Joining" at the row's end. Join is one press: the row says Joining at once and Open once the server
   has me in it, and keeps its place; the nav takes the channel when the `subscription` event comes. ---------- */
async function loadJoinable(b) { let list;
  try { list = (await api("GET", "streams", {include_public: true, include_subscribed: false, exclude_archived: true})).streams; }
  catch (e) { if (box === b) drawJoinable([], "Not loaded: " + e.message); return; }
  if (box === b) drawJoinable(list.filter((s) => !s.invite_only && !s.is_archived && !streams.has(s.stream_id)).sort((x, y) => x.name.localeCompare(y.name)), "No other public channels."); }
function drawJoinable(list, none) { const ls = $(".chn-ls", box);
  ls.innerHTML = !list.length ? `<li class="chn-none">${esc(none)}</li>` : list.map((s, i) => `<li class="chn-row"><span class="chn-g">${ic(repositories.has(s.stream_id) ? "repo" : "hash")}</span><span class="lab"${s.description ? ` data-tip="${esc(s.description)}"` : ""}>${esc(s.name)}</span><button class="lk" data-join="${s.stream_id}" data-name="${esc(s.name)}" aria-label="Join ${esc(s.name)}" tabindex="${i ? -1 : 0}"><span>Join</span><i aria-hidden="true">Joining</i></button></li>`).join("");
  host(ls); M.appear(ls); }
/* a row's state: "" (Join), "joining", "joined" (Open, and the tick where its glyph was) */
function mark(b, st) { const row = b.closest("li"), name = b.dataset.name; row.dataset.state = st;
  b.firstElementChild.textContent = st === "joined" ? "Open" : st === "joining" ? "Joining" : "Join"; b.setAttribute("aria-label", st === "joined" ? `Open ${name}, joined` : `${b.firstElementChild.textContent} ${name}`);
  if (st === "joined") $(".chn-g", row).innerHTML = ic("check"); }
function pressRow(b) { const st = b.closest("li").dataset.state, sid = +b.dataset.join;
  if (st === "joined") { closeNew(); openWhenIn(sid); } else if (!st) join(b); }
async function join(b) { mark(b, "joining");
  try { await api("POST", "users/me/subscriptions", {subscriptions: [{name: b.dataset.name}]}); mark(b, "joined"); }
  catch (e) { mark(b, ""); if (!box || !box.contains(b)) return; M.say(b, "Not joined: " + e.message, 2400); M.refuse(b); S.play("nope"); } }
/* Up and Down along the list: one stop in the Tab order, the row I was on; the list scrolls to keep it in view */
function step(b, d) { const all = $$("[data-join]", box), n = all[Math.max(0, Math.min(all.length - 1, all.indexOf(b) + d))], ls = n.closest("ul"), r = n.closest("li");
  for (const x of all) x.tabIndex = x === n ? 0 : -1; M.focusKbd(n);
  if (r.offsetTop < ls.scrollTop) ls.scrollTop = r.offsetTop; else if (r.offsetTop + r.offsetHeight > ls.scrollTop + ls.clientHeight) ls.scrollTop = r.offsetTop + r.offsetHeight - ls.clientHeight; }

export {applyChannels, onSubscription, wireChannels};
