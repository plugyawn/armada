/* INTERIM BETA (README.md): the rule: a server-rendered message becomes bubbles and blocks; the local echo of what I type. */

import {$$, el, esc} from "./util.js";
import {marks, people, repoStreams, state, streams} from "./state.js";
import {dayLabel, hhmm} from "./time.js";
import {isOnix, nameOf} from "./faces.js";
import {echoRefs} from "./mention_text.js";
import {refLook} from "./mentions.js";
import {tkey} from "./routing.js";

/* ---------- the rule: a server-rendered message becomes bubbles and blocks ----------
   The message's rendered HTML is read as a list of its top-level elements. Each is prose or a block.
   A block is: a code block (div.codehilite or pre), a table, an image (div.message_inline_image), a spoiler, a link
   preview (div.message_embed), display maths. Everything else is prose: paragraphs, lists, quotes, headings.
   - A person's message: each run of consecutive prose elements is one bubble; each block stands as a block in the
     turn, between the bubbles, at the lane's width. A message that is one to three emoji and nothing else is jumbo.
   - Onix's message: each run of prose is ambient text on the page (no bubble); blocks are the same blocks.
   Inside prose: an emoji span becomes its character, a mention becomes a pill with the person's name (the agent's is
   always "Onix"; a mention of me, or of everyone, is mine; a silent one is the name alone), a link to a repository or a
   worktree becomes a chip that opens it here, any other link opens in a new tab, a timestamp is printed in local time. Code keeps the server's
   highlighting, mapped onto the four syntax inks the design has (keyword, string, number, comment). */
const PYG = {}; "k kn kd kc kr kt kp ow".split(" ").forEach((c) => (PYG[c] = "sx-k")); "s s1 s2 sa sb sc sd se sh si sx sr ss dl".split(" ").forEach((c) => (PYG[c] = "sx-s")); "m mi mf mh mo mb il".split(" ").forEach((c) => (PYG[c] = "sx-n")); "c c1 cm ch cp cs cpf".split(" ").forEach((c) => (PYG[c] = "sx-c"));
const tpl = document.createElement("template");
const BLOCK = (n) => n.matches("div.codehilite, pre, table, div.message_inline_image, div.spoiler-block, div.message_embed, div.inline-preview-twitter, div.youtube-video, div.message_inline_video, .katex-display") || (n.tagName === "P" && n.children.length === 1 && n.firstElementChild.matches(".katex-display") && !n.textContent.replace(n.firstElementChild.textContent, "").trim());
function clean(root) {
  $$("script, style, iframe, object, .message_length_controller, .message_expander, .message_condenser", root).forEach((x) => x.remove());
  $$("span.emoji", root).forEach((s) => { const m = /emoji-([0-9a-f-]+)/.exec(s.className); if (!m) return; let ch = ""; try { ch = String.fromCodePoint(...m[1].split("-").map((h) => parseInt(h, 16))); } catch (e) { return; } const e = el("span", "em"); e.textContent = ch; e.title = s.getAttribute("title") || ""; s.replaceWith(e); });
  $$(".user-mention, .user-group-mention, .topic-mention", root).forEach((s) => { const id = +s.dataset.userId, silent = s.classList.contains("silent"), all = s.classList.contains("topic-mention") || s.dataset.userId === "*";
    const e = el("span", "mention" + (silent ? " silent" : "") + (id === state.ME || all ? " you" : "")); e.textContent = (silent ? "" : "@") + (id && people.has(id) ? nameOf(id) : s.textContent.replace(/^@/, "")); s.replaceWith(e); });
  $$("a.stream, a.stream-topic", root).forEach((a) => { const sid = +a.dataset.streamId, st = streams.get(sid); if (!st) return; const pre = `#${st.name} > `, topic = a.classList.contains("stream-topic") && a.textContent.startsWith(pre) ? a.textContent.slice(pre.length) : null;
    const e = el(topic != null ? "a" : "span", "placelink"), live = topic != null && marks.get(tkey(sid, topic)) === "live"; if (topic != null) e.href = `#c/${sid}/${encodeURIComponent(topic)}`; if (live) e.dataset.state = "live";
    e.innerHTML = `${ic(topic != null ? "branch" : repoStreams.has(sid) ? "repo" : "hash")}<span>${esc(st.name)}</span>${topic != null ? `<span class="sep">\u203a</span><span>${esc(topic)}</span>` : ""}`; a.replaceWith(e); });
  $$("a[href]:not(.placelink)", root).forEach((a) => { const h = a.getAttribute("href"); if (h[0] === "#") a.href = "/" + h; a.target = "_blank"; a.rel = "noopener noreferrer"; });   /* a link to a narrow opens it in the old interface */
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
/* a mention or a place link is echoed as the server renders it; never inside code */
const inlineEcho = (t) => esc(t).split(/(`[^`\n]+`)/).map((x, i) => (i % 2 ? `<code>${x.slice(1, -1)}</code>` : echoRefs(x, refLook).replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/\[([^\]\n]+)\]\((\/user_uploads\/[^)\s]+|https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>'))).join("");
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

export {blockPart, clean, echoHTML, loadPics, partEl, partsOf, plainOf, QUESTION, tpl};
