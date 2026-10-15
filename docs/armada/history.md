---
orphan: true
---

# Armada's history in git, and the line after it

Armada's history has three parts. Before the line, two of its commits
are too large to review or bisect through; after it, every commit is
minimal and came through a pull request (`docs/armada/process.md`). This
file says where the line is, why the large commits stay as they are, and
how to find the work inside them.

## The three parts

1. **Zulip, then the proof of concept.** The fork base is Zulip's
   `7813218321` (27 June 2026). The proof of concept that followed, on
   `pear-main` up to `5cf3f3c241`, is ordinary history: commit by commit,
   as it was written. `tools/armada/fork_ledger.d/poc.txt` lists every
   Zulip file it changed.
2. **The two checkpoints**, on `ui-overhaul`:

   - `89fb6f54e4`, 14 October 2026, "armada: Checkpoint the interface
     rebuild before the rename.": 504 files, +46,489 −3,640.
   - `0e53081710`, 15 October 2026, "armada: Checkpoint the beta's
     partners and the app frame.": 56 files.

   Both messages say that not every gate passes at that commit.

3. **The rename** (package R1): five commits after the checkpoints that
   take the old code name out of the server, the old interface and the
   tools. Their subjects, oldest first:

   - "armada: Move the files named after the old code name."
   - "models: Give the agent's six models plain nouns."
   - "armada: Rename the agent's namespace from the old code name."
   - "armada: Call the agent Onix in everything people read."
   - "armada: Keep the old code name out, and say so in the guidance."

   Each is minimal and passed its own checks (`build/R1.md`). Find them
   with `git log --oneline 0e53081710..` rather than by hash: their
   hashes change if a fixup is folded in before they land.

**The line is drawn after the rename's last commit.** Every commit after
it reached the trunk through a pull request, passed
`.github/workflows/armada-ci.yml` on its own, and is a minimal coherent
idea.

## Why the checkpoints are not split

Up to eight builders shared one working tree, so only the orchestrator
committed, and by then six packages were mixed in the same files. Their
only possible commit was everything at once.

Splitting them now would invent states that never existed and were
never tested, since the packages overlapped for days; it would take days
and rewrite a branch that is public and that the rename is built on. The
fork ledger already maps every Zulip file to its package, and each
package's report says why it changed. Only a secret or a licensed file
in them would force a rewrite, and there is neither: the fonts committed
are open (OFL) ones, the licensed directories hold only their READMEs and
the Gen 9 file list (`tools/armada/ci/licensed-guard`), and
`tools/armada/ci/secrets-scan` finds no credential in any commit of the
fork.

## The tags

The two checkpoints are tagged, so that tools and people can name them.
Tagging and pushing tags is the user's or the integrator's step, not a
builder's:

```bash
git tag -a checkpoint/1 89fb6f54e4 -m "Checkpoint: F0-F4, the beta, deploy, part of F2b and F5."
git tag -a checkpoint/2 0e53081710 -m "Checkpoint: the beta's rounds 2 and 3, F5, F2b, checks."
git push origin checkpoint/1 checkpoint/2
```

Once the rename is on the trunk, `git merge-base --is-ancestor
checkpoint/2 main` succeeds (the trunk is `pear-main` until the user
renames it).

## Bisecting across the checkpoints

Skip both, so that `git bisect` never tests a state that was not meant
to pass:

```bash
git bisect start <bad> <good>
git bisect skip checkpoint/1 checkpoint/2
```

If bisect ends by naming a checkpoint, the change is inside it. Narrow by
package instead: take the package whose ledger file lists the file you
suspect, read its report, and diff only its paths, for example
`git diff checkpoint/1^ checkpoint/1 -- $(awk '!/^#/ {print $2}'
tools/armada/fork_ledger.d/F5.txt)`. Files the fork added (everything
under `web/src/armada/`, `tools/armada*/`, `docs/armada/`) need no ledger
line; their directory names the package.

## Where each package is

Reports are in `var/armada-overhaul/build/`, on the machine the rebuild
is done on and outside git until the private repository holds them
(process review, W8). Ledger files are in `tools/armada/fork_ledger.d/`.

| Package                               | Ledger file  | Reports                                                                                                 | Commit                  |
| ------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------- | ----------------------- |
| Proof of concept                      | `poc.txt`    | none; its commits on `pear-main`                                                                        | before `5cf3f3c241`     |
| F0, pins                              | `F0.txt`     | `F0.md`, `F0-verify.md`                                                                                 | checkpoint 1            |
| F1, toolchain                         | `F1.txt`     | `F1.md`, `F1-verify.md`                                                                                 | checkpoint 1            |
| F2, load fixes                        | `F2.txt`     | `F2.md`, `F2-verify.md`                                                                                 | checkpoint 1            |
| F2b, the cuts and the browser targets | `F2.txt`     | `F2b.md`, `F2b-verify.md`                                                                               | checkpoints 1 and 2     |
| F3, tokens and fonts                  | `F3.txt`     | `F3.md`, `F3-verify.md`                                                                                 | checkpoint 1            |
| F4, kit                               | `F4.txt`     | `F4.md`, `F4-verify.md`                                                                                 | checkpoint 1            |
| F5, the app frame                     | `F5.txt`     | `F5.md`, `F5-verify.md`, `F5-copy-notes.md`                                                             | checkpoints 1 and 2     |
| The interim beta at `/beta/`          | `beta.txt`   | `BETA.md`, `BETA-check.md`, `BETA-recheck.md`, `BETA2-check.md`, `BETA3-check.md`, `BETA3-fix-check.md` | checkpoints 1 and 2     |
| Deploy                                | `deploy.txt` | `DEPLOY.md`, `DEPLOY-check.md`, `DEPLOY-recheck.md`, `DEPLOY2-check.md`                                 | checkpoints 1 and 2     |
| The checks' fixes                     | none needed  | `CHECKS-fix.md`, `CHECKS-fix-check.md`                                                                  | checkpoint 2            |
| The agent's instructions              | none needed  | `INSTRUCTIONS.md`, `INSTRUCTIONS-check.md`                                                              | checkpoint 1            |
| R1, the rename                        | `R1.txt`     | `R1.md`                                                                                                 | the five rename commits |
