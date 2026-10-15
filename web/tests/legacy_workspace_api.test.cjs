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
    del: mock_request("del"),
    get: mock_request("get"),
    patch: mock_request("patch"),
    post: mock_request("post"),
    put: mock_request("put"),
});

const workspace_api = zrequire("legacy_workspace_api");

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
    display_root: "armada-sandbox / first run",
    stream: "armada-sandbox",
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
    workspace_api.get_armada_workspace(descriptor.uuid, result.callbacks);
    const request = last_request();
    assert.equal(request.method, "get");
    assert.equal(request.options.url, `/json/armada/workspaces/${descriptor.uuid}`);
    request.options.success({armada_workspace: descriptor});
    assert.deepEqual(result.successes, [descriptor]);
    assert.deepEqual(result.errors, []);

    const streamed_raw = {
        ...descriptor,
        limits: {...descriptor.limits, raw_file_max_bytes: null},
    };
    request.options.success({armada_workspace: streamed_raw});
    assert.deepEqual(result.successes.at(-1), streamed_raw);

    request.options.success({armada_workspace: {...descriptor, workspace_revision: "wrong"}});
    assert.equal(result.errors.at(-1).kind, "invalid_response");
});

run_test("tree and search serialize only present query parameters", () => {
    requests = [];
    const tree = callbacks();
    workspace_api.get_armada_workspace_tree(
        descriptor.uuid,
        {directoryId: root_entry.entry_id, pageSize: 200},
        tree.callbacks,
    );
    let request = last_request();
    assert.deepEqual(request.options.data, {directory_id: root_entry.entry_id, page_size: 200});
    request.options.success({
        armada_workspace_tree: {
            workspace_uuid: descriptor.uuid,
            workspace_revision: 7,
            directory: root_entry,
            entries: [entry],
            next_cursor: null,
        },
    });
    assert.deepEqual(tree.successes[0].entries, [entry]);

    const search = callbacks();
    workspace_api.search_armada_workspace(descriptor.uuid, "read", undefined, search.callbacks);
    request = last_request();
    assert.deepEqual(request.options.data, {q: "read"});
    request.options.success({
        armada_workspace_search: {
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
    workspace_api.get_armada_workspace_file(descriptor.uuid, entry.entry_id, get.callbacks);
    let request = last_request();
    assert.deepEqual(request.options.data, {entry_id: entry.entry_id});
    request.options.success({armada_workspace_file: file});
    assert.deepEqual(get.successes, [file]);

    const put = callbacks();
    workspace_api.put_armada_workspace_file(
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
        armada_workspace_file: {
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
    workspace_api.put_armada_workspace_file(
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
    workspace_api.get_armada_workspace(descriptor.uuid, transport.callbacks);
    last_request().options.error({status: 503, statusText: "Unavailable", responseJSON: {}});
    assert.deepEqual(transport.errors, [{kind: "transport", status: 503, message: "Unavailable"}]);
});

run_test("entry mutations preserve revision and path identity contracts", () => {
    requests = [];
    const created = callbacks();
    workspace_api.create_armada_workspace_entry(
        descriptor.uuid,
        {
            destinationPath: "notes/today.md",
            kind: "file",
            workspaceRevision: 7,
            content: "# Today\n",
        },
        created.callbacks,
    );
    let request = last_request();
    assert.equal(request.method, "post");
    assert.equal(request.options.url, `/json/armada/workspaces/${descriptor.uuid}/entries`);
    assert.deepEqual(request.options.data, {
        destination_path: "notes/today.md",
        kind: "file",
        workspace_revision: 7,
        content: "# Today\n",
    });
    const created_entry = {
        ...entry,
        entry_id: "created-entry",
        path: "notes/today.md",
        name: "today.md",
        status: "untracked",
    };
    request.options.success({
        armada_workspace_mutation: {
            operation: "create",
            workspace_uuid: descriptor.uuid,
            workspace_revision: 8,
            previous_entry_id: null,
            entry: created_entry,
            path: created_entry.path,
        },
    });
    assert.equal(created.successes[0].workspace_revision, 8);
    assert.equal(created.successes[0].entry.entry_id, "created-entry");

    const renamed = callbacks();
    workspace_api.rename_armada_workspace_entry(
        descriptor.uuid,
        {
            entryId: created_entry.entry_id,
            expectedPath: created_entry.path,
            destinationPath: "notes/archive.md",
            workspaceRevision: 8,
        },
        renamed.callbacks,
    );
    request = last_request();
    assert.equal(request.method, "patch");
    assert.deepEqual(request.options.data, {
        entry_id: "created-entry",
        expected_path: "notes/today.md",
        destination_path: "notes/archive.md",
        workspace_revision: 8,
    });

    const deleted = callbacks();
    workspace_api.delete_armada_workspace_entry(
        descriptor.uuid,
        {
            entryId: "renamed-entry",
            expectedPath: "notes/archive.md",
            workspaceRevision: 9,
            recursive: true,
            confirmIrreversible: true,
        },
        deleted.callbacks,
    );
    request = last_request();
    assert.equal(request.method, "del");
    assert.deepEqual(request.options.data, {
        entry_id: "renamed-entry",
        expected_path: "notes/archive.md",
        workspace_revision: 9,
        recursive: true,
        confirm_irreversible: true,
    });
    request.options.success({
        armada_workspace_mutation: {
            operation: "delete",
            workspace_uuid: descriptor.uuid,
            workspace_revision: 10,
            previous_entry_id: "renamed-entry",
            entry: null,
            path: "notes/archive.md",
            removed_entries: 1,
        },
    });
    assert.equal(deleted.successes[0].removed_entries, 1);
});

run_test("entry history serializes pagination and rejects malformed commits", () => {
    requests = [];
    const history = callbacks();
    workspace_api.get_armada_workspace_history(
        descriptor.uuid,
        entry.entry_id,
        {cursor: "opaque-cursor", limit: 25},
        history.callbacks,
    );
    const request = last_request();
    assert.equal(request.method, "get");
    assert.equal(request.options.url, `/json/armada/workspaces/${descriptor.uuid}/history`);
    assert.deepEqual(request.options.data, {
        entry_id: entry.entry_id,
        cursor: "opaque-cursor",
        limit: 25,
    });
    request.options.success({
        armada_workspace_history: {
            workspace_uuid: descriptor.uuid,
            workspace_revision: 7,
            entry,
            commits: [
                {
                    commit: "abc123",
                    authored_at: 1_700_000_000,
                    author_name: "Pear",
                    subject: "Document the workspace",
                },
            ],
            next_cursor: null,
        },
    });
    assert.equal(history.successes[0].commits[0].subject, "Document the workspace");

    request.options.success({
        armada_workspace_history: {
            workspace_uuid: descriptor.uuid,
            workspace_revision: 7,
            entry,
            commits: [{commit: "abc123", authored_at: "yesterday"}],
            next_cursor: null,
        },
    });
    assert.equal(history.errors.at(-1).kind, "invalid_response");
});

run_test("request guard aborts superseded work and rejects late attachment", () => {
    const guard = workspace_api.create_armada_workspace_request_guard();
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
        workspace_api.armada_workspace_raw_url("workspace/with slash", "entry + slash/", true),
        "/json/armada/workspaces/workspace%2Fwith%20slash/raw?entry_id=entry+%2B+slash%2F&download=true",
    );
});
