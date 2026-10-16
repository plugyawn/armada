/* INTERIM BETA (README.md): a code block: its code, a button that copies it, and long output folded under a line that
   says how much more there is, with Show all. */

import {$, el, press} from "./util.js";
import {ready} from "./composer.js";

/* The code stands in a span of its own, which scrolls sideways when a line is long, so that what the block draws around
   it (the button) stays where it is and never reaches the code's text. */
function codeBlock(html) { const pre = el("pre", "blk"); setCode(pre, html); press(pre, ".cp", copyCode); press(pre, ".fold > .lk", unfold); return pre; }
/* the code, written again as the server's rendering of it changes (a streamed answer): the button is kept */
function setCode(pre, html) { let code = $(":scope > .code", pre); if (!code) { code = el("span", "code"); pre.prepend(code); } code.innerHTML = html;
  if (!$(":scope > .cp", pre)) { pre.append(el("button", "c sq s cp", ic("copy"))); const b = $(":scope > .cp", pre); b.setAttribute("aria-label", "Copy code"); b.dataset.tip = "Copy code"; b.tabIndex = -1; host(pre); }
  pre._lines = (html.match(/\n/g) || []).length + 1; fold(pre, pre._lines); }
/* code still being written: its new characters go at the end (before `before`, the working mark, when it stands there),
   as text, and nothing before them is written again */
function appendCode(pre, text, before = null) { const code = $(":scope > .code", pre); if (before) before.before(text); else code.append(text); pre._lines += (text.match(/\n/g) || []).length; fold(pre, pre._lines); }
/* Long output folds: a block of more than FOLD_AT lines shows FOLD_AT - 1 of them and, on the line under them, how many
   more there are and Show all. Folded, it is exactly as tall as a block of FOLD_AT lines, so a block that Onix is still
   writing stops growing at that height, and nothing under it moves while it streams on. Show all opens it for as long as
   the block is there; Show less folds it again. */
const FOLD_AT = 16;
function fold(pre, lines) { const long = lines > FOLD_AT; let line = $(":scope > .fold", pre);
  if (!long) { if (line) line.remove(); delete pre.dataset.fold; return; }
  if (!line) { line = el("div", "fold", '<span class="n"></span><button class="lk"></button>'); $(":scope > .code", pre).after(line); host(line); }
  const open = !!pre._open, n = open ? `${lines} lines` : `${lines - FOLD_AT + 1} more lines`, verb = open ? "Show less" : "Show all", b = $(".lk", line);
  pre.dataset.fold = open ? "open" : "closed"; if ($(".n", line).textContent !== n) $(".n", line).textContent = n; if (b.textContent !== verb) b.textContent = verb; b.setAttribute("aria-expanded", String(open)); }
function unfold(b, e, ptr) { const pre = b.closest("pre"); pre._open = !pre._open; fold(pre, $(":scope > .code", pre).textContent.split("\n").length); if (ptr) ready(); }
/* the code as it reads, without the highlighting; from the pointer, the caret goes back to the field, as Copy text does */
function copyCode(b, e, ptr) { const code = $(":scope > .code", b.closest("pre")); try { navigator.clipboard.writeText(code.textContent).catch(() => {}); } catch (x) {} M.say(b, "Copied", 1200); if (ptr) ready(); }

export {appendCode, codeBlock, setCode};
