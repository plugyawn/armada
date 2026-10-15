/* INTERIM BETA (README.md): a person's card (any face opens it) and the evolution moment. */

import {$, $$, el, esc, press, T} from "./util.js";
import {marks, people, state, streams} from "./state.js";
import {age, now} from "./time.js";
import {api, signOut} from "./connection.js";
import {nav, tx} from "./frame.js";
import {bigFaceOf, faceOf, isOnix, nameOf, person, presOf} from "./faces.js";
import {note, ready, syncField} from "./composer.js";
import {info} from "./receipts.js";
import {dialogOpen, menu} from "./hover.js";
import {pop} from "./model_picker.js";
import {pal} from "./palette.js";
import {dkey, openPlace, placeFromKey, placeOfMsg, topicLabel} from "./routing.js";
import {applyPartners, hopOnce, me_, pack, partnerOf, pkBig, speciesName, withIcons} from "./partners.js";
import {nextForms, openPicker, stepsFrom, stoneOfSpecies} from "./partner_picker.js";

/* ----- a person's card (statement 27: "I should be able to click the profile icon and do something"): a raised card beside the
   face that was pressed, or above my own face in the account row. Their partner large on its circle, its species, level and
   how far to the next form; for someone else their local time, whether they are here, and where they last spoke (with a live
   run there marked), then Message, Mention and their page; for me my experience, what comes next, my stone, Evolve when I held
   it, the branch's choices when it waits on me, Change partner and Sign out. Onix has no face, so no card. ----- */
let card = null;
const CARD_FACE = 72;
function drawMeRow() { const r = $(".row.me", nav); if (!r) return; const me = me_(), t = el("template"); t.innerHTML = faceOf(state.ME, "s20"); const f = $(":scope > .av, :scope > .pk", r), neu = t.content.firstElementChild;
  if (f && neu && f.dataset.fs !== neu.dataset.fs) f.replaceWith(neu);
  const wait = !!(me && me.ready && !me.held && withIcons(me.ready.choices).length > 1), mark = $(":scope > .mk", r); if (wait && !mark) r.insertAdjacentHTML("beforeend", mk("needs")); else if (!wait && mark) mark.remove();
  r.dataset.tip = "Signed in as " + state.meName; }

/* what the card says, as plain values: kept apart from the drawing so tests can read them */
/* whether they are here: the presence the page holds, in words */
function presenceWords(uid, pres = presOf(uid), presences = state.presences, at = now()) { if (uid === state.ME || pres === "on") return "Active now"; if (pres === "away") return "Away";
  const p = presences[person(uid).email], ts = p && p.aggregated && p.aggregated.timestamp; if (!ts || at / 1000 - ts >= 86400 * 14) return "Offline"; const a = age(ts); return a === "now" ? "Active now" : `Active ${a} ago`; }
/* their clock: the time where they are, and the place their time zone names ("14:20 in Kolkata"); "" when they set none */
function localTime(tz, at = now()) { if (!tz) return ""; try { const t = new Intl.DateTimeFormat(undefined, {hour: "numeric", minute: "2-digit", timeZone: tz}).format(new Date(at));
  const city = tz.split("/").pop().replace(/_/g, " "); return /^(UTC|GMT|Etc)/.test(tz) ? t + " " + tz : `${t} in ${city}`; } catch (e) { return ""; } }
/* how far a partner has grown toward its next form: [the share of the way, the next forms, the level they come at]. The chosen
   form is always its line's first (the server allows no other), so the stage is the count of forms before this one. */
function growth(species, level, levels) { const s = pack && pack.species[species]; if (!s) return [0, [], null];
  const step = stepsFrom(s.line, species), next = withIcons(nextForms(species)), at = next.length ? levels[step] || null : null;
  if (!at) return [Math.min(1, level / 100), next, null];
  const from = step ? levels[step - 1] : 1; return [Math.max(0, Math.min(1, (level - from) / (at - from))), next, at]; }
/* where they last spoke, from their latest message I can see: the place's key and its name */
function lastPlace(m) { if (!m) return null; const key = placeOfMsg(m);
  if (m.type === "stream") { const s = streams.get(m.stream_id); return {key, label: `${s ? s.name : m.display_recipient} / ${topicLabel(m.subject ?? m.topic ?? "")}`, ts: m.timestamp}; }
  return {key, label: "A direct message", ts: m.timestamp}; }
/* a mention of them, as the field writes one: the name alone unless another person shares it */
function mentionOf(uid) { const name = person(uid).name, twin = [...people.values()].some((u) => u.id !== uid && u.active && u.name === name); return twin ? `@**${name}|${uid}**` : `@**${name}**`; }

/* what the card fetches for someone else: their time zone and their latest message, once a minute at most */
const profiles = new Map();
async function loadProfile(uid) { const had = profiles.get(uid); if (had && performance.now() - had.at < 60000) return;
  const prof = {at: performance.now(), tz: had ? had.tz : undefined, last: had ? had.last : undefined}; profiles.set(uid, prof);
  const [u, m] = await Promise.allSettled([api("GET", `users/${uid}`), api("GET", "messages", {anchor: "newest", num_before: 1, num_after: 0, narrow: [{operator: "sender", operand: uid}], apply_markdown: false, client_gravatar: true})]);
  /* undefined while asked, null when the server could not say (shown as such, never as an empty cell) */
  prof.tz = u.status === "fulfilled" ? (u.value.user && u.value.user.timezone) || "" : prof.tz !== undefined ? prof.tz : null;
  prof.last = m.status === "fulfilled" ? lastPlace(m.value.messages[m.value.messages.length - 1]) || "" : prof.last !== undefined ? prof.last : null;
  if (card && +card.dataset.uid === uid) drawFacts(); }

function openCard(uid = state.ME, from = null) { if (moment) return; if (card && +card.dataset.uid === uid) { closeCard(); return; } if (card) closeCard(); if (!(uid > 0) || isOnix(uid)) return;
  const c = card = el("div", "pcard raised float"); card.dataset.uid = uid; card.setAttribute("role", "dialog"); card.setAttribute("aria-label", uid === state.ME ? "You" : nameOf(uid)); card.tabIndex = -1;
  c.opener = {back: document.activeElement, from}; document.body.append(card); drawCard(); placeCard(from);   /* what had focus, for when the card closes (closeCard) */
  card.addEventListener("keydown", cardKey); M.release(card, {y: T["pane-rise"] || 4}, "move"); M.appear(card); if (uid !== state.ME) loadProfile(uid);
  /* a dialog takes focus, on the card itself so that Return presses nothing; after the press that opened it has ended */
  setTimeout(() => { if (card === c) c.focus({preventScroll: true}); }, 0); }
/* Tab goes round the card's buttons while it is open; Esc closes it, and focus goes back to what had it (closeCard). Esc
   with focus outside the card is host.js's. */
function cardKey(e) { if (!card || moment || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeCard(); return; }
  if (e.key !== "Tab") return; const b = $$("button:not([disabled])", card); if (!b.length) return;
  e.preventDefault(); const i = b.indexOf(document.activeElement); b[i < 0 ? (e.shiftKey ? b.length - 1 : 0) : (i + (e.shiftKey ? b.length - 1 : 1)) % b.length].focus(); }
/* beside the face that was pressed, its top a little above the face's, inside the window; my own card keeps its place in CSS,
   above the account row */
function placeCard(from) { if (!card || !from || !from.isConnected) return; card.classList.add("at"); const r = from.getBoundingClientRect(), w = card.offsetWidth, h = card.offsetHeight, gap = 8;
  let left = r.right + gap; if (left + w > innerWidth - gap) left = r.left - w - gap; const top = Math.max(gap, Math.min(innerHeight - h - gap, r.top - gap));
  card.style.left = `${Math.round(Math.max(gap, left))}px`; card.style.top = `${Math.round(top)}px`; }
function closeCard() { if (!card || card.classList.contains("moment")) return; const c = card, held = c.contains(document.activeElement); card = null; c.inert = true; M.leave(c); if (held) giveFocusBack(c); }
/* a dialog's rule: a card closed while it holds focus gives it back to what had it when the card opened (the field, the account
   row, More). More's controls went with its menu, so the message it was opened from takes focus first, which brings them back up.
   Anything else, or nothing, and the field takes it (ready). A closing card is inert but holds focus until the browser moves it to
   the page, so it lets go first. */
const inertAt = (x) => { for (let n = x; n; n = n.parentElement) if (n.inert) return true; return false; };
function giveFocusBack(c) { const {back, from} = c.opener || {}, live = (x) => !!x && x.isConnected && x !== document.body;
  if (live(back) && inertAt(back) && live(from) && from.tabIndex >= 0) from.focus({preventScroll: true});
  if (live(back) && !inertAt(back)) back.focus({preventScroll: true});
  if (c.contains(document.activeElement)) { document.activeElement.blur(); ready(); } }
function drawCard() { if (!card || card.classList.contains("moment")) return; const uid = +card.dataset.uid, within = card.contains(document.activeElement); card.innerHTML = uid === state.ME ? myCardHTML() : theirCardHTML(uid); host(card); drawFacts();
  if (within && !card.contains(document.activeElement)) card.focus({preventScroll: true}); }   /* a redraw does not drop the focus out of the card */
const head = (uid, name) => `<div class="ih"><b>${esc(name)}</b><span class="e">${esc(presenceWords(uid))}</span></div><div class="stage">${bigFaceOf(uid, CARD_FACE)}</div>`;
function myCardHTML() { const me = me_(), btns = (first) => `<div class="btns">${first}<button class="btn" data-signout>Sign out</button></div>`;
  if (!pack || !me || !me.species) return head(state.ME, state.meName) + (pack ? `<p class="cap">Choose a partner: it is your face here, and it grows as you use Armada.</p>` : "") + btns(pack ? '<button class="btn" data-change>Choose partner</button>' : "");
  const s = pack.species[me.species], r = me.ready, into = me.next_level_xp ? Math.max(0, Math.min(1, (me.xp - me.level_xp) / (me.next_level_xp - me.level_xp))) : 1;
  const [, nx, at] = growth(me.species, me.level, me.evolve_levels || [16, 30]);
  const next = r && r.kind === "evolve" ? "" : nx.length && at ? `Next: ${nx.map((x) => pack.species[x].name).join(" or ")} at level ${at}.` : nx.length ? "" : me.shiny ? "The end of its line, and shiny." : "The end of its line.";
  const choices = r ? withIcons(r.choices) : [];
  let act = ""; if (r && choices.length > 1) act = `<p>${esc(s.name)} can evolve. Choose what it becomes:</p><div class="seg pkpicks">${choices.map((c) => `<button data-evolve="${esc(c)}">${esc(pack.species[c].name)}</button>`).join("")}</div>`;
  else if (r && me.held) act = `<p>You kept ${esc(s.name)} as it is.</p><div class="btns"><button class="go" data-evolve="${esc(choices[0])}">${r.kind === "shiny" ? "Make it shiny" : "Evolve"}</button></div>`;
  return head(state.ME, state.meName) + `<p class="pkline"><span>${esc(s.name)}${me.shiny ? " (shiny)" : ""}</span><span class="e">Level <span class="fig">${fig(me.level)}</span></span></p>`
    + `<div class="xpbar" role="img" aria-label="${me.xp} experience"><i style="width:${Math.round(into * 100)}%"></i></div><p class="cap">${me.next_level_xp ? `${me.xp - me.level_xp} of ${me.next_level_xp - me.level_xp} to level ${me.level + 1}` : "Level 100"}. Today +${me.today_xp}.</p>`
    + (next ? `<p class="cap">${esc(next)}</p>` : "") + `<p class="cap">Your bubbles are ${esc(partnerOf(state.ME) ? partnerOf(state.ME).stone : stoneOfSpecies(me.species))}, as others see them.</p>${act}` + btns('<button class="btn" data-change>Change partner</button>'); }
function theirCardHTML(uid) { const p = partnerOf(uid), levels = (me_() && me_().evolve_levels) || [16, 30];
  let partner = ""; if (p) { const [share, nx, at] = growth(p.species, p.level || 1, levels);
    partner = `<p class="pkline"><span>${esc(speciesName(p.species))}${p.shiny ? " (shiny)" : ""}</span><span class="e">Level <span class="fig">${fig(p.level || 1)}</span></span></p>`
      + `<div class="xpbar" role="img" aria-label="Level ${p.level || 1}"><i style="width:${Math.round(share * 100)}%"></i></div><p class="cap">${esc(nx.length && at ? `${nx.map((x) => pack.species[x].name).join(" or ")} at level ${at}.` : "The end of its line.")}</p>`; }
  const where = state.place && (state.place.kind === "topic" || state.place.kind === "dm"), page = placeFromKey(`page:${uid}`);
  return head(uid, nameOf(uid)) + partner + `<dl class="facts"><dt>Local time</dt><dd data-fact="time"></dd><dt>Now</dt><dd data-fact="now"></dd></dl>`
    + `<div class="btns">${page ? '<button class="btn" data-card-page>Page</button>' : ""}${where ? '<button class="btn" data-card-mention>Mention</button>' : ""}<button class="go" data-card-dm>Message</button></div>`; }
/* the two facts that arrive after the card opens fill cells that are already there, so the card never changes size */
function drawFacts() { if (!card) return; const uid = +card.dataset.uid, prof = profiles.get(uid), t = $('[data-fact="time"]', card), n = $('[data-fact="now"]', card); if (!t || !n) return;
  t.textContent = !prof || prof.tz === undefined ? "…" : prof.tz === null ? "Not known" : localTime(prof.tz) || "Not set";
  if (!prof || prof.last === undefined) { n.textContent = "…"; return; } if (!prof.last) { n.textContent = prof.last === null ? "Not known" : "Nothing yet"; return; }
  const run = marks.get(prof.last.key); n.innerHTML = `${run ? mk(run) : ""}<button class="lk" data-card-go="${esc(prof.last.key)}">${esc(prof.last.label)}</button><span class="d"> · ${esc(age(prof.last.ts))}</span>`; host(n); }

/* the card's controls */
function mention(uid) { const f = state.threadTx && state.threadTx.isConnected ? state.threadTx : tx, text = f.textContent; f.textContent = text + (text && !/\s$/.test(text) ? " " : "") + mentionOf(uid) + " "; if (f === tx) syncField();
  closeCard(); f.focus({preventScroll: true}); const s = getSelection(); s.selectAllChildren(f); s.collapseToEnd(); }
/* a press on a face, anywhere but in the nav, the palette, the picker and a card, opens that person's card. It is taken
   before the row around the face sees it (a Home row, a person in the column), so the face's press is the card's alone. */
const FACE = "[data-uid][data-fs]";
function faceAt(e) { const f = e.target && e.target.closest && e.target.closest(FACE); if (!f || f.closest("nav, .pal, .pcard, #picker, .pkdetail, .pkcol")) return null; const uid = +f.dataset.uid; return uid > 0 && !isOnix(uid) ? f : null; }
function wireCard() {
  document.addEventListener("pointerdown", (e) => { if (e.button !== 0 || e.ctrlKey) return; const f = faceAt(e); if (!f) return; e.stopPropagation(); f._pd = performance.now(); openCard(+f.dataset.uid, f); }, true);
  document.addEventListener("click", (e) => { const f = faceAt(e); if (!f) return; e.stopPropagation(); e.preventDefault(); if (f._pd && performance.now() - f._pd < 1500) { f._pd = 0; return; } openCard(+f.dataset.uid, f); }, true);
  /* the card's controls act on the press, as every control on the page does; Sign out waits for the release, so a press
     that slides off it is not a sign-out */
  const go = (p) => { closeCard(); openPlace(p); };
  press(document.body, ".pcard [data-card-dm], .pcard [data-card-mention], .pcard [data-card-page], .pcard [data-card-go]", (b) => { if (!card || !card.contains(b)) return; const uid = +card.dataset.uid;
    if (b.matches("[data-card-dm]")) go(placeFromKey(dkey([uid])));
    else if (b.matches("[data-card-mention]")) mention(uid);
    else if (b.matches("[data-card-page]")) go(placeFromKey(`page:${uid}`));
    else go(placeFromKey(b.dataset.cardGo)); });
  document.body.addEventListener("click", (e) => { const b = e.target.closest && e.target.closest(".pcard [data-signout]"); if (b && card && card.contains(b)) signOut(); });
}
async function evolveTo(into, hold) { try { applyPartners(await api("POST", "armada/partner/evolve", hold ? {hold: true} : {into})); } catch (e) { note(e.message); } }

/* ----- the moment: about 1.4 s in the card, only for me, only when I am idle. Outlines of the old and new forms trade places
   faster and faster, eight points burst, the new form lands in colour with one hop. Any key or a press outside ends it where it
   stands (the evolution happens); Stop, or Esc with the field empty, keeps the old form (held). It never takes focus. ----- */
let moment = null, momentTimer = 0;
function momentDue() { clearTimeout(momentTimer); const me = me_(); if (!pack || !me || !me.ready || me.held || moment) return; if (withIcons(me.ready.choices).length > 1) { drawMeRow(); return; }
  const idle = document.visibilityState === "visible" && !state.pressing && performance.now() - state.lastKey >= 3000 && !(document.activeElement === tx && tx.textContent.trim()) && !pal && !menu && !pop && !dialogOpen && !info && !(state.place && state.place.kind === "partner");
  if (idle) playMoment(withIcons(me.ready.choices)[0]); else momentTimer = setTimeout(momentDue, 500); }
function playMoment(into) { const me = me_(), from = me.species, shiny = me.ready.kind === "shiny"; if (card) { card.remove(); card = null; }
  moment = {into, from, shiny, done: false, timers: []}; const c = card = el("div", "pcard raised float moment"); c.dataset.uid = state.ME; c.setAttribute("role", "status"); document.body.append(c);
  const oldImg = pkBig(from, me.shiny, 2, "old"), newImg = pkBig(into, shiny || me.shiny, 2, "new");
  c.innerHTML = `<div class="ih"><b>${esc(pack.species[from].name)} is evolving</b><span class="e">Level <span class="fig">${fig(me.level)}</span></span></div><div class="stage">${oldImg}${newImg}<span class="bits"></span></div><p class="say"></p><p class="cap"></p><div class="btns"><button class="btn" data-stop>Stop</button></div>`;
  const stage = $(".stage", c), o = $(".pk.old", stage), n = $(".pk.new", stage); n.hidden = true; M.release(c, {y: T["pane-rise"] || 4}, "move"); M.appear(c);
  const at = (ms, fn) => moment.timers.push(setTimeout(fn, ms * M.SLOW)); S.play("evolve");
  if (M.reduced()) { at(500, () => { o.hidden = true; n.hidden = false; M.appear(n, 0, 200); }); at(700, () => landMoment()); return; }
  at(340, () => o.classList.add("ghost")); let t = 340, showNew = false;
  for (const d of [240, 200, 160, 120, 90, 70, 60]) { t += Math.round(d * 0.81); at(t, () => { showNew = !showNew; o.hidden = showNew; n.hidden = !showNew; n.classList.add("ghost"); }); }
  at(1100, () => { const bits = $(".bits", stage); bits.innerHTML = "<i></i>".repeat(8); stage.classList.add("pulse"); });
  at(1380, () => landMoment()); }
function landMoment() { if (!moment || moment.done) return; const c = card, {from, into, shiny} = moment; moment.done = true; moment.timers.forEach(clearTimeout);
  const o = $(".pk.old", c), n = $(".pk.new", c); if (o) o.hidden = true; if (n) { n.hidden = false; n.classList.remove("ghost"); hopOnce(c.querySelector(".stage")); }
  const name = pack.species[into].name; $(".ih b", c).textContent = shiny ? `${name} (shiny)` : name; $(".say", c).textContent = shiny ? `${name} turned shiny.` : `${pack.species[from].name} evolved into ${name}.`;
  const nx = withIcons(nextForms(into)), L = (me_() && me_().evolve_levels) || [16, 30], lvl = L[stepsFrom(me_().chosen, into)];
  $("p.cap", c).textContent = `Your bubbles ${stoneOfSpecies(into) === stoneOfSpecies(from) ? "stay" : "are now"} ${stoneOfSpecies(into)}.${nx.length && lvl ? ` Next: ${nx.map((x) => pack.species[x].name).join(" or ")} at level ${lvl}.` : ""}`;
  const st = $("[data-stop]", c); if (st) st.remove(); evolveTo(into, false);
  setTimeout(() => endMoment(), 4000 * M.SLOW); }
/* a key or a press outside: the evolution happens and the card shows its end at once, then fades */
function cutMoment() { if (!moment) return; if (!moment.done) landMoment(); endMoment(true); }
function endMoment(now_) { if (!moment || !card) { moment = null; return; } if (!now_ && card.matches(":hover")) { setTimeout(() => endMoment(), 1000); return; } const c = card; card = null; moment = null; c.inert = true; M.leave(c); }
function holdMoment() { if (!moment || moment.done) return; moment.timers.forEach(clearTimeout); const c = card, from = moment.from; moment = null; c.classList.remove("moment");
  const o = $(".pk.old", c), n = $(".pk.new", c); if (o) { o.hidden = false; o.classList.remove("ghost"); } if (n) n.hidden = true; $(".stage", c).classList.remove("pulse"); $(".bits", c).innerHTML = "";
  $(".ih b", c).textContent = pack.species[from].name; $(".say", c).textContent = `${pack.species[from].name} stays as it is. Evolve it from this card when you like.`; evolveTo(null, true).then(() => drawCard()); }

export {card, closeCard, cutMoment, drawCard, drawMeRow, faceAt, growth, holdMoment, lastPlace, localTime, mentionOf, moment, momentDue,
  openCard, playMoment, presenceWords, wireCard};
