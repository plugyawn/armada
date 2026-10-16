/* INTERIM BETA (README.md): pictures in the conversation. A picture the server rendered is read into a box whose shape is
   known before a byte of it arrives (the server writes every upload's size, data-original-dimensions, on the placeholder and
   on the thumbnail alike), so nothing moves when it loads. It loads when it comes within a screen of being seen, and fades
   in inside its box. My own pictures show at once, from this browser, until the server's thumbnail takes their place. */

import {$$, clamp, el, esc} from "./util.js";
import {feed} from "./frame.js";

/* a picture keeps its own shape, clamped: a very tall one (a full-page screenshot) shows its top in a box of 1:3, a very wide
   one its start in a box of 8:1. One picture is at most 320 high (the plan's blocks) and never larger than it is. */
const TALL = 1 / 3, WIDE = 8;
const UPLOAD = /^\/user_uploads\/(?!thumbnail\/)/;
const VIDEO = ".youtube-video, .vimeo-video, .embed-video";
const baseName = (href) => { const last = String(href || "").split(/[?#]/)[0].split("/").pop() || ""; try { return decodeURIComponent(last); } catch (e) { return last; } };
const loose = (s) => String(s || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/* the box a picture of w by h stands in: its shape on screen, and which end is cut when the shape is clamped */
function picBox(w, h) { if (!(w > 0 && h > 0)) return null; const ar = w / h; return {ratio: Math.round(clamp(ar, TALL, WIDE) * 1e4) / 1e4, crop: ar < TALL ? "tall" : ar > WIDE ? "wide" : ""}; }

/* what the server's markup says of one picture: where it opens (href), what loads in the conversation (src, the thumbnail;
   null while the server is still making it), the file the lightbox shows (full: the original, or the server's transcoded copy
   of a format browsers cannot show, HEIC or TIFF), its size and its name. A video's preview is a picture that opens its link. */
function picOf(img, a) {
  const href = (a && a.getAttribute("href")) || img.dataset.originalSrc || img.getAttribute("src") || "";
  const dm = /^(\d+)x(\d+)$/.exec(img.dataset.originalDimensions || ""), pending = img.classList.contains("image-loading-placeholder");
  const src = pending ? null : img.getAttribute("src"), animated = img.dataset.animated === "true";
  const tc = img.dataset.transcodedImage, thumbDir = src && src.startsWith("/user_uploads/thumbnail/") ? src.replace(/\/[^/]+$/, "/") : UPLOAD.test(href) ? href.replace(/^\/user_uploads\//, "/user_uploads/thumbnail/") + "/" : "";
  return {href, src: animated && src ? src.replace(/-anim(\.\w+)$/, "$1") : src, anim: animated ? src : null, full: tc && thumbDir ? thumbDir + tc : href,
    w: dm ? +dm[1] : 0, h: dm ? +dm[2] : 0, name: (a && a.getAttribute("title")) || img.getAttribute("alt") || baseName(href), link: !!(a && a.closest(VIDEO))};
}
/* every picture of one preview (the server writes one image in each; a video's preview too) */
const picsOf = (div) => $$("img", div).map((img) => picOf(img, img.closest("a")));

/* the HTML of a group of pictures: each a link to the original (a press opens the lightbox; a modified press, the file itself)
   holding an image with no source yet: loadPics() gives it one when it comes near */
function picHTML(items) {
  return items.map((p) => { const b = picBox(p.w, p.h), path = UPLOAD.test(p.href) ? p.href : "";
    const attrs = [`class="im"`, `href="${esc(p.href)}"`, `target="_blank"`, `rel="noopener noreferrer"`, `data-state="wait"`, `aria-label="${esc(p.name || "Image")}"`,
      b ? `style="--ar:${b.ratio};--w:${p.w}px"` : `data-size="unknown"`, b && b.crop ? `data-crop="${b.crop}"` : "", path ? `data-path="${esc(path)}"` : "", p.link ? `data-kind="link"` : `data-full="${esc(p.full)}"`].filter(Boolean).join(" ");
    return `<a ${attrs}><img alt="" decoding="async"${p.src ? ` data-src="${esc(p.src)}"` : ""}${p.anim ? ` data-anim="${esc(p.anim)}"` : ""}></a>`; }).join("");
}

/* ---------- the server's markup, made ready to be cut into parts (parts.js) ---------- */
/* a Markdown image (![alt](...)) stands among its paragraph's words; it is lifted out to stand after the paragraph as a preview,
   as a linked image does, so that a picture is always a block beside the words, never inside them */
function liftInline(root) {
  for (const top of [...root.children]) { const imgs = $$("img.inline-image", top); if (!imgs.length) continue; let at = top;
    for (const img of imgs) { const d = el("div", "message_inline_image"), a = document.createElement("a"); a.setAttribute("href", img.dataset.originalSrc || img.getAttribute("src") || ""); if (img.alt) a.title = img.alt; img.classList.remove("inline-image"); a.append(img); d.append(a); at.after(d); at = d; }
    trimBreaks(top); if (!top.textContent.trim() && !top.querySelector("img, .em")) top.remove(); }
}
/* the composer and Onix attach a file as a link that names it ([shot.png](/user_uploads/...)); when the picture is shown below,
   that name is the picture's label (and the lightbox's), not words of the message: the link leaves the bubble */
function dropNames(root) {
  const shown = new Map($$("div.message_inline_image > a[href]", root).map((a) => [a.getAttribute("href"), a]));
  for (const a of $$("a[href]", root)) { if (a.closest("div.message_inline_image")) continue; const h = a.getAttribute("href"), pic = shown.get(h), text = a.textContent.trim();
    if (!pic || !text || loose(text) !== loose(baseName(h))) continue;
    if (!pic.getAttribute("title")) pic.title = text;
    const p = a.parentElement; a.remove(); if (!p) continue; trimBreaks(p); if (p.parentNode === root && !p.textContent.trim() && !p.querySelector("img, .em, .mention")) p.remove(); }
}
/* line breaks left at a paragraph's ends, or doubled, by what was taken out of it */
function trimBreaks(p) {
  const blank = (n) => n && ((n.nodeType === 3 && !n.textContent.trim()) || n.nodeName === "BR");
  while (blank(p.firstChild)) p.firstChild.remove(); while (blank(p.lastChild)) p.lastChild.remove();
  for (const br of $$(":scope > br", p)) { let n = br.nextSibling; while (n && n.nodeType === 3 && !n.textContent.trim()) n = n.nextSibling; if (n && n.nodeName === "BR") br.remove(); }
}

/* ---------- loading ---------- */
/* an upload's address -> a source of it this page has shown: the next rendering of that picture (my echo, then the server's
   placeholder, then its thumbnail; a topic drawn again) starts from what is already on screen and moves to the new source
   only once that has decoded, so a picture never flashes empty */
const seen = new Map();
/* the size of a picture I attached, read in this browser: my echo reserves the box the server's copy will have */
const local = new Map();
let nearFeed = null, nearView = null, waiting = [];
/* a box that comes within a screen of the conversation's visible part loads; the conversation is its own scroller, so the
   observer's root is that scroller (the viewport's margin would stop at the scroller's edge). Pictures elsewhere (a page, the
   column) use the viewport. Without an observer (old browsers, tests) every picture loads at once. */
function observerFor(a) {
  if (typeof IntersectionObserver !== "function") return null;
  const seenNow = (es, io) => es.forEach((e) => { if (e.isIntersecting) { io.unobserve(e.target); show(e.target); } });
  if (feed && feed.contains(a)) return nearFeed || (nearFeed = new IntersectionObserver(seenNow, {root: feed, rootMargin: "100% 0px"}));
  return nearView || (nearView = new IntersectionObserver(seenNow, {rootMargin: "50% 0px"}));
}
/* called with whatever was just drawn: its boxes are watched once they are in the page (the caller attaches them in the same
   task), so each finds the scroller it is in. A picture this page has already shown (my echo's, one drawn again) is shown
   before the next paint: it costs no fetch, and waiting for the observer would leave its box empty for a frame. */
function loadPics(root) {
  if (!root) return; const fresh = $$("a.im[data-state='wait']", root).filter((a) => !a._watched); if (!fresh.length) return;
  fresh.forEach((a) => (a._watched = true));
  if (!waiting.length) queueMicrotask(() => { const list = waiting; waiting = []; for (const a of list) { const io = !seen.has(a.dataset.path) && observerFor(a); if (io) io.observe(a); else show(a); } });
  waiting.push(...fresh); wireAnim();
}
function show(a) {
  if (a.dataset.state !== "wait") return; const img = a.firstElementChild, want = img && img.dataset.src, path = a.dataset.path, had = path && seen.get(path);
  if (!img || (!want && !had)) return;   /* the server is still making its thumbnail: the box waits on its fill, and the update that brings it draws the part again */
  a.dataset.state = "load";
  img.addEventListener("load", () => { a.dataset.state = "shown"; if (path && img.src && !img.src.startsWith("blob:")) seen.set(path, img.src); });
  img.addEventListener("error", () => { if (want && img.getAttribute("src") !== want) { img.src = want; return; } failed(a); });
  if (had && want && had !== want) { img.src = had; upgrade(img, want, path); } else img.src = want || had;
  if (img.complete && img.naturalWidth) a.dataset.state = "shown";   /* in the cache: whole in this frame, with no fade */
}
/* the newer source is fetched and decoded beside the picture on screen, then put in its place */
function upgrade(img, want, path) { const pre = new Image(); pre.src = want; const done = () => { if (img.isConnected && img.dataset.src === want) img.src = want; seen.set(path, want); };
  (pre.decode ? pre.decode() : new Promise((ok, no) => { pre.onload = ok; pre.onerror = no; })).then(done, () => {}); }
function failed(a) { a.dataset.state = "failed"; a.removeAttribute("data-full"); if (!a.querySelector(".nf")) a.append(el("span", "nf", `${ic("image")}<span>Image not available</span>`)); a.setAttribute("aria-label", `${a.getAttribute("aria-label") || "Image"} (not available)`); }
/* an animated picture shows its still frame; it plays while the pointer is on it or it has focus, then stops */
let animWired = false;
function wireAnim() { if (animWired) return; animWired = true;
  const play = (e, on) => { const a = e.target.closest && e.target.closest("a.im[data-state='shown']"); if (!a) return; const img = a.firstElementChild, anim = img && img.dataset.anim; if (!anim) return; img.src = on ? anim : img.dataset.src; };
  document.addEventListener("pointerover", (e) => play(e, true)); document.addEventListener("pointerout", (e) => { if (!e.relatedTarget || !e.target.closest("a.im") || !e.target.closest("a.im").contains(e.relatedTarget)) play(e, false); });
  document.addEventListener("focusin", (e) => play(e, true)); document.addEventListener("focusout", (e) => play(e, false)); }

/* ---------- my own pictures ---------- */
/* a picture I attach is read here once: its size, and a small copy (at most the server's 840 by 560) shown in my chip, in my
   echo and in my sent message until the server's thumbnail has decoded */
async function localPicture(file) {
  if (!/^image\//.test(file.type || "") || typeof createImageBitmap !== "function") return null;
  let bmp; try { bmp = await createImageBitmap(file); } catch (e) { return null; }
  const w = bmp.width, h = bmp.height; let preview;
  try { const s = Math.min(1, 840 / w, 560 / h), c = new OffscreenCanvas(Math.max(1, Math.round(w * s)), Math.max(1, Math.round(h * s))); c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height); preview = URL.createObjectURL(await c.convertToBlob({type: "image/webp", quality: 0.9})); }
  catch (e) { preview = URL.createObjectURL(file); }
  if (bmp.close) bmp.close(); return {w, h, preview};
}
/* the upload's address is known: my echo and the server's copy of it start from my picture */
function rememberLocal(url, pic) { if (!url || !pic) return; local.set(url, {w: pic.w, h: pic.h}); if (pic.preview && !seen.has(url)) seen.set(url, pic.preview); }
const localSize = (url) => local.get(url) || null;

export {dropNames, liftInline, loadPics, localPicture, localSize, picBox, picHTML, picOf, picsOf, rememberLocal, seen};
