# feed

The conversation: an imperative keyed renderer in plain TypeScript. `view.ts`, `row.ts`, `parts.ts` (message HTML into bubbles and blocks), `scroll.ts` (the one scroll controller), `echo.ts`, `read.ts`, `selection.ts`, `run_row.ts`, `stream_writer.ts` and their stylesheets.

Not here: Preact, jQuery, Handlebars; a timer other than the two named thresholds; a layout read outside `scroll.ts`; `innerHTML` outside `parts.ts` and `stream_writer.ts`.

To add one: build elements with `kit/dom.ts`, never replace or remove a row, and prove the behaviour with `arm check feed-semantics echo-semantics stream-cost`.
