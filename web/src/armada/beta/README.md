# web/src/armada/beta — INTERIM

The hosted beta at `/beta/`: the design's prototype (`var/armada-overhaul/design/final3/mock/`) running on this
server's real data, for the team to use every day ahead of the rebuild. It is not the rebuild and is not written to
its rules: eslint, stylelint, prettier and the custom checks ignore this directory. Nothing outside it imports it.
Delete it when the rebuild's conversation lands.

- `index.js`: the build entry (`armada-beta` in `web/webpack.assets.json`); imports the faces, the kit, then
  `host.js`, the page.
- `kit/`: the prototype's `mock.css` (its `@font-face` lines removed; the app's faces are imported instead),
  `proto.css`, `glyphs.js`, `marks.js`, `kit.js`, `motion.js`, `sound.js`. Each script sets window globals (`ic`, `mk`,
  `wk`, `agentFace`, `frame`, `M`, `S`, ...). `marks.js` holds the committed plain marks and Onix's face (the r4 marks
  study's drawings); `kit.js`'s `agentFace` is the one place the face is drawn, so a party sprite can replace it.
- The themed marks (the ball for a live run, the egg for a queued one) are not in git: they live in
  `web/sprites/licensed/marks/` (git-ignored), are served to signed-in people only by `armada_beta_themed_mark`
  (`/beta/sprites/marks/<name>`), and are read by `runs.js` (`loadThemedMarks`) before the first draw. Without them
  the page draws a dot and a ring.
- Partners (round three): the picker place (`#partner`), faces, the card and the evolution moment are in
  `partners.js`, `partner_picker.js` and `partner_card.js`; the sprites and `species.json` come from the git-ignored `web/sprites/licensed/` through
  `/beta/sprites/<path>`, partners from `/json/armada/partners` (`zerver/lib/partner.py`, in interim bot-storage rows).
  A face is the partner's Scarlet/Violet icon standing on a circle in its type's gradient (the r5 round-four study,
  `sv.html`); Onix has no face. With no pack, or a partner with no icon, faces are initials and nothing else changes.
- The page is ES modules, one for each part of it (below), wired by `host.js`. Reads and writes go to `/json/` with
  the session cookie and the page's CSRF token.
- `beta.css`: what real data needs beyond the kit. Tokens only.

## Modules and owners

The page was one file, `beta.js`, until contract C8 (`var/armada-overhaul/design/r6/CONTRACTS.md`) split it along the
surfaces the sprint's builders own, with no change in behaviour. A builder changes the modules it owns; a module
nobody owns this sprint takes fixes only, reviewed by the owner of `host.js`. Each later package that ports a surface
into the engineered client deletes the module it replaces.

| Module | What it holds | Owner |
|---|---|---|
| `host.js` | boot: the frame bound, every listener wired, the first place opened; the seams (below); `window.armadaBeta` for checks | host (one owner a wave) |
| `util.js` | `$`, `$$`, `el`, `esc`, `clamp`, `sleep`, `store` (this browser's storage), `press`, `mid`, `inlineMd` | host |
| `state.js` | `state` (the values more than one module replaces), the server's data (people, streams, unread, marks, places, ...), `DATA`, `stats` | host |
| `time.js` | the server's clock (`now`) and the ways a time is written | host |
| `frame.js` | the elements of the frame the kit draws (`main`, `feed`, `lane`, `nav`, `band`, the field's `tx`, ...), found by `bindFrame()` | host |
| `connection.js` | `api()`, `register()`, the event queue (`poll`), event dispatch (`handle`, `on`), signing out | connection |
| `routing.js` | place keys and addresses, the place caches and what fills them, `openPlace()`, marking read | connection |
| `outbox.js` | a message on its way (`transmit`), the server's copy taking its place (`adopt`), a local row dropped | connection |
| `faces.js` | names, initials, stones, presence, `faceOf()` | faces |
| `partners.js` | the sprite pack, the team's partners, faces redrawn, the evolution caption | faces |
| `partner_picker.js` | the partner picker, a place (`#partner`) | faces |
| `partner_card.js` | my partner card and the evolution moment: where the profile card grows | faces |
| `composer.js` | the field: the agent switch, the verb, the placeholder, attachments, `note()`, `ready()` | composer |
| `send.js` | sending: the words addressed and echoed, `send()`, `retry()` | composer |
| `model_picker.js` | the model and effort picker | composer |
| `parts.js` | the rule: server HTML becomes bubbles and blocks (`partsOf`); the local echo (`echoHTML`) | feed |
| `feed.js` | the conversation: its scroll, its turns, `drawLane()` | feed |
| `agent_turn.js` | the agent's turn: the working mark, the footer, the streamed words followed in place | feed |
| `reactions.js` | reactions | feed |
| `receipts.js` | read receipts, the ticks and Info | feed |
| `threads.js` | threads, opened in place, with their own field | feed |
| `hover.js` | the message's controls: the hover overlay, More, edit, delete | feed |
| `events.js` | what each live event does to the page | feed |
| `runs.js` | runs: their marks (and the themed ones), the live runs at load, the questions they wait on | feed |
| `checklist.js` | the run's checklist, in the header and the column | feed |
| `column.js` | the column: the checklist's standing home, Changes, Runs, People | feed |
| `nav.js` | the nav: channels, topics, direct messages, their trailing marks and counts | none this sprint |
| `header.js` | the header: the repository's facts | none this sprint |
| `band.js` | the band: what the run is doing, its clock, Stop | none this sprint |
| `views.js` | the views: README, Files, Changes | none this sprint |
| `home.js` | Home: what needs me | none this sprint |
| `palette.js` | Jump to (⌘K) | none this sprint |
| `lab.js` | the Lab: what is real, what is not, and its controls | none this sprint |

The pages builder (personal pages, contract C5) adds its own module, imported by `host.js` and listed here. A new place
kind is named in `routing.js` (`placeFromKey`, `hashOf`, `placeFromHash`, `openPlace`, `showHome`), which the
connection builder owns: agree the change with them.

## Seams

`host.js` exports these: what a module built on this page relies on. No module imports `host.js` (the third rule
below), so a module imports each from the module named beside it.

- `state` (`state.js`): the place in view (`state.place`), its cache (`state.P`), the view (`state.tab`), the session
  (`state.ME`, `state.queue`, ...), the feed's scroll, the field's attachments. The maps exported beside it (`people`,
  `streams`, `unread`, `marks`, `places`, ...) are changed in place, never replaced.
- `api(method, path, params, options)` (`connection.js`): every request to `/json/`.
- `handle(event, at)` and `on(type, handler)` (`connection.js`): event dispatch. `poll()` hands each event the queue
  returns to `handle`, which calls the handler `on` named for its type. `host.js` names them all, so a module that
  needs a new event type adds one line there (and the type to `register()`'s `event_types`).
- `register()` (`connection.js`): registers the event queue and applies what the server returns.
- `openPlace(place, options)` (`routing.js`): place drawing. A place is made from its key by `routing.js`'s
  `placeFromKey(key)`.
- The field: `tx` (`frame.js`), the element; `ready()` (`composer.js`), which hands the caret back after every handler;
  `send(options)` (`send.js`).

Three rules keep the modules apart:

- A module's top-level code reads only `util.js`, `state.js` and `time.js` while the page loads; everything else is read
  inside functions. Modules import each other in cycles, so another module may not have run yet. `host.js` runs last.
- A module assigns only its own `let`s (an import is read-only) and `state`'s properties.
- No module imports `host.js`; only the entry, `index.js`, does.

Served by `zerver/views/armada_beta.py` (`@zulip_login_required`) through `templates/zerver/armada_beta.html`; its
themed marks by the same file's `armada_beta_themed_mark`.

To delete the beta, remove all of these in one change:

- this directory
- `zerver/views/armada_beta.py`, `templates/zerver/armada_beta.html`, `zerver/tests/test_armada_beta.py`
- in `zproject/urls.py`, the import from `zerver.views.armada_beta`, the two `beta/` paths and the four
  `armada/partner...` routes, with their comments (`zerver/lib/partner.py` stays for the rebuild, which moves its rows into
  the Partner model)
- in `web/webpack.assets.json`, the `"armada-beta"` entry
- in `web/templates/navbar.hbs`, the `armada-beta-link` anchor and its comment
- the `web/src/armada/beta` lines (and their comments) in `eslint.config.js`, `stylelint.config.js`
  (`ignoreFiles`), `.prettierignore` and `tools/linter_lib/exclude.py`
- `tools/armada/fork_ledger.d/beta.txt`, the `marks/` section of `web/sprites/licensed/README.md`, and the `beta/` lines in `docs/armada/architecture.md`
  ("Directories"), in `.claude/CLAUDE.md` ("Where the build is") and in `.claude/rules/fork-boundary.md`

`git grep --untracked -n -e "INTERIM BETA" -e armada-beta -e armada_beta -e armada/beta` finds every one of them.
