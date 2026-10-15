// The known.json lint: every entry well formed (lib/known.mjs), and the
// file no worse than where this branch left the base branch. `io` holds
// what touches the outside world, so that the tests can stand in for git:
// {git(args) -> stdout or null, read(path) -> text, env, print(line)}.

import {lint_known, ratchet_exceptions} from "./known.mjs";

export const KNOWN_FILE = "tools/armada-checks/known.json";

// Where a branch starts from, tried in order, unless ARMADA_RATCHET_BASE
// names one. The local branch comes first: on the machine the rebuild is
// done on, origin can lag behind it; a CI checkout has only origin's.
export const BASE_BRANCHES = ["ui-overhaul", "origin/ui-overhaul"];

export function find_base(io) {
    const named = io.env.ARMADA_RATCHET_BASE;
    for (const ref of named ? [named] : BASE_BRANCHES) {
        const commit = io.git(["merge-base", "HEAD", ref])?.trim();
        if (commit) {
            return {ref, commit};
        }
    }
    if (named) {
        throw new Error(`ARMADA_RATCHET_BASE=${named} has no merge base with HEAD`);
    }
    return null;
}

// Returns the exit status: 0 when known.json is well formed and improves on
// its base, 1 otherwise.
export function run_lint(io) {
    const base = find_base(io);
    const commit = base?.commit ?? "HEAD";
    const against = base ? `${base.ref} (merge base ${commit.slice(0, 10)})` : "HEAD";
    const base_text = io.git(["show", `${commit}:${KNOWN_FILE}`]) ?? undefined;
    const exceptions = base
        ? ratchet_exceptions(io.git(["log", "--format=%B", `${commit}..HEAD`]) ?? "")
        : new Set();
    const problems = lint_known(io.read(KNOWN_FILE), base_text, exceptions);
    for (const problem of problems) {
        io.print(`${KNOWN_FILE}: ${problem}`);
    }
    if (problems.length > 0) {
        io.print(
            `known.json may only improve on ${against}: delete entries, lower a max, fix an owner or an audit. Anything else needs a "Ratchet-exception: <id>" trailer, which only the orchestrator writes (tools/armada-checks/README.md).`,
        );
        return 1;
    }
    return 0;
}
