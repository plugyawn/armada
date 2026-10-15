/* INTERIM BETA (README.md): reactions. */

import {$, $$, el, esc} from "./util.js";
import {msgIndex, state} from "./state.js";
import {api} from "./connection.js";
import {note} from "./composer.js";
import {hush, sectionOf} from "./feed.js";
import {refreshPartners} from "./partners.js";

/* ----- reactions ----- */
function chipsOf(m) { const by = new Map(); for (const r of m.reactions || []) { const k = r.reaction_type + ":" + r.emoji_code; let g = by.get(k); if (!g) by.set(k, (g = {r, n: 0, mine: false})); g.n++; if (r.user_id === state.ME) g.mine = true; } return [...by.values()]; }
const emojiChar = (r) => { if (r.reaction_type !== "unicode_emoji") return ":" + r.emoji_name + ":"; try { return String.fromCodePoint(...r.emoji_code.split("-").map((h) => parseInt(h, 16))); } catch (e) { return ":" + r.emoji_name + ":"; } };
function drawReactions(cl, m) {
  $$(`.rcs[data-mid="${m.id}"]`, cl).forEach((x) => (x.closest(".rca") || x).remove());
  const groups = chipsOf(m), own = $$(`:scope > [data-mid="${m.id}"]`, cl); if (!own.length) return null;
  const onix = cl.classList.contains("onix"), bubbles = own.filter((e) => e.classList.contains("bl")), b = bubbles.length ? $(".b", bubbles[bubbles.length - 1]) : null;
  if (b) b.classList.toggle("liked", groups.some((g) => g.mine) && !cl.classList.contains("me"));
  if (!groups.length) return null;
  const rcs = el("span", "rcs", groups.map((g) => `<button class="rc${g.mine ? " mine" : ""}" data-name="${esc(g.r.emoji_name)}" data-code="${esc(g.r.emoji_code)}" data-type="${esc(g.r.reaction_type)}" title="${esc(":" + g.r.emoji_name + ":")}"><span class="e">${esc(emojiChar(g.r))}</span>${g.n > 1 ? `<span class="n fig">${fig(g.n)}</span>` : ""}</button>`).join("")); rcs.dataset.mid = m.id;
  if (onix) { const ans = own.filter((e) => e.classList.contains("ans")).pop(), lastP = ans && ($$(":scope > p", ans).pop() || ans.lastElementChild); if (!lastP) { own[own.length - 1].after(rcs); return rcs; } const a = el("span", "rca"); a.append(rcs); lastP.append(a); }
  else if (b) b.after(rcs); else { const r = el("div", "rcpt" + (cl.classList.contains("me") ? " end" : "")); r.style.height = "auto"; r.append(rcs); own[own.length - 1].after(r); r.dataset.mid = m.id; r.classList.add("rcs"); }
  hush(cl); return rcs;
}
function applyReaction(c, m, ev, mine) {
  m.reactions = m.reactions || []; const i = m.reactions.findIndex((r) => r.user_id === ev.user_id && r.emoji_code === ev.emoji_code && r.reaction_type === ev.reaction_type);
  if (ev.op === "add") { if (i >= 0) return; m.reactions.push({user_id: ev.user_id, emoji_name: ev.emoji_name, emoji_code: ev.emoji_code, reaction_type: ev.reaction_type}); } else { if (i < 0) return; m.reactions.splice(i, 1); }
  if (c !== state.P) return; const cl = sectionOf(m.id); if (!cl) return;
  const timeEl = $(`.bl[data-mid="${m.id}"] > time`, cl), x0 = timeEl && M.rest(timeEl).left; const rcs = drawReactions(cl, m);
  const rc = rcs && $$(".rc", rcs).find((x) => x.dataset.code === ev.emoji_code);
  if (ev.op === "add" && rc) { if (mine) { const b = cl.classList.contains("onix") ? null : $(`.bl[data-mid="${m.id}"] .b`, cl); M.land($(".n", rc) ? $(".e", rc) : rc); M.burst(rc, emojiChar(ev)); S.play("react");
      if (b) { const k = cl.classList.contains("me") ? 1 : -1; M.squash(b); M.kick(b, {x: 110 * k, y: 110}); if (b.classList.contains("liked")) M.release(b, {sx: -0.7, sy: -0.7}, "move", {pseudo: "::after", id: "wash"}); } }
    else M.appear($(".n", rc) || rc); }
  if (timeEl && timeEl.isConnected && mine) { const d = x0 - M.rest(timeEl).left; if (d) M.release(timeEl, {x: d}, "touch"); }
}
async function react(m, r) {   /* my reaction: it lands in this frame; the server is told; if it refuses, it is taken back */
  if (typeof m.id !== "number") return; const has = (m.reactions || []).some((x) => x.user_id === state.ME && x.emoji_code === r.emoji_code && x.reaction_type === r.reaction_type);
  const ev = {op: has ? "remove" : "add", user_id: state.ME, emoji_name: r.emoji_name, emoji_code: r.emoji_code, reaction_type: r.reaction_type}; applyReaction(state.P, m, ev, true);
  try { await api(has ? "DELETE" : "POST", `messages/${m.id}/reactions`, {emoji_name: r.emoji_name, emoji_code: r.emoji_code, reaction_type: r.reaction_type}); refreshPartners({fold: true, wait: 2000}); }
  catch (e) { applyReaction(msgIndex.get(m.id) || state.P, m, {...ev, op: has ? "add" : "remove"}, false); note(e.message); }
}

export {applyReaction, drawReactions, react};
