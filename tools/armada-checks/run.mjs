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
import {classify, rewrite_known, stale_ids} from "./lib/classify.mjs";
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
            const counts = classify(result, known, seen, print_line);
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

    const stale = stale_ids(known, results, seen);
    if (stale.length > 0) {
        console.log("");
    }
    for (const id of stale) {
        totals.stale += 1;
        print_line("STALE", id, "listed in known.json but no such assertion ran");
    }

    if (options.update_known || options.accept_failures) {
        const sorted = rewrite_known(
            known,
            results,
            stale,
            options,
            totals,
            started.toISOString().slice(0, 10),
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
