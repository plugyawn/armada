/* INTERIM BETA (README.md): the page's state: what the server gave, and what more than one part of the page replaces. */

import {$, store} from "./util.js";

const DATA = ($("#beta-data") || {dataset: {}}).dataset;   /* what the page cannot ask for (zerver/views/armada_beta.py) */
const QUESTION_TIMEOUT = +DATA.questionTimeout || 600;    /* how long a run waits for an answer, then goes on with defaults (ARMADA_QUESTION_TIMEOUT_SECONDS) */
const LIVE_RUN_IDS = (() => { try { return JSON.parse(DATA.liveRunIds || "[]"); } catch (e) { return []; } })();   /* the live runs in my channels at load: where Home starts */
/* the page's own measurements, which the Lab and the checks read: each list keeps its newest 600 entries, as the event log does */
class Recent extends Array { static get [Symbol.species]() { return Array; } push(...x) { super.push(...x); if (this.length > 600) this.splice(0, this.length - 600); return this.length; } }
const stats = {sends: new Recent(), firstWords: new Recent(), runs: new Recent(), events: 0, api: 0, log: []};

/* ---------- state ---------- */
/* what more than one part of the page replaces: the session, the place in view and its cache, the view, the feed's scroll,
   the field. The maps below are changed in place. */
const state = {
  ME: 0, meName: "", queue: null, lastEvent: -1, skew: 0,
  dms: [], presences: {}, place: null, P: null, tab: "Chat", localSeq: 0, agentMention: "@**Onix**",
  runtimeCfg: null,                                        /* the server's runtime settings, from armada/runs: the service tier is server-wide */
  /* the realm's rules for changing and deleting a message (register, then the realm event): a limit of null is none; a
     group setting is as the server writes it, a group's id or {direct_members, direct_subgroups}, read against `groups`.
     `guest`: I am a guest, whom the realm's can_delete_any_message_group never holds. */
  realm: {allowEdit: true, editLimit: null, deleteLimit: null, deleteAny: null, deleteOwn: null, guest: false},
  muted: new Set(),                                        /* the people I muted: receipts do not count them */
  hd: null,                                                /* the header, drawn again whole when what it says changes */
  pinned: true, lastSet: 0,                                /* the feed: whether it keeps to its end, and where it was last scrolled to */
  threadTx: null,                                          /* the thread field that holds the caret, while it does */
  atts: [],                                                /* the field's attachments, until the message is sent */
  lastKey: -1e9, pressing: false,                          /* the last key and a press held: an evolution waits for the page to be idle */
};
const people = new Map(), streams = new Map(), repoStreams = new Set(), armadaBots = new Set();
const topicsOf = new Map();                                /* stream id -> [{name, max_id}] in the order first shown; null while loading */
const worktrees = new Map();                               /* stream id -> Set of lower-cased topic names that have a worktree */
const unread = new Map();                                  /* place key -> Set of message ids */
const marks = new Map();                                   /* place key -> "needs" | "live" | "queued" | "failed" | "paused": a run's mark in the nav */
const places = new Map();                                  /* place key -> what is known of it */
const groups = new Map();                                  /* user group id -> {members, subgroups}: Sets of ids, the subgroups direct */
const msgIndex = new Map();                                /* message id -> its place's cache */
/* what the page keeps of the places it has opened (routing.js, keep): a place left keeps its newest KEEP messages and their
   rows; the place in view, its end in view, trims back to KEEP once it holds CAP; PLACES_KEPT places are kept at most */
const KEEP = 100, CAP = 400, PLACES_KEPT = 10;
const expanded = new Set(JSON.parse(store.get("open") || "[]")), allKids = new Set();

export {allKids, armadaBots, CAP, DATA, expanded, groups, KEEP, LIVE_RUN_IDS, marks, msgIndex, people, places, PLACES_KEPT,
  QUESTION_TIMEOUT, repoStreams, state, stats, streams, topicsOf, unread, worktrees};
