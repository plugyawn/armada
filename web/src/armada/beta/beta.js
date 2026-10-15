/* INTERIM BETA, served at /beta/ (zerver/views/armada_beta.py) and deleted with this directory when the rebuild's
   conversation lands. The design prototype's interface (kit/: mock.css, kit.js, motion.js, sound.js) on this server's
   real data, grown from var/armada-overhaul/preview/. Nothing here is scripted: every row, mark, count and word on
   screen comes from the server, or from what the person just did and the server then confirms.
   Signed in by the session: reads and writes go to /json/ with the session cookie, and every write carries the
   page's CSRF token. No API key exists on this page.
   Rules kept from the prototype: read, write the whole new state, then ask the motion kit to release the difference;
   every handler ends with the field ready; nothing moves that the person did not act on. */
(function () {
"use strict";
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const R = document.documentElement, Q = new URLSearchParams(location.search), T = M.T;
const store = {get: (k) => { try { return localStorage.getItem("armada-beta-" + k); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem("armada-beta-" + k, v); } catch (e) {} }, del: (k) => { try { localStorage.removeItem("armada-beta-" + k); } catch (e) {} }};
window.TOPICS = {};                                        /* tells the kit this page scrolls its feed itself */

/* ---------- the server ---------- */
const CSRF = ($('input[name="csrfmiddlewaretoken"]') || {}).value || "";   /* the session's token, written into the page by the server */
const DATA = ($("#beta-data") || {dataset: {}}).dataset;   /* what the page cannot ask for (zerver/views/armada_beta.py) */
const QUESTION_TIMEOUT = +DATA.questionTimeout || 600;    /* how long a run waits for an answer, then goes on with defaults (ARMADA_QUESTION_TIMEOUT_SECONDS) */
const LIVE_RUN_IDS = (() => { try { return JSON.parse(DATA.liveRunIds || "[]"); } catch (e) { return []; } })();   /* the live runs in my channels at load: where Home starts */
let runtimeCfg = null;                                     /* the server's runtime settings, from armada/runs: the service tier is server-wide */
const stats = {sends: [], firstWords: [], runs: [], events: 0, api: 0, log: []};
async function api(method, path, params = {}, o = {}) {
  const sp = new URLSearchParams(); for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) sp.set(k, typeof v === "string" ? v : JSON.stringify(v));
  let url = "/json/" + path, body = o.body; if (method === "GET" || method === "DELETE") { const s = sp.toString(); if (s) url += "?" + s; } else if (!body) body = sp;
  stats.api++;
  const headers = method === "GET" ? {} : {"X-CSRFToken": CSRF};
  let r; try { r = await fetch(url, {method, body, headers, credentials: "same-origin", signal: o.signal}); } catch (e) { throw Object.assign(new Error("The server cannot be reached"), {net: true}); }
  /* the server's rate limit (200 requests a minute a person): a read waits as long as the server says and asks again, up to
     three times, instead of showing "API usage exceeded rate limit" or calling the server offline. A write is not repeated. */
  if (r.status === 429 && method === "GET" && (o.tries || 0) < 3 && !(o.signal && o.signal.aborted)) { let wait = parseFloat(r.headers.get("Retry-After"));
    if (!(wait >= 0)) { try { wait = +(await r.json())["retry-after"]; } catch (e) {} } stats.rateLimited = (stats.rateLimited || 0) + 1;
    await sleep(Math.min(30, Math.max(0.5, wait || 1)) * 1000 + Math.random() * 250); return api(method, path, params, {...o, tries: (o.tries || 0) + 1}); }
  if (r.status === 401) { signedOut(); throw Object.assign(new Error("Signed out"), {status: 401}); }
  let j; try { j = await r.json(); } catch (e) { throw Object.assign(new Error(`The server answered ${r.status}`), {status: r.status, net: r.status >= 500}); }
  if (j.result !== "success") throw Object.assign(new Error(j.msg || "The request failed"), {code: j.code, status: r.status});
  return j;
}

/* ---------- state ---------- */
let ME = 0, meName = "", queue = null, lastEvent = -1, skew = 0;
const people = new Map(), streams = new Map(), repoStreams = new Set(), armadaBots = new Set();
const topicsOf = new Map();                                /* stream id -> [{name, max_id}] in the order first shown; null while loading */
const worktrees = new Map();                               /* stream id -> Set of lower-cased topic names that have a worktree */
const unread = new Map();                                  /* place key -> Set of message ids */
const marks = new Map();                                   /* place key -> "needs" | "live" | "queued" | "failed" | "paused": a run's mark in the nav */
const places = new Map();                                  /* place key -> what is known of it */
const msgIndex = new Map();                                /* message id -> its place's cache */
const expanded = new Set(JSON.parse(store.get("open") || "[]")), allKids = new Set();
let dms = [], presences = {}, place = null, P = null, tab = "Chat", localSeq = 0, agentMention = "@**Onix**";
const now = () => Date.now() + skew;
const STONES = ["lapis", "larimar", "rose", "amethyst", "moss", "flint"];
/* a person's stone: mine is my colour; with a partner, the stone of its type (the server says which); else one by user id */
const stoneOf = (id) => { if (id === ME) return "me"; const p = partnerOf(id); return p && p.stone ? p.stone : STONES[id % STONES.length]; };
const person = (id) => people.get(id) || {id, name: "Someone", email: ""};
const isOnix = (id) => armadaBots.has(id);
const nameOf = (id) => (isOnix(id) ? "Onix" : person(id).name);
/* a face's initials: letters and figures of any script (a name in Devanagari keeps its first letter with its vowel sign),
   cut by grapheme, so no name is drawn as "?" while it has a letter */
const graphemes = (t) => { try { return [...new Intl.Segmenter(undefined, {granularity: "grapheme"}).segment(t)].map((x) => x.segment); } catch (e) { return [...t]; } };
const initials = (name) => { const w = name.replace(/[^\p{L}\p{N}\p{M} ]/gu, "").split(/\s+/).filter(Boolean); if (!w.length) return "?"; const g = graphemes(w[0]);
  if (!/^[\p{Script=Latin}0-9]/u.test(w[0])) return g[0];   /* another script: one letter, which fills the disc as two Latin letters do */
  return w.length > 1 && /^\p{Lu}/u.test(w[1]) ? g[0].toUpperCase() + graphemes(w[1])[0] : g[0].toUpperCase() + (g[1] && /^[\p{Script=Latin}0-9]/u.test(g[1]) ? g[1] : ""); };
const presOf = (id) => { const p = presences[person(id).email], a = p && p.aggregated; if (!a || now() / 1000 - a.timestamp > 300) return ""; return a.status === "active" ? "on" : a.status === "idle" ? "away" : ""; };
/* a face: the partner's sprite (partners section, below) wherever one is drawn at 18 px or more; else initials on the stone,
   and Onix's boulder disc. Every face carries its person, so a partner that arrives or evolves is redrawn in place. */
const faceSize = (cls) => (/s14/.test(cls || "") ? 14 : /s18/.test(cls || "") ? 18 : /s20/.test(cls || "") ? 20 : 28);
const faceOf = (id, cls, pres) => { const size = faceSize(cls), p = size >= 18 ? partnerOf(id) : null;
  if (p && !brokenSprites.has(spriteSrc(p.species, p.shiny))) { const h = pkFace(p, size, cls, pres, id); if (h) return h; }
  if (isOnix(id)) return agentFace(size).replace("<svg ", `<svg data-uid="${id}" data-fs="mark" `);
  return face(`${initials(person(id).name)}:${stoneOf(id)}${pres ? " " + pres : ""}`, cls).replace("<i ", `<i data-uid="${id}" data-fs="i" `); };
const tkey = (sid, topic) => `s:${sid}:${topic.toLowerCase()}`, dkey = (ids) => "d:" + [...ids].sort((a, b) => a - b).join(",");
const topicLabel = (t) => t || "general chat";

/* ---------- time ---------- */
const p2 = (n) => String(n).padStart(2, "0");
const hhmm = (ts) => { const d = new Date(ts * 1000); return `${d.getHours()}:${p2(d.getMinutes())}`; };
const dayKey = (ts) => { const d = new Date(ts * 1000); return d.getFullYear() * 10000 + d.getMonth() * 100 + d.getDate(); };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const dayLabel = (ts) => { const d = new Date(ts * 1000), t = new Date(now()), days = Math.round((new Date(t.getFullYear(), t.getMonth(), t.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  return days === 0 ? "Today" : days === 1 ? "Yesterday" : days > 1 && days < 7 ? DOW[d.getDay()] : `${d.getDate()} ${MON[d.getMonth()]}${d.getFullYear() !== t.getFullYear() ? " " + d.getFullYear() : ""}`; };
const shortDay = (ts) => { const d = new Date(ts * 1000), days = (now() - d) / 864e5; return days < 6 ? DOW[d.getDay()].slice(0, 3) : `${d.getDate()} ${MON[d.getMonth()]}`; };
const age = (ts) => { const s = Math.max(0, now() / 1000 - ts); return s < 60 ? "now" : s < 3600 ? Math.floor(s / 60) + "m" : s < 172800 ? Math.floor(s / 3600) + "h" : Math.floor(s / 86400) + "d"; };
const dur = (s) => { s = Math.max(1, Math.round(s)); return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${p2(s % 60)}s` : `${Math.floor(s / 3600)}h ${p2(Math.floor(s % 3600 / 60))}m`; };
const mmss = (s) => (s < 600 ? `${Math.floor(s / 60)}:${p2(s % 60)}` : s < 6000 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h`);

/* ---------- the rule: a server-rendered message becomes bubbles and blocks ----------
   The message's rendered HTML is read as a list of its top-level elements. Each is prose or a block.
   A block is: a code block (div.codehilite or pre), a table, an image (div.message_inline_image), a spoiler, a link
   preview (div.message_embed), display maths. Everything else is prose: paragraphs, lists, quotes, headings.
   - A person's message: each run of consecutive prose elements is one bubble; each block stands as a block in the
     turn, between the bubbles, at the lane's width. A message that is one to three emoji and nothing else is jumbo.
   - Onix's message: each run of prose is ambient text on the page (no bubble); blocks are the same blocks.
   Inside prose: an emoji span becomes its character, a mention becomes the person's name in the mention weight (the
   agent's is always "Onix"), a link opens in a new tab, a timestamp is printed in local time. Code keeps the server's
   highlighting, mapped onto the four syntax inks the design has (keyword, string, number, comment). */
const PYG = {}; "k kn kd kc kr kt kp ow".split(" ").forEach((c) => (PYG[c] = "sx-k")); "s s1 s2 sa sb sc sd se sh si sx sr ss dl".split(" ").forEach((c) => (PYG[c] = "sx-s")); "m mi mf mh mo mb il".split(" ").forEach((c) => (PYG[c] = "sx-n")); "c c1 cm ch cp cs cpf".split(" ").forEach((c) => (PYG[c] = "sx-c"));
const tpl = document.createElement("template");
const BLOCK = (n) => n.matches("div.codehilite, pre, table, div.message_inline_image, div.spoiler-block, div.message_embed, div.inline-preview-twitter, div.youtube-video, div.message_inline_video, .katex-display") || (n.tagName === "P" && n.children.length === 1 && n.firstElementChild.matches(".katex-display") && !n.textContent.replace(n.firstElementChild.textContent, "").trim());
function clean(root) {
  $$("script, style, iframe, object, .message_length_controller, .message_expander, .message_condenser", root).forEach((x) => x.remove());
  $$("span.emoji", root).forEach((s) => { const m = /emoji-([0-9a-f-]+)/.exec(s.className); if (!m) return; let ch = ""; try { ch = String.fromCodePoint(...m[1].split("-").map((h) => parseInt(h, 16))); } catch (e) { return; } const e = el("span", "em"); e.textContent = ch; e.title = s.getAttribute("title") || ""; s.replaceWith(e); });
  $$(".user-mention, .user-group-mention, .topic-mention", root).forEach((s) => { const id = +s.dataset.userId, e = el("span", "mention" + (id === ME ? " you" : "")); e.textContent = id && people.has(id) ? nameOf(id) : s.textContent.replace(/^@/, ""); s.replaceWith(e); });
  $$("a[href]", root).forEach((a) => { const h = a.getAttribute("href"); if (h[0] === "#") a.href = "/" + h; a.target = "_blank"; a.rel = "noopener noreferrer"; });   /* a link to a narrow opens it in the old interface */
  $$("time[datetime]", root).forEach((t) => { const d = new Date(t.getAttribute("datetime")); if (!isNaN(d)) t.textContent = `${dayLabel(d / 1000)} ${hhmm(d / 1000)}`; });
  $$("img:not(.emoji)", root).forEach((i) => { i.removeAttribute("width"); i.removeAttribute("height"); i.loading = "lazy"; });   /* uploads load with the session's cookie */
  return root;
}
function codeHTML(pre) { const code = pre.querySelector("code") || pre; $$("span", code).forEach((s) => { const c = PYG[s.className]; if (c) s.className = c; else s.replaceWith(...s.childNodes); }); return code.innerHTML.replace(/\n$/, ""); }
function blockPart(n) {
  if (n.matches("div.codehilite, pre")) return {t: "pre", html: codeHTML(n.matches("pre") ? n : n.querySelector("pre") || n)};
  if (n.matches("table")) { $$("th, td", n).forEach((c) => { if (/right/.test(c.getAttribute("style") || "")) c.className = "r"; c.removeAttribute("style"); }); return {t: "tbl", html: n.outerHTML}; }
  if (n.matches("div.message_inline_image")) { const i = n.querySelector("img"); if (i) return {t: "pic", html: i.outerHTML}; }
  return {t: "blk", html: n.innerHTML};
}
const QUESTION = /needs your input to continue/;
/* one option a list item: `label` — what it means. The label is what the run asked for, so it is what an answer says. */
function askPart(ul) { const opts = $$(":scope > li", ul).map((li) => { const c = li.querySelector("code"), label = (c ? c.textContent : li.textContent.split(" — ")[0]).trim(), desc = li.textContent.slice(li.textContent.indexOf(label) + label.length).replace(/^\s*[—-]\s*/, "").trim(); return {label, desc}; });
  return {t: "ask", html: opts.map((o, i) => `<button class="opt" data-answer="${esc(o.label)}"${o.desc ? ` data-tip="${esc(o.desc)}"` : ""}><span class="k">${i + 1}</span><span class="t">${esc(o.label)}</span>${o.desc ? `<span class="d">${esc(o.desc)}</span>` : ""}</button>`).join("")}; }
const JUMBO = /^(?:\p{Extended_Pictographic}|\p{Emoji_Modifier}|‍|️|[\u{1f1e6}-\u{1f1ff}]){1,12}$/u;
function partsOf(m) {
  const onix = isOnix(m.sender_id), out = []; let run = [];
  if (onix && /^·\s*Working\s*$/.test((m.content || "").replace(/<[^>]+>/g, "").trim())) return out;   /* the server's placeholder: Onix has no words yet */
  tpl.innerHTML = m.content || ""; const root = clean(tpl.content);
  $$("code", root).forEach((x) => { if (!x.closest("pre") && x.textContent.length <= 32) x.classList.add("nw"); });   /* a short flag or path does not break at its own hyphen */
  if (onix) [...root.children].filter((x) => x.tagName === "P").forEach((x) => { if (x.childNodes.length === 1 && x.firstChild.tagName === "STRONG") { const h = el("h4"); h.append(...x.firstChild.childNodes); x.replaceWith(h); } });   /* "**What changed**" alone on its line is a heading. The fragment's own children: ":scope" matches nothing in a DocumentFragment */
  const flush = () => { if (!run.length) return;
    if (onix) { run.forEach((n) => { if (/^H[12]$/.test(n.tagName)) { const h = el("h3"); h.append(...n.childNodes); n.replaceWith(h); run[run.indexOf(n)] = h; } else if (/^H[3-6]$/.test(n.tagName)) { const h = el("h4"); h.append(...n.childNodes); n.replaceWith(h); run[run.indexOf(n)] = h; } });
      const box = el("div"); box.append(...run); $$("li", box).forEach((li) => { const s = el("span"); s.append(...li.childNodes); li.append(s); }); out.push({t: "ans", html: box.innerHTML}); }
    else if (run.length === 1 && run[0].tagName === "P") { const p = run[0], txt = p.textContent.trim(), ems = $$(".em, img.emoji", p).length; out.push({t: "b", tag: "p", html: p.innerHTML, jumbo: ems >= 1 && ems <= 3 && JUMBO.test(txt.replace(/\s/g, ""))}); }
    else { const box = el("div"); box.append(...run); out.push({t: "b", tag: "div", html: box.innerHTML}); }
    run = []; };
  const asking = onix && QUESTION.test(root.textContent);
  for (const n of [...root.children]) { if (n.tagName === "HR") continue;
    if (asking && n.tagName === "UL") { flush(); out.push(askPart(n)); continue; }   /* the question's options are buttons, under the question */
    if (asking && n.tagName === "P" && QUESTION.test(n.textContent)) { n.textContent = "Onix needs your answer to go on."; }
    if (asking && n.tagName === "P" && /^_?Reply in this thread to answer/.test(n.textContent.trim())) { const m_ = /waiting up to (\d+) minutes/.exec(n.textContent); n.className = "askcap"; n.innerHTML = `<em>Press an answer, or write your own to @Onix. The run waits${m_ ? ` up to ${m_[1]} minutes` : ""}.</em>`; }   /* in words, the answer names Onix (or the place's switch is on): people talking here do not answer it */
    if (BLOCK(n)) { flush(); out.push(blockPart(n)); } else run.push(n); }
  flush();
  if (!out.length && !onix) { const t = (root.textContent || "").trim(); out.push({t: "b", tag: "p", html: esc(t || " ")}); }
  return out;
}
const plainOf = (html) => { tpl.innerHTML = html || ""; return tpl.content.textContent; };
function partEl(p, m) {
  let e;
  if (p.t === "b") { const b = el(p.tag, "b" + (p.jumbo ? " jumbo" : ""), p.html); e = el("div", "bl"); e.append(b); }
  else if (p.t === "ans") { e = el("div", "ans"); setAns(e, p.html); }
  else if (p.t === "pre") e = el("pre", "blk", p.html);
  else if (p.t === "tbl") e = el("div", "blk tbl", p.html);
  else if (p.t === "ask") e = el("div", "blk ask", p.html);
  else if (p.t === "pic") e = el("div", "blk pic", p.html);
  else e = el("div", "blk", p.html);
  e.dataset.mid = m.id; e.dataset.part = p.t; e._src = p.html; loadPics(e); return e;
}
function setAns(ans, html) { ans.innerHTML = html; for (const c of ans.children) c._src = c.innerHTML; }
function loadPics() {}
/* the local echo: the words as typed, with the Markdown the field's words usually carry, cut into the same parts the
   server's rendering will be cut into (a fenced block is a block, a list or a quote stays in its bubble), so the
   server's copy replaces it without changing its shape. The server's rendering replaces it only where it differs. */
const inlineEcho = (t) => esc(t).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/\[([^\]\n]+)\]\((\/user_uploads\/[^)\s]+|https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>');
function echoHTML(text) {
  const out = [], lines = text.replace(/\r/g, "").split("\n"); let i = 0;
  const para = (ls) => { if (!ls.length) return; let k = 0; while (k < ls.length) { const blk = [];
      if (/^\s*[-*+] /.test(ls[k])) { while (k < ls.length && /^\s*[-*+] /.test(ls[k])) blk.push("<li>" + inlineEcho(ls[k++].replace(/^\s*[-*+] /, "")) + "</li>"); out.push("<ul>" + blk.join("") + "</ul>"); continue; }
      if (/^\s*\d+[.)] /.test(ls[k])) { while (k < ls.length && /^\s*\d+[.)] /.test(ls[k])) blk.push("<li>" + inlineEcho(ls[k++].replace(/^\s*\d+[.)] /, "")) + "</li>"); out.push("<ol>" + blk.join("") + "</ol>"); continue; }
      if (/^\s*> ?/.test(ls[k])) { while (k < ls.length && /^\s*> ?/.test(ls[k])) blk.push(inlineEcho(ls[k++].replace(/^\s*> ?/, ""))); out.push("<blockquote><p>" + blk.join("<br>") + "</p></blockquote>"); continue; }
      while (k < ls.length && !/^\s*([-*+] |\d+[.)] |> ?)/.test(ls[k])) blk.push(inlineEcho(ls[k++])); out.push("<p>" + blk.join("<br>") + "</p>"); } };
  let buf = [];
  while (i < lines.length) { const l = lines[i], f = /^\s*(```+|~~~+)/.exec(l);
    if (f) { para(buf); buf = []; const code = []; i++; while (i < lines.length && !lines[i].trim().startsWith(f[1])) code.push(lines[i++]); i++; out.push(`<div class="codehilite"><pre><span></span><code>${esc(code.join("\n"))}\n</code></pre></div>`); continue; }
    if (!l.trim()) { para(buf); buf = []; i++; continue; }
    const img = /^\[([^\]]+)\]\((\/user_uploads\/[^)\s]+\.(?:png|jpe?g|gif|webp))\)$/i.exec(l.trim());
    if (img) { buf.push(l); para(buf); buf = []; out.push(`<div class="message_inline_image"><a href="${esc(img[2])}"><img src="${esc(img[2])}"></a></div>`); i++; continue; }
    buf.push(l); i++; }
  para(buf);
  return out.join("") || "<p></p>";
}

/* ---------- the frame ---------- */
let main, feed, lane, nav, navScroll, navFixed, side, outline, hd, band, cz, tx, sh, go, goc, ph, toBtn, modeBtn, panes;
let pinned = true, lastSet = 0, verb = "Send";
/* Onix answers what I send in a place only where I turned it on there: off until then, kept in this browser for each place,
   never on outside a repository. Words that name @Onix address it whatever the switch says. */
const onixKey = (p) => "onix:" + p.key;
const onixHere = () => !!place && place.kind === "topic" && !!place.repo && store.get(onixKey(place)) === "1";
/* Home's Answer: the next message in that place answers the run that waits, once; the switch stays as it was. Esc, leaving the
   place, or the question ending gives it up. */
const answering = () => !!P && !!P.answering && !!waitingRun(P);
const onixNow = () => onixHere() || answering();
const dist = () => feed.scrollHeight - feed.scrollTop - feed.clientHeight;
const pin = () => { feed.scrollTop = 1e7; lastSet = feed.scrollTop; };
const edge = () => { const y = feed.scrollTop; feed.classList.toggle("cut", y > 2); feed.style.setProperty("--cut", Math.min(20, Math.round(y)) + "px"); };
const firstRow = () => lane.firstElementChild, lastCl = () => lane.lastElementChild;
function haltLane() { const y = (M.current(lane)[0].y || 0) + (M.current(lane, "pane")[0].y || 0); M.halt(lane); M.halt(lane, "pane"); if (Math.abs(y) > 0.5) feed.scrollTop -= y; }
function show(e, on) { if (!e) return; if (e === band.jump) band.el.classList.toggle("more", on); if (on === !e.classList.contains("off")) return; e.inert = !on; if (on) { e.classList.remove("off"); M.appear(e); } else { M.fade(e, 0); e.classList.add("off"); e.style.opacity = ""; } }
const syncJump = () => show(band.jump, tab === "Chat" && dist() >= 4);
/* a write that adds to the feed: older rows are shown where they were and released to where they are. One forced layout. */
function grown(write, token = "move", o = {}) {
  const ref = firstRow(), before = ref ? M.rest(ref).top : 0; const out = write(); hugAll();
  if (pinned) pin();
  const d = ref && ref.isConnected ? before - M.rest(ref).top : 0;
  if (d) M.release(lane, {y: d}, token, o);
  edge(); syncJump(); return out;
}
/* the field is ready after everything: the main field, or the thread field I am writing in, or the palette's */
const ready = () => { if ($("#lab.open:focus-within") || pal || dialogOpen || (pop && pop.contains(document.activeElement))) return; const f = threadTx && threadTx.isConnected && tab === "Chat" ? threadTx : place && place.kind !== "home" ? tx : null; if (!f) return; if (document.activeElement !== f) f.focus({preventScroll: true}); if (f.textContent && document.activeElement === f) { const s = getSelection(); if (!f.contains(s.anchorNode)) { s.selectAllChildren(f); s.collapseToEnd(); } } };
function press(root, sel, fn) {
  root.addEventListener("pointerdown", (e) => { if (e.button !== 0 || e.ctrlKey) return; const t = e.target.closest(sel); if (!t || !root.contains(t) || t.disabled) return; t._pd = performance.now(); fn(t, e, true); });
  root.addEventListener("click", (e) => { const t = e.target.closest(sel); if (!t || !root.contains(t) || t.disabled) return; if (t._pd && performance.now() - t._pd < 1500) { t._pd = 0; return; } t._pd = 0; fn(t, e, false); });
}

/* ---------- navigation ---------- */
const subs = () => [...streams.values()].sort((a, b) => (repoStreams.has(b.stream_id) - repoStreams.has(a.stream_id)) || a.name.localeCompare(b.name));
const countOf = (key) => (unread.get(key) || {size: 0}).size;
const streamCount = (sid) => { let n = 0; for (const [k, s] of unread) if (k.startsWith(`s:${sid}:`)) n += s.size; return n; };
const MARK_RANK = {needs: 5, live: 4, queued: 3, failed: 2, paused: 1};
/* a live run's mark: waiting on me (the balloon), working (the ball, or the dot), or queued behind another (the egg, or the ring) */
const runMark = (r, status = r.status) => (r.waiting ? "needs" : status === "queued" ? "queued" : "live");
const LIVE_MARKS = new Set(["needs", "live", "queued"]);
const streamMark = (sid) => { let m = null; for (const [k, v] of marks) if (k.startsWith(`s:${sid}:`) && (!m || MARK_RANK[v] > MARK_RANK[m])) m = v; return m; };
const trailHTML = (key, sel) => { const m = marks.get(key), n = countOf(key); return m ? mk(m) : n && !sel ? `<span class="n fig">${fig(n)}</span>` : ""; };   /* one trailing thing by priority: needs you, failed, live, unread */
function rowHTML(key, cls, lead, label, trail, extra = "") { return `<button class="row ${cls}" data-k="${esc(key)}"${extra}>${lead}<span class="lab">${esc(label)}</span>${trail}</button>`; }
function drawNav() {
  const y = navScroll.scrollTop, cur = place && place.key; let h = "";
  for (const s of subs()) { const sid = s.stream_id, repo = repoStreams.has(sid), open = expanded.has(sid), list = topicsOf.get(sid);
    const glyph = repo ? (open ? "repo-open" : "repo") : s.invite_only ? "lock" : "hash", cm = streamMark(sid), cn = streamCount(sid);
    const trail = open ? "" : cm ? mk(cm) : cn ? `<span class="n fig">${fig(cn)}</span>` : "";
    h += `<div class="grp" data-sid="${sid}">` + rowHTML("c:" + sid, (repo ? "repo" : "chan") + (!open && cn ? " new-in" : "") + (!open && cur && place.stream_id === sid ? " sel" : ""), ic(glyph), s.name, trail, ` aria-expanded="${open}"`);
    if (open) { h += '<div class="kids">';
      if (!list) h += '<span class="none">Loading</span>';
      else { let kids = list; const cap = 12; if (!allKids.has(sid) && kids.length > cap) { kids = kids.slice(0, cap); if (cur && place.stream_id === sid && !kids.some((t) => tkey(sid, t.name) === cur)) { const c = list.find((t) => tkey(sid, t.name) === cur); if (c) kids.push(c); } }
        if (cur && place.stream_id === sid && !list.some((t) => tkey(sid, t.name) === cur)) kids = [{name: place.topic, max_id: 0}, ...kids];
        for (const t of kids) { const k = tkey(sid, t.name), sel = k === cur; h += rowHTML(k, "t" + (sel ? " sel" : "") + (countOf(k) || marks.get(k) === "failed" ? " new-in" : ""), "", topicLabel(t.name), trailHTML(k, sel)); }
        if (!kids.length) h += '<span class="none">No topics yet</span>';
        if (list.length > kids.length) h += `<button class="mo" data-more="${sid}">${list.length - kids.length} more</button>`; }
      h += "</div>"; }
    h += "</div>"; }
  if (dms.length) h += '<div class="gap"></div>';
  for (const d of dms) { const k = dkey(d.user_ids), sel = k === cur, ids = d.user_ids.length ? d.user_ids : [ME];
    const lead = ids.length > 1 ? `<span class="avs">${ids.slice(0, 2).map((i) => faceOf(i, "s14")).join("")}</span>` : faceOf(ids[0], "s20", presOf(ids[0]));
    h += rowHTML(k, (sel ? "sel" : "") + (countOf(k) ? " new-in" : ""), lead, (ids.length === 1 ? nameOf(ids[0]) : ids.map((i) => nameOf(i).split(/[ ,]/)[0]).join(", ")) + (d.user_ids.length ? "" : " (you)"), trailHTML(k, sel)); }
  navScroll.innerHTML = h; host(navScroll); navScroll.scrollTop = y; navMore();
}
/* the nav's list fades under the me row while there is more of it below */
const navMore = () => navScroll.classList.toggle("more", navScroll.scrollTop + navScroll.clientHeight < navScroll.scrollHeight - 1);
const rowOf = (key) => $$(".row", navScroll).find((r) => r.dataset.k === key);
function drawTrail(key) { const r = rowOf(key); if (!r) { if (key.startsWith("s:")) { const sid = +key.split(":")[1]; if (!expanded.has(sid)) drawNav(); } return; }
  const sel = r.classList.contains("sel"), html = trailHTML(key, sel), old = $(":scope > .mk, :scope > .n", r), was = old ? old.outerHTML : "";
  r.classList.toggle("new-in", !!countOf(key) || marks.get(key) === "failed");
  if (old && html && old.matches(".n") && /class="n fig"/.test(html)) { M.figure(old, countOf(key), {quiet: true}); return; }
  if (old && html && old.matches(".mk") && /class="mk/.test(html)) { if (!old.classList.contains(marks.get(key))) M.cross(old, html); return; }
  if (was === html) return; if (old) { old.classList.remove("mk", "n"); old.inert = true; M.leave(old, () => old.remove()); }
  if (html) { r.insertAdjacentHTML("beforeend", html); M.appear(r.lastElementChild); } }
const topicLoads = new Map();
/* a channel's topics, once */
function loadTopics(sid) { if (!topicLoads.has(sid)) topicLoads.set(sid, loadTopics_(sid)); return topicLoads.get(sid); }
async function loadTopics_(sid) {
  if (topicsOf.has(sid)) return; topicsOf.set(sid, null);
  try { const r = await api("GET", `users/me/${sid}/topics`); const list = r.topics.map((t) => ({name: t.name, max_id: t.max_id}));
    if (repoStreams.has(sid)) { const w = await api("GET", "armada/workspace-topics", {stream: streams.get(sid).name}).catch(() => ({armada_workspace_topics: []})); const set = new Set(); for (const t of w.armada_workspace_topics) { set.add(t.topic.toLowerCase()); if (!list.some((x) => x.name.toLowerCase() === t.topic.toLowerCase())) list.push({name: t.topic, max_id: 0}); } worktrees.set(sid, set); }
    topicsOf.set(sid, list); } catch (e) { topicsOf.set(sid, []); }
  if (expanded.has(sid) || (place && place.stream_id === sid)) drawNav();
}
/* which worktrees have a run going: the page was given the live runs in my channels at load (zerver/views/armada_beta.py),
   read here in one request for every twenty; run events keep them current after that. For each, the last few messages
   of its worktree say whether it waits on a question the server's live state no longer holds. */
async function scanLive() {
  for (let i = 0; i < LIVE_RUN_IDS.length; i += 20) { let r;
    try { r = await api("GET", "armada/runs", {run_ids: LIVE_RUN_IDS.slice(i, i + 20)}); } catch (e) { continue; }
    if (r.runtime_config) runtimeCfg = r.runtime_config;
    await Promise.all(r.armada_runs.map(adoptLive)); } }
async function adoptLive(r0) { if (r0.session_kind === "side" || !liveNow(r0)) return; const s = [...streams.values()].find((x) => x.name.toLowerCase() === String(r0.stream || "").toLowerCase()); if (!s) return;
  const k = tkey(s.stream_id, r0.topic || ""), c = places.get(k); let list = c && c.runsLoaded ? c.runs : runsSeen.get(k); if (!list) runsSeen.set(k, (list = []));
  let r = list.find((x) => x.id === r0.id); if (!r) { r = askState(r0); list.unshift(r); }   /* known already (the place was opened, or an event came): that copy is newer */
  if (c && c.loaded) inferAsk(r, c.msgs); else { try { const mr = await api("GET", "messages", {narrow: [{operator: "channel", operand: s.stream_id}, {operator: "topic", operand: r.topic}], anchor: "newest", num_before: 6, num_after: 0, apply_markdown: true, client_gravatar: false}); inferAsk(r, mr.messages); } catch (e) {} }
  if (!liveNow(r)) return; const want = runMark(r); if (marks.get(k) !== want) { marks.set(k, want); drawTrail(k); } }
function toggleStream(sid) { if (expanded.has(sid)) expanded.delete(sid); else { expanded.add(sid); loadTopics(sid); } store.set("open", JSON.stringify([...expanded])); drawNav(); }

/* ---------- places ---------- */
function placeFromKey(key) {
  if (key === "home") return HOME;
  if (key === "partner") return pack ? PARTNER : null;
  if (key.startsWith("s:")) { const i = key.indexOf(":", 2), sid = +key.slice(2, i), s = streams.get(sid); if (!s) return null; const lower = key.slice(i + 1), known = (topicsOf.get(sid) || []).find((t) => t.name.toLowerCase() === lower); return {key, kind: "topic", stream_id: sid, stream: s.name, topic: known ? known.name : lower, repo: repoStreams.has(sid)}; }
  if (key.startsWith("d:")) { const ids = key.slice(2).split(",").filter(Boolean).map(Number); return {key, kind: "dm", ids, repo: false}; }
  return null;
}
const hashOf = (p) => (p.kind === "home" ? "#home" : p.kind === "partner" ? "#partner" : p.kind === "topic" ? `#c/${p.stream_id}/${encodeURIComponent(p.topic)}` : `#d/${p.ids.join(",")}`);
function placeFromHash() { const h = location.hash; if (h === "#home") return HOME; if (h === "#partner") return pack ? PARTNER : null; let m = /^#c\/(\d+)\/(.*)$/.exec(h); if (m && streams.has(+m[1])) { let t = m[2]; try { t = decodeURIComponent(t); } catch (e) {} const p = placeFromKey(tkey(+m[1], t)); if (p) p.topic = (topicsOf.get(+m[1]) || []).find((x) => x.name.toLowerCase() === t.toLowerCase())?.name ?? t; return p; }
  m = /^#d\/([\d,]*)$/.exec(h); if (m) return placeFromKey(dkey(m[1].split(",").filter(Boolean).map(Number))); return null; }
const cacheOf = (p) => { let c = places.get(p.key); if (!c) { c = {place: p, msgs: [], byId: new Map(), loaded: false, oldest: false, loading: false, runs: [], ctx: null, typers: new Map(), open: new Set()}; places.set(p.key, c); } return c; };
const narrowOf = (p) => (p.kind === "topic" ? [{operator: "channel", operand: p.stream_id}, {operator: "topic", operand: p.topic}] : [{operator: "dm", operand: p.ids.length ? p.ids : [ME]}]);
const placeOfMsg = (m) => (m.type === "stream" ? tkey(m.stream_id, m.subject ?? m.topic ?? "") : dkey((m.display_recipient || []).map((u) => u.id).filter((i) => i !== ME)));
function addMsg(c, m) { if (c.byId.has(m.id)) return false; c.byId.set(m.id, m); msgIndex.set(m.id, c); let i = c.msgs.length; if (typeof m.id === "number") while (i > 0 && typeof c.msgs[i - 1].id === "number" && c.msgs[i - 1].id > m.id) i--; c.msgs.splice(i, 0, m); return true; }
async function fetchMsgs(c, older) {
  if (c.loading || (older && c.oldest)) return; c.loading = true;
  try { const first = c.msgs.find((m) => typeof m.id === "number"); const q = {narrow: narrowOf(c.place), num_before: 50, num_after: 0, apply_markdown: true, client_gravatar: false};
    if (older && first) { q.anchor = String(first.id); q.include_anchor = false; } else q.anchor = "newest";
    const r = await api("GET", "messages", q); for (const m of r.messages) addMsg(c, m); if (r.found_oldest) c.oldest = true; c.loaded = true; c.error = null; }
  catch (e) { c.error = e.message; }
  c.loading = false;
}
async function fetchRuns(c) { const p = c.place; if (p.kind !== "topic" || !p.repo) return; try { const r = await api("GET", "armada/runs", {stream: p.stream, topic: p.topic}); if (r.runtime_config) runtimeCfg = r.runtime_config; c.runs = r.armada_runs.filter((x) => x.session_kind !== "side"); c.runs.forEach(askState); runsSeen.set(p.key, c.runs); c.runsLoaded = true; } catch (e) {} }
/* a run that waits for a person: the server's live state says it is on a question (request_user_input); the answer is
   any message that names the agent while the run waits (zerver/lib/armada.py, request_user_answers) */
function askState(r) { if (r.waiting === undefined) r.waiting = !!(r.live && r.live.operation && r.live.operation.kind === "question" && (r.status === "running" || r.status === "queued")); return r; }
/* the live state can be gone (it is a cache entry), so the conversation says it too: a run waits when Onix's question is
   the last word after its trigger and no one has named Onix since */
const namesOnix = (m) => [...(m.content || "").matchAll(/data-user-id="(\d+)"/g)].some((x) => isOnix(+x[1]));
function inferAsk(r, msgs) { if (!r || !(r.status === "running" || r.status === "queued")) return r; let q = null;
  let lastQ = null; for (const m of msgs) { if (typeof m.id !== "number" || m.id <= r.trigger_message_id) continue; if (isOnix(m.sender_id) && QUESTION.test(m.content || "")) q = lastQ = m; else if (q && (isOnix(m.sender_id) || namesOnix(m))) q = null; }   /* answered, or Onix went on */
  if (q && !(r.answeredAt && r.answeredAt >= q.timestamp)) { r.waiting = true; r.askedAt = q.timestamp; r.question = questionOf(q); }
  else if (r.waiting && !r.askedAt && lastQ) { r.askedAt = lastQ.timestamp; r.question = questionOf(lastQ); }   /* the server's live state says it waits but not since when: the question's message does */
  return settleAsk(r); }
/* the server waits QUESTION_TIMEOUT for an answer and then goes on with defaults (request_user_answers): past that, the
   question is spent wherever it is read (the band, the nav, Home), and the run is working again from that moment. The
   grace covers the gap between the question's message and the server starting its clock. */
function settleAsk(r) { if (r && r.waiting && r.askedAt && now() / 1000 - r.askedAt > QUESTION_TIMEOUT + 30) { r.waiting = false; r.resumedAt = r.askedAt + QUESTION_TIMEOUT; } return r; }
function answered(r) { r.waiting = false; r.answeredAt = r.resumedAt = now() / 1000; r.askedAt = 0; }
const liveNow = (r) => r.status === "queued" || r.status === "running";
/* a question can pass its time while no event comes: every few seconds the waiting runs are read against the clock */
function sweepAsks() { let any = false;
  for (const [k, runs] of runsSeen) for (const r of runs) { if (!r.waiting || !liveNow(r)) continue; settleAsk(r); if (r.waiting) continue; any = true; if (marks.get(k) === "needs") { marks.set(k, runMark(r)); drawTrail(k); } }
  if (any) { drawHomeRow(); if (P && place && place.kind !== "home") drawRunState(); } }
const waitingRun = (c) => { const r = c && liveRun(c); return r && r.waiting ? r : null; };
const runsSeen = new Map();                                /* place key -> runs, from the nav's scan and from places opened: what Home reads */
const askedAt = (c, r) => { if (r.askedAt) return r.askedAt; const q = c.msgs.filter((m) => isOnix(m.sender_id) && typeof m.id === "number" && m.id > r.trigger_message_id && QUESTION.test(m.content || "")).pop(); return q ? q.timestamp : r.date_updated || r.date_created; };
/* the options under the question in sight are live only while its run waits */
function drawAsks() { if (!P || !lane) return; const r = waitingRun(P), all = $$(".blk.ask", lane), last = all[all.length - 1]; all.forEach((q) => { const on = !!r && q === last; $$(".opt", q).forEach((o) => o.setAttribute("aria-disabled", String(!on)));   /* not disabled: a control disabled under the pointer drops the focus to the page */ q.classList.toggle("done", !on);
    /* the caption under the options says what is true now: a question the run no longer waits on does not say it waits */
    const cap = q.nextElementSibling && $("p.askcap > em", q.nextElementSibling); if (cap) { if (cap.dataset.live == null) cap.dataset.live = cap.textContent; const t = on ? cap.dataset.live : "The run no longer waits for an answer here."; if (cap.textContent !== t) cap.textContent = t; } }); }
async function fetchCtx(c) { const p = c.place; if (p.kind !== "topic" || !p.repo) return; try { const r = await api("GET", "armada/topic-context", {stream: p.stream, topic: p.topic}); c.ctx = r.armada_topic_context; } catch (e) { c.ctxError = e.message; } }
const liveRun = (c) => c.runs.find((r) => r.status === "queued" || r.status === "running");
const runOfMsg = (c, id) => c.runs.find((r) => r.result_message_id === id);
const hasWorktree = (c) => !!(c.ctx && c.ctx.workspace_uuid && c.ctx.git && c.ctx.git.available);

async function openPlace(p, o = {}) {
  if (!p) return; const same = place && place.key === p.key; if (same && !o.force) { ready(); return; }
  const prev = place; if (prev && prev.key !== p.key && places.get(prev.key)) places.get(prev.key).answering = false; place = p; P = cacheOf(p); P.place = p; pinned = true;
  if (!o.keepHash && location.hash !== hashOf(p)) history[o.replace ? "replaceState" : "pushState"](null, "", location.pathname + location.search + hashOf(p));
  store.set("place", p.key);
  if (p.kind === "topic" && !expanded.has(p.stream_id)) { expanded.add(p.stream_id); loadTopics(p.stream_id); store.set("open", JSON.stringify([...expanded])); }
  if (tab !== "Chat") setTab("Chat", true);
  if (threadTx && !lane.contains(threadTx)) threadTx = null; if (editing) endEdit(); closePicker(); closeMenu(); closeInfo(true); overHide();
  showHome(p.kind === "home", p.kind === "partner"); $$(".row", navFixed).forEach((r) => r.classList.toggle("sel", r.dataset.k === "home" && p.kind === "home")); closeCard(); drawPickerBand();
  if (p.kind === "partner") { drawNav(); drawHeader(); drawRunState(true); drawPicker(); drawSide(); drawComposer(); if (prev && prev.key !== p.key) M.release($("#picker"), {y: T["pane-rise"] || 4}, "touch", {id: "pane"}); ready(); return; }
  if (p.kind === "home") { drawNav(); drawHeader(); drawRunState(true); drawSide(); drawComposer(); drawHome(); if (prev && prev.key !== p.key) M.release($("#home"), {y: T["pane-rise"] || 4}, "touch", {id: "pane"}); fetchMentions(); return; }
  drawNav(); drawHeader(); drawLane(); drawRunState(true); drawSide(); drawTyping(); drawComposer();
  if (prev && prev.key !== p.key) M.release(lane, {y: T["pane-rise"] || 4}, "touch", {id: "pane"});
  ready();
  const c = P, fresh = !c.loaded;
  const jobs = [fresh ? fetchMsgs(c) : null, fetchRuns(c), c.ctx ? null : fetchCtx(c)];
  if (RECEIPTS && p.kind === "topic") loadMembers(p.stream_id);
  await jobs[0]; if (place !== p) return; if (fresh) { drawLane(); } pollReceipts({open: true});
  await jobs[1]; if (place !== p) return; inferAsk(liveRun(c), c.msgs); drawRunState(true); drawFooters(); drawSide(); drawAsks();
  { const r = liveRun(c), k = p.key, m_ = r ? runMark(r) : null; if (m_ && marks.get(k) !== m_) { marks.set(k, m_); drawTrail(k); drawHomeTrail(); } }
  await jobs[2]; if (place !== p) return; drawHeader(); drawSide(); drawComposer(); syncPaused(c);
  markRead(c);
  if (!fresh) fetchCtx(c).then(() => { if (place === p) { drawHeader(); drawSide(); } });
}
/* a worktree its people paused (the topic's context says so) shows Zz in the nav while no run is live there */
function syncPaused(c) { const k = c.place.key, paused = !!(c.ctx && c.ctx.is_paused) && !liveRun(c), was = marks.get(k);
  if (paused && !was) { marks.set(k, "paused"); drawTrail(k); } else if (!paused && was === "paused") { marks.delete(k); drawTrail(k); } }
/* Home is a pane of its own: no conversation, no band, no field */
/* the picker is a place of its own too, with the band (its one sentence) and the field (its search) */
function showHome(on, picker = false) { if (!feed) return; feed.hidden = on || picker; panes.Home.hidden = !on; if (panes.Partner) panes.Partner.hidden = !picker; $("main > .dock").hidden = on; band.el.hidden = on; main.classList.toggle("bare", on); R.classList.toggle("picking", picker); }
function markRead(c) { const set = unread.get(c.place.key); if (!set || !set.size || document.hidden) return; const ids = [...set].filter((i) => typeof i === "number"); unread.delete(c.place.key); drawTrail(c.place.key); if (ids.length) api("POST", "messages/flags", {messages: ids, op: "add", flag: "read"}).catch(() => {}); }

/* ---------- the header: the repository's facts, from the worktree's real state ---------- */
function progOf(c) { const run = planRun(c); if (!run) return {title: "Checklist"}; const steps = run.plan, live = run.status === "queued" || run.status === "running";
  const marksOf = steps.map((s) => stepMark(s, run)), done = marksOf.filter((x) => x === "done").length;
  return {marks: marksOf, cap: live || done < steps.length ? `${done} of ${steps.length}` : `Finished ${hhmm(run.date_finished || run.date_updated || run.date_created)}`}; }
const planRun = (c) => { const l = liveRun(c); if (l && l.plan && l.plan.length) return l; return c.runs.find((r) => r.plan && r.plan.length) || null; };
const stepMark = (s, run) => (s.status === "completed" ? "done" : s.status === "in_progress" ? (run.status === "failed" ? "failed" : run.status === "cancelled" ? "cancelled" : run.status === "done" ? "done" : "live") : run.status === "cancelled" ? "cancelled" : "left");
function repoPath(url, fallback) { const m = /([^/:]+\/[^/]+?)(?:\.git)?\/?$/.exec(url || ""); return m ? m[1] : fallback; }
function headCfg() { const p = place, c = P;
  if (p.kind === "partner") return {kind: "plain", path: esc(meName), title: "Choose your partner", say: "", checklist: {title: "Your partner"}, seg: pick.seg, has: !!(me_() && me_().species)};
  if (p.kind === "home") { const {needs} = homeRows(), n = needs.filter((x) => x.verb === "Answer").length, live = [...runsSeen.values()].filter((rs) => rs.some((r) => (r.status === "queued" || r.status === "running") && !r.waiting)).length;
    return {kind: "plain", title: "Home", say: (n ? `${n === 1 ? "One question waits" : n + " questions wait"} for you.` : needs.length ? "A run failed." : "Nothing waits for you.") + (live ? ` ${live === 1 ? "One run is" : live + " runs are"} live.` : ""), checklist: {title: "Running"}}; }
  if (p.kind === "dm") { const ids = p.ids.length ? p.ids : [ME], one = ids.length === 1 ? ids[0] : 0, pr = one ? presOf(one) : "";
    return {kind: "plain", title: esc(ids.map(nameOf).join(", ")), say: one ? (one === ME ? "Notes to yourself." : pr === "on" ? "Here now." : pr === "away" ? "Away." : "Not here just now.") : `${ids.length + 1} people`, checklist: {title: "Together"}}; }
  const s = streams.get(p.stream_id), title = esc(topicLabel(p.topic));
  if (!p.repo) return {kind: "plain", path: esc(s.name), title, say: esc(s.description || "A channel with no repository."), checklist: {title: "People"}};
  const ctx = c.ctx, g = ctx && ctx.git && ctx.git.available ? ctx.git : null, path = esc(repoPath(ctx && (ctx.repository_url || ctx.github_repo), s.name));
  if (ctx && !g) return {kind: "plain", path, title, say: ctx.workspace_uuid ? "The worktree's state is not available." : "No worktree yet. Onix makes one on its first run here.", checklist: progOf(c)};
  const lc = g && g.last_commit, base = (ctx && ctx.base_ref) || "main";
  const distance = !g ? "" : g.ahead && g.behind ? `${g.ahead} ahead, ${g.behind} behind` : g.ahead ? `${g.ahead} ahead of ${esc(base)}` : g.behind ? `${g.behind} behind ${esc(base)}` : `Even with ${esc(base)}`;
  return {path, title, branch: g && !ctx.branch_is_topic_default ? esc(g.branch) : "", dist: distance, commit: {who: lc ? esc(lc.author) : "", subj: lc ? esc(lc.subject) : "", sha: lc ? esc(lc.sha) : "", age: lc ? age(lc.timestamp) : ""}, tab, changes: g ? g.changed_count : 0, checklist: progOf(c)}; }
function drawHeader() { const cfg = headCfg(), wt = cfg.kind !== "plain"; R.classList.toggle("wt", wt); if (!wt && tab !== "Chat") setTab("Chat", true);
  const sig = JSON.stringify(cfg); if (hd && hd._sig === sig) return;
  const t = el("template"); t.innerHTML = headHTML(cfg); const neu = t.content.firstElementChild; if (place.kind === "partner") pickerHead(neu); hd.replaceWith(neu); hd = neu; hd._sig = sig; host(hd); snap(hd);
  { const subj = $(".commit .subj", hd); if (subj) { let w0 = 0; new ResizeObserver(() => { const w = subj.parentElement.clientWidth; if (w !== w0) { w0 = w; wordcut(subj); } }).observe(subj.parentElement); } }   /* the subject is cut again when the window changes width */
  const sp = $("#side > .hdg"); if (sp) { sp.innerHTML = progHTML(cfg.checklist, "prog-side"); host(sp); }
  $$("[aria-label='More']", hd).forEach((b) => (b.dataset.tip = "More: in the old interface for now")); if (place.kind !== "partner") $$("a.repo, button.fact.branch, button.fact.commit", hd).forEach((b) => (b.dataset.tip = "Not in the beta yet")); $$("#side > .hdg button.prog").forEach((b) => (b.dataset.tip = "The checklist is below")); const pg = $("#prog", hd); if (pg) pg.dataset.tip = "The checklist is in the column at 1408 px and wider"; }
function drawProg() { const cfg = progOf(P); const a = $("#prog", hd), b = $("#prog-side"); const sig = JSON.stringify(cfg); if (drawProg.sig === sig + place.key) return; drawProg.sig = sig + place.key;
  if (a) { const t = el("template"); t.innerHTML = progHTML(cfg, "prog"); a.replaceWith(t.content.firstElementChild); host(hd); } if (b) { const t = el("template"); t.innerHTML = progHTML(cfg, "prog-side"); b.replaceWith(t.content.firstElementChild); host(side); } if (hd) hd._sig = ""; }

/* ---------- the conversation ---------- */
const kindOf = (m) => (isOnix(m.sender_id) ? "onix" : m.sender_id === ME ? "me" : "them");
function named(cl) { for (let x = cl.previousElementSibling; x; x = x.previousElementSibling) { if (!x.classList.contains("cl") || x.classList.contains("me")) continue; return x.dataset.sid !== cl.dataset.sid; } return true; }
function newSection(c, m, kind, o) {
  const cl = el("section", "cl " + kind); cl.dataset.sid = m.sender_id; cl.dataset.day = dayKey(m.timestamp); c.append(cl);
  if (kind === "onix") { const lab = named(cl); cl.classList.toggle("lab", lab); cl.innerHTML = faceOf(m.sender_id) + (lab ? '<div class="who">Onix</div>' : ""); }
  else if (kind === "them") { const solo = place.kind === "dm" && place.ids.length === 1, lab = !solo && named(cl); cl.classList.add("s-" + stoneOf(m.sender_id)); cl.classList.toggle("lab", lab);
    cl.innerHTML = o.thread ? `<div class="who">${faceOf(m.sender_id, "s20")}${esc(nameOf(m.sender_id))}</div>` : faceOf(m.sender_id) + (lab ? `<div class="who">${esc(nameOf(m.sender_id))}</div>` : ""); if (o.thread) cl.classList.add("lab"); }
  return cl;
}
/* a turn's bubbles: the first keeps its lower inner corner square, the last its upper one; the turn's time stands on its last bubble */
function finish(cl) { if (cl.classList.contains("onix")) return; const bs = $$(":scope > .bl > .b", cl); bs.forEach((b, i) => { b.classList.toggle("u", i < bs.length - 1); b.classList.toggle("t", i > 0); });
  $$(":scope > .bl > time, :scope > .bl > .rt, :scope > .rcpt.at", cl).forEach((t) => t.remove()); const lastPart = $$(":scope > [data-part]", cl).pop(); if (!lastPart || cl.dataset.pending) return;
  const edited = (mid) => { const c = msgIndex.get(+mid), x = c && c.byId.get(+mid); return !!(x && (x.edited || x.last_edit_timestamp)); };
  /* an edited message that is not the turn's last says so on its own last bubble; the last one says it with the time */
  const lastOf = new Map(); for (const b of $$(":scope > .bl[data-mid]", cl)) lastOf.set(b.dataset.mid, b);
  for (const [mid, b] of lastOf) if (mid !== lastPart.dataset.mid && edited(mid)) b.append(el("time", "ed", "Edited"));
  const ts = +cl.dataset.ts, t = el("time", "", (edited(lastPart.dataset.mid) ? "Edited " : "") + hhmm(ts)); t.title = new Date(ts * 1000).toLocaleString();
  const cell = RECEIPTS && cl.classList.contains("me") ? receiptButton(t, lastPart.dataset.mid) : t;   /* my turn: the time and its ticks, one button */
  if (lastPart.classList.contains("bl")) lastPart.append(cell); else { const r = el("div", "rcpt at" + (cl.classList.contains("me") ? " end" : "")); r.append(cell); lastPart.after(r); } }
function appendMessage(c, m, o = {}) {
  const kind = kindOf(m), d = dayKey(m.timestamp);
  if (!o.thread && c._day !== d) { c.append(el("div", "day", dayLabel(m.timestamp))); c._day = d; }
  if (o.unreadAt === m.id) c.append(el("div", "unread", "New"));
  let cl = c.lastElementChild;
  const join = kind !== "onix" && cl && cl.classList.contains("cl") && +cl.dataset.sid === m.sender_id && !cl.dataset.closed && m.timestamp - +cl.dataset.ts < 600 && !cl.classList.contains("onix");
  if (!join) cl = newSection(c, m, kind, o);
  cl.dataset.ts = m.timestamp;
  const parts = partsOf(m).map((p) => partEl(p, m)); cl.append(...parts); const evo = evoCaption(m); if (evo) cl.append(evo);
  if (kind === "onix") { cl.dataset.mid = m.id; if (!parts.length) cl.append(placeholderAns(m)); syncOnix(cl, m); }
  if (kind === "onix" && m.submessages && m.submessages.length) cl.append(el("div", "note nw", 'This answer carries an artifact, which the beta does not draw yet. <a href="/#narrow/near/' + m.id + '">Open it in the old interface</a>.'));
  if (m.failed) failMark(cl, m); if (m.pending || m.failed) $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.toggle("unsent", !!m.failed));
  drawReactions(cl, m); if (!o.thread) drawThreadLine(cl, m);
  finish(cl); hush(cl); toHug.add(cl); return {cl, parts};
}
/* a wrapped bubble is as wide as its longest line, as a chat bubble hugs its words: read every waiting bubble's line boxes
   after one write, then set the widths. A one-paragraph bubble only (a list's bullets stand outside its line boxes); a width
   that would change the bubble's lines is not kept. */
const toHug = new Set();
function hugAll() { if (!toHug.size) return; const bs = []; for (const cl of toHug) if (cl.isConnected && !cl.classList.contains("onix")) bs.push(...$$(":scope > .bl > p.b:not(.jumbo)", cl)); toHug.clear(); if (!bs.length) return;
  bs.forEach((b) => (b.style.width = "")); const range = document.createRange();
  const want = bs.map((b) => { const h = b.offsetHeight; if (h <= 40) return null; range.selectNodeContents(b); const rs = [...range.getClientRects()].filter((x) => x.width > 0); if (!rs.length) return null; const cs = getComputedStyle(b); return {h, w: Math.ceil(Math.max(...rs.map((x) => x.right)) - Math.min(...rs.map((x) => x.left)) + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight)) + 1}; });
  bs.forEach((b, i) => { if (want[i]) b.style.width = want[i].w + "px"; });
  bs.forEach((b, i) => { if (want[i] && b.offsetHeight !== want[i].h) b.style.width = ""; }); }
const hush = (root) => $$(".rc, .rt, [data-thread], .tl .lk, .line .lk, .bl > .lk", root).forEach((b) => (b.tabIndex = -1));
function drawLane(o = {}) {
  const c = P, h0 = feed.scrollHeight, y0 = feed.scrollTop; lane.textContent = ""; lane._day = null;
  const set = unread.get(place.key), firstUnread = set && set.size ? Math.min(...[...set].filter((i) => typeof i === "number")) : null;
  if (c.loaded && !c.oldest) lane.append(el("div", "more-up", c.loading ? "Loading earlier messages" : "Scroll up for earlier messages"));
  const list = c.msgs.filter((m) => !m.thread_root_message_id);
  for (const m of list) appendMessage(lane, m, {unreadAt: firstUnread});
  for (const id of c.open) openThread(id, true);
  if (!list.length) lane.append(el("div", "empty", c.error ? esc(c.error) : !c.loaded ? "Loading" : place.kind === "dm" ? "No messages yet." : "Nothing here yet. Say something."));
  hugAll();
  if (o.keep) { feed.scrollTop = y0 + (feed.scrollHeight - h0); lastSet = feed.scrollTop; } else if (pinned) pin();
  edge(); syncJump();
}
/* ----- reactions ----- */
function chipsOf(m) { const by = new Map(); for (const r of m.reactions || []) { const k = r.reaction_type + ":" + r.emoji_code; let g = by.get(k); if (!g) by.set(k, (g = {r, n: 0, mine: false})); g.n++; if (r.user_id === ME) g.mine = true; } return [...by.values()]; }
const emojiChar = (r) => { if (r.reaction_type !== "unicode_emoji") return ":" + r.emoji_name + ":"; try { return String.fromCodePoint(...r.emoji_code.split("-").map((h) => parseInt(h, 16))); } catch (e) { return ":" + r.emoji_name + ":"; } };
function drawReactions(cl, m) {
  $$(`.rcs[data-mid="${m.id}"]`, cl).forEach((x) => (x.closest(".rca") || x).remove());
  const groups = chipsOf(m), own = $$(`:scope > [data-mid="${m.id}"]`, cl); if (!own.length) return null;
  const onix = cl.classList.contains("onix"), bubbles = own.filter((e) => e.classList.contains("bl")), b = bubbles.length ? $(".b", bubbles[bubbles.length - 1]) : null;
  if (b) b.classList.toggle("liked", groups.some((g) => g.mine) && !cl.classList.contains("me"));
  if (!groups.length) return null;
  const rcs = el("span", "rcs", groups.map((g) => `<button class="rc${g.mine ? " mine" : ""}" data-name="${esc(g.r.emoji_name)}" data-code="${esc(g.r.emoji_code)}" data-type="${esc(g.r.reaction_type)}" title="${esc(":" + g.r.emoji_name + ":")}"><span class="e">${esc(emojiChar(g.r))}</span>${g.n > 1 ? `<span class="n fig">${fig(g.n)}</span>` : ""}</button>`).join("")); rcs.dataset.mid = m.id;
  if (onix) { const ans = own.filter((e) => e.classList.contains("ans")).pop(), lastP = ans && ($$(":scope > p", ans).pop() || ans.lastElementChild); if (!lastP) { own[own.length - 1].after(rcs); return rcs; } const a = el("span", "rca"); a.append(rcs); lastP.append(a); }
  else if (b) b.after(rcs); else { const r = el("div", "rcpt" + (cl.classList.contains("me") ? " end" : "")); r.style.height = "auto"; r.append(rcs); own[own.length - 1].after(r); r.dataset.mid = m.id; r.classList.add("rcs"); }
  hush(cl); return rcs;
}
function sectionOf(id, root = lane) { const n = root.querySelector(`[data-mid="${id}"]`); return n ? n.closest(".cl") : null; }
function applyReaction(c, m, ev, mine) {
  m.reactions = m.reactions || []; const i = m.reactions.findIndex((r) => r.user_id === ev.user_id && r.emoji_code === ev.emoji_code && r.reaction_type === ev.reaction_type);
  if (ev.op === "add") { if (i >= 0) return; m.reactions.push({user_id: ev.user_id, emoji_name: ev.emoji_name, emoji_code: ev.emoji_code, reaction_type: ev.reaction_type}); } else { if (i < 0) return; m.reactions.splice(i, 1); }
  if (c !== P) return; const cl = sectionOf(m.id); if (!cl) return;
  const timeEl = $(`.bl[data-mid="${m.id}"] > time`, cl), x0 = timeEl && M.rest(timeEl).left; const rcs = drawReactions(cl, m);
  const rc = rcs && $$(".rc", rcs).find((x) => x.dataset.code === ev.emoji_code);
  if (ev.op === "add" && rc) { if (mine) { const b = cl.classList.contains("onix") ? null : $(`.bl[data-mid="${m.id}"] .b`, cl); M.land($(".n", rc) ? $(".e", rc) : rc); M.burst(rc, emojiChar(ev)); S.play("react");
      if (b) { const k = cl.classList.contains("me") ? 1 : -1; M.squash(b); M.kick(b, {x: 110 * k, y: 110}); if (b.classList.contains("liked")) M.release(b, {sx: -0.7, sy: -0.7}, "move", {pseudo: "::after", id: "wash"}); } }
    else M.appear($(".n", rc) || rc); }
  if (timeEl && timeEl.isConnected && mine) { const d = x0 - M.rest(timeEl).left; if (d) M.release(timeEl, {x: d}, "touch"); }
}
async function react(m, r) {   /* my reaction: it lands in this frame; the server is told; if it refuses, it is taken back */
  if (typeof m.id !== "number") return; const has = (m.reactions || []).some((x) => x.user_id === ME && x.emoji_code === r.emoji_code && x.reaction_type === r.reaction_type);
  const ev = {op: has ? "remove" : "add", user_id: ME, emoji_name: r.emoji_name, emoji_code: r.emoji_code, reaction_type: r.reaction_type}; applyReaction(P, m, ev, true);
  try { await api(has ? "DELETE" : "POST", `messages/${m.id}/reactions`, {emoji_name: r.emoji_name, emoji_code: r.emoji_code, reaction_type: r.reaction_type}); refreshPartners({fold: true, wait: 2000}); }
  catch (e) { applyReaction(msgIndex.get(m.id) || P, m, {...ev, op: has ? "add" : "remove"}, false); note(e.message); }
}
/* ----- read receipts: step 1 of the r4 receipts study (design/r4/receipts/RECEIPTS.md), on the existing endpoint -----
   My turns carry ticks beside their time, in a 16 px cell that exists from the frame of the send and changes only what it
   holds: nothing for the first 400 ms of a send, then a ring while it is in flight; one tick once the server has it (it is
   then in everyone's feed: Zulip's "delivered"); two in the second ink once someone counted has read it; two in my colour
   once everyone counted has. They are read from GET messages/{id}/read_receipts, for my own recent messages only, and never
   more than RCPT_CAP times a minute. Info (pressing the ticks, More > Info, or I on a message) lists who has read a message
   and who has not yet. None of it is drawn when the organization has read receipts off. No sound: someone reading is
   neither my action nor something that needs me. */
const RECEIPTS = DATA.readReceipts === "on";
const RCPT_CAP = 12;                                       /* background requests to read_receipts in any 60 s (opening places, polling, signs of life) */
const RCPT_INFO_CAP = 12;                                  /* Info's own budget: what I asked for answers at once, whatever the background spent */
const RCPT_POLL = 30000, RCPT_RECENT = 15 * 60, RCPT_FRESH = 10000, RCPT_OPEN = 6;
const rcpt = new Map();                                    /* message id -> {read: Set of user ids, at, err} */
const rcptLog = [], rcptInfoLog = [], rcptInflight = new Map(), members = new Map();
let muted = new Set(), info = null;
stats.receipts = {requests: 0, refused: 0, maxPerMinute: 0};
function rcptAllowed(forInfo) { const t = performance.now(), log = forInfo ? rcptInfoLog : rcptLog; while (log.length && t - log[0] >= 60000) log.shift();
  if (log.length >= (forInfo ? RCPT_INFO_CAP : RCPT_CAP)) { stats.receipts.refused++; return false; }
  log.push(t); stats.receipts.requests++; if (!forInfo) stats.receipts.maxPerMinute = Math.max(stats.receipts.maxPerMinute, log.length); else stats.receipts.info = (stats.receipts.info || 0) + 1; return true; }
function fetchReceipts(id, o = {}) { if (!RECEIPTS || typeof id !== "number") return Promise.resolve(null); const had = rcpt.get(id);
  if (had && !o.force && performance.now() - had.at < RCPT_FRESH) return Promise.resolve(had);
  if (rcptInflight.has(id)) return rcptInflight.get(id);
  if (!rcptAllowed(!!o.retry)) { if (o.retry) setTimeout(() => { if (info && info.id === id) fetchReceipts(id, o); }, 60000 - (performance.now() - rcptInfoLog[0]) + 50); return Promise.resolve(had || null); }   /* Info has its own budget; past twelve Infos in a minute, it waits for the minute to free one */
  const p = api("GET", `messages/${id}/read_receipts`).then((r) => ({read: new Set(r.user_ids), at: performance.now()}), (e) => ({read: had ? had.read : new Set(), at: performance.now(), err: e.message}))
    .then((x) => { rcpt.set(id, x); rcptInflight.delete(id); drawTicks(id); if (info && info.id === id) drawInfo(); return x; });
  rcptInflight.set(id, p); return p; }
function loadMembers(sid) { if (members.has(sid)) return Promise.resolve(members.get(sid));
  const p = api("GET", `streams/${sid}/members`).then((r) => { const s = new Set(r.subscribers); members.set(sid, s); $$(".rt[data-info]", lane).forEach((b) => drawTicks(+b.dataset.info)); if (info) drawInfo(); return s; }, () => { members.delete(sid); return null; });
  members.set(sid, p); return p; }
/* who a message's receipts count, as far as the page can know: the channel's subscribers now (or the conversation's
   people), less the sender, bots (Onix among them: it answers, it does not read), deactivated people and people I muted.
   Null until the channel's subscribers are known. */
function audience(m) { const c = msgIndex.get(m.id), p = c && c.place; if (!p || p.kind === "home") return null;
  let ids; if (p.kind === "topic") { const s = members.get(p.stream_id); if (!(s instanceof Set)) return null; ids = [...s]; } else ids = [ME, ...p.ids];
  return [...new Set(ids)].filter((i) => i !== m.sender_id && !muted.has(i) && people.has(i) && !people.get(i).bot && people.get(i).active); }
function tickState(m) { if (!m || m.failed) return "none"; if (m.local || m.pending || typeof m.id !== "number") return "pending";
  const x = rcpt.get(m.id), who = audience(m); if (!x) return "sent";
  const others = [...x.read].some((i) => i !== m.sender_id); if (!who || !who.length) return others ? "read" : "sent";   /* a reader the page cannot count (not subscribed now) still reads */
  return who.every((i) => x.read.has(i)) ? "all" : others ? "read" : "sent"; }
const TICKS = {
  pending: '<circle cx="8.5" cy="8.5" r="4.5"/><path d="M8.5 6v3h2"/>',
  sent: '<path d="M3.5 8.5l3 3 6-6"/>',
  read: '<path d="M1.5 8.5l3 3 6-6"/><path d="M7.5 10.5l1 1 6-6"/>'};
TICKS.all = TICKS.read;
const tickSVG = (st) => (TICKS[st] ? `<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><g class="n">${TICKS[st]}</g></svg>` : "");
function tickWords(m, st) { const x = rcpt.get(m.id), who = audience(m);
  if (st === "pending") return "Sending"; if (st === "none") return "Not sent";
  if (st === "sent") return "Sent. Not read yet";
  if (who && who.length === 1) return "Read";
  return who && who.length ? `Read by ${who.filter((i) => x.read.has(i)).length} of ${who.length}` : `Read by ${[...x.read].filter((i) => i !== m.sender_id).length}`; }
/* the time and the ticks: one button, which opens Info */
function receiptButton(t, mid) { const b = el("button", "rt"); b.dataset.info = mid; b.tabIndex = -1; const cell = el("span", "tk"); b.append(t, cell);
  const id = /^\d+$/.test(mid) ? +mid : mid, c = msgIndex.get(id); paintTick(b, c && c.byId.get(id), false); return b; }
function paintTick(b, m, motion) { const cell = $(".tk", b), st = tickState(m), was = cell.dataset.s; if (was === st) return;
  cell.dataset.s = st; const same = (was === "read" && st === "all") || (was === "all" && st === "read"); if (!same) cell.innerHTML = tickSVG(st);
  const words = tickWords(m, st); b.setAttribute("aria-label", `${words}. Message info`); b.dataset.tip = words;
  const svg = $("svg", cell); if (!motion || !svg || same) return;
  if (was === "pending" && st === "sent") M.land(svg); else if (st === "read" || st === "all") M.appear(svg, 0, T["fade-in"]); }
function drawTicks(id) { if (!lane) return; for (const b of $$(`.rt[data-info="${id}"]`, lane)) { const c = msgIndex.get(id), m = c && c.byId.get(id); if (m) paintTick(b, m, true); } }
/* which of my messages to ask about: each of my turns' last message (the one whose time and ticks show), newest first.
   On opening a place, the latest few that are not known to be read by everyone; after that only my latest turn, and only
   while it is younger than RCPT_RECENT: every RCPT_POLL while the page is in view, when someone in the place shows a sign
   of life (a message, a reaction, typing), and when I come back to the page. */
function pollReceipts(o = {}) { if (!RECEIPTS || !P || !place || place.kind === "home" || document.hidden || tab !== "Chat") return;
  const ids = $$(".cl.me .rt[data-info]", lane).filter((b) => !b.closest(".thr-in")).map((b) => +b.dataset.info).filter(Number.isFinite).reverse().slice(0, o.open ? RCPT_OPEN : 1);
  for (const id of ids) { const m = P.byId.get(id); if (!m) continue; const st = tickState(m), old = now() / 1000 - m.timestamp > RCPT_RECENT;
    if (st === "all" || (old && (!o.open || rcpt.has(id)))) continue; fetchReceipts(id); } }
let lifeTimer = 0;
const signOfLife = () => { clearTimeout(lifeTimer); lifeTimer = setTimeout(() => pollReceipts(), 1500); };
/* Info: who has read a message, and who has not yet. Above the message it describes (below when there is no room), never
   over the field; the conversation stays live under it. Rows keep the order they had when it opened: a change rewrites a
   row's trailing cell and the heading's figure. */
function toggleInfo(id, from, kbd) { if (info && info.id === id) { closeInfo(); return; } openInfo(id, from, kbd); }
function openInfo(id, from, kbd) { closeInfo(true); closeMenu(); overHide(); const m = P && P.byId.get(id); if (!RECEIPTS || !m || typeof id !== "number") { ready(); return; }
  const box = el("div", "info raised float"); box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Message info"); box.tabIndex = -1;
  info = {id, el: box, from, kbd, order: null}; main.append(box); if (place.kind === "topic") loadMembers(place.stream_id); drawInfo(); placeInfo();
  $$(`.rt[data-info="${id}"]`, lane).forEach((b) => b.classList.add("open")); box.focus({preventScroll: true}); M.release(box, {y: T["pane-rise"] || 4}, "move");
  fetchReceipts(id, {force: true, retry: true}); }
function placeInfo() { if (!info) return; const id = info.id, box = info.el, mr = main.getBoundingClientRect(), fr = feed.getBoundingClientRect(), dock = $("main > .dock").getBoundingClientRect();
  const parts = $$(`[data-mid="${id}"]`, lane).filter((x) => x.matches(".bl, [data-part], .cl")), first = parts[0], last = parts[parts.length - 1]; if (!first) return;
  const rt = $(`.rt[data-info="${id}"]`, lane), a = first.getBoundingClientRect(), z = last.getBoundingClientRect(), h = box.offsetHeight, w = box.offsetWidth;
  const left = clamp((rt ? rt.getBoundingClientRect().left : a.left) - mr.left, 8, mr.width - w - 8);
  let top = a.top - h - 8; if (top < fr.top + 8) top = z.bottom + 8; if (top + h > dock.top - 8) top = Math.max(fr.top + 8, a.top - h - 8);
  box.style.cssText = `position:absolute;left:${Math.round(left)}px;top:${Math.round(top - mr.top)}px`; }
function drawInfo() { if (!info) return; const c = msgIndex.get(info.id), m = c && c.byId.get(info.id); if (!m) { closeInfo(); return; }
  const x = rcpt.get(info.id), who = audience(m), mine = m.sender_id === ME, solo = !!who && who.length === 1 && place.kind === "dm";
  const read = x ? [...x.read].filter((i) => i !== m.sender_id) : [], counted = who || [];
  if (!info.order && x) { const byName = (a, b) => nameOf(a).localeCompare(nameOf(b)); info.order = [...read.sort(byName), ...counted.filter((i) => !x.read.has(i)).sort(byName)]; }
  const n = counted.filter((i) => x && x.read.has(i)).length;
  const head = !x ? "Loading" : solo ? (n ? "Read" : "Not read yet") : !n && !read.length ? "No one has read this yet" : counted.length ? `Read by <span class="fig">${fig(n)}</span> of ${counted.length}` : `Read by <span class="fig">${fig(read.length)}</span>`;
  const end = mine ? `Sent ${hhmm(m.timestamp)}` : `${esc(nameOf(m.sender_id).split(/[ ,]/)[0])}, ${hhmm(m.timestamp)}`;
  const trail = (i) => (x && x.read.has(i) ? "Read" : solo ? (presOf(i) === "on" ? "Here now" : presOf(i) === "away" ? "Away" : "Not yet") : "Not yet");
  const rows = (info.order || []).map((i) => `<li class="${x && x.read.has(i) ? "" : "not"}">${faceOf(i, "s20", presOf(i))}<span class="t">${esc(nameOf(i))}</span><span class="e">${trail(i)}</span></li>`).join("");
  const run = c.runs && c.runs.find((r) => r.trigger_message_id === info.id), runWord = run ? ({queued: "Queued", running: run.waiting ? "Asks you" : "Working", done: `Answered ${hhmm(run.date_finished || run.date_updated || run.date_created)}`, failed: "Failed", cancelled: "Stopped"}[run.status] || "") : "";
  const err = x && x.err ? `<p class="bad">Could not load who read this. <button class="lk" data-info-retry>Retry</button></p>` : "";
  info.el.innerHTML = `<div class="ih"><b>${head}</b><span class="e">${end}</span></div>${err}<ul class="ls">${rows}</ul>${run ? `<ul class="ls agent"><li>${faceOf([...armadaBots][0], "s20")}<span class="t">Onix</span><span class="e">${esc(runWord)}</span></li></ul>` : ""}`;
  host(info.el); }
function closeInfo(quiet) { if (!info) return; const {el: box, from, kbd} = info; info = null; $$(".rt.open", lane).forEach((b) => b.classList.remove("open")); box.inert = true; M.leave(box, () => box.remove());
  if (quiet) return; if (kbd && from && from.isConnected) M.focusKbd(from); else ready(); }
/* ----- threads: the root says how many replies it has; a press opens them under it, in the conversation ----- */
const repliesOf = (c, id) => c.msgs.filter((m) => m.thread_root_message_id === id);
/* under my own right-aligned bubbles a thread line starts where its words end, at a fraction of a CSS px. Its width is rounded
   up to a whole px once it is laid out (before it is painted, with no forced layout), so its faces land on whole CSS px
   (SPRITES.md section 5) */
const wholeWidth = typeof ResizeObserver === "function" ? new ResizeObserver((es) => { for (const e of es) { const t = e.target, w = e.borderBoxSize && e.borderBoxSize[0] ? e.borderBoxSize[0].inlineSize : t.getBoundingClientRect().width; if (w % 1) t.style.width = Math.ceil(w) + "px"; wholeWidth.unobserve(t); } }) : null;
function drawThreadLine(cl, m) {
  $$(`.tl[data-mid="${m.id}"]`, cl).forEach((x) => x.remove()); const c = P, loaded = repliesOf(c, m.id), s = m.reply_summary, n = Math.max(loaded.length, s ? s.reply_count || 0 : 0); if (!n) return;
  const last = loaded[loaded.length - 1], who = s && s.participants ? s.participants.map((p) => p.user_id) : [...new Set(loaded.map((x) => x.sender_id))];
  const snippet = last ? `${nameOf(last.sender_id).split(/[ ,]/)[0]}: ${plainOf(last.content).replace(/\s+/g, " ").trim().slice(0, 80)}` : s && s.last_sender ? `${nameOf(s.last_sender.user_id).split(/[ ,]/)[0]} replied ${age(s.last_reply_at)} ago` : "";
  const tl = el("div", "tl", `<span class="avs">${who.slice(0, 3).map((i) => faceOf(i, "s18")).join("")}</span><button class="lk${c.open.has(m.id) ? " on" : ""}" data-thread="${m.id}">${n} ${n === 1 ? "reply" : "replies"}</button><span class="sn">${esc(snippet)}</span>`); tl.dataset.mid = m.id;
  const own = $$(`:scope > [data-mid="${m.id}"]:not(.tl):not(.thr-in)`, cl); (own[own.length - 1] || cl.lastElementChild).after(tl); cl.dataset.closed = 1; host(tl); if (wholeWidth && cl.classList.contains("me")) wholeWidth.observe(tl);
}
/* a thread opens where it is, under its root, with its own flat field at its foot; a reply goes to the thread */
function openThread(id, quiet, o = {}) { const cl = sectionOf(id); if (!cl) return; const tl = $(`.tl[data-mid="${id}"]`, cl); let box = $(`.thr-in[data-mid="${id}"]`, cl);
  if (box) { if (o.focus) { const f = $(":scope > .cz", box); if (f) { threadTx = $(".tx", f); ready(); } return; }
    P.open.delete(id); if (threadTx && box.contains(threadTx)) threadTx = null; const write = () => box.remove(); if (quiet) write(); else grown(write, "touch"); if (tl) $(".lk", tl).classList.remove("on"); ready(); return; }
  P.open.add(id); box = el("div", "thr-in"); box.dataset.mid = id; const list = repliesOf(P, id), m = P.byId.get(id), want = m && m.reply_summary ? m.reply_summary.reply_count || 0 : list.length;
  const anchor = tl || $$(`:scope > [data-mid="${id}"]:not(.thr-in)`, cl).pop() || cl.lastElementChild;
  const write = () => { anchor.after(box); if (want > list.length) { box.append(el("div", "note more-replies", `Loading ${want - list.length} earlier ${want - list.length === 1 ? "reply" : "replies"}`)); fetchThread(P, id); } for (const r of list) appendMessage(box, r, {thread: true}); box.append(threadField(id)); };
  if (quiet) { write(); hugAll(); } else {   /* the root stays where it was read; the view moves only to bring the thread's field into sight, never the root out of it */
    haltLane(); pinned = false; const y = feed.scrollTop; write(); hugAll(); feed.scrollTop = y;
    const f = $(":scope > .cz", box).getBoundingClientRect(), fr = feed.getBoundingClientRect(), rr = (tl || anchor).getBoundingClientRect(), up = Math.min(f.bottom - (fr.bottom - 8), rr.top - (fr.top + 28));
    if (up > 0) { feed.scrollTop += up; M.release(lane, {y: up}, "move"); } lastSet = feed.scrollTop; pinned = dist() < 4; edge(); syncJump(); M.release(box, {y: -(T.rise || 6)}, "touch"); M.appear(box, 0.3); }
  if (tl) $(".lk", tl).classList.add("on");
  if (o.focus) { threadTx = $(":scope > .cz .tx", box); ready(); } }
/* replies further back than the conversation's window: the thread's own narrow fetches them, once */
async function fetchThread(c, id) { try { const r = await api("GET", "messages", {narrow: [{operator: "thread", operand: id}], anchor: "newest", num_before: 200, num_after: 0, apply_markdown: true, client_gravatar: false}); for (const m of r.messages) if (m.id !== id) addMsg(c, m); } catch (e) { return; }
  if (P !== c) return; const box = $(`.thr-in[data-mid="${id}"]`, lane); if (!box) return; const f = $(":scope > .cz", box), had = f && document.activeElement === $(".tx", f);
  grown(() => { $$(":scope > :not(.cz)", box).forEach((x) => x.remove()); for (const r of repliesOf(c, id)) appendMessage(box, r, {thread: true}); if (f) box.append(f); }, "touch"); if (had) ready(); }
/* a reply that arrives or that I send goes above the thread's field */
function threadAppend(box, m) { const f = $(":scope > .cz", box); appendMessage(box, m, {thread: true}); if (f) box.append(f); }
let threadTx = null;                                       /* the thread field that holds the caret, while it does */
function threadField(id) {
  const t = el("template"); t.innerHTML = composerHTML({plain: 1, verb: "Reply", focus: false}, "", null); const cz_ = t.content.firstElementChild, tx_ = $(".tx", cz_), go_ = $(".disc", cz_);
  cz_.classList.remove("is-focus", "has-text"); tx_.innerHTML = ""; tx_.contentEditable = "plaintext-only"; tx_.classList.add("m-caret", "idle"); go_.classList.remove("armed"); $$(".sz", cz_).forEach((x) => x.classList.add("sz")); host(cz_);
  const sync = () => { const on = !!tx_.textContent.trim(); cz_.classList.toggle("has-text", on); go_.classList.toggle("armed", on); };
  tx_.addEventListener("input", sync); tx_.addEventListener("focus", () => { threadTx = tx_; cz_.classList.add("is-focus"); }); tx_.addEventListener("blur", () => cz_.classList.remove("is-focus"));
  tx_.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); replyThread(id, tx_, e.timeStamp); sync(); } else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); threadTx = null; ready(); } });
  go_.addEventListener("click", (e) => { replyThread(id, tx_, e.timeStamp); sync(); });
  return cz_;
}
function replyThread(id, tx_, at = performance.now()) {
  const t0 = performance.now(), text = tx_.textContent.trim(), c = P, root = c.byId.get(id), box = tx_.closest(".thr-in");
  if (!text || !root || !box) { M.refuse(tx_.closest(".sh")); S.play("nope", 0, at); return; }
  const named_ = ONIX_AT.test(text); ONIX_AT.lastIndex = 0;
  const m = {id: "L" + ++localSeq, local: true, pending: true, sender_id: ME, timestamp: Math.floor(now() / 1000), content: named_ ? addressedEcho(text.replace(ONIX_AT, "").trim()) : echoHTML(text), raw: text.replace(ONIX_AT, agentMention), toOnix: named_ && place.kind === "topic" && !!place.repo, thread_root_message_id: id, reactions: []};
  root.reply_summary = root.reply_summary || {reply_count: 0, participants: []}; root.reply_summary.reply_count = (root.reply_summary.reply_count || 0) + 1;
  if (!root.reply_summary.participants.some((x) => x.user_id === ME)) root.reply_summary.participants.push({user_id: ME});
  const row = grown(() => { addMsg(c, m); tx_.textContent = ""; const cl = sectionOf(id); if (cl) drawThreadLine(cl, root); const r = {cl: null, parts: []}; const f = $(":scope > .cz", box); Object.assign(r, appendMessage(box, m, {thread: true})); if (f) box.append(f); return r; }, "touch");
  const b = $$(".b", row.parts[row.parts.length - 1] || row.cl).pop(); if (b) M.appear(b, 0.6); S.play("send", 0, at);
  threadTx = tx_; ready();
  stats.sends.push({task: Math.round((performance.now() - t0) * 100) / 100, sinceKey: null, painted: null, thread: true});
  transmit(c, m, null);
}

/* ----- Onix's turn: one element for the life of an answer ----- */
/* where the working mark rides: the end of the last words, in a paragraph or in a list's last item */
function tailOf(ans) { let e = ans.lastElementChild; for (let i = 0; i < 6 && e && /^(UL|OL)$/.test(e.tagName); i++) { const li = e.lastElementChild; if (!li) return null; const s = li.lastElementChild; if (!s || s.tagName !== "SPAN") return li; const inner = s.lastElementChild; if (inner && /^(UL|OL)$/.test(inner.tagName)) { e = inner; continue; } return s; } return e && !/^(UL|OL)$/.test(e.tagName) ? e : null; }
function placeholderAns(m) { const a = el("div", "ans"); a.dataset.mid = m.id; a.dataset.part = "ans"; a._src = ""; const p = el("p"); p._src = ""; a.append(p); return a; }
const WORKING = (m) => /^·\s*Working\s*$/.test((m.content || "").replace(/<[^>]+>/g, "").trim());
const msgLive = (c, m) => { if (m.local) return true; const r = runOfMsg(c, m.id); return r ? r.status === "queued" || r.status === "running" : !!m.maybeLive; };   /* a message whose run is not known yet is live if it arrived as the server's placeholder */
function syncOnix(cl, m) {   /* the working mark rides the end of the words while the run is live; a settled run leaves its record */
  const c = msgIndex.get(m.id) || P, live = msgLive(c, m), was = cl.classList.contains("live"); let head = $(".head:not(.gone)", cl);
  if (was && !live) cl.classList.add("held");   /* the room a live run keeps stays through the change: a layout read in between (the mark's leave) would otherwise clamp the pinned scroll by that room */
  cl.classList.toggle("live", live); if (live) cl.classList.remove("held");
  if (live) { const ans = $$(":scope > .ans", cl).pop() || cl.appendChild(placeholderAns(m)); let p = tailOf(ans); if (!p) { p = el("p"); p._src = ""; ans.append(p); }
    if (!head) { head = el("span", "head", wk(true)); p.append(head); } else if (head.parentNode !== p) p.append(head); else if (head.nextSibling && !(head.nextSibling.classList && head.nextSibling.classList.contains("rca"))) p.append(head);
    head.classList.toggle("first", !p.textContent.trim()); const f = $(":scope > .line.foot", cl); if (f) f.remove(); }
  else { if (head) { head.classList.add("gone"); M.leave(head); }   /* the mark fades where it stood: it takes no room in its line, and moving it out to the turn's end put it between the words and the footer, whose gap then changed by 4 px when it went */
    /* a run stopped before it said anything says so on the one line its words would have had ("Stopped after 1s.", drawFooter):
       no footer is added, so the turn keeps the height it had while live and nothing below or above it moves */
    const r = runOfMsg(c, m.id), said = $$(":scope > .ans", cl).some((a) => a.textContent.trim() || a.querySelector("img")) || !!$(":scope > [data-part]:not(.ans)", cl);
    if (r && r.status === "cancelled" && !said) { const a = $$(":scope > .ans", cl).pop() || cl.appendChild(placeholderAns(m)); let p = $("p", a); if (!p) { p = el("p"); a.append(p); } p.prepend(document.createTextNode("Stopped.")); p._src = "Stopped."; a._src = "<p>Stopped.</p>"; }
    $$(":scope > .ans", cl).forEach((a) => { if (!a.textContent.trim() && !a.querySelector("img")) a.remove(); });
    const f = drawFooter(cl, m); cl.classList.toggle("held", !f && (was || cl.classList.contains("held"))); }   /* a run that settles with no footer keeps the footer's room while it is the last thing said: the conversation does not drop */
}
const stoppedWord = (cl) => { const a = $$(":scope > .ans", cl); if (a.length !== 1 || !/^Stopped( after [^.]+)?\.$/.test(a[0].textContent.trim())) return null; const w = document.createTreeWalker(a[0], NodeFilter.SHOW_TEXT); for (let n = w.nextNode(); n; n = w.nextNode()) if (/^Stopped/.test(n.data.trim())) return n; return null; };
function drawFooter(cl, m) { const c = msgIndex.get(m.id) || P, r = runOfMsg(c, m.id), old = $(":scope > .line.foot", cl); let html = "";
  if (r) { const secs = (r.usage && r.usage.duration_seconds) || r.duration_seconds || ((r.date_finished || r.date_updated) - r.date_created), tools = r.tool_count ? ` · ${r.tool_count} ${r.tool_count === 1 ? "tool" : "tools"}` : "";
    if (r.status === "failed") html = `${mk("failed")}<span class="c-fail">Failed after ${dur(secs)}</span>`;
    else if (r.status === "cancelled") { const word = stoppedWord(cl); if (word) word.data = `Stopped after ${dur(secs)}.`; else html = `${mk("cancelled")}<span>Stopped after ${dur(secs)}</span>`; }   /* a run stopped before it said anything: its one line says how long it ran, with no footer to repeat it */
    else if (r.status === "done" && (secs >= 10 || r.tool_count || (r.plan && r.plan.length))) html = `<span>Ran ${dur(secs)}${tools}</span>`; }
  if (!html) { if (old) old.remove(); return null; } if (old) { if (old._sig !== html) { old.innerHTML = html; old._sig = html; } return old; }
  const f = el("div", "line foot", html); f._sig = html; const tl = $(":scope > .tl", cl), lastPart = $$(":scope > [data-part]", cl).pop(); if (tl) tl.before(f); else if (lastPart) lastPart.after(f); else cl.append(f); cl.classList.add("run"); return f; }   /* right after the words: the room a live run keeps for it is that gap and that line */
function drawFooters() { for (const cl of $$(".cl.onix[data-mid]", lane)) { const m = P.byId.get(+cl.dataset.mid) || P.byId.get(cl.dataset.mid); if (m) syncOnix(cl, m); } }
/* the server edits Onix's message as it writes. The new rendering is followed into the same elements: a paragraph that
   grew gets only its new words (they arrive on the ink's fade); a paragraph that changed is rewritten in place; parts
   that are new are added after the last. The row is never replaced. */
function morphOnix(cl, m) {
  const parts = partsOf(m), ex = $$(`:scope > [data-part][data-mid="${m.id}"]`, cl), head = $(".head:not(.gone)", cl), rca = $(".rca", cl); if (rca) rca.remove();
  let wrote = false, lastEl = null;
  parts.forEach((p, i) => { const old = ex[i];
    if (old && old.dataset.part === p.t) { lastEl = old; if (old._src === p.html) return; wrote = true; if (p.t === "ans") morphAns(old, p.html, head); else { old.innerHTML = p.html; loadPics(old); } old._src = p.html; return; }
    const e = partEl(p, m); wrote = true; if (old) { if (head && old.contains(head)) head.remove(); old.replaceWith(e); } else if (lastEl) lastEl.after(e); else { const anchor = $(":scope > .who", cl) || $(":scope > .sigil", cl); anchor ? anchor.after(e) : cl.prepend(e); } lastEl = e; if (!M.reduced()) M.appear(e, 0.45); });
  ex.slice(parts.length).forEach((x) => { if (head && x.contains(head)) head.remove(); x.remove(); });
  return wrote;
}
function morphAns(ans, html, head) {
  tpl.innerHTML = html; const neu = [...tpl.content.children], old = [...ans.children];
  neu.forEach((n, j) => { const o = old[j], nh = n.innerHTML;
    if (o && o.tagName === n.tagName) { const oh = o._src ?? ""; if (oh === nh) return;
      if (n.tagName === "P" && nh.startsWith(oh)) { const s = el("span", "wn", nh.slice(oh.length)); if (head && head.parentNode === o) head.before(s); else o.append(s); if (M.reduced()) s.replaceWith(...s.childNodes); else M.appear(s, 0.45).finished.then(() => s.parentNode && s.replaceWith(...s.childNodes), () => {}); }
      else { if (head && o.contains(head)) head.remove(); o.innerHTML = nh; }
      o._src = nh; return; }
    n._src = nh; if (o) { if (head && o.contains(head)) head.remove(); o.replaceWith(n); } else ans.append(n); });
  old.slice(neu.length).forEach((x) => { if (head && x.contains(head)) head.remove(); x.remove(); });
}
function updateMessage(c, m, firstWordsAt) {
  if (c !== P) return; const cl = sectionOf(m.id); if (!cl) { if (!m.thread_root_message_id) drawLane({keep: !pinned}); return; }
  if (!cl.classList.contains("onix")) {   /* a person's edit: that message's parts are rebuilt in place */
    const write = () => { const old = $$(`:scope > [data-part][data-mid="${m.id}"]`, cl), parts = partsOf(m).map((p) => partEl(p, m)); if (old.length) { old[0].before(...parts); old.forEach((x) => x.remove()); } else cl.append(...parts); drawReactions(cl, m); finish(cl); hush(cl); toHug.add(cl); };
    if (cl.closest(".thr-in")) { write(); hugAll(); } else grown(write, "move"); return; }
  const h0 = feed.scrollHeight, hadWords = !!cl.querySelector(".ans") && cl.querySelector(".ans").textContent.trim().length > 0;
  const wrote = morphOnix(cl, m); syncOnix(cl, m); drawReactions(cl, m); loadPics(cl);
  if (wrote && firstWordsAt && !hadWords && cl.textContent.trim()) { const t = firstWordsAt; requestAnimationFrame(() => setTimeout(() => { stats.firstWords.push(Math.round((performance.now() - t) * 10) / 10); drawLab(); }, 0)); }
  const h1 = feed.scrollHeight; if (h1 !== h0) follow(h1 - h0, cl);
}
/* a pinned feed follows the stream, never by more than a line's spring, until the answer's first line would leave the
   top; then it holds, and Jump to latest says there is more */
function follow(d, cl) { if (!pinned || tab !== "Chat") { syncJump(); return; }
  const top = M.rest(cl).top, limit = feed.getBoundingClientRect().top + 20;
  if (feed.scrollHeight > feed.clientHeight + 1 && top - d < limit) { const part = Math.max(0, Math.round(top - limit)); if (part > 0) { feed.scrollTop += part; lastSet = feed.scrollTop; edge(); M.release(lane, {y: part}, "touch", {clamp: {y: T.follow || 24}}); } pinned = dist() < 4; syncJump(); return; }
  pin(); edge(); M.release(lane, {y: d}, "touch", {clamp: {y: Math.max(T.follow || 24, 28)}}); }

/* ---------- sending ---------- */
function failMark(cl, m) { $$(`.bl[data-mid="${m.id}"] > .meta, .bl[data-mid="${m.id}"] > .lk`, cl).forEach((x) => x.remove()); const bl = $$(`.bl[data-mid="${m.id}"]`, cl).pop(); if (!bl) return;
  const meta = el("span", "meta bad", esc(m.failed === true ? "Not sent" : "Not sent: " + m.failed)), b = el("button", "lk", "Retry"); b.dataset.retry = m.id; bl.append(meta, b); host(bl); hush(bl); }
/* "@Onix" in my words is the agent's mention on the wire; a block that starts the words keeps its own line after it */
const ONIX_AT = /(?<![\w@])@(?:\*\*)?Onix(?:\*\*)?(?![\w-])/gi;
const startsBlock = (t) => /^\s*(```|~~~|[-*+] |\d+[.)] |> ?)/.test(t);
const addressed = (bare) => `${agentMention}${startsBlock(bare) ? "\n\n" : " "}${bare}`;
const addressedEcho = (bare) => (startsBlock(bare) ? '<p><span class="mention">Onix</span></p>' + echoHTML(bare) : echoHTML(bare).replace(/^<p>/, '<p><span class="mention">Onix</span> '));
function send(o = {}) {
  if (place && place.kind === "partner") { choosePick(); return; }   /* on the picker, Return chooses */
  const at = o.at ?? performance.now(), t0 = performance.now(), typed = o.text ?? tx.textContent.trim(), files = o.text != null ? [] : atts.filter((a) => a.url);
  if (editing && o.text == null) { saveEdit(typed, at); return; }
  if (atts.some((a) => !a.url && !a.failed)) { note("Still uploading"); M.refuse(sh); return; }
  const text = [typed, ...files.map((a) => `[${a.name}](${a.url})`)].filter(Boolean).join("\n");
  if (!text) { M.refuse(sh); S.play("nope", 0, at); return; }
  if (!P.loaded && !P.error) { M.refuse(sh); return; }
  const inView = tab === "Files" ? P.filePath : tab === "README" ? P.readmePath : null;   /* sent from a view, the words say what was in view */
  if (tab !== "Chat") setTab("Chat", true);   /* the field is the same on every view; what it sends is read in Chat */
  /* To Onix: in a repository, with the place's switch on, or words that name @Onix, or an option of its question pressed.
     The message is flagged agent_followup, as today's client flags it, and it also names the agent. The flag alone starts
     nothing on this server (its addressing router is disabled, so an unaddressed follow-up is dropped); the mention is
     what the server admits a run on, and what a waiting run takes as its answer. With the switch off, a run that waits
     is not answered by people talking to each other. */
  const named_ = ONIX_AT.test(text); ONIX_AT.lastIndex = 0;
  const p = place, c = P, live = liveRun(c), ask = waitingRun(c), toOnix = p.kind === "topic" && !!p.repo && (!!o.onix || onixNow() || named_), starts = toOnix && !live;
  const bare0 = text.replace(ONIX_AT, "").trim() || text, bare = inView && toOnix && o.text == null ? `\`${inView}\` ${bare0}` : bare0;
  const raw = toOnix || named_ ? addressed(bare) : text, shown = toOnix || named_ ? addressedEcho(bare) : echoHTML(text);
  const m = {id: "L" + ++localSeq, local: true, pending: true, sender_id: ME, timestamp: Math.floor(now() / 1000), content: shown, raw, toOnix, reactions: []};
  let ph = null; if (starts) ph = {id: "O" + localSeq, local: true, onix: true, sender_id: [...armadaBots][0], timestamp: m.timestamp, content: "", reactions: [], is_agent_message: true, for: m.id};
  pinned = true; haltLane();
  const row = grown(() => { $$(":scope > .empty", lane).forEach((x) => x.remove()); addMsg(c, m); const r = appendMessage(lane, m); if (o.text == null) { tx.textContent = ""; atts = []; drawAtts(); } syncField(); if (ph) { addMsg(c, ph); r.onix = appendMessage(lane, ph); } return r; }, "touch", {clamp: {y: 240}});
  const b = $$(".b", row.parts[row.parts.length - 1] || row.cl).pop(); if (b) M.appear(b, 0.6);   /* my bubble rises out of the field into its place with the conversation, one piece, on touch */
  S.play(live && toOnix ? "steer" : "send", 0, at); hopOnce($(".row.me", nav)); refreshPartners({fold: true, wait: 2000});   /* my face hops once; what I sent counts, after the frame */
  if (row.onix) { const g = $(".sigil", row.onix.cl), w = $(".head > svg", row.onix.cl); if (w) M.land(w); if (g) M.appear(g); S.play("ack", 0.12, at); }
  if (ask && toOnix) { c.answering = false; answered(ask); marks.set(p.key, runMark(ask)); drawRunState(); drawAsks(); drawTrail(p.key); drawHomeRow(p.key); }   /* the answer is in: the band says Working again, its clock from now */
  ready();
  const task = performance.now() - t0, rec = {task: Math.round(task * 100) / 100, sinceKey: null, painted: null}; stats.sends.push(rec);
  requestAnimationFrame(() => setTimeout(() => { rec.painted = Math.round((performance.now() - at) * 10) / 10; rec.sinceKey = Math.round((t0 - at) * 10) / 10; drawLab(); }, 0));
  transmit(c, m, ph);
  requestAnimationFrame(() => setTimeout(() => { if (P === c) drawSide(); }, 0));   /* the column learns I spoke after the frame that shows my bubble, never inside the send task */
}
async function transmit(c, m, ph) {
  const p = c.place, q = {content: m.raw, local_id: m.id, queue_id: queue, read_by_sender: true};
  if (p.kind === "topic") Object.assign(q, {type: "stream", to: p.stream_id, topic: p.topic}); else Object.assign(q, {type: "direct", to: p.ids.length ? p.ids : [ME]});
  if (m.thread_root_message_id) q.thread_root_message_id = m.thread_root_message_id;
  if (m.toOnix) q.agent_followup = true;                 /* what today's client sends when its Onix control is on (web/src/compose.ts) */
  try { const r = await api("POST", "messages", q); if (m.pending) { adopt(c, m, r.id); }
    if (ph) setTimeout(() => { if (ph.local && c.byId.has(ph.id)) { dropLocal(c, ph); note("Onix did not start a run for that message."); } }, 20000); }
  catch (e) { m.pending = false; m.failed = e.net ? true : e.message; if (ph) dropLocal(c, ph); if (c === P) { const cl = sectionOf(m.id); if (cl) { $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.add("unsent")); failMark(cl, m); M.refuse($$(`.bl[data-mid="${m.id}"] .b`, cl).pop() || cl); } drawTicks(m.id); } S.play("fail"); }
}
function dropLocal(c, x) { c.byId.delete(x.id); msgIndex.delete(x.id); c.msgs = c.msgs.filter((y) => y !== x); if (c !== P) return; const cl = sectionOf(x.id); if (cl) M.leave(cl, () => { const y = feed.scrollTop; cl.remove(); feed.scrollTop = y; }); }
/* the server's copy of what I sent: my row takes its id. Nothing on screen changes unless the server rendered it differently. */
function adopt(c, m, id, server) {
  const old = m.id; if (old !== id) { c.byId.delete(old); msgIndex.delete(old); m.id = id; c.byId.set(id, m); msgIndex.set(id, c); if (c === P) $$(`[data-mid="${old}"]`, lane).forEach((n) => (n.dataset.mid = id)); if (c === P) $$(".cl", lane).forEach((n) => { if (n.dataset.mid === String(old)) n.dataset.mid = id; }); if (c === P) $$(`.rt[data-info="${old}"]`, lane).forEach((n) => (n.dataset.info = id)); }
  m.local = false; m.pending = false; m.failed = false;
  if (server) { const was = JSON.stringify(partsOf(m).map((p) => [p.t, p.html])); Object.assign(m, server, {id}); const is = JSON.stringify(partsOf(m).map((p) => [p.t, p.html])); if (c === P && was !== is) updateMessage(c, m); }
  c.msgs.sort((a, b) => (typeof a.id === "number" && typeof b.id === "number" ? a.id - b.id : 0));
  if (c === P) drawTicks(id);   /* sent: one tick lands */
}
function retry(id) { const m = P.byId.get(id); if (!m || !m.failed) return; m.failed = false; m.pending = true; const cl = sectionOf(m.id); if (cl) { $$(`[data-mid="${m.id}"] .b`, cl).forEach((b) => b.classList.remove("unsent")); $$(`.bl[data-mid="${m.id}"] > .meta, .bl[data-mid="${m.id}"] > .lk`, cl).forEach((x) => x.remove()); } S.play("send"); transmit(P, m, null); ready(); }

/* ---------- live events: one queue ---------- */
let runWindow = null;
function onMessage(ev) {
  const m = ev.message, key = placeOfMsg(m), c = places.get(key), mine = m.sender_id === ME, flags = ev.flags || m.flags || [];
  if (m.type === "stream") { const list = topicsOf.get(m.stream_id), name = m.subject ?? ""; if (list && !list.some((t) => t.name.toLowerCase() === name.toLowerCase())) { list.unshift({name, max_id: m.id}); drawNav(); } }
  else if (!dms.some((d) => dkey(d.user_ids) === key)) { dms.unshift({user_ids: key.slice(2).split(",").filter(Boolean).map(Number), max_message_id: m.id}); drawNav(); }
  const seenNow = place && place.key === key && !document.hidden && tab === "Chat";
  if (!mine && !flags.includes("read")) { if (seenNow) api("POST", "messages/flags", {messages: [m.id], op: "add", flag: "read"}).catch(() => {}); else { let s = unread.get(key); if (!s) unread.set(key, (s = new Set())); s.add(m.id); drawTrail(key); if (flags.includes("mentioned") || flags.includes("stream_wildcard_mentioned") || flags.includes("topic_wildcard_mentioned")) mentions.set(m.id, m); drawHomeRow(key); } }
  { const runs = (c && c.runsLoaded ? c.runs : runsSeen.get(key)) || [], r = runs.find((x) => x.status === "queued" || x.status === "running");   /* a question asked, or answered by someone */
    if (r && isOnix(m.sender_id) && QUESTION.test(m.content || "")) { r.waiting = true; r.answeredAt = 0; r.askedAt = m.timestamp; r.question = questionOf(m); marks.set(key, "needs"); drawTrail(key); drawHomeRow(key); if (c === P) drawRunState(); }
    else if (r && r.waiting && !isOnix(m.sender_id) && namesOnix(m)) { answered(r); marks.set(key, runMark(r)); drawTrail(key); drawHomeRow(key); if (c === P) drawRunState(); } }
  if (!c || !c.loaded) return;
  if (ev.local_message_id && c.byId.has(ev.local_message_id)) { adopt(c, c.byId.get(ev.local_message_id), m.id, m); return; }
  if (c.byId.has(m.id)) { const mine_ = c.byId.get(m.id); adopt(c, mine_, m.id, m); return; }
  if (isOnix(m.sender_id) && WORKING(m)) m.maybeLive = true;
  if (isOnix(m.sender_id)) { const ph = c.msgs.find((x) => x.local && x.onix); if (ph) { ph.maybeLive = true; adopt(c, ph, m.id, m); if (c === P) { const cl = sectionOf(m.id); if (cl) { cl.dataset.mid = m.id; syncOnix(cl, ph); } } return; } }
  c.typers.delete(m.sender_id); addMsg(c, m);
  if (m.thread_root_message_id) { const root = c.byId.get(m.thread_root_message_id); if (root) { root.reply_summary = root.reply_summary || {reply_count: 0, participants: []}; root.reply_summary.reply_count = Math.max((root.reply_summary.reply_count || 0) + 1, repliesOf(c, root.id).length); if (c === P) { const cl = sectionOf(root.id); if (cl) grown(() => { drawThreadLine(cl, root); const box = $(`.thr-in[data-mid="${root.id}"]`, cl); if (box) threadAppend(box, m); }, "settle"); } } return; }
  if (c !== P) return;
  drawTyping(); drawSide();
  if (tab !== "Chat") { $$(":scope > .empty", lane).forEach((x) => x.remove()); appendMessage(lane, m); return; }
  const row = grown(() => { $$(":scope > .empty", lane).forEach((x) => x.remove()); return appendMessage(lane, m); }, "settle");
  if (pinned) { const last = row.parts[row.parts.length - 1], b = last && ($(".b", last) || last); if (b && !isOnix(m.sender_id)) { M.release(b, {y: T.rise || 6}, "settle", {id: "in"}); M.appear(b); } else if (isOnix(m.sender_id)) M.appear(row.cl, 0.3); }
  if (!mine && !isOnix(m.sender_id)) { S.play("arrive"); signOfLife(); hopOnce(row.cl); refreshPartners({wait: 4000, force: true}); }
}
function onUpdate(ev, at) {
  const c = msgIndex.get(ev.message_id); if (!c) return; const m = c.byId.get(ev.message_id); if (!m) return;
  if (ev.rendered_content == null) return;
  m.content = ev.rendered_content; if (!ev.rendering_only && ev.edit_timestamp) m.last_edit_timestamp = ev.edit_timestamp;   /* a person's edit, live: the turn says Edited (finish) */
  if (runWindow && runWindow.msg === m.id) runWindow.updates++;
  updateMessage(c, m, at);
}
function onRun(ev) {
  if (!ev.stream) return; const s = [...streams.values()].find((x) => x.name.toLowerCase() === ev.stream.toLowerCase()); if (!s) return; const key = tkey(s.stream_id, ev.topic || ""), c = places.get(key), live = ev.status === "queued" || ev.status === "running";
  const list = c ? c.runs : runsSeen.get(key) || []; if (!runsSeen.has(key)) runsSeen.set(key, list);
  let r = list.find((x) => x.id === ev.run_id); const fresh = !r; if (!r) { r = {id: ev.run_id, session_kind: "shared", date_created: ev.run_date_created, plan: null, overview: null, tool_count: 0, waiting: false}; list.unshift(r); }
  if (!live) r.waiting = false; else if (ev.event_type === "question_waiting" && !(r.answeredAt && now() / 1000 - r.answeredAt < 20)) { if (!r.waiting) r.askedAt = r.askedAt || ev.date_created; r.waiting = true; settleAsk(r); }   /* the server's heartbeat while it waits */
  const was = marks.get(key), want = live ? runMark(r, ev.status) : null; if (live) { if (was !== want) { marks.set(key, want); drawTrail(key); } } else if (LIVE_MARKS.has(was)) { if (ev.status === "failed" && !(place && place.key === key)) marks.set(key, "failed"); else marks.delete(key); drawTrail(key); }
  drawHomeRow(key);
  if (!c) return;
  const wasLive = r.status === "queued" || r.status === "running";
  Object.assign(r, {status: ev.status, trigger_message_id: ev.trigger_message_id, result_message_id: ev.result_message_id ?? r.result_message_id, current_activity: ev.current_activity, tool_count: ev.tool_count ?? r.tool_count, date_updated: ev.date_created, date_created: ev.run_date_created ?? r.date_created});
  if (ev.plan) r.plan = ev.plan; if (ev.overview) r.overview = ev.overview; if (ev.usage) r.usage = {...(r.usage || {}), ...ev.usage}; if (ev.duration_seconds != null) { r.duration_seconds = ev.duration_seconds; r.date_finished = ev.date_created; }
  if (ev.agents) r.agents = ev.agents;
  if (live && (!runWindow || runWindow.run !== r.id)) runWindow = {run: r.id, msg: r.result_message_id, t0: performance.now(), events: 0, updates: 0};
  if (runWindow && runWindow.run === r.id) { runWindow.events++; runWindow.msg = r.result_message_id; if (!live) { const secs = (performance.now() - runWindow.t0) / 1000, n = runWindow.events + runWindow.updates; stats.runs.push({run: r.id, seconds: Math.round(secs * 10) / 10, events: n, perSecond: Math.round(n / Math.max(secs, 0.001) * 10) / 10}); runWindow = null; drawLab(); } }
  if (c !== P) return;
  drawRunState(fresh); drawSide(); drawProg();
  if (r.result_message_id) { const cl = sectionOf(r.result_message_id), m = c.byId.get(r.result_message_id); if (cl && m) { const before = feed.scrollHeight; syncOnix(cl, m); const d = feed.scrollHeight - before; if (d) follow(d, cl); } }   /* what the conversation grew by: a footer that lands in the room the live run kept grows nothing */
  if (wasLive && !live) { S.play(ev.status === "failed" ? "fail" : "done"); refreshCtx(c, 600); }
}
function onReaction(ev) { const c = msgIndex.get(ev.message_id); if (!c) return; const m = c.byId.get(ev.message_id); if (m) applyReaction(c, m, ev, false); if (c === P && ev.user_id !== ME) signOfLife(); }
function onFlags(ev) { if (ev.flag !== "read" || ev.op !== "add") return; if (ev.all) { unread.clear(); drawNav(); return; } const ids = new Set(ev.messages); for (const [k, s] of unread) { let ch = false; for (const i of ids) if (s.delete(i)) ch = true; if (ch) { if (!s.size) unread.delete(k); drawTrail(k); } } }
function onDelete(ev) { const ids = ev.message_ids || [ev.message_id]; let redraw = false; for (const id of ids) { const c = msgIndex.get(id); if (!c) continue; const m = c.byId.get(id); c.byId.delete(id); msgIndex.delete(id); c.msgs = c.msgs.filter((x) => x !== m); if (c === P) redraw = true; } if (redraw) drawLane({keep: !pinned}); }
function onTyping(ev) { const key = ev.message_type === "stream" ? tkey(ev.stream_id, ev.topic || "") : dkey((ev.recipients || []).map((u) => u.user_id).filter((i) => i !== ME)), c = places.get(key), id = ev.sender.user_id; if (!c || id === ME || isOnix(id)) return;   /* Onix's own sign that it is working is its mark, not a typing bubble */
  clearTimeout(c.typers.get(id)); if (ev.op === "start" && c === P) signOfLife(); if (ev.op === "start") c.typers.set(id, setTimeout(() => { c.typers.delete(id); if (c === P) drawTyping(); }, 16000)); else c.typers.delete(id); if (c === P) drawTyping(); }
function drawTyping() { const ids = [...P.typers.keys()], ty = band.ty, on = ids.length > 0, sig = ids.join(","); if (ty._sig === sig) return; ty._sig = sig;
  if (on) { ty.innerHTML = (ids.length > 1 ? `<span class="avs">${ids.map((i) => faceOf(i)).join("")}</span>` : faceOf(ids[0])) + `<p class="tyb${ids.length === 1 ? " s-" + stoneOf(ids[0]) : ""}" id="tyb">${dots()}</p><span class="nm">${esc(ids.map((i) => nameOf(i).split(/[ ,]/)[0]).join(" and "))}</span>`; }
  ty.classList.toggle("chip", on && !band.run.classList.contains("off")); show(ty, on); }
let ctxTimer = 0;
function refreshCtx(c, ms = 1000) { clearTimeout(ctxTimer); ctxTimer = setTimeout(async () => { await fetchCtx(c); if (c === P) { drawHeader(); drawSide(); if (tab === "Changes") drawChanges(); } }, ms); }
function handle(ev, at) {
  stats.events++; if (ev.type === "agent_run" || ev.type === "update_message") { stats.log.push([Math.round(at), ev.type === "agent_run" ? `run:${ev.event_type}:${ev.status}` : `edit:${(ev.rendered_content || "").length}`]); if (stats.log.length > 600) stats.log.shift(); }
  try {
    if (ev.type === "message") onMessage(ev); else if (ev.type === "update_message") onUpdate(ev, at); else if (ev.type === "agent_run") onRun(ev); else if (ev.type === "reaction") onReaction(ev);
    else if (ev.type === "update_message_flags") onFlags(ev); else if (ev.type === "delete_message") onDelete(ev); else if (ev.type === "typing") onTyping(ev);
    else if (ev.type === "workspace_changed") { if (P && P.ctx && P.ctx.workspace_uuid === ev.workspace_id) refreshCtx(P); }
  } catch (e) { console.error("event", ev.type, e); }
}
async function poll() { let wait = 0;
  while (!signedOut.done) {
    const open = setTimeout(() => setDown(false), 3000);   /* a long-poll still open after 3 s is an answering server: without this the label said "not answering" until the next event or heartbeat */
    try { const r = await api("GET", "events", {queue_id: queue, last_event_id: lastEvent}); const at = performance.now(); clearTimeout(open); setDown(false); wait = 0; await partnersFirst(r.events); for (const ev of r.events) { lastEvent = Math.max(lastEvent, ev.id); handle(ev, at); } }
    catch (e) { clearTimeout(open); if (signedOut.done) return; if (e.code === "BAD_EVENT_QUEUE_ID") { try { await api("GET", "users/me"); await register(); for (const c of places.values()) { c.loaded = false; c.msgs = c.msgs.filter((m) => m.local); c.byId = new Map(c.msgs.map((m) => [m.id, m])); c.oldest = false; } if (place) openPlace(place, {force: true, keepHash: true}); } catch (x) {} } else { setDown(true); wait = Math.min(15000, wait ? wait * 2 : 1000); await sleep(wait); } }
  }
}
function setDown(d) { const l = $("#lab"); if (l && l.classList.contains("down") !== d) { l.classList.toggle("down", d); $("#lab-t").textContent = d ? "Beta · offline" : "Beta"; } }
/* signed out (here, in another tab, or the session ended): the page says so at once and offers sign-in; it never goes on as
   a spectator, which the realm would allow */
function signedOut() { if (signedOut.done) return; signedOut.done = true; palette(false); closePicker(); closeMenu(); overHide();
  const b = el("div", "", '<p>You are signed out. <a href="/login/?next=/beta/">Sign in again</a></p>'); b.id = "boot"; b.setAttribute("role", "alert"); document.body.append(b); $("a", b).focus(); }
/* coming back to the page asks whether the session still stands: a sign-out in another tab shows here at once, not when the
   long poll next returns */
let sessionAsked = -1e9;
function askSession() { if (signedOut.done || document.hidden || performance.now() - sessionAsked < 5000) return; sessionAsked = performance.now(); api("GET", "users/me").catch(() => {}); }

/* ---------- the band, the verb, Stop ---------- */
let stopping = 0;
/* the band's far end says what the run is doing: its own overview line, else its current activity when that is a thing
   it is doing (a command, a file) and not the runtime's bookkeeping or the request repeated */
function actOf(c, r) { if (r.overview) return r.overview; const a = (r.current_activity || "").trim(), trig = c.byId.get(r.trigger_message_id), asked = trig ? (trig.raw || (trig.content || "").replace(/<[^>]+>/g, "")).replace(/^\s*@?\**Onix\**\s*/i, "").trim() : "";
  if (!a || /^(Codex\b.*|Completed\.?|Working\.?|Rate limit.*|Queued.*)$/i.test(a) || (asked && asked.startsWith(a.replace(/…$|\.\.\.$/, "").slice(0, 40)))) return ""; return a; }
function drawRunState(cut) {
  const r = liveRun(P), ask = waitingRun(P), wt = place.kind === "topic" && place.repo, raised = !!r && (!!ask || now() / 1000 - r.date_created > 0.4 || (r.plan && r.plan.length) || r.tool_count > 0), b = band;
  if (r && !raised) setTimeout(() => { if (P && liveRun(P) === r) drawRunState(); }, 450);
  b.run.classList.toggle("att", !!ask); const lead = $(":scope > .mk", b.run), want = r ? runMark(r) : "live"; if (lead && !lead.classList.contains(want)) lead.outerHTML = mk(want);
  const quiet = !!r && !ask && quietRun(r);
  if (raised) { const word = ask ? "Onix asks" : stopping === r.id ? "Stopping" : r.status === "queued" ? "Queued" : quiet ? "Quiet" : "Working", cur = $(":scope > :not(.gone):not(.sz)", b.st);
    if (!cur || cur.textContent !== word) { if (cut || b.run.classList.contains("off")) b.st.innerHTML = `<span>${word}</span><i class="sz">Stopping</i>`; else M.swap(b.st, `<span>${word}</span>`, {quiet: true, seq: true}); }
    tick(true); const act = quiet ? `No word from this run since ${dayLabel(lastSign(r))} ${hhmm(lastSign(r))}. The server still lists it as running.` : actOf(P, r); if (b.act.textContent !== act) b.act.textContent = act; b.stop.setAttribute("aria-disabled", stopping === r.id); }
  if (cut) { b.run.classList.toggle("off", !raised); b.run.inert = !raised; b.run.style.opacity = ""; } else show(b.run, raised);
  b.ty.classList.toggle("chip", raised && P.typers.size > 0);
  setVerb(place.kind === "partner" ? "Choose" : editing ? "Save" : ask && onixNow() ? "Answer" : raised && wt && onixHere() ? "Steer" : "Send", cut);   /* with the switch off a waiting run is answered by its options or by naming @Onix */
  if (!r) stopping = 0;
  drawAsks();
}
/* a run the server still calls running that has given no sign of life for a long while (an old fixture, a worker that died):
   the band says it is quiet and for how long, not that it has been working all that time */
const QUIET_AFTER = 15 * 60;
const lastSign = (r) => Math.max(r.date_updated || 0, r.date_created || 0, r.resumedAt || 0);
const quietRun = (r) => r.status === "running" && !r.waiting && now() / 1000 - lastSign(r) > QUIET_AFTER;
function tick(force) { if (!P) return; const r = liveRun(P); if (!r || band.run.classList.contains("off") && !force) return; if (!r.waiting && quietRun(r) && !/Quiet/.test(band.st.textContent)) { drawRunState(); return; }
  const v = mmss(Math.max(0, Math.floor(now() / 1000 - (r.waiting ? askedAt(P, r) : quietRun(r) ? lastSign(r) : r.resumedAt || r.date_created)))); if (force) band.clock.innerHTML = fig(v); else M.figure(band.clock, v, {cut: true}); }   /* asking: since the question; working: since the run started or went on after a question */
const placeName = () => (place ? (place.kind === "home" ? "Home" : place.kind === "dm" ? (place.ids.length === 1 ? nameOf(place.ids[0]) : place.ids.length ? place.ids.map((i) => nameOf(i).split(/[ ,]/)[0]).join(", ") : "yourself") : topicLabel(place.topic)) : "");
/* the placeholder says what Return will do; on a view that is not Chat, with Onix on, it names what is in view */
const phText = () => (place && place.kind === "partner" ? `Search ${pack ? (pack.count || 1025).toLocaleString("en") : "1,025"} by name or number` : verb === "Steer" ? "Steer Onix" : verb === "Answer" ? "Answer Onix" : verb === "Save" ? "Edit your message" : !onixHere() ? "Message " + placeName() : tab !== "Chat" ? `Ask Onix about ${tab === "README" ? "the README" : tab === "Files" ? (P && P.fileName ? P.fileName : "these files") : "these changes"}` : `Ask Onix in ${placeName()}`);
function setVerb(v, cut) { const cur = $(":scope > span:not(.gone)", ph), same = v === verb;
  if (!same) { const up = ["Send", "Steer", "Answer", "Save", "Choose"].indexOf(v) > ["Send", "Steer", "Answer", "Save", "Choose"].indexOf(verb) ? 1 : -1; verb = v; if (cut) { $$(":scope > :not(.sz)", goc).forEach((x) => x.remove()); goc.insertAdjacentHTML("afterbegin", `<span>${v}</span>`); } else M.swap(goc, `<span>${v}</span>`, {dir: up, seq: true}); go.setAttribute("aria-label", v); go.dataset.tip = v; }
  const text = phText();   /* read after the verb has changed: read before, the field kept saying "Steer Onix" after a run ended */
  if (!cur || cur.textContent !== text) { if (cut || same) ph.innerHTML = `<span>${esc(text)}</span>`; else M.swap(ph, `<span>${esc(text)}</span>`, {quiet: true, seq: true}); } }
async function stop() { const r = P && liveRun(P); if (!r || stopping === r.id) { ready(); return; } stopping = r.id; drawRunState(); ready();
  try { await api("POST", `armada/runs/${r.id}/cancel`); } catch (e) { stopping = 0; drawRunState(); note(e.message); } }
function drawComposer() { const wt = place.kind === "topic" && place.repo, on = onixHere(), ctx = P.ctx; toBtn.hidden = !wt; modeBtn.hidden = !wt; modeBtn.classList.remove("off");
  toBtn.classList.toggle("off", !on); toBtn.setAttribute("aria-pressed", on); toBtn.dataset.tip = on ? "Onix answers what you send here. Press to talk to people only" : "What you send here goes to people. Press to have Onix answer";
  if (wt && ctx) { const label = modeLabel(ctx); const sl = $(".slot", modeBtn); if (sl && sl._v !== label) { sl._v = label; sl.innerHTML = `<span>${esc(label)}</span><i class="sz">${esc(label)}</i>`; } }
  modeBtn.dataset.tip = "Model, speed and effort for this worktree"; modeBtn.dataset.key = "⌘/"; setVerb(verb, true); placeJump(); }
/* Jump to latest stands left of the model label, whose width follows the model's name */
function placeJump() { if (!band || !band.el || !band.jump || !modeBtn) return; const box = band.jump.offsetParent || band.el, r = modeBtn.hidden ? null : modeBtn.getBoundingClientRect(), b = box.getBoundingClientRect(); band.el.style.setProperty("--jump-right", r && r.width ? `${Math.ceil(b.right - r.left) + 8}px` : ""); }
const syncField = () => cz.classList.toggle("has-text", !!tx.textContent.trim() || atts.length > 0);

/* ---------- the column: the checklist's standing home, Changes, Runs, People ---------- */
function drawSide() {
  const c = P, p = place, wt = p.kind === "topic" && p.repo; let h = "";
  if (p.kind === "home") { const live = []; for (const [k, rs] of runsSeen) { const r = rs.find((x) => x.status === "queued" || x.status === "running"), q = placeFromKey(k); if (r && q) live.push({k, r, q}); }
    h += '<section class="sec grow" id="sec-running"><ul class="ls">' + (live.length ? live.map(({k, r, q}) => `<li class="on" data-k="${esc(k)}">${mk(runMark(r))}<span class="t">${esc(topicLabel(q.topic))}</span><span class="e"><span class="d">${esc(q.stream)}</span></span></li>`).join("") : `<li>${ic("check")}<span class="t">${homeScanned ? "No run is live" : "Looking"}</span><span class="e"></span></li>`) + "</ul></section>";
    const here = [...people.values()].filter((u) => !u.bot && u.active && u.id !== ME && presOf(u.id) === "on").slice(0, 8);
    h += '<section class="sec foot" id="sec-people"><div class="sh"><span>People</span></div><ul class="ls">' + `<li>${faceOf(ME, "s20", "on")}<span class="t">${esc(meName)}</span><span class="e"></span></li>` + here.map((u) => `<li data-k="${esc(dkey([u.id]))}">${faceOf(u.id, "s20", "on")}<span class="t">${esc(u.name)}</span><span class="e"></span></li>`).join("") + "</ul></section>";
    if (outline._sig === h) return; outline._sig = h; outline.innerHTML = h; host(outline); fitLists(side); return; }
  if (p.kind === "partner") { h = pack ? pickerSideHTML() : ""; if (outline._sig === h) return; outline._sig = h; outline.innerHTML = h; host(outline); return; }
  if (wt) { const run = planRun(c);
    if (run) { const live = run.status === "queued" || run.status === "running";
      /* the checklist reserves the rows it has held in this run, up to seven: a plan that grows pushes the lists below once */
      c.chkRows = c.chkRows || {}; const rows = Math.min(7, Math.max(c.chkRows[run.id] || 0, run.plan.length + (live && run.overview ? 1 : 0))); c.chkRows[run.id] = rows;
      h += `<ol class="chk" id="steps" style="--rows:${rows}">` + run.plan.map((s) => { const mkn = stepMark(s, run), cls = mkn === "live" ? "now" : mkn; return `<li class="step ${cls}">${mk(mkn, mkn === "live" ? "live" : "")}<span class="t">${inlineMd(s.step)}</span><span class="e"></span></li>` + (mkn === "live" && live && run.overview ? `<li class="ev act"><i></i><span class="t cell"><span>${esc(run.overview)}</span></span><span class="e"></span></li>` : ""); }).join("") + "</ol>"; }
    else h += `<div class="note">${c.runsLoaded ? (c.runs.length ? "The runs here had no plan. A run that plans its work shows its steps here." : "No run here yet.") : "Loading"}</div>`;
    const g = c.ctx && c.ctx.git && c.ctx.git.available ? c.ctx.git : null, files = g ? g.changed_files || [] : [];
    c.changeRows = Math.min(4, Math.max(1, files.length, c.changeRows || 0));   /* the rows Changes has held while the place is open, up to four */
    h += `<section class="sec slots" id="sec-changes" style="--slots:${c.changeRows}"><div class="sh"><span>Changes</span><span class="e"><button class="lk" data-goto="Changes">Review</button></span></div><ul class="ls">` + (files.length ? files.map((f) => `<li>${ic("file")}<span class="t"><code>${esc(mid(f.path, 26))}</code></span><span class="e"><span class="kd">${esc(f.kind)}</span></span></li>`).join("") : `<li>${ic(g ? "check" : "file")}<span class="t">${g ? "No uncommitted changes" : c.ctx ? "No worktree yet" : "Loading"}</span><span class="e"></span></li>`) + "</ul></section>";
    h += '<section class="sec grow" id="sec-runs"><div class="sh"><span>Runs</span></div><ul class="ls">' + c.runs.slice(0, 30).map((r) => { const live = r.status === "queued" || r.status === "running", m = live ? "live" : r.status === "done" ? "done" : r.status === "failed" ? "failed" : r.status === "cancelled" ? "cancelled" : "left", trig = c.byId.get(r.trigger_message_id), title = trig ? (trig.raw || (trig.content || "").replace(/<[^>]+>/g, "")).replace(/^\s*@?\**Onix\**\s*/i, "").trim() : (r.summary || "Run " + r.id);
      return `<li class="${live ? "on" : ""}">${mk(live ? runMark(r) : m)}<span class="t">${esc(title.slice(0, 80))}</span><span class="e"><span class="d">${live ? esc(age(r.date_created)) : esc(dayKey(r.date_created) === dayKey(now() / 1000) ? hhmm(r.date_created) : shortDay(r.date_created))}</span></span></li>`; }).join("") + (c.runs.length ? "" : `<li>${ic("trace")}<span class="t">${c.runsLoaded ? "No runs yet" : "Loading"}</span><span class="e"></span></li>`)
      + `<li class="old keep">${ic("open")}<span class="t">Monitors, sub-agents, traces</span><span class="e"><a class="lk" href="/#narrow/channel/${p.stream_id}/topic/${encodeURIComponent(p.topic)}" data-tip="This worktree in the old interface">Open</a></span></li></ul></section>`; }
  const ids = p.kind === "dm" ? [ME, ...p.ids] : [...new Set(c.msgs.filter((m) => !isOnix(m.sender_id)).map((m) => m.sender_id))].slice(-8);
  /* in a channel with no repository the column's heading already says People (headCfg) */
  h += `<section class="sec ${wt ? "foot" : "grow"}" id="sec-people">${p.kind === "topic" && !p.repo ? "" : '<div class="sh"><span>People</span></div>'}<ul class="ls">` + (wt ? `<li>${faceOf([...armadaBots][0], "s20")}<span class="t">Onix</span><span class="e"></span></li>` : "") + ids.map((i) => `<li>${faceOf(i, "s20", presOf(i))}<span class="t">${esc(nameOf(i))}</span><span class="e"></span></li>`).join("") + "</ul></section>";
  if (outline._sig === h) return; outline._sig = h; outline.innerHTML = h; host(outline); fitLists(side);
}
const mid = (s, n) => { if (s.length <= n) return s; const k = (n - 1) >> 1; return s.slice(0, n - 1 - k) + "…" + s.slice(-k); };
const inlineMd = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

/* ---------- the views: README, Files, Changes ---------- */
function setTab(name, quiet) { if (name === tab || !panes[name]) return; if (tab === "Chat") { haltLane(); P && (P.scroll = feed.scrollTop); }
  $$(".tab", hd).forEach((x) => { const on = x.dataset.tab === name; x.classList.toggle("on", on); x.setAttribute("aria-selected", on); });
  panes[tab].hidden = true; tab = name; const pn = panes[name]; pn.hidden = false; if (hd) hd._sig = "";
  const wide = name === "Files" || name === "Changes"; R.classList.toggle("widepane", wide); side.classList.toggle("folded", wide); R.classList.toggle("folded", wide);
  /* the header, the band and the field are the same on every view: what I type on README goes to the conversation */
  overHide(); setVerb(verb, true);
  if (name === "Chat") { if (pinned) pin(); else if (P) feed.scrollTop = P.scroll || 0; edge(); syncJump(); }
  if (!quiet) { const inner = name === "Chat" ? lane : pn.firstElementChild; M.release(inner, {y: T["pane-rise"] || 4}, "touch", {id: "pane"}); }
  if (name === "README") drawReadme(); if (name === "Files") drawFiles(); if (name === "Changes") drawChanges();
}
const ws = (path, q) => api("GET", `armada/workspaces/${P.ctx.workspace_uuid}${path}`, q);
async function drawReadme() { const doc = $("#readme"), c = P; if (!hasWorktree(c)) { doc.innerHTML = '<p class="note">There is no worktree here, so there is no README.</p>'; return; }
  if (c.readme) { doc.innerHTML = c.readme; return; } doc.innerHTML = '<p class="note">Loading</p>';
  try { const d = (await ws("")).armada_workspace, e = d.default_entry; if (!e) { doc.innerHTML = '<p class="note">This worktree has no README.</p>'; return; }
    const f = (await ws("/file", {entry_id: e.entry_id})).armada_workspace_file; c.readmePath = f.path; let src = f.content || "", cutNote = "";
    { let fence = false; src = src.split("\n").map((l) => { if (/^\s*(```|~~~)/.test(l)) fence = !fence; return fence || !/^\s*</.test(l) ? l : l.replace(/<\/?[a-zA-Z][^>]*>/g, ""); }).join("\n"); }   /* a line of raw HTML outside code keeps its words and loses its tags: the server's renderer would print them */
    if (src.length > 9000) { const k = src.lastIndexOf("\n\n", 9000); src = src.slice(0, k > 2000 ? k : 9000); cutNote = '<p class="note">The rest of this file is not shown: the beta renders a README through the server\'s message renderer, which takes 10,000 characters.</p>'; }
    const r = await api("POST", "messages/render", {content: src}); if (P !== c) return;
    tpl.innerHTML = r.rendered; const root = clean(tpl.content), box = el("div"); let title = esc(e.name);
    for (const n of [...root.children]) { if (n.matches("div.codehilite, pre")) box.append(el("pre", "blk", blockPart(n).html)); else if (n.matches("table")) box.append(el("div", "blk tbl", blockPart(n).html)); else if (n.tagName === "H1" && title === esc(e.name)) { title = n.innerHTML; } else if (/^H[1-2]$/.test(n.tagName)) { const h = el("h2"); h.append(...n.childNodes); box.append(h); } else if (/^H[3-6]$/.test(n.tagName)) { const h = el("h3"); h.append(...n.childNodes); box.append(h); } else box.append(n); }
    $$("ul > li", box).forEach((li) => { const s = el("span"); s.append(...li.childNodes); li.append(s); });
    c.readme = `<div class="dh"><h1>${title}</h1><code style="color:var(--ink-2)">${esc(f.path)}</code></div>${box.innerHTML}${cutNote}<p class="note">Read only. Rendered by the server's Markdown; raw HTML in the file (a centred logo, say) is dropped. Editing is in the old interface.</p>`; doc.innerHTML = c.readme; loadPics(doc); }
  catch (e) { doc.innerHTML = `<p class="note">${esc(e.message)}</p>`; } }
async function drawFiles() { const tree = $("#tree"), fv = $("#fv"), c = P; if (!hasWorktree(c)) { tree.innerHTML = ""; fv.innerHTML = '<p class="note">There is no worktree here, so there are no files.</p>'; return; }
  if (!c.tree) { c.tree = {rows: [], open: new Set(), kids: new Map()}; tree.innerHTML = '<span class="note">Loading</span>'; fv.innerHTML = '<p class="note">Choose a file.</p>'; try { const r = (await ws("/tree")).armada_workspace_tree; c.tree.kids.set("", r.entries); } catch (e) { fv.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; } }
  if (P !== c) return; const rows = []; const walk = (id, depth) => { for (const e of c.tree.kids.get(id) || []) { const open = c.tree.open.has(e.entry_id); rows.push(`<button class="row${depth ? " d" + Math.min(depth, 2) : ""}${c.file === e.entry_id ? " sel" : ""}" data-entry="${esc(e.entry_id)}" data-kind="${esc(e.kind)}"${depth > 2 ? ` style="padding-left:${64 + (depth - 2) * 24}px"` : ""}>${ic(e.kind === "directory" ? (open ? "folder-open" : "folder") : /\.(md|rst|txt)$/i.test(e.name) ? "doc" : "file")}<span class="lab">${esc(e.name)}</span>${e.status && e.status !== "clean" ? `<span class="m">${esc(e.status)}</span>` : ""}</button>`); if (open) walk(e.entry_id, depth + 1); } }; walk("", 0);
  tree.innerHTML = rows.join(""); host(tree); if (!c.file) fv.innerHTML = '<p class="note">Choose a file.</p>'; }
async function pickEntry(row) { const c = P, id = row.dataset.entry;
  if (row.dataset.kind === "directory") { if (c.tree.open.has(id)) c.tree.open.delete(id); else { c.tree.open.add(id); if (!c.tree.kids.has(id)) { try { const r = (await ws("/tree", {directory_id: id})).armada_workspace_tree; c.tree.kids.set(id, r.entries); } catch (e) { c.tree.kids.set(id, []); } } } drawFiles(); return; }
  c.file = id; c.fileName = row.querySelector(".lab") ? row.querySelector(".lab").textContent : ""; setVerb(verb, true); drawFiles(); const fv = $("#fv"); fv.innerHTML = '<p class="note">Loading</p>';
  try { const f = (await ws("/file", {entry_id: id})).armada_workspace_file; if (P !== c || c.file !== id) return; c.filePath = f.path; const lines = (f.content || "").split("\n"), cap = 1500, shown = lines.slice(0, cap);
    fv.innerHTML = `<div class="fn"><code>${esc(f.path)}</code><span class="m">${lines.length} lines</span></div><div class="src">${shown.map((l, i) => `<div class="sl"><i>${i + 1}</i><span>${esc(l) || " "}</span></div>`).join("")}</div>` + (lines.length > cap ? `<p class="note">${lines.length - cap} more lines are not shown in the beta.</p>` : "") + '<p class="note">Read only, without syntax colour. Editing and Diff are in the old interface.</p>'; M.release($(".src", fv), {y: T["pane-rise"] || 4}, "touch"); }
  catch (e) { fv.innerHTML = `<p class="note">${esc(e.message)} (the beta shows text files only)</p>`; } }
function drawChanges() { const box = $("#changes"), c = P, g = c.ctx && c.ctx.git && c.ctx.git.available ? c.ctx.git : null; if (!g) { box.innerHTML = '<p class="note">There is no worktree here, so there are no changes.</p>'; return; }
  const f = g.changed_files || [], base = esc((c.ctx && c.ctx.base_ref) || "main");
  box.innerHTML = `<div class="prow"><b class="t-heading">${f.length ? `${g.changed_count} ${g.changed_count === 1 ? "file" : "files"} changed` : "No uncommitted changes"}</b><span class="i2">${g.ahead} ${g.ahead === 1 ? "commit" : "commits"} ahead of ${base}${g.behind ? `, ${g.behind} behind` : ""}, on <code>${esc(g.branch)}</code></span></div><div class="ch-list">` + f.map((x) => `<div class="fh">${ic("file")}<code>${esc(x.path)}</code><span class="k">${esc(x.kind)}</span></div>`).join("") + (g.changed_files_truncated ? '<p class="note">The server lists only the first files.</p>' : "") + '</div><p class="note">The list is the worktree\'s real state. Diffs, Viewed, Commit and Open pull request are not in the beta: the server has no endpoint that returns a diff.</p>'; }

/* ---------- the message's controls: the hover overlay, its menu, edit and delete ----------
   One raised object with four controls (React, Reply in thread, Copy text, More) on the bubble's open side, or on
   Onix's first line. It waits for the pointer to rest, travels between messages while it is up, never sits in flow. */
let pal = null, dialogOpen = false, menu = null, editing = null, atts = [], pop = null;
const mentions = new Map();                                 /* message id -> message: unread mentions of me, for Home */
const OVER = {react: "React", thread: "Reply in thread", copy: "Copy text", more: "More"};
let over = null, overAt = null, overTimer = 0, overWant = null, px_ = 0, py_ = 0;
const msgOf = (t) => { const n = t && t.closest("[data-mid]"); if (!n) return null; const id = n.dataset.mid; return P.byId.get(+id) || P.byId.get(id) || null; };
function buildOver() {
  over = el("span", "over", Object.keys(OVER).map((n) => `<button class="c" aria-label="${OVER[n]}" data-act="${n}" data-tip="${OVER[n]}">${ic(n)}</button>`).join("")); host(over); over.inert = true;
  feed.addEventListener("pointermove", (e) => { if (menu) return; if (e.target.closest && e.target.closest(".rcs, .cz, .blk.ask")) return;
    const t = e.target.closest && (e.target.closest(".over") ? overAt : e.target.closest(".b, .cl.onix")); if (!t || !msgOf(t)) { if (!e.target.closest(".over")) { clearTimeout(overTimer); overWant = null; } return; }
    if (t === overAt && !over.inert) return; if (!over.inert) { overShow(t); return; }
    if (t === overWant && Math.hypot(e.clientX - px_, e.clientY - py_) < 40) return; overWant = t; px_ = e.clientX; py_ = e.clientY; clearTimeout(overTimer); overTimer = setTimeout(() => { if (overWant === t) overShow(t); }, 120); }, {passive: true});
  feed.addEventListener("pointerleave", () => { if (!menu) overHide(); });
  press(over, ".c", (a, e, ptr) => act(a.dataset.act, overAt, a, !ptr && e.detail === 0));
  /* the keyboard: one message at a time is in the Tab order (the one I last stood on, else the latest); Up and Down move
     between messages; standing on one brings its overlay up inside it, so Tab goes on to React, Reply in thread, Copy text
     and More, then out to the band and the field */
  lane.addEventListener("keydown", (e) => { const t = e.target, d = {ArrowUp: -1, ArrowDown: 1}[e.key]; if (!d || !t.dataset || !t.dataset.door || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault(); const all = doors(), n = all[clamp(all.indexOf(t) + d, 0, all.length - 1)]; if (!n || n === t) return; door = n; syncDoor(); n.focus({preventScroll: true});
    const r = n.getBoundingClientRect(), fr = feed.getBoundingClientRect(), up = fr.top + 48 - r.top, down = r.bottom - (fr.bottom - 72); if (up > 0) feed.scrollTop -= up; else if (down > 0) feed.scrollTop += Math.min(down, r.top - fr.top - 48); });
  lane.addEventListener("focusin", (e) => { const t = e.target; if (t.dataset && t.dataset.door) { door = t; overShow(t); } });
  lane.addEventListener("focusout", (e) => { if (!overAt || menu) return; const to = e.relatedTarget; if (to && (overAt.contains(to) || over.contains(to) || (to.dataset && to.dataset.door))) return; overHide(); });
  new MutationObserver(() => { if (!syncDoor.due) { syncDoor.due = true; queueMicrotask(syncDoor); } }).observe(lane, {childList: true, subtree: true});
}
let door = null;
const doors = () => $$(".bl > .b, .cl.onix[data-mid]", lane).filter((t) => !t.closest(".cz"));
function syncDoor() { syncDoor.due = false; const all = doors(); if (!door || !door.isConnected || !all.includes(door)) door = all[all.length - 1] || null;
  for (const x of $$("[data-door]", lane)) if (x !== door) { x.removeAttribute("tabindex"); delete x.dataset.door; }
  if (door && !door.dataset.door) { door.tabIndex = 0; door.dataset.door = "1"; } }
function overShow(t) { if (!t || !t.isConnected) return; if (overAt === t && !over.inert) return; const up = !over.inert && over.isConnected, before = up && over.getBoundingClientRect(); overAt = t; (t.matches(".b") ? t.parentElement : t).append(over); over.inert = false;   /* a bubble's overlay stands in its row, past its time and reactions (beta.css) */
  const mine = (msgOf(t) || {}).sender_id === ME; $('[data-act="more"]', over).hidden = false; void mine;
  if (up) { M.fade(over, 1); M.flip(over, before, "touch"); } else { M.fade(over, 1); M.release(over, {y: T["pane-rise"] || 4}, "touch"); } }
function overHide() { clearTimeout(overTimer); overWant = null; if (!over || over.inert) return; over.inert = true; overAt = null; M.fade(over, 0); }
const plainText = (t) => { const x = t.cloneNode(true); $$(".rcs, .rca, .over, .head, time, .who, .sigil, .face, .tl, .thr-in, .line", x).forEach((y) => y.remove()); return x.textContent.trim(); };
function act(name, t, btn, kbd) { const m = msgOf(t); if (!m) return;   /* from the keyboard, focus stays on the control: the field is not where I was */
  if (name === "react") { react(m, {emoji_name: "+1", emoji_code: "1f44d", reaction_type: "unicode_emoji"}); if (!kbd) ready(); }
  else if (name === "thread") { overHide(); openThread(m.thread_root_message_id || m.id, false, {focus: true}); }
  else if (name === "copy") { try { navigator.clipboard.writeText(plainText(t.matches(".b") ? t : t)).catch(() => {}); } catch (e) {} M.say(btn, "Copied", 1200); if (!kbd) ready(); }
  else if (name === "more") openMenu(m, btn); }
/* More: what can be done to this message. Mine can be edited and deleted; any can be linked or copied. */
function openMenu(m, btn) { closeMenu(); const mine = m.sender_id === ME && typeof m.id === "number", items = [];
  if (mine && !isOnix(m.sender_id)) items.push(["edit", "Edit", "E"], ["delete", "Delete", ""]);
  items.push(["copy", "Copy text", "C"], ["link", "Copy link", ""]); if (RECEIPTS && typeof m.id === "number") items.push(["info", "Info", "I"]);
  menu = el("div", "menu raised float", items.map(([k, label, key]) => `<button class="mi" data-mi="${k}" tabindex="-1">${label}<span class="k">${key}</span></button>`).join("")); menu.setAttribute("role", "menu");
  const r = btn.getBoundingClientRect(), mr = main.getBoundingClientRect(); menu.style.cssText = `position:absolute;left:${Math.round(r.left - mr.left)}px;top:${Math.round(r.bottom - mr.top + 4)}px`; main.append(menu); host(menu);
  let hi = 0; const mark = () => $$(".mi", menu).forEach((x, i) => x.classList.toggle("hl", i === hi)); mark();
  menu._key = (e) => { const d = {ArrowDown: 1, ArrowUp: -1}[e.key]; if (d) { e.preventDefault(); hi = clamp(hi + d, 0, items.length - 1); mark(); return true; } if (e.key === "Enter") { e.preventDefault(); $$(".mi", menu)[hi].click(); return true; } if (e.key === "Escape") { e.preventDefault(); closeMenu(); ready(); return true; }
    const hot = items.findIndex(([, , key]) => key && key.toLowerCase() === e.key.toLowerCase()); if (hot >= 0 && !e.metaKey && !e.ctrlKey) { e.preventDefault(); $$(".mi", menu)[hot].click(); return true; } return false; };
  menu._t = overAt;
  press(menu, ".mi", (b, e, ptr) => { const k = b.dataset.mi, t = menu._t; closeMenu(); overHide();
    if (k === "edit") startEdit(m); else if (k === "delete") confirmDelete(m); else if (k === "info") openInfo(m.id, t, !ptr);
    else if (k === "copy") { try { navigator.clipboard.writeText(plainText(t)).catch(() => {}); } catch (e) {} note("Copied"); ready(); }
    else if (k === "link") { const p = place, u = p.kind === "topic" ? `${location.origin}/#narrow/channel/${p.stream_id}/topic/${encodeURIComponent(p.topic)}/near/${m.id}` : `${location.origin}/#narrow/near/${m.id}`; try { navigator.clipboard.writeText(u).catch(() => {}); } catch (e) {} note("Link copied"); ready(); } });
  M.release(menu, {y: -(T["pane-rise"] || 4)}, "touch"); }
function closeMenu() { if (!menu) return; const x = menu; menu = null; x.inert = true; M.leave(x, () => x.remove()); }
/* editing my message: its words go back to the field, Return saves them into the same message, Esc gives the draft up */
async function startEdit(m) { if (!m || m.sender_id !== ME || typeof m.id !== "number") return; if (editing) endEdit();
  let raw; try { raw = (await api("GET", `messages/${m.id}`, {apply_markdown: false})).message.content; } catch (e) { note(e.message); return; }
  raw = raw.split(agentMention).join("@Onix");
  editing = {m, was: tx.textContent}; $$(`[data-mid="${m.id}"] .b`, lane).forEach((b) => b.classList.add("unsent"));
  threadTx = null; tx.textContent = raw; syncField(); go.classList.add("armed"); drawRunState(); ready(); }
function endEdit() { if (!editing) return; const {m, was} = editing; editing = null; $$(`[data-mid="${m.id}"] .b`, lane).forEach((b) => b.classList.remove("unsent")); tx.textContent = was; syncField(); go.classList.toggle("armed", !!was.trim()); drawRunState(); }
async function saveEdit(text, at) { const {m} = editing; if (!text) { endEdit(); confirmDelete(m); return; }
  const named_ = ONIX_AT.test(text); ONIX_AT.lastIndex = 0; const raw = text.replace(ONIX_AT, agentMention), c = msgIndex.get(m.id) || P;
  tx.textContent = ""; editing.was = ""; endEdit(); m.content = named_ ? addressedEcho(text.replace(ONIX_AT, "").trim()) : echoHTML(text); m.edited = true; updateMessage(c, m); S.play("send", 0, at); ready();
  try { await api("PATCH", `messages/${m.id}`, {content: raw}); } catch (e) { note(e.message); } }
/* deleting cannot be undone, so it asks once, in a dialog; the server's event takes the message away */
function confirmDelete(m) { if (dialogOpen) return; dialogOpen = true; const d = el("div", "dlg raised float", `<p style="font:var(--t-strong)">Delete this message?</p><p style="color:var(--ink-2);margin-top:var(--s-2)">It is removed for everyone, and cannot be brought back.</p><div class="btns"><button class="btn" data-no>Cancel</button><button class="go" data-yes>Delete</button></div>`); d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true");
  d.style.cssText = "position:absolute;left:calc(50% - 200px);top:160px"; main.append(d); host(d); M.release(d, {y: -(T["pane-rise"] || 4)}, "touch");
  const close = () => { dialogOpen = false; d.inert = true; M.leave(d, () => d.remove()); removeEventListener("keydown", key, true); ready(); };
  const yes = async () => { close(); try { await api("DELETE", `messages/${m.id}`); } catch (e) { note(e.message); } };
  const key = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); (document.activeElement === $("[data-no]", d) ? close : yes)(); } else if (e.key === "Tab") { e.preventDefault(); const b = $$("button", d), i = b.indexOf(document.activeElement); b[(i + 1) % b.length].focus(); } };
  addEventListener("keydown", key, true); press(d, "[data-no]", close); press(d, "[data-yes]", yes); $("[data-no]", d).focus(); }   /* it stands on Cancel: Return alone never deletes */

/* ---------- attach: the field's Plus, a paste or a drop; the file goes to the server's upload endpoint and stands as a chip
   before the words until the message is sent ---------- */
let fileIn = null;
function upload(file) { const a = {id: ++localSeq, name: file.name || "pasted file", url: null, failed: false}; atts.push(a); drawAtts();
  const fd = new FormData(); fd.append("file", file, a.name);
  api("POST", "user_uploads", {}, {body: fd}).then((r) => { a.url = r.url || r.uri; a.name = r.filename || a.name; }, (e) => { a.failed = true; note(e.message); }).finally(() => { drawAtts(); ready(); }); }
function drawAtts() { let box = $(".atts", cz); if (!atts.length) { if (box) box.remove(); syncField(); return; } if (!box) { box = el("span", "atts"); $(".txw", cz).prepend(box); }
  box.innerHTML = atts.map((a) => `<span class="att${a.url ? "" : a.failed ? " bad" : " up"}">${ic(/\.(png|jpe?g|gif|webp|svg)$/i.test(a.name) ? "image" : "file")}<span class="an">${esc(mid(a.name, 28))}${a.url ? "" : a.failed ? " (failed)" : "…"}</span><button class="x" data-unatt="${a.id}" aria-label="Remove ${esc(a.name)}" data-tip="Remove">${ic("x")}</button></span>`).join(""); host(box); syncField(); }

/* ---------- the model and effort: a popover from the label, offering what the server's catalog offers for this worktree.
   The choice is the worktree's (POST armada/topic-context, the endpoint today's runtime menu uses). ---------- */
const FALLBACK_EFFORTS = [{effort: "low", label: "Low"}, {effort: "medium", label: "Medium"}, {effort: "high", label: "High"}, {effort: "xhigh", label: "XHigh"}];
const effortsFor = (ctx, model) => { const e = (ctx.model_catalog || []).find((x) => x.slug === model); return e && e.supported_reasoning_levels && e.supported_reasoning_levels.length ? e.supported_reasoning_levels : ctx.reasoning_efforts && ctx.reasoning_efforts.length ? ctx.reasoning_efforts : FALLBACK_EFFORTS; };
/* a model named the way people say it, from its id: "gpt-6-astra" is "GPT-6 Astra", "gpt-5.6-sol" is "GPT-5.6 Sol" */
const modelName = (slug) => { const w = String(slug || "").split("-").filter(Boolean); if (!w.length) return String(slug || ""); const cap = (x) => (/^\d/.test(x) ? x : x[0].toUpperCase() + x.slice(1));
  return w[0].toLowerCase() === "gpt" && w.length > 1 ? ["GPT-" + w[1], ...w.slice(2).map(cap)].join(" ") : w.map(cap).join(" "); };
const modelsOf = (ctx) => { const cat = ctx.model_catalog || []; const list = cat.map((e) => ({slug: e.slug, name: modelName(e.slug), tiers: Array.isArray(e.service_tiers) ? e.service_tiers : [], dflt: e.default_reasoning_level}));
  if (!list.length) for (const sl of ctx.model_choices || []) list.push({slug: sl, name: modelName(sl), tiers: []}); if (ctx.model && !list.some((x) => x.slug === ctx.model)) list.unshift({slug: ctx.model, name: modelName(ctx.model), tiers: []}); return list; };
const tierNames = (tiers) => tiers.map((t) => (typeof t === "string" ? t : t.name || t.display_name || t.id || "")).filter(Boolean).join(", ");
/* the speed is the server's service tier (ARMADA_WORKER_SERVICE_TIER, one for every worktree), named on a model whose catalog
   offers it: "GPT-6 Astra · Ultrafast · Medium". Codex takes "fast" for its priority tier. */
const tierId = (t) => String(typeof t === "string" ? t : (t && (t.id || t.name || t.display_name)) || "").toLowerCase();
const tierOf = (ctx, model) => { const want = String((runtimeCfg && runtimeCfg.service_tier) || "").toLowerCase(); if (!want) return "";
  const id = want === "fast" ? "priority" : want, m = modelsOf(ctx).find((x) => x.slug === model), t = m && m.tiers.find((x) => tierId(x) === id || tierId(x) === want);
  return !t ? "" : (typeof t === "object" && (t.display_name || t.name)) || (id === "priority" ? "Fast" : id[0].toUpperCase() + id.slice(1)); };
const modeLabel = (ctx) => { const m = modelsOf(ctx).find((x) => x.slug === ctx.model), e = effortsFor(ctx, ctx.model).find((x) => x.effort === ctx.reasoning_effort), tier = tierOf(ctx, ctx.model); return `${m ? m.name : modelName(ctx.model)}${tier ? " · " + tier : ""} · ${e ? e.label : ctx.reasoning_effort}`; };
function togglePicker(o = {}) { if (pop) { closePicker({back: o.kbd}); return; } const ctx = P && P.ctx; if (!ctx || !place.repo || modeBtn.hidden) { ready(); return; }
  pop = el("div", "pop raised float"); const r = modeBtn.getBoundingClientRect(), mr = main.getBoundingClientRect(); pop.style.cssText = `position:absolute;right:${Math.max(8, Math.round(mr.right - r.right))}px;bottom:${Math.round(mr.bottom - r.top + 6)}px`;
  main.append(pop); drawPop(); modeBtn.classList.add("open"); modeBtn.setAttribute("aria-expanded", "true");
  press(pop, ".mi", (b, e, ptr) => { const ctx_ = P.ctx, model = b.dataset.model, ef = effortsFor(ctx_, model); let effort = ctx_.reasoning_effort; if (!ef.some((x) => x.effort === effort)) effort = (modelsOf(ctx_).find((x) => x.slug === model) || {}).dflt || ef[0].effort; saveMode(model, effort); closePicker({back: !ptr}); });
  press(pop, ".steps button", (b) => saveMode(P.ctx.model, b.dataset.effort));
  /* the keyboard: Up and Down through the models (and from the last one down to the efforts), Left and Right through the
     efforts, Tab between the two groups, Return or Space to choose, Esc back to the label */
  pop.addEventListener("keydown", (e) => { const t = e.target, rows = $$(".models .mi", pop), steps = $$(".steps button", pop), ri = rows.indexOf(t), si = steps.indexOf(t), cur = (l) => l.find((b) => b.tabIndex === 0) || l[0]; let n = null;
    if (e.key === "ArrowDown") n = ri >= 0 ? rows[ri + 1] || cur(steps) : null; else if (e.key === "ArrowUp") n = ri > 0 ? rows[ri - 1] : si >= 0 ? rows[rows.length - 1] : null;
    else if (e.key === "ArrowRight") n = si >= 0 ? steps[si + 1] : null; else if (e.key === "ArrowLeft") n = si > 0 ? steps[si - 1] : null;
    else if (e.key === "Tab") n = ri >= 0 ? cur(steps) : cur(rows); else return;
    e.preventDefault(); if (!n) return; for (const l of [rows, steps]) if (l.includes(n)) l.forEach((b) => (b.tabIndex = b === n ? 0 : -1)); M.focusKbd(n); });
  M.release(pop, {y: T["pane-rise"] || 4}, "touch"); if (o.kbd) focusPop(); else ready(); }
/* into the picker from the keyboard: onto the model in use */
function focusPop() { if (!pop) return; const rows = $$(".models .mi", pop), b = rows.find((x) => x.tabIndex === 0) || rows[0]; if (b) M.focusKbd(b); }
function drawPop() { if (!pop) return; const ctx = P.ctx, models = modelsOf(ctx), efforts = effortsFor(ctx, ctx.model), live = !!liveRun(P), tier = tierOf(ctx, ctx.model);
  const f = document.activeElement, had = f && pop.contains(f) ? (f.dataset.model ? `[data-model="${CSS.escape(f.dataset.model)}"]` : f.dataset.effort ? `[data-effort="${CSS.escape(f.dataset.effort)}"]` : "") : "";
  pop.innerHTML = `<div class="models" role="group" aria-label="Model">${models.map((m) => `<button class="mi${m.slug === ctx.model ? " hl" : ""}" data-model="${esc(m.slug)}" aria-pressed="${m.slug === ctx.model}" tabindex="${m.slug === ctx.model ? 0 : -1}"><span class="ml">${esc(m.name)}<small>${esc(m.slug)}${m.tiers.length ? " · " + esc(tierNames(m.tiers)) : ""}</small></span><span class="k">${m.slug === ctx.model ? ic("check") : ""}</span></button>`).join("")}</div><div class="steps t-text" role="group" aria-label="Effort">${efforts.map((e) => `<button data-effort="${esc(e.effort)}" aria-pressed="${e.effort === ctx.reasoning_effort}" class="${e.effort === ctx.reasoning_effort ? "on" : ""}" tabindex="${e.effort === ctx.reasoning_effort ? 0 : -1}">${esc(e.label)}</button>`).join("")}</div><span class="cap">${live ? "Applies from the next turn" : "This worktree\'s model and effort"}${tier ? `. ${esc(tier)} is the server's speed for every worktree` : ""}</span>`; host(pop);
  if (had) { const b = $(had, pop); if (b) M.focusKbd(b); } }
function closePicker(o = {}) { if (!pop) return; const p = pop, back = o.back || p.contains(document.activeElement); pop = null; p.inert = true; modeBtn.classList.remove("open"); modeBtn.setAttribute("aria-expanded", "false"); M.leave(p, () => p.remove()); if (back && !modeBtn.hidden) M.focusKbd(modeBtn); else ready(); }   /* from the keyboard, back to the label */
async function saveMode(model, effort) { const c = P, p = place, ctx = c.ctx; if (!ctx || (model === ctx.model && effort === ctx.reasoning_effort)) return; const prev = {model: ctx.model, reasoning_effort: ctx.reasoning_effort};
  Object.assign(ctx, {model, reasoning_effort: effort}); drawComposer(); drawPop(); S.play("tick");
  try { const r = await api("POST", "armada/topic-context", {stream: p.stream, topic: p.topic, model, reasoning_effort: effort, response_policy: ctx.response_policy || "ambient", include_git: false}); const n = r.armada_topic_context;
    if (n) { const keep = c.ctx.git; c.ctx = {...c.ctx, ...n}; if (!(n.git && n.git.available)) c.ctx.git = keep; } }
  catch (e) { Object.assign(ctx, prev); note(e.message); }
  if (P === c) { drawComposer(); drawPop(); } }

/* ---------- Jump to (⌘K): every place I can go to, by name ---------- */
async function loadAllTopics() { const list = subs().filter((s) => !topicsOf.has(s.stream_id)); let i = 0; const work = async () => { while (i < list.length) await loadTopics(list[i++].stream_id, {quiet: true}); }; await Promise.all([work(), work()]); if (pal) pal._draw(); }
function places_() { const out = [{key: "home", label: "Home", glyph: ic("home"), cap: ""}];
  for (const s of subs()) { const repo = repoStreams.has(s.stream_id); for (const t of topicsOf.get(s.stream_id) || []) out.push({key: tkey(s.stream_id, t.name), label: topicLabel(t.name), glyph: ic(repo ? "repo" : s.invite_only ? "lock" : "hash"), cap: s.name, hay: `${s.name} ${t.name}`}); }
  const seen = new Set(); for (const d of dms) { const k = dkey(d.user_ids); seen.add(k); const ids = d.user_ids.length ? d.user_ids : [ME]; out.push({key: k, label: ids.map(nameOf).join(", "), glyph: faceOf(ids[0], "s20"), cap: "Direct message"}); }
  for (const u of people.values()) { if (u.bot || u.id === ME || !u.active) continue; const k = dkey([u.id]); if (seen.has(k)) continue; out.push({key: k, label: u.name, glyph: faceOf(u.id, "s20"), cap: "Direct message"}); }
  if (place && place.repo) for (const v of ["README", "Files", "Changes"]) out.push({cmd: () => setTab(v), label: `Open ${v}`, glyph: ic(v === "README" ? "doc" : v === "Files" ? "folder" : "diff"), cap: topicLabel(place.topic)});
  if (P && liveRun(P)) out.push({cmd: stop, label: "Stop the run", glyph: ic("stop"), cap: "⌘."});
  if (pack) out.push({cmd: openPicker, label: me_() && me_().species ? "Change partner" : "Choose your partner", glyph: faceOf(ME, "s20"), cap: ""});
  out.push({cmd: () => S.set(!S.on()), label: S.on() ? "Turn sound off" : "Turn sound on", glyph: ic(S.on() ? "mute" : "sound"), cap: ""}, {cmd: () => (location.href = "/"), label: "Open the old interface", glyph: ic("open"), cap: ""});
  return out; }
function score(it, q) { if (!q) return it.key === "home" ? 0 : 1; const l = it.label.toLowerCase(), h = (it.hay || it.label + " " + it.cap).toLowerCase(); if (l.startsWith(q)) return 0; if (new RegExp("(^|[\\s/_-])" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(l)) return 1; if (l.includes(q)) return 2; if (h.includes(q)) return 3; const words = q.split(/\s+/).filter(Boolean); return words.length > 1 && words.every((w) => h.includes(w)) ? 4 : -1; }
function palette(open = !pal) { if (!open) { if (pal) { const p = pal; pal = null; p.inert = true; M.leave(p, () => p.remove()); ready(); } return; } if (pal) return; closePicker(); closeMenu(); overHide();
  const p = pal = el("div", "pal raised float", `<div class="in">${ic("search", "i2")}<span contenteditable="plaintext-only" class="pin m-caret" role="combobox" aria-label="Jump to" aria-expanded="true"></span><span class="pph">Jump to a worktree, channel or person</span></div><div class="list" role="listbox"></div>`);
  p.style.cssText = "position:absolute;left:calc(50% - 280px);top:96px"; main.append(p); host(p);
  const inp = $(".pin", p), list = $(".list", p); let hi = 0, shown = [], all = places_();
  const draw = () => { const onRow = list.contains(document.activeElement), q = inp.textContent.trim().toLowerCase(); $(".pph", p).hidden = !!q; all = places_(); shown = all.map((it, i) => [it, score(it, q), i]).filter((x) => x[1] >= 0).sort((a, b) => a[1] - b[1] || a[2] - b[2]).slice(0, 8).map((x) => x[0]); hi = clamp(hi, 0, Math.max(0, shown.length - 1));
    list.innerHTML = shown.length ? shown.map((it, i) => `<button class="mi${i === hi ? " hl" : ""}" tabindex="${i === hi ? 0 : -1}" role="option" aria-selected="${i === hi}">${it.glyph}<span class="lab">${esc(it.label)}</span><span class="k">${esc(it.cap || "")}</span></button>`).join("") : '<span class="none">Nothing by that name</span>'; host(list);
    if (onRow) { const r = $$(".mi", list)[hi]; if (r) r.focus({preventScroll: true}); else inp.focus(); } };   /* a redraw (more topics arrived) keeps the row I stand on */
  const mark = (focus) => $$(".mi", list).forEach((x, i) => { x.classList.toggle("hl", i === hi); x.tabIndex = i === hi ? 0 : -1; x.setAttribute("aria-selected", String(i === hi)); if (focus && i === hi) M.focusKbd(x); });
  const toField = () => { inp.focus(); const sel = getSelection(); sel.selectAllChildren(inp); sel.collapseToEnd(); };
  p._draw = draw;
  const go_ = (it) => { palette(false); if (!it) return; if (it.cmd) it.cmd(); else openPlace(placeFromKey(it.key)); };
  draw(); inp.focus(); M.release(p, {y: -(T["pane-rise"] || 4)}, "touch"); loadAllTopics();
  inp.addEventListener("input", () => { hi = 0; draw(); }); press(list, ".mi", (m) => go_(shown[$$(".mi", list).indexOf(m)]));
  /* the keyboard: arrows move through the rows from the field or from a row; Tab goes between the field and the row in
     hand; Return goes; a letter typed on a row goes on into the field */
  p.addEventListener("keydown", (e) => { const onRow = !!(e.target.closest && e.target.closest(".mi")), d = {ArrowDown: 1, ArrowUp: -1}[e.key];
    if (d) { e.preventDefault(); hi = clamp(hi + d, 0, Math.max(0, shown.length - 1)); mark(onRow); return; }
    if (e.key === "Tab") { e.preventDefault(); if (onRow) toField(); else if (shown.length) mark(true); return; }
    if (e.key === "Enter") { e.preventDefault(); go_(shown[hi]); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); palette(false); return; }
    if (onRow && e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) toField(); }); }

/* ---------- Home: what needs me. Runs that wait for my answer, then failed runs, mentions of me, and new words in worktrees. ---------- */
const HOME = {key: "home", kind: "home", repo: false};
function homeRows() { const needs = [], ment = [], news = [];
  for (const [k, runs] of runsSeen) { const p = placeFromKey(k); if (!p) continue; const r = runs.find((x) => (x.status === "queued" || x.status === "running") && x.waiting), where = `${p.stream} / ${topicLabel(p.topic)}`;
    if (r) { const c = places.get(k), q = c && c.msgs.filter((m) => isOnix(m.sender_id) && QUESTION.test(m.content || "")).pop(), h = (q && questionOf(q)) || r.question;
      needs.push({k, g: mk("needs", "s28"), text: h ? `Onix asks: ${h}` : "Onix is waiting for your answer", where, verb: "Answer", go: true, t: r.date_updated}); }
    else if (marks.get(k) === "failed") needs.push({k, g: mk("failed", "s28"), text: "Onix's run failed", where, verb: "Open", t: 0}); }
  for (const m of [...mentions.values()].sort((a, b) => b.id - a.id)) { const k = placeOfMsg(m), set = unread.get(k); if (!set || !set.has(m.id)) continue; const p = placeFromKey(k); if (!p) continue;
    ment.push({k, g: faceOf(m.sender_id), text: `${nameOf(m.sender_id)}: ${plainOf(m.content).slice(0, 120)}`, where: p.kind === "topic" ? `${p.stream} / ${topicLabel(p.topic)}` : "Direct message", time: m.timestamp}); }
  for (const [k, set] of unread) { if (!set.size || !k.startsWith("s:")) continue; const p = placeFromKey(k); if (!p || !p.repo || ment.some((x) => x.k === k)) continue; news.push({k, g: ic("repo"), text: `${set.size} new ${set.size === 1 ? "message" : "messages"} in ${topicLabel(p.topic)}`, where: p.stream, n: set.size}); }
  news.sort((a, b) => b.n - a.n); return {needs, ment, news}; }
/* the question's own words: the first "**header** — question" line of Onix's message */
function questionOf(m) { tpl.innerHTML = m.content || ""; const p = [...tpl.content.querySelectorAll("p")].find((x) => x.querySelector("strong") && / — /.test(x.textContent)); return p ? p.textContent.split(" — ").slice(1).join(" — ").trim() : ""; }
const hrowHTML = (r) => `<div class="hrow${r.verb ? " act" : ""}" role="button" tabindex="${r.verb ? -1 : 0}" data-k="${esc(r.k)}"><span class="g">${r.g}</span><span class="tx"><p>${esc(r.text)}</p><span class="wh">${esc(r.where)}</span></span>${r.verb ? `<span class="v"><button class="${r.go ? "go" : "lk"}" data-k="${esc(r.k)}"${r.verb === "Answer" ? " data-answer" : ""}>${r.verb}</button></span>` : r.time ? `<time>${hhmm(r.time)}</time>` : ""}</div>`;
function drawHome() { if (!place || place.kind !== "home") return; const box = $("#home"), {needs, ment, news} = homeRows(); let h = '<div class="home">';
  if (needs.length) h += needs.map(hrowHTML).join(""); h += `<div class="gh">Mentions of you</div>` + (ment.length ? ment.map(hrowHTML).join("") : '<p class="note">No unread mentions.</p>');
  h += `<div class="gh">New in worktrees</div>` + (news.length ? news.map(hrowHTML).join("") : '<p class="note">Nothing new.</p>');
  if (!homeScanned) h += '<p class="note">Looking at every worktree\'s runs</p>'; h += "</div>"; if (box._sig === h) return; box._sig = h; box.innerHTML = h; host(box);
  const n = needs.filter((x) => x.verb === "Answer").length, sum = n ? `${n === 1 ? "One question waits" : n + " questions wait"} for you.` : needs.length ? "A run failed." : "Nothing waits for you.";
  const say = $(".hd .say, .hd .l2 .s"); if (say && say.textContent !== sum) { hd._sig = ""; drawHeader(); } drawHomeTrail(); }
let homeScanned = false, homeTimer = 0;
function drawHomeRow() { clearTimeout(homeTimer); homeTimer = setTimeout(() => { drawHome(); drawHomeTrail(); if (place && place.kind === "home") { hd._sig = ""; drawHeader(); drawSide(); } }, 50); }
function drawHomeTrail() { const r = $('.row[data-k="home"]', nav); if (!r) return; const n = [...marks.values()].filter((v) => v === "needs").length, old = $(":scope > .mk", r); if (!!old === !!n) return; if (old) old.remove(); if (n) { r.insertAdjacentHTML("beforeend", mk("needs")); M.appear(r.lastElementChild); } }
async function scanAll() { await Promise.all([scanLive(), ...[...repoStreams].map((sid) => loadTopics(sid))]); homeScanned = true; drawHomeRow(); }   /* the repositories' topics name the rows */
async function fetchMentions() { try { const r = await api("GET", "messages", {narrow: [{operator: "is", operand: "mentioned"}], anchor: "newest", num_before: 60, num_after: 0, apply_markdown: true, client_gravatar: false}); for (const m of r.messages) if (!(m.flags || []).includes("read")) mentions.set(m.id, m); } catch (e) { return; } drawHomeRow(); }

/* ---------- partners: the Pokémon each person chose (the r4 partner and sprite studies) ----------
   The pack (species.json and the sprites) is not in git: the server serves it from its git-ignored sprite directory to
   signed-in people (zerver/views/armada_beta.py, /beta/sprites/...), and partners come from GET armada/partners. With no
   pack, or a server without the agent, every face stays initials and nothing here draws.
   A face is a sprite at whole CSS pixels (one sprite pixel a CSS pixel; two in the picker's column and the card), in a box
   the size of the face it replaces, so a partner arriving or evolving moves nothing. The sprite stands on its feet at the
   box's foot, centred on what it draws (its content box, measured by tools/armada/sprites.py), in a window it may overflow
   upward in the gutter and is cut by in lists; it never catches the pointer. Still at rest; it hops (+1, -3 px, 133 ms a
   step) under the pointer or focus, while its person types, when their message lands, and on my send. Onix's face stays
   still while its run is live: the ball is the run's one loop (MARKS.md, direction A). */
const SPRITE_V = DATA.spriteVersion || "";
let pack = null, partners = null, partnersAt = 0;
const OFFERED = [];                                        /* species ids the picker offers: first forms the pack draws, Onix's line left out */
const spritePath = (path) => "/beta/sprites/" + path.split("/").map(encodeURIComponent).join("/");
const spriteSrc = (sid, shiny) => { const sp = pack && pack.species[sid] && pack.species[sid].sprite; return !sp ? "" : spritePath(shiny && sp.shiny ? sp.shiny : sp.regular); };
/* a sprite that does not load (a pack missing a file, a server that lost it) is never an empty box: every face drawn from it
   goes back to initials where it stands, and stays so on this page */
const brokenSprites = new Set();
let facesDue = false;
document.addEventListener("error", (e) => { const img = e.target; if (!(img instanceof HTMLImageElement) || !img.closest(".pk")) return; brokenSprites.add(img.getAttribute("src"));
  if (!facesDue) { facesDue = true; requestAnimationFrame(() => { facesDue = false; refreshFaces(); drawMeRow(); }); } }, true);
const agentLine = () => (pack && pack.species["95"] ? pack.species["95"].line : 95);
function partnerOf(id) { if (!pack || !partners) return null;
  if (isOnix(id)) { const a = partners.agent; return a && a.species && pack.species[a.species] ? {species: a.species, shiny: false, stone: "flint"} : null; }
  const p = partners.partners[id]; return p && pack.species[p.species] && pack.species[p.species].sprite ? p : null; }
const speciesName = (sid) => (pack && pack.species[sid] ? pack.species[sid].name : "");
/* the sprite in a window w by h (CSS px) at k CSS px a sprite pixel: feet on the window's foot, centred on the drawing */
function spriteHTML(sid, shiny, k, w, h, lazy) { const s = pack.species[sid], sp = s && s.sprite; if (!sp) return "";
  const useShiny = !!shiny && !!sp.shiny, box = useShiny ? sp.shiny_box : sp.box, [bx, by, bw, bh] = box;
  const left = Math.round(w / 2 - (bx + bw / 2) * k), top = h - (by + bh) * k;
  return `<img src="${esc(spriteSrc(sid, useShiny))}" alt="" draggable="false" decoding="async"${lazy ? ' loading="lazy"' : ""} style="left:${left}px;top:${top}px;width:${sp.size[0] * k}px;height:${sp.size[1] * k}px">`; }
/* the windows: [box, window width, window height] by face size. 28 is the gutter's (40 wide, cut at the sides only, so a
   face never reaches the lane); 20 and 18 are a list's and a caption's (cut on every side). */
const PK = {28: [28, 40, 32], 20: [20, 28, 28], 18: [18, 22, 20]};
function pkFace(p, size, cls, pres, uid) { const [b, w, h] = PK[size] || PK[28], img = spriteHTML(p.species, p.shiny, 1, w, h); if (!img) return "";
  return `<i class="pk s${size} ${(cls || "").replace(/\bs\d+\b/g, "")} ${pres || ""}" data-uid="${uid}" data-fs="${esc(p.species)}:${p.shiny ? 1 : 0}" aria-hidden="true"><span class="win" style="left:${(b - w) / 2}px;top:${b - h}px;width:${w}px;height:${h}px">${img}</span></i>`; }
/* a large drawing: the picker's cells (1x) and its column and the card (2x), the window the whole canvas */
function pkBig(sid, shiny, k, cls = "", lazy = false) { const sp = pack.species[sid] && pack.species[sid].sprite, w = 68 * k, h = 56 * k; if (!sp) return "";
  return `<i class="pk big ${cls}" style="width:${w}px;height:${h}px" aria-hidden="true"><span class="win" style="left:0;top:0;width:${w}px;height:${h}px">${spriteHTML(sid, shiny, k, w, h, lazy)}</span></i>`; }
/* a picker cell's drawing: two CSS px a sprite pixel (SPRITES.md section 5), the cell's width, as tall as the drawing; a drawing
   wider than the cell is cut at its sides, never scaled */
const CELL_W = 96;
function pkCell(sid) { const sp = pack.species[sid].sprite, h = sp.box[3] * 2;
  return `<i class="pk big" style="width:${CELL_W}px;height:${h}px" aria-hidden="true"><span class="win" style="left:0;top:0;width:${CELL_W}px;height:${h}px">${spriteHTML(sid, false, 2, CELL_W, h, true)}</span></i>`; }
/* one hop, two steps: for a message that lands and for my send. Reduced motion: none. */
function hopOnce(face) { const img = face && face.querySelector && face.querySelector(".pk img"); if (!img || M.reduced()) return;
  img.animate([{transform: "translateY(1px)", easing: "step-end"}, {transform: "translateY(-3px)", offset: 0.5, easing: "step-end"}, {transform: "translateY(0)"}], {duration: 266 * M.SLOW}); }

async function loadPartners() { if (!SPRITE_V) return;
  try { const [p, st] = await Promise.all([fetch(`/beta/sprites/species.json?v=${encodeURIComponent(SPRITE_V)}`, {credentials: "same-origin", redirect: "error"}).then((r) => (r.ok ? r.json() : null)), api("GET", "armada/partners")]);
    if (!p || !p.species || !st.on) return; pack = p; partners = st; partnersAt = performance.now();
    OFFERED.length = 0; for (const [sid, s] of Object.entries(pack.species)) if (s.first && s.sprite && s.line !== agentLine()) OFFERED.push(sid);
    OFFERED.sort((a, b) => pack.species[a].num - pack.species[b].num || a.localeCompare(b, "en", {numeric: true})); }
  catch (e) { pack = null; partners = null; } }
/* the team's partners change rarely (a choice, an evolution): they are asked for again every two minutes, on coming back to
   the page, a little after someone speaks, and after my own send and reaction (which count, so mine are folded then) */
let partnerTimer = 0;
async function fetchPartners(fold) { partnersAt = performance.now(); try { applyPartners(await api(fold ? "POST" : "GET", fold ? "armada/partner/fold" : "armada/partners")); } catch (e) {} }
function refreshPartners(o = {}) { if (!pack) return; clearTimeout(partnerTimer); partnerTimer = setTimeout(() => {
    if (!o.force && performance.now() - partnersAt < 30000) return; fetchPartners(o.fold); }, o.wait ?? 1500); }
/* the caption "Evolved into X" is laid out with its bubble, never added under a bubble already drawn. So a batch of events
   that brings a message from someone who can evolve their partner now (the server's may_change), or that comes while what
   the page knows of partners is over PARTNERS_STALE_MS old, waits for the team's partners first: one request, at most
   PARTNERS_FIRST_MS. Messages from Onix and my own never wait (my bubble was drawn when I pressed Return). Four seconds
   after someone else's message the page asks again, after the fold their own page makes two seconds after their send:
   a level that a message crossed is known before that person's next message comes. */
const PARTNERS_FIRST_MS = 400, PARTNERS_STALE_MS = 15000;
async function partnersFirst(events) { if (!pack || !partners) return;
  const stale = performance.now() - partnersAt > PARTNERS_STALE_MS;
  const due = events.some((ev) => { if (ev.type !== "message") return false; const id = ev.message.sender_id; if (id === ME || isOnix(id)) return false; const p = partners.partners[id]; return stale || !!(p && p.may_change); });
  if (due) await Promise.race([fetchPartners(false), sleep(PARTNERS_FIRST_MS)]); }
function applyPartners(st) { if (!st || !st.on || !pack) return; const before = JSON.stringify(partners && partners.partners) + JSON.stringify(partners && partners.agent); partners = st; partnersAt = performance.now();
  if (before !== JSON.stringify(st.partners) + JSON.stringify(st.agent)) refreshFaces();
  drawMeRow(); if (card) drawCard(); if (place && place.kind === "partner") { drawPicker(); drawSide(); drawPickerBand(); } momentDue(); }
const me_ = () => (partners && partners.me) || null;
/* every face on the page that is out of date is redrawn where it stands (same box), on the fade; bubbles take the new stone */
function refreshFaces() {
  for (const f of $$("[data-uid][data-fs]")) { if (!(+f.dataset.uid > 0)) continue; const uid = +f.dataset.uid, cls = f.getAttribute("class") || "", size = f.matches(".pk") ? +((/\bs(\d+)\b/.exec(cls) || [])[1] || 28) : f.matches("svg") ? (/\bs(\d+)\b/.exec(cls) ? +/\bs(\d+)\b/.exec(cls)[1] : 28) : faceSize(cls);
    const pres = /\bon\b/.test(cls) ? "on" : /\baway\b/.test(cls) ? "away" : "", keep = cls.split(/\s+/).filter((c) => /^s\d+$/.test(c)).join(" ");
    const t = el("template"); t.innerHTML = faceOf(uid, keep || (size === 28 ? "" : "s" + size), pres); const neu = t.content.firstElementChild; if (!neu || neu.dataset.fs === f.dataset.fs) continue;
    for (const c of cls.split(/\s+/)) if (c && !/^(av|pk|sigil|agent|in-disc|on|away|s\d+|p-\w+|big)$/.test(c) && !neu.classList.contains(c)) neu.classList.add(c);
    f.replaceWith(neu); if (!M.reduced()) M.appear(neu, 0, T["fade-in"]); }
  for (const cl of $$(".cl.them[data-sid]")) { const want = "s-" + stoneOf(+cl.dataset.sid); if (!cl.classList.contains(want)) { [...cl.classList].filter((c) => c.startsWith("s-")).forEach((c) => cl.classList.remove(c)); cl.classList.add(want); } }
  drawNav(); if (P && place && place.kind !== "home" && place.kind !== "partner") drawSide(); }

/* ----- the caption the team sees: my next bubble after an evolution carries one line, laid out with that bubble ----- */
function evoCaption(m) { if (typeof m.sender_id !== "number" || isOnix(m.sender_id)) return null; const p = partnerOf(m.sender_id); if (!p || !p.evolved_at) return null;
  const since = Math.floor(p.evolved_at); if (m.timestamp < since) return null;   /* a message's time is in whole seconds; the evolution's is not */
  const c = msgIndex.get(m.id); if (!c) return null; const first = c.msgs.find((x) => x.sender_id === m.sender_id && x.timestamp >= since && !x.thread_root_message_id); if (first !== m) return null;
  const e = el("div", "evo", `${faceOf(m.sender_id, "s18")}<span>${p.evolved_kind === "shiny" ? esc(`${speciesName(p.species)} turned shiny`) : esc(`Evolved into ${speciesName(p.species)}`)}</span>`); e.dataset.mid = m.id; return e; }

/* ----- the picker: a place (#partner), not a dialog. Starters first, then each generation; the field is its search ----- */
const PARTNER = {key: "partner", kind: "partner", repo: false};
const pick = {seg: "starters", q: "", sel: null, picks: {}, back: null, refused: new Set()};
const GEN_NAMES = ["", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
/* the lines held, by line: the holders the server lists, and a line the server refused me that it does not list (held by
   someone I cannot see), as Someone's */
const takenLines = () => { const out = new Map(); if (!partners) return out; for (const [uid, p] of Object.entries(partners.partners)) { const s = pack.species[p.species]; if (s) out.set(s.line, +uid); } for (const l of pick.refused) if (!out.has(l)) out.set(l, -1); return out; };
/* the first form of a species' line: what the picker offers for it */
const firstOf = (sid) => { let cur = sid; for (let i = 0; i < 6 && pack.species[cur] && pack.species[cur].prevo && pack.species[pack.species[cur].prevo]; i++) cur = pack.species[cur].prevo; return cur; };
/* the search: a name (prefix, then word start, then substring) or a number (that Pokédex number and no other), across every
   form in the pack. A later form finds its line's first form, with a hint saying which to choose. */
function matches(q) { q = q.trim().toLowerCase(); if (!q) return null; const n = /^#?0*(\d+)$/.exec(q), found = [], hints = [], all = Object.keys(pack.species);
  const add = (sid) => { const first = firstOf(sid), name = pack.species[sid].name, own = rank(first) >= 0;
    if (!OFFERED.includes(first)) { const h = `The agent has ${pack.species[first] ? pack.species[first].name : name}'s line.`; if (pack.species[first] && pack.species[first].line === agentLine() && hints.length < 2 && !hints.includes(h)) hints.push(h); return; }
    if (!found.includes(first)) found.push(first); if (first !== sid && !own && hints.length < 2 && !hints.some((h) => h.endsWith(`choose ${pack.species[first].name}`) || h.endsWith(`choose ${pack.species[first].name}.`))) hints.push(endSentence(`${name} grows from ${pack.species[first].name}: choose ${pack.species[first].name}`)); };
  const rank = (sid) => { if (n) return String(pack.species[sid].num) === n[1] ? 0 : -1; const l = pack.species[sid].name.toLowerCase(); return l.startsWith(q) ? 0 : new RegExp("(^|[\\s(-])" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(l) ? 1 : l.includes(q) ? 2 : -1; };
  const key = (sid) => [rank(sid), pack.species[sid].form ? 1 : 0, pack.species[sid].num];
  all.map((sid) => [sid, key(sid)]).filter((x) => x[1][0] >= 0).sort((a, b) => a[1][0] - b[1][0] || a[1][1] - b[1][1] || a[1][2] - b[1][2]).forEach((x) => add(x[0]));
  return {found, hints}; }
function pickerSections() { const m = matches(pick.q); if (m) return [["Found", m.found, m.hints]];
  const starters = (pack.starters || []).filter((sid) => OFFERED.includes(sid)), out = [];
  if (pick.seg === "starters" || pick.seg === "all") out.push(["Starters", starters]);
  for (let g = 1; g <= 9; g++) if (pick.seg === "all" || pick.seg === String(g)) out.push([`Generation ${g}`, OFFERED.filter((sid) => pack.species[sid].gen === g)]);
  return out; }
const visibleCells = () => pickerSections().flatMap((x) => x[1]);
function cellHTML(sid, taken) { const holder = taken.get(pack.species[sid].line), mine = holder === ME, other = holder != null && !mine;
  return `<button class="pkc${other ? " taken" : ""}${sid === pick.sel ? " sel" : ""}" data-sid="${esc(sid)}" role="option" aria-selected="${sid === pick.sel}" tabindex="-1">${pkCell(sid)}<span class="nm">${esc(pack.species[sid].name)}</span><span class="cap">${other ? esc(nameOf(holder).split(/[ ,]/)[0]) : mine ? "Yours" : ""}</span></button>`; }
function drawPicker() { const box = $("#picker"); if (!box || !pack) return; const taken = takenLines(), secs = pickerSections(), y = panes.Partner.scrollTop;
  if (!pick.sel || !visibleCells().includes(pick.sel)) pick.sel = visibleCells().find((sid) => !taken.has(pack.species[sid].line)) || visibleCells()[0] || null;
  let h = `<div class="pkp"><p class="pkintro">Your partner is your face here. It levels up as you use Armada, and evolves.</p><div class="pkdetail">${detailHTML()}</div>`;
  for (const [title, list, hints] of secs) h += `<h2 class="pkh">${esc(title)}</h2>` + (hints && hints.length ? `<p class="note pkhint">${esc(hints.join(" "))}</p>` : "") + (list.length ? `<div class="pkg" role="listbox" aria-label="${esc(title)}">${list.map((sid) => cellHTML(sid, taken)).join("")}</div>` : '<p class="note">Nothing by that name or number.</p>');
  box.innerHTML = h + "</div>"; host(box); panes.Partner.scrollTop = y; drawPickerSide(); syncPickVerb(); drawPickerBand(); }
function selectCell(sid, o = {}) { if (!sid) return; pick.sel = sid; for (const b of $$(".pkc", panes.Partner)) { const on = b.dataset.sid === sid; b.classList.toggle("sel", on); b.setAttribute("aria-selected", String(on)); }
  const b = $$(".pkc", panes.Partner).find((x) => x.dataset.sid === sid); if (b && o.scroll) b.scrollIntoView({block: "nearest"}); drawPickerSide(); syncPickVerb(); }
/* arrows move through the grid as it is drawn: Left and Right in reading order, Up and Down to the nearest cell above or below */
function movePick(key) { const cells = $$(".pkc", panes.Partner); if (!cells.length) return; const i = Math.max(0, cells.findIndex((b) => b.dataset.sid === pick.sel)), cur = cells[i];
  let n = null; if (key === "ArrowLeft") n = cells[i - 1]; else if (key === "ArrowRight") n = cells[i + 1];
  else { const r = cur.getBoundingClientRect(), down = key === "ArrowDown"; let best = null, bd = 1e9;
    for (const b of cells) { const q = b.getBoundingClientRect(); if (down ? q.top <= r.top + 4 : q.top >= r.top - 4) continue; const dy = Math.abs(q.top - r.top), d = dy * 1000 + Math.abs(q.left - r.left); if (d < bd) { bd = d; best = b; } } n = best; }
  if (n) selectCell(n.dataset.sid, {scroll: true}); }
/* the line from the chosen form, with the level each form arrives at, and the branches my level passes on the way */
function lineOf(sid) { const out = [], L = (me_() && me_().evolve_levels) || [16, 30]; let cur = [sid];
  while (cur.length && out.length < 4) { out.push({forms: cur, level: out.length ? L[out.length - 1] || null : null}); cur = cur.length === 1 ? nextForms(cur[0]) : []; }
  return out; }
function nextForms(sid) { const out = []; for (const e of (pack.species[sid] || {evos: []}).evos) { if (!pack.species[e]) continue; if (pack.species[e].sprite) out.push(e); else out.push(...nextForms(e).filter((x) => !out.includes(x))); } return out; }
/* what my level makes of a choice, as the server will: as far along the line as it allows, with my picks at branches */
function arrivalOf(sid) { const me = me_(), L = (me && me.evolve_levels) || [16, 30], level = me ? me.level : 1, stages = L.filter((x) => level >= x).length; let cur = sid, branch = null;
  for (let i = 0; i < stages; i++) { const n = nextForms(cur); if (!n.length) break; if (n.length === 1) { cur = n[0]; continue; } const p = pick.picks[cur]; if (p && n.includes(p)) { cur = p; continue; } branch = {at: cur, choices: n}; break; }
  return {sid: cur, branch}; }
/* how many evolutions lie between the chosen form and this one (the server's steps_from) */
const stepsFrom = (first, sid) => { let n = 0, cur = sid; while (cur && cur !== first && pack.species[cur]) { cur = pack.species[cur].prevo; n++; } return cur === first ? n : 0; };
const typeWords = (sid) => pack.species[sid].types.join(" and ");
const STONE_OF_TYPE = {Grass: "moss", Bug: "moss", Water: "larimar", Ice: "larimar", Flying: "lapis", Dragon: "lapis", Psychic: "amethyst", Poison: "amethyst", Ghost: "amethyst", Fire: "rose", Fighting: "rose", Fairy: "rose", Normal: "flint", Rock: "flint", Steel: "flint", Dark: "flint", Electric: "citrine", Ground: "citrine"};
const stoneOfSpecies = (sid) => { const t = pack.species[sid].types; return STONE_OF_TYPE[t[0] === "Normal" && t[1] ? t[1] : t[0]] || "flint"; };
function detailHTML() { const sid = pick.sel; if (!sid || !pack.species[sid]) return ""; const s = pack.species[sid], taken = takenLines(), holder = taken.get(s.line), arr = arrivalOf(sid);
  const line = lineOf(sid).map((st) => `<li>${st.forms.length === 1 ? pkFace({species: st.forms[0], shiny: false}, 20, "", "", 0) : ic("branch")}<span class="t">${st.forms.map((f) => esc(pack.species[f].name)).join(" or ")}</span><span class="e">${st.level ? `level ${st.level}` : "now"}</span></li>`).join("");
  const branch = arr.branch ? `<p class="pkbr">Your level passes ${esc(pack.species[arr.branch.at].name)}'s evolution. Choose what it becomes:</p><div class="seg pkpicks">${arr.branch.choices.map((c) => `<button data-pickfor="${esc(arr.branch.at)}" data-pick="${esc(c)}">${esc(pack.species[c].name)}</button>`).join("")}</div>` : "";
  const sample = `<div class="pksample cl them s-${stoneOfSpecies(arr.sid)}">${pkFace({species: arr.sid, shiny: false}, 28, "", "", 0)}<div class="bl"><p class="b">Morning, team.</p></div></div>`;
  return `<div class="pkbig2">${pkBig(sid, false, 2)}</div><p class="pkno"><span class="fig">#${String(s.num).padStart(3, "0")}</span> ${esc(s.name)}</p><p class="pkty">${esc(typeWords(sid))}. Your bubbles would be ${stoneOfSpecies(arr.sid)}.</p>`
    + (holder != null && holder !== ME ? `<p class="pkty bad">${esc(nameOf(holder))} has this line.</p>` : "")
    + `<div class="sh"><span>Line</span></div><ul class="ls pkline">${line}</ul>${branch}<div class="sh"><span>How it looks</span></div>${sample}`; }
function drawPickerSide() { const d = $(".pkdetail", panes.Partner); if (d) { d.innerHTML = detailHTML(); host(d); } if (place && place.kind === "partner") drawSide(); }
function pickerSideHTML() { const taken = [...takenLines()].filter(([, uid]) => partners.partners[uid]).sort((a, b) => nameOf(a[1]).localeCompare(nameOf(b[1])));
  const rows = taken.map(([, uid]) => `<li>${faceOf(uid, "s20")}<span class="t">${esc(nameOf(uid))}</span><span class="e"><span class="d">${esc(speciesName(partners.partners[uid].species))}</span></span></li>`).join("");
  const onix = [...armadaBots][0], a = partners.agent && partners.agent.species;
  return `<section class="sec pkcol">${detailHTML()}</section><section class="sec foot"><div class="sh"><span>Taken</span></div><ul class="ls">${rows}${onix && a ? `<li>${faceOf(onix, "s20")}<span class="t">Onix</span><span class="e"><span class="d">${esc(speciesName(a))}</span></span></li>` : ""}</ul></section>`; }
const pickFree = () => { const s = pick.sel && pack.species[pick.sel]; if (!s) return false; const h = takenLines().get(s.line); return h == null || h === ME; };
/* a sentence ends in one full stop, also after a name that ends in one ("Mime Jr.") */
const endSentence = (t) => (t.endsWith(".") ? t : `${t}.`);
function syncPickVerb() { if (place && place.kind === "partner") go.classList.toggle("armed", pickFree()); }
function drawPickerBand() { const b = band && band.pk; if (!b) return; const on = !!place && place.kind === "partner" && !!pack; b.hidden = !on; if (!on) return; const me = me_(), sid = pick.sel;
  if (!sid || !me) { b.textContent = ""; return; } const holder = takenLines().get(pack.species[sid].line);
  if (holder != null && holder !== ME) { b.textContent = `${nameOf(holder)} has ${pack.species[sid].name}'s line. Choose another.`; return; }
  const arr = arrivalOf(sid), word = me.xp ? `You are level ${me.level} from your history.` : "You start at level 1.";
  b.textContent = endSentence(`${word} ${pack.species[sid].name} arrives as ${pack.species[arr.sid].name}${arr.branch ? `, and you choose its evolution` : ""}`); }
/* the picker's header: line 2 is a switch of text controls (Starters, All, the nine generations), and Later at the right */
function pickerHead(h) { const say = $(".l2 .say", h), end = $(".l2 .end", h); if (!say || !end) return; const has = !!(me_() && me_().species);
  const segs = [["starters", "Starters"], ["all", "All"], ...GEN_NAMES.slice(1).map((g) => [g, g])];
  say.outerHTML = `<div class="seg pkseg" role="tablist" aria-label="Show">${segs.map(([k, l]) => `<button role="tab" data-seg="${k}" aria-selected="${pick.seg === k && !pick.q}"${pick.seg === k && !pick.q ? ' class="on"' : ""}${/^\d$/.test(k) ? ` aria-label="Generation ${k}"` : ""}>${l}</button>`).join("")}</div>`;
  end.innerHTML = `<button class="lk later" data-later>${has ? "Keep " + esc(speciesName(me_().species)) : "Later"}</button>`; }
async function choosePick() { if (!pickFree()) { M.refuse(sh); S.play("nope"); return; } const sid = pick.sel, into = Object.values(pick.picks);
  try { applyPartners(await api("POST", "armada/partner", {species: sid, into})); S.play("tick"); leavePicker(); }
  catch (e) { note(e.message); if (e.status !== 400) return;
    /* refused (someone took the line first): the picker shows who has it now; a line the server still does not list as held
       (its holder is someone I cannot see) is drawn as taken by Someone, so Choose is not armed on it again */
    const line = pack.species[sid].line; await fetchPartners(false); if (!takenLines().has(line)) { pick.refused.add(line); if (place && place.kind === "partner") { drawPicker(); drawSide(); } } } }
async function putOff() { const me = me_(); if (me && !me.species && !me.later) { try { applyPartners(await api("POST", "armada/partner", {later: true})); } catch (e) { note(e.message); return; } } leavePicker(); }
function leavePicker() { const back = pick.back && pick.back.kind !== "partner" ? pick.back : null; pick.q = ""; tx.textContent = ""; syncField(); openPlace(back || HOME); }
/* changing later: the same place with my partner selected, under the switch that shows it */
function selectMine() { const me = me_(); if (!me || !me.chosen || !pack.species[me.chosen]) return; pick.sel = me.chosen; pick.q = ""; if (!(pack.starters || []).includes(me.chosen)) pick.seg = String(pack.species[me.chosen].gen); }
function openPicker() { if (!pack) return; closeCard(); if (place && place.kind !== "partner") pick.back = place; selectMine(); openPlace(PARTNER); }

/* ----- my partner card: a raised card above my own face in the account row. My level and experience, what comes next, my
   stone, today's experience, Change partner; Evolve when I held it; the branch's choices when it waits on me. ----- */
let card = null;
function drawMeRow() { const r = $(".row.me", nav); if (!r) return; const me = me_(), t = el("template"); t.innerHTML = faceOf(ME, "s20"); const f = $(":scope > .av, :scope > .pk", r), neu = t.content.firstElementChild;
  if (f && neu && f.dataset.fs !== neu.dataset.fs) f.replaceWith(neu);
  const wait = !!(me && me.ready && !me.held && me.ready.choices.length > 1), mark = $(":scope > .mk", r); if (wait && !mark) r.insertAdjacentHTML("beforeend", mk("needs")); else if (!wait && mark) mark.remove();
  r.dataset.tip = !pack ? "Signed in as " + meName : me && me.species ? "Your partner" : "Choose your partner"; }
function openCard() { if (card) { closeCard(); return; } if (!pack) return; const me = me_(); if (!me || !me.species) { openPicker(); return; }
  card = el("div", "pcard raised float"); card.setAttribute("role", "dialog"); card.setAttribute("aria-label", "Your partner"); document.body.append(card); drawCard(); M.release(card, {y: T["pane-rise"] || 4}, "move"); M.appear(card); }
function closeCard() { if (!card || card.classList.contains("moment")) return; const c = card; card = null; c.inert = true; M.leave(c); }
function drawCard() { if (!card || card.classList.contains("moment")) return; const me = me_(); if (!me || !me.species) { closeCard(); return; } const s = pack.species[me.species], r = me.ready;
  const into = me.next_level_xp ? Math.max(0, Math.min(1, (me.xp - me.level_xp) / (me.next_level_xp - me.level_xp))) : 1;
  const nx = nextForms(me.species), L = me.evolve_levels || [16, 30], at = L[stepsFrom(me.chosen, me.species)];
  const next = r && r.kind === "evolve" ? "" : nx.length && at ? `Next: ${nx.map((x) => pack.species[x].name).join(" or ")} at level ${at}.` : nx.length ? "" : me.shiny ? "The end of its line, and shiny." : "The end of its line.";
  let act = ""; if (r && r.choices.length > 1) act = `<p>${esc(s.name)} can evolve. Choose what it becomes:</p><div class="seg pkpicks">${r.choices.map((c) => `<button data-evolve="${esc(c)}">${esc(pack.species[c].name)}</button>`).join("")}</div>`;
  else if (r && me.held) act = `<p>You kept ${esc(s.name)} as it is.</p><div class="btns"><button class="go" data-evolve="${esc(r.choices[0])}">${r.kind === "shiny" ? "Make it shiny" : "Evolve"}</button></div>`;
  card.innerHTML = `<div class="ih"><b>${esc(s.name)}${me.shiny ? " (shiny)" : ""}</b><span class="e">Level <span class="fig">${fig(me.level)}</span></span></div><div class="stage">${pkBig(me.species, me.shiny, 2)}</div>`
    + `<div class="xpbar" role="img" aria-label="${me.xp} experience"><i style="width:${Math.round(into * 100)}%"></i></div><p class="cap">${me.next_level_xp ? `${me.xp - me.level_xp} of ${me.next_level_xp - me.level_xp} to level ${me.level + 1}` : "Level 100"}. Today +${me.today_xp}.</p>`
    + (next ? `<p class="cap">${esc(next)}</p>` : "") + `<p class="cap">Your bubbles are ${esc(partnerOf(ME) ? partnerOf(ME).stone : stoneOfSpecies(me.species))}, as others see them.</p>${act}<div class="btns"><button class="btn" data-change>Change partner</button></div>`;
  host(card); }
async function evolveTo(into, hold) { try { applyPartners(await api("POST", "armada/partner/evolve", hold ? {hold: true} : {into})); } catch (e) { note(e.message); } }

/* ----- the moment: about 1.4 s in the card, only for me, only when I am idle. Outlines of the old and new forms trade places
   faster and faster, eight pixels burst, the new form lands in colour with one hop. Any key or a press outside ends it where it
   stands (the evolution happens); Stop, or Esc with the field empty, keeps the old form (held). It never takes focus. ----- */
let moment = null, lastKey = -1e9, pressing = false, momentTimer = 0;
function momentDue() { clearTimeout(momentTimer); const me = me_(); if (!pack || !me || !me.ready || me.held || moment) return; if (me.ready.choices.length > 1) { drawMeRow(); return; }
  const idle = document.visibilityState === "visible" && !pressing && performance.now() - lastKey >= 3000 && !(document.activeElement === tx && tx.textContent.trim()) && !pal && !menu && !pop && !dialogOpen && !info && !(place && place.kind === "partner");
  if (idle) playMoment(me.ready.choices[0]); else momentTimer = setTimeout(momentDue, 500); }
function playMoment(into) { const me = me_(), from = me.species, shiny = me.ready.kind === "shiny"; if (card) { card.remove(); card = null; }
  moment = {into, from, shiny, done: false, timers: []}; const c = card = el("div", "pcard raised float moment"); c.setAttribute("role", "status"); document.body.append(c);
  const oldImg = pkBig(from, me.shiny, 2, "old"), newImg = pkBig(into, shiny || me.shiny, 2, "new");
  c.innerHTML = `<div class="ih"><b>${esc(pack.species[from].name)} is evolving</b><span class="e">Level <span class="fig">${fig(me.level)}</span></span></div><div class="stage">${oldImg}${newImg}<span class="bits"></span></div><p class="say"></p><p class="cap"></p><div class="btns"><button class="btn" data-stop>Stop</button></div>`;
  const stage = $(".stage", c), o = $(".pk.old", stage), n = $(".pk.new", stage); n.hidden = true; M.release(c, {y: T["pane-rise"] || 4}, "move"); M.appear(c);
  const at = (ms, fn) => moment.timers.push(setTimeout(fn, ms * M.SLOW)); S.play("evolve");
  if (M.reduced()) { at(500, () => { o.hidden = true; n.hidden = false; M.appear(n, 0, 200); }); at(700, () => landMoment()); return; }
  at(340, () => o.classList.add("ghost")); let t = 340, showNew = false;
  for (const d of [240, 200, 160, 120, 90, 70, 60]) { t += Math.round(d * 0.81); at(t, () => { showNew = !showNew; o.hidden = showNew; n.hidden = !showNew; n.classList.add("ghost"); }); }
  at(1100, () => { const bits = $(".bits", stage); bits.innerHTML = "<i></i>".repeat(8); stage.classList.add("pulse"); });
  at(1380, () => landMoment()); }
function landMoment() { if (!moment || moment.done) return; const c = card, {from, into, shiny} = moment; moment.done = true; moment.timers.forEach(clearTimeout);
  const o = $(".pk.old", c), n = $(".pk.new", c); if (o) o.hidden = true; if (n) { n.hidden = false; n.classList.remove("ghost"); hopOnce(c.querySelector(".stage")); }
  const name = pack.species[into].name; $(".ih b", c).textContent = shiny ? `${name} (shiny)` : name; $(".say", c).textContent = shiny ? `${name} turned shiny.` : `${pack.species[from].name} evolved into ${name}.`;
  const nx = nextForms(into), L = (me_() && me_().evolve_levels) || [16, 30], lvl = L[stepsFrom(me_().chosen, into)];
  $("p.cap", c).textContent = `Your bubbles ${stoneOfSpecies(into) === stoneOfSpecies(from) ? "stay" : "are now"} ${stoneOfSpecies(into)}.${nx.length && lvl ? ` Next: ${nx.map((x) => pack.species[x].name).join(" or ")} at level ${lvl}.` : ""}`;
  const st = $("[data-stop]", c); if (st) st.remove(); evolveTo(into, false);
  setTimeout(() => endMoment(), 4000 * M.SLOW); }
/* a key or a press outside: the evolution happens and the card shows its end at once, then fades */
function cutMoment() { if (!moment) return; if (!moment.done) landMoment(); endMoment(true); }
function endMoment(now_) { if (!moment || !card) { moment = null; return; } if (!now_ && card.matches(":hover")) { setTimeout(() => endMoment(), 1000); return; } const c = card; card = null; moment = null; c.inert = true; M.leave(c); }
function holdMoment() { if (!moment || moment.done) return; moment.timers.forEach(clearTimeout); const c = card, from = moment.from; moment = null; c.classList.remove("moment");
  const o = $(".pk.old", c), n = $(".pk.new", c); if (o) { o.hidden = false; o.classList.remove("ghost"); } if (n) n.hidden = true; $(".stage", c).classList.remove("pulse"); $(".bits", c).innerHTML = "";
  $(".ih b", c).textContent = pack.species[from].name; $(".say", c).textContent = `${pack.species[from].name} stays as it is. Evolve it from this card when you like.`; evolveTo(null, true).then(() => drawCard()); }

/* ---------- the Lab: what is real, what is not, and the few real controls ---------- */
const NOT = ["New, the workspace menu, More in the header; settings", "The keyboard selection of messages; a reaction picker (React and a double press give 👍; a chip toggles)", "Queue (⌘Return), sub-agents, the trace, artifacts, monitors", "Diffs, Commit, pull requests, file editing, syntax colour in Files", "The checklist as a sheet below 1408 px", "Sending my own typing; presence after load; moved or renamed topics; channels I am not subscribed to", "The typing bubble growing into the message (it appears, it does not morph)"];
let lab;
function buildLab() {
  /* the standing label opens the beta's panel; beside it, a plain link back to the old interface */
  lab = el("div", "", `<button id="lab-t">Beta</button><div class="pn"></div>`); lab.id = "lab"; const wk_ = $("nav .top .wk-name"); if (wk_) wk_.after(lab); else document.body.append(lab);   /* fixed in place; in the DOM after the workspace's name, where it stands */
  $("#lab-t").onclick = () => { lab.classList.toggle("open"); drawLab(); };
  lab.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
  lab.addEventListener("click", (e) => { const b = e.target.closest("[data-set]"); if (!b) return; const [k, v] = b.dataset.set.split(":");
    if (k === "theme") { if (v === "auto") delete R.dataset.theme; else R.dataset.theme = v; store.set("theme", v); }
    if (k === "sound") S.set(!S.on());
    if (k === "motion") { if (R.dataset.motion === "reduced") { delete R.dataset.motion; store.set("motion", ""); } else { R.dataset.motion = "reduced"; store.set("motion", "reduced"); } M.stretch(); }
    drawLab(); ready(); });
}
function drawLab() { if (!lab || !lab.classList.contains("open")) return; const th = R.dataset.theme || "auto", last = stats.sends[stats.sends.length - 1], fw = stats.firstWords[stats.firstWords.length - 1], rn = stats.runs[stats.runs.length - 1];
  const btn = (set, label, on) => `<button data-set="${set}"${on ? ' class="on"' : ""}>${label}</button>`;
  $(".pn", lab).innerHTML = `<p>The beta of Armada's new interface, on this server's real data, signed in as <b>${esc(meName)}</b>. Settings and everything listed below stay in the <a href="/">old interface</a>.</p>
    <h4>Theme</h4>${["auto", "light", "dark"].map((v) => btn("theme:" + v, v[0].toUpperCase() + v.slice(1), th === v)).join("")}
    <h4>Sound and motion</h4>${btn("sound:x", S.on() ? "Sound is on" : "Sound is off", S.on())}${btn("motion:x", R.dataset.motion === "reduced" ? "Motion is reduced" : "Reduce motion", R.dataset.motion === "reduced")}
    <h4>Measured in this page</h4><p class="num">Last send: task ${last ? last.task + " ms" : "none yet"}, Return to bubble painted ${last && last.painted != null ? last.painted + " ms" : "none yet"}<br>First streamed event to its words painted: ${fw != null ? fw + " ms" : "none yet"}<br>Last run: ${rn ? `${rn.events} events in ${rn.seconds} s (${rn.perSecond} per second)` : "none yet"}<br>Events handled: ${stats.events}. Requests made: ${stats.api}.</p>
    <h4>Not in the beta yet</h4><ul>${NOT.map((x) => `<li>${x}</li>`).join("")}</ul>
    <p style="color:var(--ink-2)">Return sends. In a repository the Onix switch in the field is off until you turn it on there; on, Return starts a run, and while a run is live it steers it (⌘. stops it). Writing @Onix asks it either way. ⌘K jumps anywhere.</p>`; }
/* what Tab stops on, in document order: what the browser would stop on, shown and not inert */
const tabStops = () => $$("a[href], button, input, [tabindex], [contenteditable]").filter((x) => x.tabIndex >= 0 && !x.disabled && !x.hidden && !x.closest("[inert], [hidden]") && x.getClientRects().length > 0 && getComputedStyle(x).visibility !== "hidden");
function note(text) { M.say(go, text, 2400); }

/* ---------- start ---------- */
/* the themed marks (the ball for a live run, the egg for a queued one) are not in git: the server serves them from its
   git-ignored web/sprites/licensed/marks/ to signed-in people (zerver/views/armada_beta.py). They are read before the
   first draw, as data: only each path's tone and its drawing commands are kept. Without them the plain dot and ring are
   drawn (kit/marks.js). */
async function loadThemedMarks() {
  const get = async (name) => { try { const r = await fetch(`/beta/sprites/marks/${name}`, {credentials: "same-origin", redirect: "error"}); if (!r.ok) return null;
    const doc = new DOMParser().parseFromString(await r.text(), "image/svg+xml"); return doc.querySelector("parsererror") ? null : doc.documentElement; } catch (e) { return null; } };
  const paths = (root) => [...root.querySelectorAll("path")].map((x) => { const d = x.getAttribute("d") || "", t = x.getAttribute("class"); return /^[MmHhVvZz0-9.\s-]+$/.test(d) ? `<path${t === "t-b" || t === "t-h" ? ` class="${t}"` : ""} d="${d}"/>` : ""; }).join("");
  const [ball, egg] = await Promise.all([get("ball.svg"), get("egg.svg")]), themed = window.BETA_THEMED;
  if (ball) { const frames = [...ball.children].filter((g) => g.tagName === "g").map(paths); if (frames.length === 3 && frames.every(Boolean)) themed.live = {frames, rest: frames[1]}; }
  if (egg) { const d = paths(egg); if (d) themed.queued = {rest: d}; }
}
async function register() {
  const r = await api("POST", "register", {fetch_event_types: ["muted_users", "subscription", "realm_user", "presence", "update_message_flags", "message", "recent_private_conversations", "realm_bot", "stream"], event_types: ["message", "update_message", "reaction", "agent_run", "workspace_changed", "typing", "delete_message", "update_message_flags"], apply_markdown: true, client_gravatar: false,
    client_capabilities: {notification_settings_null: true, bulk_message_deletion: true, user_avatar_url_field_optional: false, stream_typing_notifications: true, user_settings_object: true, linkifier_url_template: true, user_list_incomplete: false, include_deactivated_groups: false, archived_channels: false, empty_topic_name: true}});
  if (!r.user_id || (ME && r.user_id !== ME)) { signedOut(); throw Object.assign(new Error("Signed out"), {status: 401}); }   /* no session: the server registered a spectator */
  queue = r.queue_id; lastEvent = r.last_event_id; skew = r.server_timestamp * 1000 - Date.now(); ME = r.user_id; meName = r.full_name; presences = r.presences || {};
  people.clear(); for (const u of [...(r.realm_users || []), ...(r.cross_realm_bots || [])]) people.set(u.user_id, {id: u.user_id, name: u.full_name, email: u.email, bot: u.is_bot, active: u.is_active !== false});
  armadaBots.clear(); (r.agent_user_ids || []).forEach((i) => armadaBots.add(i)); muted = new Set((r.muted_users || []).map((u) => u.id));
  streams.clear(); for (const s of r.subscriptions || []) if (!s.is_archived) streams.set(s.stream_id, s);
  repoStreams.clear(); (r.repository_stream_ids || []).forEach((i) => streams.has(i) && repoStreams.add(i));
  dms = (r.recent_private_conversations || []).slice(0, 12);
  unread.clear(); const u = r.unread_msgs || {}; for (const s of u.streams || []) unread.set(tkey(s.stream_id, s.topic), new Set(s.unread_message_ids)); for (const d of u.pms || []) unread.set(dkey([d.other_user_id]), new Set(d.unread_message_ids)); for (const d of u.huddles || []) unread.set(dkey(d.user_ids_string.split(",").map(Number).filter((i) => i !== ME)), new Set(d.unread_message_ids));
}
async function boot() {
  store.del("onix");   /* the old switch for every place */
  const bootEl = el("div", "", "Loading"); bootEl.id = "boot"; document.body.append(bootEl);
  try { await Promise.all([register(), loadThemedMarks(), loadPartners()]); api("GET", "armada/session").then((r) => { if (r.armada_session && r.armada_session.agent_mention) agentMention = r.armada_session.agent_mention; }).catch(() => {}); }
  catch (e) { if (signedOut.done) { bootEl.remove(); return; } bootEl.innerHTML = `<p>The beta could not load: ${esc(e.message)}. <a href="/">Open the old interface</a></p>`; return; }
  frame({nav: "", path: "", title: "", dist: "", commit: {who: "", sha: "", subj: "", age: ""}, tab: "Chat", checklist: {title: "Checklist"}, band: {}, composer: {text: "", verb: "Send", mode: "", focus: false}});
  main = $("main"); feed = $("#pane"); lane = $("#col"); nav = $("nav"); side = $("#side"); outline = $("#outline"); hd = $("main > header.hd");
  band = {el: $("main > .band"), ty: $("#ty"), run: $("#run"), jump: $("#jump"), clock: $("#clock"), st: $("#run-st"), act: $("#act"), stop: $("#stop")};
  band.pk = el("p", "pkb"); band.pk.hidden = true; $(".lane", band.el).append(band.pk);   /* the picker's one sentence */
  cz = $("main > .dock .cz"); tx = $(".tx", cz); sh = $(".sh", cz); go = $(".disc", cz); goc = $(".disc .cell", cz); ph = $(".ph", cz); toBtn = $(".k.to", cz); modeBtn = $(".k.mode", cz);
  panes = {Chat: feed}; $$("main > .pane[data-pane]").forEach((x) => (panes[x.dataset.pane] = x));
  $$(".slot > i, .pc > i").forEach((i) => i.classList.add("sz"));
  [band.run, band.ty, band.jump].forEach((e) => e && (e.inert = e.classList.contains("off")));
  /* the nav: the kit's top row, a scroller of real places, and me */
  const top = $(".top", nav); nav.textContent = ""; navScroll = el("div", "scroll"); navFixed = el("div", "fixed", `<button class="row" data-k="jump" data-tip="Jump to" data-key="⌘K">${ic("search")}<span class="lab">Jump to</span></button><button class="row" data-k="home">${ic("home")}<span class="lab">Home</span></button><a class="row old" href="/" data-tip="The old interface, with everything the beta does not have yet">${ic("open")}<span class="lab">Old interface</span></a>`);
  nav.append(top, navFixed, navScroll); const meRow = el("button", "row me", `${faceOf(ME, "s20")}<span class="lab">${esc(meName)}</span>`); nav.append(meRow); host(nav);
  $(".wk-name", top).dataset.tip = "The workspace menu is in the old interface"; $(".new", top).dataset.tip = "New: in the old interface for now"; meRow.dataset.tip = "Signed in as " + meName; drawMeRow();
  /* my face opens my partner's card (or the picker, before I have one); the picker's switch, Later, its cells; the card's controls */
  press(nav, ".row.me", () => { if (pack) openCard(); ready(); });
  press(main, "[data-seg]", (b) => { pick.seg = b.dataset.seg; if (pick.q) { pick.q = ""; tx.textContent = ""; syncField(); } pick.sel = null; hd._sig = ""; drawHeader(); drawPicker(); drawPickerBand(); panes.Partner.scrollTop = 0; ready(); });
  press(main, "[data-later]", () => putOff());
  press(panes.Partner, ".pkc", (b) => { selectCell(b.dataset.sid); drawPickerBand(); ready(); });
  panes.Partner.addEventListener("dblclick", (e) => { const b = e.target.closest(".pkc"); if (b) { selectCell(b.dataset.sid); choosePick(); } });
  press(document.body, "[data-pick]", (b) => { pick.picks[b.dataset.pickfor] = b.dataset.pick; drawPickerSide(); drawPickerBand(); ready(); });
  press(document.body, ".pcard [data-change]", () => openPicker());
  press(document.body, ".pcard [data-evolve]", (b) => playMoment(b.dataset.evolve));
  press(document.body, ".pcard [data-stop]", () => { holdMoment(); ready(); });
  /* attach: the Plus opens the file chooser; a paste or a drop of files does the same */
  fileIn = el("input"); fileIn.type = "file"; fileIn.multiple = true; fileIn.hidden = true; document.body.append(fileIn); fileIn.addEventListener("change", () => { [...fileIn.files].forEach(upload); fileIn.value = ""; ready(); });
  tx.addEventListener("paste", (e) => { const f = [...(e.clipboardData && e.clipboardData.files || [])]; if (f.length) { e.preventDefault(); f.forEach(upload); } });
  addEventListener("dragover", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
  addEventListener("drop", (e) => { const f = [...(e.dataTransfer && e.dataTransfer.files || [])]; if (!f.length) return; e.preventDefault(); if (place && place.kind !== "home") f.forEach(upload); });
  tx.addEventListener("focus", () => { threadTx = null; });
  buildOver();
  /* the field */
  toBtn.insertAdjacentHTML("beforeend", '<span class="tl">Onix</span>'); toBtn.classList.replace("sq", "w");   /* the switch says its name, in both states */
  cz.classList.remove("is-focus", "has-text", "is-off"); tx.innerHTML = ""; tx.contentEditable = "plaintext-only"; tx.classList.add("m-caret", "idle"); go.classList.remove("armed");
  let caret = 0; tx.addEventListener("input", () => { syncField(); go.classList.toggle("armed", !!tx.textContent.trim()); tx.classList.remove("idle"); clearTimeout(caret); caret = setTimeout(() => tx.classList.add("idle"), T["caret-hold"] || 600);
    if (place && place.kind === "partner") { pick.q = tx.textContent; pick.sel = null; hd._sig = ""; drawHeader(); drawPicker(); drawPickerBand(); } });   /* on the picker the field is its search, across every generation */
  tx.addEventListener("keydown", (e) => { if (place && place.kind === "partner" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {   /* the picker: arrows move through the grid, Return chooses, Esc clears the search */
      if (/^Arrow(Up|Down|Left|Right)$/.test(e.key)) { e.preventDefault(); movePick(e.key); drawPickerBand(); return; }
      if (e.key === "Escape" && tx.textContent) { e.preventDefault(); e.stopPropagation(); tx.textContent = ""; tx.dispatchEvent(new Event("input")); return; } }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send({at: e.timeStamp}); if (!(place && place.kind === "partner")) go.classList.remove("armed"); return; }
    if (e.key === "Escape" && editing) { e.preventDefault(); e.stopPropagation(); endEdit(); return; }
    if (e.key === "ArrowUp" && !tx.textContent && !editing && !e.shiftKey && !atts.length && !pop) { const m = P && P.msgs.filter((x) => x.sender_id === ME && typeof x.id === "number" && !x.thread_root_message_id).pop(); if (m) { e.preventDefault(); startEdit(m); } } });   /* Up in an empty field edits my last message */
  go.addEventListener("click", (e) => { send({at: e.timeStamp}); go.classList.remove("armed"); });
  /* a press on a control does not take focus from the field */
  const KEEP = ".tab, nav .row, nav .mo, .cz button, .rc, .jump, .stop, .prog, .sec .lk, .ls > li, [data-thread], .tree .row, .line .lk, .bl > .lk, .hd button, .mo, .over .c, .menu .mi, .pop button, .pal .mi, .opt, .hrow, .dlg button, .rt, .info button, .pcard button, .pkc, .pkseg button, [data-later], [data-pick]";
  document.addEventListener("mousedown", (e) => { if (e.target.closest && e.target.closest(KEEP)) e.preventDefault(); }, true);
  /* the field is ready after everything: a press that ends anywhere outside a control or a selection hands the caret back */
  document.addEventListener("mouseup", (e) => setTimeout(() => { const a = document.activeElement; if (a === tx || (a && a.closest("#lab, input, textarea, .thr-in .tx, .pal, .dlg, .pop")) || (e.target.closest && e.target.closest("#lab")) || !getSelection().isCollapsed) return; ready(); }, 0));
  /* a press outside a menu, the picker or the palette closes it and keeps what was pressed */
  addEventListener("pointerdown", (e) => { pressing = true; if (moment && !e.target.closest(".pcard")) cutMoment(); else if (card && !e.target.closest(".pcard, .row.me")) closeCard();
    if (info && !e.target.closest(".info, .rt")) closeInfo(true);
    if (menu && !e.target.closest(".menu")) closeMenu(); if (pop && !e.target.closest(".pop, .k.mode")) closePicker(); if (pal && !e.target.closest(".pal")) palette(false); }, true);
  addEventListener("pointerup", () => { pressing = false; }, true); addEventListener("pointercancel", () => { pressing = false; }, true);
  M.wire(); M.warm();
  press(navFixed, ".row[data-k]", (r) => { if (r.dataset.k === "jump") palette(); else openPlace(HOME); });
  press(navScroll, ".row", (r) => { const k = r.dataset.k; if (k.startsWith("c:")) { toggleStream(+k.slice(2)); ready(); return; } openPlace(placeFromKey(k)); });
  press(navScroll, ".mo", (b) => { allKids.add(+b.dataset.more); drawNav(); ready(); });
  press(main, ".tab", (t) => { setTab(t.dataset.tab); ready(); });
  press(main, "[data-goto]", (b) => { setTab(b.dataset.goto); ready(); });
  press(outline, "li[data-k]", (li) => { const p = placeFromKey(li.dataset.k); if (p) openPlace(p); });
  press(main, ".stop", () => stop());
  press(main, ".jump", () => { haltLane(); const ref = firstRow(), before = ref ? M.rest(ref).top : 0; pinned = true; pin(); syncJump(); edge(); const d = ref ? before - M.rest(ref).top : 0; if (d) M.release(lane, {y: clamp(d, -240, 240)}, "move"); ready(); });
  press(cz, ".k.to", () => { if (!place || !place.repo) return; if (onixHere()) store.del(onixKey(place)); else store.set(onixKey(place), "1"); drawComposer(); drawRunState(); S.play("tick"); ready(); });
  press(cz, ".k.mode", (b, e, ptr) => togglePicker({kbd: !ptr}));
  press(cz, ".out", () => fileIn.click());
  press(cz, "[data-unatt]", (b) => { atts = atts.filter((a) => String(a.id) !== b.dataset.unatt); drawAtts(); ready(); });
  press(lane, ".blk.ask .opt", (o, e) => { if (o.getAttribute("aria-disabled") === "true" || !waitingRun(P)) { ready(); return; } o.classList.add("sel"); send({text: o.dataset.answer, at: e.timeStamp, onix: true}); });   /* an option is the answer: it is sent as I would write it */
  $("#home").addEventListener("keydown", (e) => { const r = e.target; if ((e.key === "Enter" || e.key === " ") && r.matches && r.matches(".hrow[tabindex='0']")) { e.preventDefault(); r.click(); } });   /* a row with no verb is itself the button */
  press($("#home"), ".hrow, .hrow button", (r) => { const p = placeFromKey(r.dataset.k); if (!p) return; if (r.matches("[data-answer]") && p.repo) cacheOf(p).answering = true; openPlace(p); });   /* Answer: what I write there next answers Onix, once */
  press(lane, ".rc", (rc) => { const part = rc.closest(".rcs"), m = P.byId.get(+part.dataset.mid); if (m) react(m, {emoji_name: rc.dataset.name, emoji_code: rc.dataset.code, reaction_type: rc.dataset.type}); ready(); });
  press(lane, "[data-thread]", (b) => { openThread(+b.dataset.thread, false, {focus: true}); });
  press(lane, "[data-retry]", (b) => retry(b.dataset.retry));
  press(lane, ".rt[data-info]", (b, e, ptr) => { const id = +b.dataset.info; if (Number.isFinite(id)) toggleInfo(id, b, !ptr); else ready(); });
  press(main, "[data-info-retry]", () => { if (info) fetchReceipts(info.id, {force: true, retry: true}); });
  press($("#tree"), ".row", (r) => pickEntry(r));
  /* a double press on a message is my thumbs-up (React in the overlay does the same) */
  lane.addEventListener("dblclick", (e) => { const n = e.target.closest("[data-mid]"); if (!n || e.target.closest(".rc, button, a")) return; const m = P.byId.get(+n.dataset.mid); if (m) { getSelection().removeAllRanges(); react(m, {emoji_name: "+1", emoji_code: "1f44d", reaction_type: "unicode_emoji"}); } ready(); });
  feed.addEventListener("scroll", () => { edge(); if (tab !== "Chat") return; if (Math.abs(feed.scrollTop - lastSet) < 1) return; lastSet = -1; pinned = dist() < 4; syncJump();
    if (feed.scrollTop < 240 && P && P.loaded && !P.oldest && !P.loading) { const c = P; fetchMsgs(c, true).then(() => { if (P === c) drawLane({keep: true}); }); const mu = $(".more-up", lane); if (mu) mu.textContent = "Loading earlier messages"; } }, {passive: true});
  navScroll.addEventListener("scroll", navMore, {passive: true}); new ResizeObserver(navMore).observe(navScroll);
  /* a wrapped bubble keeps hugging its words when the lane's width changes, and once the faces have loaded */
  let laneW = 0; new ResizeObserver(() => { const w = lane.clientWidth; if (w === laneW) return; laneW = w; $$(".cl", lane).forEach((x) => toHug.add(x)); hugAll(); }).observe(lane);
  document.fonts.ready.then(() => { $$(".cl", lane).forEach((x) => toHug.add(x)); hugAll(); });
  feed.addEventListener("wheel", haltLane, {passive: true}); feed.addEventListener("touchstart", haltLane, {passive: true});
  band.el.addEventListener("wheel", (e) => { if (tab !== "Chat") return; haltLane(); feed.scrollTop += e.deltaY; }, {passive: true});
  new ResizeObserver(() => { if (tab === "Chat" && pinned) pin(); syncJump(); }).observe(feed);
  let dockH = 0; new ResizeObserver(([e]) => { const h = e.borderBoxSize ? e.borderBoxSize[0].blockSize : e.contentRect.height, was = dockH; dockH = h; if (!was || h === was || tab !== "Chat") return; if (pinned) { pin(); M.release(lane, {y: h - was}, "touch"); } M.release(band.el, {y: h - was}, "touch"); }).observe($("main > .dock"));
  new ResizeObserver(() => fitLists(side)).observe(outline);
  new ResizeObserver(() => { if (tab === "Chat" && pinned && !M.current(lane)[0].y) { pin(); edge(); } }).observe(lane, {box: "border-box"});   /* a face that loads late, an image that arrives, the room a live run keeps (the lane's padding): the end stays the end */
  addEventListener("keydown", (e) => { lastKey = performance.now();
    if (moment) { if (e.key === "Escape" && !moment.done && !tx.textContent.trim()) { e.preventDefault(); holdMoment(); return; } cutMoment(); }   /* any key ends the moment where it stands (the evolution happens) and goes on to the field; Esc with the field empty keeps the old form */
    if (card && e.key === "Escape") { e.preventDefault(); closeCard(); ready(); return; }
    const mod = e.metaKey || e.ctrlKey; if (mod && e.key === ".") { e.preventDefault(); stop(); return; }
    if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); palette(); return; }
    if (mod && e.key === "/") { e.preventDefault(); togglePicker({kbd: true}); return; }
    if (pop && (e.key === "ArrowDown" || e.key === "ArrowUp") && !pop.contains(document.activeElement)) { e.preventDefault(); focusPop(); return; }   /* opened with the pointer: an arrow goes into it */
    if (menu && menu._key(e)) return;
    /* Tab goes round the page: from the last stop to the first (the nav), never onto the page itself in between */
    if (e.key === "Tab" && !e.defaultPrevented && !mod && !e.altKey) { const all = tabStops(), i = all.indexOf(document.activeElement);
      if (all.length && !e.shiftKey && i === all.length - 1) { e.preventDefault(); all[0].focus(); return; }
      if (all.length && e.shiftKey && i === 0) { e.preventDefault(); all[all.length - 1].focus(); return; } }
    if (pop && e.key === "Escape") { e.preventDefault(); closePicker(); return; }
    if (e.key === "Escape" && info) { e.preventDefault(); closeInfo(); return; }
    /* I: Info on the message I stand on (its turn in the Tab order, or under the pointer with its controls up) */
    if (RECEIPTS && !mod && !e.altKey && (e.key === "i" || e.key === "I") && !info) { const a = document.activeElement, t = a && a.dataset && a.dataset.door ? a : a && over && over.contains(a) ? overAt : null, m = t && msgOf(t);
      if (m && typeof m.id === "number") { e.preventDefault(); openInfo(m.id, a, true); return; } }
    if (e.key === "Escape") { if (lab.classList.contains("open")) lab.classList.remove("open"); else if (editing) endEdit(); else if (threadTx) threadTx = null; else if (answering()) { P.answering = false; drawRunState(); } ready(); return; }
    const onControl = !!document.activeElement && document.activeElement !== document.body && document.activeElement.matches("button, a, [role=button], [tabindex]:not(#pane)");   /* Space there presses it */
    if (e.key === " " && onControl) return;
    if (!mod && !e.altKey && e.key.length === 1 && place && place.kind === "home" && !pal && !(document.activeElement && document.activeElement.closest("#lab, input, textarea, [contenteditable]"))) { e.preventDefault(); palette(); const inp = $(".pal .pin"); if (inp) { inp.textContent = e.key; inp.dispatchEvent(new Event("input")); const sel = getSelection(); sel.selectAllChildren(inp); sel.collapseToEnd(); } return; }   /* Home has no field: a letter typed there goes to Jump to */
    if (!mod && !e.altKey && e.key.length === 1 && tab === "Chat" && document.activeElement !== tx && !(document.activeElement && document.activeElement.closest("#lab, input, textarea"))) ready(); });   /* a letter typed anywhere goes to the field */
  addEventListener("popstate", () => { const p = placeFromHash(); if (p) openPlace(p, {keepHash: true}); });
  addEventListener("hashchange", () => { const p = placeFromHash(); if (p && (!place || p.key !== place.key)) openPlace(p, {keepHash: true}); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) return; askSession(); if (P) markRead(P); signOfLife(); refreshPartners({wait: 500}); });
  setInterval(() => refreshPartners({wait: 0}), 120000);   /* the team's partners: a choice or an evolution elsewhere shows here within two minutes */
  addEventListener("focus", () => { askSession(); signOfLife(); });
  setInterval(() => pollReceipts(), RCPT_POLL);
  setInterval(() => tick(false), 500);
  setInterval(sweepAsks, 5000);   /* a question past the server's wait stops reading as waiting, with no event to say so */
  setInterval(() => { if (P && P.ctx && hd) { hd._sig = ""; drawHeader(); } }, 60000);
  setInterval(() => { if (place && place.kind === "home") drawHome(); }, 60000);
  buildLab();
  /* where to: the address, then where I last was, then the first repository's latest topic */
  for (const sid of expanded) if (streams.has(sid)) loadTopics(sid); else expanded.delete(sid);
  let p = placeFromHash() || (store.get("place") && placeFromKey(store.get("place")));
  if (!p) { const s = subs()[0]; if (s) { expanded.add(s.stream_id); await loadTopics(s.stream_id); const t = (topicsOf.get(s.stream_id) || [])[0]; if (t) p = placeFromKey(tkey(s.stream_id, t.name)); } }
  if (p && p.kind === "topic") { await loadTopics(p.stream_id); const t = (topicsOf.get(p.stream_id) || []).find((x) => x.name.toLowerCase() === p.topic.toLowerCase()); if (t) p.topic = t.name; }
  if (!p && dms[0]) p = placeFromKey(dkey(dms[0].user_ids));
  /* the first visit after partners arrived: the page opens on the picker, and keeps where it was going for afterwards */
  if (pack && me_() && !me_().species && !me_().later && (!p || p.kind !== "partner")) { pick.back = p; p = PARTNER; }
  if (p && p.kind === "partner") selectMine();
  bootEl.remove();
  if (p) openPlace(p, {replace: true}); else drawNav();
  setTimeout(momentDue, 3000);   /* an evolution ready at load plays after the first idle 3 s */
  poll();
  /* Home needs every worktree's runs and my unread mentions: asked once, after the first place is drawn, then kept by events */
  setTimeout(() => { scanAll(); if (!place || place.kind !== "home") fetchMentions(); }, 300);   /* Home, opened first, has asked for them already */
}
window.armadaBeta = {get partners() { return partners; }, get pack() { return !!pack; }, applyPartners, refreshPartners, openCard, openPicker, playMoment, get moment() { return moment; }, hopOnce, stats, get place() { return place; }, get pinned() { return pinned; }, get cache() { return P; }, openPlace: (k) => openPlace(placeFromKey(k)), tkey, dkey, streams, send, stop, setTab, marks, unread, runsSeen, palette, togglePicker, openThread, startEdit, confirmDelete, get verb() { return verb; }, get atts() { return atts; }, QUESTION_TIMEOUT, sweepAsks, handle, redraw: () => { if (place && place.kind !== "home") { drawRunState(true); drawAsks(); } drawNav(); drawHomeRow(); }};   /* for checks from a driver */
document.addEventListener("DOMContentLoaded", boot);
})();
