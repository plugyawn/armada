/* INTERIM BETA (README.md): Home: what needs me. */

import {$, esc} from "./util.js";
import {marks, places, repoStreams, state, unread} from "./state.js";
import {hhmm} from "./time.js";
import {api} from "./connection.js";
import {placeFromKey, placeOfMsg, topicLabel} from "./routing.js";
import {nav} from "./frame.js";
import {faceOf, isOnix, nameOf} from "./faces.js";
import {plainOf, QUESTION, tpl} from "./parts.js";
import {loadTopics} from "./nav.js";
import {runsSeen, scanLive} from "./runs.js";
import {drawHeader} from "./header.js";
import {drawSide} from "./column.js";

const mentions = new Map();                                 /* message id -> message: unread mentions of me, for Home */

/* ---------- Home: what needs me. Runs that wait for my answer, then failed runs, mentions of me, and new words in worktrees. ---------- */
const HOME = {key: "home", kind: "home", repo: false};
function homeRows() { const needs = [], ment = [], news = [];
  for (const [k, runs] of runsSeen) { const p = placeFromKey(k); if (!p) continue; const r = runs.find((x) => (x.status === "queued" || x.status === "running") && x.waiting), where = `${p.stream} / ${topicLabel(p.topic)}`;
    if (r) { const c = places.get(k), q = c && c.msgs.filter((m) => isOnix(m.sender_id) && QUESTION.test(m.content || "")).pop(), h = (q && questionOf(q)) || r.question;
      needs.push({k, g: mk("needs", "s28"), text: h ? `Onix asks: ${h}` : "Onix is waiting for your answer", where, verb: "Answer", go: true, t: r.date_updated}); }
    else if (marks.get(k) === "failed") needs.push({k, g: mk("failed", "s28"), text: "Onix's run failed", where, verb: "Open", t: 0}); }
  for (const m of [...mentions.values()].sort((a, b) => b.id - a.id)) { const k = placeOfMsg(m), set = unread.get(k); if (!set || !set.has(m.id)) continue; const p = placeFromKey(k); if (!p) continue;
    ment.push({k, g: faceOf(m.sender_id), text: `${nameOf(m.sender_id)}: ${plainOf(m.content).slice(0, 120)}`, where: p.kind === "topic" ? `${p.stream} / ${topicLabel(p.topic)}` : "Direct message", time: m.timestamp}); }
  for (const [k, set] of unread) { if (!set.size || !k.startsWith("s:")) continue; const p = placeFromKey(k); if (!p || !p.repo || ment.some((x) => x.k === k)) continue; news.push({k, g: ic("repo"), text: `${set.size} new ${set.size === 1 ? "message" : "messages"} in ${topicLabel(p.topic)}`, where: p.stream, n: set.size}); }
  news.sort((a, b) => b.n - a.n); return {needs, ment, news}; }
/* the question's own words: the first "**header** — question" line of Onix's message */
function questionOf(m) { tpl.innerHTML = m.content || ""; const p = [...tpl.content.querySelectorAll("p")].find((x) => x.querySelector("strong") && / — /.test(x.textContent)); return p ? p.textContent.split(" — ").slice(1).join(" — ").trim() : ""; }
const hrowHTML = (r) => `<div class="hrow${r.verb ? " act" : ""}" role="button" tabindex="${r.verb ? -1 : 0}" data-k="${esc(r.k)}"><span class="g">${r.g}</span><span class="tx"><p>${esc(r.text)}</p><span class="wh">${esc(r.where)}</span></span>${r.verb ? `<span class="v"><button class="${r.go ? "go" : "lk"}" data-k="${esc(r.k)}"${r.verb === "Answer" ? " data-answer" : ""}>${r.verb}</button></span>` : r.time ? `<time>${hhmm(r.time)}</time>` : ""}</div>`;
function drawHome() { if (!state.place || state.place.kind !== "home") return; const box = $("#home"), {needs, ment, news} = homeRows(); let h = '<div class="home">';
  if (needs.length) h += needs.map(hrowHTML).join(""); h += `<div class="gh">Mentions of you</div>` + (ment.length ? ment.map(hrowHTML).join("") : '<p class="note">No unread mentions.</p>');
  h += `<div class="gh">New in worktrees</div>` + (news.length ? news.map(hrowHTML).join("") : '<p class="note">Nothing new.</p>');
  if (!homeScanned) h += '<p class="note">Looking at every worktree\'s runs</p>'; h += "</div>"; if (box._sig === h) return; box._sig = h; box.innerHTML = h; host(box);
  const n = needs.filter((x) => x.verb === "Answer").length, sum = n ? `${n === 1 ? "One question waits" : n + " questions wait"} for you.` : needs.length ? "A run failed." : "Nothing waits for you.";
  const say = $(".hd .say, .hd .l2 .s"); if (say && say.textContent !== sum) { state.hd._sig = ""; drawHeader(); } drawHomeTrail(); }
let homeScanned = false, homeTimer = 0;
function drawHomeRow() { clearTimeout(homeTimer); homeTimer = setTimeout(() => { drawHome(); drawHomeTrail(); if (state.place && state.place.kind === "home") { state.hd._sig = ""; drawHeader(); drawSide(); } }, 50); }
function drawHomeTrail() { const r = $('.row[data-k="home"]', nav); if (!r) return; const n = [...marks.values()].filter((v) => v === "needs").length, old = $(":scope > .mk", r); if (!!old === !!n) return; if (old) old.remove(); if (n) { r.insertAdjacentHTML("beforeend", mk("needs")); M.appear(r.lastElementChild); } }
async function scanAll() { await Promise.all([scanLive(), ...[...repoStreams].map((sid) => loadTopics(sid))]); homeScanned = true; drawHomeRow(); }   /* the repositories' topics name the rows */
async function fetchMentions() { try { const r = await api("GET", "messages", {narrow: [{operator: "is", operand: "mentioned"}], anchor: "newest", num_before: 60, num_after: 0, apply_markdown: true, client_gravatar: false}); for (const m of r.messages) if (!(m.flags || []).includes("read")) mentions.set(m.id, m); } catch (e) { return; } drawHomeRow(); }

export {drawHome, drawHomeRow, drawHomeTrail, fetchMentions, HOME, homeRows, homeScanned, mentions, questionOf,
  scanAll};
