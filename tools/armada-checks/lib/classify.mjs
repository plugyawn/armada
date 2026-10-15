// The ratchet: each assertion of a run compared with known.json, and what
// --update-known and --accept-failures write back. Pure, so that the
// harness's own tests (tools/armada-checks/test/) can hold it at 100%.

export function format_value(value, unit) {
    return value === null ? "n/a" : `${value}${unit}`;
}

// Compare one check's assertions with known.json. Sets each assertion's
// `status`, adds its id to `seen`, and calls `print(status, id, detail)`
// once per line.
export function classify(result, known, seen, print) {
    const counts = {pass: 0, known_fail: 0, fail: 0, fixed: 0};
    for (const assertion of result.assertions) {
        seen.add(assertion.id);
        const entry = known[assertion.id];
        const detail = `${format_value(assertion.value, assertion.unit)} ${assertion.operator} ${assertion.threshold}${assertion.unit}${assertion.note ? `  (${assertion.note})` : ""}`;
        if (assertion.pass && entry) {
            assertion.status = "FIXED";
            counts.fixed += 1;
            print(
                "FIXED",
                assertion.id,
                `${detail}  [was ${entry.measured}${entry.unit ?? ""}; remove it from known.json or run with --update-known]`,
            );
        } else if (assertion.pass) {
            assertion.status = "PASS";
            counts.pass += 1;
            print("PASS", assertion.id, detail);
        } else if (entry && assertion.value === null && entry.measured !== null) {
            // It used to fail with a number and now measures nothing: the
            // element it looks at is gone or renamed, and it could never
            // report FIXED.
            assertion.status = "FAIL";
            counts.fail += 1;
            print("FAIL", assertion.id, `${detail}  [known failure is no longer measurable]`);
        } else if (entry && typeof entry.max === "number" && assertion.value > entry.max) {
            // A known failure that carries a ceiling: a count that is wrong
            // today (uncaught exceptions) must still not grow.
            assertion.status = "FAIL";
            counts.fail += 1;
            print("FAIL", assertion.id, `${detail}  [known at most ${entry.max}; it got worse]`);
        } else if (entry) {
            assertion.status = "KNOWN-FAIL";
            counts.known_fail += 1;
            print("KNOWN-FAIL", assertion.id, detail);
        } else {
            assertion.status = "FAIL";
            counts.fail += 1;
            print("FAIL", assertion.id, detail);
        }
    }
    for (const measurement of result.measurements) {
        print(
            "INFO",
            measurement.id,
            `${format_value(measurement.value, measurement.unit)}${measurement.note ? `  (${measurement.note})` : ""}`,
        );
    }
    return counts;
}

// A known entry whose assertion no longer exists is dead weight that would
// hide a renamed assertion; the suite treats it as a failure.
export function stale_ids(known, results, seen) {
    const ran = new Set(results.filter((result) => !result.error).map((result) => result.name));
    return Object.keys(known).filter(
        (id) => ran.has(id.split("/")[0]) && !seen.has(id) && !id.startsWith("_"),
    );
}

// The known.json that --update-known and --accept-failures write: entries
// sorted by id. Moves `totals` and each assertion's status the way the
// rewrite does.
export function rewrite_known(known, results, stale, options, totals, today) {
    const next = new Map(Object.entries(known));
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
                        existing?.audit ?? "TODO: say which audit or commit explains this failure",
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
    return Object.fromEntries([...next].toSorted(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}
