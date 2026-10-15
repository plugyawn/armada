import assert from "node:assert/strict";
import * as fs from "node:fs";
import path from "node:path";
import {test} from "node:test";
import {fileURLToPath} from "node:url";

import {
    beyond_max,
    distance,
    entry_problems,
    lint_known,
    parse_threshold,
    ratchet_exceptions,
    ratchet_problems,
} from "../lib/known.mjs";
import {BASE_BRANCHES, KNOWN_FILE, find_base, run_lint} from "../lib/known_lint.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

function entry(fields = {}) {
    return {
        measured: 4,
        unit: "px",
        threshold: "<= 0.5",
        max: 5,
        owner: "B3",
        audit: "maps/perf-input.md",
        since: "2026-10-13",
        ...fields,
    };
}

test("the repository's own known.json passes", () => {
    const text = fs.readFileSync(path.join(ROOT, KNOWN_FILE), "utf8");
    assert.deepEqual(lint_known(text, text, new Set()), []);
});

test("parse_threshold", () => {
    assert.deepEqual(parse_threshold("<= 0.5"), {operator: "<=", threshold: 0.5});
    assert.deepEqual(parse_threshold(">= -2"), {operator: ">=", threshold: -2});
    assert.deepEqual(parse_threshold("== 1"), {operator: "==", threshold: 1});
    assert.equal(parse_threshold("< 1"), null);
    assert.equal(parse_threshold(undefined), null);
});

test("distance and beyond_max go the failing way of each operator", () => {
    assert.equal(distance(5, "<=", 1), 4);
    assert.equal(distance(5, ">=", 8), 3);
    assert.equal(distance(4, "==", 6), 2);
    assert.equal(beyond_max(6, 5, "<=", 0), true);
    assert.equal(beyond_max(5, 5, "<=", 0), false);
    assert.equal(beyond_max(2, 3, ">=", 10), true);
    assert.equal(beyond_max(4, 3, ">=", 10), false);
    // A flag that must be 1 and is 0 cannot get worse.
    assert.equal(beyond_max(0, 0, "==", 1), false);
    assert.equal(beyond_max(3, 4, "==", 6), true);
    assert.equal(beyond_max(7, 4, "==", 6), false);
});

test("entry_problems accepts a full entry and names every missing part", () => {
    assert.deepEqual(entry_problems("c/a", entry()), []);
    assert.deepEqual(entry_problems("c/a", entry({measured: null})), []);
    assert.deepEqual(entry_problems("Bad id", entry()), [
        'Bad id: the id is not "<check>/<assertion>"',
    ]);
    assert.deepEqual(
        entry_problems("c/a", {
            measured: "4",
            threshold: "< 1",
            owner: " ",
            audit: "TODO: explain",
            since: "yesterday",
            extra: 1,
        }),
        [
            'c/a: unknown field "extra"',
            'c/a: threshold "< 1" is not "<=|>=|== <number>"',
            "c/a: measured is neither a number nor null",
            "c/a: unit is missing",
            "c/a: max is missing: every known failure has a ceiling it may not get worse than",
            "c/a: owner is missing",
            "c/a: audit still says TODO",
            "c/a: since is not a date (YYYY-MM-DD)",
        ],
    );
    assert.deepEqual(entry_problems("c/a", entry({measured: 6})), [
        "c/a: measured 6 is already worse than max 5",
    ]);
    assert.deepEqual(entry_problems("c/a", entry({measured: 6, threshold: "<"})), [
        'c/a: threshold "<" is not "<=|>=|== <number>"',
    ]);
});

test("ratchet_exceptions reads every id of every trailer", () => {
    const messages = [
        "checks: Add a check.",
        "",
        "Ratchet-exception: c/a, c/b",
        "  Ratchet-exception:c/c  ",
        "Ratchet-exception: ,",
        "Not-a-trailer: c/d",
    ].join("\n");
    assert.deepEqual([...ratchet_exceptions(messages)], ["c/a", "c/b", "c/c"]);
});

test("ratchet_problems allows only improvements, unless an exception names the id", () => {
    const base = {
        "c/deleted": entry(),
        "c/lowered": entry(),
        "c/raised": entry(),
        "c/rethresholded": entry(),
        "c/reunited": entry(),
        "c/ceiling-added": entry({max: undefined}),
        "c/excepted": entry(),
        "c/bad-threshold": entry({threshold: "<"}),
    };
    const next = {
        "c/lowered": entry({max: 4, owner: "C1", audit: "new audit", measured: 3}),
        "c/raised": entry({max: 6}),
        "c/rethresholded": entry({threshold: "<= 1"}),
        "c/reunited": entry({unit: "ms"}),
        "c/ceiling-added": entry(),
        "c/excepted": entry({max: 60}),
        "c/new": entry(),
        "c/bad-threshold": entry({threshold: "<", max: 9}),
    };
    assert.deepEqual(ratchet_problems(base, next, new Set(["c/excepted"])), [
        "c/raised: max raised from 5 to 6",
        'c/rethresholded: threshold changed from "<= 0.5px" to "<= 1px"',
        'c/reunited: threshold changed from "<= 0.5px" to "<= 0.5ms"',
        "c/new: new known failure; fix it instead, or ask for a Ratchet-exception",
    ]);
    assert.deepEqual(ratchet_problems({"c/a": {threshold: "== 0"}}, {"c/a": {threshold: "== 1"}}), [
        'c/a: threshold changed from "== 0" to "== 1"',
    ]);
});

test("lint_known checks JSON, every entry, the order and the ratchet", () => {
    assert.match(lint_known("{", undefined, new Set())[0], /^known.json is not JSON: /);
    const text = (known) => JSON.stringify(known);
    assert.deepEqual(lint_known(text({"c/a": entry(), "c/b": entry()}), undefined), []);
    assert.deepEqual(
        lint_known(
            text({"c/b": entry(), "c/a": entry({max: 9})}),
            text({"c/b": entry(), "c/a": entry()}),
            new Set(),
        ),
        [
            "entries are not sorted by id (run.mjs writes them sorted)",
            "c/a: max raised from 5 to 9",
        ],
    );
});

function fake_io({refs = {}, files = {}, log = "", env = {}} = {}) {
    const lines = [];
    const calls = [];
    return {
        lines,
        calls,
        env,
        print: (line) => lines.push(line),
        read: (file) => files[file],
        git(args) {
            calls.push(args.join(" "));
            if (args[0] === "merge-base") {
                return refs[args[2]] === undefined ? null : `${refs[args[2]]}\n`;
            }
            if (args[0] === "show") {
                return files[args[1]] ?? null;
            }
            return log;
        },
    };
}

test("find_base tries the base branches in order, or the one named", () => {
    assert.deepEqual(BASE_BRANCHES, ["ui-overhaul", "origin/ui-overhaul"]);
    assert.equal(find_base(fake_io()), null);
    assert.deepEqual(find_base(fake_io({refs: {"origin/ui-overhaul": "abc"}})), {
        ref: "origin/ui-overhaul",
        commit: "abc",
    });
    assert.deepEqual(
        find_base(fake_io({refs: {"ui-overhaul": "def", "origin/ui-overhaul": "abc"}})),
        {
            ref: "ui-overhaul",
            commit: "def",
        },
    );
    const named = {env: {ARMADA_RATCHET_BASE: "main"}, refs: {"ui-overhaul": "def"}};
    assert.throws(() => find_base(fake_io(named)), /ARMADA_RATCHET_BASE=main has no merge base/);
    assert.deepEqual(find_base(fake_io({...named, refs: {main: "0123456789abc"}})), {
        ref: "main",
        commit: "0123456789abc",
    });
});

test("run_lint compares the file with its merge base and honours trailers there", () => {
    const base = JSON.stringify({"c/a": entry(), "c/b": entry()});
    const raised = JSON.stringify({"c/a": entry({max: 7}), "c/b": entry({max: 8})});
    const io = fake_io({
        refs: {"ui-overhaul": "0123456789abcdef"},
        files: {[KNOWN_FILE]: raised, [`0123456789abcdef:${KNOWN_FILE}`]: base},
        log: "checks: Raise one.\n\nRatchet-exception: c/b\n",
    });
    assert.equal(run_lint(io), 1);
    assert.deepEqual(io.calls, [
        "merge-base HEAD ui-overhaul",
        `show 0123456789abcdef:${KNOWN_FILE}`,
        "log --format=%B 0123456789abcdef..HEAD",
    ]);
    assert.deepEqual(io.lines, [
        `${KNOWN_FILE}: c/a: max raised from 5 to 7`,
        'known.json may only improve on ui-overhaul (merge base 0123456789): delete entries, lower a max, fix an owner or an audit. Anything else needs a "Ratchet-exception: <id>" trailer, which only the orchestrator writes (tools/armada-checks/README.md).',
    ]);

    const failing_git = fake_io({
        refs: {"ui-overhaul": "0123456789abcdef"},
        files: {[KNOWN_FILE]: base},
        log: null,
    });
    assert.equal(run_lint(failing_git), 0);
});

test("run_lint without a base branch compares with HEAD, and with no history checks the entries only", () => {
    const base = JSON.stringify({"c/a": entry()});
    const io = fake_io({files: {[KNOWN_FILE]: JSON.stringify({}), [`HEAD:${KNOWN_FILE}`]: base}});
    assert.equal(run_lint(io), 0);
    assert.deepEqual(io.calls.at(-1), `show HEAD:${KNOWN_FILE}`);

    const fresh = fake_io({files: {[KNOWN_FILE]: JSON.stringify({"c/a": entry({owner: ""})})}});
    assert.equal(run_lint(fresh), 1);
    assert.match(fresh.lines.at(-1), /^known.json may only improve on HEAD: /);
});
