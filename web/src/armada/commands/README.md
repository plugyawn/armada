# commands

`registry.ts` is the one list of commands; the palette, menus, tooltips and the shortcut sheet all read it. `keymap.ts` binds keys; `places.ts` lists where one can go. A surface registers its own commands from its `commands.ts`.

Not here: a key handler in a component, or a command known to only one menu.

To add one: add it to the surface's `commands.ts` with a name, a key and a run function; never edit `hotkey.ts` for it.
