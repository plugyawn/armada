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
  feed's modules), so that the feed's rules do not interleave with everyone else's. `previews.css`: a link's card
  (`previews.js`).

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
| `previews.js` | a link's card under the words that hold it: a page's preview, a video's still, a quote of a linked message (and its chip in the words), at most three a bubble with "+N more"; the place a preview the server is still fetching keeps from the first draw (statement 41); its sheet is `previews.css`; tested by `web/tests/armada/beta_previews.test.cjs` | feed |
| `code_blocks.js` | a code block: its code, its copy button, long output folded with Show all | feed |
| `media.js` | pictures: the server's previews read into boxes of known size (`picOf`, `picHTML`), a Markdown image lifted out of its words, the name link that is a picture's label (`liftInline`, `dropNames`), loading when near (`loadPics`), my own pictures shown at once (`localPicture`, `rememberLocal`); its sheet is `media.css`; node tests in `web/tests/armada/beta_media.test.cjs` | feed |
| `chunks.js` | what loads on first use: the lightbox, an artifact's document, a scene and what a plugin's widget opens are chunks (`chunk`, `loadedChunk`), prefetched once the first place is drawn and the page is idle (`prefetchChunks`); a chunk imports nothing the first load holds, and is handed what it uses of the page when it arrives (its module's default export) | feed |
| `lightbox.js` | a chunk (`chunks.js`): a picture opened whole: pressed (the press is `media.js`'s `wireLightbox`), it travels to the window's middle; the original swapped in once decoded; Left and Right through the conversation's pictures; Actual size; Esc, and focus back where it was (`openLightbox`, `lightboxOpen`); its rules are in `media.css`; tested in `beta_media.test.cjs` | feed |
| `feed.js` | the conversation: its scroll, its turns, `drawLane()` | feed |
| `agent_turn.js` | the agent's turn: the working mark, the footer, the streamed words (`agent_stream` events, written once a frame, a block at a time) followed in place | feed |
| `reactions.js` | reactions | feed |
| `artifacts.js` | Onix's artifacts (`msg_type: "artifact"` submessages, the contract in `tools/armada/artifact_contract.json`): the block after Onix's words (`artifactPart`), the document opened in the column it borrows or, below 1408, a sheet (`openArtifact`, `closeArtifact`), the document drawn by `artifact_view.js` (a scene by `scene.js`), both loaded on first use, actions sent to Onix, a later artifact in place (`onArtifactEvent`); its sheet is `artifacts.css`; node tests in `web/tests/armada/beta_artifacts.test.cjs` | feed |
| `artifact_view.js` | a chunk (`chunks.js`): an artifact's document drawn: copy, metric, meter, series, log, key_value, media, and a scene through `scene.js` (`panel`, `mount`, `replace`) | feed |
| `scene.js` | a chunk (`chunks.js`): an artifact's scene, ported from the old renderer: nodes bound to the data (`derive`), a graph in ranks from top to bottom (`ranksOf`), connectors drawn after layout, steps with Previous, Play, Next and a track (`sceneHTML`, `wireScene`); its rules are in `artifacts.css`; tested in `beta_artifacts.test.cjs` | feed |
| `widgets.js` | polls and to-do lists: the `widget` submessages replayed with mine not yet confirmed on top (`widgetOf`), drawn in place of the words that made them (`widgetPart`) and changed row by row (`drawWidget`), the `submessage` event (`onSubmessage`), my echo's widget (`localSubmessages`); its sheet is `widgets.css`; node tests in `web/tests/armada/beta_widgets.test.cjs` | feed |
| `receipts.js` | read receipts, the ticks and Info | feed |
| `threads.js` | threads, opened in place, with their own field | feed |
| `hover.js` | the message's controls: the hover overlay, More, edit, delete | feed |
| `events.js` | what each live event does to the page | feed |
| `runs.js` | runs: their marks, the live runs at load, the questions they wait on | feed |
| `checklist.js` | the run's checklist, in the header and the column | feed |
| `column.js` | the column: the checklist's standing home, Changes, Runs, People | feed |
| `nav.js` | the nav: channels, topics, direct messages, their trailing marks and counts | none this sprint |
| `channels.js` | New, from the nav's "+": a raised surface under it that makes a channel (`POST channels/create`, which refuses a name that is taken) and lists the public channels I am not in (`GET streams`, asked when it opens), each joined with one press; the channels I join and leave, kept in the nav by the `subscription` event, a repository's as a repository; its sheet is `channels.css`; tested by `web/tests/armada/beta_channels.test.cjs` | channels |
| `header.js` | the header: the repository's facts | none this sprint |
| `band.js` | the band: what the run is doing, its clock, Stop | none this sprint |
| `views.js` | the views: README, Files, Changes | none this sprint |
| `home.js` | Home: what needs me | none this sprint |
| `later.js` | Later and Send when: a message put off (More › Later, the Later list on Home, what came back at Home's top), and a message of mine held until a time or a run's end (the menu on Send, its pending row at the conversation's foot); on the server's reminders, stars and scheduled messages, with `agent_run_id` for a run's end (`zerver/lib/armada_later.py`) | later |
| `palette.js` | Jump to (⌘K) | none this sprint |
| `lab.js` | the Lab: what is real, what is not, and its controls | none this sprint |
| `plugins.js` | plugins (`design/r9/plugins`): the installs and their widgets' documents (`armada_plugin`, `armada_widget` and `armada_widget_preference` events and their `/register` snapshot; my own view of each widget through `widget_view.js`), the nav's widget group at rest (a glyph, the label, one trailing thing in a cell as wide as the widget's sizer, by the nav's priority, the paused mark once a widget goes unheard for three times its cadence), the slots of the list a linked widget adds to its place's column (from the snapshot), a plugin bot's words on the plane, the Plugins place's keys and addresses, the place for its rows in Jump to; an update patches what is drawn in place; its sheet is `plugins.css`; tested by `web/tests/armada/beta_plugins.test.cjs` | plugins |
| `widget_view.js` | pure: a person's own view of a widget (ROUND2.md, section G), from the team's document and my preference: my figure at rest, the rows my facets keep, the alert that marks my nav, my label, in my nav and my column or not, my order; node tests in `web/tests/armada/beta_plugin_view.test.cjs` | plugins |
| `paste_guard.js` | pure: whether words hold something shaped like a key (ROUND2.md, decision 4), so that Onix's field in a widget's sheet and a plugin's plain settings offer to put it in the plugin's secrets first; loaded with the plugin chunk; node tests in `web/tests/armada/beta_paste_guard.test.cjs` | plugins |
| `plugin_view.js` | a chunk (`chunks.js`): the rows of a linked list in the column, the plugins' commands in Jump to, and what a widget opens: the sheet beside the nav, the "+" menu, Edit (my own view as controls, and asking Onix to change the widget) and a row's menu, Make a widget…, the paste guard, an item that borrows the column, the Plugins place and a plugin's page (its settings from the manifest's schema, secrets written and never read back, links, where it shows, tools for Onix, Remove and Undo); the other blocks of a document are drawn by `artifact_view.js`; tested in `beta_plugins.test.cjs` | plugins |
| `pages.js` | personal pages (contract C5): a person's page, a post, my editor; their places (`page:`, `post:`, `edit:`), the column's list of pages, their rows in Jump to, the `armada_post` event | pages |

Plugins are `plugins.js`, with its chunk `plugin_view.js`, its sheet `plugins.css` and its pane (`#plugins`) in the template.
Its place kind, `plugins`, reaches the shared modules as pages do: `routing.js` (`pluginPlaceFromKey`, `pluginHash`,
`pluginPlaceFromHash`, `openPlugins`, and `closeWidgetViews` on leaving a place), `header.js` (`pluginsHead`), `column.js`
(`pluginsSideHTML`, and `pluginSectionsHTML` for a linked list after Changes), `palette.js` (`pluginItems`); a plugin's bot
speaks through `faces.js` (`pluginVoice`, `isMachine`), which `parts.js`, `feed.js` and `events.js` read; `host.js` wires it
(`wirePlugins`) and its two events, also in `register()`'s `event_types`.

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
- in `tools/armada-checks/checks/size.mjs`, the beta's budget and its `beta-*` assertions, with their words in
  `tools/armada-checks/README.md` (the `size` row and "Size budgets"): give them to the entry that serves `/` instead

`git grep --untracked -n -e "INTERIM BETA" -e armada-beta -e armada_beta -e armada/beta` finds every one of them.
