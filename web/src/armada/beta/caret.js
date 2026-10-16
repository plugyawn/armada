/* INTERIM BETA (README.md): the field's caret. While it moves it is the browser's own, solid (caret-animation: manual).
   After --caret-hold at rest, a drawn caret stands over the same spot and blinks on the compositor (opacity, the kit's
   m-cur keyframes), the browser's own transparent under it: a field that waits costs no frame, where animating
   caret-color cost a style recalculation every frame. Anything that can move the caret (a key, a press, a selection, a
   change of words, of width or of typeface, the field's own scroll, the window losing focus) hands it back to the browser's.
   Where its spot cannot be read (beside a pill with no words after it, scrolled out of the field), the browser's own
   blink stands in. A field is any `.m-caret` in a `.txw`: the main field and each thread's. */

import {el, T} from "./util.js";

let resting = null, timer = 0, frame = 0, size = "";
const changed = new MutationObserver(() => wake());
const resized = new ResizeObserver(() => { if (resting && sizeOf(resting) !== size) wake(); });
const sizeOf = (f) => { const r = f.getBoundingClientRect(); return `${r.width}x${r.height}`; };
const field = () => { const f = document.activeElement; return f && f.classList && f.classList.contains("m-caret") && f.parentElement && f.parentElement.classList.contains("txw") ? f : null; };

/* where the browser draws the caret, in the viewport: at the left edge of the collapsed selection's rect, snapped down to
   the pixel as Chromium snaps it, as tall as the line's glyphs. In an empty field there is no rect: the caret stands at
   the start of the line (its indent included), as tall as the placeholder's glyphs, which are set in the same face. */
function caretRect(f) {
  const s = getSelection(); if (!s.rangeCount || !s.isCollapsed || !f.contains(s.anchorNode)) return null;
  const box = f.getBoundingClientRect(), r = [...s.getRangeAt(0).getClientRects()].pop();
  let at = r ? {x: r.left, y: r.top, h: r.height} : null;
  if (!at && !f.textContent && !f.querySelector("img, [data-seed], .pill")) {
    const glyphs = f.parentElement.querySelector(".ph > span:not(.gone)"); if (!glyphs || !glyphs.firstChild) return null;
    const q = document.createRange(); q.selectNodeContents(glyphs); const g = q.getClientRects()[0]; if (!g) return null;
    const cs = getComputedStyle(f), px = (v) => parseFloat(v) || 0, lh = px(cs.lineHeight) || g.height;
    at = {x: box.left + px(cs.borderLeftWidth) + px(cs.paddingLeft) + px(cs.textIndent), y: box.top + px(cs.borderTopWidth) + px(cs.paddingTop) + (lh - g.height) / 2, h: g.height}; }
  if (!at || !at.h || at.y < box.top - 1 || at.y + at.h > box.bottom + 1) return null;
  return {x: Math.floor(at.x), y: at.y, h: at.h};
}
/* with motion reduced the caret holds still: the browser's own, solid, is that already */
function rest(f) {
  if (field() !== f || !document.hasFocus() || M.reduced()) return;
  const at = caretRect(f), w = f.parentElement;
  if (!at) { f.dataset.rest = "native"; }
  else { let cur = w.querySelector(":scope > .m-cur"); if (!cur) { cur = el("i", "m-cur"); cur.setAttribute("aria-hidden", "true"); w.append(cur); }
    const o = w.getBoundingClientRect(); cur.style.left = at.x - o.left + "px"; cur.style.top = at.y - o.top + "px"; cur.style.height = at.h + "px"; cur.hidden = false; f.dataset.rest = "drawn"; }
  resting = f; size = sizeOf(f);
  changed.observe(w, {childList: true, characterData: true, subtree: true}); resized.observe(f);
}
/* the caret is the browser's again, and rests anew after --caret-hold if a field still holds it */
function wake() {
  clearTimeout(timer); cancelAnimationFrame(frame);
  if (resting) { delete resting.dataset.rest; const cur = resting.parentElement && resting.parentElement.querySelector(":scope > .m-cur"); if (cur) cur.hidden = true; changed.disconnect(); resized.unobserve(resting); resting = null; }
  const f = field(); if (f) timer = setTimeout(() => { frame = requestAnimationFrame(() => rest(f)); }, T["caret-hold"]);
}
function wireCaret() {
  for (const t of ["selectionchange", "focusin", "focusout", "input"]) document.addEventListener(t, wake, true);
  document.addEventListener("scroll", (e) => { if (resting && e.target === resting) wake(); }, {capture: true, passive: true});
  addEventListener("focus", wake); addEventListener("blur", wake); addEventListener("resize", wake);
  if (document.fonts) document.fonts.addEventListener("loadingdone", wake);
  wake();
}

export {caretRect, wireCaret};
