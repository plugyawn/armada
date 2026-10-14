---
paths:
  - "web/src/armada/**"
  - "tools/armada/reel*"
---

# Motion and sound

Motion is everywhere and never distracting; the tokens and the fixed
choreography are in `docs/armada/design-language.md` ("Motion", "Sound").

- Only `web/src/armada/motion/` calls `element.animate`. Enforced by: eslint
  `no-restricted-syntax` ("Only web/src/armada/motion/ calls
  element.animate"; it fires on any method called `animate`).
- Only `transform` and `opacity` animate, and colour on the fades.
  Enforced by: stylelint `plugin/no-low-performance-animation-properties`
  and `declaration-property-value-allowed-list` (`transition-property`);
  `arm check motion` (`animated.properties-other-than-transform-and-opacity`).
- A duration or an easing exists once, as a token in `tokens/tokens.css`;
  script reads it through `motion/springs.ts`. Enforced by: stylelint
  `unit-disallowed-list` (`ms`, `s`); custom checks "No duration literal in
  script" and "No easing literal in script" (`sound/` is exempt from the
  first).
- No bézier: timing functions are `var(--spring-*)` or `linear`.
  Enforced by: stylelint `function-disallowed-list` (`cubic-bezier`) and
  `declaration-property-value-allowed-list`.
- `@keyframes` live in `kit/hover.css` and `kit/marks.css` only; a live run
  has one loop, the working mark. Enforced by: stylelint
  `at-rule-disallowed-list`; `arm check motion` (`animated.kinds-of-loop`).
- What I asked for is whole and opaque in the frame of the input: no timer,
  `await` or animation callback stands between an input and its state.
  Enforced by: `arm check send-cost` (`plain.row-in-first-frame`) and
  `typing-cost`; `arm check motion` (`hover.*`, `press.first-frame-scale`).
- Layout is written once and the difference released with a transform; the
  helpers that measure run after layout, never in an input task.
  Enforced by: eslint `no-restricted-syntax` ("No layout read here");
  `arm check typing-cost` (`*.handler-forced-layouts.max-per-key`).
- Every animation yields to the next input from its current value and
  velocity; nothing is queued. Enforced by: `arm check motion`
  (`opposite.*`), which was not yet stable over three runs at F4's
  verification (`build/F4-verify.md`, defect 4).
- Motion never moves layout or anything being read. Enforced by:
  `arm check interaction-stability`, `frame-stability`.
- Reduced motion collapses the spring durations, keeps the fades and holds
  loops on one frame; there is no blanket rule. Enforced by: the token test
  (`web/tests/armada/tokens.test.cjs`); `arm check motion` (`reduced.*`).
- Nothing animates for longer than 440 ms, and no `transition: all`.
  Enforced by: stylelint `declaration-property-value-disallowed-list`;
  `arm check design-lint` (`longer-than-440ms`).
- Only `web/src/armada/sound/` may name an `AudioContext`, and it is not
  created before the first gesture or while muted. Enforced by: eslint
  `no-restricted-syntax` ("Only web/src/armada/sound/"); the sound engine's
  node tests (`web/tests/armada/sound.test.cjs`).
- A sound is a recipe in `sound/recipes.ts`, rendered by the same code as
  `tools/armada/reel`; no audio file is shipped. Enforced by: review;
  `sound/` is covered to 100% (`ARMADA_FULLY_COVERED_GLOBS`).
