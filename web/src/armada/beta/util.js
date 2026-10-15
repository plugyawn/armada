/* INTERIM BETA (README.md): helpers every part of the page uses: elements, escaping, this browser's storage, a press. */

const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const R = document.documentElement, Q = new URLSearchParams(location.search), T = M.T;
const store = {get: (k) => { try { return localStorage.getItem("armada-beta-" + k); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem("armada-beta-" + k, v); } catch (e) {} }, del: (k) => { try { localStorage.removeItem("armada-beta-" + k); } catch (e) {} }};
window.TOPICS = {};                                        /* tells the kit this page scrolls its feed itself */
function press(root, sel, fn) {
  root.addEventListener("pointerdown", (e) => { if (e.button !== 0 || e.ctrlKey) return; const t = e.target.closest(sel); if (!t || !root.contains(t) || t.disabled) return; t._pd = performance.now(); fn(t, e, true); });
  root.addEventListener("click", (e) => { const t = e.target.closest(sel); if (!t || !root.contains(t) || t.disabled) return; if (t._pd && performance.now() - t._pd < 1500) { t._pd = 0; return; } t._pd = 0; fn(t, e, false); });
}
const mid = (s, n) => { if (s.length <= n) return s; const k = (n - 1) >> 1; return s.slice(0, n - 1 - k) + "…" + s.slice(-k); };
const inlineMd = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

export {$, $$, clamp, el, esc, inlineMd, mid, press, R, sleep, store, T};
