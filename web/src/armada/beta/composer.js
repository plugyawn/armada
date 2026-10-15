/* INTERIM BETA (README.md): the field: the agent switch, the verb, the placeholder, attachments, notes, the caret handed back. */

import {$, $$, el, esc, mid, store} from "./util.js";
import {state} from "./state.js";
import {api} from "./connection.js";
import {topicLabel} from "./routing.js";
import {band, cz, go, goc, modeBtn, ph, toBtn, tx} from "./frame.js";
import {nameOf} from "./faces.js";
import {waitingRun} from "./runs.js";
import {dialogOpen} from "./hover.js";
import {modeLabel, pop} from "./model_picker.js";
import {pal} from "./palette.js";
import {pack} from "./partners.js";
import {reseed, seedField} from "./mentions.js";
import {fieldEmpty, seedOf} from "./mention_text.js";
import {drawRunState} from "./band.js";

let verb = "Send";
/* Onix answers what I send in a place only where I turned it on there: off until then, kept in this browser for each place,
   never on outside a repository. The switch puts @Onix in the field as a pill (mentions.js, seedField), so what is sent is
   what is seen; words that name @Onix address it whatever the switch says. */
const onixKey = (p) => "onix:" + p.key;
const onixHere = () => !!state.place && state.place.kind === "topic" && !!state.place.repo && store.get(onixKey(state.place)) === "1";
/* Home's Answer: the next message in that place answers the run that waits, once; the switch stays as it was. Esc, leaving the
   place, or the question ending gives it up. */
const answering = () => !!state.P && !!state.P.answering && !!waitingRun(state.P);
const onixNow = () => onixHere() || answering();
/* the field is ready after everything: the main field, or the thread field I am writing in, or the palette's */
const ready = () => { if ($("#lab.open:focus-within") || $(".pcard:focus-within") || pal || dialogOpen || (pop && pop.contains(document.activeElement))) return; const f = state.threadTx && state.threadTx.isConnected && state.tab === "Chat" ? state.threadTx : state.place && state.place.kind !== "home" ? tx : null; if (!f) return; if (document.activeElement !== f) f.focus({preventScroll: true}); if (document.activeElement === f) { const s = getSelection(), seedOnly = !!seedOf(f) && fieldEmpty(f); if (seedOnly || (f.textContent && !f.contains(s.anchorNode))) { s.selectAllChildren(f); s.collapseToEnd(); } } };   /* a field that holds only the switch's pill is written after it, never before */
const placeName = () => (state.place ? (state.place.kind === "home" ? "Home" : state.place.kind === "dm" ? (state.place.ids.length === 1 ? nameOf(state.place.ids[0]) : state.place.ids.length ? state.place.ids.map((i) => nameOf(i).split(/[ ,]/)[0]).join(", ") : "yourself") : topicLabel(state.place.topic)) : "");
/* the placeholder says what Return will do; on a view that is not Chat, with Onix on, it names what is in view. With the
   switch on it stands after the @Onix pill, which already says who is asked. */
const phText = () => (state.place && state.place.kind === "partner" ? `Search ${pack ? (pack.count || 1025).toLocaleString("en") : "1,025"} by name or number` : verb === "Steer" ? "Steer the run" : verb === "Answer" ? "Your answer" : verb === "Save" ? "Edit your message" : !onixHere() ? "Message " + placeName() : state.tab !== "Chat" ? `About ${state.tab === "README" ? "the README" : state.tab === "Files" ? (state.P && state.P.fileName ? state.P.fileName : "these files") : "these changes"}` : `Ask in ${placeName()}`);
function setVerb(v, cut) { const cur = $(":scope > span:not(.gone)", ph), same = v === verb;
  if (!same) { const up = ["Send", "Steer", "Answer", "Save", "Choose"].indexOf(v) > ["Send", "Steer", "Answer", "Save", "Choose"].indexOf(verb) ? 1 : -1; verb = v; if (cut) { $$(":scope > :not(.sz)", goc).forEach((x) => x.remove()); goc.insertAdjacentHTML("afterbegin", `<span>${v}</span>`); } else M.swap(goc, `<span>${v}</span>`, {dir: up, seq: true}); go.setAttribute("aria-label", v); go.dataset.tip = v; }
  const text = phText();   /* read after the verb has changed: read before, the field kept saying "Steer Onix" after a run ended */
  if (!cur || cur.textContent !== text) { if (cut || same) ph.innerHTML = `<span>${esc(text)}</span>`; else M.swap(ph, `<span>${esc(text)}</span>`, {quiet: true, seq: true}); } }
function drawComposer() { const wt = state.place.kind === "topic" && state.place.repo, on = onixHere(), ctx = state.P.ctx; toBtn.hidden = !wt; modeBtn.hidden = !wt; modeBtn.classList.remove("off");
  toBtn.classList.toggle("off", !on); toBtn.setAttribute("aria-pressed", on); toBtn.dataset.tip = on ? "Onix answers what you send here. Press to talk to people only" : "What you send here goes to people. Press to have Onix answer";
  if (wt && ctx) { const label = modeLabel(ctx); const sl = $(".slot", modeBtn); if (sl && sl._v !== label) { sl._v = label; sl.innerHTML = `<span>${esc(label)}</span><i class="sz">${esc(label)}</i>`; } }
  modeBtn.dataset.tip = "Model, speed and effort for this worktree"; modeBtn.dataset.key = "⌘/"; seedField(); setVerb(verb, true); placeJump(); }
/* Jump to latest stands left of the model label, whose width follows the model's name */
function placeJump() { if (!band || !band.el || !band.jump || !modeBtn) return; const box = band.jump.offsetParent || band.el, r = modeBtn.hidden ? null : modeBtn.getBoundingClientRect(), b = box.getBoundingClientRect(); band.el.style.setProperty("--jump-right", r && r.width ? `${Math.ceil(b.right - r.left) + 8}px` : ""); }
/* the switch's pill draws its name from CSS, so a field that holds only it has no text and shows its placeholder */
/* the switch addresses Onix while its pill (or another @Onix pill) is in the field; an empty field takes the pill back. A person
   who takes it out of their words talks to people, so the verb says Send, not Steer or Answer (band.js). */
const switchHolds = () => !!seedOf(tx) || !!tx.querySelector("[data-agent]") || fieldEmpty(tx);
let held = true;
const syncField = () => { reseed(); cz.classList.toggle("has-text", !!tx.textContent.trim() || state.atts.length > 0); cz.classList.toggle("seeded", !!seedOf(tx));
  const h = switchHolds(); if (h !== held) { held = h; if (onixNow() && state.P) drawRunState(); } };

/* ---------- attach: the field's Plus, a paste or a drop; the file goes to the server's upload endpoint and stands as a chip
   before the words until the message is sent ---------- */
function upload(file) { const a = {id: ++state.localSeq, name: file.name || "pasted file", url: null, failed: false}; state.atts.push(a); drawAtts();
  const fd = new FormData(); fd.append("file", file, a.name);
  api("POST", "user_uploads", {}, {body: fd}).then((r) => { a.url = r.url || r.uri; a.name = r.filename || a.name; }, (e) => { a.failed = true; note(e.message); }).finally(() => { drawAtts(); ready(); }); }
function drawAtts() { let box = $(".atts", cz); if (!state.atts.length) { if (box) box.remove(); syncField(); return; } if (!box) { box = el("span", "atts"); $(".txw", cz).prepend(box); }
  box.innerHTML = state.atts.map((a) => `<span class="att${a.url ? "" : a.failed ? " bad" : " up"}">${ic(/\.(png|jpe?g|gif|webp|svg)$/i.test(a.name) ? "image" : "file")}<span class="an">${esc(mid(a.name, 28))}${a.url ? "" : a.failed ? " (failed)" : "…"}</span><button class="x" data-unatt="${a.id}" aria-label="Remove ${esc(a.name)}" data-tip="Remove">${ic("x")}</button></span>`).join(""); host(box); syncField(); }
function note(text) { M.say(go, text, 2400); }

export {answering, drawAtts, drawComposer, note, onixHere, onixKey, onixNow, ready, setVerb, switchHolds, syncField, upload, verb};
