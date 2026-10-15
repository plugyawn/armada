/* INTERIM BETA (README.md): what the field's words become on the wire, and back: mentions, links to places, a reply's
   quote, a send's client key. Pure: it imports nothing and reads no global but the elements it is given, so node tests
   load it (web/tests/armada/beta_mention_text.test.cjs).

   The field holds the words as typed, with a mention or a link as a pill: an element that stands for its wire text
   (`data-wire`). What the field shows is what is sent, in the place it was written: no mention is added in front of the
   words. A pill the switch put there (`data-seed`) is the one exception to "as typed": it is visible, and it is dropped
   when the words name the agent themselves. */

/* "@onix" or "@Onyx", with or without Zulip's stars, typed by hand: each is the agent's mention */
const AGENT_ALIAS = /(?<![\w@*])@(?:\*\*)?(?:onix|onyx)(?:\*\*)?(?![\w|*-])/gi;
/* code, where nothing is a mention: a fenced block (open to the end if unclosed), a code span */
const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/;
const outsideCode = (text, fn) => text.split(CODE).map((s, i) => (i % 2 ? s : fn(s))).join("");
const typedAliases = (text, agentMention) => outsideCode(text, (s) => s.replace(AGENT_ALIAS, agentMention));
/* a block the words start with keeps its own line after a mention, or a path, in front of it */
const BLOCK = /^(?:```|~~~|[-*+] |\d+[.)] |> ?)/;
const BLOCK_START = new RegExp(String.raw`^((?:@\*\*[^*\n]+\*\*[ \t]*)+)(?=${BLOCK.source.slice(1)})`);

/* ---------- the field ---------- */
const SEED = "\u0000";
function serialize(node, out) {
  for (const n of node.childNodes) {
    if (n.nodeType === 3) out.push(n.nodeValue.replace(/ /g, " "));
    else if (n.nodeType !== 1) continue;
    else if (n.dataset && n.dataset.wire != null) out.push(n.dataset.seed ? SEED : n.dataset.wire);
    else if (n.tagName === "BR") out.push("\n");
    else { if (/^(DIV|P)$/.test(n.tagName) && out.length && !out[out.length - 1].endsWith("\n")) out.push("\n"); serialize(n, out); }
  }
}
/* the words to send: pills as their wire text, the agent's names typed by hand as its mention, the switch's pill only when
   the words do not name the agent already */
function wireOf(root, agentMention) {
  const out = []; serialize(root, out);
  let t = typedAliases(out.join(""), agentMention);
  if (t.includes(SEED)) { const rest = t.split(SEED).join(""); t = rest.includes(agentMention) || !rest.trim() ? rest : t.replace(SEED, agentMention).split(SEED).join(""); }
  return t.trim().replace(BLOCK_START, (m) => m.trimEnd() + "\n\n");
}
/* sent from a view, the words to the agent say what was in view: after its mention when they start with it */
function withPath(text, path, agentMention) {
  const lead = text.startsWith(agentMention) ? agentMention + " " : "", rest = (lead ? text.slice(agentMention.length) : text).trimStart();
  return `${lead}\`${path}\`${BLOCK.test(rest) ? "\n\n" : " "}${rest}`;
}
/* nothing to send: the switch's pill alone is not words */
const fieldEmpty = (root) => { const out = []; serialize(root, out); return !out.join("").split(SEED).join("").trim(); };
const seedOf = (root) => root.querySelector("[data-seed]");

/* a pill: what it says, and what it is on the wire. The switch's pill says its name from CSS, so the field's text stays
   empty and every "is the field empty" in the page reads it as empty. */
function pillNode(doc, o) {
  const s = doc.createElement("span"); s.className = "pill" + (o.chip ? " chip" : "") + (o.you ? " you" : ""); s.setAttribute("contenteditable", "false"); s.dataset.wire = o.wire;
  if (o.uid != null) s.dataset.uid = o.uid; if (o.agent) s.dataset.agent = "1";
  if (o.seed) { s.dataset.seed = "1"; s.dataset.label = o.label; } else s.textContent = o.label;
  return s;
}
const MENTION = /@\*\*([^*|\n]+?)(?:\|(\d+))?\*\*|#\*\*([^*\n]+?)\*\*/g;
/* the field from wire text (an edit): each mention and link the page can name becomes its pill; code stays as written */
function fillField(root, wire, resolve) {
  const doc = root.ownerDocument; root.textContent = "";
  wire.split(CODE).forEach((s, i) => {
    if (i % 2) { root.append(doc.createTextNode(s)); return; }
    let at = 0; for (const m of s.matchAll(MENTION)) { const o = resolve(m[3] != null ? {kind: "place", name: m[3]} : {kind: "person", name: m[1], id: m[2] ? +m[2] : null, wire: m[0]}); if (!o) continue;
      if (m.index > at) root.append(doc.createTextNode(s.slice(at, m.index))); root.append(pillNode(doc, o)); at = m.index + m[0].length; }
    if (at < s.length) root.append(doc.createTextNode(s.slice(at)));
  });
}

/* ---------- the pickers ---------- */
/* the picker a caret stands in: "@" and a name (two words at most), or "#" and a place ("repo>topic"), after a space, a
   bracket or the start; never inside a code span */
const TRIGGER = /(?:^|[\s([{"'“‘])(?:(@)([^\s@#*`]*(?: [^\s@#*`]*)?)|(#)((?:[^\s#*`>][^#*`>\n]{0,40})?(?:>[^#*`\n]{0,60})?))$/u;
function triggerAt(before) {
  if ((before.match(/`/g) || []).length % 2) return null;
  const m = TRIGGER.exec(before); if (!m) return null;
  const kind = m[1] || m[3], q = m[1] ? m[2] : m[4];
  return {kind, q, length: q.length + 1};
}
const fold = (s) => String(s).toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
/* how well a name answers a query: its start, then a word's start, then anywhere (from two letters); -1 when it does not */
function score(name, q) { const n = fold(name), x = fold(q).trim(); if (!x) return 3; if (n.startsWith(fold(q))) return 0; if (n.split(/[\s._-]+/).some((w) => w.startsWith(x))) return 1; return x.length > 1 && n.includes(x) ? 2 : -1; }
const PRESENCE_ORDER = {on: 0, away: 1, "": 2};
/* the best match first; at an equal match people (who is here now before who is away), then the agent, then @topic, so
   "@Oni" offers Onix before Polonius and Enter starts a run */
function rankPeople(q, people, o = {}) {
  const max = o.max || 6, ranked = people.map((p) => ({p, s: score(p.name, q)})).filter((x) => x.s >= 0)
    .sort((a, b) => a.s - b.s || PRESENCE_ORDER[a.p.pres || ""] - PRESENCE_ORDER[b.p.pres || ""] || a.p.name.localeCompare(b.p.name)).slice(0, max);
  const list = ranked.map((x) => ({kind: "person", ...x.p})), agentScore = o.agent ? score(o.agent.name, q) : -1;
  if (agentScore >= 0) { const after = ranked.findIndex((x) => x.s > agentScore); list.splice(after < 0 ? list.length : after, 0, {kind: "agent", ...o.agent}); }
  if (o.topic && score("topic", q) >= 0) list.push({kind: "topic", name: "topic"});
  return list;
}
/* "#": repositories, then their worktrees; "repo>top" looks only in that repository */
function rankPlaces(q, streams, topicsOf, o = {}) {
  const max = o.max || 8, gt = q.indexOf(">"), sq = gt >= 0 ? q.slice(0, gt) : q, tq = gt >= 0 ? q.slice(gt + 1) : q, out = [];
  if (gt < 0) streams.map((s) => ({s, v: score(s.name, sq)})).filter((x) => x.v >= 0).sort((a, b) => a.v - b.v || a.s.name.localeCompare(b.s.name)).forEach((x) => out.push({kind: "stream", stream: x.s}));
  const scope = gt >= 0 ? streams.filter((s) => fold(s.name) === fold(sq).trim() || score(s.name, sq) === 0) : streams;
  const topics = [];
  for (const s of scope) for (const t of topicsOf(s.stream_id) || []) { const v = score(t.name, tq); if (v >= 0 && (gt >= 0 || tq)) topics.push({s, t, v}); }
  topics.sort((a, b) => a.v - b.v || (b.t.worktree ? 1 : 0) - (a.t.worktree ? 1 : 0) || (b.t.max_id || 0) - (a.t.max_id || 0)).forEach((x) => out.push({kind: "place", stream: x.s, topic: x.t.name, worktree: !!x.t.worktree}));
  return out.slice(0, max);
}
/* a choice as a pill */
function pillFor(c, agentMention) {
  if (c.kind === "person") return {wire: `@**${c.name}|${c.id}**`, label: "@" + c.name, uid: c.id};
  if (c.kind === "agent") return {wire: agentMention, label: "@" + c.name, uid: c.id, agent: true};
  if (c.kind === "topic") return {wire: "@**topic**", label: "@topic"};
  if (c.kind === "stream") return {wire: `#**${c.stream.name}**`, label: "#" + c.stream.name, chip: true};
  return {wire: `#**${c.stream.name}>${c.topic}**`, label: `#${c.stream.name} › ${c.topic}`, chip: true};
}

/* ---------- the echo: wire text as the server will render it, before clean() reads it (parts.js) ---------- */
/* `html` is already escaped; a name is looked up in the escaped form it has there */
function echoRefs(html, look) {
  return html.replace(/@(_?)\*\*([^*|\n]+?)(?:\|(\d+))?\*\*/g, (all, silent, name, id) => {
    if (!silent && /^(topic|all|everyone|channel|stream)$/i.test(name)) return `<span class="topic-mention">@${name}</span>`;
    const uid = id ? +id : look.personByName(name); if (uid == null) return all;
    return `<span class="user-mention${silent ? " silent" : ""}" data-user-id="${uid}">${silent ? "" : "@"}${name}</span>`;
  }).replace(/#\*\*([^*\n]+?)\*\*/g, (all, ref) => { const gt = ref.indexOf("&gt;"), s = look.streamByName(gt >= 0 ? ref.slice(0, gt) : ref); if (!s) return all;
    return gt >= 0 ? `<a class="stream-topic" data-stream-id="${s}">#${ref.slice(0, gt)} &gt; ${ref.slice(gt + 4)}</a>` : `<a class="stream" data-stream-id="${s}">#${ref}</a>`; });
}

/* ---------- a reply: one line of the message replied to, in Zulip's own quote form, so every client reads it ---------- */
/* the line: the first that has words, with no mention in it (a quoted "@Onix" must not address the agent again) */
function quoteLine(text, max = 100) { const line = (String(text).split("\n").map((l) => l.trim()).find(Boolean) || "").replace(/\s+/g, " ").replace(/@(?=[\w*_])/g, "");
  return line.length > max ? line.slice(0, max - 1).trimEnd() + "…" : line; }
function quoteWire(r) { const runs = String(r.line).match(/`+/g) || [], fence = "`".repeat(Math.max(3, ...runs.map((x) => x.length + 1)));
  return `@_**${r.name}|${r.uid}** [said](${r.link}):\n${fence}quote\n${r.line}\n${fence}\n`; }
/* the reverse of quoteWire, for editing a reply: its quote and the words after it; null for a message that is not a reply */
const QUOTE_WIRE = /^@_\*\*([^*|\n]+)\|(\d+)\*\* \[said\]\(([^)\s]+)\):\n(`{3,})quote\n([\s\S]*?)\n\4(?:\n|$)/;
function splitQuoteWire(wire) { const m = QUOTE_WIRE.exec(wire), near = m && /\/near\/(\d+)/.exec(m[3]); if (!m || !near) return null;
  return {quote: {name: m[1], uid: +m[2], link: m[3], id: +near[1], line: m[5]}, rest: wire.slice(m[0].length)}; }
/* a rendered message that starts with a reply's quote: who said it, the message, the line; null otherwise. The server's
   form is a paragraph of a silent mention, a "said" link to the message and a colon, then a quote; it is read before or
   after clean() (parts.js), which keeps the mention's user id. */
function quoteOf(children) {
  const [p, q] = children; if (!p || !q || p.tagName !== "P" || q.tagName !== "BLOCKQUOTE") return null;
  const who = p.querySelector(".user-mention.silent, .mention.silent"), a = p.querySelector("a[href]"), near = a && /\/near\/(\d+)/.exec(a.getAttribute("href"));
  if (!who || !near || !/:\s*$/.test(p.textContent)) return null;
  return {uid: +who.dataset.userId, name: who.textContent.replace(/^@/, ""), id: +near[1], line: q.textContent.replace(/\s+/g, " ").trim()};
}

/* ---------- contract C4: a send's key, the same on every retry of it ---------- */
const clientKey = (rand) => [...rand(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");

export {clientKey, echoRefs, fieldEmpty, fillField, outsideCode, pillFor, pillNode, quoteLine, quoteOf, quoteWire, rankPeople,
  rankPlaces, seedOf, splitQuoteWire, triggerAt, typedAliases, wireOf, withPath};
