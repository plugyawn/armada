---
orphan: true
---

# How a package reaches the team

One package of the plan is one issue, one branch, one worktree, one pull
request and one reviewable series of commits. Isolation replaces locks:
nobody works in another builder's tree, and machines check what reports
used to assert. This file is the workflow; `docs/armada/history.md` says
where it begins in git.

Some steps are not built yet. Each says so, with the work item of the
process review (`var/armada-overhaul/design/r6/process.md`, W1 to W8)
that builds it. Until then, do the step by hand as written.

## 1. Issue

Each package of the plan has an issue: the plan's package text, its owns
list (the paths it may change) and its acceptance criteria. A package is
claimed by assigning its issue and opening a draft pull request for it.
Never start a package whose issue is assigned or that already has a
draft pull request. (W8 creates the issues and the board; W6 checks the
claim before a builder starts.)

## 2. Branch

The branch is `pkg/<id>-<slug>` (for example `pkg/F6-feed`), cut from the
trunk. The trunk is `pear-main` until the user renames it `main`: that
rename and the trunk's protection are the user's to approve (BRIEF
statement 32, unanswered). Builders of the 15 October sprint use
`wt/<name>`.

## 3. Worktree

Each builder works in a git worktree of its own:

```bash
git worktree add var/wt/<name> -b pkg/<id>-<slug> pear-main
```

`git worktree add` refuses a branch checked out elsewhere, so a second
builder cannot take the same branch. Inside a worktree, do anything to
your own branch and nothing to any other branch, tree or tag.

Tools run in the VM against a mirror of the worktree:

```bash
limactl shell armada -- var/armada-overhaul/bin/armada-wt.sh <name> ./tools/lint <files>
limactl shell armada -- var/armada-overhaul/bin/armada-wt.sh <name> \
    flock /tmp/armada-backend.lock ./tools/test-backend <modules>
```

Backend tests share PostgreSQL's test databases with every other tree,
so they run one at a time under the lock. (W4 gives each builder a
runtime of its own, a throwaway server, and makes `arm` follow the
worktree, so the lock goes.)

## 4. Commits

The builder commits its own series as it goes, following CLAUDE.md
("Commit Discipline"): each commit a minimal coherent idea that passes
on its own, with a message that says why. A fix to an earlier commit of
the series is a `fixup!` commit while the work is in review and is
folded into its target before the series lands; CI refuses a series that
still has one. Push the branch at least every 30 minutes, so that an
interrupted builder resumes from it. (W6 automates the heartbeat and the
resume.)

## 5. Pull request

Open the pull request as a draft as soon as the first commit exists.
Its title starts with `[ai]`, and its body is the package's report: the
template (`.github/pull_request_template.md`) has every section, and each
stays, with "None." where it does not apply:

- **Package** and `Fixes: #<issue>`.
- **Why**, in words a person uses.
- **Acceptance**: one row per criterion of the package, with the command
  that checks it, its result, and its evidence (a CI job, an artifact, a
  picture).
- **Pictures**, before and after, beside the design's stills. Pictures
  that show licensed sprites or fonts go to the private repository.
- **Not done**, **Needs the user**, **For the team** (the release note)
  and **Plan changes** (a plan or contract changed rides in the same pull
  request).
- **Self-review**, the checklist.

(W5 adds a check that fails a body missing a section.)

## 6. CI

`.github/workflows/armada-ci.yml` runs on every pull request and every
push to the trunk. Branch protection requires one status, **Required
jobs**, which passes only when every job below passed:

- **Guard**: no licensed file is tracked, and no commit of the fork adds a
  credential (`tools/armada/ci/licensed-guard`,
  `tools/armada/ci/secrets-scan`).
- **Lint**: the commit messages of the pull request, with gitlint and no
  fixup commits (`tools/armada/ci/commit-lint <base> HEAD`); every other
  linter over the files it changes
  (`tools/armada/ci/lint-changed <base> HEAD`); mypy over the project
  (`tools/run-mypy`), which finds no error; the tools' own tests
  (`./tools/test-tools`).
- **Node tests**: `./tools/test-js-with-node --coverage`.
- **Backend tests**, in four shards: every test module the fork added or
  changed, and the tests of every production file it changed
  (`tools/armada/ci/backend-tests`, `--list` to see them, `--shard 2/4`
  for one shard).
- **Behaviour checks**: a placeholder. `arm check` needs a running
  server, so the checks run at integration on the preview until CI
  serves one of its own.

Each command runs the same on a builder's tree (in the VM, through the
helper above). Two of them hold what was wrong on the trunk when CI
began, so that the first run is green and nothing new gets in: a
backend test may fail only if `tools/armada/ci/backend-known-failures.txt`
lists it; and the files in `tools/armada/ci/lint-known-findings.txt`
are not linted. Both only shrink: CI says when a line can be removed,
a fix removes it in the same commit, and each list goes when it is
empty (W2). mypy had a third, a ceiling on its count of errors, until
the sprint brought the count to zero.

Zulip's own CI, which tests Zulip's distribution on five operating
systems, is gone, and so are the workflows that served Zulip's
organization (its chat notifications, labels, one-click apps and cloud
deploys). CodeQL still scans the trunk and its pull requests; the
production build and install suite runs on release tags.

Builders do not wait for CI: open the pull request, move on, and come
back if it fails. (W3's remainder: the per-commit job, the scope job
against the owns list, the hermetic behaviour checks and a prebuilt image
that keeps CI under 15 minutes.)

## 7. Review

A fresh agent reviews each pull request: a new worktree at the pull
request's head, none of the builder's context, and four lenses
(correctness, scope against the owns list, security, and feel, with the
pictures beside the stills). It posts a pull request review. The builder
answers with `fixup!` commits and folds them before the merge. A push
after the review asks for a new one. (W5 makes the review a required
status, `armada/review`, that a new push resets, and opens pull requests
from a GitHub App so that the user can approve them.)

## 8. Merge

The trunk takes rebased commits only: linear history, no merge commits,
no force-push. A pull request merges when CI and the review pass and it
has no `needs-user` label. (W1 commits the ruleset once the user approves
the trunk's protection; W5 turns on auto-merge.) A red trunk is reverted
first and fixed after.

## 9. What the user approves

The user is never on the merge path. They approve three things, when
they like:

- **Product questions** (P-n). Builders go on with the plan's marked
  assumption meanwhile.
- **The permission list**: dependencies, downloads, provisioning,
  migrations that rewrite data, history rewrites, secrets and settings.
  A pull request that needs one carries the `needs-user` label, which
  only the user's approval clears.
- **Releases**, below.

## 10. Release

The team's server runs only annotated tags named `beta-YYYY.MM.DD-N`, cut
from the trunk:

```bash
git tag -a beta-2026.10.16-1 -m "<the For the team lines since the last release>"
git push origin beta-2026.10.16-1
```

A tag runs the production build and install suite. Deploying it waits on
one approval from the user, in the GitHub Environment `beta`, with the
release notes and pictures attached. Until W7 builds that pipeline (the
tarball, a check that every setting the server is given is one it reads,
a backup, the user's approval, the upgrade, a smoke check against the
server, the notes posted to the team), a release is deployed by hand as
`docs/armada/deploy.md` says, and only from a tag.
