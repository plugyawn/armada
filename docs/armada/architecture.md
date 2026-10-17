---
orphan: true
---

# Armada architecture

How the rebuilt frontend is made, reduced from the engineering specification
(third final, 14 October 2026). `docs/armada/design-language.md` says what is
drawn; this file says how it is built; `docs/armada/cuts.md` says what is
removed. Where the two disagree about what is drawn, the design wins; where
they disagree about how it is made, this file wins. A line marked **(P-n)**
follows a decision the user has not made yet; the build assumes the stated
answer. The work packages named here (F1, B3, and so on) are those of the
build plan in `var/armada-overhaul/design/final3/PLAN.md`.

This file is maintained by hand, from documents that are not in the
repository: `var/armada-overhaul/design/final3/SPEC-ENGINEERING.md` and
`PLAN.md` (the engineering specification and the plan; last changed on 15
October 2026, from the corrections of F2b and F5 listed in
`var/armada-overhaul/build/STAGE-NOTES.md`), the user's brief
(`var/armada-overhaul/BRIEF.md`, through statement 21, and statement 24 for
the beta's partners), and the reports of the packages built so far
(`var/armada-overhaul/build/F0.md` to `F5.md`), where they record what was
built differently from the specification. No script writes it. A change to the
design or to the plan is made in this file as well, by whoever makes it, and
this file is what the code is held to.

## The decisions on one page

| Question                | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Targets                 | Electron is the main target. Chromium 132 and later (Electron 34 and later) and Safari 18 and later are gated; Firefox 140 and later is best effort and not gated. English only. The user accepted these on 14 October. The frame is drawn from 1100 px wide; below 1040 the nav becomes a sheet; phones are not drawn (the plan's reading, not the user's word). Polyfills and old-browser fallbacks are deleted.                                                                                                                                                                                                                                        |
| Frame                   | One CSS grid written once in the server template, the same on every place: a worktree, a channel, a direct message and Home have one two-line header, one lane, one column, one band and one field. The document never scrolls. Each pane owns one native scroller. The conversation and the column beside it are one centred composition, computed in CSS from the window's width less the nav; the column stands when the pane is 1160 or wider (a 1408 window), on every place **(P-8)**, and it is not a grid track.                                                                                                                                  |
| Feed scroll             | A block scroller with `overflow-anchor: none` and one controller: a pinned flag kept by the scroll event, one ResizeObserver through which every height change passes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Rendering               | Preact `.tsx` for all chrome, on a kit. The feed is an imperative keyed renderer in plain TypeScript, with no Preact, jQuery or Handlebars in it. The field is a native `<textarea>`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| State                   | One small store per domain; views read stores and emit intents; only `api/` touches the network. Runs are realm-wide, fed by a snapshot in `/register` and by events.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Send                    | An outbox keyed by a client key. My bubble, the empty field, Onix's row with its working mark and the POST all happen in the keydown task with no layout read; the bubble is born where the default button stands and its rise is released after layout from that cell's cached rectangle. A row is never replaced and never removed.                                                                                                                                                                                                                                                                                                                     |
| `message_list_view.ts`  | Kept and hosted in the new frame until the new feed passes the behaviour pins, then deleted with `message_row.css` and `message_header.css`. The list model (`message_list.ts`, `message_list_data.ts`) and the read flags stay.                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Ultrafast               | Codex 0.157.1 calls it the `ultrafast` service tier (name "Ultrafast"). It is advertised per model by the live catalog, today for `gpt-6-astra` only. The tier becomes a per-topic setting resolved against the catalog at each turn; text reaches clients as volatile deltas coalesced at 33 ms, written to the screen once a frame. The default model of a new topic is open **(P-2)**: the user asked for "sol 6.1" on ultrafast, which the catalog does not offer, and is being asked again. Until they answer, the plan's assumption stands: `gpt-6-astra` on ultrafast where the server's login is offered it, otherwise `gpt-5.6-sol` on priority. |
| Fonts                   | Söhne from `web/fonts/licensed/` (git-ignored) or `ARMADA_FONT_DIR`, found by a generator at build time; Hanken at 106% behind it; Geist Mono for code; no pixel face; all first-paint faces preloaded with `font-display: block`.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Budgets                 | Counts are gates, times are tripwires at three times the budget, both run by `arm check` against the dev build; sizes are gated on a production-mode build.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Code location           | `web/src/armada/`, inside the existing toolchain roots. Tokens keep the mock's names; classes are `armada-…`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Names                   | Armada is the product, Onix is the agent. The server and the wire say `armada` or a plain noun (`AgentRun`, `agent_run`); the old code name is gone (package R1) and a lint guard keeps it out.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Palette                 | Colour tokens are one generated file a palette (**(P-5)** Lapis the default; Tide, Citrine and the second final's Onyx as swaps), chosen by `data-palette` on the root element, which the server sets from `ARMADA_PALETTE`. No other file names a colour. A speaker's bubble and face take one of six stones from their id; code has four roles and a diff two grounds, all tokens of the palette.                                                                                                                                                                                                                                                       |
| Type                    | Five sizes and two lines: what is said and read is 16 on 24; chrome, lists and code are 14 on 20.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Icons and marks         | 49 stroked glyphs and the pixel drawings (marks, the working mark as one stepped sprite, the sigil at 20, 28 and 64 with its outline, dots) are generated into one SVG sprite, inlined once.                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Motion                  | `motion/` is the design's `motion.js`, ported under the same names and the only caller of `element.animate`; hover and press are one stylesheet. Measuring happens after layout, never in an input task.                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Beside the conversation | One column host on every place, from the header's second line to the field's foot, drawing the plain lists its place registered (a worktree: Checklist, Changes, Runs, People). A trace, a past run, an artifact or a thread opened beside borrows it and gives it back. Under the threshold the same contents are sheets. No panel.                                                                                                                                                                                                                                                                                                                      |
| Threads                 | **(P-7)** A thread opens where it is, under its root bubble; the column is the alternative, one action away. One component, two hosts.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Proof                   | Every pinned behaviour has a named breakage of the application that turns it red. A package that moves the behaviour rewrites the breakage (section 8).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Pending                 | The cut list (P-1) and the targets (P-3) are answered. The base model (P-2), the design's five questions (P-5 to P-9) and where the hosted beta runs are open; the first six are assumed and marked, and `PLAN.md` appendix D costs the alternatives.                                                                                                                                                                                                                                                                                                                                                                                                     |

## Ground rules

- **The brief is the budget.** "Not even the slight latency at keypress and send" is the numbers in "Budgets" below, not a mood. A frontend change names the check or test that proves it; a server change names its backend test; a change to documentation or guidance says that no check applies.
- **Features are removed by deleting files**, never by hiding them in CSS. A removed event type leaves `server_event_types.ts` and its dispatch arm in the same change, with its tests.
- **Upstream mergeability is given up** for the frame, feed, composer, sidebars, settings and their styles (statement 7). It is kept in practice for the data layer and the server outside Armada's own modules. The branch is never rebased on or merged with upstream Zulip; an upstream fix, a security fix above all, is brought in deliberately as a named cherry-pick. A deep change to a Zulip file says what it gives up and what it buys. The fork ledger ("Toolchain") records every Zulip file the fork changes.
- **One new dependency in the whole programme**, approved and installed on 13 October (P-4, answered): `@babel/plugin-transform-react-jsx` (dev), pinned to the 7 line, with `preact` moved to the runtime dependencies. Preact 10.29 is already installed and already in the initial bundle through `@uppy/core`. Nothing else is added; several libraries leave.
- **English only**, accepted by the user on 14 October. Every user-visible string is still tagged (`$t`), so translation can return; the 49 other locales are cut.
- **The dev environment** is the Lima VM in `ENV.md`. Its sync skips git-ignored paths, which matters for fonts (section 7).

## Directories

```text
web/src/armada/
  boot.ts               the one module ui_init.js calls: mounts the frame, wires stores to events, registers commands
  hooks/                the only armada modules that old Zulip files import
    events.ts             dispatch(event): called first in server_events_dispatch.js
    narrow.ts             on_narrow(), on_leave(): called from message_view.ts
    legacy_*.ts           per area, the old initialisers still in use; emptied by the package that replaces the area
  tokens/               tokens.css (every literal that is not a colour: 105 properties of the mock's, and three thresholds it lacks),
                        palette-lapis.css, palette-tide.css, palette-citrine.css, palette-onyx.css (generated, 56 colour
                        properties each; the only files with a colour), palette-shared.css (generated: two shared colours
                        and Increase Contrast), fonts.css, layers.css. No base.css: an element rule above the zulip layer
                        would restyle every old screen, so base rules wait for the kit's components (build/F3.md)
  kit/                  each component written once as a description (tree.ts) by a recipe (icons.ts, controls.ts,
                        conversation.ts, blocks.ts, chrome.ts, lists.ts, raised.ts); view.tsx renders a description
                        with Preact, dom.ts as plain elements for the feed, so the two cannot drift; index.ts exports a
                        component per recipe; one stylesheet per family (17, among them hover.css, the hover and press
                        layer, and marks.css, the stepped loops); icons/sprite.svg + names.ts (generated: glyphs,
                        marks, working mark, sigil, dots)
  motion/               motion.ts (the public names of mock/motion.js: release, current, halt, rest, flip, fade, appear, leave, hold,
                        thumb, figure, cells, swap, cross, grow, fly, carry, land, kick, squash, burst, shake, refuse, say:
                        the only callers of element.animate), ring.ts and tip.ts (the window's one focus ring and one tooltip),
                        springs.ts, guard.ts, layer.ts, index.ts
  sound/                sound.ts (engine, limiter, focus matrix), recipes.ts (pure data, shared with the reel script)
  state/                store.ts and one file per store (section 4)
  api/                  the only network code, which translates wire names into the tree's words: client.ts, schemas.ts, one file per resource
  commands/             registry.ts, keymap.ts, places.ts
  frame/                frame.tsx, frame.css (the grid and the composition), panes.ts, route_view.ts, after_layout.ts
  feed/                 view.ts, row.ts, parts.ts, scroll.ts, echo.ts, read.ts, selection.ts, reactions.ts, group.ts, times.ts,
                        days.ts, overlay.ts, thread_line.ts, thread_inline.ts, run_row.ts, stream_writer.ts, working.ts,
                        typing.ts, question.ts, footer.ts, *.css      (no Preact here)
  surfaces/             nav/ header/ band/ composer/ column/ checklist/ lists/ trace/ home/ readme/ files/ changes/ artifacts/
                        emoji/ palette/ dialogs/ settings/      each: index.tsx, parts, <name>.css, commands.ts, showroom.tsx
                        portico/: the pages before the app, which the server draws; portico.css and the armada-portico entry
  showroom/             index.tsx, fixtures.ts, bench.ts, showroom.css: the dev page /devtools/armada/ (every component
                        in every state, Preact and DOM side by side, the four palettes, the motion and sound benches)
  bridge/               temporary hosts and styles for old surfaces; README.md lists each with its deleter
  beta/                 INTERIM: the hosted beta at / (and /beta/), the design's prototype on real data; outside every rule
                        above (no lint applies), built by the armada-beta entry, deleted when the conversation lands
                        (beta/README.md lists what goes with it)
web/tests/armada/       node tests
web/fonts/armada/       Geist Mono, Hanken Grotesk (OFL, committed)
web/fonts/licensed/     git-ignored; Söhne
web/generated/          armada_fonts.css (the preloads are found in the webpack stats, so no armada_fonts.json)
tools/armada-checks/    the gates: checks/, lib/ (the harness, mutate.mjs and prove.mjs among it), fixtures/, known.json, and
                        from F5, mutations/<package>.mjs (a package's rewritten breakages, loaded over the built-in ones)
tools/armada/           fork_ledger, size/, reel (renders the sound reel from recipes.ts), palette.py + palette.json
                        (write the four palette files and refuse a palette that fails a gate), icons.py + icons_gen.py
                        (write the sprite and its names), artifact_contract.py + artifact_contract.json (write the
                        artifact contract's Python and TypeScript copies)
docs/armada/            design-language.md, architecture.md, cuts.md, fork-ledger.md (generated)
```

Every directory under `web/src/armada/` has a `README.md` of a few lines: what
belongs there, what does not, and what to copy when adding to it.

**Built so far** (the Foundation packages F0 to F4, uncommitted): `tokens/`,
`kit/`, `motion/`, `sound/`, `showroom/`, the checks and the tools above, and
the README of every other directory. `boot.ts`, `hooks/`, `frame/` and
`bridge/`'s contents are F5's; `state/`, `api/`, `commands/`, `feed/` and the
surfaces come in the stages after it. Each package's report is
`var/armada-overhaul/build/<id>.md`.

## Layers: who may import whom

The rules are zones of `import/no-restricted-paths` and options of
`no-restricted-imports` in `eslint.config.js`; a violation is a lint error.

| Code in                    | May import, of Armada's                                                           | May not                                                              |
| -------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| old Zulip modules          | `armada/hooks/`; `boot.ts` from `ui_init.js`; bundle entry files anything         | anything else under `armada/`                                        |
| `kit/`                     | `motion/`, `tokens/`                                                              | stores, the API, the frame, the feed, surfaces                       |
| `state/`                   | `api/`, other stores                                                              | any module that touches the DOM                                      |
| `api/`                     | `web/src/channel.ts` (it alone)                                                   |                                                                      |
| `feed/`                    | `kit/dom.ts`, `kit/icons/`, `state/`, `motion/`                                   | Preact, a `.tsx` file, the API                                       |
| `surfaces/<name>/`         | `kit/`, `state/`, `commands/`, `motion/`, `sound/`, another surface's `index.tsx` | another surface's internals, the API, the feed, the frame, `bridge/` |
| everything under `armada/` |                                                                                   | jQuery, Handlebars and `.hbs`, tippy.js, micromodal                  |

Views read stores and emit intents; only `api/` touches the network; a store
is changed by server events and by API results. The frame package creates each
surface directory once, already wired into `boot.ts`, the stylesheet order, the
command registry, the column's registry and the showroom, so that a surface
package fills its own directory and edits no shared file.

## The cascade

`@layer zulip, armada.tokens, armada.base, armada.kit, armada.surfaces`.

Every stylesheet that is not under `web/src/armada/` is wrapped in
`@layer zulip` at build time by `web/zulip-layer-loader.ts`: the sheets of
`web/styles/` (after PostCSS), the generated icon font's sheet, and the sheets
of libraries in `node_modules`. CodeMirror writes its styles into a `<style>`
element at run time through the `style-mod` package; the same loader makes
that text a `@layer zulip` block too, and fails the build if it can no longer
find the place. A sheet that is already one `@layer zulip` block passes
through unchanged. Rules in a layer lose to rules in a later layer whatever their
specificity, so a new rule wins over an old one without an id or `!important`;
among themselves the old rules cascade as they always did, because they share
one layer in their old order. The inline `<style>` of the loading splash in
`templates/zerver/app/index.html` is outside any layer and overrides nothing
else; it goes with the splash.

Two libraries still write unlayered rules at run time, and are left alone
because no old rule overrides what they write: flatpickr adds one rule (the
arrow of an open date picker) with `insertRule` to the first stylesheet it can
write to, at its top level, and Plotly writes its own `<style>` on `/stats`,
a page the cut list removes. An override of either written in an old sheet
would lose. A check that every stylesheet starts with `@layer` has to allow
for the splash, for Plotly's sheet and for flatpickr's one inserted rule.

New CSS lives beside its component, is plain CSS with native nesting (no
PostCSS), and puts itself in its layer with an `@layer armada.…` block.

## Toolchain

- **`.tsx`.** Babel compiles JSX to Preact's automatic runtime
  (`web/babel.config.js`); TypeScript checks it (`tsconfig.json`: `jsx:
react-jsx`, `jsxImportSource: preact`); webpack, the node test loader
  (`web/tests/lib/index.cjs`), coverage, ESLint, Prettier and the translation
  extractor (`makemessages`) all know the extension.
- **Tests.** Node tests for the new tree are `web/tests/armada/*.test.cjs`
  (`arm test armada/<name>`). Components mount into a real DOM
  (`web/tests/lib/real_dom.cjs`). `tools/test-js-with-node --coverage` requires
  100% statement coverage of every `.ts` and `.tsx` file of `state/`, `api/`,
  `motion/`, `sound/` and `commands/`, and of `feed/parts.ts`
  (`ARMADA_FULLY_COVERED_GLOBS`).
- **Lint.** `tools/lint` also lists files git does not track yet. The design
  language is enforced for `web/src/armada/**/*.css` by an override in
  `stylelint.config.js`, the layering and the banned calls by zones in
  `eslint.config.js`, and system typefaces, motion literals in script, stray
  colour tokens and names taken from the agent by
  `tools/linter_lib/custom_check.py`. `.claude/rules/` names the rule behind
  each statement. The surfaces are the directories under `surfaces/`, read
  when lint runs, so a new one is fenced without a change to the
  configuration. A rule that matches syntax cannot see a type: any method
  called `animate` is taken for `element.animate` (the motion kit has none of
  that name), and the layout, network and HTML rules know the names listed in
  `eslint.config.js` and no alias of them.
- **The fork ledger.** `tools/armada/fork_ledger` compares the tree with the
  fork base (`7813218321`). Every Zulip file that differs must have a line in
  `tools/armada/fork_ledger.d/<package>.txt` saying what became of it: `hooked`,
  `bridged:<package>`, `absorbed:<path>`, `deleted`, or `kept` for toolchain,
  server code and tests that are changed in place; `poc.txt` is the snapshot of
  what the proof of concept had changed before the rebuild began, with the id
  of each file's content then, and a file that has only that line must still
  have that content: a package that edits it adds its own line. `tools/lint`
  runs `--check`; `--write` regenerates `docs/armada/fork-ledger.md`, which also
  counts the old Armada modules, the bridges, the test exemptions, the old
  stylesheets and templates and the files naming a system typeface that are
  left. Those counts, the bridges apart, are kept in
  `tools/armada/fork_ledger.d/counts.json` and only fall: `--check` fails when
  one is higher than recorded, and `--write` lowers the record.

## Frame, composition and scroll

One CSS grid, written once in the server template, is the window on every
place: a two-line header, the conversation's lane, the column beside it, the
band and the field. The document never scrolls; each pane owns one native
scroller. The conversation and the column are one centred composition computed
in CSS from the pane's width (`--col`, `--side-w`, `--side-gap`, `--comp`,
`--lx`); no script writes them. The pane's width is the window's less the nav
(`--W`), switched by a media query on a 1408 window (`frame/frame.css`), and
not read from the plane as a size container: the field sits in the plane on an
`auto` track, so a size container restyled the whole plane on every key (1.45
ms of style a key, F5). A media query cannot read `--nav-w`, so when the nav
becomes draggable, B1 moves the threshold with it. The column is positioned
over the grid and is not a track, so that showing or folding it moves nothing.
Layout is read only in the after-layout phase (`frame/after_layout.ts`,
`motion/`, `feed/scroll.ts`, `surfaces/composer/origin.ts`), never after a
write inside an input task.

## Rendering

Chrome is Preact `.tsx` on the kit. The kit writes each component once, as a
description that Preact (`kit/view.tsx`) and the feed's DOM builder
(`kit/dom.ts`) both render; a test and `arm check kit` compare the two. The feed is an imperative keyed renderer
in plain TypeScript: rows are created once and never replaced or removed,
because the element created at send is the element that later holds the
answer. The field is a native `<textarea>`: input methods, undo, spellcheck
and selection are the platform's. Popovers and sheets are placed in CSS from
fixed cells; the kit uses the Popover API and `<dialog>`.

## State, send and runs

One small store per domain under `state/`. Runs, worktrees and checklists are
realm-wide state fed by a snapshot in `/register` and by events, not by
requests on every topic entry. A send goes through an outbox keyed by a client
key: my bubble, the empty field, the agent's row with its working mark and the
POST all happen in the keydown task with no layout read.

## The feed

A block scroller with `overflow-anchor: none` and one controller: a pinned flag
kept by the scroll event and one ResizeObserver through which every height
change passes. `parts.ts` splits a message's HTML into bubbles and blocks, the
same on the client and the server (`arm check parts-corpus`). Streamed text is
written once a frame by `stream_writer.ts`. The old `message_list_view.ts`
stays hosted in the new frame until the new feed passes the behaviour pins of
`arm check feed-semantics echo-semantics keyboard-semantics`; the list model
(`message_list.ts`, `message_list_data.ts`) and the read flags stay for good.

## Fonts

`--f-human: "Söhne", "Hanken Grotesk", sans-serif` and `--f-code: "Geist Mono",
monospace`. Söhne is licensed and never committed: it is read from
`web/fonts/licensed/` (git-ignored) or `ARMADA_FONT_DIR` by a generator at build
time, with Hanken Grotesk at 106% behind it, so test files and licensed files
are the same code path. Every face used on first paint is preloaded with
`font-display: block`, because a swap moves text. No system face is named
anywhere. Emoji are content and fall through to the platform's emoji face.

## Budgets, and the check that enforces each

All checks are `tools/armada-checks/checks/*.mjs`, run by
`limactl shell armada -- arm check [name]` against the dev build at 1440 by 900,
with the ratchet in `known.json`. Counts gate; a time gates only at three times
its budget. Checks the table calls new are created by the plan's packages.

| Budget             | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Check and assertion                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A key in the field | 0 layouts forced in `keydown`, `input`, `keyup`; 0 timers that force layout; at most 1.5 layouts and 2 style recalculations a key; 0 long tasks in 200 keys, with `@`, `#`, `:` and backtick tokens too. Handler 1 ms at p95                                                                                                                                                                                                                                                                                                                     | `typing-cost`: `*.handler-forced-layouts.max-per-key`, `*.timers-forcing-layout`, `*.layouts-per-key`, `*.style-recalcs-per-key`, `tokens.long-tasks`; tripwire `handler-ms.median` ≤ 3                                                                                                                                                                                                                                       |
| Send               | row in the next frame; 0 forced layouts in the Return handler (the harness's limit of 1 is tightened to 0); handler 8 ms at p95; 0 requests before the frame; composer height unchanged; six unacknowledged sends all on screen                                                                                                                                                                                                                                                                                                                  | `send-cost`: `plain.row-in-first-frame`, `plain.enter-forced-layouts`, `plain.enter-handler-ms.median`, `*.row-before-server`, `rapid.six-unacknowledged.rows-shown`, `*.compose-height-changes`                                                                                                                                                                                                                              |
| Acknowledgement    | the server never replaces the row or its body, moves it or resizes it                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `send-cost`: `plain.row-replaced-by-server-echo`, `plain.content-replaced-by-server-echo`, `plain.row-moved-after-paint`, `plain.row-height-changed-after-paint`                                                                                                                                                                                                                                                              |
| Onix acknowledged  | an element matching `[data-run-ack]` is visible while the POST is held; it is the element that later holds the answer; 0 frames from send to first words with neither mark nor words; the mark wholly in view from its first frame; 0 rows removed and 0 heights changed when the run settles                                                                                                                                                                                                                                                    | `send-cost/agent.client-ack-before-server`; `stream-cost/ack.same-element`, `ack.empty-frames` (new)                                                                                                                                                                                                                                                                                                                          |
| Stream             | at 400 and at 1,200 words a second through the real dispatch arm: 0 forced layouts; at most 1.1 layouts a frame and 2 an event; at most 1 DOM write an event and 1 a frame per run; no event that takes a long task to apply; no word waits more than 2 frames; newest word visible, above the band and at 60 percent ink or more in every frame; the feed at most 20 px behind the stream; first words painted within one frame of arriving; final text equal to the server's. Script 2 ms a frame                                              | `stream-cost` (written by F0, 24 assertions, 12 known): `*.forced-layouts`, `*.layouts-per-frame`, `*.layouts-per-event`, `*.writes-per-event`, `*.long-tasks`, `*.words-waiting.max-frames`, `*.frames-word-hidden`, `*.first-words.frames-to-paint`, `*.settle.text-equal`, `*.events-replayed`; tripwire `*.script-ms-per-frame` ≤ 6. The gates as they stand, and when each tightens, are in the table below              |
| Split              | client and server Markdown give the same parts for the corpus                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | `parts-corpus` (F0): `cases-compared` ≥ 150 (309 today), `cases-differing-in-kind` = 0 (passes), `cases-differing` = 0 (10 today, known with that ceiling)                                                                                                                                                                                                                                                                    |
| Topic revisit      | 0 rows rendered again; at most 2 requests, 0 for a kept topic; shown within 16 ms                                                                                                                                                                                                                                                                                                                                                                                                                                                                | `topic-switch-cost`: `*.rows-rendered-again`, `*.requests-per-switch`, `*.revisit-ms.median` (the budget is far from today's 61 ms, so it gates)                                                                                                                                                                                                                                                                              |
| First visit        | header and views in the frame's first frame; nothing moves after it appears                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `topic-entry-stability`                                                                                                                                                                                                                                                                                                                                                                                                       |
| Nothing moves      | every frame of every interaction: header, views, nav rows, band, field, conversation and column within 0.5 px across a worktree, a channel, a direct message and Home, the four views, the column's contents and their sheets, the picker, a reaction, typing, a run starting and stopping, a repository opening; 0 `layout-shift` entries outside the pane's content except the five the design accepts, each the thing acted on (two for a nav block folded, two for the header's facts and one for the model's label on a change of worktree) | `frame-stability`, `topic-entry-stability`, `nav-stability` (today's `sidebar-stability`, re-pointed), `column-stability` (new, replaces `sidebar-toggle-stability`), `interaction-stability` (F0 walks the 19 interactions that exist today, 38 assertions; A1, B4, C1 and D1 add the design's own as their surfaces appear, the thread unfolding in place among them, where the rows below the root are the thing acted on) |
| Idle               | quiet topic: at most 10 layouts, 10 style recalculations, 0.5 s of main thread and 0 requests a minute. A live run or open monitors: 120, 240, 1 s, 6                                                                                                                                                                                                                                                                                                                                                                                            | `idle-cost`                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Motion             | only `transform` and `opacity` animate (colour on the two fades); nothing over 440 ms; no bézier; at most 2 loops with one run live and 0 outside the pane; the opposite input at 100 ms neither jumps (under 8 px) nor queues; hover, press, focus and selection all move (a hover fill at 30% in the frame of entry and whole in 80 ms, a press at 0.96, one travelling ring, one thumb a list); what I asked for is whole and opaque in its first frame                                                                                       | `motion` (new): `getAnimations()` sampled through the tour; stylelint for the static half                                                                                                                                                                                                                                                                                                                                     |
| Startup            | first paint is the frame; longest task 100 ms; layout shift 0.01; 0 late fonts; at most 100 kB of images before the first row; usable topic in 800 ms on a LAN                                                                                                                                                                                                                                                                                                                                                                                   | `startup`: `longest-task-ms.median`, `cumulative-layout-shift.median`, `late-font-requests`, `image-kilobytes-before-first-row`; tripwire `first-row-ms.median` ≤ 2,400                                                                                                                                                                                                                                                       |
| Size               | initial JavaScript at most 2.0 MB minified (5.94 MB today); initial CSS at most 160 kB minified (1.22 MB); no lazy chunk over 800 kB; at most 5 preloaded fonts and 260 kB; the inlined sprite at most 16 kB                                                                                                                                                                                                                                                                                                                                     | `size` (new; `var/armada-overhaul/lean-weight/webpack.lw.config.mjs` and `feat.py`, vendored). Ceilings step down stage by stage through `known.json` `max`                                                                                                                                                                                                                                                                   |
| Weight at rest     | at most 900 DOM nodes on a topic (1,695 today), 2,500 CSS rules (9,315), 40 listeners on `body` (276)                                                                                                                                                                                                                                                                                                                                                                                                                                            | `weight` (new)                                                                                                                                                                                                                                                                                                                                                                                                                |
| Design language    | the counts of `SPEC-DESIGN.md` section 12 and appendix A on the running app's screens, both themes, at 1440, 1100 and 1920; where the design accepts a failed row, its own figure is the ceiling                                                                                                                                                                                                                                                                                                                                                 | `design-lint` (new, section 11)                                                                                                                                                                                                                                                                                                                                                                                               |
| Server             | commit to `turn/start` 150 ms at p50; 0 durable writes closer together than 1 s while streaming; deltas neither lost nor doubled                                                                                                                                                                                                                                                                                                                                                                                                                 | backend tests with a fake app-server (`zerver/tests/test_armada_stream.py`, new)                                                                                                                                                                                                                                                                                                                                              |
| No errors          | 0 console errors, uncaught exceptions or failed responses on the tour                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `console-clean`                                                                                                                                                                                                                                                                                                                                                                                                               |

## What is in the initial bundle, and what is not

**Initial:** the frame, nav, header, checklist, band, field, the feed and stream writer, the column's host with the checklist and the lists, Home, the palette's shell and the command registry, stores and API, motion, the sound engine without buffers, the sprite of glyphs and marks (one inline SVG, at most 16 kB), tokens, the four palettes (the three that are not the default are about 14 kB before minification, from the mock's files) and kit CSS.

**Lazy, loaded on first use:**

| Chunk                                          | Loaded when                                                                                | Preloaded on idle                              |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| Files and README (workspace shell, tree, tabs) | a README, Files or Changes view opens                                                      | when a repository topic is in front (as today) |
| CodeMirror and its languages                   | a file or Source opens                                                                     | with the above                                 |
| the rich README editor                         | Edit is chosen                                                                             | never                                          |
| Changes (diff rendering)                       | Changes opens                                                                              | never                                          |
| artifact renderers, by kind                    | an artifact is opened                                                                      | never                                          |
| KaTeX                                          | a message with math is rendered                                                            | never                                          |
| lightbox                                       | an image is opened                                                                         | never                                          |
| uploads (uppy, tus, compressor)                | a file is attached, dropped or pasted                                                      | never                                          |
| emoji names                                    | `:` opens the typeahead, or the picker opens                                               | never                                          |
| the emoji picker, at most 60 kB, no image      | React is pressed, or `:` on a selected message                                             | never                                          |
| Settings                                       | ⌘ and comma                                                                                | never                                          |
| trace                                          | a step is pressed in the checklist                                                         | never                                          |
| message search results                         | a search is run from the palette                                                           | never                                          |
| sound buffers                                  | rendered on the first gesture; the `AudioContext` does not exist before it, or while muted | n/a                                            |

**Gone from every bundle:** the Handlebars compiler (one import, 208 kB minified), Sentry (370 kB), core-js, regenerator-runtime and the `light-dark()` expansion, jquery-validation, both emoji sprite sheets, FontAwesome, flatpickr, and the webpack entries of pages Armada stops serving, each with its route (K-96). Kept entries: `app`, `common`, `signup` (the old pages under `corporate/`), `dev-login`, `error-styles` (a server's custom 5xx page), `desktop-login`, the dev showroom, and the pages Armada serves: `armada-portico` (the pages before the app, the 4xx and 5xx pages among them; `templates/zerver/armada_portico.html`), `portico` (the old shell's pages: integrations, development pages), `redirect-to-post` (the link in a sign-up email), `desktop-redirect` (the desktop app's sign-in) and `help` (`/policies/`).

## Names

| Name       | Where it is used                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Armada** | the product, in every user-visible string and brand asset; the home of new frontend code: `web/src/armada/`, `armada-` classes, `docs/armada/`, `tools/armada*`; and the server and wire namespace where a plain noun does not fit better: `zerver/lib/armada*.py`, `/json/armada/` routes, `ARMADA_*` settings, the `armada` management commands and deploy scripts. The models and the wire names people read are plain nouns (`AgentRun`, `Repository`, `Workspace`, the `agent_run` event, `is_agent_message`); the new events are `agent_stream`, `armada_topic` and `armada_runtime`, and the send parameter `run_intent` |
| **Onix**   | the agent, to people. It is one server constant (`ARMADA_AGENT_NAME`), sent to clients in `/register`; strings say `{agent}`. The mention is `@**Onix**`. No file, class, token or identifier is named after the agent: it has been renamed twice. Code says `agent`, `run`, `worktree`, `repository`                                                                                                                                                                                                                                                                                                                           |
| **legacy** | the old interface, which the rebuild deletes surface by surface: `web/src/legacy_*.ts`, `legacy_*.hbs`, `legacy*.css`, `.legacy-*` classes and `--legacy-*` properties, so that `armada-` stays the kit's                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Zulip**  | nowhere in the interface                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

The hosted beta's partners (the Pokémon each person chooses, which levels up
and evolves with use) have no model yet: until a later package moves them to
a `Partner` model with its migration, each person's row is JSON in a
`BotStorageData` row under the agent's bot, key `partner:<user_id>`,
written only by `zerver/lib/partner.py`. No other application code reads or
writes those rows.
