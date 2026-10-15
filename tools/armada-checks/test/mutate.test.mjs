import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import path from "node:path";
import {afterEach, test} from "node:test";
import {fileURLToPath} from "node:url";

import {
    MUTATIONS,
    REPLACED_BY,
    load_overrides,
    mutate_script,
    mutation_applied,
    requested_mutation,
} from "../lib/mutate.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CHECKS = new Set(
    fs.readdirSync(path.join(ROOT, "checks")).map((file) => file.replace(/\.mjs$/, "")),
);
const KNOWN = JSON.parse(fs.readFileSync(path.join(ROOT, "known.json"), "utf8"));

afterEach(() => {
    delete process.env.ARMADA_CHECKS_MUTATE;
});

test("every mutation names its check, its scenarios and the passing assertions it breaks", () => {
    assert.ok(Object.keys(MUTATIONS).length > 0);
    for (const [name, mutation] of Object.entries(MUTATIONS)) {
        assert.match(name, /^[a-z0-9-]+$/, name);
        assert.equal(typeof mutation.about, "string", name);
        assert.ok(CHECKS.has(mutation.check), `${name}: no check ${mutation.check}`);
        assert.doesNotThrow(() => new RegExp(mutation.only), name);
        assert.ok(mutation.breaks.length > 0, name);
        for (const id of mutation.breaks) {
            assert.ok(id.startsWith(`${mutation.check}/`), `${name}: ${id}`);
            // A known failure cannot turn red, so it cannot be proved.
            assert.ok(!(id in KNOWN), `${name}: ${id} is in known.json`);
        }
        assert.ok(mutation.edits.length > 0, name);
        for (const edit of mutation.edits) {
            // match() counts every place only with the g flag.
            assert.ok(edit.find instanceof RegExp && edit.find.global, `${name}: ${edit.find}`);
            assert.ok(["string", "function"].includes(typeof edit.replace), name);
            assert.ok(Number.isInteger(edit.min) && edit.min >= 1, name);
        }
    }
});

test("the packages' files replace built-in mutations by name", () => {
    for (const [name, file] of Object.entries(REPLACED_BY)) {
        assert.ok(name in MUTATIONS, name);
        assert.ok(fs.existsSync(path.join(ROOT, "mutations", file)), file);
    }
});

test("load_overrides reads .mjs files in name order, later ones winning", async () => {
    const empty = await load_overrides(path.join(os.tmpdir(), "armada-checks-no-such-directory"));
    assert.deepEqual(empty.replaced, {});
    assert.deepEqual(Object.keys(empty.merged), Object.keys(MUTATIONS));

    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "armada-checks-mutations-"));
    try {
        const entry = (about) =>
            `{about: "${about}", check: "kit", only: "x", breaks: ["kit/x"], edits: []}`;
        fs.writeFileSync(
            path.join(directory, "A.mjs"),
            `export const MUTATIONS = {"made-up": ${entry("first")}, "stream-thrashes": ${entry("replaced")}};`,
        );
        fs.writeFileSync(
            path.join(directory, "B.mjs"),
            `export const MUTATIONS = {"made-up": ${entry("second")}};`,
        );
        fs.writeFileSync(path.join(directory, "C.mjs"), "export const OTHER = 1;");
        fs.writeFileSync(path.join(directory, "notes.txt"), "not a module");
        const {merged, replaced} = await load_overrides(directory);
        assert.deepEqual(replaced, {"made-up": "B.mjs", "stream-thrashes": "A.mjs"});
        assert.equal(merged["made-up"].about, "second");
        assert.equal(merged["stream-thrashes"].about, "replaced");
    } finally {
        fs.rmSync(directory, {recursive: true});
    }
});

test("requested_mutation reads ARMADA_CHECKS_MUTATE", () => {
    assert.equal(requested_mutation(), undefined);
    process.env.ARMADA_CHECKS_MUTATE = "no-such-mutation";
    assert.throws(() => requested_mutation(), /no such mutation: no-such-mutation \(have: /);
    process.env.ARMADA_CHECKS_MUTATE = "stream-thrashes";
    const mutation = requested_mutation();
    assert.equal(mutation.name, "stream-thrashes");
    assert.equal(mutation.check, "stream-cost");
    assert.deepEqual(mutation.applied, [0]);
});

test("mutate_script rewrites every match and counts it against min", () => {
    const mutation = {
        name: "made-up",
        edits: [
            {find: /alpha\(\)/g, replace: "beta()", min: 2},
            {find: /gamma/g, replace: "delta", min: 1},
        ],
        applied: [0, 0],
    };
    assert.equal(mutate_script(mutation, "nothing to see"), undefined);
    assert.equal(mutate_script(mutation, "alpha(); alpha();"), "beta(); beta();");
    assert.deepEqual(mutation.applied, [2, 0]);
    assert.equal(mutation_applied(mutation), false);
    assert.equal(mutate_script(mutation, "gamma"), "delta");
    assert.equal(mutation_applied(mutation), true);
});
