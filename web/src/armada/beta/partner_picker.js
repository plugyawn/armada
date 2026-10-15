/* INTERIM BETA (README.md): the partner picker, a place (#partner). */

import {$, $$, esc} from "./util.js";
import {armadaBots, state} from "./state.js";
import {api} from "./connection.js";
import {openPlace} from "./routing.js";
import {band, go, panes, sh, tx} from "./frame.js";
import {faceOf, nameOf} from "./faces.js";
import {note, syncField} from "./composer.js";
import {drawSide} from "./column.js";
import {HOME} from "./home.js";
import {agentLine, applyPartners, fetchPartners, me_, OFFERED, pack, partners, pkBig, pkCell, pkFace, speciesName} from "./partners.js";
import {closeCard} from "./partner_card.js";

/* ----- the picker: a place (#partner), not a dialog. Starters first, then each generation; the field is its search ----- */
const PARTNER = {key: "partner", kind: "partner", repo: false};
const pick = {seg: "starters", q: "", sel: null, picks: {}, back: null, refused: new Set()};
const GEN_NAMES = ["", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
/* the lines held, by line: the holders the server lists, and a line the server refused me that it does not list (held by
   someone I cannot see), as Someone's */
const takenLines = () => { const out = new Map(); if (!partners) return out; for (const [uid, p] of Object.entries(partners.partners)) { const s = pack.species[p.species]; if (s) out.set(s.line, +uid); } for (const l of pick.refused) if (!out.has(l)) out.set(l, -1); return out; };
/* the first form of a species' line: what the picker offers for it */
const firstOf = (sid) => { let cur = sid; for (let i = 0; i < 6 && pack.species[cur] && pack.species[cur].prevo && pack.species[pack.species[cur].prevo]; i++) cur = pack.species[cur].prevo; return cur; };
/* the search: a name (prefix, then word start, then substring) or a number (that Pokédex number and no other), across every
   form in the pack. A later form finds its line's first form, with a hint saying which to choose. */
function matches(q) { q = q.trim().toLowerCase(); if (!q) return null; const n = /^#?0*(\d+)$/.exec(q), found = [], hints = [], all = Object.keys(pack.species);
  const add = (sid) => { const first = firstOf(sid), name = pack.species[sid].name, own = rank(first) >= 0;
    if (!OFFERED.includes(first)) { const h = `The agent has ${pack.species[first] ? pack.species[first].name : name}'s line.`; if (pack.species[first] && pack.species[first].line === agentLine() && hints.length < 2 && !hints.includes(h)) hints.push(h); return; }
    if (!found.includes(first)) found.push(first); if (first !== sid && !own && hints.length < 2 && !hints.some((h) => h.endsWith(`choose ${pack.species[first].name}`) || h.endsWith(`choose ${pack.species[first].name}.`))) hints.push(endSentence(`${name} grows from ${pack.species[first].name}: choose ${pack.species[first].name}`)); };
  const rank = (sid) => { if (n) return String(pack.species[sid].num) === n[1] ? 0 : -1; const l = pack.species[sid].name.toLowerCase(); return l.startsWith(q) ? 0 : new RegExp("(^|[\\s(-])" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(l) ? 1 : l.includes(q) ? 2 : -1; };
  const key = (sid) => [rank(sid), pack.species[sid].form ? 1 : 0, pack.species[sid].num];
  all.map((sid) => [sid, key(sid)]).filter((x) => x[1][0] >= 0).sort((a, b) => a[1][0] - b[1][0] || a[1][1] - b[1][1] || a[1][2] - b[1][2]).forEach((x) => add(x[0]));
  return {found, hints}; }
function pickerSections() { const m = matches(pick.q); if (m) return [["Found", m.found, m.hints]];
  const starters = (pack.starters || []).filter((sid) => OFFERED.includes(sid)), out = [];
  if (pick.seg === "starters" || pick.seg === "all") out.push(["Starters", starters]);
  for (let g = 1; g <= 9; g++) if (pick.seg === "all" || pick.seg === String(g)) out.push([`Generation ${g}`, OFFERED.filter((sid) => pack.species[sid].gen === g)]);
  return out; }
const visibleCells = () => pickerSections().flatMap((x) => x[1]);
function cellHTML(sid, taken) { const holder = taken.get(pack.species[sid].line), mine = holder === state.ME, other = holder != null && !mine;
  return `<button class="pkc${other ? " taken" : ""}${sid === pick.sel ? " sel" : ""}" data-sid="${esc(sid)}" role="option" aria-selected="${sid === pick.sel}" tabindex="-1">${pkCell(sid)}<span class="nm">${esc(pack.species[sid].name)}</span><span class="cap">${other ? esc(nameOf(holder).split(/[ ,]/)[0]) : mine ? "Yours" : ""}</span></button>`; }
function drawPicker() { const box = $("#picker"); if (!box || !pack) return; const taken = takenLines(), secs = pickerSections(), y = panes.Partner.scrollTop;
  if (!pick.sel || !visibleCells().includes(pick.sel)) pick.sel = visibleCells().find((sid) => !taken.has(pack.species[sid].line)) || visibleCells()[0] || null;
  let h = `<div class="pkp"><p class="pkintro">Your partner is your face here. It levels up as you use Armada, and evolves.</p><div class="pkdetail">${detailHTML()}</div>`;
  for (const [title, list, hints] of secs) h += `<h2 class="pkh">${esc(title)}</h2>` + (hints && hints.length ? `<p class="note pkhint">${esc(hints.join(" "))}</p>` : "") + (list.length ? `<div class="pkg" role="listbox" aria-label="${esc(title)}">${list.map((sid) => cellHTML(sid, taken)).join("")}</div>` : '<p class="note">Nothing by that name or number.</p>');
  box.innerHTML = h + "</div>"; host(box); panes.Partner.scrollTop = y; drawPickerSide(); syncPickVerb(); drawPickerBand(); }
function selectCell(sid, o = {}) { if (!sid) return; pick.sel = sid; for (const b of $$(".pkc", panes.Partner)) { const on = b.dataset.sid === sid; b.classList.toggle("sel", on); b.setAttribute("aria-selected", String(on)); }
  const b = $$(".pkc", panes.Partner).find((x) => x.dataset.sid === sid); if (b && o.scroll) b.scrollIntoView({block: "nearest"}); drawPickerSide(); syncPickVerb(); }
/* arrows move through the grid as it is drawn: Left and Right in reading order, Up and Down to the nearest cell above or below */
function movePick(key) { const cells = $$(".pkc", panes.Partner); if (!cells.length) return; const i = Math.max(0, cells.findIndex((b) => b.dataset.sid === pick.sel)), cur = cells[i];
  let n = null; if (key === "ArrowLeft") n = cells[i - 1]; else if (key === "ArrowRight") n = cells[i + 1];
  else { const r = cur.getBoundingClientRect(), down = key === "ArrowDown"; let best = null, bd = 1e9;
    for (const b of cells) { const q = b.getBoundingClientRect(); if (down ? q.top <= r.top + 4 : q.top >= r.top - 4) continue; const dy = Math.abs(q.top - r.top), d = dy * 1000 + Math.abs(q.left - r.left); if (d < bd) { bd = d; best = b; } } n = best; }
  if (n) selectCell(n.dataset.sid, {scroll: true}); }
/* the line from the chosen form, with the level each form arrives at, and the branches my level passes on the way */
function lineOf(sid) { const out = [], L = (me_() && me_().evolve_levels) || [16, 30]; let cur = [sid];
  while (cur.length && out.length < 4) { out.push({forms: cur, level: out.length ? L[out.length - 1] || null : null}); cur = cur.length === 1 ? nextForms(cur[0]) : []; }
  return out; }
function nextForms(sid) { const out = []; for (const e of (pack.species[sid] || {evos: []}).evos) { if (!pack.species[e]) continue; if (pack.species[e].sprite) out.push(e); else out.push(...nextForms(e).filter((x) => !out.includes(x))); } return out; }
/* what my level makes of a choice, as the server will: as far along the line as it allows, with my picks at branches */
function arrivalOf(sid) { const me = me_(), L = (me && me.evolve_levels) || [16, 30], level = me ? me.level : 1, stages = L.filter((x) => level >= x).length; let cur = sid, branch = null;
  for (let i = 0; i < stages; i++) { const n = nextForms(cur); if (!n.length) break; if (n.length === 1) { cur = n[0]; continue; } const p = pick.picks[cur]; if (p && n.includes(p)) { cur = p; continue; } branch = {at: cur, choices: n}; break; }
  return {sid: cur, branch}; }
/* how many evolutions lie between the chosen form and this one (the server's steps_from) */
const stepsFrom = (first, sid) => { let n = 0, cur = sid; while (cur && cur !== first && pack.species[cur]) { cur = pack.species[cur].prevo; n++; } return cur === first ? n : 0; };
const typeWords = (sid) => pack.species[sid].types.join(" and ");
const STONE_OF_TYPE = {Grass: "moss", Bug: "moss", Water: "larimar", Ice: "larimar", Flying: "lapis", Dragon: "lapis", Psychic: "amethyst", Poison: "amethyst", Ghost: "amethyst", Fire: "rose", Fighting: "rose", Fairy: "rose", Normal: "flint", Rock: "flint", Steel: "flint", Dark: "flint", Electric: "citrine", Ground: "citrine"};
const stoneOfSpecies = (sid) => { const t = pack.species[sid].types; return STONE_OF_TYPE[t[0] === "Normal" && t[1] ? t[1] : t[0]] || "flint"; };
function detailHTML() { const sid = pick.sel; if (!sid || !pack.species[sid]) return ""; const s = pack.species[sid], taken = takenLines(), holder = taken.get(s.line), arr = arrivalOf(sid);
  const line = lineOf(sid).map((st) => `<li>${st.forms.length === 1 ? pkFace({species: st.forms[0], shiny: false}, 20, "", "", 0) : ic("branch")}<span class="t">${st.forms.map((f) => esc(pack.species[f].name)).join(" or ")}</span><span class="e">${st.level ? `level ${st.level}` : "now"}</span></li>`).join("");
  const branch = arr.branch ? `<p class="pkbr">Your level passes ${esc(pack.species[arr.branch.at].name)}'s evolution. Choose what it becomes:</p><div class="seg pkpicks">${arr.branch.choices.map((c) => `<button data-pickfor="${esc(arr.branch.at)}" data-pick="${esc(c)}">${esc(pack.species[c].name)}</button>`).join("")}</div>` : "";
  const sample = `<div class="pksample cl them s-${stoneOfSpecies(arr.sid)}">${pkFace({species: arr.sid, shiny: false}, 28, "", "", 0)}<div class="bl"><p class="b">Morning, team.</p></div></div>`;
  return `<div class="pkbig2">${pkBig(sid, false, 2)}</div><p class="pkno"><span class="fig">#${String(s.num).padStart(3, "0")}</span> ${esc(s.name)}</p><p class="pkty">${esc(typeWords(sid))}. Your bubbles would be ${stoneOfSpecies(arr.sid)}.</p>`
    + (holder != null && holder !== state.ME ? `<p class="pkty bad">${esc(nameOf(holder))} has this line.</p>` : "")
    + `<div class="sh"><span>Line</span></div><ul class="ls pkline">${line}</ul>${branch}<div class="sh"><span>How it looks</span></div>${sample}`; }
function drawPickerSide() { const d = $(".pkdetail", panes.Partner); if (d) { d.innerHTML = detailHTML(); host(d); } if (state.place && state.place.kind === "partner") drawSide(); }
function pickerSideHTML() { const taken = [...takenLines()].filter(([, uid]) => partners.partners[uid]).sort((a, b) => nameOf(a[1]).localeCompare(nameOf(b[1])));
  const rows = taken.map(([, uid]) => `<li>${faceOf(uid, "s20")}<span class="t">${esc(nameOf(uid))}</span><span class="e"><span class="d">${esc(speciesName(partners.partners[uid].species))}</span></span></li>`).join("");
  const onix = [...armadaBots][0], a = partners.agent && partners.agent.species;
  return `<section class="sec pkcol">${detailHTML()}</section><section class="sec foot"><div class="sh"><span>Taken</span></div><ul class="ls">${rows}${onix && a ? `<li>${faceOf(onix, "s20")}<span class="t">Onix</span><span class="e"><span class="d">${esc(speciesName(a))}</span></span></li>` : ""}</ul></section>`; }
const pickFree = () => { const s = pick.sel && pack.species[pick.sel]; if (!s) return false; const h = takenLines().get(s.line); return h == null || h === state.ME; };
/* a sentence ends in one full stop, also after a name that ends in one ("Mime Jr.") */
const endSentence = (t) => (t.endsWith(".") ? t : `${t}.`);
function syncPickVerb() { if (state.place && state.place.kind === "partner") go.classList.toggle("armed", pickFree()); }
function drawPickerBand() { const b = band && band.pk; if (!b) return; const on = !!state.place && state.place.kind === "partner" && !!pack; b.hidden = !on; if (!on) return; const me = me_(), sid = pick.sel;
  if (!sid || !me) { b.textContent = ""; return; } const holder = takenLines().get(pack.species[sid].line);
  if (holder != null && holder !== state.ME) { b.textContent = `${nameOf(holder)} has ${pack.species[sid].name}'s line. Choose another.`; return; }
  const arr = arrivalOf(sid), word = me.xp ? `You are level ${me.level} from your history.` : "You start at level 1.";
  b.textContent = endSentence(`${word} ${pack.species[sid].name} arrives as ${pack.species[arr.sid].name}${arr.branch ? `, and you choose its evolution` : ""}`); }
/* the picker's header: line 2 is a switch of text controls (Starters, All, the nine generations), and Later at the right */
function pickerHead(h) { const say = $(".l2 .say", h), end = $(".l2 .end", h); if (!say || !end) return; const has = !!(me_() && me_().species);
  const segs = [["starters", "Starters"], ["all", "All"], ...GEN_NAMES.slice(1).map((g) => [g, g])];
  say.outerHTML = `<div class="seg pkseg" role="tablist" aria-label="Show">${segs.map(([k, l]) => `<button role="tab" data-seg="${k}" aria-selected="${pick.seg === k && !pick.q}"${pick.seg === k && !pick.q ? ' class="on"' : ""}${/^\d$/.test(k) ? ` aria-label="Generation ${k}"` : ""}>${l}</button>`).join("")}</div>`;
  end.innerHTML = `<button class="lk later" data-later>${has ? "Keep " + esc(speciesName(me_().species)) : "Later"}</button>`; }
async function choosePick() { if (!pickFree()) { M.refuse(sh); S.play("nope"); return; } const sid = pick.sel, into = Object.values(pick.picks);
  try { applyPartners(await api("POST", "armada/partner", {species: sid, into})); S.play("tick"); leavePicker(); }
  catch (e) { note(e.message); if (e.status !== 400) return;
    /* refused (someone took the line first): the picker shows who has it now; a line the server still does not list as held
       (its holder is someone I cannot see) is drawn as taken by Someone, so Choose is not armed on it again */
    const line = pack.species[sid].line; await fetchPartners(false); if (!takenLines().has(line)) { pick.refused.add(line); if (state.place && state.place.kind === "partner") { drawPicker(); drawSide(); } } } }
async function putOff() { const me = me_(); if (me && !me.species && !me.later) { try { applyPartners(await api("POST", "armada/partner", {later: true})); } catch (e) { note(e.message); return; } } leavePicker(); }
function leavePicker() { const back = pick.back && pick.back.kind !== "partner" ? pick.back : null; pick.q = ""; tx.textContent = ""; syncField(); openPlace(back || HOME); }
/* changing later: the same place with my partner selected, under the switch that shows it */
function selectMine() { const me = me_(); if (!me || !me.chosen || !pack.species[me.chosen]) return; pick.sel = me.chosen; pick.q = ""; if (!(pack.starters || []).includes(me.chosen)) pick.seg = String(pack.species[me.chosen].gen); }
function openPicker() { if (!pack) return; closeCard(); if (state.place && state.place.kind !== "partner") pick.back = state.place; selectMine(); openPlace(PARTNER); }

export {choosePick, drawPicker, drawPickerBand, drawPickerSide, movePick, nextForms, openPicker, PARTNER, pick,
  pickerHead, pickerSideHTML, putOff, selectCell, selectMine, stepsFrom, stoneOfSpecies};
