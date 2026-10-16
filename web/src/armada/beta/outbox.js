/* INTERIM BETA (README.md): a message on its way to the server, and the server's copy taking its place. */

import {$$} from "./util.js";
import {msgIndex, state} from "./state.js";
import {api} from "./connection.js";
import {feed, lane} from "./frame.js";
import {partsOf} from "./parts.js";
import {note} from "./composer.js";
import {removeRow, sectionOf, shown, stale} from "./feed.js";
import {drawTicks, retick} from "./receipts.js";
import {updateMessage} from "./agent_turn.js";
import {clearFailMark, failMark} from "./send.js";
import {clientKey} from "./mention_text.js";

async function transmit(c, m, ph) {
  /* contract C4: the key is made once for the message and sent again on every retry, so the server sends it once */
  if (!m.client_key) m.client_key = clientKey((a) => crypto.getRandomValues(a));
  const p = c.place, q = {content: m.raw, local_id: m.id, queue_id: state.queue, read_by_sender: true, client_key: m.client_key};
  if (p.kind === "topic") Object.assign(q, {type: "stream", to: p.stream_id, topic: p.topic}); else Object.assign(q, {type: "direct", to: p.ids.length ? p.ids : [state.ME]});
  if (m.thread_root_message_id) q.thread_root_message_id = m.thread_root_message_id;
  if (m.toOnix) q.agent_followup = true;                 /* what today's client sends when its Onix control is on (web/src/compose.ts) */
  const watchRun = () => { if (ph) setTimeout(() => { if (ph.local && c.byId.has(ph.id)) { dropLocal(c, ph); note("Onix did not start a run for that message."); } }, 20000); };
  try { const r = await api("POST", "messages", q); if (m.pending) { adopt(c, m, r.id); } watchRun(); }
  catch (e) { if (!m.pending) { watchRun(); return; }   /* its own event adopted it first: it arrived, only the answer to the POST was lost */
    m.pending = false; m.failed = e.net ? true : e.message; if (ph) dropLocal(c, ph); if (!shown(c)) stale(c, m.id); else { const cl = sectionOf(m.id); if (cl) { $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.add("unsent")); failMark(cl, m); M.refuse($$(`.bl[data-mid="${m.id}"] .b`, cl).pop() || cl); } drawTicks(m.id); } S.play("fail"); }
}
function dropLocal(c, x) { c.byId.delete(x.id); msgIndex.delete(x.id); c.msgs = c.msgs.filter((y) => y !== x); if (!shown(c)) { removeRow(c, x.id); return; } const cl = sectionOf(x.id); c.rows.delete(x.id); if (cl) M.leave(cl, () => { const y = feed.scrollTop; cl.remove(); feed.scrollTop = y; }); }
/* the server's copy of what I sent: my row takes its id. Nothing on screen changes unless the server rendered it differently. */
function adopt(c, m, id, server) {
  const old = m.id; if (old !== id) { c.byId.delete(old); msgIndex.delete(old); m.id = id; c.byId.set(id, m); msgIndex.set(id, c); const cl = c.rows.get(old); if (cl) { c.rows.delete(old); c.rows.set(id, cl); for (const n of [cl, ...cl.querySelectorAll(`[data-mid="${old}"]`)]) if (n.dataset.mid === String(old)) n.dataset.mid = id; cl.querySelectorAll(`.rt[data-info="${old}"]`).forEach((n) => (n.dataset.info = id)); } retick(old, id); }
  /* a send whose answer was lost was marked Not sent; its own event says it arrived, so the mark and its Retry go */
  if (m.failed) { const cl = c.rows.get(id); if (cl) clearFailMark(cl, id); }
  m.local = false; m.pending = false; m.failed = false;
  if (server) { const was = JSON.stringify(partsOf(m).map((p) => [p.t, p.html])); Object.assign(m, server, {id}); const is = JSON.stringify(partsOf(m).map((p) => [p.t, p.html])); if (was !== is) { if (shown(c)) updateMessage(c, m); else stale(c, id); } }
  c.msgs.sort((a, b) => (typeof a.id === "number" && typeof b.id === "number" ? a.id - b.id : 0));
  if (c === state.P) drawTicks(id);   /* sent: one tick lands */
}

export {adopt, transmit};
