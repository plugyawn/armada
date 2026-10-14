# motion

The motion kit, under the design's names: `motion.ts` (`release`, `current`, `halt`, `rest`, `flip`, `fade`, `appear`, `leave`, `hold`, `thumb`, `figure`, `cells`, `swap`, `cross`, `grow`, `fly`, `carry`, `land`, `kick`, `squash`, `burst`, `shake`, `refuse`), `ring.ts` and `tip.ts` (the window's one focus ring and one tooltip, with `say`), `springs.ts` (reads durations and springs from the tokens once, and samples a spring in closed form), `guard.ts` (in a development build, a helper that measures after a write in an input task throws), `index.ts` (`wire`, `warm` and everything above). The only caller of `element.animate`, and one of the few places that may measure layout.

Not here: a duration or easing literal, a CSS animation (those are `kit/hover.css` and `kit/marks.css`), anything that waits for an animation before changing state.

To add one: copy `fade` in `motion.ts`; animate only `transform` and `opacity`; call `guard.reading` if it measures; cover it to 100% in `web/tests/armada/motion.test.cjs`; give it a control on the showroom's bench (`showroom/bench.ts`).
