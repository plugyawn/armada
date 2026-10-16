/* INTERIM BETA (README.md): people and their faces: names, initials, stones, presence. */

import {armadaBots, DATA, people, state} from "./state.js";
import {now} from "./time.js";
import {brokenSprites, partnerOf, svFace, svSrc} from "./partners.js";

const STONES = ["lapis", "larimar", "rose", "amethyst", "moss", "flint"];
/* a person's stone: mine is my colour; with a partner, the stone of its type (the server says which); else one by user id */
const stoneOf = (id) => { if (id === state.ME) return "me"; const p = partnerOf(id); return p && p.stone ? p.stone : STONES[id % STONES.length]; };
const person = (id) => people.get(id) || {id, name: "Someone", email: ""};
const isOnix = (id) => armadaBots.has(id);
const nameOf = (id) => (isOnix(id) ? "Onix" : person(id).name);
/* a face's initials: letters and figures of any script (a name in Devanagari keeps its first letter with its vowel sign),
   cut by grapheme, so no name is drawn as "?" while it has a letter */
const graphemes = (t) => { try { return [...new Intl.Segmenter(undefined, {granularity: "grapheme"}).segment(t)].map((x) => x.segment); } catch (e) { return [...t]; } };
const initials = (name) => { const w = name.replace(/[^\p{L}\p{N}\p{M} ]/gu, "").split(/\s+/).filter(Boolean); if (!w.length) return "?"; const g = graphemes(w[0]);
  if (!/^[\p{Script=Latin}0-9]/u.test(w[0])) return g[0];   /* another script: one letter, which fills the disc as two Latin letters do */
  return w.length > 1 && /^\p{Lu}/u.test(w[1]) ? g[0].toUpperCase() + graphemes(w[1])[0] : g[0].toUpperCase() + (g[1] && /^[\p{Script=Latin}0-9]/u.test(g[1]) ? g[1] : ""); };
/* here ("on") while the person was last active within the server's offline threshold, away while they were last idle within it
   (presence.js keeps state.presences: by user id, the last times each was active and idle) */
const OFFLINE_S = +DATA.presenceOffline || 200;
const presOf = (id) => { const p = state.presences[id]; if (!p) return ""; const t = now() / 1000; return t - (p.active_timestamp || 0) < OFFLINE_S ? "on" : t - (p.idle_timestamp || 0) < OFFLINE_S ? "away" : ""; };
/* a face (the r5 round-four study, sv.html): a gradient circle in the colour of the partner's type with its Scarlet/Violet
   icon standing whole on it, head over the rim, wherever one is drawn at 18 px or more; else initials on the stone. Onix
   has no face: in the gutter nothing is drawn (its words start at the edge), and in a list an empty cell keeps the names
   on one axis. Every face carries its person, so a partner that arrives or evolves is redrawn in place, and a press on
   one opens that person's card (partner_card.js). */
const faceSize = (cls) => (/s14/.test(cls || "") ? 14 : /s18/.test(cls || "") ? 18 : /s20/.test(cls || "") ? 20 : 28);
const faceOf = (id, cls, pres) => { const size = faceSize(cls);
  if (isOnix(id)) return size === 28 ? "" : `<i class="av none s${size}" aria-hidden="true"></i>`;
  const p = size >= 18 ? partnerOf(id) : null;
  if (p && !brokenSprites.has(svSrc(p.species))) { const h = svFace(p.species, size, cls, pres, id); if (h) return h; }
  return face(`${initials(person(id).name)}:${stoneOf(id)}${pres ? " " + pres : ""}`, cls).replace("<i ", `<i data-uid="${id}" data-fs="i" `); };
/* a large face for a person's card: the partner on its circle, or the initials, at `size` px */
const bigFaceOf = (id, size) => { const p = partnerOf(id);
  if (p && !brokenSprites.has(svSrc(p.species))) { const h = svFace(p.species, size, "", "", 0); if (h) return h; }
  return `<i class="av p-${stoneOf(id)} big" style="width:${size}px;height:${size}px" aria-hidden="true">${initials(person(id).name)}</i>`; };

export {bigFaceOf, faceOf, faceSize, initials, isOnix, nameOf, person, presOf, stoneOf};
