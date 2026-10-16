/* INTERIM BETA (README.md): what a link becomes under the words that hold it (statement 41). The design draws one shape
   for it, a card on the block's fill, 368 wide ("the one thing narrower than the lane by rule is a link's preview, 368,
   because it belongs to the bubble above it"), and it stands under the bubble (or Onix's paragraph) whose link it is:
   - a page: the server's div.message_embed, which its embed worker writes into the message seconds after it was sent.
     A card of 56: the title, the site and the description, and a 40 px picture that loads lazily inside its box (the link
     glyph without one). The whole card opens the link.
   - a video (YouTube's, Vimeo's and any oEmbed video preview): its still at 16:9 with a play mark, then the title and
     the site; it opens the video. No player in the lane. The bubble keeps the link as it was typed: the server rewrites a
     bare video link's words to "YouTube - <title>" when its title arrives, and that title is the card's.
   - a link the server is fetching a preview of: the card's place, kept from the moment the message is drawn, with the
     site's name, so that the preview fills it and nothing moves. The server says which links: its renderer marks each one
     it asks the embed worker to fetch (a data-preview), and the worker's rendering has no mark, preview or none. Only my
     echo, drawn before the server's copy exists, mirrors the renderer's rule (fetchedLinks). A mark is honoured for
     WAIT_LIMIT after the message was sent or edited: a worker that never answers, or a rendering that never asked it,
     leaves a mark behind.
   A bubble shows at most three cards; past that, a quiet "+N more" line under them opens the rest in place, below it, so
   nothing above it moves. */

import {$$, el, esc, press} from "./util.js";
import {msgIndex, people, state} from "./state.js";
import {now} from "./time.js";
import {isOnix} from "./faces.js";
import {lane} from "./frame.js";
import {dist} from "./feed.js";
import {updateMessage} from "./agent_turn.js";

const MAX_UNDER = 3;                           /* the cards a bubble shows before "+N more" */
const WAIT_LIMIT = 90e3;                       /* how long a mark is honoured, from the message's send or edit */
const MAX_FETCHED = 24;                        /* InlineInterestingLinkProcessor.INLINE_PREVIEW_LIMIT_PER_MESSAGE */
const IMAGE = /\.(bmp|gif|jpe|jpeg|jpg|png|webp)$/i, MOVIE = /\.(mp4|m4v|mov|qt|webm)$/i;   /* the server inlines these at once */

const VIDEO_DIV = "div.youtube-video, div.vimeo-video, div.embed-video";
const SITE_PREFIX = /^(YouTube|Vimeo) - /;
const PLAY = '<svg class="play" viewBox="0 0 16 16" aria-hidden="true"><path d="M5.5 3.5v9l7-4.5z"/></svg>';
const urlOf = (href) => { try { return new URL(href, location.href); } catch (e) { return null; } };
const siteOf = (href) => { const u = urlOf(href); return u ? u.hostname.replace(/^www\./, "") : ""; };
const youtubeId = (u) => { const h = u.hostname; if (/^(m\.|www\.)?youtube(-nocookie)?\.com$/.test(h)) return u.searchParams.get("v") || /^\/(embed|shorts|v)\/([\w-]+)/.exec(u.pathname)?.[2] || null; return h === "youtu.be" ? u.pathname.slice(1) || null : null; };
const isVideoSite = (href) => { const u = urlOf(href); return !!u && (!!youtubeId(u) || /^(www\.)?vimeo\.com$/.test(u.hostname)); };

/* ---------- which links the server is fetching a preview of ----------
   my echo's: zerver/lib/markdown InlineInterestingLinkProcessor.run, as a message is sent: with URL previews on (the
   server's setting and the realm's), from a person (or the agent: its own posts are previewed), every distinct link
   outside a quote, unless the message has more than 24 of them; not a link to this server or a relative one, not
   Dropbox's (previewed at once), not a picture or a movie by its name (inlined at once while image previews are on). A
   YouTube link is fetched for its title; my echo keeps its card's place, which the server's copy fills with the still. */
function fetchedLinks(root, senderId) {
  const r = state.realm, who = people.get(senderId); if (!r.urlPreviews || !r.serverUrlPreviews || (who && who.bot && !isOnix(senderId))) return [];
  const hrefs = []; for (const a of $$("a[href]", root)) { if (a.closest("blockquote")) continue; const h = a.getAttribute("href"); if (!hrefs.includes(h)) hrefs.push(h); }
  if (hrefs.length > MAX_FETCHED) return [];
  const inlined = new Set($$("div.message_inline_image a[href], div.message_inline_video a[href]", root).map((a) => a.getAttribute("href")));
  return hrefs.filter((h) => { const u = /^https?:\/\//i.test(h) && urlOf(h); if (!u || u.host === location.host || /(^|\.)dropbox\.com$/.test(u.hostname) || inlined.has(h)) return false;
    return !(r.imagePreviews && r.serverImagePreviews && (IMAGE.test(u.pathname) || MOVIE.test(u.pathname))); });
}
/* the links of a message whose previews are coming: the server's marks, or my echo's guess; none once WAIT_LIMIT has
   passed since it was sent or edited. A message still waiting is drawn again when its time is up. */
const waits = new Map();                       /* message id -> the timer that closes its places */
function waitingLinks(root, m) {
  const until = (m.last_edit_timestamp || m.timestamp || 0) * 1000 + WAIT_LIMIT, left = until - now(); if (left <= 0) return new Set();
  const hrefs = m.local ? fetchedLinks(root, m.sender_id) : $$("a[data-preview]", root).map((a) => a.getAttribute("href")); if (!hrefs.length) return new Set();
  if (!waits.has(m.id)) waits.set(m.id, setTimeout(() => { waits.delete(m.id); const c = msgIndex.get(m.id); if (c) updateMessage(c, m); }, left + 50));
  return new Set(hrefs);
}

/* ---------- the cards of one message ---------- */
const embedImage = (a) => { const m = a && /url\("((?:[^"\\]|\\.)*)"\)/.exec(a.getAttribute("style") || ""); return m ? m[1].replace(/\\a /g, "\n").replace(/\\(.)/g, "$1") : null; };
/* the messages whose cards past the first three are open (their "+N more" was pressed) */
const opened = new Set();
/* partsOf(): the server's previews leave the words; each comes back under the run of words that holds its link, with the
   places kept for those still coming; what no run holds comes last */
function takeCards(root, m) {
  const found = new Map();
  for (const d of $$("div.message_embed", root)) { const a = d.querySelector(".message_embed_title a[href]") || d.querySelector("a[href]"), href = a && a.getAttribute("href"); if (href && !found.has(href)) {
      const title = (d.querySelector(".message_embed_title") || {textContent: ""}).textContent.trim(), desc = (d.querySelector(".message_embed_description") || {textContent: ""}).textContent.replace(/\s+/g, " ").trim();
      found.set(href, {kind: isVideoSite(href) ? "video" : "link", href, title, desc, img: embedImage(d.querySelector("a.message_embed_image"))}); }
    d.remove(); }
  for (const d of $$(VIDEO_DIV, root)) { const a = d.querySelector("a[href]"), href = a && a.getAttribute("href"), img = d.querySelector("img"); if (href && !found.has(href)) found.set(href, {kind: "video", href, title: (a.getAttribute("title") || "").trim(), desc: "", img: img && img.getAttribute("src")}); d.remove(); }
  /* the server's late title of a bare video link, back to the link as typed; the title goes to the card */
  for (const a of $$("a[href]", root)) { const p = found.get(a.getAttribute("href")); if (p && p.kind === "video" && SITE_PREFIX.test(a.textContent)) { if (!p.title) p.title = a.textContent.replace(SITE_PREFIX, ""); a.textContent = a.getAttribute("href"); } }
  const waiting = waitingLinks(root, m), used = new Set(), card = (p) => ({t: "pv", ...p, html: JSON.stringify(p)});
  $$("a[data-preview]", root).forEach((a) => a.removeAttribute("data-preview"));   /* read; the words are the same with the mark or without it */
  const capped = (cards) => (cards.length <= MAX_UNDER || opened.has(m.id) ? cards : [...cards.slice(0, MAX_UNDER), {t: "pvm", n: cards.length - MAX_UNDER, html: String(cards.length - MAX_UNDER)}]);
  return {
    under(run) { const out = [];
      for (const n of run) for (const a of n.matches("a[href]") ? [n] : $$("a[href]", n)) { if (a.closest("blockquote")) continue; const h = a.getAttribute("href");
        if (used.has(h)) continue; if (found.has(h)) { used.add(h); out.push(card(found.get(h))); } else if (waiting.has(h)) { used.add(h); out.push(card({kind: isVideoSite(h) ? "video" : "wait", href: h, wait: true})); } }
      return capped(out); },
    rest() { return capped([...found.values()].filter((p) => !used.has(p.href)).map(card)); },
  };
}

/* ---------- a card ---------- */
function cardHTML(p) {
  const site = esc(siteOf(p.href));
  const text = p.wait ? `<span class="pvh"><b>${site}</b></span><span class="pvc"></span>` : `<span class="pvh"><b>${esc(p.title || site)}</b></span><span class="pvc">${p.title ? site : ""}${p.title && p.desc ? " · " : ""}${esc(p.desc)}</span>`;
  const pic = p.img ? `<img alt="" loading="lazy" decoding="async" src="${esc(p.img)}">` : "";
  if (p.kind === "video") return `<span class="still">${pic}${PLAY}</span><span class="pvt">${text}</span>`;
  return `<span class="pvt">${text}</span><span class="tn">${ic("link")}${pic}</span>`;
}
const labelOf = (p) => `${p.kind === "video" ? "Video" : "Link"}: ${p.title || siteOf(p.href)}${p.title ? ", " + siteOf(p.href) : ""}`;
function cardEl(p) { const e = el("a", "pv"); setCard(e, p); return e; }
function setCard(e, p) {
  e.dataset.kind = p.kind === "wait" ? "link" : p.kind; e.toggleAttribute("data-wait", !!p.wait); e.href = p.href; e.setAttribute("aria-label", labelOf(p)); e.target = "_blank"; e.rel = "noopener noreferrer";
  e.innerHTML = cardHTML(p);
}
/* the line that opens a bubble's cards past the first three */
function moreEl(p) { const e = el("button", "pvm", `+${p.n} more`); e.setAttribute("aria-label", `Show ${p.n} more ${p.n === 1 ? "link" : "links"}`); return e; }
/* a card already on screen takes what arrived (the preview of a kept place): its box stays, its words fade in */
function fillCard(e, p) { const was = e.hasAttribute("data-wait"); setCard(e, p); e._src = p.html ?? JSON.stringify(p);
  if (was && !p.wait && !M.reduced()) for (const x of e.querySelectorAll(".pvt")) M.appear(x, 0.3); }

/* ---------- once, at boot (host.js) ---------- */
/* a card's picture fades in when it has loaded; one that fails leaves the glyph. "+N more" opens the rest of a bubble's
   cards where it stood. */
function wirePreviews() {
  /* the rest opens under the cards that stay: the view keeps its place even at the conversation's end, where the feed
     would follow the growth and lift everything above the line. By the keyboard (a click with no pointer), focus goes on
     to the first card that opened. */
  press(lane, ".pvm", (b, e, pointer) => { const id = +b.dataset.mid || b.dataset.mid, c = msgIndex.get(id), m = c && c.byId.get(id); if (!m) return; opened.add(m.id);
    const above = b.previousElementSibling, href = above && above.getAttribute("href"), byKey = !pointer && e.detail === 0;
    state.pinned = false; updateMessage(c, m); state.pinned = dist() < 4;
    if (byKey) { const kept = $$(`.pv[data-mid="${m.id}"]`, lane).find((x) => x.getAttribute("href") === href), next = kept && kept.nextElementSibling; if (next) next.focus(); } });
  lane.addEventListener("load", (e) => { if (e.target.matches && e.target.matches(".pv img")) e.target.dataset.state = "ready"; }, true);
  lane.addEventListener("error", (e) => { if (e.target.matches && e.target.matches(".pv img")) e.target.remove(); }, true);
}

export {cardEl, fetchedLinks, fillCard, moreEl, takeCards, wirePreviews, WAIT_LIMIT};
