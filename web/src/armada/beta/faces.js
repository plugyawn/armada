/* INTERIM BETA (README.md): people and their faces: names, initials, stones, presence. */

import {armadaBots, people, state} from "./state.js";
import {now} from "./time.js";
import {brokenSprites, partnerOf, pkFace, spriteSrc} from "./partners.js";

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
const presOf = (id) => { const p = state.presences[person(id).email], a = p && p.aggregated; if (!a || now() / 1000 - a.timestamp > 300) return ""; return a.status === "active" ? "on" : a.status === "idle" ? "away" : ""; };
/* a face: the partner's sprite (partners section, below) wherever one is drawn at 18 px or more; else initials on the stone,
   and Onix's boulder disc. Every face carries its person, so a partner that arrives or evolves is redrawn in place. */
const faceSize = (cls) => (/s14/.test(cls || "") ? 14 : /s18/.test(cls || "") ? 18 : /s20/.test(cls || "") ? 20 : 28);
const faceOf = (id, cls, pres) => { const size = faceSize(cls), p = size >= 18 ? partnerOf(id) : null;
  if (p && !brokenSprites.has(spriteSrc(p.species, p.shiny))) { const h = pkFace(p, size, cls, pres, id); if (h) return h; }
  if (isOnix(id)) return agentFace(size).replace("<svg ", `<svg data-uid="${id}" data-fs="mark" `);
  return face(`${initials(person(id).name)}:${stoneOf(id)}${pres ? " " + pres : ""}`, cls).replace("<i ", `<i data-uid="${id}" data-fs="i" `); };

export {faceOf, faceSize, isOnix, nameOf, presOf, stoneOf};
