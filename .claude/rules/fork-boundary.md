# The fork boundary

Armada is a fork of Zulip (fork base `7813218321`). The branch is not
rebased on or merged with upstream; the data layer and the server outside
`pear` stay close enough to Zulip's that an upstream fix can still be
brought in. This file applies to every change. The one exception to the
rules of `web/src/armada/` is `web/src/armada/beta/`, the interim beta:
eslint, stylelint, prettier and the custom checks ignore it, and it is
deleted when the rebuild's conversation lands.

- New frontend code lives under `web/src/armada/`; do not add a module to
  `web/src/`, a sheet to `web/styles/` or a template to `web/templates/`.
  Enforced by: `arm lint` (the `fork-ledger` linter fails when a count of
  old modules, sheets or templates is above `fork_ledger.d/counts.json`).
- Every Zulip file a package changes or deletes has a line in
  `tools/armada/fork_ledger.d/<package>.txt`: `hooked`, `bridged:<package>`,
  `absorbed:<path>`, `deleted` or `kept`. Enforced by: `arm lint` (the same
  linter compares each file with its content when the rebuild began;
  `tools/armada/fork_ledger --suggest` prints the missing lines).
- Old modules reach the rebuild only through `web/src/armada/hooks/`, on one
  line marked `// armada:` (`ui_init.js` may also call `boot.ts`, which F5
  creates; bundle entry files are exempt). Enforced by: eslint
  `import/no-restricted-paths` ("Old modules reach the rebuild only
  through"); the ledger's `hooked` status requires the marker.
- Nothing under `web/src/armada/` imports jQuery, Handlebars, tippy.js,
  micromodal or a `.hbs` template. Enforced by: eslint
  `@typescript-eslint/no-restricted-imports` and `no-restricted-syntax`
  ("No jQuery under web/src/armada/").
- An old module that others still import becomes an adapter under
  `web/src/armada/bridge/`, listed in `bridge/README.md` with the package
  that deletes it. Enforced by: the ledger's `bridged:<package>` status;
  `docs/armada/fork-ledger.md` counts the bridges, which reach 0 at the
  final close.
- A feature is removed by deleting its files, tests included, as
  `docs/armada/cuts.md` lists it (the list is approved; each line belongs to
  the package it names); never hide it in CSS. The list's last section
  names what must never be cut. Enforced by: the ledger's `deleted` status
  (the file must be gone); the pins in `arm check`; hiding is review.
- Old stylesheets, the icon font's sheet, library sheets and the styles
  CodeMirror injects all sit in `@layer zulip`; never add an unlayered old
  rule to beat a new one. Enforced by: `web/zulip-layer-loader.ts` in the
  build (it fails if it can no longer wrap what CodeMirror injects).
- The old tests' exemption list only shrinks. Enforced by: `arm lint` (the
  `fork-ledger` linter holds its count in `counts.json`);
  `tools/test-js-with-node --coverage` (a fully covered file still listed in
  `ARMADA_EXEMPT_FILES` fails; a missing one is noted).
- Server code for Armada stays in the `pear` namespace until its own
  rename; do not rename models, routes or settings in passing.
  Enforced by: review; the rename is one planned step with migrations.
- Say what a deep change to a Zulip file gives up and what it buys. Do not
  fetch from, merge or rebase on upstream Zulip. An upstream fix, a security
  fix above all, comes in deliberately: a cherry-pick in a task that says
  so, the upstream commit named in the commit message. Enforced by: the
  ledger (every file it touches needs a line); otherwise nothing automatic.
