/* INTERIM BETA (README.md): a code block: its code, and a button that copies it. */

import {$, el, press} from "./util.js";
import {ready} from "./composer.js";

/* The code stands in a span of its own, which scrolls sideways when a line is long, so that what the block draws around
   it (the button) stays where it is and never reaches the code's text. */
function codeBlock(html) { const pre = el("pre", "blk"); setCode(pre, html); press(pre, ".cp", copyCode); return pre; }
/* the code, written again as the server's rendering of it changes (a streamed answer): the button is kept */
function setCode(pre, html) { let code = $(":scope > .code", pre); if (!code) { code = el("span", "code"); pre.prepend(code); } code.innerHTML = html;
  if (!$(":scope > .cp", pre)) { pre.append(el("button", "c sq s cp", ic("copy"))); const b = $(":scope > .cp", pre); b.setAttribute("aria-label", "Copy code"); b.dataset.tip = "Copy code"; b.tabIndex = -1; host(pre); } }
/* the code as it reads, without the highlighting; from the pointer, the caret goes back to the field, as Copy text does */
function copyCode(b, e, ptr) { const code = $(":scope > .code", b.closest("pre")); try { navigator.clipboard.writeText(code.textContent).catch(() => {}); } catch (x) {} M.say(b, "Copied", 1200); if (ptr) ready(); }

export {codeBlock, setCode};
