// The known.json lint, as tools/lint runs it: see lib/known_lint.mjs.
//
//   node tools/armada-checks/known-lint.mjs

import {spawnSync} from "node:child_process";
import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {run_lint} from "./lib/known_lint.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

process.exitCode = run_lint({
    git(args) {
        const result = spawnSync("git", args, {cwd: ROOT, encoding: "utf8"});
        return result.status === 0 ? result.stdout : null;
    },
    read(file) {
        return fs.readFileSync(path.join(ROOT, file), "utf8");
    },
    env: process.env,
    print(line) {
        console.log(line);
    },
});
