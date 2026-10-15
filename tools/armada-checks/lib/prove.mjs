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

import {keep_targets} from "./host.mjs";
import {ENV_ERROR_STATUS, run_under_lock} from "./lock.mjs";
import {MUTATIONS, REPLACED_BY} from "./mutate.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RUNNER = path.resolve(HERE, "../run.mjs");

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
// Mutations whose run reported ENV-ERROR: neither proved nor not proved.
const env_errors = [];

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
    if (report?.verdict === "ENV-ERROR") {
        env_errors.push(name);
        results.push({name, env_errors: report.env_errors});
        console.log(`ENV-ERROR  ${name}: the environment changed during its run`);
        for (const {problem} of report.env_errors) {
            console.log(`             ${problem}`);
        }
        continue;
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
    proved: names.length - failed - env_errors.length,
    failed,
    env_errors,
    by_precondition_only,
    results,
};
for (const target of keep_targets("var/armada-checks/prove.json")) {
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, `${JSON.stringify(summary, null, 4)}\n`);
}
const proved = names.length - failed - env_errors.length;
if (env_errors.length > 0) {
    console.log(
        `\nENV-ERROR: ${env_errors.length} of ${names.length} mutations ran while the environment changed (${env_errors.join(", ")}); run them again. ${proved} proved, ${failed} not proved.`,
    );
    process.exitCode = ENV_ERROR_STATUS;
} else {
    console.log(
        `\n${failed === 0 ? "ALL PROVED" : "NOT ALL PROVED"}: ${proved} of ${names.length} mutations turn their assertions red; ${by_precondition_only} of them only by making an assertion unmeasurable ("PROVED n/a": that assertion still needs an edit that gives it a failing value)`,
    );
    process.exitCode = failed === 0 ? 0 : 1;
}
