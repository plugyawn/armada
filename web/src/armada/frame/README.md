# frame

The window: `frame.css` (the grid, the composition of conversation and column, the five z-index layers), `frame.tsx` (what script draws into the template's cells, and `data-place` and `data-view` on the plane), `after_layout.ts` (the phase in which layout may be read, and the header's scroll edge). The grid itself is markup in `templates/zerver/app/index.html`, so the first paint is the frame. Kept panes (`panes.ts`) arrive with the feed.

Not here: anything a surface draws inside its region; a width, a breakpoint or a `z-index` computed in script.

To add one: a new region is a change to `docs/armada/design-language.md` first; then one grid area in `frame.css` and one assertion in `arm check frame-stability`.
