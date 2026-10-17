"use strict";

// What is shaped like a key (web/src/armada/beta/paste_guard.js,
// design/r9/plugins/ROUND2.md decision 4): a key goes into a plugin's
// settings, never into a message, so a field offers to put it there first.
// The keys below are made up in each service's shape. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {findKey} = zrequire("armada/beta/paste_guard.js");
// The keys below are made up. Those a credential scanner would take for
// real ones (tools/armada/ci/secrets-scan, GitHub's push protection) are
// put together here, so that no line of the repository holds one.
const PRIVATE_KEY_LINE = ["-----BEGIN OPENSSH", "PRIVATE KEY-----"].join(" ");

run_test("a key in the shape a service gives it", () => {
    for (const key of [
        "sk-proj-4fQ9xT2mZr8LkW1vB7nYc3Hd",
        "sk-ant-api03-Zx81kLmq0Pd2Wn7Rt5Yb",
        ["sk", "live_51HxQ2eLkd93PqZr0aBc"].join("_"),
        "ghp_8ZqLm2Xc4Vb7Nn1Kp0Rt5Yw9Ss3Dd6Ff2Gh",
        "github_pat_11ABCDE0Y0aBcDeFgHiJkLmN",
        ["glpat", "x8Zq2Lm4Vb7Nn1Kp0Rt5"].join("-"),
        ["xoxb", "1234567890-0987654321-AbCdEfGhIj"].join("-"),
        "hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
        ["AKIA", "IOSFODNN7EXAMPLE"].join(""),
        "AIzaSyD4x8Zq2Lm4Vb7Nn1Kp0Rt5Yw9Ss3Dd6",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "redis://:s3cret-passw0rd@10.0.0.4:6379/0",
        "postgres://iago:hunter22@db.internal/armada",
        "q8ZvL2mXc4Vb7Nn1Kp0Rt5Yw9Ss3Dd6F",
    ]) {
        assert.equal(findKey(key), key);
    }
    // A private key's block, wherever it starts.
    assert.equal(findKey(`here:\n${PRIVATE_KEY_LINE}\nb3BlbnNzaC1rZXktdjEA\n`), PRIVATE_KEY_LINE);
});

run_test("in words: the first word that is one, out of its quotes and its full stop", () => {
    assert.equal(
        findKey('use "ghp_8ZqLm2Xc4Vb7Nn1Kp0Rt5Yw9Ss3Dd6Ff2Gh". thanks'),
        "ghp_8ZqLm2Xc4Vb7Nn1Kp0Rt5Yw9Ss3Dd6Ff2Gh",
    );
    assert.equal(
        findKey("the key is (sk-proj-4fQ9xT2mZr8LkW1vB7nYc3Hd), and the other"),
        "sk-proj-4fQ9xT2mZr8LkW1vB7nYc3Hd",
    );
});

run_test("what is not a key: words, paths, addresses, commits and checksums", () => {
    for (const words of [
        "",
        "only the H100 machines, and their temperatures",
        "pip install sk-learn-pipeline-extras",
        "sk-learn-pipeline-extras-and-more",
        "plugins/eval-queue/armada-plugin.json",
        "https://wandb.ai/runix/nextlat-scaling/runs/lr-3e-4-b",
        "redis://10.0.0.4:6379/0",
        "pin it to 3c51a9e0b8f2d4c6a1e9f7b5d3c1a0e8f6d4b2a9",
        "sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "CamelCaseWordsWithoutAnyDigitsInThem",
        "lr-3e-4-b, lr-1e-3-b and lr-1e-4-a",
    ]) {
        assert.equal(findKey(words), null, words);
    }
    assert.equal(findKey(undefined), null);
});

run_test("in a setting, a long hex run is a key too: a W&B key is forty hex digits", () => {
    const wandb = "3c51a9e0b8f2d4c6a1e9f7b5d3c1a0e8f6d4b2a9";
    assert.equal(findKey(wandb), null);
    assert.equal(findKey(wandb, {hex: true}), wandb);
    // A short one is not.
    assert.equal(findKey("3c51a9e0b8f2d4c6", {hex: true}), null);
});
