---
name: visual-test
description: "Visually verify UI changes on the running Armada app: screenshots with arm shot, measurements with arm check, components in the showroom. Use when you need to check layout, colors, positioning, motion or other visual aspects of a UI change."
---

# Visual Test

Armada's dev server runs inside a Lima VM and is already up at
`http://localhost:9991`. You do not start it, and you do not write a
Puppeteer test file: you write a small scenario and run it with `arm shot`,
which drives headless Chromium against the running app and saves
screenshots you read as images. Read `var/armada-overhaul/ENV.md` first; it
is the authority for the commands below.

Three tools, for three questions:

| Question                                | Tool                                          |
| --------------------------------------- | --------------------------------------------- |
| What does this screen look like now?    | `arm shot` (this file)                        |
| Did anything move, slow down or break?  | `arm check` (`tools/armada-checks/README.md`) |
| Is this component right in every state? | the showroom, `/devtools/armada/`             |

## 1. Write a scenario

Put a JSON file under `var/armada-overhaul/` (any subfolder; `var/` is not
in git). Prefix every screenshot name with your task's key so that agents
do not overwrite each other's files.

```json
{
    "viewport": [1440, 900],
    "scale": 2,
    "theme": "dark",
    "user": "desdemona@zulip.com",
    "out": "/Users/progyan/runix/zulip/var/armada-overhaul/build/<task>",
    "steps": [
        {"goto": "/#narrow/channel/22-click/topic/demo-live-run", "wait": 3000},
        {"waitFor": ".some-selector"},
        {"click": ".some-selector", "wait": 400},
        {"hover": ".some-selector"},
        {"type": "text", "sel": "#some-input"},
        {"press": "Escape"},
        {"eval": "(() => document.querySelector('.x').getBoundingClientRect().toJSON())()"},
        {"shot": "mykey-01-whole"},
        {"shot": "mykey-02-detail", "clip": [320, 88, 704, 400]},
        {"shot": "mykey-03-element", "el": ".some-selector"}
    ]
}
```

- `scale` is the device scale factor: 2 for anything a person will judge,
  1 when you only need numbers.
- `theme` is `light` (default) or `dark`. Check both when a change can
  affect colour; geometry needs one.
- `eval` runs in the page and prints its JSON result as an `EVAL` line.
  Page errors print as `PAGEERROR`; a step that cannot run prints
  `STEPFAIL`. Treat either as a failure of your change until shown
  otherwise.
- Sizes to cover: 1920, 1440 and 1100 wide. Electron is the main
  target; phones are not drawn.

## 2. Run it and read the result

```bash
limactl shell armada -- arm shot /Users/progyan/runix/zulip/var/armada-overhaul/build/<task>/scenario.json
```

Then use the Read tool on each PNG. Look at the whole picture first, then
at a clip of the area you changed.

## 3. Measure, do not eyeball

- Positions and sizes: `getBoundingClientRect()` in an `eval` step. Compare
  numbers with the design's table (`docs/armada/design-language.md`,
  "Frame and layout").
- Colours and type: `getComputedStyle()` in an `eval` step.
- Keyboard focus: use `press` steps (`Tab`); a programmatic `.focus()` does
  not show the ring.
- A fixed or sticky element can report a rectangle that is not where it
  is painted; compute a `clip` from elements in normal flow.
- The VM's Chromium has no macOS fonts and not this Mac's emoji. For a
  PNG that will be judged for type, colour or spacing, render a static
  page on the host with `var/armada-overhaul/bin/mac-mock` (see `ENV.md`).

## 4. Before and after

Write one scenario and run it twice: before you make the change and after
it, with a different `out` directory or name prefix. Do not check out
another branch to get the "before": the working tree is shared. If the
change is already made, compare with the design's stills instead.

For a change that must not alter anything (a refactor, a build change),
compare the two runs by pixel and by computed style, screen by screen; a
worked example is `var/armada-overhaul/build/F1/tour/` (`tour.mjs` takes
the tour, `compare.mjs` compares two tours).

## 5. Stability, latency and motion: `arm check`

A screenshot cannot show that a click moved the header by 3 px for one
frame, or that a keypress forced a layout. The checks can:

```bash
limactl shell armada -- arm check --list
limactl shell armada -- arm check frame-stability interaction-stability
limactl shell armada -- arm check typing-cost send-cost
```

Run the checks your change can affect three times and require identical
statuses. `FAIL` is a regression or a new assertion; `FIXED` means a known
failure now passes and its entry leaves `known.json` (only Foundation
packages and stage closes edit that file). A full run takes about ten
minutes; every run takes `/tmp/armada-check.lock` itself and waits for
another run to finish.

## 6. Components: the showroom

Every kit component is shown in every state (rest, hover, pressed,
focused, selected, disabled, empty, loading, error, overflowing) at
`/devtools/armada/`, in both themes, as Preact and as DOM builder side by
side. A component that is not there may not be used. `arm check kit`
compares the page with its baseline by pixel. The page takes
`?as=preact|dom|both`, `?theme=light|dark`, `?p=tide|citrine|onyx` (Lapis
is the default), `?motion=reduced` and `?section=<id>`.

## Fixtures

Channel `click` (stream id 22) is a real git repository. Its `demo-*`
topics show every agent-run state without calling a model:
`demo-completed-run`, `demo-live-run`, `demo-needs-input`,
`demo-queued-run`, `demo-failed-run`, `demo-cancelled-run`,
`demo-paused-topic`, `demo-artifacts`, `demo-wall-monitors`,
`demo-threads`, `demo-dirty-worktree`, `demo-long-topic`
(`var/armada-overhaul/SEED.md`). Reload them with
`limactl shell armada -- arm sh "ARMADA_TOPIC_WORKDIR_ROOT=/srv/armada/workspaces ./manage.py armada_demo_data"`
(about 40 s), never while another agent is taking screenshots. The
variable is the server's worktree root: without it, the command puts the
worktrees in the code's default root, which the server refuses. Do not
send messages to the agent to make a screenshot: a real run spends the
user's quota.

## Report

Say which screens you looked at, at which sizes and themes, and give the
paths of the PNGs. Report what you measured as numbers. If a screen could
not be reached or a step failed, say so; do not describe what you did not
see.
