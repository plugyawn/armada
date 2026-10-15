/* INTERIM BETA (README.md): partners: the sprite pack, the team's partners, faces redrawn. */

import {$$, el, esc, sleep, T} from "./util.js";
import {DATA, msgIndex, state} from "./state.js";
import {api} from "./connection.js";
import {faceOf, faceSize, isOnix, stoneOf} from "./faces.js";
import {drawNav} from "./nav.js";
import {drawSide} from "./column.js";
import {drawPicker, drawPickerBand} from "./partner_picker.js";
import {card, drawCard, drawMeRow, momentDue} from "./partner_card.js";

/* ---------- partners: the Pokémon each person chose (the r4 partner and sprite studies) ----------
   The pack (species.json and the sprites) is not in git: the server serves it from its git-ignored sprite directory to
   signed-in people (zerver/views/armada_beta.py, /beta/sprites/...), and partners come from GET armada/partners. With no
   pack, or a server without the agent, every face stays initials and nothing here draws.
   A face is a sprite at whole CSS pixels (one sprite pixel a CSS pixel; two in the picker's column and the card), in a box
   the size of the face it replaces, so a partner arriving or evolving moves nothing. The sprite stands on its feet at the
   box's foot, centred on what it draws (its content box, measured by tools/armada/sprites.py), in a window it may overflow
   upward in the gutter and is cut by in lists; it never catches the pointer. Still at rest; it hops (+1, -3 px, 133 ms a
   step) under the pointer or focus, while its person types, when their message lands, and on my send. Onix's face stays
   still while its run is live: the ball is the run's one loop (MARKS.md, direction A). */
const SPRITE_V = DATA.spriteVersion || "";
let pack = null, partners = null, partnersAt = 0;
const OFFERED = [];                                        /* species ids the picker offers: first forms the pack draws, Onix's line left out */
const spritePath = (path) => "/beta/sprites/" + path.split("/").map(encodeURIComponent).join("/");
const spriteSrc = (sid, shiny) => { const sp = pack && pack.species[sid] && pack.species[sid].sprite; return !sp ? "" : spritePath(shiny && sp.shiny ? sp.shiny : sp.regular); };
/* a sprite that does not load (a pack missing a file, a server that lost it) is never an empty box: every face drawn from it
   goes back to initials where it stands, and stays so on this page */
const brokenSprites = new Set();
let facesDue = false;
document.addEventListener("error", (e) => { const img = e.target; if (!(img instanceof HTMLImageElement) || !img.closest(".pk")) return; brokenSprites.add(img.getAttribute("src"));
  if (!facesDue) { facesDue = true; requestAnimationFrame(() => { facesDue = false; refreshFaces(); drawMeRow(); }); } }, true);
const agentLine = () => (pack && pack.species["95"] ? pack.species["95"].line : 95);
function partnerOf(id) { if (!pack || !partners) return null;
  if (isOnix(id)) { const a = partners.agent; return a && a.species && pack.species[a.species] ? {species: a.species, shiny: false, stone: "flint"} : null; }
  const p = partners.partners[id]; return p && pack.species[p.species] && pack.species[p.species].sprite ? p : null; }
const speciesName = (sid) => (pack && pack.species[sid] ? pack.species[sid].name : "");
/* the sprite in a window w by h (CSS px) at k CSS px a sprite pixel: feet on the window's foot, centred on the drawing */
function spriteHTML(sid, shiny, k, w, h, lazy) { const s = pack.species[sid], sp = s && s.sprite; if (!sp) return "";
  const useShiny = !!shiny && !!sp.shiny, box = useShiny ? sp.shiny_box : sp.box, [bx, by, bw, bh] = box;
  const left = Math.round(w / 2 - (bx + bw / 2) * k), top = h - (by + bh) * k;
  return `<img src="${esc(spriteSrc(sid, useShiny))}" alt="" draggable="false" decoding="async"${lazy ? ' loading="lazy"' : ""} style="left:${left}px;top:${top}px;width:${sp.size[0] * k}px;height:${sp.size[1] * k}px">`; }
/* the windows: [box, window width, window height] by face size. 28 is the gutter's (40 wide, cut at the sides only, so a
   face never reaches the lane); 20 and 18 are a list's and a caption's (cut on every side). */
const PK = {28: [28, 40, 32], 20: [20, 28, 28], 18: [18, 22, 20]};
function pkFace(p, size, cls, pres, uid) { const [b, w, h] = PK[size] || PK[28], img = spriteHTML(p.species, p.shiny, 1, w, h); if (!img) return "";
  return `<i class="pk s${size} ${(cls || "").replace(/\bs\d+\b/g, "")} ${pres || ""}" data-uid="${uid}" data-fs="${esc(p.species)}:${p.shiny ? 1 : 0}" aria-hidden="true"><span class="win" style="left:${(b - w) / 2}px;top:${b - h}px;width:${w}px;height:${h}px">${img}</span></i>`; }
/* a large drawing: the picker's cells (1x) and its column and the card (2x), the window the whole canvas */
function pkBig(sid, shiny, k, cls = "", lazy = false) { const sp = pack.species[sid] && pack.species[sid].sprite, w = 68 * k, h = 56 * k; if (!sp) return "";
  return `<i class="pk big ${cls}" style="width:${w}px;height:${h}px" aria-hidden="true"><span class="win" style="left:0;top:0;width:${w}px;height:${h}px">${spriteHTML(sid, shiny, k, w, h, lazy)}</span></i>`; }
/* a picker cell's drawing: two CSS px a sprite pixel (SPRITES.md section 5), the cell's width, as tall as the drawing; a drawing
   wider than the cell is cut at its sides, never scaled */
const CELL_W = 96;
function pkCell(sid) { const sp = pack.species[sid].sprite, h = sp.box[3] * 2;
  return `<i class="pk big" style="width:${CELL_W}px;height:${h}px" aria-hidden="true"><span class="win" style="left:0;top:0;width:${CELL_W}px;height:${h}px">${spriteHTML(sid, false, 2, CELL_W, h, true)}</span></i>`; }
/* one hop, two steps: for a message that lands and for my send. Reduced motion: none. */
function hopOnce(face) { const img = face && face.querySelector && face.querySelector(".pk img"); if (!img || M.reduced()) return;
  img.animate([{transform: "translateY(1px)", easing: "step-end"}, {transform: "translateY(-3px)", offset: 0.5, easing: "step-end"}, {transform: "translateY(0)"}], {duration: 266 * M.SLOW}); }

async function loadPartners() { if (!SPRITE_V) return;
  try { const [p, st] = await Promise.all([fetch(`/beta/sprites/species.json?v=${encodeURIComponent(SPRITE_V)}`, {credentials: "same-origin", redirect: "error"}).then((r) => (r.ok ? r.json() : null)), api("GET", "armada/partners")]);
    if (!p || !p.species || !st.on) return; pack = p; partners = st; partnersAt = performance.now();
    OFFERED.length = 0; for (const [sid, s] of Object.entries(pack.species)) if (s.first && s.sprite && s.line !== agentLine()) OFFERED.push(sid);
    OFFERED.sort((a, b) => pack.species[a].num - pack.species[b].num || a.localeCompare(b, "en", {numeric: true})); }
  catch (e) { pack = null; partners = null; } }
/* the team's partners change rarely (a choice, an evolution): they are asked for again every two minutes, on coming back to
   the page, a little after someone speaks, and after my own send and reaction (which count, so mine are folded then) */
let partnerTimer = 0;
async function fetchPartners(fold) { partnersAt = performance.now(); try { applyPartners(await api(fold ? "POST" : "GET", fold ? "armada/partner/fold" : "armada/partners")); } catch (e) {} }
function refreshPartners(o = {}) { if (!pack) return; clearTimeout(partnerTimer); partnerTimer = setTimeout(() => {
    if (!o.force && performance.now() - partnersAt < 30000) return; fetchPartners(o.fold); }, o.wait ?? 1500); }
/* the caption "Evolved into X" is laid out with its bubble, never added under a bubble already drawn. So a batch of events
   that brings a message from someone who can evolve their partner now (the server's may_change), or that comes while what
   the page knows of partners is over PARTNERS_STALE_MS old, waits for the team's partners first: one request, at most
   PARTNERS_FIRST_MS. Messages from Onix and my own never wait (my bubble was drawn when I pressed Return). Four seconds
   after someone else's message the page asks again, after the fold their own page makes two seconds after their send:
   a level that a message crossed is known before that person's next message comes. */
const PARTNERS_FIRST_MS = 400, PARTNERS_STALE_MS = 15000;
async function partnersFirst(events) { if (!pack || !partners) return;
  const stale = performance.now() - partnersAt > PARTNERS_STALE_MS;
  const due = events.some((ev) => { if (ev.type !== "message") return false; const id = ev.message.sender_id; if (id === state.ME || isOnix(id)) return false; const p = partners.partners[id]; return stale || !!(p && p.may_change); });
  if (due) await Promise.race([fetchPartners(false), sleep(PARTNERS_FIRST_MS)]); }
function applyPartners(st) { if (!st || !st.on || !pack) return; const before = JSON.stringify(partners && partners.partners) + JSON.stringify(partners && partners.agent); partners = st; partnersAt = performance.now();
  if (before !== JSON.stringify(st.partners) + JSON.stringify(st.agent)) refreshFaces();
  drawMeRow(); if (card) drawCard(); if (state.place && state.place.kind === "partner") { drawPicker(); drawSide(); drawPickerBand(); } momentDue(); }
const me_ = () => (partners && partners.me) || null;
/* every face on the page that is out of date is redrawn where it stands (same box), on the fade; bubbles take the new stone */
function refreshFaces() {
  for (const f of $$("[data-uid][data-fs]")) { if (!(+f.dataset.uid > 0)) continue; const uid = +f.dataset.uid, cls = f.getAttribute("class") || "", size = f.matches(".pk") ? +((/\bs(\d+)\b/.exec(cls) || [])[1] || 28) : f.matches("svg") ? (/\bs(\d+)\b/.exec(cls) ? +/\bs(\d+)\b/.exec(cls)[1] : 28) : faceSize(cls);
    const pres = /\bon\b/.test(cls) ? "on" : /\baway\b/.test(cls) ? "away" : "", keep = cls.split(/\s+/).filter((c) => /^s\d+$/.test(c)).join(" ");
    const t = el("template"); t.innerHTML = faceOf(uid, keep || (size === 28 ? "" : "s" + size), pres); const neu = t.content.firstElementChild; if (!neu || neu.dataset.fs === f.dataset.fs) continue;
    for (const c of cls.split(/\s+/)) if (c && !/^(av|pk|sigil|agent|in-disc|on|away|s\d+|p-\w+|big)$/.test(c) && !neu.classList.contains(c)) neu.classList.add(c);
    f.replaceWith(neu); if (!M.reduced()) M.appear(neu, 0, T["fade-in"]); }
  for (const cl of $$(".cl.them[data-sid]")) { const want = "s-" + stoneOf(+cl.dataset.sid); if (!cl.classList.contains(want)) { [...cl.classList].filter((c) => c.startsWith("s-")).forEach((c) => cl.classList.remove(c)); cl.classList.add(want); } }
  drawNav(); if (state.P && state.place && state.place.kind !== "home" && state.place.kind !== "partner") drawSide(); }

/* ----- the caption the team sees: my next bubble after an evolution carries one line, laid out with that bubble ----- */
function evoCaption(m) { if (typeof m.sender_id !== "number" || isOnix(m.sender_id)) return null; const p = partnerOf(m.sender_id); if (!p || !p.evolved_at) return null;
  const since = Math.floor(p.evolved_at); if (m.timestamp < since) return null;   /* a message's time is in whole seconds; the evolution's is not */
  const c = msgIndex.get(m.id); if (!c) return null; const first = c.msgs.find((x) => x.sender_id === m.sender_id && x.timestamp >= since && !x.thread_root_message_id); if (first !== m) return null;
  const e = el("div", "evo", `${faceOf(m.sender_id, "s18")}<span>${p.evolved_kind === "shiny" ? esc(`${speciesName(p.species)} turned shiny`) : esc(`Evolved into ${speciesName(p.species)}`)}</span>`); e.dataset.mid = m.id; return e; }

export {agentLine, applyPartners, brokenSprites, evoCaption, fetchPartners, hopOnce, loadPartners, me_, OFFERED,
  pack, partnerOf, partners, partnersFirst, pkBig, pkCell, pkFace, refreshPartners, speciesName, spriteSrc};
