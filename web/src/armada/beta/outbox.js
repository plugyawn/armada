/* INTERIM BETA (README.md): a message on its way to the server, and the server's copy taking its place. */

import {$$} from "./util.js";
import {msgIndex, state} from "./state.js";
import {api} from "./connection.js";
import {feed, lane} from "./frame.js";
import {partsOf} from "./parts.js";
import {note} from "./composer.js";
import {sectionOf} from "./feed.js";
import {drawTicks} from "./receipts.js";
import {updateMessage} from "./agent_turn.js";
import {failMark} from "./send.js";

async function transmit(c, m, ph) {
  const p = c.place, q = {content: m.raw, local_id: m.id, queue_id: state.queue, read_by_sender: true};
  if (p.kind === "topic") Object.assign(q, {type: "stream", to: p.stream_id, topic: p.topic}); else Object.assign(q, {type: "direct", to: p.ids.length ? p.ids : [state.ME]});
  if (m.thread_root_message_id) q.thread_root_message_id = m.thread_root_message_id;
  if (m.toOnix) q.agent_followup = true;                 /* what today's client sends when its Onix control is on (web/src/compose.ts) */
  try { const r = await api("POST", "messages", q); if (m.pending) { adopt(c, m, r.id); }
    if (ph) setTimeout(() => { if (ph.local && c.byId.has(ph.id)) { dropLocal(c, ph); note("Onix did not start a run for that message."); } }, 20000); }
  catch (e) { m.pending = false; m.failed = e.net ? true : e.message; if (ph) dropLocal(c, ph); if (c === state.P) { const cl = sectionOf(m.id); if (cl) { $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.add("unsent")); failMark(cl, m); M.refuse($$(`.bl[data-mid="${m.id}"] .b`, cl).pop() || cl); } drawTicks(m.id); } S.play("fail"); }
}
function dropLocal(c, x) { c.byId.delete(x.id); msgIndex.delete(x.id); c.msgs = c.msgs.filter((y) => y !== x); if (c !== state.P) return; const cl = sectionOf(x.id); if (cl) M.leave(cl, () => { const y = feed.scrollTop; cl.remove(); feed.scrollTop = y; }); }
/* the server's copy of what I sent: my row takes its id. Nothing on screen changes unless the server rendered it differently. */
function adopt(c, m, id, server) {
  const old = m.id; if (old !== id) { c.byId.delete(old); msgIndex.delete(old); m.id = id; c.byId.set(id, m); msgIndex.set(id, c); if (c === state.P) $$(`[data-mid="${old}"]`, lane).forEach((n) => (n.dataset.mid = id)); if (c === state.P) $$(".cl", lane).forEach((n) => { if (n.dataset.mid === String(old)) n.dataset.mid = id; }); if (c === state.P) $$(`.rt[data-info="${old}"]`, lane).forEach((n) => (n.dataset.info = id)); }
  m.local = false; m.pending = false; m.failed = false;
  if (server) { const was = JSON.stringify(partsOf(m).map((p) => [p.t, p.html])); Object.assign(m, server, {id}); const is = JSON.stringify(partsOf(m).map((p) => [p.t, p.html])); if (c === state.P && was !== is) updateMessage(c, m); }
  c.msgs.sort((a, b) => (typeof a.id === "number" && typeof b.id === "number" ? a.id - b.id : 0));
  if (c === state.P) drawTicks(id);   /* sent: one tick lands */
}

export {adopt, transmit};
