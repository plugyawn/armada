# kit/icons

`sprite.svg` and `names.ts`, both written by `tools/armada/icons.py`: the stroked glyphs, the run and step marks, the working mark as one sprite of four steps, the sigil at three sizes with its outline, and a typing dot, as `<symbol>`s inlined once in a page. `names.ts` holds the typed names a component uses them by.

Not here: a hand edit, an image file, or a glyph drawn in a component.

To add one: draw it in `tools/armada/icons_gen.py`, run `tools/armada/icons.py`, and use it with `glyph()`, `mark()` or `sigil()` from `kit/icons.ts` by its typed name.
