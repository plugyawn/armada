/* INTERIM BETA (README.md): New, from the nav's "+": a raised surface under it in which a channel is made, with its name, a
   description and who can see it; and the channels I am in, kept in the nav as I join and leave them (the `subscription`
   event). A channel is made by POST channels/create, Zulip's own, which refuses a name that is taken; POST
   users/me/subscriptions would quietly subscribe me to the channel that has it. Its sheet is channels.css. */

import {$, $$, el, press, T} from "./util.js";
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
  if (opening && streams.has(opening)) openFirst(opening);
}
/* a channel's first place: its latest topic, as the first visit opens the first repository's; with none yet, its general chat */
async function openFirst(sid) { opening = 0; await loadTopics(sid); const t = (topicsOf.get(sid) || [])[0]; openPlace(placeFromKey(tkey(sid, t ? t.name : ""))); }

/* ---------- New: a raised surface under the "+", over the nav and no further. Return creates, Tab goes round its fields,
   the arrows choose who can see it; Esc, a press outside or focus going elsewhere closes it, and focus goes back to where
   it was. A refusal is said in plain words in its caption, and what I typed stays. ---------- */
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
    <p class="chn-say" aria-live="polite">${HINTS.public}</p>`);
  box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "New channel"); box._back = document.activeElement;
  document.body.append(box); host(box);
  press(box, "[data-who]", (b) => choose(b.dataset.who));
  press(box, "[data-create]", create);
  box.addEventListener("input", () => say(""));
  box.addEventListener("keydown", onKey);
  /* a press on anything but a field keeps the caret where it is, so the page's own press rules (host.js) leave it there */
  box.addEventListener("mousedown", (e) => { if (!e.target.closest("input")) e.preventDefault(); });
  addEventListener("pointerdown", outside, true); document.addEventListener("focusin", away);
  plus.classList.add("open"); plus.setAttribute("aria-expanded", "true");
  M.release(box, {y: -(T["pane-rise"] || 4)}, "move");
  fieldOf("name").focus(); }
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
  const radio = e.target.closest("[data-who]");
  if (e.key === "Escape") closeNew();
  else if (e.key === "Enter" && !e.shiftKey && !e.isComposing) create();
  else if (e.key === "Tab") { const stops = $$("input, button", box).filter((x) => x.tabIndex >= 0), i = stops.indexOf(e.target), n = stops[(i + (e.shiftKey ? stops.length - 1 : 1)) % stops.length];
    if (n.matches("button")) M.focusKbd(n); else n.focus(); }
  else if (radio && /^Arrow(Left|Right|Up|Down)$/.test(e.key)) choose(radio.dataset.who === "public" ? "private" : "public", true);
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
    opening = r.id; if (streams.has(r.id)) openFirst(r.id); }
  catch (e) { b._busy = false; verb.textContent = "Create"; if (box !== b) return; refuse($("[data-create]", b), refusal(e, name, priv)); fieldOf("name").focus(); } }

export {applyChannels, onSubscription, wireChannels};
