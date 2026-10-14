# tokens

Every literal of the design language. `tokens.css` holds each value that is not a colour (faces, type, space, radii, sizes, the composition, springs, durations). `palette-*.css` are written by `tools/armada/palette.py` and hold every colour: one file a palette, and `palette-shared.css` for what any palette derives from its own inks (the pressed fill, Increase Contrast). Never edit them by hand. `fonts.css` declares the bundled faces and `layers.css` the layer order; the licensed face is declared by the build (`web/fonts/licensed/README.md`).

Not here: a selector that styles a component (that is `kit/`), an element default (no `base.css` exists yet: a rule on `code` or `body` in this layer would restyle every old screen), or a colour anywhere but a palette file.

To add one: add the custom property to `tokens.css` under its group, with the name `docs/armada/design-language.md` gives it, and to `web/tests/armada/tokens.design.json`. A new colour is a change to `tools/armada/palette.json`, then `tools/armada/palette.py --write`. The palette a page is drawn in is the server's `ARMADA_PALETTE` setting, written to `<html data-palette>`; no script chooses it.
