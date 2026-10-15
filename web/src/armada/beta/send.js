/* INTERIM BETA (README.md): sending: the words as typed, addressed to the agent or not, retried. */

import {$, $$, el, esc} from "./util.js";
import {armadaBots, marks, state, stats} from "./state.js";
import {now} from "./time.js";
import {addMsg} from "./routing.js";
import {lane, nav, sh, tx} from "./frame.js";
import {echoHTML} from "./parts.js";
import {drawAtts, note, onixNow, ready, syncField} from "./composer.js";
import {appendMessage, grown, haltLane, hush, sectionOf} from "./feed.js";
import {drawTrail} from "./nav.js";
import {answered, drawAsks, liveRun, runMark, waitingRun} from "./runs.js";
import {transmit} from "./outbox.js";
import {drawRunState} from "./band.js";
import {drawSide} from "./column.js";
import {setTab} from "./views.js";
import {editing, saveEdit} from "./hover.js";
import {drawHomeRow} from "./home.js";
import {hopOnce, refreshPartners} from "./partners.js";
import {choosePick} from "./partner_picker.js";
import {drawLab} from "./lab.js";

/* ---------- sending ---------- */
function failMark(cl, m) { $$(`.bl[data-mid="${m.id}"] > .meta, .bl[data-mid="${m.id}"] > .lk`, cl).forEach((x) => x.remove()); const bl = $$(`.bl[data-mid="${m.id}"]`, cl).pop(); if (!bl) return;
  const meta = el("span", "meta bad", esc(m.failed === true ? "Not sent" : "Not sent: " + m.failed)), b = el("button", "lk", "Retry"); b.dataset.retry = m.id; bl.append(meta, b); host(bl); hush(bl); }
/* "@Onix" in my words is the agent's mention on the wire; a block that starts the words keeps its own line after it */
const ONIX_AT = /(?<![\w@])@(?:\*\*)?Onix(?:\*\*)?(?![\w-])/gi;
const startsBlock = (t) => /^\s*(```|~~~|[-*+] |\d+[.)] |> ?)/.test(t);
const addressed = (bare) => `${state.agentMention}${startsBlock(bare) ? "\n\n" : " "}${bare}`;
const addressedEcho = (bare) => (startsBlock(bare) ? '<p><span class="mention">Onix</span></p>' + echoHTML(bare) : echoHTML(bare).replace(/^<p>/, '<p><span class="mention">Onix</span> '));
function send(o = {}) {
  if (state.place && state.place.kind === "partner") { choosePick(); return; }   /* on the picker, Return chooses */
  const at = o.at ?? performance.now(), t0 = performance.now(), typed = o.text ?? tx.textContent.trim(), files = o.text != null ? [] : state.atts.filter((a) => a.url);
  if (editing && o.text == null) { saveEdit(typed, at); return; }
  if (state.atts.some((a) => !a.url && !a.failed)) { note("Still uploading"); M.refuse(sh); return; }
  const text = [typed, ...files.map((a) => `[${a.name}](${a.url})`)].filter(Boolean).join("\n");
  if (!text) { M.refuse(sh); S.play("nope", 0, at); return; }
  if (!state.P.loaded && !state.P.error) { M.refuse(sh); return; }
  const inView = state.tab === "Files" ? state.P.filePath : state.tab === "README" ? state.P.readmePath : null;   /* sent from a view, the words say what was in view */
  if (state.tab !== "Chat") setTab("Chat", true);   /* the field is the same on every view; what it sends is read in Chat */
  /* To Onix: in a repository, with the place's switch on, or words that name @Onix, or an option of its question pressed.
     The message is flagged agent_followup, as today's client flags it, and it also names the agent. The flag alone starts
     nothing on this server (its addressing router is disabled, so an unaddressed follow-up is dropped); the mention is
     what the server admits a run on, and what a waiting run takes as its answer. With the switch off, a run that waits
     is not answered by people talking to each other. */
  const named_ = ONIX_AT.test(text); ONIX_AT.lastIndex = 0;
  const p = state.place, c = state.P, live = liveRun(c), ask = waitingRun(c), toOnix = p.kind === "topic" && !!p.repo && (!!o.onix || onixNow() || named_), starts = toOnix && !live;
  const bare0 = text.replace(ONIX_AT, "").trim() || text, bare = inView && toOnix && o.text == null ? `\`${inView}\` ${bare0}` : bare0;
  const raw = toOnix || named_ ? addressed(bare) : text, shown = toOnix || named_ ? addressedEcho(bare) : echoHTML(text);
  const m = {id: "L" + ++state.localSeq, local: true, pending: true, sender_id: state.ME, timestamp: Math.floor(now() / 1000), content: shown, raw, toOnix, reactions: []};
  let ph = null; if (starts) ph = {id: "O" + state.localSeq, local: true, onix: true, sender_id: [...armadaBots][0], timestamp: m.timestamp, content: "", reactions: [], is_agent_message: true, for: m.id};
  state.pinned = true; haltLane();
  const row = grown(() => { $$(":scope > .empty", lane).forEach((x) => x.remove()); addMsg(c, m); const r = appendMessage(lane, m); if (o.text == null) { tx.textContent = ""; state.atts = []; drawAtts(); } syncField(); if (ph) { addMsg(c, ph); r.onix = appendMessage(lane, ph); } return r; }, "touch", {clamp: {y: 240}});
  const b = $$(".b", row.parts[row.parts.length - 1] || row.cl).pop(); if (b) M.appear(b, 0.6);   /* my bubble rises out of the field into its place with the conversation, one piece, on touch */
  S.play(live && toOnix ? "steer" : "send", 0, at); hopOnce($(".row.me", nav)); refreshPartners({fold: true, wait: 2000});   /* my face hops once; what I sent counts, after the frame */
  if (row.onix) { const g = $(".sigil", row.onix.cl), w = $(".head > svg", row.onix.cl); if (w) M.land(w); if (g) M.appear(g); S.play("ack", 0.12, at); }
  if (ask && toOnix) { c.answering = false; answered(ask); marks.set(p.key, runMark(ask)); drawRunState(); drawAsks(); drawTrail(p.key); drawHomeRow(p.key); }   /* the answer is in: the band says Working again, its clock from now */
  ready();
  const task = performance.now() - t0, rec = {task: Math.round(task * 100) / 100, sinceKey: null, painted: null}; stats.sends.push(rec);
  requestAnimationFrame(() => setTimeout(() => { rec.painted = Math.round((performance.now() - at) * 10) / 10; rec.sinceKey = Math.round((t0 - at) * 10) / 10; drawLab(); }, 0));
  transmit(c, m, ph);
  requestAnimationFrame(() => setTimeout(() => { if (state.P === c) drawSide(); }, 0));   /* the column learns I spoke after the frame that shows my bubble, never inside the send task */
}
function retry(id) { const m = state.P.byId.get(id); if (!m || !m.failed) return; m.failed = false; m.pending = true; const cl = sectionOf(m.id); if (cl) { $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.remove("unsent")); $$(`.bl[data-mid="${m.id}"] > .meta, .bl[data-mid="${m.id}"] > .lk`, cl).forEach((x) => x.remove()); } S.play("send"); transmit(state.P, m, null); ready(); }

export {addressedEcho, failMark, ONIX_AT, retry, send};
