// The harness's own tests: node's test runner over test/*.test.mjs, with
// every module in FULLY_COVERED held to 100% of its lines, branches and
// functions. The harness's logic is the ratchet the rebuild is judged by,
// so it is tested like the product's pure code.
//
//   limactl shell armada -- arm sh "node tools/armada-checks/selftest.mjs"

import {spawnSync} from "node:child_process";
import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const FULLY_COVERED = [
    "lib/classify.mjs",
    "lib/known.mjs",
    "lib/known_lint.mjs",
    "lib/mutate.mjs",
    "lib/stats.mjs",
];

const tests = fs
    .readdirSync(path.join(HERE, "test"))
    .filter((file) => file.endsWith(".test.mjs"))
    .toSorted()
    .map((file) => path.join(HERE, "test", file));

const {status} = spawnSync(
    process.execPath,
    [
        "--test",
        "--experimental-test-coverage",
        ...FULLY_COVERED.map((file) => `--test-coverage-include=${path.join(HERE, file)}`),
        "--test-coverage-lines=100",
        "--test-coverage-branches=100",
        "--test-coverage-functions=100",
        ...tests,
    ],
    {stdio: "inherit"},
);
process.exitCode = status ?? 1;
