# web/src/armada/beta — INTERIM

The interface signed-in people get at `/` (and at its earlier address, `/beta/`): the design's prototype (`var/armada-overhaul/design/final3/mock/`) running on this
server's real data, for the team to use every day ahead of the rebuild. It is not the rebuild and is not written to
its rules: eslint, stylelint, prettier and the custom checks ignore this directory. Nothing outside it imports it.
Delete it when the rebuild's conversation lands.

- `index.js`: the build entry (`armada-beta` in `web/webpack.assets.json`); imports the faces, the kit, then
  `host.js`, the page.
- `kit/`: the prototype's `mock.css` (its `@font-face` lines removed; the app's faces are imported instead),
  `proto.css`, `glyphs.js`, `marks.js`, `kit.js`, `motion.js`, `sound.js`. Each script sets window globals (`ic`, `mk`,
  `wk`, `smk`, `agentFace`, `frame`, `M`, `S`, ...). `marks.js` holds the marks of a run and of a step, as vectors;
  `kit.js` draws them (`mk`), the thinking star (`wk`, and `smk` for a step followed live) and Onix's mark
  (`agentFace`, the one place it is drawn).
- Partners (round three): the picker place (`#partner`), faces, the card and the evolution moment are in
  `partners.js`, `partner_picker.js` and `partner_card.js`; the sprites and `species.json` come from the git-ignored `web/sprites/licensed/` through
  `/beta/sprites/<path>`, partners from `/json/armada/partners` (`zerver/lib/partner.py`, in interim bot-storage rows).
  A face is the partner's Scarlet/Violet icon standing on a circle in its type's gradient (the r5 round-four study,
  `sv.html`); Onix has no face. With no pack, or a partner with no icon, faces are initials and nothing else changes.
- The page is ES modules, one for each part of it (below), wired by `host.js`. Reads and writes go to `/json/` with
  the session cookie and the page's CSRF token.
- `beta.css`: what real data needs beyond the kit. Tokens only. `feed.css`: the same for the conversation's parts (the
  feed's modules), so that the feed's rules do not interleave with everyone else's.

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
| `time.js` | the server's clock (`now`) and the ways a time is written; the day key (the local date as a number, 20261016) has node tests in `web/tests/armada/beta_time.test.cjs` | host |
| `frame.js` | the elements of the frame the kit draws (`main`, `feed`, `lane`, `nav`, `band`, the field's `tx`, ...), found by `bindFrame()` | host |
| `connection.js` | `api()`, `register()`, the event queue (`poll`), event dispatch (`handle`, `on`), the session (`session`), signing out, the tabs of this browser told of each other's session, reloading for new code | connection |
| `link.js` | the connection's rules, with nothing of the page in them: one register in flight, backoff with jitter, the long poll's 65 s deadline, the CSRF token mended from `GET armada/session`, signed out and signed in elsewhere (resilience.md R1); tested by `web/tests/armada/beta_link.test.cjs` | connection |
| `routing.js` | place keys and addresses, the place caches and what fills them, `openPlace()`, marking read | connection |
| `outbox.js` | a message on its way (`transmit`), the server's copy taking its place (`adopt`), a local row dropped | connection |
| `faces.js` | names, initials, stones, presence, `faceOf()` | faces |
| `presence.js` | presence reported as the old interface reports it (active or idle, on the server's interval), and heard | faces |
| `partners.js` | the sprite pack, the team's partners, faces redrawn, the evolution caption | faces |
| `partner_picker.js` | the partner picker, a place (`#partner`) | faces |
| `partner_card.js` | a person's card (any face opens it; mine from the account row) and the evolution moment | faces |
| `composer.js` | the field: the agent switch, the verb, the placeholder, attachments, `note()`, `ready()` | composer |
| `send.js` | sending: the words addressed and echoed, `send()`, `retry()` | composer |
| `caret.js` | the field's caret: the browser's own while it moves, a drawn one that blinks on the compositor at rest | composer |
| `model_picker.js` | the model and effort picker | composer |
| `mentions.js` | the `@` and `#` pickers, pills in the field, the switch's `@Onix` pill, a reply's quote (in the field and above a bubble) | composer |
| `mention_text.js` | pure: the field's words on the wire and back (mentions in place, `@onix` typed by hand), the pickers' order, the echo of a mention, a reply's quote, a send's client key (contract C4); node tests in `web/tests/armada/beta_mention_text.test.cjs` | composer |
| `parts.js` | the rule: server HTML becomes bubbles and blocks (`partsOf`); the local echo (`echoHTML`); a block of streamed words (`streamHTML`) | feed |
| `code_blocks.js` | a code block: its code, its copy button, long output folded with Show all | feed |
| `media.js` | pictures: the server's previews read into boxes of known size (`picOf`, `picHTML`), a Markdown image lifted out of its words, the name link that is a picture's label (`liftInline`, `dropNames`), loading when near (`loadPics`), my own pictures shown at once (`localPicture`, `rememberLocal`); its sheet is `media.css`; node tests in `web/tests/armada/beta_media.test.cjs` | feed |
| `lightbox.js` | a picture opened whole: pressed, it travels to the window's middle; the original swapped in once decoded; Left and Right through the conversation's pictures; Actual size; Esc, and focus back where it was (`openLightbox`, `wireLightbox`, `lightboxOpen`); its rules are in `media.css`; tested in `beta_media.test.cjs` | feed |
| `feed.js` | the conversation: its scroll, its turns, `drawLane()` | feed |
| `agent_turn.js` | the agent's turn: the working mark, the footer, the streamed words (`agent_stream` events, written once a frame, a block at a time) followed in place | feed |
| `reactions.js` | reactions | feed |
| `artifacts.js` | Onix's artifacts (`msg_type: "artifact"` submessages, the contract in `tools/armada/artifact_contract.json`): the block after Onix's words (`artifactPart`), the document opened in the column it borrows or, below 1408, a sheet (`openArtifact`, `closeArtifact`), copy, metric, meter, series, log, key_value and media drawn, actions sent to Onix, a later artifact in place (`onArtifactEvent`); its sheet is `artifacts.css`; node tests in `web/tests/armada/beta_artifacts.test.cjs` | feed |
| `widgets.js` | polls and to-do lists: the `widget` submessages replayed with mine not yet confirmed on top (`widgetOf`), drawn in place of the words that made them (`widgetPart`) and changed row by row (`drawWidget`), the `submessage` event (`onSubmessage`), my echo's widget (`localSubmessages`); its sheet is `widgets.css`; node tests in `web/tests/armada/beta_widgets.test.cjs` | feed |
| `receipts.js` | read receipts, the ticks and Info | feed |
| `threads.js` | threads, opened in place, with their own field | feed |
| `hover.js` | the message's controls: the hover overlay, More, edit, delete | feed |
| `events.js` | what each live event does to the page | feed |
| `runs.js` | runs: their marks, the live runs at load, the questions they wait on | feed |
| `checklist.js` | the run's checklist, in the header and the column | feed |
| `column.js` | the column: the checklist's standing home, Changes, Runs, People | feed |
| `nav.js` | the nav: channels, topics, direct messages, their trailing marks and counts | none this sprint |
| `header.js` | the header: the repository's facts | none this sprint |
| `band.js` | the band: what the run is doing, its clock, Stop | none this sprint |
| `views.js` | the views: README, Files, Changes | none this sprint |
| `home.js` | Home: what needs me | none this sprint |
| `later.js` | Later and Send when: a message put off (More › Later, the Later list on Home, what came back at Home's top), and a message of mine held until a time or a run's end (the menu on Send, its pending row at the conversation's foot); on the server's reminders, stars and scheduled messages, with `agent_run_id` for a run's end (`zerver/lib/armada_later.py`) | later |
| `palette.js` | Jump to (⌘K) | none this sprint |
| `lab.js` | the Lab: what is real, what is not, and its controls | none this sprint |
| `pages.js` | personal pages (contract C5): a person's page, a post, my editor; their places (`page:`, `post:`, `edit:`), the column's list of pages, their rows in Jump to, the `armada_post` event | pages |

Personal pages (contract C5) are `pages.js`, with its sheet `pages.css` and its pane (`#page`) in the template. Its place
kind, `page`, reaches the shared modules on one line each: `routing.js` hands its keys, addresses and opening to it
(`pageFromKey`, `pageHash`, `pageFromHash`, `openPage`) and hides the band and the field for it, as for Home;
`header.js` asks it for the header (`pageHead`), `column.js` for the column (`pagesSideHTML`), `palette.js` for its rows
in Jump to (`pageItems`); `host.js` wires its pane (`wirePages`) and its event (`armada_post`, also in `register()`'s
`event_types`). A person's card opens `page:<user id>`.

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

Served by `zerver/views/armada_beta.py` (`armada_beta_page`, which `/` calls from `zerver/views/home.py` while
`ARMADA_NEW_INTERFACE_AT_ROOT` is on, and `/beta/`) through `templates/zerver/armada_beta.html`; its sprites by the same
file's `armada_beta_sprite`. The old interface is at `/legacy/`, linked from nowhere.

To delete the beta, remove all of these in one change:

- this directory
- `zerver/views/armada_beta.py`, `templates/zerver/armada_beta.html`, `zerver/tests/test_armada_beta.py`,
  `web/tests/armada/beta_*.test.cjs`
- in `zproject/urls.py`, the import from `zerver.views.armada_beta`, the two `beta/` paths and the three
  `armada/partner...` routes, with their comments (`zerver/lib/partner.py` stays for the rebuild, which moves its rows into
  the Partner model)
- in `web/webpack.assets.json`, the `"armada-beta"` entry
- in `zerver/views/home.py`, the import of `armada_beta_page` and the branch of `home_real` that serves it (the rebuild's
  client takes its place at `/`)
- the `web/src/armada/beta` lines (and their comments) in `eslint.config.js`, `stylelint.config.js`
  (`ignoreFiles`), `.prettierignore` and `tools/linter_lib/exclude.py`
- `tools/armada/fork_ledger.d/beta.txt` and the `beta/` lines in `docs/armada/architecture.md`
  ("Directories"), in `.claude/CLAUDE.md` ("Where the build is") and in `.claude/rules/fork-boundary.md`

`git grep --untracked -n -e "INTERIM BETA" -e armada-beta -e armada_beta -e armada/beta` finds every one of them.
