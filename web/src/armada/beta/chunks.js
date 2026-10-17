/* INTERIM BETA (README.md): what the page loads on first use. The lightbox, an artifact's document, a scene, what a
   plugin's widget opens (plugin_view.js), and the nav's cards for channels and topics with New from its "+"
   (channel_view.js, channel_new.js) are not part of the first load: each is a chunk (webpack splits an import() into one), asked for once and kept. Once the page
   has drawn its first place and goes idle they are fetched and run, so a press never waits on the network; a press
   that comes before (prefetchChunks() not yet run, or a slow network) shows nothing new and does its work when its
   chunk is in.
   A chunk imports nothing the first load holds: what it uses of the page is handed to its module's default export when
   it arrives. An import across chunks would keep webpack from joining the first load's modules into one scope, which
   costs more than the chunks save. */

import {$, $$, clamp, el, esc, press, T} from "./util.js";
import {groups, marks, members, people, places, repoStreams, state, streams, topicsOf, worktrees} from "./state.js";
import {age, dayLabel, hhmm} from "./time.js";
import {api} from "./connection.js";
import {faceOf, isOnix, nameOf, presOf} from "./faces.js";
import {ready} from "./composer.js";
import {loadPics, picHTML} from "./media.js";
import {katexSheet} from "./parts.js";
import {bound, dataAt, isObj, text, words} from "./artifacts.js";
import {openPlace, placeFromKey, tkey, topicLabel} from "./routing.js";
import {loadTopics} from "./nav.js";
import {isRepository, loadMembers, membersChanged, openWhenIn} from "./channels.js";
import {holds} from "./hover.js";
import {runsSeen} from "./runs.js";
import {repoPath} from "./header.js";
import {nav, navScroll, tx} from "./frame.js";
import {refLook, startThread} from "./mentions.js";
import {outsideCode, wireOf} from "./mention_text.js";

const LOAD = {
  lightbox: () => import(/* webpackChunkName: "beta-lightbox" */ "./lightbox.js"),
  artifact: () => import(/* webpackChunkName: "beta-artifact" */ "./artifact_view.js"),
  scene: () => import(/* webpackChunkName: "beta-scene" */ "./scene.js"),
  plugins: () => import(/* webpackChunkName: "beta-plugins" */ "./plugin_view.js"),
  channels: () => import(/* webpackChunkName: "beta-channels" */ "./channel_view.js"),
};
/* read when a chunk arrives, never while the modules load (they import each other in cycles) */
const page = () => ({$, $$, clamp, el, esc, press, T, state, dayLabel, hhmm, age, nameOf, ready, katexSheet, loadPics, picHTML, bound, dataAt, isObj, text, words,
  api, streams, repoStreams, members, marks, places, topicsOf, worktrees, runsSeen, people, faceOf, presOf, isOnix, openPlace, placeFromKey, tkey, topicLabel,
  loadTopics, loadMembers, membersChanged, holds, repoPath, nav, navScroll, startThread, openWhenIn, isRepository, groups, tx, wireOf, outsideCode,
  personByName: refLook.personByName});
const asked = new Map(), loaded = new Map();
/* the chunk, once it has run: a press reads it here and acts in its own frame */
const loadedChunk = (name) => loaded.get(name) || null;
/* the chunk, when it has arrived; one that fails to arrive is asked for again next time */
function chunk(name) {
  if (!asked.has(name)) asked.set(name, LOAD[name]().then((mod) => { const api = mod.default(page()); loaded.set(name, api); return api; }, (e) => { asked.delete(name); throw e; }));
  return asked.get(name);
}
/* after the first place is drawn, when the page is idle (Safari has no requestIdleCallback: a few seconds later) */
function prefetchChunks() {
  const go = () => { for (const name of Object.keys(LOAD)) chunk(name).catch(() => {}); };
  if (typeof requestIdleCallback === "function") requestIdleCallback(go, {timeout: 5000}); else setTimeout(go, 3000);
}
/* tests: a chunk that arrives when the test says */
function setChunkLoader(name, load) { LOAD[name] = load; asked.delete(name); loaded.delete(name); }

export {chunk, loadedChunk, prefetchChunks, setChunkLoader};
