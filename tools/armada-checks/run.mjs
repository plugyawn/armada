// Armada layout-stability and latency checks.
//
//   limactl shell armada -- arm check [check names...] [--json <path>]
//                                     [--update-known] [--accept-failures] [--list]
//
// Runs each check in a fresh page of one headless Chromium, prints one line
// per assertion, writes a JSON report, and exits non-zero unless the suite is
// green. See README.md in this directory.

import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {BASE, USER, VIEWPORT, launch, login} from "./lib/browser.mjs";
import {CheckContext} from "./lib/harness.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
// The suite runs from the VM's copy of the checkout, which a sync loop
// overwrites from the host checkout. Files the suite writes for people to
// keep (known.json, the report) therefore also go to the host checkout when
// it is mounted.
const HOST_REPO = process.env.ARMADA_HOST_CHECKOUT ?? "/Users/progyan/runix/zulip";
const KNOWN_FILE = "tools/armada-checks/known.json";
const DEFAULT_REPORT = "var/armada-checks/report.json";

function parse_arguments(argv) {
    const options = {
        names: [],
        json: undefined,
        update_known: false,
        accept_failures: false,
        list: false,
    };
    const rest = [...argv];
    while (rest.length > 0) {
        const argument = rest.shift();
        switch (argument) {
            case "--json": {
                options.json = rest.shift();

                break;
            }
            case "--update-known": {
                options.update_known = true;

                break;
            }
            case "--accept-failures": {
                options.accept_failures = true;

                break;
            }
            case "--list": {
                options.list = true;

                break;
            }
            case "--help":
            case "-h": {
                options.help = true;

                break;
            }
            default:
                if (argument.startsWith("--")) {
                    throw new Error(`unknown option ${argument}`);
                } else {
                    options.names.push(argument);
                }
        }
    }
    return options;
}

async function load_checks() {
    const directory = path.join(HERE, "checks");
    const files = fs
        .readdirSync(directory)
        .filter((file) => file.endsWith(".mjs"))
        .toSorted();
    const checks = [];
    for (const file of files) {
        const module = await import(path.join(directory, file));
        checks.push(module.default ?? module);
    }
    return checks.toSorted((a, b) => (a.order ?? 100) - (b.order ?? 100));
}

function repo_targets(relative) {
    const targets = [path.join(REPO, relative)];
    if (HOST_REPO !== REPO && fs.existsSync(path.join(HOST_REPO, "tools/armada-checks"))) {
        targets.push(path.join(HOST_REPO, relative));
    }
    return targets;
}

function write_json(target, data) {
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, `${JSON.stringify(data, null, 4)}\n`);
}

function read_known() {
    // Prefer the host copy: it is the one people edit.
    for (const target of repo_targets(KNOWN_FILE).toReversed()) {
        if (fs.existsSync(target)) {
            return JSON.parse(fs.readFileSync(target, "utf8"));
        }
    }
    return {};
}

function format_value(value, unit) {
    return value === null ? "n/a" : `${value}${unit}`;
}

function print_line(status, id, detail) {
    console.log(`${status.padEnd(10)} ${id}  ${detail}`);
}

async function run_check(browser, check) {
    const context = new CheckContext(browser, check.name);
    const started = Date.now();
    let error;
    try {
        await context.init();
        await check.run(context);
    } catch (error_) {
        error = String(error_?.stack ?? error_)
            .split("\n")
            .slice(0, 4)
            .join(" | ");
    } finally {
        await context.close();
    }
    return {
        name: check.name,
        description: check.description,
        duration_ms: Date.now() - started,
        error,
        assertions: context.assertions,
        measurements: context.measurements,
    };
}

// Compare one check's assertions with known.json and print them.
function classify(result, known, seen) {
    const counts = {pass: 0, known_fail: 0, fail: 0, fixed: 0};
    for (const assertion of result.assertions) {
        seen.add(assertion.id);
        const entry = known[assertion.id];
        const detail = `${format_value(assertion.value, assertion.unit)} ${assertion.operator} ${assertion.threshold}${assertion.unit}${assertion.note ? `  (${assertion.note})` : ""}`;
        if (assertion.pass && entry) {
            assertion.status = "FIXED";
            counts.fixed += 1;
            print_line(
                "FIXED",
                assertion.id,
                `${detail}  [was ${entry.measured}${entry.unit ?? ""}; remove it from known.json or run with --update-known]`,
            );
        } else if (assertion.pass) {
            assertion.status = "PASS";
            counts.pass += 1;
            print_line("PASS", assertion.id, detail);
        } else if (entry && assertion.value === null && entry.measured !== null) {
            // It used to fail with a number and now measures nothing: the
            // element it looks at is gone or renamed, and it could never
            // report FIXED.
            assertion.status = "FAIL";
            counts.fail += 1;
            print_line("FAIL", assertion.id, `${detail}  [known failure is no longer measurable]`);
        } else if (entry && typeof entry.max === "number" && assertion.value > entry.max) {
            // A known failure that carries a ceiling: a count that is wrong
            // today (uncaught exceptions) must still not grow.
            assertion.status = "FAIL";
            counts.fail += 1;
            print_line(
                "FAIL",
                assertion.id,
                `${detail}  [known at most ${entry.max}; it got worse]`,
            );
        } else if (entry) {
            assertion.status = "KNOWN-FAIL";
            counts.known_fail += 1;
            print_line("KNOWN-FAIL", assertion.id, detail);
        } else {
            assertion.status = "FAIL";
            counts.fail += 1;
            print_line("FAIL", assertion.id, detail);
        }
    }
    for (const measurement of result.measurements) {
        print_line(
            "INFO",
            measurement.id,
            `${format_value(measurement.value, measurement.unit)}${measurement.note ? `  (${measurement.note})` : ""}`,
        );
    }
    return counts;
}

async function main() {
    const options = parse_arguments(process.argv.slice(2));
    const all_checks = await load_checks();
    if (options.help) {
        console.log(
            "usage: arm check [check names...] [--json <path>] [--update-known] [--accept-failures] [--list]",
        );
        return 0;
    }
    if (options.list) {
        for (const check of all_checks) {
            console.log(`${check.name.padEnd(26)} ${check.description}`);
        }
        return 0;
    }
    const unknown = options.names.filter(
        (name) => !all_checks.some((check) => check.name === name),
    );
    if (unknown.length > 0) {
        console.error(
            `no such check: ${unknown.join(", ")} (have: ${all_checks.map((check) => check.name).join(", ")})`,
        );
        return 2;
    }
    const checks =
        options.names.length > 0
            ? all_checks.filter((check) => options.names.includes(check.name))
            : all_checks;

    const known = read_known();
    const started = new Date();
    const browser = await launch();
    const results = [];
    const totals = {pass: 0, known_fail: 0, fail: 0, fixed: 0, error: 0, stale: 0};
    const seen = new Set();
    let chrome;
    try {
        chrome = await browser.version();
        await login(browser);
        for (const check of checks) {
            console.log(`\n== ${check.name}: ${check.description}`);
            const result = await run_check(browser, check);
            results.push(result);
            const counts = classify(result, known, seen);
            for (const [key, value] of Object.entries(counts)) {
                totals[key] += value;
            }
            if (result.error) {
                totals.error += 1;
                print_line("ERROR", check.name, result.error);
            }
            console.log(`   (${(result.duration_ms / 1000).toFixed(1)} s)`);
        }
    } finally {
        await browser.close();
    }

    // A known entry whose assertion no longer exists is dead weight that
    // would hide a renamed assertion; treat it as a failure of the suite.
    const ran = new Set(results.filter((result) => !result.error).map((result) => result.name));
    const stale = Object.keys(known).filter(
        (id) => ran.has(id.split("/")[0]) && !seen.has(id) && !id.startsWith("_"),
    );
    if (stale.length > 0) {
        console.log("");
    }
    for (const id of stale) {
        totals.stale += 1;
        print_line("STALE", id, "listed in known.json but no such assertion ran");
    }

    if (options.update_known || options.accept_failures) {
        const next = new Map(Object.entries(known));
        const today = started.toISOString().slice(0, 10);
        for (const result of results) {
            for (const assertion of result.assertions) {
                if (assertion.status === "FIXED" && options.update_known) {
                    next.delete(assertion.id);
                    totals.fixed -= 1;
                    totals.pass += 1;
                    assertion.status = "PASS";
                } else if (assertion.status === "KNOWN-FAIL" && options.update_known) {
                    next.set(assertion.id, {...next.get(assertion.id), measured: assertion.value});
                } else if (assertion.status === "FAIL" && options.accept_failures) {
                    const existing = next.get(assertion.id);
                    next.set(assertion.id, {
                        measured: assertion.value,
                        unit: assertion.unit,
                        threshold: `${assertion.operator} ${assertion.threshold}`,
                        audit:
                            existing?.audit ??
                            "TODO: say which audit or commit explains this failure",
                        since: existing?.since ?? today,
                        // Accepting a count that grew past its ceiling raises the ceiling.
                        ...(typeof existing?.max === "number" && assertion.value !== null
                            ? {max: Math.max(existing.max, assertion.value)}
                            : {}),
                    });
                    totals.fail -= 1;
                    totals.known_fail += 1;
                    assertion.status = "KNOWN-FAIL";
                }
            }
        }
        if (options.update_known) {
            for (const id of stale) {
                next.delete(id);
                totals.stale -= 1;
            }
        }
        const sorted = Object.fromEntries(
            [...next].toSorted(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
        );
        for (const target of repo_targets(KNOWN_FILE)) {
            write_json(target, sorted);
        }
        console.log(`\nknown.json rewritten (${Object.keys(sorted).length} entries)`);
    }

    const green =
        totals.fail === 0 && totals.fixed === 0 && totals.error === 0 && totals.stale === 0;
    const report = {
        started: started.toISOString(),
        finished: new Date().toISOString(),
        base: BASE,
        user: USER,
        viewport: VIEWPORT,
        chrome,
        green,
        totals,
        checks: results,
    };
    const report_targets = options.json
        ? [path.resolve(options.json)]
        : repo_targets(DEFAULT_REPORT);
    for (const target of report_targets) {
        write_json(target, report);
    }

    console.log(
        `\n${green ? "GREEN" : "RED"}: ${totals.pass} pass, ${totals.known_fail} known-fail, ${totals.fail} fail, ${totals.fixed} fixed-not-promoted, ${totals.stale} stale, ${totals.error} check errors`,
    );
    console.log(`report: ${report_targets.at(-1)}`);
    return green ? 0 : 1;
}

process.exitCode = await main();
