---
paths:
  - "web/src/armada/**"
  - "tools/armada/**"
---

# Design language

The full language is `docs/armada/design-language.md`. "stylelint" is the
`web/src/armada/**/*.css` override in `stylelint.config.js`, "custom check"
is `tools/linter_lib/custom_check.py`; both run in `arm lint`.

- A literal lives in a token file and nowhere else: colours in the generated
  `tokens/palette-*.css`, every other value in `tokens/tokens.css`.
  Enforced by: stylelint `color-no-hex`, `color-named`, colour functions in
  `function-disallowed-list`, every length and time unit in
  `unit-disallowed-list`; custom check "A colour token is defined only in".
  Not seen: a length written as a multiple of a token, as the kit and the
  showroom do about 140 times with `calc(var(--px) * n)` from `kit/base.css`
  until their lengths become tokens, and three `stylelint-disable` lines in
  `kit/hover.css` and `kit/header.css` (`build/F4-verify.md`, defect 6).
- Never edit a palette file by hand; change `tools/armada/palette.json` and
  run `tools/armada/palette.py`. Enforced by: `arm lint` (`armada-palette`).
- Two faces: `--f-human` (Söhne, Hanken Grotesk behind it) for people, Onyx
  and chrome; `--f-code` (Geist Mono) for what a machine would accept (Onyx
  in the human face follows statement 20, which the user offered as a
  "maybe"). No pixel face, and no system typeface in `web/` outside the
  custom check's exempt old files. Enforced by: custom check "Do not name a
  system typeface"; stylelint (`font-family` is `var(--f-*)`);
  `arm check design-lint` (`faces-not-the-two`).
- Five sizes (12, 14, 16, 20, 28) through the `--t-*` tokens; no text under
  12 px. Enforced by: stylelint (`font`, size, weight and line height are
  tokens); `design-lint` (`sizes-off-the-scale`, `text-under-12px`).
- No capitals as labels, no tracking, no word spacing. Enforced by: stylelint
  `property-disallowed-list`; `design-lint` (`capitals-or-tracked-runs`).
- Chrome has no surface at rest: no `border` (an edge is a 1 px shadow;
  `kit/table.css` is the exception), no `filter`, no `backdrop-filter`;
  `box-shadow` and `border-radius` take tokens. Enforced by: stylelint
  `property-disallowed-list`, `declaration-property-value-allowed-list`.
- `z-index` appears only in `frame/frame.css` (F5's), which names the five
  layers. Enforced by: stylelint `property-disallowed-list`.
- Colour in chrome means news (passed, needs you, failed) or is mine; a fill
  never reports a state. Enforced by: nothing automatic yet; `design-lint`
  does not count colour, and the design's review tools are run by hand.
- New rules win by cascade layer, never by weight: no id selectors, no
  `!important`, specificity at most `0,3,0`; old sheets sit in `@layer zulip`.
  Enforced by: stylelint `selector-max-id`, `declaration-no-important`,
  `selector-max-specificity`; `web/zulip-layer-loader.ts` in the build.
- A component is drawn once, in `kit/`, and shown in every state in the
  showroom (`/devtools/armada/`); a surface never uses a raw `<button>`,
  `<input>`, `<select>` or `<dialog>`. Enforced by: eslint
  `no-restricted-syntax` under `surfaces/` (JSX, `h()`, `createElement()`);
  `arm check kit`.
- Glyphs and marks come from the generated sprite by typed name. Enforced
  by: TypeScript (`kit/icons/names.ts`); `arm check kit` (`sprite.*`);
  `tools/armada/icons.py --check`, run by hand, not by lint.
- Every user-visible string is tagged for translation. Enforced by: eslint
  `formatjs/no-literal-string-in-jsx`, `no-restricted-syntax` (a literal set
  as text or a label on the DOM); one passed through a variable is not seen.
- Hit areas are at least 24 by 24 px; every control has a name and a ring on
  keyboard focus. Enforced by: `arm check kit` (`controls.*`) and
  `arm check motion` (`ring.shown-for-the-keyboard`).
