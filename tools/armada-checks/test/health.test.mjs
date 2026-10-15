import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import path from "node:path";
import {afterEach, beforeEach, test} from "node:test";

import {verdict_of} from "../lib/classify.mjs";
import {
    MIN_AVAILABLE_MEMORY,
    MIN_FREE_DISK,
    WATCHED,
    changed_files,
    changes,
    check_env_errors,
    describe_changed_files,
    foreign_events,
    is_local,
    meminfo_available,
    problems,
    response_problem,
    server_processes,
    snapshot,
} from "../lib/health.mjs";

let root;
let proc;
let tree;

function add_process(pid, command, {cwd = tree, state = "S", start = "100"} = {}) {
    const directory = path.join(proc, String(pid));
    fs.mkdirSync(directory, {recursive: true});
    fs.writeFileSync(path.join(directory, "cmdline"), `${command.replaceAll(" ", "\0")}\0`);
    if (cwd !== null) {
        fs.symlinkSync(cwd, path.join(directory, "cwd"));
    }
    const fields = [
        state,
        "1",
        ...Array.from({length: 17}, (_, index) => String(index)),
        start,
        "0",
    ];
    fs.writeFileSync(path.join(directory, "stat"), `${pid} (python3 (x)) ${fields.join(" ")}\n`);
}

// The development server as tools/run-dev starts it.
function dev_server() {
    add_process(
        10,
        "python3 ./manage.py rundjangoserver --settings=zproject.settings 127.0.0.1:9992",
    );
    add_process(11, "/venv/bin/python3 -m tornado.autoreload ./manage.py runtornado --autoreload");
    add_process(12, "python3 ./manage.py process_queue --all --automated", {start: "200"});
    add_process(13, "python3 ./tools/webpack --watch");
    fs.mkdirSync(path.join(proc, "sys/kernel/random"), {recursive: true});
    fs.writeFileSync(path.join(proc, "sys/kernel/random/boot_id"), "boot-1\n");
    fs.writeFileSync(path.join(proc, "meminfo"), "MemTotal: 9 kB\nMemAvailable:    4000000 kB\n");
}

beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), "armada-checks-health-"));
    proc = path.join(root, "proc");
    tree = path.join(root, "tree");
    fs.mkdirSync(proc);
    fs.mkdirSync(tree);
});

afterEach(() => {
    fs.rmSync(root, {recursive: true});
});

test("is_local knows the development hosts", () => {
    assert.equal(is_local("http://localhost:9991"), true);
    assert.equal(is_local("http://127.0.0.1:9991"), true);
    assert.equal(is_local("http://[::1]:9991"), true);
    assert.equal(is_local("http://zulip.zulipdev.com:9991"), true);
    assert.equal(is_local("http://zulipdev.com:9991"), true);
    assert.equal(is_local("http://armada.localhost"), true);
    assert.equal(is_local("https://armada.example.com"), false);
});

test("server_processes finds the server's processes running from the tree", () => {
    dev_server();
    add_process(20, "python3 ./manage.py process_queue --all", {cwd: path.join(root, "other")});
    add_process(21, "python3 ./manage.py runtornado", {cwd: null});
    add_process(22, "python3 ./manage.py rundjangoserver");
    fs.rmSync(path.join(proc, "22", "stat"));
    fs.mkdirSync(path.join(proc, "23"));
    fs.writeFileSync(path.join(proc, "locks"), "");
    assert.deepEqual(server_processes(proc, tree), [
        {role: "django", pid: 10, start: "100", state: "S"},
        {role: "tornado", pid: 11, start: "100", state: "S"},
        {role: "queue", pid: 12, start: "200", state: "S"},
    ]);
});

test("changed_files sees saves, syncs and deletions in the watched source only", async () => {
    for (const directory of ["web/src", "zerver/__pycache__", "tools/armada-checks/baseline"]) {
        fs.mkdirSync(path.join(tree, directory), {recursive: true});
    }
    fs.writeFileSync(path.join(tree, "web/src/a.ts"), "a");
    fs.writeFileSync(path.join(tree, "web/src/b.ts"), "b");
    fs.writeFileSync(path.join(tree, "zerver/c.py"), "c");
    await new Promise((resolve) => setTimeout(resolve, 30));
    const since = Date.now();
    assert.deepEqual(changed_files(tree, since), []);
    await new Promise((resolve) => setTimeout(resolve, 30));
    fs.writeFileSync(path.join(tree, "web/src/a.ts"), "saved");
    // A sync keeps the edit's own modification time; the change time moves.
    fs.utimesSync(path.join(tree, "web/src/a.ts"), new Date(0), new Date(0));
    fs.rmSync(path.join(tree, "zerver/c.py"));
    fs.writeFileSync(path.join(tree, "zerver/__pycache__/c.pyc"), "compiled");
    fs.writeFileSync(path.join(tree, "tools/armada-checks/baseline/kit.png"), "accepted");
    // The deletion shows as its directory's change.
    assert.deepEqual(changed_files(tree, since), ["web/src/a.ts", "zerver"]);
    assert.deepEqual(changed_files(tree, since, ["web/src/b.ts", "missing"]), []);
    assert.ok(WATCHED.includes("zerver"));
});

test("meminfo_available", () => {
    assert.equal(meminfo_available("MemAvailable:    2 kB\n"), 2048);
    assert.equal(meminfo_available("MemTotal: 1 kB\n"), null);
    assert.equal(meminfo_available(null), null);
});

function io({status = 200, error, bavail = 10 * 1024 ** 2} = {}) {
    return {
        proc,
        async fetch(url, options) {
            assert.ok(url.endsWith("/health"));
            assert.ok(options.signal);
            if (error) {
                throw error;
            }
            return {status};
        },
        statfs(target) {
            assert.equal(target, tree);
            return {bavail, bsize: 4096};
        },
    };
}

test("snapshot of a local server, and of a remote one", async () => {
    dev_server();
    const local = await snapshot({base: "http://localhost:9991", tree, io: io()});
    assert.deepEqual(local, {
        local: true,
        health: 200,
        boot_id: "boot-1",
        processes: server_processes(proc, tree),
        free_disk: 10 * 1024 ** 2 * 4096,
        available_memory: 4000000 * 1024,
    });
    const remote = await snapshot({
        base: "https://armada.example.com",
        tree,
        io: io({error: new Error("timed out")}),
    });
    assert.deepEqual(remote, {local: false, health: "timed out"});
    const odd = await snapshot({base: "https://armada.example.com", tree, io: io({error: "down"})});
    assert.equal(odd.health, "down");
    fs.rmSync(path.join(proc, "sys"), {recursive: true});
    fs.rmSync(path.join(proc, "meminfo"));
    const bare = await snapshot({base: "http://localhost:9991", tree, io: io()});
    assert.equal(bare.boot_id, null);
    assert.equal(bare.available_memory, null);
});

test("problems: a healthy server has none; each kind of trouble is named", async () => {
    dev_server();
    const healthy = await snapshot({base: "http://localhost:9991", tree, io: io()});
    assert.deepEqual(problems(healthy, tree), []);
    assert.deepEqual(problems({local: false, health: 200}, tree), []);
    assert.deepEqual(problems({local: false, health: 502}, tree), [
        "the server's /health answered 502",
    ]);

    // A dead queue processor, a zombie Tornado, a full disk, no memory.
    fs.rmSync(path.join(proc, "12"), {recursive: true});
    fs.rmSync(path.join(proc, "11"), {recursive: true});
    add_process(11, "python3 ./manage.py runtornado", {state: "Z"});
    const sick = {
        ...(await snapshot({base: "http://localhost:9991", tree, io: io()})),
        free_disk: MIN_FREE_DISK - 1,
        available_memory: MIN_AVAILABLE_MEMORY - 1,
    };
    assert.deepEqual(problems(sick, tree), [
        "the tornado process 11 is a zombie",
        `no queue process runs from ${tree} (ARMADA_SERVED_TREE names the tree the server runs from)`,
        "only 2.0 GiB of disk is free",
        "only 0.5 GiB of memory is available",
    ]);
});

test("changes: a restart of any server process, or a reboot", async () => {
    dev_server();
    const before = await snapshot({base: "http://localhost:9991", tree, io: io()});
    assert.deepEqual(changes(before, before), []);
    assert.deepEqual(changes({local: false}, before), []);
    assert.deepEqual(changes(before, {local: false}), []);
    // A save: Django's autoreloader starts a new child.
    fs.rmSync(path.join(proc, "10"), {recursive: true});
    add_process(30, "python3 ./manage.py rundjangoserver");
    fs.writeFileSync(path.join(proc, "sys/kernel/random/boot_id"), "boot-2\n");
    const after = await snapshot({base: "http://localhost:9991", tree, io: io()});
    assert.deepEqual(changes(before, after), [
        "the machine rebooted",
        "the django processes restarted (10@100 -> 30@100)",
    ]);
});

test("describe_changed_files", () => {
    assert.deepEqual(describe_changed_files([]), []);
    assert.deepEqual(describe_changed_files(["a", "b"]), [
        "the served tree changed during the check: a, b",
    ]);
    assert.deepEqual(describe_changed_files(["1", "2", "3", "4", "5", "6", "7"]), [
        "the served tree changed during the check: 1, 2, 3, 4, 5 and 2 more",
    ]);
});

test("response_problem: someone else's rate limit, or the server failing", () => {
    assert.equal(response_problem(200, "/json/messages"), null);
    assert.equal(response_problem(404, "/json/messages/9"), null);
    assert.equal(
        response_problem(429, "/json/messages"),
        "429 (rate limited) for /json/messages: someone else's traffic as this user",
    );
    assert.equal(response_problem(502, "/json/events"), "502 from the server for /json/events");
    assert.equal(response_problem(400, "/json/messages"), null);
    assert.equal(
        response_problem(400, "/json/events?queue_id=1"),
        "400 for /json/events?queue_id=1: the server lost the page's event queue (Tornado restarted)",
    );
});

test("foreign_events: what another person did reaches the page", () => {
    const me = 8;
    const events = [
        {type: "heartbeat"},
        {type: "presence", user_id: 9},
        {type: "message", message: {sender_id: me, display_recipient: "sandbox", subject: "x"}},
        {type: "message", message: {sender_id: 9, display_recipient: "click", subject: "demo"}},
        {type: "message", message: {sender_id: 10, display_recipient: [{id: 8}, {id: 10}]}},
        {type: "update_message", user_id: null, rendering_only: true},
        {type: "update_message", user_id: 31},
        {type: "reaction", user_id: me},
        {type: "reaction", user_id: 9},
        {type: "typing", sender: {user_id: 10}},
        {type: "message"},
    ];
    assert.deepEqual(foreign_events(events, me), [
        "a message in click > demo from user 9",
        "a direct message from user 10",
        "update_message from user 31",
        "reaction from user 9",
        "typing from user 10",
    ]);
    assert.deepEqual(foreign_events(undefined, me), []);
});

// Each failure the health checks exist for, planted where run.mjs looks for
// it, ends the run ENV-ERROR with status 3: never RED, even when a check
// failed because of it, and never GREEN.
const RED = {pass: 3, known_fail: 0, fail: 1, fixed: 0, error: 1, stale: 0};
const GREEN = {pass: 3, known_fail: 1, fail: 0, fixed: 0, error: 0, stale: 0};

function assert_env_error(lines) {
    assert.ok(lines.length > 0, "the planted failure was not seen");
    for (const totals of [RED, GREEN]) {
        assert.deepEqual(verdict_of(totals, lines.length), {
            verdict: "ENV-ERROR",
            green: false,
            status: 3,
        });
    }
}

test("verdict_of: green, red, and ENV-ERROR over both", () => {
    assert.deepEqual(verdict_of(GREEN, 0), {verdict: "GREEN", green: true, status: 0});
    assert.deepEqual(verdict_of(RED, 0), {verdict: "RED", green: false, status: 1});
    for (const key of ["fail", "fixed", "error", "stale"]) {
        assert.equal(verdict_of({...GREEN, [key]: 1}, 0).verdict, "RED");
    }
    assert.equal(verdict_of(GREEN, 1).verdict, "ENV-ERROR");
});

test("planted before the run: each makes the run ENV-ERROR", async () => {
    dev_server();
    const base = "http://localhost:9991";
    const healthy = await snapshot({base, tree, io: io()});
    assert.deepEqual(problems(healthy, tree), []);
    const planted = [
        [
            "the server answers /health with 502",
            async () => problems(await snapshot({base, tree, io: io({status: 502})}), tree),
        ],
        [
            "the server does not answer /health",
            async () =>
                problems(
                    await snapshot({base, tree, io: io({error: new Error("ECONNREFUSED")})}),
                    tree,
                ),
        ],
        [
            "the disk is nearly full",
            async () => problems(await snapshot({base, tree, io: io({bavail: 1024})}), tree),
        ],
        [
            "memory is nearly gone",
            async () => {
                fs.writeFileSync(path.join(proc, "meminfo"), "MemAvailable: 1000 kB\n");
                return problems(await snapshot({base, tree, io: io()}), tree);
            },
        ],
        [
            "Tornado is a zombie",
            async () => {
                fs.rmSync(path.join(proc, "11"), {recursive: true});
                add_process(11, "python3 ./manage.py runtornado", {state: "Z"});
                return problems(await snapshot({base, tree, io: io()}), tree);
            },
        ],
        [
            "the queue processor is gone",
            async () => {
                fs.rmSync(path.join(proc, "12"), {recursive: true});
                return problems(await snapshot({base, tree, io: io()}), tree);
            },
        ],
        [
            "Django is gone",
            async () => {
                fs.rmSync(path.join(proc, "10"), {recursive: true});
                return problems(await snapshot({base, tree, io: io()}), tree);
            },
        ],
        [
            "the server runs from another tree",
            async () => {
                const other = path.join(root, "other");
                const statfs = () => ({bavail: 1e9, bsize: 4096});
                return problems(await snapshot({base, tree: other, io: {...io(), statfs}}), other);
            },
        ],
    ];
    for (const [name, plant] of planted) {
        fs.rmSync(proc, {recursive: true});
        fs.mkdirSync(proc);
        dev_server();
        const lines = await plant();
        assert.ok(lines.length > 0, name);
        assert_env_error(lines);
    }
});

test("planted during a check: each makes the run ENV-ERROR", async () => {
    dev_server();
    fs.mkdirSync(path.join(tree, "web/src"), {recursive: true});
    fs.writeFileSync(path.join(tree, "web/src/a.ts"), "a");
    const base = "http://localhost:9991";
    const before = await snapshot({base, tree, io: io()});
    await new Promise((resolve) => setTimeout(resolve, 30));
    const since = Date.now();
    const quiet = {env_events: [], before, after: before, files: [], tree};
    assert.deepEqual(check_env_errors(quiet), []);
    assert.deepEqual(verdict_of(RED, check_env_errors(quiet).length).verdict, "RED");

    // What the pages saw (lib/harness.mjs keeps these lines).
    for (const line of [
        response_problem(429, "/json/messages"),
        response_problem(502, "/json/messages"),
        response_problem(400, "/json/events?queue_id=1"),
        ...foreign_events([{type: "typing", sender: {user_id: 9}}], 8),
    ]) {
        assert_env_error(check_env_errors({...quiet, env_events: [line]}));
    }

    // A save to the served tree while the check ran.
    await new Promise((resolve) => setTimeout(resolve, 30));
    fs.writeFileSync(path.join(tree, "web/src/a.ts"), "saved");
    const saved = check_env_errors({...quiet, files: changed_files(tree, since)});
    assert.deepEqual(saved, ["the served tree changed during the check: web/src/a.ts"]);
    assert_env_error(saved);

    // Django reloaded.
    fs.rmSync(path.join(proc, "10"), {recursive: true});
    add_process(31, "python3 ./manage.py rundjangoserver");
    const restarted = await snapshot({base, tree, io: io()});
    assert_env_error(check_env_errors({...quiet, after: restarted}));

    // The machine rebooted.
    fs.writeFileSync(path.join(proc, "sys/kernel/random/boot_id"), "boot-9\n");
    assert_env_error(check_env_errors({...quiet, after: await snapshot({base, tree, io: io()})}));

    // The server went down after the check.
    assert_env_error(
        check_env_errors({
            ...quiet,
            after: await snapshot({base, tree, io: io({status: 503})}),
        }),
    );
});
