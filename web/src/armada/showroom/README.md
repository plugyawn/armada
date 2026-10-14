# showroom

The dev page `/devtools/armada/`: every kit component in every state, drawn through Preact and through the DOM builder from one fixture (`fixtures.ts`); the tokens of every palette with their contrast; the sprite; and the bench (`bench.ts`) that puts every piece of the motion kit and every sound under a control. It is the visual contract `arm check kit` shoots and `motion` and `design-lint` read. `?as=preact|dom|both`, `?theme=light|dark`, `?p=<palette>`, `?motion=reduced`, `?section=<id>`.

Not here: anything the product imports.

To add one: add an item to its section in `fixtures.ts`, built with the kit's recipes; a surface adds its own section from its `showroom.tsx` once the frame registers surfaces.
