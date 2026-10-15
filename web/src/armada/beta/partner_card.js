/* INTERIM BETA (README.md): my partner card and the evolution moment. */

import {$, el, esc, T} from "./util.js";
import {state} from "./state.js";
import {api} from "./connection.js";
import {nav, tx} from "./frame.js";
import {faceOf} from "./faces.js";
import {note} from "./composer.js";
import {info} from "./receipts.js";
import {dialogOpen, menu} from "./hover.js";
import {pop} from "./model_picker.js";
import {pal} from "./palette.js";
import {applyPartners, hopOnce, me_, pack, partnerOf, pkBig} from "./partners.js";
import {nextForms, openPicker, stepsFrom, stoneOfSpecies} from "./partner_picker.js";

/* ----- my partner card: a raised card above my own face in the account row. My level and experience, what comes next, my
   stone, today's experience, Change partner; Evolve when I held it; the branch's choices when it waits on me. ----- */
let card = null;
function drawMeRow() { const r = $(".row.me", nav); if (!r) return; const me = me_(), t = el("template"); t.innerHTML = faceOf(state.ME, "s20"); const f = $(":scope > .av, :scope > .pk", r), neu = t.content.firstElementChild;
  if (f && neu && f.dataset.fs !== neu.dataset.fs) f.replaceWith(neu);
  const wait = !!(me && me.ready && !me.held && me.ready.choices.length > 1), mark = $(":scope > .mk", r); if (wait && !mark) r.insertAdjacentHTML("beforeend", mk("needs")); else if (!wait && mark) mark.remove();
  r.dataset.tip = !pack ? "Signed in as " + state.meName : me && me.species ? "Your partner" : "Choose your partner"; }
function openCard() { if (card) { closeCard(); return; } if (!pack) return; const me = me_(); if (!me || !me.species) { openPicker(); return; }
  card = el("div", "pcard raised float"); card.setAttribute("role", "dialog"); card.setAttribute("aria-label", "Your partner"); document.body.append(card); drawCard(); M.release(card, {y: T["pane-rise"] || 4}, "move"); M.appear(card); }
function closeCard() { if (!card || card.classList.contains("moment")) return; const c = card; card = null; c.inert = true; M.leave(c); }
function drawCard() { if (!card || card.classList.contains("moment")) return; const me = me_(); if (!me || !me.species) { closeCard(); return; } const s = pack.species[me.species], r = me.ready;
  const into = me.next_level_xp ? Math.max(0, Math.min(1, (me.xp - me.level_xp) / (me.next_level_xp - me.level_xp))) : 1;
  const nx = nextForms(me.species), L = me.evolve_levels || [16, 30], at = L[stepsFrom(me.chosen, me.species)];
  const next = r && r.kind === "evolve" ? "" : nx.length && at ? `Next: ${nx.map((x) => pack.species[x].name).join(" or ")} at level ${at}.` : nx.length ? "" : me.shiny ? "The end of its line, and shiny." : "The end of its line.";
  let act = ""; if (r && r.choices.length > 1) act = `<p>${esc(s.name)} can evolve. Choose what it becomes:</p><div class="seg pkpicks">${r.choices.map((c) => `<button data-evolve="${esc(c)}">${esc(pack.species[c].name)}</button>`).join("")}</div>`;
  else if (r && me.held) act = `<p>You kept ${esc(s.name)} as it is.</p><div class="btns"><button class="go" data-evolve="${esc(r.choices[0])}">${r.kind === "shiny" ? "Make it shiny" : "Evolve"}</button></div>`;
  card.innerHTML = `<div class="ih"><b>${esc(s.name)}${me.shiny ? " (shiny)" : ""}</b><span class="e">Level <span class="fig">${fig(me.level)}</span></span></div><div class="stage">${pkBig(me.species, me.shiny, 2)}</div>`
    + `<div class="xpbar" role="img" aria-label="${me.xp} experience"><i style="width:${Math.round(into * 100)}%"></i></div><p class="cap">${me.next_level_xp ? `${me.xp - me.level_xp} of ${me.next_level_xp - me.level_xp} to level ${me.level + 1}` : "Level 100"}. Today +${me.today_xp}.</p>`
    + (next ? `<p class="cap">${esc(next)}</p>` : "") + `<p class="cap">Your bubbles are ${esc(partnerOf(state.ME) ? partnerOf(state.ME).stone : stoneOfSpecies(me.species))}, as others see them.</p>${act}<div class="btns"><button class="btn" data-change>Change partner</button></div>`;
  host(card); }
async function evolveTo(into, hold) { try { applyPartners(await api("POST", "armada/partner/evolve", hold ? {hold: true} : {into})); } catch (e) { note(e.message); } }

/* ----- the moment: about 1.4 s in the card, only for me, only when I am idle. Outlines of the old and new forms trade places
   faster and faster, eight pixels burst, the new form lands in colour with one hop. Any key or a press outside ends it where it
   stands (the evolution happens); Stop, or Esc with the field empty, keeps the old form (held). It never takes focus. ----- */
let moment = null, momentTimer = 0;
function momentDue() { clearTimeout(momentTimer); const me = me_(); if (!pack || !me || !me.ready || me.held || moment) return; if (me.ready.choices.length > 1) { drawMeRow(); return; }
  const idle = document.visibilityState === "visible" && !state.pressing && performance.now() - state.lastKey >= 3000 && !(document.activeElement === tx && tx.textContent.trim()) && !pal && !menu && !pop && !dialogOpen && !info && !(state.place && state.place.kind === "partner");
  if (idle) playMoment(me.ready.choices[0]); else momentTimer = setTimeout(momentDue, 500); }
function playMoment(into) { const me = me_(), from = me.species, shiny = me.ready.kind === "shiny"; if (card) { card.remove(); card = null; }
  moment = {into, from, shiny, done: false, timers: []}; const c = card = el("div", "pcard raised float moment"); c.setAttribute("role", "status"); document.body.append(c);
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
  const nx = nextForms(into), L = (me_() && me_().evolve_levels) || [16, 30], lvl = L[stepsFrom(me_().chosen, into)];
  $("p.cap", c).textContent = `Your bubbles ${stoneOfSpecies(into) === stoneOfSpecies(from) ? "stay" : "are now"} ${stoneOfSpecies(into)}.${nx.length && lvl ? ` Next: ${nx.map((x) => pack.species[x].name).join(" or ")} at level ${lvl}.` : ""}`;
  const st = $("[data-stop]", c); if (st) st.remove(); evolveTo(into, false);
  setTimeout(() => endMoment(), 4000 * M.SLOW); }
/* a key or a press outside: the evolution happens and the card shows its end at once, then fades */
function cutMoment() { if (!moment) return; if (!moment.done) landMoment(); endMoment(true); }
function endMoment(now_) { if (!moment || !card) { moment = null; return; } if (!now_ && card.matches(":hover")) { setTimeout(() => endMoment(), 1000); return; } const c = card; card = null; moment = null; c.inert = true; M.leave(c); }
function holdMoment() { if (!moment || moment.done) return; moment.timers.forEach(clearTimeout); const c = card, from = moment.from; moment = null; c.classList.remove("moment");
  const o = $(".pk.old", c), n = $(".pk.new", c); if (o) { o.hidden = false; o.classList.remove("ghost"); } if (n) n.hidden = true; $(".stage", c).classList.remove("pulse"); $(".bits", c).innerHTML = "";
  $(".ih b", c).textContent = pack.species[from].name; $(".say", c).textContent = `${pack.species[from].name} stays as it is. Evolve it from this card when you like.`; evolveTo(null, true).then(() => drawCard()); }

export {card, closeCard, cutMoment, drawCard, drawMeRow, holdMoment, moment, momentDue, openCard, playMoment};
