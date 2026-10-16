/* INTERIM BETA (README.md): partners: the sprite pack, the team's partners, faces redrawn. */

import {$$, el, esc, T} from "./util.js";
import {armadaBots, DATA, msgIndex, places, state} from "./state.js";
import {api} from "./connection.js";
import {faceOf, faceSize, isOnix, stoneOf} from "./faces.js";
import {drawSide} from "./column.js";
import {drawPicker, drawPickerBand} from "./partner_picker.js";
import {card, drawCard, drawMeRow, momentDue} from "./partner_card.js";

/* ---------- partners: the Pokémon each person chose (the r4 partner and sprite studies) ----------
   The pack (species.json and the sprites) is not in git: the server serves it from its git-ignored sprite directory to
   signed-in people (zerver/views/armada_beta.py, /beta/sprites/...), and partners come from GET armada/partners. With no
   pack, or a server without the agent, every face stays initials and nothing here draws.
   A face (the r5 round-four study, sv.html) is a circle filled with the gradient of the partner's type, in the box of the
   face it replaces, so a partner arriving or evolving moves nothing; on it stands the partner's Scarlet/Violet menu icon,
   whole (never cut), its feet inside the circle and its head over the rim. The icons are smooth renders on canvases of
   their own sizes, so each is placed by its content box (measured by tools/armada/sprites.py): scaled to the circle plus a
   peep, centred on what it draws. It never catches the pointer. Still at rest; it hops under the pointer or focus, while
   its person types, when their message lands, and on my send. A species with no icon has no face: initials stand in. */
const SPRITE_V = DATA.spriteVersion || "";
let pack = null, partners = null, partnersAt = 0;
const OFFERED = [];                                        /* species ids the picker offers: first forms with an icon, Onix's line left out */
const spritePath = (path) => "/beta/sprites/" + path.split("/").map(encodeURIComponent).join("/");
const spriteSrc = (sid, shiny) => { const sp = pack && pack.species[sid] && pack.species[sid].sprite; return !sp ? "" : spritePath(shiny && sp.shiny ? sp.shiny : sp.regular); };
const svOf = (sid) => (pack && pack.species[sid] && pack.species[sid].sv) || null;
const svSrc = (sid) => { const sv = svOf(sid); return sv ? spritePath(sv.path) : ""; };
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
/* the type whose colour a face takes: the first, except Normal, which yields to a second (as the server picks the stone) */
const typeOf = (sid) => { const t = (pack && pack.species[sid] && pack.species[sid].types) || []; return (t[0] === "Normal" && t[1] ? t[1] : t[0] || "Normal").toLowerCase(); };
/* where the icon stands on a circle of `size` px: as tall as the circle plus a peep (2 px on a small circle, a fifth on a
   larger one), never wider than the circle and a little more, centred on its drawing, its feet 1 or 2 px inside the rim */
function svPlace(sv, size) { const [bx, by, bw, bh] = sv.box, [W, H] = sv.size, small = size < 24;
  const k = Math.min((small ? size + 2 : Math.round(size * 1.21)) / bh, (small ? size + 10 : Math.round(size * 1.36)) / bw), inset = small ? 1 : Math.max(2, Math.round(size / 14));
  return {left: Math.round(size / 2 - (bx + bw / 2) * k), top: Math.round(size - inset - (by + bh) * k), width: Math.round(W * k), height: Math.round(H * k)}; }
/* a face of `size` px for species `sid`: the circle and the icon standing on it. uid names its person (0 for a drawing of a
   species alone, which no press opens); "" when the species has no icon. */
function svFace(sid, size, cls, pres, uid, lazy) { const sv = svOf(sid); if (!sv) return ""; const at = svPlace(sv, size), std = size === 28 || size === 20 || size === 18;
  const box = std ? "" : ` style="width:${size}px;height:${size}px"`, who = uid ? ` data-uid="${uid}" data-fs="sv:${esc(sid)}"` : "";
  return `<i class="pk s${size} ty-${esc(typeOf(sid))} ${(cls || "").replace(/\bs\d+\b/g, "")} ${pres || ""}"${box}${who} aria-hidden="true"><span class="rim"></span>`
    + `<img src="${esc(spritePath(sv.path))}" alt="" draggable="false" decoding="async"${lazy ? ' loading="lazy"' : ""} style="left:${at.left}px;top:${at.top}px;width:${at.width}px;height:${at.height}px"></i>`; }
/* a partner's face in a list or the gutter (the picker's sample and its line use it with no person) */
const pkFace = (p, size, cls, pres, uid) => svFace(p.species, size, cls, pres, uid);
/* a large drawing with no circle, for the evolution moment's two forms trading places: the icon standing on the window's
   foot, as tall as the window allows; the pixel icon of the old pack where a form has none */
function pkBig(sid, shiny, k, cls = "", lazy = false) { const w = 68 * k, h = 56 * k, sv = svOf(sid);
  if (sv) { const [bx, by, bw, bh] = sv.box, [W, H] = sv.size, q = Math.min((h - 4) / bh, (w - 8) / bw);
    return `<i class="pk big ${cls}" style="width:${w}px;height:${h}px" aria-hidden="true"><img src="${esc(spritePath(sv.path))}" alt="" draggable="false" decoding="async"${lazy ? ' loading="lazy"' : ""} style="left:${Math.round(w / 2 - (bx + bw / 2) * q)}px;top:${Math.round(h - (by + bh) * q)}px;width:${Math.round(W * q)}px;height:${Math.round(H * q)}px"></i>`; }
  const sp = pack.species[sid] && pack.species[sid].sprite; if (!sp) return "";
  const useShiny = !!shiny && !!sp.shiny, [bx, by, bw, bh] = useShiny ? sp.shiny_box : sp.box;
  return `<i class="pk big px ${cls}" style="width:${w}px;height:${h}px" aria-hidden="true"><img src="${esc(spriteSrc(sid, useShiny))}" alt="" draggable="false" decoding="async" style="left:${Math.round(w / 2 - (bx + bw / 2) * k)}px;top:${h - (by + bh) * k}px;width:${sp.size[0] * k}px;height:${sp.size[1] * k}px"></i>`; }
/* a picker cell's drawing: the face it would be, on a 48 px circle, in a window that leaves its head room */
const CELL_FACE = 48;
const pkCell = (sid) => `<span class="pkcf">${svFace(sid, CELL_FACE, "", "", 0, true)}</span>`;
/* the forms an evolution can reach that have an icon, so a partner never grows out of its face; all of them when none has */
const withIcons = (forms) => { const drawn = forms.filter((sid) => svOf(sid)); return drawn.length ? drawn : forms; };
/* one hop, two steps: for a message that lands and for my send. Reduced motion: none. */
function hopOnce(face) { const img = face && face.querySelector && face.querySelector(".pk img"); if (!img || M.reduced()) return;
  img.animate([{transform: "translateY(0)", easing: "ease-out"}, {transform: "translateY(-3px)", offset: 0.4, easing: "ease-in"}, {transform: "translateY(0)"}], {duration: 320 * M.SLOW}); }

async function loadPartners() { if (!SPRITE_V) return;
  try { const [p, st] = await Promise.all([fetch(`/beta/sprites/species.json?v=${encodeURIComponent(SPRITE_V)}`, {credentials: "same-origin", redirect: "error"}).then((r) => (r.ok ? r.json() : null)), api("GET", "armada/partners")]);
    if (!p || !p.species || !st.on) return; pack = p; partners = st; partnersAt = performance.now();
    OFFERED.length = 0; for (const [sid, s] of Object.entries(pack.species)) if (s.first && s.sprite && s.sv && s.line !== agentLine()) OFFERED.push(sid);
    OFFERED.sort((a, b) => pack.species[a].num - pack.species[b].num || a.localeCompare(b, "en", {numeric: true})); }
  catch (e) { pack = null; partners = null; } }
/* the team's partners come from the server as they change (the armada_partner event, contract C3); mine are folded after my
   own send and reaction, which count */
let partnerTimer = 0;
async function fetchPartners(fold) { partnersAt = performance.now(); try { applyPartners(await api(fold ? "POST" : "GET", fold ? "armada/partner/fold" : "armada/partners")); } catch (e) {} }
function refreshPartners(o = {}) { if (!pack) return; clearTimeout(partnerTimer); partnerTimer = setTimeout(() => {
    if (!o.force && performance.now() - partnersAt < 30000) return; fetchPartners(o.fold); }, o.wait ?? 1500); }
/* someone's partner changed. The server publishes it ahead of the message that caused an evolution, so the caption
   "Evolved into X" is laid out with that message's bubble, never added under one already drawn. Mine carries what only I
   see (experience, an evolution ready), so it is asked for in full. */
function onPartner(ev) { if (!pack || !partners || ev.op !== "update") return; const {user_id: id, ...row} = ev.partner;
  if (id === state.ME) { fetchPartners(false); return; }
  applyPartners({...partners, partners: {...partners.partners, [id]: row}}); }
function applyPartners(st) { if (!st || !st.on || !pack) return; const was = partners; partners = st; partnersAt = performance.now();
  if (!was) refreshFaces(); else { const who = new Set(), them = {...was.partners, ...st.partners};   /* the people whose partner changed: theirs are the only faces drawn again */
    for (const id of Object.keys(them)) if (JSON.stringify(was.partners[id]) !== JSON.stringify(st.partners[id])) who.add(+id);
    if (JSON.stringify(was.agent) !== JSON.stringify(st.agent)) for (const id of armadaBots) who.add(id);
    if (who.size) refreshFaces(who); }
  drawMeRow(); if (card) drawCard(); if (state.place && state.place.kind === "partner") { drawPicker(); drawSide(); drawPickerBand(); } momentDue(); }
const me_ = () => (partners && partners.me) || null;
const stoneClass = (f) => [...f.classList].find((c) => c.startsWith("p-")) || "";
/* the faces of these people (every face when none are named) are redrawn where they stand (same box), on the fade, on the
   page and in the rows of places left; their bubbles take the new stone. Nothing else is drawn again. */
function refreshFaces(who) { const ids = who ? [...who] : null, roots = [document, ...[...places.values()].map((c) => c.park).filter(Boolean)];
  const faces = ids ? ids.map((i) => `[data-uid="${i}"][data-fs]`).join(", ") : "[data-uid][data-fs]", turns = ids ? ids.map((i) => `.cl.them[data-sid="${i}"]`).join(", ") : ".cl.them[data-sid]";
  for (const root of roots) for (const f of root.querySelectorAll(faces)) { if (!(+f.dataset.uid > 0)) continue; const uid = +f.dataset.uid, cls = f.getAttribute("class") || "", size = f.matches(".pk") ? +((/\bs(\d+)\b/.exec(cls) || [])[1] || 28) : faceSize(cls);
    const pres = /\bon\b/.test(cls) ? "on" : /\baway\b/.test(cls) ? "away" : "", keep = cls.split(/\s+/).filter((c) => /^s\d+$/.test(c)).join(" ");
    const t = el("template"); t.innerHTML = faceOf(uid, keep || (size === 28 ? "" : "s" + size), pres); const neu = t.content.firstElementChild; if (!neu || (neu.dataset.fs === f.dataset.fs && stoneClass(neu) === stoneClass(f))) continue;   /* initials keep their data-fs; their stone is a class */
    for (const c of cls.split(/\s+/)) if (c && !/^(av|pk|on|away|s\d+|p-\w+|ty-\w+|big)$/.test(c) && !neu.classList.contains(c)) neu.classList.add(c);
    f.replaceWith(neu); if (!M.reduced() && neu.isConnected) M.appear(neu, 0, T["fade-in"]); }
  for (const root of roots) for (const cl of root.querySelectorAll(turns)) { const want = "s-" + stoneOf(+cl.dataset.sid); if (!cl.classList.contains(want)) { [...cl.classList].filter((c) => c.startsWith("s-")).forEach((c) => cl.classList.remove(c)); cl.classList.add(want); } }
  if (state.P && state.place && state.place.kind !== "home" && state.place.kind !== "partner") drawSide(); }

/* ----- the caption the team sees: my next bubble after an evolution carries one line, laid out with that bubble ----- */
function evoCaption(m) { if (typeof m.sender_id !== "number" || isOnix(m.sender_id)) return null; const p = partnerOf(m.sender_id); if (!p || !p.evolved_at) return null;
  const since = Math.floor(p.evolved_at); if (m.timestamp < since) return null;   /* a message's time is in whole seconds; the evolution's is not */
  const c = msgIndex.get(m.id); if (!c) return null; const first = c.msgs.find((x) => x.sender_id === m.sender_id && x.timestamp >= since && !x.thread_root_message_id); if (first !== m) return null;
  const e = el("div", "evo", `${faceOf(m.sender_id, "s18")}<span>${p.evolved_kind === "shiny" ? esc(`${speciesName(p.species)} turned shiny`) : esc(`Evolved into ${speciesName(p.species)}`)}</span>`); e.dataset.mid = m.id; return e; }

export {agentLine, applyPartners, brokenSprites, evoCaption, fetchPartners, hopOnce, loadPartners, me_, OFFERED,
  onPartner, pack, partnerOf, partners, pkBig, pkCell, pkFace, refreshPartners, speciesName, spriteSrc, svFace, svOf,
  svPlace, svSrc, typeOf, withIcons};
