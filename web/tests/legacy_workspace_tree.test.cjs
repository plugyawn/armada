"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const tree = zrequire("legacy_workspace_tree");

function entry(path, kind = "file", overrides = {}) {
    return {
        entry_id: `id:${path || "root"}`,
        path,
        name: path.split("/").at(-1) ?? "",
        kind,
        status: "clean",
        has_children: kind === "directory",
        size: kind === "file" ? 12 : null,
        ...overrides,
    };
}

function descriptor(revision = 1) {
    return {
        uuid: "workspace-a",
        display_root: "Workspace A",
        stream: "pear",
        topic: "work",
        branch: "work",
        head: "abc123",
        dirty: false,
        dirty_count: 0,
        workspace_revision: revision,
        root: entry("", "directory"),
        default_path: null,
        default_entry: null,
        capabilities: {
            read_file: true,
            write_file: true,
            refresh: true,
            create_entry: false,
            rename_entry: false,
            delete_entry: false,
            history: false,
        },
        limits: {text_file_max_bytes: 1000, raw_file_max_bytes: 2000},
    };
}

function page(state, directory, entries, next_cursor = null) {
    const started = tree.begin_pear_workspace_directory_request(state, directory.entry_id);
    assert.ok(started.request !== undefined);
    return {
        state: tree.receive_pear_workspace_directory_page(started.state, started.request, {
            workspace_uuid: state.workspaceId,
            workspace_revision: state.workspaceRevision,
            directory,
            entries,
            next_cursor,
        }),
        request: started.request,
    };
}

run_test("directory pages append once and preserve server order", () => {
    let state = tree.create_pear_workspace_tree(descriptor());
    const root = state.entries.get(state.rootId);
    const first = page(state, root, [entry("docs", "directory"), entry("README.md")], "next");
    state = first.state;
    assert.deepEqual(
        tree
            .pear_workspace_tree_window(state, {offset: 0, limit: 10})
            .rows.map((row) => row.entry.path),
        ["docs", "README.md"],
    );

    const second = page(state, root, [entry("README.md"), entry("src", "directory")]);
    state = second.state;
    assert.deepEqual(state.directories.get(state.rootId).childIds, [
        "id:docs",
        "id:README.md",
        "id:src",
    ]);
    assert.equal(state.directories.get(state.rootId).complete, true);
    assert.equal(
        tree.begin_pear_workspace_directory_request(state, state.rootId).request,
        undefined,
    );
});

run_test("stale pages and responses with non-child paths fail closed", () => {
    let state = tree.create_pear_workspace_tree(descriptor());
    const root = state.entries.get(state.rootId);
    const started = tree.begin_pear_workspace_directory_request(state, state.rootId);
    state = started.state;
    const newer = tree.begin_pear_workspace_directory_request(state, state.rootId);
    assert.equal(newer.request, undefined, "one directory request is single-flight");

    const stale_response = {
        workspace_uuid: "workspace-a",
        workspace_revision: 2,
        directory: root,
        entries: [entry("README.md")],
        next_cursor: null,
    };
    state = tree.receive_pear_workspace_directory_page(state, started.request, stale_response);
    assert.equal(state.ignoredStaleResponses, 1);
    assert.equal(state.directories.get(state.rootId).loading, true);

    const invalid_response = {...stale_response, workspace_revision: 1, entries: [entry("a/b.md")]};
    state = tree.receive_pear_workspace_directory_page(state, started.request, invalid_response);
    assert.equal(state.directories.get(state.rootId).error, "invalid_tree_page");
    assert.equal(state.entries.has("id:a/b.md"), false);
});

run_test("revision replacement invalidates IDs while preserving path preferences", () => {
    let state = tree.create_pear_workspace_tree(descriptor());
    const root = state.entries.get(state.rootId);
    state = page(state, root, [entry("docs", "directory"), entry("README.md")]).state;
    state = tree.set_pear_workspace_directory_expanded(state, "id:docs", true);
    state = tree.select_pear_workspace_entry(state, "id:README.md");

    const next = descriptor(2);
    next.root = {...next.root, entry_id: "revision-2-root"};
    state = tree.replace_pear_workspace_tree_revision(state, next);
    assert.equal(state.rootId, "revision-2-root");
    assert.equal(state.entries.has("id:README.md"), false);
    assert.equal(state.selectedId, undefined);
    assert.equal(state.selectedPath, "README.md");
    assert.equal(state.expandedPaths.has("docs"), true);

    const stale = tree.replace_pear_workspace_tree_revision(state, descriptor(1));
    assert.equal(stale.workspaceRevision, 2);
    assert.equal(stale.ignoredStaleResponses, 1);
});

run_test("search reveal adds only the ancestor chain and expands it", () => {
    let state = tree.create_pear_workspace_tree(descriptor());
    const docs = entry("docs", "directory");
    const guide = entry("docs/guide", "directory");
    const target = entry("docs/guide/start.md");
    state = tree.reveal_pear_workspace_search_result(
        state,
        {
            workspace_uuid: "workspace-a",
            workspace_revision: 1,
            query: "start",
            entries: [{...target, ancestors: [state.entries.get(state.rootId), docs, guide]}],
        },
        target.entry_id,
    );

    assert.equal(state.selectedId, target.entry_id);
    assert.deepEqual([...state.expandedPaths], ["", "docs", "docs/guide"]);
    assert.deepEqual(
        tree
            .pear_workspace_tree_window(state, {offset: 0, limit: 10})
            .rows.map((row) => [row.entry.path, row.depth]),
        [
            ["docs", 0],
            ["docs/guide", 1],
            ["docs/guide/start.md", 2],
        ],
    );
});

run_test("windowing never materializes more rows than requested", () => {
    let state = tree.create_pear_workspace_tree(descriptor());
    const root = state.entries.get(state.rootId);
    const entries = Array.from({length: 10_000}, (_value, index) =>
        entry(`file-${String(index).padStart(5, "0")}.txt`),
    );
    state = page(state, root, entries).state;
    const window = tree.pear_workspace_tree_window(state, {offset: 9_000, limit: 200});
    assert.equal(window.rows.length, 200);
    assert.equal(window.rows[0].entry.path, "file-09000.txt");
    assert.equal(window.rows.at(-1).entry.path, "file-09199.txt");
    assert.equal(window.hasMore, true);
});

run_test("failed requests remain retryable and stale failures are ignored", () => {
    let state = tree.create_pear_workspace_tree(descriptor());
    const started = tree.begin_pear_workspace_directory_request(state, state.rootId);
    state = tree.fail_pear_workspace_directory_request(started.state, started.request, "offline");
    assert.equal(state.directories.get(state.rootId).error, "offline");
    assert.equal(state.directories.get(state.rootId).loading, false);

    const retry = tree.begin_pear_workspace_directory_request(state, state.rootId);
    assert.equal(retry.request.requestGeneration, 2);
    state = tree.fail_pear_workspace_directory_request(retry.state, started.request, "late");
    assert.equal(state.ignoredStaleResponses, 1);
    assert.equal(state.directories.get(state.rootId).loading, true);
});
