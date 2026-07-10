"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let requests = [];
function mock_request(method) {
    return (options) => {
        const request = {
            method,
            options,
            aborted: false,
            abort() {
                this.aborted = true;
            },
        };
        requests.push(request);
        return request;
    };
}
mock_esm("../src/channel", {
    get: mock_request("get"),
    post: mock_request("post"),
    put: mock_request("put"),
});

const workspace_api = zrequire("pear_workspace_api");

const entry = {
    entry_id: "opaque-entry",
    path: "README.md",
    name: "README.md",
    kind: "file",
    status: "clean",
    has_children: false,
    size: 12,
};

const root_entry = {
    entry_id: "opaque-root",
    path: "",
    name: "",
    kind: "directory",
    status: "clean",
    has_children: true,
    size: null,
};

const descriptor = {
    uuid: "12345678-1234-5678-1234-567812345678",
    display_root: "pear-sandbox / first run",
    stream: "pear-sandbox",
    topic: "first run",
    branch: "first-run",
    head: "abc123",
    dirty: false,
    dirty_count: 0,
    workspace_revision: 7,
    root: root_entry,
    default_path: "README.md",
    default_entry: entry,
    capabilities: {
        read_file: true,
        write_file: true,
        refresh: true,
        create_entry: false,
        rename_entry: false,
        delete_entry: false,
        history: false,
    },
    limits: {text_file_max_bytes: 1_000_000, raw_file_max_bytes: 16_000_000},
};

const file = {
    workspace_uuid: descriptor.uuid,
    workspace_revision: 7,
    entry_id: entry.entry_id,
    path: entry.path,
    content: "# README\n",
    version: "a".repeat(64),
    size: 9,
    mime_type: "text/markdown",
    language: "markdown",
    editable: true,
    rich_mode_eligible: true,
    max_bytes: 1_000_000,
};

function callbacks() {
    const successes = [];
    const errors = [];
    return {
        successes,
        errors,
        callbacks: {
            success(value) {
                successes.push(value);
            },
            error(error) {
                errors.push(error);
            },
        },
    };
}

function last_request() {
    const request = requests.at(-1);
    assert.ok(request !== undefined);
    return request;
}

run_test("descriptor parsing is strict and returns only the resource", () => {
    requests = [];
    const result = callbacks();
    workspace_api.get_pear_workspace(descriptor.uuid, result.callbacks);
    const request = last_request();
    assert.equal(request.method, "get");
    assert.equal(request.options.url, `/json/pear/workspaces/${descriptor.uuid}`);
    request.options.success({pear_workspace: descriptor});
    assert.deepEqual(result.successes, [descriptor]);
    assert.deepEqual(result.errors, []);

    request.options.success({pear_workspace: {...descriptor, workspace_revision: "wrong"}});
    assert.equal(result.errors.at(-1).kind, "invalid_response");
});

run_test("tree and search serialize only present query parameters", () => {
    requests = [];
    const tree = callbacks();
    workspace_api.get_pear_workspace_tree(
        descriptor.uuid,
        {directoryId: root_entry.entry_id, pageSize: 200},
        tree.callbacks,
    );
    let request = last_request();
    assert.deepEqual(request.options.data, {directory_id: root_entry.entry_id, page_size: 200});
    request.options.success({
        pear_workspace_tree: {
            workspace_uuid: descriptor.uuid,
            workspace_revision: 7,
            directory: root_entry,
            entries: [entry],
            next_cursor: null,
        },
    });
    assert.deepEqual(tree.successes[0].entries, [entry]);

    const search = callbacks();
    workspace_api.search_pear_workspace(descriptor.uuid, "read", undefined, search.callbacks);
    request = last_request();
    assert.deepEqual(request.options.data, {q: "read"});
    request.options.success({
        pear_workspace_search: {
            workspace_uuid: descriptor.uuid,
            workspace_revision: 7,
            query: "read",
            entries: [{...entry, ancestors: []}],
        },
    });
    assert.equal(search.successes[0].entries[0].entry_id, entry.entry_id);
});

run_test("file GET and PUT preserve the identity and edit revision contract", () => {
    requests = [];
    const get = callbacks();
    workspace_api.get_pear_workspace_file(descriptor.uuid, entry.entry_id, get.callbacks);
    let request = last_request();
    assert.deepEqual(request.options.data, {entry_id: entry.entry_id});
    request.options.success({pear_workspace_file: file});
    assert.deepEqual(get.successes, [file]);

    const put = callbacks();
    workspace_api.put_pear_workspace_file(
        descriptor.uuid,
        {
            entryId: entry.entry_id,
            expectedPath: entry.path,
            content: "updated\n",
            baseVersion: file.version,
            workspaceRevision: 7,
            editRevision: 4,
        },
        put.callbacks,
    );
    request = last_request();
    assert.equal(request.method, "put");
    assert.deepEqual(request.options.data, {
        entry_id: entry.entry_id,
        expected_path: entry.path,
        content: "updated\n",
        base_version: file.version,
        workspace_revision: 7,
        edit_revision: 4,
    });
    request.options.success({
        pear_workspace_file: {
            ...file,
            workspace_revision: 8,
            entry_id: "new-revision-entry",
            edit_revision: 4,
        },
    });
    assert.equal(put.successes[0].entry_id, "new-revision-entry");
    assert.equal(put.successes[0].edit_revision, 4);
});

run_test("typed conflicts and transport failures stay distinct", () => {
    requests = [];
    const conflict = callbacks();
    workspace_api.put_pear_workspace_file(
        descriptor.uuid,
        {
            entryId: entry.entry_id,
            expectedPath: entry.path,
            content: "local",
            baseVersion: file.version,
            workspaceRevision: 7,
            editRevision: 2,
        },
        conflict.callbacks,
    );
    last_request().options.error({
        status: 409,
        statusText: "Conflict",
        responseJSON: {
            result: "error",
            msg: "The file changed.",
            error_code: "version_conflict",
            workspace_revision: 8,
            remote_file: {...file, workspace_revision: 8, entry_id: "remote-entry"},
        },
    });
    assert.equal(conflict.errors[0].kind, "server");
    assert.equal(conflict.errors[0].error.error_code, "version_conflict");
    assert.equal(conflict.errors[0].error.remote_file.entry_id, "remote-entry");

    const transport = callbacks();
    workspace_api.get_pear_workspace(descriptor.uuid, transport.callbacks);
    last_request().options.error({status: 503, statusText: "Unavailable", responseJSON: {}});
    assert.deepEqual(transport.errors, [{kind: "transport", status: 503, message: "Unavailable"}]);
});

run_test("request guard aborts superseded work and rejects late attachment", () => {
    const guard = workspace_api.create_pear_workspace_request_guard();
    const first = {
        aborted: 0,
        abort() {
            this.aborted += 1;
        },
    };
    const second = {
        aborted: 0,
        abort() {
            this.aborted += 1;
        },
    };
    const late = {
        aborted: 0,
        abort() {
            this.aborted += 1;
        },
    };

    const generation_1 = guard.begin("file");
    assert.equal(guard.attach("file", generation_1, first), true);
    const generation_2 = guard.begin("file");
    assert.equal(first.aborted, 1);
    assert.equal(guard.isCurrent("file", generation_1), false);
    assert.equal(guard.attach("file", generation_1, late), false);
    assert.equal(late.aborted, 1);
    assert.equal(guard.attach("file", generation_2, second), true);
    assert.equal(guard.settle("file", generation_1), false);
    assert.equal(guard.settle("file", generation_2), true);
    assert.equal(second.aborted, 0);

    const tree_generation = guard.begin("tree");
    guard.attach("tree", tree_generation, second);
    guard.cancelAll();
    assert.equal(second.aborted, 1);
});

run_test("raw URLs are encoded and opt into attachment disposition", () => {
    assert.equal(
        workspace_api.pear_workspace_raw_url("workspace/with slash", "entry + slash/", true),
        "/json/pear/workspaces/workspace%2Fwith%20slash/raw?entry_id=entry+%2B+slash%2F&download=true",
    );
});
