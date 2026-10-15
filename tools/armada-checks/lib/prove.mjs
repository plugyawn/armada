// Shows that the pins can fail: runs each mutation of lib/mutate.mjs
// against the check it belongs to and requires every assertion the
// mutation is meant to break to come out FAIL.
//
//   limactl shell armada -- arm sh "source ~/shot.env; node tools/armada-checks/lib/prove.mjs [mutation names...]"
//
// A mutation rewrites the application's scripts inside the one browser of
// that run; the development server and its files are not touched. Only the
// scenarios that hold the targeted assertions are run, so the suite's
// "stale" lines for the rest are expected and ignored here.

import {spawnSync} from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {run_under_lock} from "./lock.mjs";
import {MUTATIONS, REPLACED_BY} from "./mutate.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RUNNER = path.resolve(HERE, "../run.mjs");
const HOST_REPORT = "/Users/progyan/runix/zulip/var/armada-checks/prove.json";

const wanted = process.argv.slice(2);
const unknown = wanted.filter((name) => !(name in MUTATIONS));
if (unknown.length > 0) {
    console.error(`no such mutation: ${unknown.join(", ")}`);
    process.exit(2);
}
// The whole proof holds the checks' lock; each run.mjs it starts finds it
// held by this process and does not wait for it.
const locked = run_under_lock({
    spawn: spawnSync,
    argv: [process.execPath, ...process.execArgv, ...process.argv.slice(1)],
});
if (locked !== undefined) {
    process.exit(locked);
}
const names = wanted.length > 0 ? wanted : Object.keys(MUTATIONS);
const results = [];
let failed = 0;
let by_precondition_only = 0;

for (const name of names) {
    const mutation = MUTATIONS[name];
    const report_file = path.join(os.tmpdir(), `armada-prove-${process.pid}-${name}.json`);
    spawnSync("node", [RUNNER, mutation.check, "--json", report_file], {
        env: {
            ...process.env,
            ARMADA_CHECKS_MUTATE: name,
            ARMADA_CHECKS_ONLY: `^(fixture|${mutation.only})$`,
        },
        stdio: "ignore",
    });
    let report;
    try {
        report = JSON.parse(fs.readFileSync(report_file, "utf8"));
        fs.unlinkSync(report_file);
    } catch {
        report = undefined;
    }
    const check = report?.checks[0];
    const applied =
        check?.measurements.find((entry) => entry.id.endsWith(`mutation.${name}.applied`))
            ?.value === true;
    const outcomes = mutation.breaks.map((id) => {
        const assertion = check?.assertions.find((entry) => entry.id === id);
        return {
            id,
            status: assertion?.status ?? "did not run",
            value: assertion?.value ?? null,
            note: assertion?.note ?? "",
        };
    });
    const proved = applied && outcomes.every((outcome) => outcome.status === "FAIL");
    if (!proved) {
        failed += 1;
    }
    // An assertion that came out FAIL because its precondition no longer
    // held ("not measurable") was not shown to fail on its own measure:
    // the mutation broke the scenario around it.
    const unmeasured = outcomes
        .filter((outcome) => outcome.status === "FAIL" && outcome.value === null)
        .map((outcome) => outcome.id);
    if (proved && unmeasured.length > 0) {
        by_precondition_only += 1;
    }
    results.push({
        name,
        source: REPLACED_BY[name] ?? "lib/mutate.mjs",
        about: mutation.about,
        applied,
        proved,
        unmeasured,
        outcomes,
    });
    const source = REPLACED_BY[name] ? ` (mutations/${REPLACED_BY[name]})` : "";
    console.log(
        `${proved ? (unmeasured.length > 0 ? "PROVED n/a" : "PROVED    ") : "NOT PROVED"} ${name}${source}: ${mutation.about}`,
    );
    if (!applied) {
        console.log("             the edit did not match the application's scripts");
    }
    for (const outcome of outcomes) {
        console.log(
            `             ${outcome.status.padEnd(11)} ${outcome.id}  ${outcome.value ?? "n/a"}  (${outcome.note.slice(0, 160)})`,
        );
    }
}

const summary = {
    finished: new Date().toISOString(),
    proved: names.length - failed,
    failed,
    by_precondition_only,
    results,
};
for (const target of new Set([
    path.resolve(HERE, "../../../var/armada-checks/prove.json"),
    HOST_REPORT,
])) {
    try {
        fs.mkdirSync(path.dirname(target), {recursive: true});
        fs.writeFileSync(target, `${JSON.stringify(summary, null, 4)}\n`);
    } catch {
        // The host checkout is not mounted here.
    }
}
console.log(
    `\n${failed === 0 ? "ALL PROVED" : "NOT ALL PROVED"}: ${names.length - failed} of ${names.length} mutations turn their assertions red; ${by_precondition_only} of them only by making an assertion unmeasurable ("PROVED n/a": that assertion still needs an edit that gives it a failing value)`,
);
process.exitCode = failed === 0 ? 0 : 1;
