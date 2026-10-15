// What a known.json entry must hold, and the rule that the file only ever
// improves. Pure; `known-lint.mjs` feeds it the file and its git history.
//
// Every entry is a failure we know about:
//
//     "check/assertion": {
//         "measured": 52,           the value when it was last recorded
//         "unit": "px",
//         "threshold": "<= 0.5",    the assertion's own operator and threshold
//         "max": 65,                the worst value still accepted
//         "owner": "B3",            the package that turns it to FIXED
//         "audit": "...",           where the failure is explained
//         "since": "2026-10-13"
//     }
//
// "Worst" is measured from the threshold: for `<=` a larger value is worse,
// for `>=` a smaller one, for `==` one further away. So `max` is a ceiling
// for the usual `<=` and `== 0` entries, and for a `== 1` that measures 0 it
// is 0, which nothing can be worse than.

const KEYS = new Set(["measured", "unit", "threshold", "max", "owner", "audit", "since"]);

export function parse_threshold(text) {
    const match = /^(<=|>=|==) (-?\d+(?:\.\d+)?)$/.exec(String(text));
    return match ? {operator: match[1], threshold: Number(match[2])} : null;
}

// How far a value is on the failing side of its threshold.
export function distance(value, operator, threshold) {
    if (operator === "<=") {
        return value - threshold;
    }
    if (operator === ">=") {
        return threshold - value;
    }
    return Math.abs(value - threshold);
}

// Whether a measured value is worse than the entry's `max` allows.
export function beyond_max(value, max, operator, threshold) {
    return distance(value, operator, threshold) > distance(max, operator, threshold);
}

// What is wrong with one entry on its own.
export function entry_problems(id, entry) {
    const problems = [];
    const say = (text) => problems.push(`${id}: ${text}`);
    if (!/^[a-z0-9-]+\/\S+$/.test(id)) {
        say('the id is not "<check>/<assertion>"');
    }
    for (const key of Object.keys(entry)) {
        if (!KEYS.has(key)) {
            say(`unknown field "${key}"`);
        }
    }
    const parsed = parse_threshold(entry.threshold);
    if (!parsed) {
        say(`threshold "${entry.threshold}" is not "<=|>=|== <number>"`);
    }
    if (entry.measured !== null && typeof entry.measured !== "number") {
        say("measured is neither a number nor null");
    }
    if (typeof entry.unit !== "string") {
        say("unit is missing");
    }
    if (typeof entry.max !== "number") {
        say("max is missing: every known failure has a ceiling it may not get worse than");
    } else if (
        parsed &&
        typeof entry.measured === "number" &&
        beyond_max(entry.measured, entry.max, parsed.operator, parsed.threshold)
    ) {
        say(`measured ${entry.measured} is already worse than max ${entry.max}`);
    }
    for (const key of ["owner", "audit"]) {
        if (typeof entry[key] !== "string" || entry[key].trim() === "") {
            say(`${key} is missing`);
        } else if (/\bTODO\b/.test(entry[key])) {
            say(`${key} still says TODO`);
        }
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(entry.since))) {
        say("since is not a date (YYYY-MM-DD)");
    }
    return problems;
}

// Ids named by `Ratchet-exception:` trailers in commit messages. Only the
// orchestrator writes one; it lets the named entries change in a way that
// is not an improvement (a new known failure, a raised max).
export function ratchet_exceptions(messages) {
    const ids = new Set();
    for (const line of messages.split("\n")) {
        const match = /^Ratchet-exception:\s*(.+)$/.exec(line.trim());
        if (match) {
            for (const id of match[1].split(/[\s,]+/)) {
                if (id) {
                    ids.add(id);
                }
            }
        }
    }
    return ids;
}

// How `next` fails to improve on `base`. An entry may be deleted, its max
// lowered, its owner, audit and measured value changed; it may not appear,
// change its threshold or unit, or have its max raised.
export function ratchet_problems(base, next, exceptions = new Set()) {
    const problems = [];
    for (const [id, entry] of Object.entries(next)) {
        if (exceptions.has(id)) {
            continue;
        }
        const before = base[id];
        if (before === undefined) {
            problems.push(
                `${id}: new known failure; fix it instead, or ask for a Ratchet-exception`,
            );
            continue;
        }
        if (before.threshold !== entry.threshold || before.unit !== entry.unit) {
            problems.push(
                `${id}: threshold changed from "${before.threshold}${before.unit ?? ""}" to "${entry.threshold}${entry.unit ?? ""}"`,
            );
            continue;
        }
        const parsed = parse_threshold(entry.threshold);
        if (
            parsed &&
            typeof before.max === "number" &&
            typeof entry.max === "number" &&
            beyond_max(entry.max, before.max, parsed.operator, parsed.threshold)
        ) {
            problems.push(`${id}: max raised from ${before.max} to ${entry.max}`);
        }
    }
    return problems;
}

// Everything wrong with `next`, a known.json's text, against the version it
// must improve on.
export function lint_known(next_text, base_text, exceptions) {
    let next;
    try {
        next = JSON.parse(next_text);
    } catch (error) {
        return [`known.json is not JSON: ${error.message}`];
    }
    const problems = Object.entries(next).flatMap(([id, entry]) => entry_problems(id, entry));
    const ids = Object.keys(next);
    if (ids.join("\n") !== ids.toSorted().join("\n")) {
        problems.push("entries are not sorted by id (run.mjs writes them sorted)");
    }
    if (base_text !== undefined) {
        problems.push(...ratchet_problems(JSON.parse(base_text), next, exceptions));
    }
    return problems;
}
