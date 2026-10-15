// Armada mock kit, third final: draws the glyphs, Onix's pixel marks and the frame every screen shares (nav, header,
// band, composer, the column's first heading), so the frame cannot differ between two screens or two places. Screens
// supply the pane's content and the body of the column beside it. Data for the drawings is glyphs.js (tools/icons.py).
(function () {
  const q = new URLSearchParams(location.search), R = document.documentElement;
  if (q.get("theme")) R.dataset.theme = q.get("theme");
  if (q.get("mask")) R.classList.add("mask");          // the review sheet's mask: the fill of bubbles and of the reactions on them
  const pal = q.get("p");                               // ?p=tide | citrine | onyx : a palette is one file of colour tokens

  // ---- stroked glyphs: 16 px box, 14 live, one weight on whole device pixels, butt caps. Never scaled.
  window.ic = (n, cls) => { const g = GLYPHS[n]; if (!g) return ""; return `<svg class="ic ${cls || ""}" viewBox="0 0 16 16" aria-hidden="true">${g.s ? `<g class="n">${g.s}</g>` : ""}${g.f ? `<g class="f">${g.f}</g>` : ""}</svg>`; };
  // ---- the marks of a run and of a step (r4 marks study): 12 by 12 cells of one CSS pixel, in the 14 px box (28 on Home,
  // 2 px a cell). done, live, queued, needs, paused, failed, cancelled; "left" is a step not reached. Each state has its
  // own silhouette; a hue only on needs (attention) and failed (failure). Drawings: marks.js (plain, committed), and the
  // themed ball and egg that runs.js puts in BETA_THEMED before the first draw when this server has them.
  const MKCLS = {needs: "c-att", failed: "c-fail"}, THEMED = window.BETA_THEMED = window.BETA_THEMED || {};
  const BOX = 'viewBox="-1 -1 14 14" shape-rendering="crispEdges"';
  window.mk = (n, cls) => `<svg class="mk ${n} ${MKCLS[n] || ""} ${cls || ""}" ${BOX} aria-hidden="true">${(THEMED[n] && THEMED[n].rest) || BETA_MARKS[n] || ""}</svg>`;
  // the working mark: the one loop a live run has, at the head of the stream. A strip of three drawings 14 apart (the
  // ball's lean left, upright and lean right, or the plain dot's three sizes), moved a drawing at a time (mock.css), so no
  // frame is blank. One element for the life of an answer.
  window.wk = (live = true, cls) => { const t = THEMED.live, frames = t ? t.frames : BETA_DOT_LOOP;
    return `<svg class="mk wk ${t ? "ball" : "dot"}${live ? " live" : ""} ${cls || ""}" ${BOX} aria-hidden="true"${live ? ' data-m="live"' : ""}><g>${frames.map((f, i) => `<g transform="translate(${14 * i} 0)">${f}</g>`).join("")}</g></svg>`; };
  // Onix's face: the one place it is drawn, so the party sprite can take its place later by replacing this function alone.
  // Today it is direction B of the study: three faceted boulders in a flint disc where a person shows a face (28 in the
  // gutter, 20 in lists, 18 in a thread's line), bare at 14; in the field, bare at 16, solid when Onix will answer and an
  // outline ("field-off") when it will not.
  window.agentFace = (size = 28, cls = "") => { const F = BETA_AGENT_FACES, f = size === "field" ? F["bare-16"] : size === "field-off" ? F["line-16"] : F["disc-" + size] || (size <= 14 ? F["bare-14"] : F["disc-28"]), disc = f.off > 0;
    const state = size === "field" ? " sol" : size === "field-off" ? " hol" : "";
    return `<svg class="sigil agent s${f.n}${disc ? " in-disc" : ""}${state} ${cls}" viewBox="0 0 ${f.n} ${f.n}" aria-hidden="true">${disc ? `<circle class="disc" cx="${f.n / 2}" cy="${f.n / 2}" r="${f.n / 2}"/>` : ""}<g transform="translate(${f.off} ${f.off})" shape-rendering="crispEdges">${f.d}</g></svg>`; };
  window.sigil = (size = 28, live = false, cls) => (String(size) === "20h" ? agentFace("field-off", cls) : agentFace(parseInt(size, 10) || 28, cls));
  window.dots = (cls) => `<svg class="dots ${cls || ""}" viewBox="0 0 26 6" aria-hidden="true"><circle cx="3" cy="3" r="3"/><circle cx="13" cy="3" r="3"/><circle cx="23" cy="3" r="3"/></svg>`;
  // a face: initials on the person's stone (a photo when there is one). "KH:lapis", "De:me", with " on" or " away".
  window.face = (spec, cls) => { const [who, pres] = spec.split(" "), [i, st] = who.split(":"); const small = /s20|s18/.test(cls || ""), bare = /s14/.test(cls || "");   // a 14 px disc is too small for a letter
    const first = (() => { try { const it = new Intl.Segmenter(undefined, {granularity: "grapheme"}).segment(i)[Symbol.iterator]().next(); return it.done ? "" : it.value.segment; } catch (e) { return i.slice(0, 1); } })();   // a letter with its marks, never half of one
    return `<i class="av p-${st || "flint"} ${pres || ""} ${cls || ""}">${bare ? "" : small ? first : i}</i>`; };
  window.faces = (list, cls) => `<span class="avs">${list.map((f) => face(f, cls)).join("")}</span>`;
  const stone = (spec) => ((spec || "").split(" ")[0].split(":")[1]) || "flint";
  // reaction chips: "👍 2, ❤️ me, 🎉 3 me, +2"
  window.chips = (spec) => spec.split(",").map((t) => t.trim()).filter(Boolean).map((t) => {
    if (t[0] === "+") return `<button class="rc more" aria-label="More reactions">${t}</button>`;
    const p = t.split(/\s+/), mine = p.includes("me"), n = p.find((x) => /^\d+$/.test(x));
    return `<button class="rc${mine ? " mine" : ""}${p.includes("hov") ? " hov" : ""}${p.includes("press") ? " press" : ""}"><span class="e">${p[0]}</span>${n && +n > 1 ? `<span class="n">${n}</span>` : ""}</button>`; }).join("");
  // a figure in fixed cells: digits 0.6 em, punctuation 0.3 em, letters their own width. Stands in for tabular figures,
  // which the test font does not have, and is how a figure that changes keeps its place in the product.
  window.fig = (v) => [...String(v)].map((c) => `<span${/\d/.test(c) ? "" : /[A-Za-z−+]/.test(c) ? ' class="w"' : ' class="p"'}>${c === "<" ? "&lt;" : c === "&" ? "&amp;" : c}</span>`).join("");

  // Whatever takes its width from its text is rounded up to a whole pixel, so nothing after it starts on a fraction:
  // a glyph, a pixel mark and a right-aligned word all stand on whole pixels.
  const FACES = ['400 14px "Söhne"', '500 14px "Söhne"', '400 16px "Söhne"', '600 20px "Söhne"', '400 14px "Geist Mono"'];
  window.snap = (root) => Promise.all(FACES.map((f) => document.fonts.load(f))).then(() => document.fonts.ready).then(() => {
    root.querySelectorAll(".b, .go, .tab, .hd .l2 .prog, .hd .repo, .hd .sl, .hd .title, .hd .dist, .hd .pr, .hd .commit .by, .disc, .band .sw, .who, .tl .lk, .line > span, .opt").forEach((e) => { if (e.style.width || e.closest(".ans")) return; const r = e.getBoundingClientRect(); if (r.width % 1 && (!e.classList.contains("b") || r.height <= 37)) e.style.width = Math.ceil(r.width) + "px"; });
    root.querySelectorAll(".hd .commit .subj").forEach((e) => wordcut(e));
    // the working mark rides the end of a line of text, which ends on a fraction: it is moved onto the pixel grid
    root.querySelectorAll(".head > .mk").forEach((m) => { m.style.marginLeft = ""; const f = m.getBoundingClientRect().left % 1; if (f) m.style.marginLeft = (1 - f) + "px"; }); });
  // a line of words that does not fit its cell is cut at a word and ends in an ellipsis. When the cut at a word would leave
  // the cell more than a third empty (the next word is long: "Add…" and a gap), it is cut inside that word instead.
  const textWidth = (e) => { const r = document.createRange(); r.selectNodeContents(e); return r.getBoundingClientRect().width; };
  window.wordcut = (e, text) => { if (!e) return; if (text != null) e.dataset.full = text; const full = e.dataset.full || (e.dataset.full = e.textContent); e.textContent = full; e.title = "";
    if (e.scrollWidth <= e.clientWidth) return; const w = full.split(" "), room = e.clientWidth; e.title = full; let fit = 0;   /* the room the cell has: with the whole line in it, it is as wide as it can be */
    for (let n = w.length - 1; n > 0; n--) { e.textContent = w.slice(0, n).join(" ").replace(/[,;:.]$/, "") + "…"; if (e.scrollWidth <= e.clientWidth) { fit = n; break; } }
    if (fit && room - textWidth(e) <= room / 3) return;
    let lo = 1, hi = full.length - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; e.textContent = full.slice(0, m).trimEnd() + "…"; if (e.scrollWidth <= e.clientWidth) lo = m; else hi = m - 1; }
    e.textContent = full.slice(0, lo).trimEnd() + "…"; };
  // the column's lists have heights that do not depend on what they hold. The checklist is seven rows; a list with
  // slots is that many rows; the list that grows takes the room left, in whole rows. A row that has no slot is not
  // drawn: it is counted in a quiet "n more" that stands in the gap under its list and opens the rest. A checklist
  // that is longer than its rows keeps its end (the live step and what is left) and folds its earliest rows.
  const outer = (li) => li.offsetHeight + (parseFloat(getComputedStyle(li).marginBottom) || 0);
  window.fitLists = (root = document) => root.querySelectorAll("aside.side > .out").forEach((out) => {
    const H = parseFloat(getComputedStyle(out).getPropertyValue("--h-m")) || 28;
    // A row marked "keep" (the Runs list's last row, which opens the old interface) is never folded. When rows are folded
    // the "n more" line needs a slot of its own: in a growing list it takes the last row's room (or, before a kept row,
    // the room above that row), so it never stands on what follows the list.
    const fit = (ul, host, head) => { if (!ul) return; const open = host.classList.contains("all"), rows = [...ul.children].filter((x) => x.tagName === "LI"), keep = rows.filter((x) => x.classList.contains("keep")), grow = host.classList.contains("grow");
      rows.forEach((x) => x.classList.remove("xr")); keep.forEach((x) => (x.style.marginTop = ""));
      const max = Math.floor((ul.clientHeight + 1) / H) * H, fixed = keep.reduce((n, li) => n + outer(li), 0); let used = 0, cut = 0, late = 0; const shown = [];
      if (!open) for (const li of head ? rows.slice().reverse() : rows) { if (li.classList.contains("keep")) continue; if (li.classList.contains("late")) { late++; continue; } const h = outer(li); if (cut || used + h + fixed > max + 1) { li.classList.add("xr"); cut++; } else { used += h; shown.push(li); } }
      cut += late;
      if (!open && !head && grow && cut && (keep.length || ul.clientHeight - used - fixed < H)) { const last = shown.pop(); if (last) { used -= outer(last); last.classList.add("xr"); cut++; } }
      let b = ul.nextElementSibling; if (!b || !b.classList.contains("mo")) { b = document.createElement("button"); b.className = "mo"; b.tabIndex = -1; ul.after(b); }
      const n = open ? rows.length : cut; b.hidden = !(open ? b.dataset.n > 0 : n > 0); if (!open) b.dataset.n = n; b.textContent = open ? "Show fewer" : head ? `${n} earlier` : `${n} more`;
      if (!open && cut && keep.length) keep[0].style.marginTop = H + "px";
      b.style.top = (ul.offsetTop + (grow && !open ? used : ul.offsetHeight)) + "px"; };
    out.querySelectorAll(":scope > .chk").forEach((ul) => fit(ul, ul, true));
    out.querySelectorAll(":scope > .sec.slots, :scope > .sec.grow").forEach((s) => fit(s.querySelector(":scope > .ls"), s, false));
    host(out); });
  // every control that takes a fill under the pointer gets the hover layer
  window.host = (root = document) => root.querySelectorAll(".row, .tab, .c, .lk, .mo, .opt, .seg button, .k, .stop, .jump, .mi, .hrow, .prog, button.out, .wk-name, .ls > li.b1").forEach((e) => { if (e.closest(".is-off") || e.classList.contains("mh")) return; e.classList.add("mh"); if (e.matches(".c.sq, button.out, .k.to")) e.classList.add("sq"); if (e.matches(".tab, .lk, .mo, .seg button, .stop, .jump, .opt, .k.mode")) e.classList.add("w"); });
  window.hydrate = (root) => {
    root.querySelectorAll("[data-ic]").forEach((e) => (e.outerHTML = ic(e.dataset.ic, e.className)));
    root.querySelectorAll("[data-mk]").forEach((e) => (e.outerHTML = mk(e.dataset.mk, e.className)));
    root.querySelectorAll("[data-wk]").forEach((e) => (e.outerHTML = wk(e.dataset.wk !== "rest", e.className)));
    root.querySelectorAll("[data-sigil]").forEach((e) => { const [s] = e.dataset.sigil.split(" "); e.outerHTML = sigil(s || 28, false, e.className); });
    root.querySelectorAll("[data-dots]").forEach((e) => (e.outerHTML = dots(e.className)));
    root.querySelectorAll("[data-face]").forEach((e) => (e.outerHTML = face(e.dataset.face, e.className)));
    root.querySelectorAll("[data-fig]").forEach((e) => { e.classList.add("fig"); e.innerHTML = fig(e.dataset.fig); e.removeAttribute("data-fig"); });
    // reactions. On a bubble: seated on its outer edge, after it in its row, so the time follows them. On Onix's words:
    // anchored at the end of the last line and standing at the lane's edge. A bubble I reacted to keeps a wash.
    root.querySelectorAll("[data-rc]").forEach((e) => { const s = document.createElement("span"); s.className = "rcs"; s.innerHTML = chips(e.dataset.rc);
      if (e.dataset.at === "end") { const a = document.createElement("span"); a.className = "rca"; a.appendChild(s); e.appendChild(a); }
      else if (e.parentElement && e.parentElement.classList.contains("bl")) { e.insertAdjacentElement("afterend", s); if (s.querySelector(".mine") && !e.closest(".cl.me")) e.classList.add("liked"); }
      else e.appendChild(s);
      e.removeAttribute("data-rc"); });
    root.querySelectorAll("[data-composer]").forEach((e) => { const c = JSON.parse(e.dataset.composer); e.outerHTML = composerHTML(c, c.title || "option-groups", c.id || ""); });
    root.querySelectorAll("[data-band]").forEach((e) => { e.classList.add("band"); e.innerHTML = bandHTML(JSON.parse(e.dataset.band)); e.removeAttribute("data-band"); });
    root.querySelectorAll("[data-prog]").forEach((e) => (e.outerHTML = progHTML(JSON.parse(e.dataset.prog), "")));
    root.querySelectorAll("[data-head]").forEach((e) => (e.outerHTML = headHTML(JSON.parse(e.dataset.head))));
    if (root.querySelector("[data-ic], [data-mk], [data-sigil], [data-face]")) return hydrate(root);
    host(root); snap(root);
  };

  // ---- the frame
  const NAV = [
    ["jump", "search", "Jump to"], ["home", "home", "Home", {mk: "needs", fresh: 1}], "gap",
    ["click", "repo", "click", {kids: [
      ["option-groups", "option-groups"], ["demo-live-run", "demo-live-run", {mk: "live"}],
      ["demo-needs-input", "demo-needs-input", {mk: "needs", fresh: 1}], ["release-notes", "release-notes"],
      ["demo-failed-run", "demo-failed-run", {mk: "failed", fresh: 1}], ["shell-completion", "shell-completion", {n: 2}],
      ["demo-threads", "demo-threads", {n: 4}]]}],
    ["armada", "repo", "armada", {mk: "live", kids: [["schema-rename", "schema-rename", {mk: "live"}], ["sound-kit", "sound-kit"], ["nav-rewrite", "nav-rewrite", {n: 2}]], closed: 1}],
    ["codex-bridge", "repo", "codex-bridge", {kids: [["ultrafast-mode", "ultrafast-mode"], ["retry-backoff", "retry-backoff"]], closed: 1}], "gap",
    ["design", "hash", "design"], ["devel", "hash", "devel", {n: 12}], ["core team", "lock", "core team"], "gap",
    ["iago", "face:Ia:rose on", "Iago", {n: 1}], ["hamlet", "face:KH:lapis away", "King Hamlet"], ["group", "faces:Co:larimar,Ot:amethyst", "Cordelia, Othello"],
  ];
  window.NAVDATA = NAV;
  const navRow = (id, glyph, label, t, sel, nested, live) => {
    const cls = ["row", nested ? "t" : "", id === sel ? "sel" : "", t.fresh || t.n ? "new-in" : ""].filter(Boolean).join(" ");
    const lead = !glyph ? "" : glyph.startsWith("faces:") ? faces(glyph.slice(6).split(","), "s14") : glyph.startsWith("face:") ? face(glyph.slice(5), "s20") : ic(glyph);
    const trail = t.mk ? mk(t.mk) : t.n && id !== sel ? `<span class="n fig">${fig(t.n)}</span>` : "";
    return `<button class="${cls}" data-id="${id}">${lead}<span class="lab">${label}</span>${trail}</button>`;
  };
  window.navHTML = (sel, over = {}, live) => {
    let h = `<div class="top"><button class="wk-name" aria-label="Workspace">Armada${ic("down")}</button><button class="c sq new" aria-label="New">${ic("plus")}</button></div>`;
    for (const r of NAV) {
      if (r === "gap") { h += '<div class="gap"></div>'; continue; }
      const [id, glyph, label] = r; const t = id in over ? over[id] || {} : r[3] || {};
      if (r[3] && r[3].kids) {
        const kids = r[3].kids, closed = r[3].closed;
        h += `<div class="grp" data-repo="${id}">${navRow(id, closed ? "repo" : "repo-open", label, closed ? t : {}, sel, false, live).replace('class="row', `aria-expanded="${!closed}" class="row repo`)}<div class="kids"${closed ? " hidden" : ""}>${kids.map(([kid, kl, kt]) => navRow(kid, null, kl, kid in over ? over[kid] || {} : kt || {}, sel, true, live)).join("")}</div></div>`;
      } else h += navRow(id, glyph, label, t, sel, false, live);
    }
    return h + `<button class="row me" data-id="me">${face("De:me", "s20")}<span class="lab">Desdemona</span></button>`;
  };
  const TABS = ["Chat", "README", "Files", "Changes"];
  // the checklist's standing home: a word, the marks of the steps the caption counts, one caption in a cell that holds
  // every form. The caption counts steps that are done. A place with no checklist has a plain heading in the same place.
  window.progHTML = (p, id = "prog") => !p ? "" : p.title ?
    `<button class="prog plain"${id ? ` id="${id}"` : ""} aria-label="${p.title}"><span class="w">${p.title}</span>${p.cap ? `<span class="pc"><span>${p.cap}</span></span>` : ""}</button>` :
    `<button class="prog${p.open ? " open" : ""}" id="${id}" aria-label="Checklist" aria-expanded="${!!p.open}"><span class="w">Checklist</span><span class="pm">${(p.marks || []).map((m) => mk(m, m === "live" ? "live" : "")).join("")}</span><span class="pc"><span>${/^\d+ of \d+$/.test(p.cap) ? `<span class="fig">${fig(p.cap.split(" ")[0])}</span> of ${p.cap.split(" ")[2]}` : p.cap}</span><i>Finished 00:00</i><i>12 of 12</i></span></button>`;
  // a value in a cell as wide as its longest form: the value can change and nothing beside it moves
  const cell = (cls, v, sizer) => `<span class="${cls} cell hcell"><span>${v}</span><i class="sz">${sizer}</i></span>`;
  window.headHTML = (cfg) => {
    const more = `<button class="c sq" aria-label="More">${ic("more")}</button>`;
    const path = cfg.path ? `<a class="repo" tabindex="0">${cfg.path}</a><span class="sl">/</span>` : "";
    if (cfg.kind === "home" || cfg.kind === "plain")   // Home, a channel with no repository, a direct message: the same two lines; a sentence stands where views would
      return `<header class="hd"><div class="l1">${path}<h1 class="title">${cfg.title}</h1></div><div class="l2"><p class="say">${cfg.say || ""}</p><span class="end">${more}${progHTML(cfg.checklist)}</span></div></header>`;
    const c = cfg.commit;
    const branch = `<button class="fact branch${cfg.distAtt ? " c-att" : ""}" aria-label="Branch">${cfg.distAtt ? mk("needs") : ic("branch")}${cfg.branch ? `<code>${cfg.branch}</code>` : ""}${cell("dist", cfg.dist || "", cfg.distAtt ? cfg.dist || "" : "88 ahead of main")}</button>`;
    const pr = cfg.pr ? `<button class="fact pr" aria-label="Pull request">${ic("pr")}<span>${cfg.pr.n}</span><span class="st${cfg.pr.ok ? " c-ok" : ""}">${cfg.pr.st}</span></button>` : "";
    const commit = `<button class="fact commit" aria-label="Latest commit"><span class="by">${c.who}</span><span class="subj">${c.subj}</span><code>${c.sha}</code>${cell("age", c.age, "88m")}</button>`;
    const tabs = cfg.tab ? `<div class="tabs" role="tablist">${TABS.map((t) => `<button class="tab${t === cfg.tab ? " on" : ""}" role="tab" data-tab="${t}" aria-selected="${t === cfg.tab}">${t}${t === "Changes" && cfg.changes ? `<span class="n">${cfg.changes}</span>` : ""}</button>`).join("")}</div>` : "";
    return `<header class="hd"><div class="l1">${path}<h1 class="title">${cfg.title}</h1>${branch}${pr}${commit}</div><div class="l2">${tabs}<span class="end">${more}${progHTML(cfg.checklist)}</span></div></header>`;   // More stands left of the checklist at every width
  };
  window.bandHTML = (b = {}) => { const r = b.run, t = b.typing;
    const ty = `<span class="ty${t ? "" : " off"}${t && r ? " chip" : ""}" id="ty">${!t ? "" : t.length > 1 ? faces(t.map((x) => x[0])) : face(t[0][0])}<p class="tyb${t && t.length === 1 ? " s-" + stone(t[0][0]) : ""}" id="tyb">${dots()}</p>${t ? `<span class="nm">${t.map((x) => x[1]).join(" and ")}</span>` : ""}</span>`;
    const run = `<span class="run${r ? "" : " off"}${r && r.att ? " att" : ""}${r && r.quiet ? " quiet" : ""}" id="run">${r && r.att ? mk("needs") : mk(r && r.mk || "live")}<span class="sg"><span class="sw cell" id="run-st"><span>${r ? r.state : "Working"}</span></span><span class="clock fig" id="clock">${fig(r ? r.clock || "" : "0:00")}</span></span>${!r || r.stop !== false ? `<button class="stop" id="stop" data-tip="Stop the run" data-key="⌘.">${ic("stop")}Stop</button>` : ""}<span class="act" id="act">${r && r.act || ""}</span></span>`;
    return `<div class="lane">${ty}${b.undo ? `<span class="undo">${b.undo}${b.noverb ? "" : `<button class="lk">Undo</button>`}</span>` : run}<button class="jump${b.jump ? "" : " off"}" id="jump" tabindex="-1">${ic("latest")}Jump to latest</button></div>`; };
  // the composer. c.plain: 1 a thread's field (flat, no Plus, no model; Onix only if it is in the thread), 2 a place with no agent.
  window.composerHTML = (c = {}, title = "", id = "cz") => { const verb = c.verb || "Send", armed = !!c.text && !c.off && c.focus !== false, on = c.agent !== false;
    const ph = c.placeholder || (verb === "Steer" ? "Steer Onix" : verb === "Answer" ? "Answer Onix" : verb === "Reply" ? "Reply in thread" : "Message " + title);
    const cur = c.focus === false || c.off ? "" : '<span class="cur"></span>';
    const body = (c.file ? `<span class="att">${ic("file")}${c.file}</span>` : "") + (c.text ? c.text.replace(/</g, "&lt;") + cur : cur);
    const to = c.plain === 2 || (c.plain === 1 && !c.onix) ? "" : `<button class="k to${on ? "" : " off"}" aria-pressed="${on}" aria-label="Onix answers" data-tip="${on ? "Onix answers this message" : "For people only"}">${agentFace("field")}${agentFace("field-off")}</button>`;
    const mode = c.plain ? "" : `<button class="k mode${on ? "" : " off"}${c.open ? " open" : ""}" aria-label="Model and effort" data-tip="Model and effort"><span class="slot"><span>${c.mode || "Sol Fast · High"}</span><i>Sol Fast · Medium</i></span>${ic("down")}</button>`;
    return `<div class="cz${c.plain === 1 ? " flat" : ""}${c.focus === false || c.off ? "" : " is-focus"}${c.text ? " has-text" : ""}${c.off ? " is-off" : ""}" ${id ? ` id="${id}"` : ""}><div class="sh">${c.plain === 1 ? "" : `<button class="out" aria-label="Attach" data-tip="Attach">${ic("plus")}</button>`}<div class="txw"><span class="ph cell"><span>${ph}</span></span><div class="tx" role="textbox" aria-multiline="true" aria-label="Message">${body}</div></div>
      ${to}${mode}<button class="disc dip${armed ? " armed" : ""}" aria-label="${verb}" data-tip="${verb}" data-key="Return"><span class="cell"><span>${verb}</span><i class="sz">Answer</i></span></button></div></div>`; };
  window.frame = (cfg) => {
    const body = document.body, pane = document.getElementById("pane"), side = document.getElementById("side");
    if (cfg.kind !== "home" && cfg.kind !== "plain") R.classList.add("wt");
    if (side) R.classList.add("col");                     // the column stands on every place that has one
    const nav = document.createElement("nav"); nav.innerHTML = navHTML(cfg.nav, cfg.navOver, cfg.live);
    const main = document.createElement("main");
    main.innerHTML = headHTML(cfg);
    main.appendChild(pane);
    if (cfg.composer) {
      const band = document.createElement("div"); band.className = "band"; band.innerHTML = bandHTML(cfg.band); main.appendChild(band);
      const dock = document.createElement("div"); dock.className = "dock"; dock.innerHTML = `<div class="lane">${composerHTML(cfg.composer, cfg.title)}</div>`; main.appendChild(dock);
    } else main.classList.add("bare");
    if (side) { const sh = document.createElement("div"); sh.className = "hdg"; sh.innerHTML = progHTML(cfg.checklist, "prog-side"); side.prepend(sh); if (cfg.side && cfg.side !== "out") { side.classList.add("folded"); R.classList.add("folded"); } main.appendChild(side); }
    for (const x of document.querySelectorAll("body > .float")) main.appendChild(x);
    body.prepend(nav); body.appendChild(main);
    hydrate(document);
    // content that runs on above the pane fades out under the header over 20 px: nothing is cut hard by its edge.
    // The prototype moves the same variable with the first 20 px scrolled.
    if (pane && pane.classList.contains("feed") && !window.TOPICS) { const lane = pane.querySelector(".lane") || pane, cut = () => pane.style.setProperty("--cut", lane.getBoundingClientRect().top < pane.getBoundingClientRect().top - 1 ? "20px" : "0px");
      new ResizeObserver(cut).observe(lane); new ResizeObserver(cut).observe(pane); cut(); }
    if (side) { const out = side.querySelector(":scope > .out"); if (out) { new ResizeObserver(() => fitLists(side)).observe(out); document.fonts.ready.then(() => fitLists(side)); } fitLists(side); }
    const subj = main.querySelector(".hd .commit .subj"); if (subj) new ResizeObserver(() => wordcut(subj)).observe(subj.parentElement);
    // a sheet that grows from its trigger is placed by it
    for (const x of main.querySelectorAll("[data-anchor]")) { const a = main.querySelector(x.dataset.anchor); if (!a) continue; const r = a.getBoundingClientRect(), m = main.getBoundingClientRect(); x.style.right = Math.round(m.right - r.right - (+x.dataset.dx || 0)) + "px"; x.style.bottom = Math.round(m.bottom - r.top + (+x.dataset.dy || 8)) + "px"; }
  };
  document.addEventListener("DOMContentLoaded", () => { const cfg = document.body.dataset.cfg; if (cfg) frame(JSON.parse(cfg)); else hydrate(document); });
})();
