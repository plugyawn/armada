/* INTERIM BETA (README.md): what a press on a plugin's widget opens (plugins.js draws what is there at rest). A chunk of
   its own (chunks.js), fetched when the page is idle:
   - the sheet: a widget opened from its nav row, raised beside the nav, 344 wide, its foot level with its row's foot,
     every block of its document (a list's rows 44 high with their second line, a sparkline and a figure); Esc closes it
     and gives the row back its focus;
   - the rows of a list a plugin links to a place, in the slots plugins.js keeps for it in the column, and the
     commands Jump to lists for plugins;
   - an item opened from a row, which borrows the column as an artifact does and gives it back on Close (below 1408, where
     there is no column, a raised sheet).
   The other blocks of a document (copy, metric, meter, series, log, key_value, scene) are drawn by the artifact's chunk,
   which arrives with this one. */

/* What it uses of the page is handed in when it arrives (chunks.js); plugins.js hands in the store and its seams. */
export default function pluginViewChunk({$, $$, el, esc, T, hhmm, ready, isObj, text}) {
  return function bindPluginView(P) {
    const {api, press, send, side, main, closeArtifact} = P;

    /* ---------- a sparkline: the values in a fixed 64 × 20 cell, the latest point a dot ---------- */
    /* a figure on a fixed scale (a percentage) gives its domain, so an idle machine is a flat line at the foot and not its
       noise drawn tall; a figure with none is drawn on its own range */
    const SPARK_W = 62, SPARK_H = 20, SPARK_PAD = 3;
    function sparkPoints(values, domain) {
      const vs = (Array.isArray(values) ? values : []).map(Number).filter(Number.isFinite); if (vs.length < 2) return [];
      const fixed = Array.isArray(domain) && domain.length === 2 && domain.every(Number.isFinite) && domain[1] > domain[0];
      const lo = fixed ? domain[0] : Math.min(...vs), hi = fixed ? domain[1] : Math.max(...vs), r = hi - lo || 1;
      return vs.map((v, i) => [(i * SPARK_W) / (vs.length - 1), SPARK_H - SPARK_PAD - ((Math.min(hi, Math.max(lo, v)) - lo) / r) * (SPARK_H - 2 * SPARK_PAD)]);
    }
    function sparkHTML(spark) {
      const pts = isObj(spark) ? sparkPoints(spark.values, spark.domain) : []; if (!pts.length) return "<span></span>";
      const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(""), [ex, ey] = pts[pts.length - 1];
      return `<svg class="spk" viewBox="0 0 64 20" aria-hidden="true"><path d="${d}"/><circle class="end" cx="${ex.toFixed(2)}" cy="${ey.toFixed(2)}" r="2"/></svg>`;
    }

    /* ---------- a document's blocks ---------- */
    const lead = (row) => (P.markOf(row.state) ? mk(P.markOf(row.state)) : row.glyph ? ic(P.glyphOf(row.glyph)) : "<i></i>");
    const label = (row) => (row.code ? `<code>${esc(row.label)}</code>` : esc(row.label));
    /* a list's row, where there is room for all of it: its mark, its label and second line, its sparkline, its figure */
    function rowHTML(row, metric) {
      const f = P.figureText(row.figure), attrs = `data-row="${esc(row.key)}"${row.open ? " data-open" : ""}${row.action ? " data-act" : ""}${row.open || row.action ? ' tabindex="0"' : ""}`;
      if (metric) return `<li ${attrs}><span class="t">${label(row)}</span>${sparkHTML(row.spark)}<span class="v fig">${fig(f)}</span></li>`;
      return `<li ${attrs} class="${row.sub ? "two" : ""}${P.rowOn(row) ? " on" : ""}">${lead(row)}<span class="t">${label(row)}${row.sub ? `<span class="sub">${esc(row.sub)}</span>` : ""}</span>${sparkHTML(row.spark)}<span class="v fig">${fig(f)}</span></li>`;
    }
    /* a list whose rows have no mark and no glyph is a list of figures: each word stands on the edge, its line, its figure */
    const isMetric = (rows) => rows.length > 0 && rows.every((r) => !P.markOf(r.state) && !r.glyph);
    function listHTML(doc, b, i) { const rows = P.rowsOf(doc, b), metric = isMetric(rows);
      return `<section class="sec" data-block="${i}"><div class="sh"><span>${esc(b.label)}</span>${b.caption ? `<span class="e"><span class="m">${esc(b.caption)}</span></span>` : ""}</div><ul class="wl${metric ? " met" : ""}">${rows.map((r) => rowHTML(r, metric)).join("")}</ul></section>`; }
    /* every block, in its order: lists here, the rest as an artifact draws them */
    function blocksHTML(doc) {
      const V = P.artifactView(), S = P.sceneView();
      return (doc.blocks || []).map((b, i) => (b.type === "list" ? listHTML(doc, b, i) : `<div class="apanel" data-block="${i}"><section class="ablk" data-type="${esc(b.type)}"${b.state ? ` data-state="${esc(b.state)}"` : ""}>${V.blockHTML(doc, b, i, S)}</section></div>`)).join("");
    }
    /* what an artifact's block was drawn from, kept on its element, so that a new document redraws only the blocks that changed */
    function markBlocks(root, doc) { const V = P.artifactView(), S = P.sceneView(); for (const e of $$(":scope > .apanel[data-block]", root)) { const b = doc.blocks[+e.dataset.block]; e._src = b ? V.blockHTML(doc, b, +e.dataset.block, S) : ""; } }
    /* the document's verbs: ask Onix here, call one of the plugin's tools for me, or open a page (whose host is shown first) */
    const hostOf = (u) => { try { return new URL(u).host; } catch (e) { return ""; } };
    function actionsHTML(doc) { const acts = Array.isArray(doc.actions) ? doc.actions.filter(isObj).slice(0, 4) : []; if (!acts.length) return "";
      return `<div class="wacts">${acts.map((a, i) => `<button class="lk" data-wact="${i}"${a.url ? ` data-tip="${esc(hostOf(a.url))}"` : a.prompt ? ` data-tip="${esc(a.prompt)}"` : ""}>${a.url ? ic("open") : ""}${esc(a.label)}</button>`).join("")}</div>`; }
    const staleNote = (s, declared) => (s && P.staleOf(s, declared) ? `<p class="wnote">Last heard ${esc(hhmm(s.fed_at))}. What it shows may be out of date.</p>` : "");
    function mountBlocks(root, doc) { const V = P.artifactView(), S = P.sceneView(); host(root); if (V && root.querySelector(".apanel")) V.mount(root, doc, doc.blocks.some((b) => b.type === "scene") ? S : null); markBlocks(root, doc); }

    /* ---------- an action ---------- */
    let confirming = null;
    async function runAction(target, a, at, from) {
      if (a.url) { window.open(a.url, "_blank", "noopener"); ready(); return; }
      if (a.prompt) { send({text: a.prompt, onix: true, at: performance.now()}); return; }
      if (a.confirm && !(await confirmAction(a))) { if (from && from.isConnected) from.focus({preventScroll: true}); return; }
      try { const r = await api("POST", `armada/plugins/${target.install_id}/actions`, {widget: target.widget, link_id: JSON.stringify(target.link_id || null), item: target.item || "", ...at, confirmed: JSON.stringify(!!a.confirm)});
        if (from && from.isConnected) M.say(from, r.text || "Done", 1600); }
      catch (e) { if (from && from.isConnected) M.say(from, e.message, 2400); else M.say(main, e.message, 2400); }
    }
    /* an action that cannot be undone asks first, in the design's dialog; it stands on Cancel, so Return alone never acts */
    function confirmAction(a) { return new Promise((done) => {
      const d = el("div", "dlg raised float", `<p style="font:var(--t-strong)">${esc(a.label)}?</p><p style="color:var(--ink-2);margin-top:var(--s-2)">${esc(a.confirm)}</p><div class="btns"><button class="btn" data-no>Cancel</button><button class="go" data-yes>${esc(a.label)}</button></div>`);
      d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.style.cssText = "position:absolute;left:calc(50% - 200px);top:160px"; main.append(d); host(d); M.release(d, {y: -(T["pane-rise"] || 4)}, "touch"); confirming = d;
      const close = (yes) => { confirming = null; d.inert = true; M.leave(d, () => d.remove()); removeEventListener("keydown", key, true); done(yes); };
      const key = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); } else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); close(document.activeElement === $("[data-yes]", d)); } };
      addEventListener("keydown", key, true); press(d, "[data-no]", () => close(false)); press(d, "[data-yes]", () => close(true)); $("[data-no]", d).focus(); }); }

    /* ---------- the sheet ---------- */
    let sheet = null;   /* {id, el, install_id, key, scope} */
    const navEntry = (id) => P.navWidgets().find((x) => x.id === id) || null;
    function sheetBodyHTML(x) {
      const s = x.s, doc = s && s.document;
      if (x.p.state === "failed") return `<p class="wnote">${mk("failed")} ${esc(x.p.name)} stopped. Its page has its last lines and Retry.</p>`;
      if (!doc) return `<p class="wnote">${x.p.state === "waiting" ? `${esc(x.p.name)} waits for its settings, on its page.` : `Nothing from ${esc(x.p.name)} yet.`}</p>`;
      return staleNote(s, x.w) + blocksHTML(doc) + actionsHTML(doc);
    }
    function openSheet(id, kbd) {
      const x = navEntry(id); if (!x) return; closeSheet(true);
      const rows = $$("nav .row.wg"), below = rows.length - 1 - rows.findIndex((r) => r.dataset.w === id);
      const e = el("div", "wsheet raised", `<div class="ch">${ic(P.glyphOf(x.p.glyph))}<span>${esc(x.w.label)}</span></div><div class="wb">${sheetBodyHTML(x)}</div>`);
      e.setAttribute("role", "dialog"); e.setAttribute("aria-label", x.w.label); e.tabIndex = -1; e.dataset.w = id; e.style.setProperty("--below", String(Math.max(0, below)));
      document.body.append(e); sheet = {id, el: e, install_id: x.p.id, key: x.w.key, scope: P.scopeOf(x.p.id, x.w.key, null, "")}; P.setOpenWidget(id);
      if (x.s && x.s.document) mountBlocks(e, x.s.document); else host(e);
      M.release(e, {y: T["pane-rise"] || 4}, "touch"); M.appear(e, 0.6);
      addEventListener("pointerdown", outside, true);
      if (kbd) ($(".wb button, .wb [tabindex]", e) || e).focus(); else ready();
    }
    /* a press anywhere but the sheet and the widgets' rows closes it, and the press goes on */
    const outside = (e) => { if (!sheet || (e.target.closest && e.target.closest(".wsheet, nav .row.wg, .dlg"))) return; closeSheet(true); };
    function closeSheet(quiet, back) {
      if (!sheet) return; const {el: e, id} = sheet; sheet = null; P.setOpenWidget(null); removeEventListener("pointerdown", outside, true); e.inert = true;
      if (quiet) e.remove(); else M.leave(e, () => e.remove());
      if (back) { const r = P.rowEl(id); if (r) r.focus({preventScroll: true}); }
    }
    /* a new document for the widget open in a sheet: its rows change in place when they are the same rows; else its body is
       drawn again. The sheet's foot stays level with its row's foot. */
    function updateSheet() {
      const x = navEntry(sheet.id); if (!x) { closeSheet(); return; } const body = $(".wb", sheet.el), doc = x.s && x.s.document;
      if (doc && patchDoc(body, doc)) { const note = $(":scope > .wnote", body), want = staleNote(x.s, x.w); if ((note ? note.outerHTML : "") !== want) { if (note) note.remove(); if (want) body.insertAdjacentHTML("afterbegin", want); } return; }
      body.innerHTML = sheetBodyHTML(x); if (doc) mountBlocks(body, doc);
    }
    /* a document drawn before, patched: a list row by row when its rows are the same, an artifact's block drawn again
       only when what it draws changed; false when the blocks, a list's rows or the verbs are not the same, and the body
       is drawn again */
    function patchDoc(root, doc) {
      const els = $$(":scope > [data-block]", root), blocks = doc.blocks || [];
      if (els.length !== blocks.length || blocks.some((b, i) => (b.type === "list") !== els[i].matches("section.sec"))) return false;
      const acts = $$(":scope > .wacts > [data-wact]", root).map((b) => b.textContent), want = (Array.isArray(doc.actions) ? doc.actions.filter(isObj).slice(0, 4) : []).map((a) => text(a.label));
      if (acts.join("\n") !== want.join("\n")) return false;
      const plan = blocks.map((b, i) => (b.type === "list" ? {b, el: els[i], rows: P.rowsOf(doc, b), lis: $$(":scope > .wl > li", els[i])} : {b, el: els[i]}));
      if (plan.some(({rows, lis}) => rows && (rows.length !== lis.length || rows.some((r, k) => lis[k].dataset.row !== r.key) || (lis.length && lis[0].parentElement.classList.contains("met") !== isMetric(rows))))) return false;
      const V = P.artifactView(), S = P.sceneView();
      plan.forEach(({b, el: e, rows, lis}, i) => {
        if (rows) { const cap = $(".sh .m", e); if (cap && cap.textContent !== (b.caption || "")) cap.textContent = b.caption || ""; rows.forEach((r, k) => patchRow(lis[k], r)); return; }
        const html = V.blockHTML(doc, b, i, S); if (e._src === html) return; e._src = html; const sec = e.firstElementChild; sec.innerHTML = html; if (b.state) sec.dataset.state = b.state; else delete sec.dataset.state; host(e); });
      return true;
    }
    function patchRow(li, row) {
      const metric = li.parentElement.classList.contains("met"); if (!metric) { li.classList.toggle("on", P.rowOn(row)); li.classList.toggle("two", !!row.sub);
        const m = P.markOf(row.state), first = li.firstElementChild; if (m && first.matches(".mk") && !first.classList.contains(m)) M.cross(first, mk(m)); else if ((m && !first.matches(".mk")) || (!m && first.matches(".mk"))) first.outerHTML = lead(row); }
      const t = $(":scope > .t", li), words_ = label(row) + (row.sub && !metric ? `<span class="sub">${esc(row.sub)}</span>` : ""); if (t._src !== words_) { t.innerHTML = words_; t._src = words_; }
      const sp = $(":scope > .spk, :scope > span:not(.t):not(.v)", li), next = sparkHTML(row.spark);
      if (sp && sp.matches(".spk") && next.startsWith("<svg")) { const tpl = el("template"); tpl.innerHTML = next; const neu = tpl.content.firstElementChild; $("path", sp).setAttribute("d", $("path", neu).getAttribute("d")); for (const k of ["cx", "cy"]) $("circle", sp).setAttribute(k, $("circle", neu).getAttribute(k)); }
      else if (sp) sp.outerHTML = next;
      M.figure($(":scope > .v", li), P.figureText(row.figure));
    }

    /* ---------- the column: the rows of a linked list. plugins.js draws each section and its slots from the snapshot
       until this chunk is here; then the sections are this chunk's, and a new document changes their rows in place.
       And the commands Jump to lists for plugins. ---------- */
    let openRowAt = null;   /* the item open in the column: {scope, key} */
    function setOpenRow(x) { openRowAt = x; for (const li of $$("section.sec.plug li[data-row]", P.outline)) { const on = !!x && li.closest("section").dataset.wscope === x.scope && li.dataset.row === x.key; if (on) li.setAttribute("aria-current", "true"); else li.removeAttribute("aria-current"); } }
    /* a row of the column's list: its mark (or glyph), its label, its figure; no second line and no sparkline beside the conversation */
    function colRowHTML(x, row) {
      const lead = P.markOf(row.state) ? mk(P.markOf(row.state)) : row.glyph ? ic(P.glyphOf(row.glyph)) : "<i></i>", f = P.figureText(row.figure);
      return `<li data-row="${esc(row.key)}"${row.open || row.action ? ' data-open tabindex="0"' : ""}${P.rowOn(row) ? ' class="on"' : ""}${openRowAt && openRowAt.scope === x.scope && openRowAt.key === row.key ? ' aria-current="true"' : ""}>${lead}<span class="t">${row.code ? `<code>${esc(row.label)}</code>` : esc(row.label)}</span><span class="e"><span class="d">${f ? `<span class="fig">${fig(f)}</span>` : ""}</span></span></li>`;
    }
    /* the column's sections for the place in view (column.js draws them after Changes): a section like Changes, its slots fixed
       (at most three) whatever it holds, the widget's label in Strong, one caption naming the figure */
    function sectionsHTML(place) {
      return P.columnWidgets(place).map((x) => { x.scope = P.scopeOf(x.p.id, x.w.key, x.link.id, "");
        const doc = x.s && x.s.document, block = P.listsOf(doc)[0] || null, rows = block ? P.rowsOf(doc, block) : [], slots = P.slotsOf(x);
        const stale = x.s && P.staleOf(x.s, x.w), cap = stale ? `<span class="m" data-tip="${esc(`Last heard ${hhmm(x.s.fed_at)}`)}">${mk("paused")}</span>` : block && block.caption ? `<span class="m">${esc(block.caption)}</span>` : "";
        const body = rows.length ? rows.map((row) => colRowHTML(x, row)).join("") : `<li>${ic(P.glyphOf(x.p.glyph))}<span class="t">${x.s ? "Nothing here now" : `Waiting for ${esc(x.p.name)}`}</span><span class="e"></span></li>`;
        return `<section class="sec slots plug" data-wscope="${esc(x.scope)}" style="--slots:${slots}"><div class="sh"><span>${esc(x.w.label)}</span><span class="e">${cap}</span></div><ul class="ls">${body}</ul></section>`; }).join("");
    }
    /* a document for a list in view: its rows change in place when they are the same rows; else the column is drawn again,
       which keeps every section's slots, so nothing outside the list moves */
    function patchColumn(scope) {
      for (const sec of $$("section.sec.plug[data-wscope]", P.outline)) { if (scope && sec.dataset.wscope !== scope) continue;
        const x = P.columnWidgets(P.place).find((y) => P.scopeOf(y.p.id, y.w.key, y.link.id, "") === sec.dataset.wscope); if (!x) { P.drawSide(); return; }
        const doc = x.s && x.s.document, block = P.listsOf(doc)[0] || null, rows = block ? P.rowsOf(doc, block) : [], lis = $$(":scope > .ls > li", sec);
        if (!rows.length || rows.length !== lis.length || rows.some((row, i) => lis[i].dataset.row !== row.key)) { P.drawSide(); return; }
        rows.forEach((row, i) => patchColRow(lis[i], row));
        const capEl = $(".sh > .e", sec), stale = P.staleOf(x.s, x.w), want = stale ? "paused" : (block && block.caption) || "";
        if (capEl._want !== want) { capEl._want = want; capEl.innerHTML = stale ? `<span class="m" data-tip="${esc(`Last heard ${hhmm(x.s.fed_at)}`)}">${mk("paused")}</span>` : want ? `<span class="m">${esc(want)}</span>` : ""; } }
    }
    function patchColRow(li, row) {
      li.classList.toggle("on", P.rowOn(row)); const lead = li.firstElementChild, m = P.markOf(row.state);
      if (m && lead.matches(".mk") && !lead.classList.contains(m)) M.cross(lead, mk(m));
      else if (m && !lead.matches(".mk")) lead.outerHTML = mk(m);
      const t = $(":scope > .t", li), label = row.code ? `<code>${esc(row.label)}</code>` : esc(row.label); if (t._src !== label && t.innerHTML !== label) t.innerHTML = label; t._src = label;
      const d = $(".e .d", li), f = P.figureText(row.figure), cell = $(":scope > .fig", d);
      if (cell && f) M.figure(cell, f); else if (f) d.innerHTML = `<span class="fig">${fig(f)}</span>`; else d.textContent = "";
    }
    /* Jump to: one command a widget in the nav ("Show GPUs") */
    function items() {
      const out = [];
      for (const x of P.navWidgets()) out.push({cmd: () => P.showWidget(x.id, true), label: `Show ${x.w.label}`, glyph: ic(P.glyphOf(x.p.glyph)), cap: x.p.name});
      return out;
    }
    /* a row of a linked list: an item opened in the column, or the address it names (in a tab of its own) */
    press(P.outline, "section.sec.plug li[data-row]", (li, e, ptr) => { const sec = li.closest("section"), [install_id, widget, link_id] = sec.dataset.wscope.split("/").map((v, i) => (i === 1 ? v : +v));
      const s = P.widgets.get(sec.dataset.wscope), block = s ? P.listsOf(s.document)[0] : null, row = block ? P.rowsOf(s.document, block).find((x) => x.key === li.dataset.row) : null; if (!row) { ready(); return; }
      openRow({install_id, widget, link_id, row, from: li, kbd: !ptr}); });
    P.outline.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("section.sec.plug li[tabindex]")) { e.preventDefault(); e.target.click(); } });

    /* ---------- an item opened from a row, in the column it borrows ---------- */
    let item = null;   /* {el, scope, key, back, target} */
    /* the item's document: the row's own link first, then one for the whole team, then any the plugin pushed for that item */
    function itemState(install_id, widget, link_id, key) {
      return P.widgets.get(P.scopeOf(install_id, widget, link_id, key)) || P.widgets.get(P.scopeOf(install_id, widget, null, key))
        || [...P.widgets.values()].find((s) => s.install_id === install_id && s.widget === widget && s.item === key) || null;
    }
    function itemBodyHTML(row, s, declared) {
      if (!s || !s.document) return `<div class="sum"><span>${esc(row.sub || "")}</span><span>${esc(P.figureText(row.figure))}</span></div><p class="wnote">Nothing more from the plugin about ${esc(row.label)} yet.</p>`;
      return staleNote(s, declared) + blocksHTML(s.document) + actionsHTML(s.document);
    }
    function openRow({install_id, widget, link_id, row, from, kbd}) {
      const p = P.installs.get(install_id); if (!p) return;
      const at = {row: row.key};
      if (row.open && row.open.url) { window.open(row.open.url, "_blank", "noopener"); ready(); return; }
      if (!row.open || !row.open.widget) { if (row.action) runAction({install_id, widget, link_id, item: ""}, row.action, at, from); else ready(); return; }
      const target = row.open.widget, scope = P.scopeOf(install_id, widget, link_id, ""), declared = (p.widgets || []).find((w) => w.key === target);
      if (item && item.scope === scope && item.key === row.key) { closeItem(false); return; }
      closeSheet(true); closeItem(true); closeArtifact(true);
      const s = itemState(install_id, target, link_id, row.key), inColumn = !!side && getComputedStyle(side).display !== "none";
      const e = el("div", inColumn ? "wd" : "wd sheet-chk raised float", `<div class="ch"><span>${label(row)}</span><button class="c sq" data-wclose aria-label="Close" data-tip="Close" data-key="Esc">${ic("x")}</button></div><div class="wb">${itemBodyHTML(row, s, declared)}</div>`);
      e.setAttribute("role", "region"); e.setAttribute("aria-label", row.label);
      if (inColumn) { side.append(e); side.dataset.borrowed = "plugin"; item = {folded: side.classList.contains("folded")}; side.classList.add("folded"); } else { main.append(e); item = {folded: null}; }
      Object.assign(item, {el: e, scope, key: row.key, back: from || null, target: {install_id, widget: target, link_id, item: row.key}, row, declared});
      setOpenRow({scope, key: row.key}); if (s && s.document) mountBlocks(e, s.document); else host(e);
      M.release(e, {y: T["pane-rise"] || 4}, "touch"); M.appear(e, 0.6);
      if (kbd) $("[data-wclose]", e).focus(); else ready();
    }
    function closeItem(quiet) {
      if (!item) return; const {el: e, back, folded} = item; item = null; setOpenRow(null); e.inert = true;
      const done = () => { e.remove(); if (side && !side.querySelector(":scope > .wd, :scope > .apanel")) delete side.dataset.borrowed; };
      if (folded === false) side.classList.remove("folded");
      if (quiet) done(); else { M.leave(e, done); if (back && back.isConnected && !back.closest("[inert]")) back.focus({preventScroll: true}); else ready(); }
    }
    function updateItem(scope) {
      const t = item.target; if (scope !== P.scopeOf(t.install_id, t.widget, t.link_id, t.item) && scope !== P.scopeOf(t.install_id, t.widget, null, t.item)) return;
      const s = itemState(t.install_id, t.widget, t.link_id, t.item), body = $(".wb", item.el);
      if (s && s.document && patchDoc(body, s.document)) { const note = $(":scope > .wnote", body), want = staleNote(s, item.declared); if ((note ? note.outerHTML : "") !== want) { if (note) note.remove(); if (want) body.insertAdjacentHTML("afterbegin", want); } return; }
      body.innerHTML = itemBodyHTML(item.row, s, item.declared); if (s && s.document) mountBlocks(body, s.document);
    }

    /* the presses of what this chunk draws: a row, an action, Close; Esc closes the sheet or the item */
    function actionTarget(root) {
      if (sheet && root === sheet.el) return {target: {install_id: sheet.install_id, widget: sheet.key, link_id: null, item: ""}, doc: (P.widgets.get(sheet.scope) || {}).document};
      if (item && root === item.el) { const s = itemState(item.target.install_id, item.target.widget, item.target.link_id, item.target.item); return {target: item.target, doc: s && s.document}; }
      return {target: null, doc: null};
    }
    const rootOf = (b) => b.closest(".wsheet, .wd");
    press(document.body, ".wd [data-wclose]", () => closeItem(false));
    press(document.body, ":is(.wsheet, .wd) [data-wact]", (b) => { const {target, doc} = actionTarget(rootOf(b)); const a = doc && (doc.actions || [])[+b.dataset.wact]; if (target && a) runAction(target, a, {index: JSON.stringify(+b.dataset.wact)}, b); });
    press(document.body, ":is(.wsheet, .wd) .wl > li:is([data-open], [data-act])", (li, e, ptr) => {
      const {target, doc} = actionTarget(rootOf(li)); if (!target || !doc) return; const row = doc.blocks.flatMap((b) => (b.type === "list" ? P.rowsOf(doc, b) : [])).find((r) => r.key === li.dataset.row); if (!row) return;
      openRow({install_id: target.install_id, widget: target.widget, link_id: target.link_id, row, from: sheet ? P.rowEl(sheet.id) : li, kbd: !ptr}); });
    document.addEventListener("keydown", (e) => {
      if (confirming) return;
      if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches(":is(.wsheet, .wd) .wl > li[tabindex]")) { e.preventDefault(); e.target.click(); return; }
      if (e.key !== "Escape" || (document.activeElement && document.activeElement.closest && document.activeElement.closest(".pal, .menu, .pop, .pcard, #lightbox"))) return;
      if (sheet) { e.preventDefault(); e.stopPropagation(); closeSheet(false, true); return; }
      if (item && item.el.contains(document.activeElement)) { e.preventDefault(); e.stopPropagation(); closeItem(false); } }, true);

    return {
      openSheet, closeSheet, openRow, closeItem, sparkPoints, sectionsHTML, patchColumn, items,
      /* a new document for a scope: what is open of it changes in place */
      update(scope) { if (sheet && sheet.scope === scope) updateSheet(); if (item) updateItem(scope); },
      /* an install removed: what is open of it closes */
      forget(id) { if (sheet && sheet.install_id === id) closeSheet(true); if (item && item.target.install_id === id) closeItem(true); },
      /* leaving the place: the column it borrowed is given back, and the sheet closes */
      leave() { closeSheet(true); closeItem(true); },
    };
  };
}
