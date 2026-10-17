/* INTERIM BETA (README.md): what a press on a plugin's widget opens (plugins.js draws what is there at rest). A chunk of
   its own (chunks.js), fetched when the page is idle:
   - the sheet: a widget opened from its nav row, raised beside the nav, 344 wide, its foot level with its row's foot,
     every block of its document (a list's rows 44 high with their second line, a sparkline and a figure); Esc closes it
     and gives the row back its focus;
   - the "+" menu, from the group's last row, its foot level with that row's: what I hid, with Show; what the team
     offers, with Add; All plugins;
   - Edit, from the sheet or a row's own menu (with Hide for me): my options as controls, the widget drawn with them,
     and Onix's field, which asks Onix in the plugin's topic of the plugins channel until PL12 gives it its tools;
   - the rows of a list a plugin links to a place, in the slots plugins.js keeps for it in the column, and the
     commands Jump to lists for plugins;
   - an item opened from a row, which borrows the column as an artifact does and gives it back on Close (below 1408, where
     there is no column, a raised sheet);
   - the Plugins place (a store, like Home) and a plugin's page: what it adds and may do, its settings drawn from its
     manifest's schema (secrets written, never read back), its links, where its widgets show, its tools for Onix, Recent
     and Remove.
   The other blocks of a document (copy, metric, meter, series, log, key_value, scene) are drawn by the artifact's chunk,
   which arrives with this one. */

/* What it uses of the page is handed in when it arrives (chunks.js); plugins.js hands in the store and its seams. */
export default function pluginViewChunk({$, $$, el, esc, T, state, hhmm, nameOf, ready, isObj, text}) {
  return function bindPluginView(P) {
    const {api, press, send, openPlace, placeFromKey, side, main, streams, drawSide, drawHeader, closeArtifact, faceOf} = P;

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
    let sheet = null;   /* what floats beside the nav, from its row: {kind: "widget" | "menu", id, el}; a widget's also {install_id, key, scope} */
    /* a widget as I see it, whether or not it is in my nav now */
    const navEntry = (id) => { const x = P.teamWidgets().find((y) => y.id === id); return x ? P.seen(x, null) : null; };
    /* a raised surface beside the nav, its foot level with its row's foot (CSS, from the rows below it), settling as a
       surface that holds words does; a press elsewhere closes it, and Esc gives its row the focus back */
    function float(kind, id, cls, html, label, role = "dialog") {
      closeSheet(true); const rows = $$("nav .row.wg"), below = rows.length - 1 - rows.findIndex((r) => r.dataset.w === id);
      const e = el("div", `wsheet raised ${cls}`.trim(), html); e.setAttribute("role", role); e.setAttribute("aria-label", label); e.tabIndex = -1; e.dataset.w = id; e.style.setProperty("--below", String(Math.max(0, below)));
      document.body.append(e); sheet = {kind, id, el: e}; P.setOpenWidget(id);
      M.release(e, {y: T["pane-rise"] || 4}, "touch"); M.appear(e, 0.6); addEventListener("pointerdown", outside, true);
      return e;
    }
    function sheetBodyHTML(x) {
      const s = x.s, doc = s && s.document;
      if (x.p.state === "failed") return `<p class="wnote">${mk("failed")} ${esc(x.p.name)} stopped. Its page has its last lines and Retry.</p>`;
      if (!doc) return `<p class="wnote">${x.p.state === "waiting" ? `${esc(x.p.name)} waits for its settings, on its page.` : `Nothing from ${esc(x.p.name)} yet.`}</p>`;
      return staleNote(s, x.w) + blocksHTML(doc) + actionsHTML(doc);
    }
    const viewHTML = (x) => `<div class="ch">${ic(P.glyphOf(x.p.glyph))}<span>${esc(x.label)}</span><button class="lk" data-wedit>Edit</button><button class="lk" data-wopen>Open</button></div><div class="wb">${sheetBodyHTML(x)}</div>`;
    function openSheet(id, kbd) {
      const x = navEntry(id); if (!x) return;
      const e = float("widget", id, "", viewHTML(x), x.label);
      Object.assign(sheet, {install_id: x.p.id, key: x.w.key, scope: P.scopeOf(x.p.id, x.w.key, null, "")});
      if (x.s && x.s.document) mountBlocks(e, x.s.document); else host(e);
      if (kbd) ($("[data-wopen], .wb button, .wb [tabindex]", e) || e).focus(); else ready();
    }

    /* ---------- Edit (ROUND2.md, section D): what is mine as controls, the widget drawn with them, and Onix's field ---------- */
    const pickHTML = (attr, choices, current, label) => `<span class="pk-w"><select class="pick" ${attr} aria-label="${esc(label)}">${choices.map(([v, t]) => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>${ic("down")}</span>`;
    const switchHTML = (field, on, label) => `<button class="switch${on ? " on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(label)}" data-pref="${field}"></button>`;
    /* the values a facet takes in the team's document, in the order its rows give them */
    function facetValues(doc, key) { const out = []; for (const b of (doc && doc.blocks) || []) if (isObj(b) && b.type === "list") for (const r of P.rowsOf(doc, b)) { const v = isObj(r.facets) ? r.facets[key] : undefined; if (v !== undefined && !out.includes(v)) out.push(v); } return out; }
    function controlsHTML(x) {
      const w = x.w, o = isObj(w.options) ? w.options : {}, pref = x.pref || {}, raw = (P.widgets.get(P.scopeOf(x.p.id, w.key, null, "")) || {}).document, row = (label, ctl) => `<div><span class="l">${label}</span>${ctl}</div>`, out = [];
      const figures = Array.isArray(o.figures) ? o.figures : [];
      if (figures.length) { const own = raw && isObj(raw.rest) ? figures.find((f) => f.figure === raw.rest.figure) : null;
        out.push(row("At rest", pickHTML('data-pref="rest_figure"', [["", own ? own.title : "Its own"], ...figures.filter((f) => f !== own).map((f) => [f.key, f.title])], own && pref.rest_figure === own.key ? "" : pref.rest_figure || "", "At rest"))); }
      for (const f of Array.isArray(o.facets) ? o.facets : []) { const cur = isObj(pref.filters) && f.key in pref.filters ? JSON.stringify(pref.filters[f.key]) : "";
        out.push(row(esc(f.title), pickHTML(`data-facet="${esc(f.key)}"`, [["", "All"], ...facetValues(raw, f.key).map((v) => [JSON.stringify(v), `${v} only`])], cur, f.title))); }
      for (const a of Array.isArray(o.alerts) ? o.alerts : []) { const lo = typeof a.min === "number" ? a.min : 1, hi = Math.min(typeof a.max === "number" ? a.max : lo + 31, lo + 63), more = a.op === "<=" ? "or fewer" : "or more";
        const cur = isObj(pref.alerts) && a.key in pref.alerts ? String(pref.alerts[a.key]) : "";
        out.push(row(esc(a.title), pickHTML(`data-alert="${esc(a.key)}"`, [["", "Never"], ...Array.from({length: Math.floor(hi - lo) + 1}, (_, i) => [String(lo + i), `${lo + i} ${more}`])], cur, a.title))); }
      out.push(row("Name", `<input class="inp" data-pref="label" maxlength="24" spellcheck="false" placeholder="${esc(w.label)}" value="${esc(pref.label || "")}" aria-label="Name">`));
      if ((w.in || []).includes("nav")) out.push(row("In my nav", switchHTML("shown", P.inMyNav(w, (x.p.widget_defaults || {})[w.key], pref), "In my nav")));
      if ((w.in || []).includes("column")) out.push(row("In my column", switchHTML("in_column", P.inMyColumn(w, (x.p.widget_defaults || {})[w.key], pref), "In my column")));
      return out.join("");
    }
    const editHTML = (x) => `<div class="ch">${ic(P.glyphOf(x.p.glyph))}<span>${esc(x.label)}</span><button class="lk" data-wdone>Done</button></div><div class="wb">
      <section class="sec"><div class="sh"><span>For you</span><span class="e"><span class="m">only you see these</span></span></div><div class="eo">${controlsHTML(x)}</div></section>
      <div class="ew">${x.s && x.s.document ? blocksHTML(x.s.document) : ""}</div><div class="ex" hidden></div></div>
      ${askHTML(`Ask Onix to change ${x.label}`, "Ask")}<p class="cap">A change that needs new code changes ${esc(x.label)} for everyone.</p>`;
    /* Onix's field: my words, and its verb, the view's one accent once there are words */
    const askHTML = (placeholder, verb) => `<div class="of"><textarea class="of-tx" rows="1" data-ask placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></textarea><button class="disc dip" data-ask-go aria-label="${esc(verb)}"><span class="cell"><span>${esc(verb)}</span><i class="sz">${esc(verb)}</i></span></button></div>`;
    /* Edit, from the sheet or the row's menu: the same sheet, turned into its form, its foot where it was; what is mine and
       the widget scroll inside it when the window is short, and Onix's field stays on its foot */
    function openEdit(id, kbd) {
      const x = navEntry(id); if (!x) return;
      if (!sheet || sheet.kind !== "widget" || sheet.id !== id) { float("widget", id, "", "", x.label); Object.assign(sheet, {install_id: x.p.id, key: x.w.key, scope: P.scopeOf(x.p.id, x.w.key, null, "")}); }
      sheet.mode = "edit"; sheet.el.classList.add("ed"); sheet.el.innerHTML = editHTML(x); if (x.s && x.s.document) mountBlocks($(".ew", sheet.el), x.s.document); else host(sheet.el);
      if (kbd) ($(".eo select, .eo input, .eo button", sheet.el) || sheet.el).focus(); else ready();
    }
    /* Done: the widget again, as I now see it */
    function closeEdit() { const x = navEntry(sheet.id); if (!x) { closeSheet(); return; } sheet.mode = "view"; sheet.el.classList.remove("ed"); sheet.el.innerHTML = viewHTML(x); if (x.s && x.s.document) mountBlocks(sheet.el, x.s.document); else host(sheet.el); const b = $("[data-wedit]", sheet.el); if (b) b.focus({preventScroll: true}); }
    /* my view changed (a control here, or another device): the widget under the controls follows, and each control
       says what is set, unless it is the one in my hands */
    function updateEdit(x) {
      $(".ch > span", sheet.el).textContent = x.label; const f = $("[data-ask]", sheet.el), say = `Ask Onix to change ${x.label}`;
      if (f.placeholder !== say) { f.placeholder = say; f.setAttribute("aria-label", say); $(":scope > .cap", sheet.el).textContent = `A change that needs new code changes ${x.label} for everyone.`; }
      const ew = $(".ew", sheet.el), doc = x.s && x.s.document;
      if (doc && !patchDoc(ew, doc)) { ew.innerHTML = blocksHTML(doc); mountBlocks(ew, doc); }
      const box = document.createElement("div"); box.innerHTML = controlsHTML(x);
      $$(".eo [data-pref], .eo [data-facet], .eo [data-alert]", sheet.el).forEach((c, i) => { const want = $$("[data-pref], [data-facet], [data-alert]", box)[i]; if (!want || c === document.activeElement) return;
        if (c.matches(".switch")) { c.classList.toggle("on", want.classList.contains("on")); c.setAttribute("aria-checked", want.getAttribute("aria-checked")); } else c.value = want.value; });
    }
    /* a control's change, mine at once (setPreference writes it before it is sent) */
    function changePref(c) {
      const x = navEntry(sheet.id); if (!x) return; const pref = x.pref || {}, set = (changes) => P.setPreference(x.p.id, x.w.key, changes, c);
      if (c.matches(".switch")) { const on = c.getAttribute("aria-checked") !== "true"; c.classList.toggle("on", on); c.setAttribute("aria-checked", String(on)); set({[c.dataset.pref]: on}); return; }
      if (c.dataset.pref === "label") { set({label: c.value.trim()}); return; }
      if (c.dataset.pref === "rest_figure") { set({rest_figure: c.value}); return; }
      const field = c.dataset.facet !== undefined ? "filters" : "alerts", key = c.dataset.facet ?? c.dataset.alert, next = {...(isObj(pref[field]) ? pref[field] : {})};
      if (c.value === "") delete next[key]; else next[key] = field === "filters" ? JSON.parse(c.value) : Number(c.value);
      set({[field]: next});
    }

    /* ---------- asking Onix, until its own tools for plugins are here (PL12) ----------
       The request is a message of mine that mentions Onix, in the plugin's own topic of the team's plugins repository
       (the "plugins" channel, ROUND2.md decision 1), where Onix builds and changes plugins; with no such channel, in the
       place in view. The answer comes there; the sheet says where and opens it. */
    function askPlace(topic) {
      const plugins = [...streams.values()].find((s) => s.name.toLowerCase() === "plugins");
      if (plugins) return {stream_id: plugins.stream_id, stream: plugins.name, topic};
      const p = state.place, s = p && p.kind === "topic" ? streams.get(p.stream_id) : null; return s ? {stream_id: s.stream_id, stream: s.name, topic: p.topic} : null;
    }
    async function ask(words, topic, content, from) {
      const where = askPlace(topic); if (!where) { M.say(from, "There is no plugins channel here to ask in yet.", 2400); return null; }
      try { await api("POST", "messages", {type: "stream", to: String(where.stream_id), topic: where.topic, content}); return where; }
      catch (e) { M.say(from, e.message, 2400); return null; }
    }
    async function askEdit(b) {
      const f = $("[data-ask]", sheet.el), words = f.value.trim(), x = navEntry(sheet.id); if (!words || !x) { f.focus(); return; }
      const where = await ask(words, x.p.plugin, `${state.agentMention} About the ${x.label} widget (the ${x.p.name} plugin, \`${x.p.plugin}\`): ${words}`, b);
      if (!where || !sheet || sheet.mode !== "edit") return; f.value = ""; b.classList.remove("armed");
      const ex = $(".ex", sheet.el); ex.hidden = false;
      ex.innerHTML = `<p class="ex-me">${esc(words)}</p><div class="ex-rec">${mk("done")}<span>Asked Onix in ${esc(where.stream)} › ${esc(where.topic)}</span><button class="lk" data-ask-open="${esc(`s:${where.stream_id}:${where.topic.toLowerCase()}`)}">Open</button></div>`; host(ex);
      if (ex.scrollIntoView) ex.scrollIntoView({block: "nearest"});
    }

    /* ---------- a widget row's own menu: Edit, and Hide for me ---------- */
    function openRowMenu(id, kbd) {
      const x = navEntry(id); if (!x) return;
      const e = float("menu", id, "pmenu", `<ul class="pm-l"><li><button class="mi" role="menuitem" data-wedit>${ic("edit")}<span class="t">Edit</span></button></li><li><button class="mi" role="menuitem" data-whide="${esc(id)}">${ic("eye")}<span class="t">Hide for me</span></button></li></ul>`, `${x.label}: more`, "menu");
      host(e); if (kbd) $("[role=menuitem]", e).focus(); else ready();
    }

    /* ---------- the "+" menu (ROUND2.md, section E) ---------- */
    /* what I hid, each with Show; what the team offers and I have not added, each with Add; then All plugins */
    function menuHTML() {
      const all = P.teamWidgets().filter((x) => (x.w.in || []).includes("nav")), hidden = [], offered = [];
      for (const x of all) { const d = (x.p.widget_defaults || {})[x.w.key], team = d ? d.nav : x.w.team_default || "shown", mine = x.pref && typeof x.pref.shown === "boolean" ? x.pref.shown : null;
        if (mine === false) hidden.push(x); else if (mine === null && team === "offered") offered.push(x); }
      const row = (x, verb) => `<li>${ic(P.glyphOf(x.p.glyph))}<span class="t">${esc((x.pref && x.pref.label) || x.w.label)}</span><button class="lk" role="menuitem" data-wshow="${esc(x.id)}">${verb}</button></li>`;
      return (hidden.length ? `<div class="pm-h">Hidden by you</div><ul class="pm-l">${hidden.map((x) => row(x, "Show")).join("")}</ul>` : "")
        + (offered.length ? `<div class="pm-h">Offered by your team</div><ul class="pm-l">${offered.map((x) => row(x, "Add")).join("")}</ul>` : "")
        + `${hidden.length || offered.length ? '<div class="pm-gap"></div>' : ""}<ul class="pm-l"><li><button class="mi" role="menuitem" data-wall>${ic("plugin")}<span class="t">All plugins</span></button></li></ul>`;
    }
    function openMenu(kbd) { const e = float("menu", "plus", "pmenu", menuHTML(), "Widgets", "menu"); host(e); if (kbd) ($("[role=menuitem]", e) || e).focus(); else ready(); }
    /* the menu follows what changes while it is open: a widget shown elsewhere, one the team offers */
    function updateMenu() { const items = $$("[role=menuitem]", sheet.el), at = items.indexOf(document.activeElement); sheet.el.innerHTML = menuHTML(); host(sheet.el); const now_ = $$("[role=menuitem]", sheet.el); if (at >= 0 && now_.length) now_[Math.min(at, now_.length - 1)].focus(); }
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
      if (sheet.kind === "menu") { if (sheet.id === "plus") updateMenu(); return; }
      const x = navEntry(sheet.id); if (!x) { closeSheet(); return; }
      if (sheet.mode === "edit") { updateEdit(x); return; }
      const body = $(".wb", sheet.el), doc = x.s && x.s.document;
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
        return `<section class="sec slots plug" data-wscope="${esc(x.scope)}" style="--slots:${slots}"><div class="sh"><span>${esc(x.label)}</span><span class="e">${cap}</span></div><ul class="ls">${body}</ul></section>`; }).join("");
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
    /* Jump to: Plugins, each plugin by name, and one command a widget in the nav ("Show GPUs") */
    function items() {
      const out = [{key: "plugins", label: "Plugins", glyph: ic("plugin"), cap: ""}];
      for (const x of P.navWidgets()) out.push({cmd: () => P.showWidget(x.id, true), label: `Show ${x.label}`, glyph: ic(P.glyphOf(x.p.glyph)), cap: x.p.name});
      for (const p of P.installs.values()) if (p.state !== "draft") out.push({key: `plugin:${p.id}`, label: p.name, glyph: ic(P.glyphOf(p.glyph)), cap: "Plugin"});
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
    /* a sheet's Open goes to the plugin's page */
    press(document.body, ".wsheet [data-wopen]", () => { const id = sheet && sheet.install_id; closeSheet(true); if (id) openPlace(placeFromKey(`plugin:${id}`)); });
    press(document.body, ".wd [data-wclose]", () => closeItem(false));
    /* Edit, from the sheet or a row's menu; Done; a control; Ask; and Open, where I asked */
    press(document.body, ".wsheet [data-wedit]", (b, e, ptr) => { const id = sheet && sheet.id; if (id) openEdit(id, !ptr); });
    press(document.body, ".wsheet.ed [data-wdone]", () => closeEdit());
    press(document.body, ".wsheet.ed .eo .switch", (c) => changePref(c));
    document.addEventListener("change", (e) => { if (sheet && sheet.mode === "edit" && e.target.closest && e.target.closest(".wsheet.ed .eo") && e.target.matches("select, input")) changePref(e.target); });
    press(document.body, ".wsheet.ed [data-ask-go]", (b) => askEdit(b));
    /* the verb is the view's one accent once the field has words */
    document.addEventListener("input", (e) => { if (!e.target.matches || !e.target.matches(".wsheet [data-ask]")) return; const go = $("[data-ask-go]", e.target.parentElement); if (go) go.classList.toggle("armed", e.target.value.trim() !== ""); });
    press(document.body, ".wsheet [data-ask-open]", (b) => { closeSheet(true); openPlace(placeFromKey(b.dataset.askOpen)); });
    /* Hide for me: the row leaves my nav, and the "+" holds it under Hidden by you; the keyboard is left on the "+" */
    press(document.body, ".pmenu [data-whide]", (b, e, ptr) => { const [install_id, key] = b.dataset.whide.split("/"); closeSheet(true); P.setPreference(+install_id, key, {shown: false}, P.rowEl("plus")); const plus = P.rowEl("plus"); if (!ptr && plus) plus.focus({preventScroll: true}); });
    /* the menu's verbs: Show and Add put the widget in my nav, from where I am; All plugins opens Plugins */
    press(document.body, ".pmenu [data-wshow]", (b) => { const [install_id, key] = b.dataset.wshow.split("/"); P.setPreference(+install_id, key, {shown: true}, b); });
    press(document.body, ".pmenu [data-wall]", () => { closeSheet(true); openPlace(placeFromKey("plugins")); });
    /* in a menu, Up and Down move between its verbs */
    document.addEventListener("keydown", (e) => { if (!sheet || sheet.kind !== "menu" || !sheet.el.contains(document.activeElement)) return; const items = $$("[role=menuitem]", sheet.el), i = items.indexOf(document.activeElement), d = {ArrowDown: 1, ArrowUp: -1}[e.key];
      if (!d || i < 0) return; e.preventDefault(); items[(i + d + items.length) % items.length].focus(); });
    press(document.body, ":is(.wsheet, .wd) [data-wact]", (b) => { const {target, doc} = actionTarget(rootOf(b)); const a = doc && (doc.actions || [])[+b.dataset.wact]; if (target && a) runAction(target, a, {index: JSON.stringify(+b.dataset.wact)}, b); });
    press(document.body, ":is(.wsheet, .wd) .wl > li:is([data-open], [data-act])", (li, e, ptr) => {
      const {target, doc} = actionTarget(rootOf(li)); if (!target || !doc) return; const row = doc.blocks.flatMap((b) => (b.type === "list" ? P.rowsOf(doc, b) : [])).find((r) => r.key === li.dataset.row); if (!row) return;
      openRow({install_id: target.install_id, widget: target.widget, link_id: target.link_id, row, from: sheet ? P.rowEl(sheet.id) : li, kbd: !ptr}); });
    document.addEventListener("keydown", (e) => {
      if (confirming) return;
      if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches(":is(.wsheet, .wd) .wl > li[tabindex]")) { e.preventDefault(); e.target.click(); return; }
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.target.matches && e.target.matches(".wsheet [data-ask]")) { e.preventDefault(); e.stopPropagation(); const go = $("[data-ask-go]", sheet.el); if (go) go.click(); return; }
      if (e.key === "Enter" && e.target.matches && e.target.matches(".wsheet.ed .eo input")) { e.preventDefault(); e.stopPropagation(); e.target.blur(); return; }
      if (e.key !== "Escape" || (document.activeElement && document.activeElement.closest && document.activeElement.closest(".pal, .menu, .pop, .pcard, #lightbox"))) return;
      if (sheet) { e.preventDefault(); e.stopPropagation(); closeSheet(false, true); return; }
      if (item && item.el.contains(document.activeElement)) { e.preventDefault(); e.stopPropagation(); closeItem(false); } }, true);

    /* ---------- the Plugins place ---------- */
    /* the team's installs in full, as their pages need them (GET armada/plugins): settings, whether each secret is set, links, tools turned off */
    const full = new Map(); let loaded = false, failed = "";
    async function load() { try { const r = await api("GET", "armada/plugins"); full.clear(); for (const x of r.plugins) full.set(x.id, x); loaded = true; failed = ""; } catch (e) { failed = e.message; } }
    /* ---- what waits for the server (PL3, PL6): each is a seam the server's route fills, and says so until it does ---- */
    /* Recent: the install's audit lines (RealmAuditLog), newest first; GET armada/plugins/<id>/recent is not served yet */
    const recent = new Map();
    async function loadRecent(id) { try { recent.set(id, (await api("GET", `armada/plugins/${id}/recent`)).recent); } catch (e) { recent.set(id, null); } }
    /* Tools for Onix: what the plugin's MCP server lists; GET armada/plugins/<id>/tools is not served yet */
    const tools = new Map();
    async function loadTools(id) { try { tools.set(id, (await api("GET", `armada/plugins/${id}/tools`)).tools); } catch (e) { tools.set(id, null); } }
    /* the store's index (plugyawn/armada-plugins, PL6): its entries are what can be installed; there are none until it is read */
    const INDEX = {name: "plugyawn/armada-plugins", entries: []};
    /* ---- end of what waits for the server ---- */

    const inView = () => (state.place && state.place.kind === "plugins" ? state.place : null);
    const pane = () => $("#plugins");
    const face = (glyph, size = 20) => `<i class="av pf s${size}">${ic(P.glyphOf(glyph))}</i>`;
    const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    const longDay = (ts) => new Date(ts * 1000).toLocaleDateString("en-GB", {day: "numeric", month: "short"});
    /* what a plugin adds, as a store row's caption says it */
    function addsOf(m) { const pv = (m && m.provides) || {}, w = pv.widgets || [], out = [];
      if (w.some((x) => x.in.includes("nav"))) out.push("nav"); if (w.some((x) => x.in.includes("column"))) out.push("column"); if (pv.tools) out.push("tools for Onix"); if (pv.posts) out.push("posts"); return out.join(", "); }
    const STATE_WORDS = {waiting: "Waiting for its settings", starting: "Starting", running: "Installed", failed: "Failed", draft: "Draft", removed: "Removed"};
    const stateOf = (x) => (x.date_removed ? "removed" : x.disabled ? "off" : x.state);
    function storeRow(x) { const st = stateOf(x), m = x.manifest || {};
      const verb = st === "removed" ? `<button class="lk" data-wrestore="${x.id}">Undo</button>` : `<span class="st${st === "failed" ? " c-fail" : ""}">${st === "off" ? "Off" : STATE_WORDS[st] || ""}</span>`;
      return `<div class="plg" tabindex="0" role="link" data-k="plugin:${x.id}"><span class="g">${face(m.glyph, 28)}</span><span class="tx"><span class="nm">${esc(x.name)}<span class="by">${esc([m.author, addsOf(m)].filter(Boolean).join(" · "))}</span></span><span class="ds">${esc(m.description || "")}</span></span><span class="v">${verb}</span></div>`; }
    function storeHTML() {
      if (!loaded) return `<div class="store"><p class="note">${failed ? esc(failed) : "Loading"}</p></div>`;
      const all = [...full.values()].filter((x) => x.state !== "draft"), live = all.filter((x) => !x.date_removed), gone = all.filter((x) => x.date_removed);
      return `<div class="store">${live.length ? live.map(storeRow).join("") : '<p class="note">Nothing is installed yet.</p>'}${INDEX.entries.length ? INDEX.entries.map(indexRow).join("") : ""}${gone.length ? `<h2>Recently removed</h2>${gone.map(storeRow).join("")}` : ""}</div>`;
    }
    const indexRow = (en) => `<div class="plg" tabindex="0" role="link" data-index="${esc(en.id)}"><span class="g">${face(en.manifest.glyph, 28)}</span><span class="tx"><span class="nm">${esc(en.manifest.name)}<span class="by">${esc([en.manifest.author, addsOf(en.manifest)].filter(Boolean).join(" · "))}</span></span><span class="ds">${esc(en.manifest.description)}</span></span><span class="v"><button class="lk" data-index-open="${esc(en.id)}">Install</button></span></div>`;
    const sourcesHTML = () => `<section class="sec"><div class="sh"><span>Sources</span></div><ul class="ls"><li data-tip="The store's index is not in the beta yet">${ic("globe")}<span class="t"><code>${esc(INDEX.name)}</code></span><span class="e"><span class="d">${INDEX.entries.length || ""}</span></span></li><li data-tip="Installing from an address is not in the beta yet">${ic("plus")}<span class="t">Install from an address</span><span class="e"></span></li></ul></section>`;

    /* ---- a plugin's page ---- */
    const pw = (n) => `<svg class="pw" viewBox="0 0 ${n * 10} 8" style="width:${n * 10}px" aria-label="Hidden">${Array.from({length: n}, (_, i) => `<circle cx="${i * 10 + 4}" cy="4" r="3"/>`).join("")}</svg>`;
    const r = (l, x, ctl, attrs = "") => `<div class="r"${attrs}><div><div class="l">${l}</div><div class="x">${x}</div></div>${ctl}</div>`;
    let replacing = null;   /* the secret whose field is open to be replaced */
    function secretRow(x, sec) {
      const who = sec.set_by ? nameOf(sec.set_by) : "";
      if (sec.set && replacing !== sec.name) return r(esc(sec.title), `Set${who ? ` by ${esc(who)}` : ""}${sec.date_set ? ` on ${esc(longDay(sec.date_set))}` : ""}. Only this plugin's process sees it.`, `<span class="val"><span class="key">${pw(4)}${sec.hint ? `<code>${esc(sec.hint)}</code>` : ""}</span><button class="lk" data-replace="${esc(sec.name)}">Replace</button></span>`);
      const help = ((x.manifest.secrets || {})[sec.name] || {}).help;
      return r(esc(sec.title), `${help ? esc(help) + " " : ""}Only this plugin's process sees it.`, `<input class="inp" type="password" autocomplete="off" spellcheck="false" data-secret="${esc(sec.name)}" aria-label="${esc(sec.title)}">`);
    }
    /* one setting's control, from its schema: a choice of up to three is a segmented control, of more a menu; a switch; a
       number; a list, one a line; else words */
    function controlHTML(key, sch, value) {
      const v = value === undefined ? sch.default : value, name = esc(sch.title);
      if (Array.isArray(sch.enum) && sch.enum.length <= 3) return `<div class="seg" role="radiogroup" aria-label="${name}">${sch.enum.map((o) => `<button role="radio" aria-checked="${o === v}" class="${o === v ? "on" : ""}" data-set="${esc(key)}" data-v="${esc(JSON.stringify(o))}">${esc(String(o)[0].toUpperCase() + String(o).slice(1))}</button>`).join("")}</div>`;
      if (Array.isArray(sch.enum)) return `<select class="inp" data-set="${esc(key)}" aria-label="${name}">${sch.enum.map((o) => `<option value="${esc(JSON.stringify(o))}"${o === v ? " selected" : ""}>${esc(String(o))}</option>`).join("")}</select>`;
      if (sch.type === "boolean") return `<button class="switch${v ? " on" : ""}" role="switch" aria-checked="${!!v}" aria-label="${name}" data-set="${esc(key)}" data-v="${esc(JSON.stringify(!v))}"></button>`;
      if (sch.type === "integer" || sch.type === "number") return `<input class="inp" type="number" data-set="${esc(key)}" data-kind="number" aria-label="${name}" value="${v === undefined ? "" : esc(String(v))}">`;
      if (sch.type === "array") return `<textarea class="inp" rows="3" spellcheck="false" data-set="${esc(key)}" data-kind="lines" aria-label="${name}">${esc((Array.isArray(v) ? v : []).join("\n"))}</textarea>`;
      return `<input class="inp code" type="text" spellcheck="false" data-set="${esc(key)}" aria-label="${name}" value="${v === undefined ? "" : esc(String(v))}">`;
    }
    /* the settings a plugin's manifest declares, with the secrets first (its account); the setting named posts, for a
       plugin that posts, says where it shows, so it stands under Where it shows */
    const settingKeys = (m) => Object.keys(((m.settings || {}).properties) || {});
    const isPostsSetting = (m, k) => k === "posts" && !!(m.provides && m.provides.posts);
    function accountHTML(x) { const m = x.manifest, props = (m.settings || {}).properties || {}, keys = settingKeys(m).filter((k) => !isPostsSetting(m, k));
      if (!x.secrets.length && !keys.length) return "";
      return `<h2>Account</h2><div class="rows">${x.secrets.map((s) => secretRow(x, s)).join("")}${keys.map((k) => r(esc(props[k].title), esc(props[k].description || ""), controlHTML(k, props[k], x.settings[k]))).join("")}</div>`; }
    const placeName = (l) => { const s = streams.get(l.stream_id); return l.topic != null ? esc(l.topic) : `${ic("repo")}${esc(s ? s.name : "a channel")}`; };
    let linking = false, installing = null;   /* the link being made; an index entry whose page stands in the store's place */
    function linksHTML(x) { const kinds = Object.entries((x.manifest.provides || {}).links || {}); if (!kinds.length) return ""; const [kind, decl] = kinds[0];
      const rows = x.links.map((l) => r(`<code>${esc(l.value)}</code>`, l.topic != null ? "On one worktree only." : "On every worktree of the channel.", `<span class="val"><span class="pick">${placeName(l)}</span><button class="c sq" data-unlink="${l.id}" aria-label="Unlink" data-tip="Unlink">${ic("x")}</button></span>`)).join("");
      const form = linking ? `<div class="rows">${r(esc(decl.title), "What to link, and where.", `<span class="val"><input class="inp code" data-link-value placeholder="${esc(decl.title)}" aria-label="${esc(decl.title)}"><select class="inp" data-link-stream aria-label="Channel">${[...streams.values()].map((s) => `<option value="${s.stream_id}">${esc(s.name)}</option>`).join("")}</select>${decl.to.includes("topic") ? '<input class="inp" data-link-topic placeholder="Every worktree" aria-label="Worktree">' : ""}<button class="go" data-link-go>Link</button></span>`, ` data-kind="${esc(kind)}"`)}</div>` : "";
      return `<h2>Links</h2><div class="rows">${rows}</div>${form}${linking ? "" : `<button class="lk add" data-link-add>${ic("plus")}Link a ${esc(decl.title.toLowerCase())}</button>`}`; }
    function showsHTML(x) { const m = x.manifest, out = [], props = (m.settings || {}).properties || {}, shown = ((m.provides || {}).widgets || []).filter((w) => w.in.includes("nav") || w.in.includes("column")), many = shown.length > 1;
      for (const w of shown) { const d = x.widget_defaults[w.key] || {nav: w.team_default || "shown", column: true};
        if (w.in.includes("nav")) out.push(r(`In the nav${many ? `: ${esc(w.label)}` : ""}`, d.nav === "offered" ? `${esc(w.label)} is offered to the team, not shown.` : `${esc(w.label)}, in everyone's nav.`, `<button class="switch${d.nav === "shown" ? " on" : ""}" role="switch" aria-checked="${d.nav === "shown"}" aria-label="${esc(`${w.label} in the nav`)}" data-shows="${esc(w.key)}" data-where="nav"></button>`));
        if (w.in.includes("column")) out.push(r(`In the column${many ? `: ${esc(w.label)}` : ""}`, `${count(w.column_slots || 3, "row", "rows")} on the worktrees of the places it is linked to.`, `<button class="switch${d.column !== false ? " on" : ""}" role="switch" aria-checked="${d.column !== false}" aria-label="${esc(`${w.label} in the column`)}" data-shows="${esc(w.key)}" data-where="column"></button>`)); }
      for (const k of settingKeys(m).filter((k2) => isPostsSetting(m, k2))) out.push(r(esc(props[k].title), esc(props[k].description || "When something happens in a place it is linked to."), controlHTML(k, props[k], x.settings[k])));
      return out.length ? `<h2>Where it shows</h2><div class="rows">${out.join("")}</div>` : ""; }
    function toolsHTML(x) { const pv = x.manifest.provides || {}; if (!pv.tools) return ""; const list = tools.get(x.id), off = new Set(x.disabled_tools || []);
      const rows = list ? list.map((t) => `<li>${ic("term")}<span class="t"><code>${esc(t.name)}</code> ${esc(t.description || "")}</span><span class="e"><button class="switch${off.has(t.name) ? "" : " on"}" role="switch" aria-checked="${!off.has(t.name)}" aria-label="${esc(t.name)}" data-tool="${esc(t.name)}"></button></span></li>`).join("")
        : `<li>${ic("term")}<span class="t">${list === null ? "Its tools are listed here once Armada asks the plugin for them, which the beta does not yet." : "Asking the plugin for its tools"}</span><span class="e"></span></li>`;
      return `<h2>Tools for Onix<span class="m">given to Codex as an MCP server</span></h2><ul class="ls">${rows}</ul>`; }
    function failHTML(x) { if (x.state !== "failed" && x.state !== "waiting") return "";
      return `<div class="rows">${r(x.state === "failed" ? `${mk("failed")} Stopped` : "Waiting", esc(x.detail || (x.state === "failed" ? "It stopped five times in a row." : "A setting or a secret it needs is missing.")), x.state === "failed" ? '<button class="lk" data-retry-plugin>Retry</button>' : "")}</div>`; }
    function pageHTML(p) {
      if (!loaded) return `<article class="set"><p class="lead">${failed ? esc(failed) : "Loading"}</p></article>`;
      const x = full.get(p.install_id); if (!x) return '<article class="set"><p class="lead">This plugin is not installed here.</p></article>';
      return `<article class="set">${failHTML(x)}${accountHTML(x)}${linksHTML(x)}${showsHTML(x)}${toolsHTML(x)}</article>`;
    }
    function pageSideHTML(x) {
      const list = recent.get(x.id);
      const lines = list ? list.slice(0, 12).map((ln) => `<li>${ln.acting_user_id ? faceOf(ln.acting_user_id, "s20") : face(x.manifest.glyph)}<span class="t">${esc(ln.text || ln.type)}</span><span class="e"><span class="d">${esc(hhmm(ln.time))}</span></span></li>`).join("")
        : `<li>${ic("trace")}<span class="t">${list === null ? "Not in the beta yet" : "Loading"}</span><span class="e"></span></li>`;
      const rm = x.date_removed ? `<div class="rm"><span>Removed. Its settings are kept for 7 days.</span><button class="lk" data-wrestore>Undo</button></div>` : `<div class="rm"><span>Takes back everything it added.</span><button class="lk danger" data-wremove>Remove</button></div>`;
      return `<section class="sec" id="sec-recent"><ul class="ls">${lines}</ul></section><section class="sec"><div class="sh"><span>Version</span></div><ul class="ls"><li>${ic("commit")}<span class="t"><b>${esc(x.version)}</b>${x.sha ? ` <code>${esc(x.sha.slice(0, 7))}</code>` : ""}</span><span class="e"></span></li></ul></section><section class="sec foot">${rm}</section>`;
    }
    function head(p) {
      if (p.view === "store" && installing) { const m = installing.manifest; return {kind: "plain", path: "Plugins", title: esc(m.name), say: esc([m.author, m.version, installing.repository].filter(Boolean).join(" · ")), checklist: {title: "Versions"}}; }
      if (p.view === "store") { const n = [...full.values()].filter((x) => x.state !== "draft" && !x.date_removed).length; return {kind: "plain", title: "Plugins", say: "Anyone here can install one. It runs on this server, beside Armada.", checklist: {title: "Installed", cap: loaded ? String(n) : ""}}; }
      const x = full.get(p.install_id) || P.installs.get(p.install_id), who = x && x.installed_by ? nameOf(x.installed_by) : "";
      return {kind: "plain", path: "Plugins", title: esc(x ? x.name : "Plugin"), say: x ? esc([x.version, x.date_installed ? `installed${who ? ` by ${who}` : ""} on ${longDay(x.date_installed)}` : ""].filter(Boolean).join(" · ")) : "", checklist: {title: "Recent"}};
    }
    function sideHTML(p) {
      if (p.view === "store" && installing) return installSideHTML(installing);
      if (p.view === "store") { const live = [...full.values()].filter((x) => x.state !== "draft" && !x.date_removed);
        return `<section class="sec" id="sec-installed"><ul class="ls">${live.map((x) => `<li data-k="plugin:${x.id}">${face(x.manifest.glyph)}<span class="t">${esc(x.name)}</span><span class="e">${x.state === "failed" ? mk("failed") : ""}</span></li>`).join("") || `<li>${ic("plugin")}<span class="t">${loaded ? "None yet" : "Loading"}</span><span class="e"></span></li>`}</ul></section>${sourcesHTML()}`; }
      const x = full.get(p.install_id); return x ? pageSideHTML(x) : "";
    }
    /* the place opened: its pane drawn, then what it needs asked for, and drawn again */
    async function openPluginsPlace(p) {
      const box = pane(); box.parentElement.scrollTop = 0; replacing = null; linking = false; installing = null; draw();
      await load(); if (inView() !== p) return; draw(); drawHeader(); drawSide();
      if (p.view === "page" && full.has(p.install_id)) { await Promise.all([loadRecent(p.install_id), loadTools(p.install_id)]); if (inView() === p) { draw(); drawSide(); } }
    }
    function draw() { const p = inView(), box = pane(); if (!p || !box) return; const h = p.view === "store" ? (installing ? installHTML(installing) : storeHTML()) : pageHTML(p); if (box._sig === h) return; box._sig = h; box.innerHTML = h; host(box); }
    /* an install changed (an event): what is in view is asked again */
    let again = 0;
    function refresh() { clearTimeout(again); again = setTimeout(async () => { const p = inView(); if (!p) return; await load(); if (inView() === p) { draw(); if (state.hd) state.hd._sig = ""; drawHeader(); drawSide(); } }, 250); }

    /* ---- the page's controls: each writes the whole of what it changes, then the page is drawn from the server's answer ---- */
    const pageInstall = () => { const p = inView(); return p && p.view === "page" ? full.get(p.install_id) : null; };
    async function write(x, params, from) {
      try { const res = await api("PATCH", `armada/plugins/${x.id}`, params); full.set(x.id, res.plugin); draw(); drawSide(); }
      catch (e) { M.say(from && from.isConnected ? from : main, e.message, 2400); draw(); }
    }
    const settingValue = (input) => { const kind = input.dataset.kind; if (kind === "number") return input.value === "" ? undefined : Number(input.value); if (kind === "lines") return input.value.split("\n").map((s) => s.trim()).filter(Boolean); return input.value; };
    function saveSetting(x, key, value, from) { const next = {...x.settings}; if (value === undefined) delete next[key]; else next[key] = value; if (JSON.stringify(next) === JSON.stringify(x.settings)) return; x.settings = next; write(x, {settings: JSON.stringify(next)}, from); }
    function wire(box) {
      press(box, ".plg[data-k]", (row, e) => { if (e.target.closest("[data-wrestore]")) return; openPlace(placeFromKey(row.dataset.k)); });
      press(box, "[data-wrestore]", async (b) => { const id = +b.dataset.wrestore; try { await api("POST", `armada/plugins/${id}/restore`); await load(); draw(); drawSide(); } catch (e) { M.say(b, e.message, 2400); } });
      press(box, "[data-replace]", (b) => { replacing = b.dataset.replace; draw(); const f = $(`[data-secret="${replacing}"]`, box); if (f) f.focus(); });
      press(box, "[data-set]:is(button)", (b) => { const x = pageInstall(); if (x) saveSetting(x, b.dataset.set, JSON.parse(b.dataset.v), b); });
      /* the server keeps what it is not given (armada_plugins.py _widget_defaults), so only the switch pressed is sent */
      press(box, "[data-shows]", (b) => { const x = pageInstall(); if (!x) return; const key = b.dataset.shows, d = x.widget_defaults[key] || {nav: "shown", column: true};
        const change = b.dataset.where === "nav" ? {nav: d.nav === "shown" ? "off" : "shown"} : {column: d.column === false};
        x.widget_defaults = {...x.widget_defaults, [key]: {...d, ...change}}; write(x, {widget_defaults: JSON.stringify({[key]: change})}, b); });
      press(box, "[data-tool]", (b) => { const x = pageInstall(); if (!x) return; const off = new Set(x.disabled_tools || []); if (off.has(b.dataset.tool)) off.delete(b.dataset.tool); else off.add(b.dataset.tool); x.disabled_tools = [...off]; write(x, {disabled_tools: JSON.stringify([...off])}, b); });
      press(box, "[data-link-add]", () => { linking = true; draw(); const f = $("[data-link-value]", box); if (f) f.focus(); });
      press(box, "[data-link-go]", async (b) => { const x = pageInstall(); if (!x) return; const row = b.closest("[data-kind]"), topic = $("[data-link-topic]", row);
        try { await api("POST", `armada/plugins/${x.id}/links`, {kind: row.dataset.kind, value: $("[data-link-value]", row).value.trim(), stream_id: $("[data-link-stream]", row).value, ...(topic && topic.value.trim() ? {topic: topic.value.trim()} : {})}); linking = false; await load(); draw(); }
        catch (e) { M.say(b, e.message, 2400); } });
      press(box, "[data-unlink]", async (b) => { const x = pageInstall(); if (!x) return; try { await api("DELETE", `armada/plugins/${x.id}/links/${b.dataset.unlink}`); await load(); draw(); } catch (e) { M.say(b, e.message, 2400); } });
      press(box, "[data-retry-plugin]", async (b) => { const x = pageInstall(); if (!x) return; try { full.set(x.id, (await api("POST", `armada/plugins/${x.id}/retry`)).plugin); draw(); } catch (e) { M.say(b, e.message, 2400); } });
      box.addEventListener("change", (e) => { const x = pageInstall(), t = e.target; if (!x || !t.matches("[data-set]")) return; saveSetting(x, t.dataset.set, t.tagName === "SELECT" ? JSON.parse(t.value) : settingValue(t), t); });
      box.addEventListener("keydown", (e) => { const t = e.target;
        if ((e.key === "Enter" || e.key === " ") && t.matches && t.matches(".plg[data-k]") && e.target === t) { e.preventDefault(); t.click(); return; }
        if (e.key !== "Enter" || !t.matches || t.tagName === "TEXTAREA") return;
        if (t.matches("[data-secret]") && t.value) { e.preventDefault(); const x = pageInstall(); if (!x) return; const name = t.dataset.secret, value = t.value; t.value = ""; replacing = null; write(x, {secrets: JSON.stringify({[name]: value})}, t); }
        else if (t.matches("input[data-set]")) { e.preventDefault(); t.blur(); } });
      /* the column's verbs */
      if (!side) return;
      press(side, "[data-wremove]", async (b) => { const x = pageInstall(); if (!x) return; try { await api("DELETE", `armada/plugins/${x.id}`); await load(); draw(); drawSide(); const u = $("[data-wrestore]", side); if (u) u.focus({preventScroll: true}); } catch (e) { M.say(b, e.message, 2400); } });
      press(side, "[data-wrestore]", async (b) => { const x = pageInstall(); if (!x) return; try { await api("POST", `armada/plugins/${x.id}/restore`); await load(); draw(); drawSide(); } catch (e) { M.say(b, e.message, 2400); } });
      press(side, "#sec-installed li[data-k]", (li) => openPlace(placeFromKey(li.dataset.k)));
    }
    if (pane()) wire(pane());

    /* ---- the install page (05), for an entry of the store's index, which PL6 reads; the Plugins place lists none until then ---- */
    function installHTML(en) { const m = en.manifest, props = (m.settings || {}).properties || {}, pv = m.provides || {}, adds = [];
      for (const w of pv.widgets || []) { if (w.in.includes("nav")) adds.push(`<li>${ic("nav")}<span class="t"><b>A line in the nav</b>, ${esc(w.label)}</span></li>`); if (w.in.includes("column")) adds.push(`<li>${ic("panel")}<span class="t"><b>A list in the column</b> of every worktree on a channel you link</span></li>`); }
      if (pv.tools) adds.push(`<li>${ic("term")}<span class="t"><b>Tools for Onix</b>${pv.tools.instructions ? `: ${esc(pv.tools.instructions)}` : ""}</span></li>`);
      if (pv.posts) adds.push(`<li>${ic("thread")}<span class="t"><b>A line in the topic</b> when something happens in a place it is linked to</span></li>`);
      const can = [...(m.reaches || []).map((h) => `<li>${ic("globe")}<span class="t">Reach <code>${esc(h)}</code></span></li>`), pv.posts ? `<li>${ic("person")}<span class="t">Post as <b>${esc(m.name)}</b> in the channels you link, and read them</span></li>` : "", m.run ? `<li>${ic("term")}<span class="t">Run on this server, with the access Onix's runs have</span></li>` : ""].join("");
      const secrets = Object.entries(m.secrets || {}).map(([k, s]) => r(esc(s.title), `${s.help ? esc(s.help) + " " : ""}Only this plugin's process sees it.`, `<input class="inp" type="password" autocomplete="off" data-install-secret="${esc(k)}" aria-label="${esc(s.title)}">`)).join("");
      const settings = Object.entries(props).map(([k, sch]) => r(esc(sch.title), esc(sch.description || ""), controlHTML(k, sch, undefined).replace(/data-set=/g, "data-install-set="))).join("");
      return `<article class="set"><p class="lead">${esc(m.description)}</p><h2>What it adds</h2><ul class="ls">${adds.join("")}</ul>${can ? `<h2>What it can do</h2><ul class="ls">${can}</ul>` : ""}${secrets || settings ? `<h2>Set it up</h2><div class="rows">${secrets}${settings}</div>` : ""}<div class="go-row"><button class="go" data-install="${esc(en.id)}">Install</button><span class="cap">It starts in a few seconds. Remove it whenever you like.</span></div></article>`; }
    async function install(en, b) {
      const box = pane(), secrets = {}, settings = {}; for (const f of $$("[data-install-secret]", box)) if (f.value) secrets[f.dataset.installSecret] = f.value;
      for (const f of $$("input[data-install-set], textarea[data-install-set]", box)) { const v = settingValue(f); if (v !== undefined && v !== "") settings[f.dataset.installSet] = v; }
      for (const f of $$("select[data-install-set]", box)) settings[f.dataset.installSet] = JSON.parse(f.value);
      for (const f of $$("button[data-install-set][aria-checked='true']", box)) settings[f.dataset.installSet] = f.getAttribute("role") === "switch" ? true : JSON.parse(f.dataset.v);
      try { const res = await api("POST", "armada/plugins", {manifest: JSON.stringify(en.manifest), source_kind: "github", source: en.repository, sha: en.commit, settings: JSON.stringify(settings), secrets: JSON.stringify(secrets)}); full.set(res.plugin.id, res.plugin); openPlace(placeFromKey(`plugin:${res.plugin.id}`)); }
      catch (e) { const cap = $(".go-row .cap", box); if (cap) { cap.textContent = e.message; cap.classList.add("c-fail"); } M.say(b, e.message, 2400); }
    }
    /* an entry's page stands in the store's place until it is installed or the place is left: its header and column say which */
    const installSideHTML = (en) => `<section class="sec" id="sec-versions"><ul class="ls"><li>${ic("commit")}<span class="t"><b>${esc(en.manifest.version)}</b> <code>${esc(String(en.commit).slice(0, 7))}</code></span><span class="e"></span></li></ul></section><section class="sec"><div class="sh"><span>Source</span></div><ul class="ls"><li>${ic("globe")}<span class="t"><code>${esc(en.repository)}</code></span><span class="e"><span class="d">${esc(en.manifest.license || "")}</span></span></li></ul></section>`;
    if (pane()) press(pane(), "[data-index-open]", (b) => { const en = INDEX.entries.find((x) => x.id === b.dataset.indexOpen); if (!en) return; installing = en; pane().parentElement.scrollTop = 0; draw(); if (state.hd) state.hd._sig = ""; drawHeader(); drawSide(); });
    /* before it is installed, a choice is kept in the form: the segment pressed, a switch turned */
    if (pane()) press(pane(), "button[data-install-set]", (b) => { if (b.getAttribute("role") === "switch") { const on = b.getAttribute("aria-checked") !== "true"; b.setAttribute("aria-checked", String(on)); b.classList.toggle("on", on); return; }
      for (const o of $$("button", b.parentElement)) { o.setAttribute("aria-checked", String(o === b)); o.classList.toggle("on", o === b); } });
    if (pane()) press(pane(), "[data-install]", (b) => { const en = INDEX.entries.find((x) => x.id === b.dataset.install); if (en) install(en, b); });

    return {
      openSheet, closeSheet, openMenu, openRowMenu, openEdit, openRow, closeItem, sparkPoints, sectionsHTML, patchColumn, items,
      /* a new document for a scope: what is open of it changes in place */
      update(scope) { if (sheet && (sheet.scope === scope || sheet.kind === "menu")) updateSheet(); if (item) updateItem(scope); },
      /* an install removed: what is open of it closes */
      forget(id) { if (sheet && sheet.install_id === id) closeSheet(true); if (item && item.target.install_id === id) closeItem(true); full.delete(id); },
      /* leaving the place: the column it borrowed is given back, and the sheet closes */
      leave() { closeSheet(true); closeItem(true); },
      openPlace: openPluginsPlace, refresh, head, sideHTML,
      /* the store's index, which PL6 fills */
      setIndex(entries) { INDEX.entries = entries; draw(); },
    };
  };
}
