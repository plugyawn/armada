# CLAUDE.md - Guidelines for AI Contributions to Armada

This file provides guidance to Claude (and other AI coding assistants) for
contributing to this codebase. These guidelines are designed to produce
contributions that meet the same high standards we expect from human
contributors.

## This repository is Armada

Armada is a fork of Zulip: a Codex client attached to a team chat. A
channel is a git repository, a topic is a worktree with its own warm agent
runtime, and a reply steers the live turn. The product is **Armada**; its
agent is **Onix** (statement 23). The old code name is gone (statement
24, package R1): the server and the wire say `armada` or a plain noun
(`AgentRun`, `Repository`, `/json/armada/` routes, `ARMADA_*` settings,
the `agent_run` event), and a lint guard keeps the old name out.

The frontend is being rebuilt. This section says what the user has
decided, where the build is and how to work here. The rest of the file
is Zulip's own guidance and still holds: commit discipline, testing,
honesty about what was verified, when to pause. Where it speaks of
Zulip's existing UI, read it through this section, which wins where the
two disagree.

### What the user has decided

The brief is `var/armada-overhaul/BRIEF.md`; later statements override
earlier ones.

- Keep Armada's semantics: repositories as channels, worktrees as topics,
  agent runs with steering, queueing and stop, threads, artifacts,
  monitors, the README, Files and Changes views, the GitHub-like topic
  header, the checklist of a run, people talking to each other and to
  the agent.
- Lean and instant. No perceptible latency on a keypress or on send; no
  interaction moves the layout. These are budgets with numbers, and
  `arm check` measures them.
- One design language, Armada's own, written down in
  `docs/armada/design-language.md`: Apple's principles, not Apple's
  assets, and not Zulip's look. People talk in bubbles (statement 17).
  Onix's words are ambient, on the page with no bubble, and set in Söhne
  like everyone's: the user offered that as a "maybe", to be shown and
  judged (statement 20); the design follows it and F3 built it, and the
  brief records no verdict yet. People, Onix and chrome are set in Söhne,
  code in Geist Mono, with Hanken Grotesk as the open fallback behind
  Söhne. Söhne is licensed: its files live in the git-ignored
  `web/fonts/licensed/` and are never committed. There is no pixel
  typeface (Geist Pixel is gone); in the design, pixels are drawings:
  Onix's sigil and the marks of a run. No new code names a system
  typeface (a few old portico sheets and one test still do; the fork
  ledger counts them down).
- Motion and sound are part of the product, everywhere, and never cost
  responsiveness or distract.
- Targets: Electron is the main target ("main target would probably be
  electron"). In browsers, Chromium 132 and later and Safari 18 and
  later; Firefox best effort. English only, with every string still
  tagged for translation. Phones are not drawn: that is the plan's
  reading, not something the user said, so ask before deleting anything
  that only a phone needs.
- The cut list is approved: `docs/armada/cuts.md`. Each line is carried
  out by the package that owns it, by deleting the code, never by hiding
  it in CSS.
- Zulip's own UI is not off limits, and merging from upstream is given
  up for the frontend as a constraint. It stays as a cost to name: say
  what a deep change to a Zulip file gives up (upstream mergeability,
  tests that have to be rewritten) and what it buys, and make no deep
  change that buys nothing. `tools/armada/fork_ledger` records every
  Zulip file the fork changes.
- A hosted beta is wanted: the new interface usable by the team on a
  hosted server ahead of the full rebuild.
- The base model for new topics is `gpt-6-astra` on the ultrafast tier
  (the user's choice on 14 October; their "sol 6.1" is not in the Codex
  catalog). Effort defaults to medium; `gpt-6-sol` and `gpt-5.6-sol`
  stay selectable. See `ARMADA_WORKER_*` in `zproject/default_settings.py`.
- Maintainability for future agents is a goal in itself: less code, one
  obvious place for each thing.

### Still open

Do not state a guess about these; build on what the plan assumes and
say so.

- **Where the hosted beta runs.** The user chose "My Mac for now". This
  Mac's disk was 98% full on 14 October (18 GB free), too little for a
  second, production VM, so the user has been asked to free space, use
  the dev server as a stopgap, or take a VPS. Do not create a VM until
  that is answered.
- **Onix's ambient words** (statement 20, a "maybe"): built, with no
  verdict from the user recorded.
- **The design's five questions** (palette, the composer's button,
  threads in place, the column's four lists, the two-line header): the
  build follows the design lead's recommendations, listed in
  `docs/armada/design-language.md` ("Open with the user") and marked
  (P-5) to (P-9) where `architecture.md` and `cuts.md` build on them.
- **S-10 in the cut list** (`corporate/`) says "only if approved"; ask
  before carrying it out.

### Where the build is

The plan is `var/armada-overhaul/design/final3/PLAN.md`: 35 packages in
five stages. Each package's report is
`var/armada-overhaul/build/<id>.md`, with its verification beside it.
The work is committed on `ui-overhaul`: two checkpoints (`89fb6f54e4`,
14 October; `0e53081710`, 15 October), then the rename of the old code
name in five commits (package R1, `build/R1.md`). Built so far, the
Foundation packages:

- **F0, pins**: the behaviour checks in `tools/armada-checks/` (feed,
  echo, keyboard, stream, parts, interaction stability), the breakages
  that prove each can fail (`lib/mutate.mjs`, `lib/prove.mjs`), and a
  real DOM for tests (`web/tests/lib/real_dom.cjs`).
- **F1, toolchain**: `.tsx` with Preact; the lint rules for
  `web/src/armada/` in `eslint.config.js`, `stylelint.config.js` and
  `tools/linter_lib/custom_check.py`; the cascade layer (every old sheet
  is wrapped in `@layer zulip` by `web/zulip-layer-loader.ts`); the fork
  ledger; this guidance and `docs/armada/`.
- **F2, load fixes**: the field sizes itself in CSS, KaTeX and the emoji
  sheets load on use; the `size` and `weight` checks.
- **F3, tokens and fonts**: `web/src/armada/tokens/` (the token set, the
  layer order, the faces) and four generated palettes (Lapis the default;
  Tide, Citrine, Onyx), one setting (`ARMADA_PALETTE`) to change.
- **F4, kit**: `web/src/armada/kit/` (every component, drawn once and
  rendered by Preact or as plain DOM), the icon sprite, `motion/`,
  `sound/`, the showroom at `/devtools/armada/`, and the `kit`, `motion`
  and `design-lint` checks.

The frame (F5) and everything after it are not built: `frame/`, `feed/`,
`state/`, `api/`, `commands/` and each `surfaces/<name>/` hold only a
README. The screens people use are the interim beta below; the old
interface, set in the new faces, stays for what the beta cannot do yet.

An interim hosted beta is being built beside the plan, served at `/` to
signed-in people (and at `/beta/`; `web/src/armada/beta/`, served by
`zerver/views/armada_beta.py`; the old interface is at `/legacy/`, linked
from nowhere): the design's prototype run on the server's real data.
It is outside the rebuild's rules: eslint, stylelint, prettier and the
custom checks all ignore `web/src/armada/beta/`, so no rule in
`.claude/rules/` holds there, and it is deleted when the rebuild's
conversation lands.

### Where things live

- `web/src/armada/`: the rebuilt frontend. Every directory has a README
  of a few lines saying what belongs there. New frontend code goes here
  and nowhere else; the old `web/src/legacy_*.ts` modules, `web/styles/`
  and `web/templates/` are deleted surface by surface, not extended.
- `.claude/rules/`: the rules of the rebuild, each with the lint or
  check that enforces it, or a plain statement that nothing does.
  Most load only when you work on the files they govern.
- `docs/armada/`: `design-language.md`, `architecture.md`, `cuts.md`,
  and the generated `fork-ledger.md`.
- `tools/armada-checks/`: the gates (`README.md` there is the manual).
  `tools/armada/`: the fork ledger and the generators (palette, icons,
  sound reel, size).
- `var/armada-overhaul/` (not in git, present on the machine the rebuild
  is done on): the brief (`BRIEF.md`), the environment (`ENV.md`), the
  design and the plan (`design/final3/`), the build reports (`build/`).
  Read `BRIEF.md` and `ENV.md` first when they exist.

### How to work here

**Running things.** On the rebuild's machine the app runs inside a Lima
VM from a synced copy of this checkout; `var/armada-overhaul/ENV.md` has
the details. Edit files in this checkout with your normal tools and run
the tooling through the VM:

```bash
limactl shell armada -- arm lint <files...>    # all linters, TypeScript included
limactl shell armada -- arm fmt  <files...>    # eslint, prettier, stylelint autofix
limactl shell armada -- arm test <names...>    # node tests; armada/<name> for web/tests/armada/
limactl shell armada -- arm check [names...]   # the gates in tools/armada-checks/
limactl shell armada -- arm shot <scenario>    # screenshots of the running app
```

`tools/lint` here also lints files git does not track yet, because the
rebuild's work is uncommitted between the orchestrator's commits.

**The gates.** A change is finished when `arm lint` passes on its files,
its node tests pass, and the `arm check` assertions it affects have the
status its task names: none may turn `FAIL`, and a known failure it
repairs must turn `FIXED`. A frontend change names the check or test
that proves it; a server change names its backend test; a change to
documentation or guidance says that no check applies.

**Three prohibitions.**

- **No shared git state changes.** Several agents work in this tree at
  once. Do not commit, stash, reset, check out, rebase or fetch unless
  your task says the tree is yours. The commit and rebase guidance
  below applies when it is.
- **No edits inside the VM copy** (`/srv/armada/zulip`): the sync
  overwrites them. Do not start, stop or restart the dev server unless
  your task says so.
- **No new dependency without approval.** Installing or upgrading a
  package, and running `./tools/provision`, need the user's approval,
  one request at a time. Do it only when your task says the user
  approved that step; otherwise stop short of it and report what is
  needed and why.

**Upstream Zulip.** Do not rebase or merge this branch onto upstream
Zulip. A fix from upstream, a security fix above all, may be brought in
deliberately: as a cherry-pick in a task that says so, on a tree that is
yours, with the upstream commit named in the commit message and the
files it touches in the fork ledger.

## Philosophy

Zulip is a team chat application used by thousands of organizations,
built to last for many years. It is developed by a vibrant open-source
community, with maintainers who have consistently emphasized **high
standards for codebase readability, code review, commit discipline,
debuggability, automated testing, tooling, documentation, and all the
other subtle details that together determine whether software is easy
to understand, operate, and modify**.

Zulip's engineering strategy is to **"move quickly without breaking
things"**. This is possible because the project has invested years in
testing, tooling, code structure, documentation, and development
practices that catch bugs systematically rather than relying on
individual vigilance. Maintainers spend most of their review time on
product decisions and code structure/readability, not on chasing
correctness issues — because the process is designed to prevent them.

This means Zulip's coding philosophy is to **focus relentlessly on
making the codebase easy to understand and difficult to make dangerous
mistakes**. This applies equally to AI-generated contributions. Every
change should make the codebase more maintainable and easier to read.

### No detail is too small

Zulip holds itself to a high bar for polish because users depend on
this software daily, and because the project is built to last for
decades. There is no category of "minor issue" that is acceptable
to ship — if something is broken in any context where a user would
encounter it, it must be fixed before merging. The project's
extensive investment in testing, tooling, and review processes exists
precisely so that these issues get caught and fixed, not so that they
can be classified as low-priority and deferred.

This philosophy extends to every aspect of the product:

- **Visual precision matters.** Alignment, spacing, colors, and font
  sizes must follow the design language (`docs/armada/design-language.md`)
  and the kit's components, not the old Zulip UI around them. When making
  CSS changes, you must demonstrate with pixel-precise before/after
  comparisons that there are no unintended side effects.
- **Every state matters.** UI must look correct in all its states:
  hover, active, disabled, focused, selected, empty, overflowing.
  Changes that could plausibly affect colors, contrast, or
  theme-dependent imagery must work in both light and dark themes;
  changes whose effect can't reasonably vary with theme (pure
  geometry/typography — `font-size`, `line-height`, `margin`,
  `padding`, `display`, `font-weight`, etc.) only need a single
  theme verified.
- **Every window size matters.** UI must look good from wide desktop
  (1920px) down to 1100px, the narrowest window the design draws; below
  1040px the nav becomes a sheet. Electron is the main target; phones
  are not drawn (see the top of this file).
- **Every string is tagged.** Armada ships in English only, and every
  user-visible string is still tagged for translation so that languages
  can return. Where the design gives a cell room for a longer string,
  keep that room.
- **Every interaction path matters.** Keyboard navigation, screen
  readers, permission levels, feature interactions (banners
  overlapping, resolved topics, muted messages), and edge cases in
  data (empty lists, very long names, single items vs. many) must all
  be considered.

The right attitude is: "What could go wrong, and how do I verify that
it doesn't?" not "It looks fine to me." **What isn't tested probably
doesn't work** — this applies to visual changes just as much as to
backend logic.

### Understand before coding

Before writing any code, you must understand:

1. What the existing code does and why, including the relevant help center or
   developer-facing documentation.
2. What problem you're solving, in its full scope.
3. Why your approach is the right solution, and available alternatives.
4. How you will verify that your work is correct, and avoid regressions
   that are plausible for the type of work you're doing.

The answer to "Why is X an improvement?" should never be "I'm not sure."

## Workflow

Follow this workflow for every task: **understand → propose → implement → verify**.

### 1. Understand Before Coding

Before making any changes:

```bash
# Read relevant documentation
cat docs/*/<relevant-area>.md
cat starlight_help/src/content/docs/<topic>.md
cat api_docs/<topic>.md and read the relevant part of zerver/openapi/zulip.yaml

# Look at existing code patterns
git grep "similar_function_name"
git log --oneline -20 -- path/to/file.py

# Check for related issues on GitHub
```

Always show existing similar code and explain how it works before proposing
changes.

### 2. Propose an Approach

Before writing code, explain the plan:

- Explain your understanding of the problem and all relevant design decisions
- What changes are needed and why
- How the changes fit with existing patterns
- What could break and how to prevent regressions

### 3. Implement in Minimal, Coherent Commits

Structure changes as clean commits:

- Backend and API changes (with tests and API doc changes documented
  fully using our double-entry changelog system). When starting an API
  change, reread `docs/documentation/api.md` to review the process for
  documenting an API change. Run `tools/create-api-changelog`; it
  generates an empty `api_docs/unmerged.d/ZF-XXXXXX.md` file (where
  `XXXXXX` is a random hex string the tool picks for you) and stages
  it for you. Document the changes in that file with the header
  `**Feature level ZF-XXXXXX**` and an unordered list (`*` bullets)
  of the additions or changes, formatted to match `api_docs/changelog.md`.
  In the OpenAPI yaml (`zerver/openapi/zulip.yaml`), reference the same
  filename stem in **Changes** notes, e.g.,
  `**Changes**: New in Zulip 12.0 (feature level ZF-XXXXXX).` The merge
  process replaces the placeholder with the final feature level.
  Never update `API_FEATURE_LEVEL` manually.
- Frontend UI changes (with tests and user-facing documentation
  updates). Remember to plan to use your visual test skill to check
  your work whenever you change web app code (HTML, CSS, JS).

Each commit should be self-contained, highly readable and reviewable
using `git show --color-moved`, and pass lint/tests independently. If
extracting new files or moving code, always do that in a separate
commit from other changes.

### 4. Verify Before Finalizing

Run tests before making a commit. Always manage your time by running
specific test collections, not the entire test suite:

```bash
# Includes mypy and typescript checkers
./tools/lint path/to/changed/files.py
./tools/test-backend zerver.tests.test_relevant_module
```

## Before You Start

### Read the Relevant Documentation

Zulip has over 185,000 words of developer documentation. Before working on any area:

- Read documentation from docs/, starlight_help/src/content/docs/, and api_docs/.
- Read existing code in the area you're modifying.
- Use `git grep` to find similar patterns in the codebase and read those.

### Understand the Code Style

- **Be consistent with existing code.** Look at surrounding code and follow
  the same patterns, as this is a thoughtfully crafted codebase.
- **Use clear, greppable names** for functions, arguments, variables, and
  tests. Future developers will `git grep` for relevant terms when
  researching a problem, so names should communicate purpose clearly.
- Keep everything well factored for maintainability. Avoid duplicating
  code, especially where access control or subtle correctness is involved.
- Run `./tools/lint` to catch style issues before committing, including mypy issues.
- JavaScript/TypeScript code must use `const` or `let`, never `var`.
- Avoid lodash in favor of modern ECMAScript primitives where available,
  keeping in mind our browserlist.
- Prefer writing code that is readable without explanation over heavily
  commented code using clever tricks. Comments should explain "why" when
  the reason isn't obvious, not narrate "what" the code does.
- Use `em` units instead of `px` for computed CSS values that need to
  scale with font size. Pixel approximations break at different zoom
  levels and font-size settings. Under `web/src/armada/` no length is
  written at all: every value is a token (`.claude/rules/design-language.md`).
- Comments should have a line to themself except for CSS px math.
- **Review CSS for redundant rules.** After writing CSS, review the
  full set of rules affecting the same elements. Look for rules that
  are immediately overridden by a more specific selector, duplicated
  selector lists, or cases where scoping (e.g., `:not()`) would
  eliminate the need for an override.
- **Check CSS change scope.** When modifying CSS, always check what
  other pages or components use the same selectors, files, and
  classes. Use `git grep` on class names and check webpack bundle
  entries to understand which pages load the file. Prefer scoped
  overrides (e.g., `.parent .target`) over modifying shared rules,
  to avoid unintended changes to other parts of the app.

See: https://zulip.readthedocs.io/en/latest/contributing/code-style.html

## Commit Discipline

Zulip follows the Git project's practice of **"Each commit is a minimal
coherent idea."** This is non-negotiable.

### Each Commit Must:

1. **Be coherent**: Implement one logical change completely and atomically.
2. **Pass tests**: Include test updates in the same commit as code changes.
3. **Not make Zulip worse**: Work is ordered so no commit has regressions.
4. **Be safe to deploy individually**: Or explain in detail why not.
5. **Be minimal** and **reviewable**: Don't combine moving code with changing
   it in the same commit; make liberal use of small prep commits for
   no-op refactoring that are easy to verify.

### Never:

- Mix multiple separable changes in a single commit.
- Create a commit that "fixes" a mistake from an earlier commit in the same PR;
  always edit Git to fix the original commit.
- Add content in one commit only to remove or move it in the next;
  plan upfront what belongs where and do it right the first time.
- Include debugging code, commented-out code, or temporary TODOs.
- Leave commits that break if a later commit in the PR is dropped.
  When a commit is flagged as potentially droppable, verify all
  earlier commits work correctly without it.

### Commit Message Format

```
subsystem: Summary in 72 characters or less.

The body explains why and how. Include context that helps reviewers
and future developers understand your reasoning, analysis, and
verification of the work above and beyond CI, without repeating
details already well presented in the commit metadata (filenames,
etc.). Explain what the change accomplishes and why it won't break
things one might worry about.

Line-wrap at 68-70 characters, except URLs and verbatim content
(error messages, etc.).

Fixes #123.
```

**Commit summary format:**

- Before the colon is a lower-case brief gesture at subsystem (ex: "nginx" config) or
  feature (ex: "compose" for the compose box) being modified.
- Use a period at the end of the summary
- Example: `compose: Fix cursor position after emoji insertion.`
- Example: `nginx: Refactor immutable cache headers.`
- Bad examples: `Fix bug`, `Update code`, `gather_subscriptions was broken`

**Linking issues:**

- `Fixes #123.` - Automatically closes the issue
- `Fixes part of #123.` - Does not close (for partial fixes)
- In a multi-commit PR, use `Fixes part of #123.` in earlier commits
  and `Fixes #123.` in the final commit.
- Never: `Partially fixes #123.` (GitHub ignores "partially")

### Rebasing Commits (Non-Interactive)

Since `git rebase -i` requires an interactive editor, use
`GIT_SEQUENCE_EDITOR` to supply the todo list via a script:

1. **Updating the HEAD commit:** If the commit you need to modify is
   already at HEAD, just use `git commit --amend` directly. The
   fixup+rebase workflow below is only needed for non-HEAD commits.

2. **Squashing fixups into existing commits:** Create fixup commits with
   `git commit --fixup=<target-hash>`, then write a shell script that
   outputs the desired todo (with `pick` and `fixup` lines in order)
   and run:

   ```bash
   GIT_SEQUENCE_EDITOR=/path/to/todo-script.sh git rebase -i <base>
   ```

   Note: `--autosquash` alone without `-i` does **not** reorder or
   squash anything.

3. **Rewording commit messages:** Use `git format-patch` to export
   commits as patch files, edit the message headers in the patch
   files, then reapply:

   ```bash
   git format-patch <base> -o /tmp/patches/
   # Edit the commit message in each /tmp/patches/000N-*.patch file
   # (the message is between the Subject: line and the --- line)
   git reset --hard <base>
   git am /tmp/patches/*.patch
   ```

## Testing Requirements

Zulip server takes pride in its ~98% test coverage. All server changes
must include nice tests that follow our testing philosophy.

### Before Submitting:

```bash
./tools/test-js-with-node       # JavaScript tests; full suite fast enough
./tools/lint                    # Run all linters
./tools/test-backend            # Python tests
```

A common failure mode is failing to have test coverage for error
conditions that require coverage (note `tools/coveragerc` excludes
asserts). Run `test-backend --coverage FooTest` and check the coverage
data to confirm that the new lines you added are in fact run by the
tests.

### Testing Philosophy:

- Write end-to-end tests when possible verifying what's important, not
  internal APIs.
- Tests must work offline. Use fixtures (in `zerver/tests/fixtures`) for
  external service testing and `responses` for simpler things.
- Use time_machine and similar libraries to mock time.
- Read `zerver/tests/test_example.py` for patterns.
- A good failing test before implementing is good practice so your
  test and code can jointly verify each other.
- Remember to always assert state is correctly updated, not just "success".

### For Webhooks:

```bash
./tools/test-backend zerver/webhooks/<integration>
```

### Manual Testing for UI Changes

If a PR makes frontend changes, manually verify the affected UI. This
catches issues that automated tests miss. **Treat this checklist as
blocking, not advisory** — every applicable item must be verified
before the change is ready.

**Visual appearance:**

- Is the new UI consistent with the kit's components (fonts, colors,
  sizes)? Find the closest component in the showroom (`/devtools/armada/`)
  and the matching still of the design, and compare carefully.
- Is alignment correct, both vertically and horizontally? Measure
  programmatically with `getBoundingClientRect()` when in doubt —
  don't eyeball it.
- Do clickable elements have hover behavior consistent with similar UI?
- If elements can be disabled, does the disabled state look right?
- Did the change accidentally affect other parts of the UI? Use
  `git grep` to check if modified CSS is used elsewhere. CSS changes
  are notorious for unintended consequences — check every page and
  component that shares the selectors you modified.
- Check all of the above in both light and dark themes when the
  change could plausibly affect colors, contrast, or theme-dependent
  imagery. Pure geometry/typography changes (`font-size`,
  `line-height`, `margin`, `padding`, `display`, `font-weight`,
  etc.) don't need a separate dark-theme pass —
  `web/styles/dark_theme.css` and the palette files in
  `web/src/armada/tokens/` only set colors, so a single theme suffices
  for theme-invariant changes.

**Responsiveness and internationalization:**

- Does the UI look good at different window sizes? Check 1920px, 1440px
  and 1100px.
- Is every user-visible string tagged for translation? Armada ships in
  English only, but the strings stay tagged.

**Functionality:**

- Are live updates working as expected?
- Is keyboard navigation, including tabbing to interactive elements, working?
- If the feature affects the message view, try different narrows: topic,
  channel, direct messages (and Combined feed until its cut, K-19,
  lands).
- If the feature affects the compose box, test both channel messages and
  direct messages, and both ways of resizing.
- If the feature requires elevated permissions, test as both a user who
  has permissions and one who does not.
- Think about feature interactions: could banners overlap? What about
  resolved/unresolved topics? Collapsed or muted messages?
- Think about edge cases in data: empty lists, very long names, single
  items vs. hundreds, special characters in strings.

### Visual Tests: Verifying Alignment

When using `arm shot` (the `visual-test` skill) to verify visual
alignment, do not rely on eyeballing screenshots — especially small
full-page ones. Instead:

- Use an `eval` step with `getBoundingClientRect()` to measure
  actual pixel positions of the elements you need aligned; it prints
  as an `EVAL` line. Compare the numbers.
- Always take **both** a full-page screenshot and a zoomed clip of
  the area of interest.
- For zoomed clips, calculate the clip region from non-fixed elements;
  fixed/sticky elements may report bounding-box positions that don't
  match their visual location on the page.
- Be aware that CSS nesting can scope styles to a specific parent
  (e.g., `.parent .my-class`) — reusing the same class name in a
  different context may not pick up the expected styles.
- To verify keyboard-focus styles, use real keyboard navigation
  (a `press` step); programmatic `.focus()` doesn't reliably
  trigger `:focus-visible` and may be overridden by view-level focus
  management.
- Focus rings drawn as `::before` / `::after` pseudo-elements aren't
  visible in `getComputedStyle` of the focused element — verify them
  in a screenshot, not via computed styles.
- For visual changes, produce before/after screenshot pairs from one
  scenario run twice: once before you make the change and once after
  it. Do not check out another branch to get the "before" (see the
  prohibitions at the top); if the change is already made, compare with
  the design's stills instead.

## Self-Review Checklist

Before finalizing, verify:

- [ ] The PR addresses all points described in the issue
- [ ] All relevant tests pass locally
- [ ] Code follows existing patterns in the codebase
- [ ] Names (functions, variables, tests) are clear and greppable
- [ ] Commit messages, comments, and PR description are well done.
- [ ] Each commit is a minimal coherent idea
- [ ] No debugging code or unnecessary comments remain
- [ ] Type annotations are complete and correct
- [ ] User-facing strings are tagged for translation
- [ ] User-facing error messages are clear and actionable
- [ ] No secrets or credentials are hardcoded
- [ ] Documentation is updated if behavior changes
- [ ] Refactoring is complete (`git grep` for remaining occurrences)
- [ ] Security audit of changes. Always check for XSS in UI changes
      and for incorrect access control in server changes.

Always output a recommend pull request summary+description that
follow's Zulip's guidelines once you finish preparing a series of
commits.

## Common Pitfalls

### Treating Known Issues as Acceptable

A common failure mode is discovering a problem during verification
and then noting it as a known limitation rather than fixing it. At
Zulip, there is no category of "known minor issue" that is acceptable
to ship. If it's broken in any state, size, theme, or language, it
needs to be fixed.

**Mitigation:** When you find any issue during verification, fix it
before presenting the work. If a fix would require a design decision,
raise it as a question rather than shipping the broken state.

### Overconfident Code Generation

You may generate code that looks correct but doesn't match Zulip patterns.

**Mitigation:** Always show existing similar code first before implementing.

### Incomplete Type Annotations

Python code must be fully typed for mypy.

**Mitigation:** Ensure all functions have complete type annotations. Run mypy
(perhaps via the linter) to verify.

### Missing Test Updates

Tests must be in the same commit as the code they test.

**Mitigation:** Include test updates in each commit. Show what tests need to
change.

### Verbose Commit Messages

Zulip commits are concise -- say everything that's important for a
reviewer to understand about the motivation for the work and changes,
and nothing more. Avoid wordiness and details obvious to someone who
is looking at the commit and its metadata (lists of filenames, etc).

**Mitigation:** Keep summary under 72 characters. Body should explain why,
not what.

### Mixing Concerns

Multiple changes in one commit makes review difficult.

**Mitigation:** Each commit should do exactly one thing. Plan
necessary refactoring and preparatory commits in advance of functional
changes. You can split into good commits after the fact, but it's much
faster and easier to just plan and write them well the first time.

## What Not To Do

### Code Quality:

- Don't use `Any` type annotations without comments justifying it.
- Don't use `cursor.execute()` with string formatting (SQL injection risk)
- Don't use `.extra()` in Django without careful review and commenting
- Don't use `onclick` attributes in HTML; use event delegation
- Don't access DOM APIs (`document.documentElement.style`, `$()`
  selectors for specific elements) without guarding for node test
  environments, where the DOM is mocked minimally. Check that the
  element exists before using it.
- Don't create N+1 query patterns:

  ```python
  # BAD
  for bar in bars:
      foo = Foo.objects.get(id=bar.foo_id)

  # GOOD
  foos = {f.id: f for f in Foo.objects.filter(id__in=[b.foo_id for b in bars])}
  ```

- In tests, don't assert on `assertLogs` output with
  `any(phrase in line for line in mock_log.output)`; pin the full line
  against `mock_log.output`, or a substring to a specific `mock_log.output[i]`.

### Process:

- Do not fetch from, merge or rebase on upstream Zulip. An upstream fix,
  a security fix above all, is brought in deliberately and named, as
  "Upstream Zulip" at the top of this file says.
- Don't make design or UX decisions silently. When a technical
  constraint forces a tradeoff, present the constraint and options
  to the user rather than picking one. Features are removed only as
  `docs/armada/cuts.md` lists them, by deleting their code and never by
  hiding them in CSS; say in your report which lines you carried out.
  For anything that is not on that list, or that the list marks as
  never to be cut, ask first.
- Don't submit code you haven't tested
- Don't skip becoming familiar with the code you're modifying
- Don't make claims about code behavior without verification, and
  cite your sources.
- Don't generate PR descriptions that just describe what files changed
- Always do a pre-mortem: Think about how to avoid a bug recurring,
  how it might break something that already works, or imagine under
  what circumstances your changes might need to be reverted.

## Pull Request Guidelines

### PR Description Should:

When opening a pull request, prefix the PR title with `[ai]` (e.g.,
`[ai] compose: Fix cursor position after emoji insertion.`). Armada's
pull requests go to this repository (`origin`, `plugyawn/armada`), never
to upstream Zulip; use the base branch your task names.

Output the PR description in a markdown code block so that formatting
(bold, headers, checkboxes, etc.) copy-pastes correctly into GitHub.

1. Start with a `Fixes: #...` line linking the issue being addressed.
2. Explain **why** the change is needed, not just what changed.
3. Describe how you tested the change, using checkbox format for the
   test plan (e.g., `- [x] ./tools/test-backend ...`).
4. Include screenshots for UI changes.
5. Link to relevant issues or discussions.
6. Call out any open questions, concerns, or decisions you are uncertain
   about, so they can be resolved during review.
7. Include the self-review checklist from
   `.github/pull_request_template.md` using checkbox format (`- [x]` /
   `- [ ]`), checking off all applicable items.

### PR Description Should Not:

- Regurgitate information visible from the diff
- Make claims you haven't double-checked
- Express more certainty than is justified given the evidence

## When to Pause and Discuss

Recommend pausing for discussion when:

- The approach involves security-sensitive code
- Database migrations are needed (See `docs/subsystems/schema-migrations.md`).
- The change affects many files (>10)
- Performance implications are unclear
- The feature design isn't fully specified
- The API or data model design isn't fully specified
- An API change may not be compatible (See `docs/processes/api-design.md`).
- Existing tests are failing for unclear reasons

## Task-Specific Approaches

### For Bug Fixes

1. Show the relevant code and explain what's happening
2. Brainstorm theories for how the bug might be possible
3. Analyze and propose a fix with a clear explanation
4. Write tests that would have caught this bug if possible
5. Format as a single commit following commit guidelines
6. Audit for whether the bug may exist elsewhere or might be
   re-introduced and propose appropriate changes to address if so.

### For New Features

1. Read the relevant documentation in docs/
2. Show similar existing features in the codebase
3. Propose an implementation approach before coding
4. Implement in minimal, coherent commits
5. Each commit must pass tests independently

### For Refactoring

1. Show the current implementation
2. Explain what makes it problematic
3. Propose the refactoring approach
4. Implement in commits that each leave the codebase working
5. No behavior changes unless explicitly discussed
6. Verify completeness: use `git grep` to find all occurrences and
   confirm nothing was missed

When removing a CSS dependency (e.g., Bootstrap), audit the full
property list of every rule, not just visually obvious properties like
colors and backgrounds. Subtle properties like `line-height`, `margin`,
`padding`, `text-decoration`, `font-weight`, and `border` are easy to
miss but cause visible regressions. Check inherited properties too —
e.g., a `body` rule's `line-height` or `margin` affects all descendants.

## Key Documentation Links

- Contributing guide: https://zulip.readthedocs.io/en/latest/contributing/contributing.html
- Code style: https://zulip.readthedocs.io/en/latest/contributing/code-style.html
- Commit discipline: https://zulip.readthedocs.io/en/latest/contributing/commit-discipline.html
- Testing overview: https://zulip.readthedocs.io/en/latest/testing/testing.html
- Backend tests: https://zulip.readthedocs.io/en/latest/testing/testing-with-django.html
- Code review: https://zulip.readthedocs.io/en/latest/contributing/code-reviewing.html
- mypy guide: https://zulip.readthedocs.io/en/latest/testing/mypy.html

## Repository Structure Quick Reference

```
zerver/           # Main Django app
  models/         # Database models
  views/          # API endpoints
  lib/            # Shared utilities
  tests/          # Backend tests
  webhooks/       # Integration webhooks
web/              # Frontend TypeScript/JavaScript
  src/            # Main frontend code
    armada/       # The rebuilt frontend (all new frontend code)
  styles/         # CSS
  templates/      # Frontend HTML
  tests/          # Frontend tests
templates/        # Jinja2/Handlebars templates
tools/            # Development and testing scripts
  armada/         # Fork ledger and generators
  armada-checks/  # The gates run by `arm check`
docs/             # ReadTheDocs documentation source
  armada/         # Design language, architecture, cuts, fork ledger
```

## Help Center Documentation

Zulip's help center (`starlight_help/`) documents Zulip, not Armada, and
the approved cut list removes it (S-02 in `docs/armada/cuts.md`). Do not
update it for an Armada change. When a change alters what Armada does,
update `docs/armada/` instead.

## Zulip Chat Links

When you encounter a Zulip narrow URL (e.g., from `chat.zulip.org` in a
GitHub issue, PR, or user message), use the `/fetch-zulip-messages` skill
to read the conversation. Do not use `WebFetch` — it cannot access Zulip
message content.

## Common Commands

```bash
./tools/provision           # Set up development environment
./tools/run-dev             # Start development server
./tools/lint                # Run all linters
./tools/test-backend        # Run Python tests
./tools/test-js-with-node   # Run JavaScript tests
./tools/run-mypy            # Run type checker
git grep "pattern"          # Search codebase (use extensively!)
```

On the rebuild's machine these run inside the VM, through the `arm`
commands at the top of this file; do not start the dev server yourself.

If a tool complains that provision is outdated, do not use
`--skip-provision-check` to work around the error; the check exists
because tests and linters depend on provisioned dependencies being
current. `./tools/provision` installs packages, so it needs the user's
approval: run it only when your task says the user approved it;
otherwise report the complaint, with the version it names, and wait.
