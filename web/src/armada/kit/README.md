# kit

Every component of the design language, drawn once. A component is a description of elements built by a recipe (`controls.ts`, `conversation.ts`, `blocks.ts`, `chrome.ts`, `lists.ts`, `raised.ts`, `icons.ts`; all of them under one name in `recipes.ts`) and a stylesheet of `armada-` classes beside it. `index.ts` turns each recipe into a Preact component for chrome (`view.tsx`); `dom.ts` builds the same descriptions as plain elements for the feed (`tree.ts`). `hover.css` is the hover and press layer and `marks.css` the loops (the only two files with `@keyframes`); `base.css` holds the reset and the few lengths derived from tokens; `icons/` is the generated sprite.

Not here: anything that knows a store, a route or the server; a string for a person (labels are props); a colour, a length or a duration of its own. The kit imports `motion/` and `tokens/` and nothing else of Armada's.

To add one: write its recipe beside its kin, its rules in that family's stylesheet with state as attributes, export it from `index.ts` (and `dom.ts` if the feed builds it), and show every state in `showroom/fixtures.ts`: a component that is not in the showroom may not be used. Then accept its picture: `ARMADA_KIT_ACCEPT=1 arm check kit`.
