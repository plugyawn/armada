/* INTERIM BETA: the beta's marks, drawn as vectors in the 14 px box (statement 29: no pixel art, everything crisp).
   A stroked part has the class "s" (mock.css strokes it in the mark's ink); a filled part takes the ink as its fill.
   Each state keeps its own silhouette: a tick, a cross, a bar, a ring (dashed while it waits its turn), a balloon with
   its mark knocked out, Zz. A live run is the thinking star, which kit.js draws (wk, and mk("live") still). */
window.BETA_MARKS = {
  done: '<path class="s" d="M3 7.5 5.75 10.25 11 4"/>',
  failed: '<path class="s" d="M4 4 10 10M10 4 4 10"/>',
  cancelled: '<path class="s" d="M3.5 7h7"/>',
  left: '<circle class="s" cx="7" cy="7" r="4.4"/>',
  queued: '<circle class="s dash" cx="7" cy="7" r="4.4" pathLength="16"/>',
  needs: '<path fill-rule="evenodd" d="M4 1.5h6A2.5 2.5 0 0 1 12.5 4v3A2.5 2.5 0 0 1 10 9.5H6.25L3.5 12.25V9.4A2.5 2.5 0 0 1 1.5 7V4A2.5 2.5 0 0 1 4 1.5zM6.25 3.25v3h1.5v-3zm0 4v1.5h1.5v-1.5z"/>',
  paused: '<path class="s thin" d="M2.5 6.5H7l-4.5 5H7M8.5 2.5h3l-3 3h3"/>',
};
