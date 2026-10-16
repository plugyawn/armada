/* INTERIM BETA (README.md): what a link becomes under the words that hold it (statement 41). The design draws one shape
   for it, a card on the block's fill, 368 wide ("the one thing narrower than the lane by rule is a link's preview, 368,
   because it belongs to the bubble above it"), and it stands under the bubble (or Onix's paragraph) whose link it is:
   - a page: the server's div.message_embed, which its embed worker writes into the message seconds after it was sent.
     A card of 56: the title, the site and the description, and a 40 px picture that loads lazily inside its box (the link
     glyph without one). The whole card opens the link.
   - a video (YouTube's, Vimeo's and any oEmbed video preview): its still at 16:9 with a play mark, then the title and
     the site; it opens the video. No player in the lane. The bubble keeps the link as it was typed: the server rewrites a
     bare video link's words to "YouTube - <title>" when its title arrives, and that title is the card's.
   A bubble shows at most three cards; past that, a quiet "+N more" line under them opens the rest in place, below it, so
   nothing above it moves. */

import {$$, el, esc, press} from "./util.js";
import {msgIndex, state} from "./state.js";
import {lane} from "./frame.js";
import {dist} from "./feed.js";
import {updateMessage} from "./agent_turn.js";

const MAX_UNDER = 3;                           /* the cards a bubble shows before "+N more" */

const VIDEO_DIV = "div.youtube-video, div.vimeo-video, div.embed-video";
const SITE_PREFIX = /^(YouTube|Vimeo) - /;
const PLAY = '<svg class="play" viewBox="0 0 16 16" aria-hidden="true"><path d="M5.5 3.5v9l7-4.5z"/></svg>';
const urlOf = (href) => { try { return new URL(href, location.href); } catch (e) { return null; } };
const siteOf = (href) => { const u = urlOf(href); return u ? u.hostname.replace(/^www\./, "") : ""; };
const youtubeId = (u) => { const h = u.hostname; if (/^(m\.|www\.)?youtube(-nocookie)?\.com$/.test(h)) return u.searchParams.get("v") || /^\/(embed|shorts|v)\/([\w-]+)/.exec(u.pathname)?.[2] || null; return h === "youtu.be" ? u.pathname.slice(1) || null : null; };
const isVideoSite = (href) => { const u = urlOf(href); return !!u && (!!youtubeId(u) || /^(www\.)?vimeo\.com$/.test(u.hostname)); };

/* ---------- the cards of one message ---------- */
const embedImage = (a) => { const m = a && /url\("((?:[^"\\]|\\.)*)"\)/.exec(a.getAttribute("style") || ""); return m ? m[1].replace(/\\a /g, "\n").replace(/\\(.)/g, "$1") : null; };
/* the messages whose cards past the first three are open (their "+N more" was pressed) */
const opened = new Set();
/* partsOf(): the server's previews leave the words; each comes back under the run of words that holds its link; what no
   run holds comes last */
function takeCards(root, m) {
  const found = new Map();
  for (const d of $$("div.message_embed", root)) { const a = d.querySelector(".message_embed_title a[href]") || d.querySelector("a[href]"), href = a && a.getAttribute("href"); if (href && !found.has(href)) {
      const title = (d.querySelector(".message_embed_title") || {textContent: ""}).textContent.trim(), desc = (d.querySelector(".message_embed_description") || {textContent: ""}).textContent.replace(/\s+/g, " ").trim();
      found.set(href, {kind: isVideoSite(href) ? "video" : "link", href, title, desc, img: embedImage(d.querySelector("a.message_embed_image"))}); }
    d.remove(); }
  for (const d of $$(VIDEO_DIV, root)) { const a = d.querySelector("a[href]"), href = a && a.getAttribute("href"), img = d.querySelector("img"); if (href && !found.has(href)) found.set(href, {kind: "video", href, title: (a.getAttribute("title") || "").trim(), desc: "", img: img && img.getAttribute("src")}); d.remove(); }
  /* the server's late title of a bare video link, back to the link as typed; the title goes to the card */
  for (const a of $$("a[href]", root)) { const p = found.get(a.getAttribute("href")); if (p && p.kind === "video" && SITE_PREFIX.test(a.textContent)) { if (!p.title) p.title = a.textContent.replace(SITE_PREFIX, ""); a.textContent = a.getAttribute("href"); } }
  const used = new Set(), card = (p) => ({t: "pv", ...p, html: JSON.stringify(p)});
  const capped = (cards) => (cards.length <= MAX_UNDER || opened.has(m.id) ? cards : [...cards.slice(0, MAX_UNDER), {t: "pvm", n: cards.length - MAX_UNDER, html: String(cards.length - MAX_UNDER)}]);
  return {
    under(run) { const out = [];
      for (const n of run) for (const a of n.matches("a[href]") ? [n] : $$("a[href]", n)) { if (a.closest("blockquote")) continue; const h = a.getAttribute("href");
        if (!used.has(h) && found.has(h)) { used.add(h); out.push(card(found.get(h))); } }
      return capped(out); },
    rest() { return capped([...found.values()].filter((p) => !used.has(p.href)).map(card)); },
  };
}

/* ---------- a card ---------- */
function cardHTML(p) {
  const site = esc(siteOf(p.href));
  const text = `<span class="pvh"><b>${esc(p.title || site)}</b></span><span class="pvc">${p.title ? site : ""}${p.title && p.desc ? " · " : ""}${esc(p.desc)}</span>`;
  const pic = p.img ? `<img alt="" loading="lazy" decoding="async" src="${esc(p.img)}">` : "";
  if (p.kind === "video") return `<span class="still">${pic}${PLAY}</span><span class="pvt">${text}</span>`;
  return `<span class="pvt">${text}</span><span class="tn">${ic("link")}${pic}</span>`;
}
const labelOf = (p) => `${p.kind === "video" ? "Video" : "Link"}: ${p.title || siteOf(p.href)}${p.title ? ", " + siteOf(p.href) : ""}`;
function cardEl(p) { const e = el("a", "pv"); setCard(e, p); return e; }
function setCard(e, p) {
  e.dataset.kind = p.kind; e.href = p.href; e.setAttribute("aria-label", labelOf(p)); e.target = "_blank"; e.rel = "noopener noreferrer";
  e.innerHTML = cardHTML(p);
}
/* the line that opens a bubble's cards past the first three */
function moreEl(p) { const e = el("button", "pvm", `+${p.n} more`); e.setAttribute("aria-label", `Show ${p.n} more ${p.n === 1 ? "link" : "links"}`); return e; }
/* a card already on screen takes what arrived; its box stays */
function fillCard(e, p) { setCard(e, p); e._src = p.html ?? JSON.stringify(p); }

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

export {cardEl, fillCard, moreEl, takeCards, wirePreviews};
