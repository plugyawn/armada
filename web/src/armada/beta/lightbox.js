/* INTERIM BETA (README.md): a picture opened whole. Pressing a picture in the conversation (or a post) opens it on a quiet
   surface over the window, whole in the frame of the press: the picture the conversation already shows, at the size it will
   have, travels there from its box on `move`, and the original replaces it once decoded. Left and Right step through the
   pictures of the conversation as drawn; Esc, the close control or a press beside the picture closes it, and focus goes
   back to where it was. A picture larger than the window opens to fit; a press on it (or Actual size) shows it a pixel for a
   pixel, scrolled in place. A full-page screenshot opens at the window's width, from its top.
   It is a chunk of its own (chunks.js), fetched when the page is idle; media.js wires the press and calls it. */

/* What it uses of the page is handed in when it arrives (chunks.js): a chunk imports nothing the first load holds. */
export default function lightboxChunk({$, $$, clamp, el, T, state, dayLabel, hhmm, nameOf, ready}) {

  let box = null;   /* the open lightbox: {el, list, i, back, zoom} */
  const lightboxOpen = () => !!box;
  const BAR = 52;

  /* every picture that can be opened in the place the pressed one stands: the conversation (its threads too), or a post */
  function picturesNear(a) { const scope = a.closest("#col, .doc, #page") || document.body; return $$("a.im[data-full]", scope).filter((x) => x.dataset.state !== "failed"); }
  function itemOf(a) {
    const img = a.querySelector("img"), dim = /^(\d+)x(\d+)$/.exec(a.dataset.dim || ""), n = a.closest("[data-mid]"), id = n && n.dataset.mid, m = id && state.P && (state.P.byId.get(+id) || state.P.byId.get(id));
    return {a, name: a.getAttribute("aria-label") || "Image", href: a.getAttribute("href"), full: a.dataset.full, shown: img && img.currentSrc ? img.currentSrc : img && img.getAttribute("src"),
      anim: img && img.dataset.anim, w: dim ? +dim[1] : img && img.naturalWidth, h: dim ? +dim[2] : img && img.naturalHeight, who: m ? nameOf(m.sender_id) : "", at: m ? m.timestamp : 0};
  }

  /* the size a picture opens at, in CSS px: to fit the stage; a very tall one at the stage's width (it scrolls); never larger
     than a pixel of it for a device pixel. `actual` is that last size. */
  function fitOf(it, W, H) {
    const dpr = window.devicePixelRatio || 1, w = it.w || W, h = it.h || H, aw = w / dpr, ah = h / dpr;
    const tall = h / w > 2 * (H / W), s = tall ? Math.min(1, W / aw) : Math.min(1, W / aw, H / ah);
    return {w: Math.round(aw * s), h: Math.round(ah * s), tall, actual: {w: Math.round(aw), h: Math.round(ah)}, zooms: s < 1 && !tall};
  }

  function openLightbox(a) {
    const list = picturesNear(a), i = Math.max(0, list.indexOf(a)); if (!list.length) return;
    if (box) closeLightbox(true);
    const e = el("div", "lbx", `<div class="lbx-stage"><img class="lbx-img" alt="" decoding="async"></div>
      <button class="c sq lbx-prev" data-lbx="prev" aria-label="Previous picture" data-tip="Previous" data-key="←">${ic("right")}</button><button class="c sq lbx-next" data-lbx="next" aria-label="Next picture" data-tip="Next" data-key="→">${ic("right")}</button>
      <div class="lbx-bar"><span class="lbx-t"><b></b><span class="k"></span></span><span class="lbx-n"></span><button class="lk lbx-zoom" data-lbx="zoom"></button><a class="lk lbx-orig" target="_blank" rel="noopener noreferrer">Open original</a><button class="c sq" data-lbx="close" aria-label="Close" data-tip="Close" data-key="Esc">${ic("x")}</button></div>`);
    e.setAttribute("role", "dialog"); e.setAttribute("aria-modal", "true"); e.tabIndex = -1; e.dataset.noring = "1";
    box = {el: e, list, i, back: document.activeElement, zoom: "fit"};
    document.body.append(e); host(e);
    e.addEventListener("click", onClick); e.addEventListener("pointerdown", (ev) => { if (ev.target === e || ev.target.classList.contains("lbx-stage")) { ev.preventDefault(); closeLightbox(); } });
    addEventListener("keydown", onKey, true);
    draw(null, a);
    e.focus({preventScroll: true});
  }
  /* draw the picture at box.i: the source the page already shows at once, then the full one once decoded. `from` is the box
     it travels from (on open), `dir` the side a step comes from. */
  function draw(dir, from) {
    const e = box.el, it = itemOf(box.list[box.i]), stage = $(".lbx-stage", e), img = $(".lbx-img", e), W = innerWidth - 2 * 40, H = innerHeight - BAR - 24, f = fitOf(it, W, H), actual = box.zoom === "actual" && f.zooms;
    const size = actual ? f.actual : f; e.dataset.zoom = actual ? "actual" : "fit"; stage.dataset.fit = f.tall ? "width" : actual ? "actual" : "contain";
    img.alt = it.name; img.style.width = size.w + "px"; img.style.height = size.h + "px"; img.src = it.anim || it.shown || it.full; img.dataset.want = it.full;
    if (it.full && it.full !== img.getAttribute("src")) { const pre = new Image(); pre.src = it.full; (pre.decode ? pre.decode() : Promise.reject()).then(() => { if (box && box.el === e && img.dataset.want === it.full) img.src = it.full; }, () => {}); }
    $(".lbx-t b", e).textContent = it.name; $(".lbx-t .k", e).textContent = it.who ? `${it.who} · ${dayLabel(it.at)} ${hhmm(it.at)}` : "";
    const n = box.list.length; $(".lbx-n", e).textContent = n > 1 ? `${box.i + 1} of ${n}` : "";
    $(".lbx-prev", e).hidden = $(".lbx-next", e).hidden = n < 2; $(".lbx-prev", e).disabled = box.i === 0; $(".lbx-next", e).disabled = box.i === n - 1;
    const z = $(".lbx-zoom", e); z.hidden = !f.zooms; z.textContent = actual ? "Fit to window" : "Actual size";
    $(".lbx-orig", e).href = it.href;
    stage.scrollTop = 0; stage.scrollLeft = actual ? Math.max(0, (size.w - stage.clientWidth) / 2) : 0;
    if (from) { const r = from.getBoundingClientRect(); if (r.width) M.flip(img, r, "move", {size: true}); }
    else if (dir) M.release(img, {x: dir * (T.rise || 8)}, "touch");
  }
  function step(d) { if (!box) return; const j = clamp(box.i + d, 0, box.list.length - 1); if (j === box.i) { M.refuse(box.el.querySelector(".lbx-img")); return; } box.i = j; draw(d); }
  function zoom() { if (!box) return; box.zoom = box.zoom === "actual" ? "fit" : "actual"; draw(null); }
  function onClick(ev) { const t = ev.target.closest("[data-lbx], .lbx-img"); if (!t) return; if (t.classList.contains("lbx-img")) { zoom(); return; }
    const k = t.dataset.lbx; if (k === "close") closeLightbox(); else if (k === "prev") step(-1); else if (k === "next") step(1); else if (k === "zoom") zoom(); }
  /* while it is open the lightbox has every key: Esc, the arrows, Tab round its controls; Return and Space press the control
     that has focus; nothing reaches the field */
  function onKey(ev) {
    if (!box) return; ev.stopPropagation(); const mod = ev.metaKey || ev.ctrlKey || ev.altKey;
    if (ev.key === "Escape") { ev.preventDefault(); closeLightbox(); return; }
    if (!mod && ev.key === "ArrowLeft") { ev.preventDefault(); step(-1); return; }
    if (!mod && ev.key === "ArrowRight") { ev.preventDefault(); step(1); return; }
    if (ev.key === "Tab") { ev.preventDefault(); const stops = $$("button, a[href]", box.el).filter((x) => !x.hidden && !x.disabled), k = stops.indexOf(document.activeElement); const n = stops[(k + (ev.shiftKey ? -1 : 1) + stops.length + (k < 0 && ev.shiftKey ? 1 : 0)) % stops.length]; if (n) n.focus(); return; }
    if ((ev.key === "Enter" || ev.key === " ") && document.activeElement && box.el.contains(document.activeElement) && document.activeElement.matches("button, a[href]")) return;
    if (ev.key !== "Shift" && !mod) ev.preventDefault();
  }
  /* it leaves on the fade; focus goes back to what had it, or, if that has gone, to the same picture drawn again, or the field */
  function closeLightbox(quiet) {
    if (!box) return; const {el: e, back, list, i} = box; box = null; removeEventListener("keydown", onKey, true); e.inert = true;
    if (quiet) e.remove(); else M.leave(e, () => e.remove());
    const href = list[i] && list[i].getAttribute("href"), again = back && !back.isConnected && back.matches && back.matches("a.im") ? $$(`a.im`).find((x) => x.getAttribute("href") === href) : null;
    const to = back && back.isConnected && back !== document.body && !back.isContentEditable ? back : again;
    if (to) to.focus({preventScroll: true}); else ready();
  }

  return {closeLightbox, fitOf, lightboxOpen, openLightbox};
}
