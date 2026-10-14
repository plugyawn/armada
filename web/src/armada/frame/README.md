# frame

The window: `frame.tsx` and `frame.css` (the grid, the composition of conversation and column, the five z-index layers), `panes.ts` (kept panes), `route_view.ts`, `after_layout.ts` (the phase in which layout may be read).

Not here: anything a surface draws inside its region; a width, a breakpoint or a `z-index` computed in script.

To add one: a new region is a change to `docs/armada/design-language.md` first; then one grid area in `frame.css` and one assertion in `arm check frame-stability`.
