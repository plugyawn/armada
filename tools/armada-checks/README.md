# Armada checks

A repeatable measurement of the things the UI rebuild is judged by: no click
moves the layout, a keypress and a send cost nothing you can feel, startup is
lean, and nothing expensive runs while the page is idle.

The suite drives the running development server in headless Chromium, takes
measurements, and compares each with a threshold. It is **green** when every
assertion either passes or is listed in `known.json` as a failure we already
know about. Fixing one of those turns its line into `FIXED`, which makes the
suite red until the entry is removed from `known.json`. That is the ratchet:
progress is recorded by deleting lines from that file, and nothing that passes
today can quietly start failing.

## Running it

The dev server must be up (`http://localhost:9991`). From the host:

    limactl shell armada -- arm check                       # everything, about 10 minutes
    limactl shell armada -- arm check frame-stability       # one check (or several names)
    limactl shell armada -- arm check --list                # the checks and what they cover
    limactl shell armada -- arm check --json <path>         # write the report somewhere else
    limactl shell armada -- arm check --update-known        # promote FIXED entries (see below)

`arm check` runs `node tools/armada-checks/run.mjs` in the VM checkout with
`CHROME` pointing at Chromium. Outside the VM wrapper the same command works
anywhere the repository's `puppeteer` and a Chromium are available:
`CHROME=/path/to/chromium node tools/armada-checks/run.mjs`. Environment:
`ARMADA_BASE` (default `http://localhost:9991`), `ARMADA_USER` (default
`armada-checks@zulip.com`, the checks' own user, see below), `ARMADA_APP`
(default `/legacy/`, the old interface the checks pin, since `/` serves the
new one: the client under test, as a path on that server, so that
`ARMADA_APP=/beta/` points every check at the beta; views are hashes after
it), `ARMADA_HOST_CHECKOUT` (the checkout people edit, as the VM mounts it:
`known.json`, the reports and accepted pictures are written there too; the
VM's copy is overwritten from it), `ARMADA_SERVED_TREE` (the tree the server
runs from, default this checkout),
`ARMADA_CHECKS_SHOTS=<directory>` to get a screenshot of the page whenever a
scenario or a tour step gives up.

The checks log in as a user nobody else signs in as, so that no one
else's session spends its rate limit or posts into the pages it measures.
Create it once in the development realm, and again after a database reset:

    limactl shell armada -- arm manage armada_checks_user

It takes the role and the channels of `desdemona@zulip.com`, whom the
fixtures were written for (`--like <email>` names another), with
onboarding done, a direct message from King Hamlet and a rate limit no run
reaches (`zilencer/lib/armada_checks_user.py`). A run that cannot log in
ends with `ENV-ERROR`.

One run at a time: `run.mjs` and `lib/prove.mjs` take
`/tmp/armada-check.lock` themselves (`lib/lock.mjs`), by running again
under `flock`, and say so when they wait for another run. A run started
under the lock already (`flock /tmp/armada-check.lock arm check`, or
`run.mjs` started by the proof) sees the lock held by its parent and goes
on. `ARMADA_CHECKS_LOCK` names another lock file and
`ARMADA_CHECKS_LOCK_WAIT` how many seconds to wait (3600); a run that
gives up waiting ends with `ENV-ERROR` and exit status 3.

Output is one line per assertion:

    PASS       frame-stability/long.navbar.drift  0px <= 0.5px  (0,0 1440x40)
    KNOWN-FAIL frame-stability/short.topicBar.drift  71px <= 0.5px  (326.28,111.13 768.13x63.2 -> 326.28,40.13 768.13x63.2)
    FIXED      ...      passes, but known.json still lists it: remove the entry
    FAIL       ...      fails and is not in known.json: a regression, or a new assertion
    STALE      ...      known.json lists an assertion that no longer exists
    INFO       ...      a number recorded for the report; never gates anything
    ERROR      ...      the check itself crashed

    ENV-ERROR  ...      something other than the tree changed or was wrong; see below

The exit status is 0 only when there is no `FAIL`, `FIXED`, `STALE` or
`ERROR`, 1 when the suite is red, and 3 when the run is `ENV-ERROR`.
The report (`var/armada-checks/report.json` unless `--json` is given) holds
every assertion and measurement with its value, threshold and note.

### When a line says ENV-ERROR

A result must be a function of the tree and the recipe only. Before the run
and after every check, `lib/health.mjs` looks at the environment; any
breach turns the whole run into `ENV-ERROR`, which is neither red nor
green, rewrites no `known.json`, and is rerun:

- the server's `/health` does not answer 200;
- when the server runs on this machine, from `ARMADA_SERVED_TREE` (default:
  the checkout `run.mjs` is in, which `arm check` runs from): Django,
  Tornado or the queue processor is missing or a zombie, any of them
  restarted (a save makes them reload), the machine rebooted, less than
  2 GiB of disk or 512 MiB of memory is left, or a file of the served
  tree's source changed (its change time, which a sync moves too);
- in the checks' pages: a 429 or a 5xx the check did not answer itself, a
  lost event queue (Tornado restarts inside one process, so only its
  clients see it), or a message, edit, reaction or typing by another
  person reaching the checks' user.

The report holds the problems (`env_errors`) and both snapshots (`health`).
For a server elsewhere only `/health` and the pages are looked at.

### When a line says FIXED

Delete its entry from `known.json`, or run with `--update-known`, which
removes every entry that now passes, drops stale ones, and refreshes the
measured value of the ones that still fail. Do that in the same change that
fixed the behaviour, so the diff of `known.json` reads as the list of things
the change repaired.

### Known failures that must not get worse

Every `known.json` entry names the package that turns it to `FIXED`
(`owner`, from PLAN.md appendix A) and the worst value it may still reach
(`max`); `lib/known.mjs` says what an entry holds. Worse is measured from
the threshold: for `<=` and `== 0` `max` is a ceiling; for a flag that
must be 1 and measures 0 it is 0, which nothing can be worse than. Above
it the line is `FAIL` ("it got worse"): `console-clean/uncaught-exceptions`
known at 3 means a fourth exception anywhere on the tour turns the suite
red. The entries that had no `max` before 15 October took the worst value
seen in the runs of 13 to 15 October and a quarter; a flag or a count that
must reach a value (`== 1`, `== 6`) took today's value.

A known failure whose value can no longer be measured (the element it looks
at is gone or renamed) is also `FAIL`: it could otherwise never report
`FIXED`.

### known.json only improves

`tools/lint` runs `known-lint.mjs` (the `armada-known` linter), which fails
unless every entry is complete (an owner, a max, an audit without `TODO`,
a date) and the file improves on where the branch left `ui-overhaul`
(`ARMADA_RATCHET_BASE` names another base): an entry may be deleted, its
`max` lowered, its owner, audit and measured value changed; it may not be
added, have its threshold or unit changed, or have its `max` raised. A
commit between the merge base and `HEAD` whose message has a
`Ratchet-exception: <id>, <id>` trailer lets those ids change anyway; only
the orchestrator writes one. Change the file with `--update-known`, which
removes what is fixed and refreshes measured values; `--accept-failures`
writes new entries with `TODO` in their owner and audit, which the lint
refuses until a person fills them in.

### When a line says FAIL

Either the change under test broke something (fix it), or the assertion is
new. A new assertion that fails on today's code is added to `known.json` by
hand with the measured value and a pointer to the audit or commit that
explains it. `--accept-failures` writes those entries for you with a `TODO`
in the `audit` field; it exists for bootstrapping a new check, not for making
a regression go away.

## What is measured

All checks run as `armada-checks@zulip.com` at 1440x900, device scale 1,
each in its own page with a clean `localStorage`. The notification banner a fresh
profile shows is closed first, so vertical positions are those of a settled
session. Fixture topics are the ones in `var/armada-overhaul/SEED.md`
(channel `click`: `option-groups` is the short worktree topic, `demo-long-topic`
the long one).

| Check                   | What it does                                                                                                                                                                                                                                                                                                                                                                                                     | What it asserts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `frame-stability`       | Chat -> README -> Files -> Chat on a short topic and on a long one scrolled to its middle, recording the box of navbar, both sidebars, topic bar, tab strip and compose on every frame. Then Files -> Inbox and Files -> Recent.                                                                                                                                                                                 | No box changes by more than 0.5px on any frame; bar and tabs never disappear; the document neither scrolls nor changes height; the feed's scroll position and a marked row are exactly where they were on return (also from 1px below the top); after leaving for Inbox or Recent no workspace pane is visible, the view sits where it normally does and the document is no taller. Since F5 also: the frame's rectangles against SPEC-DESIGN appendix B at 1440, 1100 and 1920 on a worktree, a channel, a direct message and Home (the old composer closed); Files folding the column; a window drag with no script writing the composition; the header's scroll edge; drag regions; the inlined sprite; the first paint without script.                                                                         |
| `topic-entry-stability` | Inbox -> worktree topic for the first time, again as a revisit, then topic -> topic, with 100 ms of network latency, recording every frame.                                                                                                                                                                                                                                                                      | The frame columns keep their left edge, top and width; topic bar, tab strip and rail card do not change after the first frame they appear in; tabs and rail card are present in the bar's first frame; the first message does not move after it appears.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `sidebar-stability`     | In the left sidebar: let a repository channel's topic list paint; click the channel below the open one, then one above; go Inbox -> Recent -> back; collapse and expand a channel section and Direct messages; type in the filter and clear it.                                                                                                                                                                  | Topic text does not shift when worktree icons arrive; the clicked row does not move and is still under the pointer; rows above a change never move; view switches move no row above the open channel and everything returns; a collapse/expand round trip and a typed-then-cleared filter leave every row where it was.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `column-stability`      | On a worktree, a channel, a direct message and Home at 1920, 1440 and 1100: opens the column's host as a sheet with More, closes it with Esc, presses the column's heading. Replaced `sidebar-toggle-stability` in F5.                                                                                                                                                                                           | The header, the views, the conversation, Home's pane, the band, the field's track, More and the nav's rows stay within 0.5px; the host shows its place's sections at rest; the sheet opens and Esc closes it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `typing-cost`           | Types 200 characters at 20 ms intervals into the open composer on the 77-message topic, once as prose and once with `@` `#` `:` and backtick tokens, under a Chromium trace.                                                                                                                                                                                                                                     | No layout is forced inside a key handler; no timer forces a layout; at most 1.5 layouts and 2 style recalculations per key; median handler time under 3 ms (tripwire); with tokens, no long task.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `send-cost`             | Three real sends to the run's own topic (below). Then sends whose POST is held and failed by the harness, so nothing reaches the server: a message mentioning the agent, and the four kinds of message that are not echoed today (image link, video link, slash command, `@**topic**`), and six sends in a row.                                                                                                  | The row is in the very next frame; at most one forced layout and 8 ms in the Return handler; the server echo neither replaces the row or its body nor moves or resizes it; compose keeps its height; every message is on screen before the server answers; an agent acknowledgement is on screen before the server answers; a sixth unacknowledged message still appears.                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `idle-cost`             | Sits for 20 seconds on a quiet topic, on `demo-live-run` and on `demo-wall-monitors`.                                                                                                                                                                                                                                                                                                                            | Per minute: layouts, style recalculations, main-thread time and application requests within the budget for that state (see below).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `topic-switch-cost`     | Five round trips between two topics that were both already visited.                                                                                                                                                                                                                                                                                                                                              | Median time until the topic is shown at most 16 ms; no message row is inserted into the DOM again; at most two requests per switch.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `startup`               | Three loads of the long topic with the HTTP cache disabled.                                                                                                                                                                                                                                                                                                                                                      | Longest task at most 100 ms; cumulative layout shift at most 0.01; no font file first requested more than 250 ms after first paint; at most 500 kB of images before the first row; first row within 2.4 s (tripwire).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `console-clean`         | A tour: every composer button, the help, main and personal menus, the Views menu, a topic menu, the runtime picker, work details, a message's menu, a thread, the three tabs, Inbox and Recent.                                                                                                                                                                                                                  | Every step could be driven; no console error; no uncaught exception; no response with an error status.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `feed-semantics`        | In the checks' own 120-message topic (`#sandbox > armada-pins`): the pointer keys, a wheel, Home and End; messages from other people and an edit handed to the page as events; unread flags set through the API; a held send; Back and Forward; a composer grown to sixteen lines.                                                                                                                               | Each key selects the message it should; page keys move a page; the selection follows a scroll by the 10% and 90% rule and is recentred at 1/2 and 1/7; a tall message is paged through; a scroll the application makes neither moves the selection nor marks anything read; a partial scroll marks only what was on screen; nothing is marked in an unfocused window, under an overlay, or in a `/near/` view before the reader scrolls back; the selected row keeps its place when messages are appended or prepended or one above is edited; the feed follows a new message only at the bottom or for my own send; older and newer history is fetched; Back and Forward restore the selected message and its offset; a channel view has one group per topic run; the composer does not cover the newest message. |
| `echo-semantics`        | Sends to the run's own topic (below) with the request held in the browser, then let through, refused or failed; a reload with text in the field; a real edit through the API; two messages in the topic of the live run, with the agent control off and on.                                                                                                                                                      | The row is in the first frame after Return; it keeps its place and stays one row when the server's id arrives; three rapid sends stay in order; a refused send stays with Retry; unsent text is back after a reload; the acknowledged row shows what a new page shows; an edit changes the row in place; the request made with the agent control on names the agent and the live topic, and the one made with it off does not.                                                                                                                                                                                                                                                                                                                                                                                     |
| `keyboard-semantics`    | Keys in the open composer, including a Return that commits an input-method candidate (CDP `Input.imeSetComposition`); Esc with a menu, the shortcut sheet and the reaction picker each over a composer holding a draft, with a thread open, with only the composer open and with nothing open; Tab and Shift-Tab through the composer; N and P; the twelve Mac conventions, with the page told it runs on a Mac. | Return sends, Shift-Return breaks the line, Return during composition does nothing; Esc closes the one thing on top and leaves the draft, the composer and the view alone (`esc.order`); today a further Esc closes the composer (`esc.closes-open-composer`, retired with the closed composer); with nothing open it stays where it is; Tab reaches every control after the field, lands on Send first, every stop draws something it did not show at rest, and Shift-Tab goes back the same way; N and P go to something unread; each Mac convention (`mac.*`).                                                                                                                                                                                                                                                  |
| `stream-cost`           | The two recorded runs in `fixtures/` (400 and 1,200 words a second) replayed into the live run's message through the application's own event code, at the times the server would have sent them; then a held send to the agent.                                                                                                                                                                                  | No layout forced while an event is applied; at most 1.1 layouts a frame, two per event applied, and one burst of DOM writes per event (one a frame is the budget and is recorded); no event takes three long tasks (150 ms) to apply, and how many take one (50 ms) is recorded; no word waits more than two frames; the newest words are on screen in every frame; first words within a frame; the final text is the server's; the send is acknowledged in the page by the element that will hold the answer.                                                                                                                                                                                                                                                                                                     |
| `parts-corpus`          | The Markdown test cases, six extra cases and every message of nine fixture topics, rendered by the client's Markdown engine and by the server, each split into bubbles and blocks by `lib/parts.mjs`.                                                                                                                                                                                                            | No case splits into different kinds of parts; no case differs at all.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `interaction-stability` | A walk through what a reader summons in a topic: hover, select, the message menu, the runtime picker, work details, reactions (mine, a second, removed, someone else's), someone typing, the composer opening, typing and closing, a thread opening and closing.                                                                                                                                                 | For each: the interaction visibly happened, no box of the frame (the composer included, except while it opens and closes) changes by more than 0.5px on any frame, and a message in the middle of the conversation does not move or resize.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `size`                  | Production builds of the `app` entry (the old interface) and of the `armada-beta` entry (the interface at `/`), each on its own into a temporary directory (`tools/armada/size/measure.mjs`, about twenty seconds for both), every emitted script and stylesheet minified with esbuild; the beta's also compressed with brotli at quality 11, as the server's pre-deploy hook does.                              | `app`: initial JavaScript at most 2,000 kB and initial CSS at most 160 kB, minified; no chunk fetched on first use over 800 kB. All three fail today and carry today's figure as a ceiling (`max`) in `known.json`. `armada-beta` (`beta-*`): initial JavaScript at most 100 kB brotli and 330 kB minified, initial CSS at most 25 kB brotli. All three pass.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `weight`                | The short worktree topic, settled and untouched.                                                                                                                                                                                                                                                                                                                                                                 | At most 900 elements in the document, 2,500 CSS rules (nested rules counted) and 40 jQuery handlers delegated on `body`. All three fail today and carry today's figure as a ceiling.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

`INFO` lines carry the rest: times that cannot gate on this machine, byte
counts of the development build, mutation counts, running animations.

## How thresholds were chosen

- **0.5px for stability.** Positions are read with `getBoundingClientRect`
  and rounded to 0.01px; sub-pixel differences of 0.1px exist between a
  sticky and a clamped position today, and half a pixel is below anything a
  person can see. "Stable" means every recorded frame, not only the last one:
  a box that jumps for one frame and returns fails.
- **Latency budgets** come from the proposed budget in
  `var/armada-overhaul/maps/perf-input.md`: keypress handler work at most
  1 ms at p95 with no forced layout and no layout from timers; a sent row
  painted in the next frame with at most one forced layout and 8 ms of
  handler; a revisit within 16 ms with nothing rendered again; cold start
  usable within 800 ms with no task over 100 ms and a layout shift of at most
  0.01.
- **Idle budgets** are stated in the check: a quiet topic does nothing (10
  layouts, 10 style recalculations, 0.5 s of main thread, 0 requests per
  minute); a live run or a wall may tick a clock once a second and poll
  slowly (120 layouts, 240 style recalculations, 1 s of main thread, 6
  requests per minute). Zulip's own heartbeat requests (`/json/events`,
  presence) are not counted.
- **Size budgets** for the `app` entry are the plan's (SPEC-ENGINEERING.md,
  section 8). The beta's are about 8% above its figures with the perf and
  features batch of 16 October 2026 (92 kB brotli and 302 kB minified
  JavaScript, 19 kB brotli CSS), rounded up to the next 5 kB, so that they
  pass today and growth past them is a deliberate raise. They are esbuild's
  figures: the deployed build, minified by terser, is a few percent smaller.
- **Where a millisecond budget cannot be verified here**, the count that
  causes the cost is asserted instead, and the time is either asserted
  against three times the budget as a tripwire (typing median, first row) or
  recorded as `INFO` (p95 per key, splash time).

## Noise, and how the suite stays deterministic

Everything runs against the unminified development build, in a VM, on a
machine other people and agents are using. Absolute times move by a factor of
two between runs. So:

- Prefer **counts and positions**: forced layouts, rows rendered, requests,
  frames, pixels. They are identical from run to run.
- For times, take the **median of repeated samples** (three sends, five round
  trips, three loads, 200 keys), and only assert a time when today's value is
  far from the threshold on one side.
- An assertion that passes in one run and fails in the next is a bug in the
  check. Either find what it depends on (shared state, a race with a fetch,
  an element outside the viewport) or turn it into `INFO` with the reason.
- Late content is made late on purpose: the entry checks add 100-150 ms of
  network latency, so "arrives after first paint" does not depend on how
  fast the local server answered. Correct code passes at any latency.
- Each check gets a fresh page and a cleared `localStorage`; the browser
  profile is new on every run. Checks must not depend on order.
- Tracing adds overhead to handler times; trace-derived milliseconds are
  comparable with each other, not with an untraced production build.

## Behaviour pins, and proving that they can fail

`feed-semantics`, `echo-semantics` and `keyboard-semantics` pin what the feed,
the local echo and the keyboard do today, so that the rebuild can be held to
it (`var/armada-overhaul/design/final/PLAN.md`, Appendix B, lists the
assertion for each behaviour). Three things keep them honest:

- **A value that could not be produced is a failure.** Every assertion has a
  precondition that makes it mean something (the feed did scroll by itself;
  the message would have been covered; the menu did open) and reports
  "not measurable" when it does not hold. An interaction that silently did
  nothing cannot pass.
- **Each has a counterpart that goes the other way** in the same run where
  one exists: a user's scroll does mark read where the application's own
  does not; focus returning does mark what the unfocused window did not.
- **Each can be broken on purpose.** `lib/mutate.mjs` lists edits to the
  application (Return sends during composition; a scripted scroll counts as
  the user's; the server's copy of a sent message is not matched to its
  echo; and two dozen more), each with the assertions it must turn red.
  With `ARMADA_CHECKS_MUTATE=<name>` the harness rewrites the development
  bundle inside its own browser as the scripts arrive (CDP `Fetch`), so no
  file and no other browser sees the change. Run them all with

        limactl shell armada -- arm sh "source ~/shot.env; node tools/armada-checks/lib/prove.mjs"

  (about 20 minutes; names as arguments run a few). It prints `PROVED` when
  the edit was applied and every listed assertion came out `FAIL`, and
  writes `var/armada-checks/prove.json`. `PROVED n/a` means that at least
  one of those assertions failed only as "not measurable": the edit broke
  the scenario around it, and the assertion has not been shown to fail on
  its own measure. `ARMADA_CHECKS_ONLY=<regex>` limits
  any run to the scenarios whose label matches.

The Mac conventions are pressed as chords with the command key. Today's
application drops those unless `navigator.platform` names a Mac, and the
checks run in a Linux Chromium, so the `mac` scenario of `keyboard-semantics`
overrides the platform (CDP `Emulation.setUserAgentOverride`, `MacIntel`)
before the application loads and reports "not measurable" if the page does
not say Mac. The browser itself is still Linux's: what Chrome on a Mac does
with a chord before the page sees it (Cmd-W closes the tab, Cmd-[ goes back,
the field's own text keys) is not exercised here.

When the rebuild moves a behaviour to new code, the mutation's `find` no
longer matches and `prove.mjs` says so: write the equivalent edit for the new
code in the same change. An assertion without a mutation must say in
`var/armada-overhaul/build/F0.md` (or its successor) how else it was shown to
fail.

### The checks' own topic

`#sandbox > armada-pins` holds 120 messages written by `lib/fixtures.mjs` the
first time a check needs them (index 30 is sixty paragraphs long). Nobody else
reads it, so the checks can set its unread flags through the API. Messages
"from other people", edits, reactions and typing are `message`,
`update_message`, `reaction` and `typing` events handed to the page through
`zulip_test.inject_events`, which is the function a `/json/events` response
goes through; they exist only in that page. If someone posts in the topic the
checks stop with a message saying which ids to move elsewhere.

### Requests the check answers itself

`ctx.route(accepts, {answer})` pauses matching requests in the browser.
Without `answer` they are held until `release()`, `release_one()`, `fail()`
or `respond(json, status)`; with it each is answered at once and never
reaches the server. `ctx.hold_sends()` does it for sent messages. Every send
that must not be posted (to the agent, in the fixture topics, in the Mac
walk) ends in `fail()`; "mark as read" requests for messages that exist only
in the page, uploads and stop requests are answered by the check.

`ctx.open(hash, {fresh: true})` loads a new document. Without `fresh`, going
from one view to another only changes the fragment and the page keeps its
state; the pins start every scenario fresh.

### Recorded runs

`fixtures/stream-400.json` and `stream-1200.json` hold the `update_message`
events of one answer as today's server sends them: the whole text so far,
rendered by the server, every 200 ms. `lib/record-stream.mjs` wrote them
from `fixtures/stream-source.md` and writes them again when the server's
rendering changes (run `prettier --write` on the result).
`fixtures/parts-corpus.json` says what `parts-corpus` compares.

## What the suite leaves behind

- Each run sends to a topic of its own,
  `#sandbox > armada-checks-<UTC date and time>-<process id>` (`PROBE` in
  `lib/selectors.mjs`), so that no topic grows from run to run. Until 15
  October every run sent to `#sandbox > armada-checks`; once it held a few
  hundred messages (it passed at 419 and failed from 479), every own send
  there re-rendered the old view and showed "Scroll down to view your
  message", and `send-cost` saw the composer change height
  (`var/armada-overhaul/build/F5-verify.md`, defect 3). The first check of
  the run that opens the topic writes one opening message there
  (`CheckContext.ensure_probe`); a run that opens no such check creates no
  topic.
- `send-cost` posts three short messages per run to that topic (and nowhere
  else). Every other send in that check is held in the browser and failed
  before it leaves, so nothing is posted to `click` and no agent run is
  ever started.
- `echo-semantics` posts four short messages per run to the same topic (one
  with a code block, three in a row) and edits the newest message there.
- `keyboard-semantics` opens that topic and types there; its one send is
  held and failed, so it posts nothing.
- `feed-semantics` and `keyboard-semantics` change the read flags of the 120
  messages in `#sandbox > armada-pins` and of the user's newest direct
  message, and set them back to read. The first run creates those 120
  messages.
- Typing checks send ordinary typing notifications for a few seconds.
- Nothing else is written: no settings change, and sidebar state lives in the
  throwaway browser profile.

## Adding a check

Create `checks/<name>.mjs` exporting `name`, `description`, `order` (position
in the run) and `async run(ctx)`:

```js
import {drift} from "../lib/stats.mjs";

export const name = "my-check";
export const order = 110;
export const description = "one line saying what is driven and what must hold";

export async function run(ctx) {
    await ctx.scenario("some-case", async () => {
        await ctx.open(ctx.topics.short);
        await ctx.start_recording(["navbar", "compose"]);
        await ctx.click("tabFiles");
        await ctx.settle();
        const frames = await ctx.stop_recording();
        ctx.assert("some-case.navbar.drift", drift(frames, "navbar")?.delta ?? null, "<=", 0.5, {
            unit: "px",
        });
        ctx.info("some-case.frames", frames.length);
    });
}
```

- `ctx.assert(id, value, operator, threshold, {unit, note})` records a gate;
  `ctx.info(id, value, {unit, note})` records a number. A `null` value fails:
  an assertion that could not be measured is not a pass. Put the evidence in
  `note` (the before and after boxes, the per-sample values).
- `ctx.scenario(label, fn)` isolates a part of a check: if it throws, one
  `<label>.scenario-completed` assertion fails and the other scenarios still
  run.
- `ctx.feed()` returns the selected message, where its row is, the rendered
  and loaded ids and the readable area; `ctx.inject(events)` hands events to
  the page; `ctx.api(method, path, params)` makes a request with the page's
  session; `ctx.wheel(delta)` is a scroll the user made.
- `ctx.open(hash)` loads a view from scratch; `ctx.navigate(hash)` changes the
  hash like a link does; `ctx.settle()` waits for the DOM to go quiet;
  `ctx.click(name)` is a real pointer click on a named element;
  `ctx.start_recording(names, probes)` / `ctx.stop_recording()` sample boxes
  on every frame; `ctx.metrics()` reads Chromium's layout, style and task
  counters; `lib/trace.mjs` counts layouts forced inside event handlers and
  timers; `lib/stats.mjs` has `drift`, `label_drift`, `median`, `quantile`.
- Elements are named in `lib/selectors.mjs`, never spelled out in a check.
  When the rebuild renames or moves something, put the new selector first in
  that element's list and the checks keep working. Fixture locations are in
  `TOPICS` in the same file.
- Run the new check three times. If it is green each time, add its failures
  to `known.json` with the audit that explains them, and add a row to the
  table above.

`tools/lint` (and so `arm lint` and `arm fmt`) skips files git does not track
yet. Until this directory is committed, lint it directly in the VM:
`limactl shell armada -- arm sh "node_modules/.bin/eslint --max-warnings=0 tools/armada-checks && node_modules/.bin/prettier --check tools/armada-checks"`.
ESLint (the repository's `eslint.config.js`) and Prettier are the linters that
apply to `.mjs` files under `tools/`.

## The harness's own tests

The logic that decides a result is tested like the product's pure code:

    limactl shell armada -- arm sh "node tools/armada-checks/selftest.mjs"

runs `test/*.test.mjs` with node's test runner and fails unless every
module listed in `FULLY_COVERED` in `selftest.mjs` (the ratchet in
`lib/classify.mjs`, the `known.json` lint, the lock, the health checks,
`lib/host.mjs`, `lib/stats.mjs`, `lib/mutate.mjs`) has 100% of its
lines, branches and functions covered. It needs no browser and no
server, and takes about a second. Every failure the health checks exist
for is planted there, before the run and during a check (in
`CheckContext`, through a stand-in for the browser's DevTools session),
and must end the run `ENV-ERROR` with status 3, over a red result as
well as a green one.

## Files

    run.mjs              runner: arguments, known.json, report, exit status
    selftest.mjs         the harness's own tests, at 100% coverage
    test/                those tests
    known.json           assertions that fail today, with value and explanation
    checks/*.mjs         one file per check
    fixtures/            recorded runs, their source text, the corpus list
    lib/browser.mjs      launch, dev login, viewport
    lib/classify.mjs     the ratchet: a run against known.json, and its rewrite
    lib/known.mjs        what a known.json entry holds; the rule that it only improves
    lib/known_lint.mjs   that rule against git (known-lint.mjs runs it)
    lib/lock.mjs         one run at a time, under /tmp/armada-check.lock
    lib/health.mjs       what makes a run ENV-ERROR
    lib/host.mjs         where files people keep are written (ARMADA_HOST_CHECKOUT)
    lib/fixtures.mjs     the checks' own topic; events for messages and edits
    lib/mutate.mjs       deliberate breakage of the application, by name
    lib/prove.mjs        runs every mutation; the pins must turn red
    lib/parts.mjs        reference split of a message into bubbles and blocks
    lib/record-stream.mjs  records the runs stream-cost replays
    lib/harness.mjs      the ctx object
    lib/page.mjs         helpers installed in the page as window.__ac
    lib/selectors.mjs    every selector and fixture URL
    lib/stats.mjs        medians, quantiles, drift of recorded boxes
    lib/trace.mjs        Chromium trace ->
