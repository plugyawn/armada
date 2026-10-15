<!-- Armada's pull request is the package's report (docs/armada/process.md).
Keep every heading; write "None." under one that does not apply. -->

## Package

<!-- The plan's package id and name, and the branch, for example "F6 · The feed, pkg/F6-feed". -->

Fixes: #

## Why

<!-- What a person gains, or what was wrong, in a few sentences. Not a list of files. -->

## Acceptance

<!-- One row per acceptance criterion of the package. "Command" is what anyone can
run again; "Evidence" links the CI job, the artifact or the picture that shows it. -->

| Criterion | Command | Result | Evidence |
| --------- | ------- | ------ | -------- |
|           |         |        |          |

## Pictures

<!-- Before and after, beside the design's still, for anything a person sees.
Pictures that show licensed sprites or fonts go to the private repository, linked here. -->

## Not done

<!-- What the package left undone, and why. -->

## Needs the user

<!-- Product questions (P-n) and permission-list items: dependencies, downloads,
provisioning, migrations that rewrite data, history rewrites, secrets, settings.
Each also puts the `needs-user` label on this pull request. -->

## For the team

<!-- One or two lines for the release notes, in words a teammate uses. -->

## Plan changes

<!-- Any change to the plan, a contract or a decision this package makes, with the
document it changes in this pull request. -->

## Self-review

- [ ] Each commit is a minimal coherent idea, passes on its own, and has a message
      that explains why (CLAUDE.md, "Commit Discipline"); no fixup commit is left.
- [ ] Every changed file is in the package's owns list, and every Zulip file it
      changes has its line in `tools/armada/fork_ledger.d/`.
- [ ] Tests cover the change: node tests, backend tests, or the `arm check`
      assertions it names, each run three times with identical statuses.
- [ ] What a person sees was checked at 1920, 1440 and 1100 px, in light and dark
      where colour can change, with the keyboard, and in every state.
- [ ] Every string a person reads is tagged for translation.
- [ ] No secret, licensed font or sprite is committed; access control and XSS
      were reviewed.
- [ ] Documentation in `docs/armada/` says what changed in Armada's behaviour.
