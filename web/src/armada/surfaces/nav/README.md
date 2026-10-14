# surfaces/nav

The one list of places on the left: the workspace, Jump to, Home, repositories with their worktrees, channels, people, the account row.

Not here: another surface's internals, a raw `<button>`, `<input>`, `<select>` or `<dialog>` (use `kit/`), a network call (use `state/` and `api/`).

To add one: keep the four seams: `index.tsx`, `nav.css`, `commands.ts`, `showroom.tsx`; a part is a file beside them.
