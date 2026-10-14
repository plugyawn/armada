# surfaces

One directory per place on screen: nav, header, band, composer, column, checklist, lists, trace, home, readme, files, changes, artifacts, emoji, palette, dialogs, settings. A surface's name is its directory, its stylesheet, its commands file and its showroom section.

Not here: shared components (`kit/`), data (`state/`), or an import of another surface's internals.

To add one: a new surface is a design decision; it then gets `index.tsx`, `<name>.css`, `commands.ts` and `showroom.tsx`, wired once into `boot.ts` by the frame's owner.
