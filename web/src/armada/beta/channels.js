/* INTERIM BETA (README.md): the channels I am in, kept in the nav as I join and leave them (the `subscription` event), and
   their members; the press on the nav's "+", whose surface, New (channel_new.js), arrives with the channels chunk. */

import {$, press} from "./util.js";
import {members, repoStreams, streams, topicsOf} from "./state.js";
import {api} from "./connection.js";
import {openPlace, placeFromKey, tkey} from "./routing.js";
import {nav} from "./frame.js";
import {drawNav, loadTopics} from "./nav.js";
import {drawReceipts} from "./receipts.js";
import {chunk, loadedChunk} from "./chunks.js";

/* ---------- the channels I am in, as the server says they change ---------- */
const repositories = new Set();   /* the channels bound to a repository that I can see, in them or not (register) */
let opening = 0;                  /* a channel I made: its first place opens once the server says I am in it */
function applyChannels(r) { repositories.clear(); for (const id of r.repository_stream_ids || []) repositories.add(id); }
function onSubscription(ev) {
  if (ev.op === "peer_add" || ev.op === "peer_remove") { onPeers(ev); return; }   /* someone else joining or leaving */
  if (ev.op === "add") for (const s of ev.subscriptions) { const {subscribers, ...sub} = s; streams.set(s.stream_id, sub); if (repositories.has(s.stream_id)) repoStreams.add(s.stream_id); if (subscribers) members.set(s.stream_id, new Set(subscribers)); else members.delete(s.stream_id); }
  else if (ev.op === "remove") for (const s of ev.subscriptions) { streams.delete(s.stream_id); repoStreams.delete(s.stream_id); members.delete(s.stream_id); }
  else return;   /* a setting of mine: nothing the nav shows */
  drawNav();
  const v = loadedChunk("channels"); if (v && ev.op === "add") v.joined(ev.subscriptions);   /* joined from another tab: New's row says Open */
  if (opening && streams.has(opening)) openFirst(opening);
}
/* ---------- a channel's members: asked for once (GET streams/<id>/members), when a place in it opens or something needs
   them, then kept by the subscription event. Someone else joining or leaving is a peer_add or peer_remove, which the server
   sends to whoever may see the channel's members; one heard while the members are being asked for is applied to the answer,
   in order, since the answer may be older or newer than it. A channel I join comes with its members. ---------- */
function loadMembers(sid) { if (members.has(sid)) return Promise.resolve(members.get(sid));
  const p = api("GET", `streams/${sid}/members`).then((r) => { if (members.get(sid) !== p) return members.get(sid) || null;
    const s = new Set(r.subscribers); for (const ev of p.heard) peer(s, ev); members.set(sid, s); membersChanged(); return s; },
  () => { if (members.get(sid) === p) members.delete(sid); return null; });
  p.heard = []; members.set(sid, p); return p; }   /* the peer events heard while it is asked for */
const peer = (s, ev) => { for (const id of ev.user_ids) if (ev.op === "peer_add") s.add(id); else s.delete(id); };
function onPeers(ev) { for (const sid of ev.stream_ids) { const s = members.get(sid); if (s instanceof Set) peer(s, ev); else if (s) s.heard.push(ev); } membersChanged(); }
/* what counts a channel's members draws again: the ticks and Info, and a channel's card (channel_view.js) */
function membersChanged() { drawReceipts(); const v = loadedChunk("channels"); if (v) v.membersChanged(); }

/* a channel's first place, once I am in it */
function openWhenIn(sid) { opening = sid; if (streams.has(sid)) openFirst(sid); }
/* a channel's first place: its latest topic, as the first visit opens the first repository's; with none yet, its general chat */
async function openFirst(sid) { opening = 0; await loadTopics(sid); const t = (topicsOf.get(sid) || [])[0]; openPlace(placeFromKey(tkey(sid, t ? t.name : ""))); }

/* New, from the nav's "+": the surface is the channels chunk's (channel_new.js); a press before it arrives opens it then */
function wireChannels() { const plus = $(".top .new", nav); plus.setAttribute("aria-haspopup", "dialog"); plus.setAttribute("aria-expanded", "false");
  plus.addEventListener("mousedown", (e) => e.preventDefault());   /* the press leaves focus where it was, to be given back there */
  press(nav, ".top .new", () => { const v = loadedChunk("channels"); if (v) v.toggleNew(); else chunk("channels").then((x) => x.toggleNew(), () => {}); }); }

/* for New (channel_new.js): whether a channel I can see has a repository */
const isRepository = (sid) => repositories.has(sid);

export {applyChannels, isRepository, loadMembers, membersChanged, onSubscription, openWhenIn, wireChannels};
