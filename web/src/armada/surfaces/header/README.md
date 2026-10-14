# surfaces/header

The two-line header of every place: path and title, branch, pull request and latest commit on line 1; the four views, or one sentence, on line 2.

Not here: another surface's internals, a raw `<button>`, `<input>`, `<select>` or `<dialog>` (use `kit/`), a network call (use `state/` and `api/`).

To add one: keep the four seams: `index.tsx`, `header.css`, `commands.ts`, `showroom.tsx`; a part is a file beside them.
