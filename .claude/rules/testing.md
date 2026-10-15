# Testing

What isn't tested probably doesn't work. Three kinds of test, each for what
it can see. This file applies to every change.

- Pure code (stores, the API layer, motion maths, sound recipes, the command
  registry, `feed/parts.ts`) has node tests in `web/tests/armada/` and 100%
  statement coverage from its first file. Enforced by:
  `tools/test-js-with-node --coverage` (`ARMADA_FULLY_COVERED_GLOBS`).
- The harness's own logic (the ratchet, its statistics, the mutations) has
  tests in `tools/armada-checks/test/` at 100% of lines, branches and
  functions.
  Enforced by: `node tools/armada-checks/selftest.mjs` (`FULLY_COVERED`).
- Components are tested for behaviour with a real DOM
  (`web/tests/lib/real_dom.cjs`): press, type, read the result back. Never
  assert on markup, class names or CSS text. Enforced by: review; F0 deleted
  the last regex-on-source tests.
- Everything a person can feel (latency, layout stability, motion, startup,
  size) is tested on the running app. Enforced by:
  `limactl shell armada -- arm check [names]`; the manual is
  `tools/armada-checks/README.md`.
- `known.json` is a ratchet: a known failure you repair must report `FIXED`
  and nothing may report `FAIL`, `ERROR` or `STALE`. Enforced by: `arm check`
  (its exit status). Only Foundation packages and stage closes edit the
  file: that is the plan's rule 5, not a check.
- Counts gate; a time gates only at three times its budget. Run the checks
  your change names three times and require identical statuses.
  Enforced by: `arm check` thresholds; your report shows the three runs.
- An assertion must be able to fail: give it a precondition that reports
  "not measurable", and break it on purpose once. Enforced by:
  `tools/armada-checks/lib/prove.mjs`, run in the VM as its header says.
- A full `arm check` (about ten minutes) or the whole proof runs under
  `flock /tmp/armada-check.lock`, one at a time. Enforced by: convention;
  long runs have taken the dev server down once.
- A deleted module's tests are deleted in the same change, and its entry
  leaves `ARMADA_EXEMPT_FILES`. Enforced by the coverage run
  (`tools/test-js-with-node --coverage`): a listed file that is gone gets
  a note, not a failure; a listed file that is fully covered fails.
- Server changes include backend tests and keep coverage; run only your
  own modules, one at a time, in the VM and when your task allows it
  (`arm sh "flock /tmp/armada-backend.lock ./tools/test-backend <modules>"`).
  Enforced by: `./tools/test-backend --coverage`.
- Run `arm lint` on every file you changed, tracked or not, and
  `arm test <names>` (`armada/<name>` for `web/tests/armada/`).
  Enforced by: `tools/lint`, which also lists files git does not track.
- Visual changes are shown: `arm shot` before and after, read as images,
  beside the design's still. Enforced by: the `visual-test` skill; your
  report holds the paths.
- Report what you ran and what failed, with output. A check you could not
  run is said to be not run. Enforced by: nothing automatic.
