/* INTERIM BETA (README.md): sending: the words as typed, mentions where they were written, retried. */

import {$, $$, el, esc} from "./util.js";
import {armadaBots, marks, state, stats} from "./state.js";
import {now} from "./time.js";
import {addMsg, keepEnd} from "./routing.js";
import {lane, nav, sh, tx} from "./frame.js";
import {echoHTML} from "./parts.js";
import {drawAtts, note, ready, syncField} from "./composer.js";
import {seedField, takeReply} from "./mentions.js";
import {wireOf, withPath} from "./mention_text.js";
import {appendMessage, grown, haltLane, hush, sectionOf} from "./feed.js";
import {drawTrail} from "./nav.js";
import {answered, drawAsks, liveRun, runMark, waitingRun} from "./runs.js";
import {transmit} from "./outbox.js";
import {drawRunState} from "./band.js";
import {drawSide} from "./column.js";
import {setTab} from "./views.js";
import {editing, saveEdit} from "./hover.js";
import {drawHomeRow} from "./home.js";
import {hopOnce} from "./partners.js";
import {choosePick} from "./partner_picker.js";
import {drawLab} from "./lab.js";
import {localSubmessages} from "./widgets.js";

/* ---------- sending ---------- */
/* the failed mark's undoing, when a retry starts or when the message's own event shows it arrived after all */
function clearFailMark(cl, id) { $$(`[data-mid="${id}"] .b`, cl).forEach((b) => b.classList.remove("unsent")); $$(`.bl[data-mid="${id}"] > .meta, .bl[data-mid="${id}"] > .lk`, cl).forEach((x) => x.remove()); }
function failMark(cl, m) { $$(`.bl[data-mid="${m.id}"] > .meta, .bl[data-mid="${m.id}"] > .lk`, cl).forEach((x) => x.remove()); const bl = $$(`.bl[data-mid="${m.id}"]`, cl).pop(); if (!bl) return;
  const meta = el("span", "meta bad", esc(m.failed === true ? "Not sent" : "Not sent: " + m.failed)), b = el("button", "lk", "Retry"); b.dataset.retry = m.id; bl.append(meta, b); host(bl); hush(bl); }
function send(o = {}) {
  if (state.place && state.place.kind === "partner") { choosePick(); return; }   /* on the picker, Return chooses */
  /* the field's words with their pills as Zulip's mention syntax; an option of Onix's question is its answer, so it names Onix */
  const at = o.at ?? performance.now(), t0 = performance.now(), typed = o.text != null ? (o.onix ? `${state.agentMention} ${o.text}` : o.text) : wireOf(tx, state.agentMention), files = o.text != null ? [] : state.atts.filter((a) => a.url);
  if (editing && o.text == null) { saveEdit(typed, at); return; }
  if (state.atts.some((a) => !a.url && !a.failed)) { note("Still uploading"); M.refuse(sh); return; }
  let text = [typed, ...files.map((a) => `[${a.name}](${a.url})`)].filter(Boolean).join("\n");
  if (!text) { M.refuse(sh); S.play("nope", 0, at); return; }
  if (!state.P.loaded && !state.P.error) { M.refuse(sh); return; }
  const inView = state.tab === "Files" ? state.P.filePath : state.tab === "README" ? state.P.readmePath : null;   /* sent from a view, the words say what was in view */
  if (state.tab !== "Chat") setTab("Chat", true);   /* the field is the same on every view; what it sends is read in Chat */
  /* To Onix: in a repository, when the words mention it (the switch's pill, one chosen from the picker, or @onix typed), or an
     option of its question pressed. The message is flagged agent_followup, as today's client flags it; the mention is what
     the server admits a run on, and what a waiting run takes as its answer. Nothing is added to the words: people talking
     to each other here do not answer a run that waits. */
  const p = state.place, c = state.P, live = liveRun(c), ask = waitingRun(c), toOnix = p.kind === "topic" && !!p.repo && (!!o.onix || text.includes(state.agentMention)), starts = toOnix && !live;
  if (inView && toOnix && o.text == null) text = withPath(text, inView, state.agentMention);
  if (o.text == null) text = takeReply() + text;   /* a reply: its one-line quote goes first */
  const raw = text, shown = echoHTML(text);
  const m = {id: "L" + ++state.localSeq, local: true, pending: true, sender_id: state.ME, timestamp: Math.floor(now() / 1000), content: shown, raw, toOnix, reactions: [], submessages: localSubmessages(text)};   /* a /poll or /todo shows as one at once (widgets.js) */
  let ph = null; if (starts) ph = {id: "O" + state.localSeq, local: true, onix: true, sender_id: [...armadaBots][0], timestamp: m.timestamp, content: "", reactions: [], is_agent_message: true, for: m.id};
  state.pinned = true; haltLane();
  const row = grown(() => { $$(":scope > .empty", lane).forEach((x) => x.remove()); addMsg(c, m); const r = appendMessage(lane, m); if (o.text == null) { tx.textContent = ""; state.atts = []; drawAtts(); } syncField(); if (ph) { addMsg(c, ph); r.onix = appendMessage(lane, ph); } return r; }, "touch", {clamp: {y: 240}});
  const b = $$(".b", row.parts.filter((x) => !/^pv/.test(x.dataset.part)).pop() || row.cl).pop(); if (b) M.appear(b, 0.6);   /* my bubble rises out of the field into its place with the conversation, one piece, on touch */
  S.play(live && toOnix ? "steer" : "send", 0, at); hopOnce($(".row.me", nav));   /* my face hops once; what I sent counts on the server, which says so (partners.js, onPartner) */
  if (row.onix) { const g = $(".sigil", row.onix.cl), w = $(".head > .mk", row.onix.cl); if (w) M.land(w); if (g) M.appear(g); S.play("ack", 0.12, at); }
  if (ask && toOnix) { c.answering = false; answered(ask); marks.set(p.key, runMark(ask)); drawRunState(); drawAsks(); drawTrail(p.key); drawHomeRow(p.key); }   /* the answer is in: the band says Working again, its clock from now */
  seedField(); ready();
  const task = performance.now() - t0, rec = {task: Math.round(task * 100) / 100, sinceKey: null, painted: null}; stats.sends.push(rec);
  requestAnimationFrame(() => setTimeout(() => { rec.painted = Math.round((performance.now() - at) * 10) / 10; rec.sinceKey = Math.round((t0 - at) * 10) / 10; drawLab(); }, 0));
  transmit(c, m, ph); keepEnd(c);
  requestAnimationFrame(() => setTimeout(() => { if (state.P === c) drawSide(); }, 0));   /* the column learns I spoke after the frame that shows my bubble, never inside the send task */
}
function retry(id) { const m = state.P.byId.get(id); if (!m || !m.failed) return; m.failed = false; m.pending = true; const cl = sectionOf(m.id); if (cl) clearFailMark(cl, m.id); S.play("send"); transmit(state.P, m, null); ready(); }

export {clearFailMark, failMark, retry, send};
