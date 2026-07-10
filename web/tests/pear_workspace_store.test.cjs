"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const workspace_store = zrequire("pear_workspace_store");

function reduce(state, action) {
    return workspace_store.reduce_pear_workspace_store(state, action);
}

function source_edit(state, id, text) {
    const changed = reduce(state, {type: "source_changed", ...id});
    const document = workspace_store.get_pear_workspace_document(changed, id);
    assert.ok(document !== undefined);
    return reduce(changed, {
        type: "source_flushed",
        ...id,
        editRevision: document.editRevision,
        text,
    });
}

function activate(state, workspace_id, workspace_revision = 1) {
    return reduce(state, {
        type: "activate_workspace",
        workspaceId: workspace_id,
        workspaceRevision: workspace_revision,
    });
}

function open_document(state, workspace_id, path) {
    const entry_id = `entry:${path}`;
    const next = reduce(state, {
        type: "open_document",
        workspaceId: workspace_id,
        entryId: entry_id,
        path,
    });
    const workspace = workspace_store.get_pear_workspace(next, workspace_id);
    assert.ok(workspace !== undefined);
    return {state: next, request_generation: workspace.requestGeneration};
}

function load_document(state, workspace_id, path, request_generation, overrides = {}) {
    return reduce(state, {
        type: "document_loaded",
        workspaceId: workspace_id,
        entryId: `entry:${path}`,
        path,
        requestGeneration: request_generation,
        workspaceRevision: 1,
        baseVersion: "version-1",
        maxBytes: 200_000,
        text: "initial\n",
        ...overrides,
    });
}

function identity(workspace_id, path, request_generation) {
    return {
        workspaceId: workspace_id,
        entryId: `entry:${path}`,
        path,
        requestGeneration: request_generation,
    };
}

function get_document(state, workspace_id, path, request_generation) {
    const document = workspace_store.get_pear_workspace_document(
        state,
        identity(workspace_id, path, request_generation),
    );
    assert.ok(document !== undefined);
    return document;
}

function loaded_store(workspace_id = "workspace-a", path = "README.md") {
    let state = workspace_store.create_pear_workspace_store();
    state = activate(state, workspace_id);
    const opened = open_document(state, workspace_id, path);
    state = load_document(opened.state, workspace_id, path, opened.request_generation);
    return {state, request_generation: opened.request_generation};
}

run_test("stale document read cannot replace a new workspace", () => {
    let state = workspace_store.create_pear_workspace_store();
    state = activate(state, "workspace-a");
    const old_request = open_document(state, "workspace-a", "README.md");

    state = activate(old_request.state, "workspace-b");
    const new_request = open_document(state, "workspace-b", "notes.md");
    state = load_document(
        new_request.state,
        "workspace-b",
        "notes.md",
        new_request.request_generation,
        {text: "workspace b\n"},
    );

    state = load_document(state, "workspace-a", "README.md", old_request.request_generation, {
        text: "late workspace a\n",
    });

    assert.equal(state.activeWorkspaceId, "workspace-b");
    assert.equal(
        get_document(state, "workspace-b", "notes.md", new_request.request_generation).currentText,
        "workspace b\n",
    );
    assert.equal(workspace_store.get_pear_workspace(state, "workspace-a").documents.size, 0);
    assert.equal(state.ignoredStaleResponses, 1);
});

run_test("a superseded path response is ignored in the same workspace", () => {
    let state = workspace_store.create_pear_workspace_store();
    state = activate(state, "workspace-a");
    const first = open_document(state, "workspace-a", "first.md");
    const second = open_document(first.state, "workspace-a", "second.md");

    state = load_document(second.state, "workspace-a", "first.md", first.request_generation);
    assert.equal(workspace_store.get_pear_workspace(state, "workspace-a").documents.size, 0);
    assert.equal(state.ignoredStaleResponses, 1);
});

run_test("leaving and returning still invalidates the old read generation", () => {
    let state = workspace_store.create_pear_workspace_store();
    state = activate(state, "workspace-a");
    const old_request = open_document(state, "workspace-a", "README.md");
    state = activate(old_request.state, "workspace-b");
    state = activate(state, "workspace-a");
    state = load_document(state, "workspace-a", "README.md", old_request.request_generation);

    assert.equal(workspace_store.get_pear_workspace(state, "workspace-a").documents.size, 0);
    assert.equal(state.ignoredStaleResponses, 1);
});

run_test("delayed Rich serialization cannot become savable text", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);

    state = reduce(state, {
        type: "set_rich_eligibility",
        ...id,
        editRevision: 0,
        eligibility: "supported",
    });
    state = reduce(state, {type: "set_editor_mode", ...id, mode: "rich"});
    state = reduce(state, {type: "rich_edited", ...id});
    state = reduce(state, {type: "serialization_started", ...id, editRevision: 1});
    state = reduce(state, {type: "rich_edited", ...id});

    state = reduce(state, {
        type: "serialization_succeeded",
        ...id,
        editRevision: 1,
        text: "stale rich text\n",
    });
    let document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.currentText, "initial\n");
    assert.equal(document.serializedRevision, 0);
    assert.deepEqual(document.serialization, {
        state: "scheduled",
        requestedRevision: 2,
        error: undefined,
    });
    assert.equal(state.ignoredStaleResponses, 1);

    state = reduce(state, {type: "save_started", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.saveInFlight, undefined);

    state = reduce(state, {type: "serialization_started", ...id, editRevision: 2});
    state = reduce(state, {
        type: "serialization_succeeded",
        ...id,
        editRevision: 2,
        text: "latest rich text\n",
    });
    state = reduce(state, {type: "save_started", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.deepEqual(document.saveInFlight, {
        workspaceId: "workspace-a",
        workspaceRevision: 1,
        entryId: "entry:README.md",
        path: "README.md",
        requestGeneration: loaded.request_generation,
        editRevision: 2,
        baseVersion: "version-1",
        text: "latest rich text\n",
    });
});

run_test("an older save success cannot clear a newer Source edit", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);

    state = source_edit(state, id, "first edit\n");
    state = reduce(state, {type: "save_started", ...id});
    state = source_edit(state, id, "second edit\n");
    state = reduce(state, {
        type: "save_succeeded",
        ...id,
        editRevision: 1,
        newEntryId: "entry:README.md",
        newVersion: "version-2",
        workspaceRevision: 2,
    });

    let document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.currentText, "second edit\n");
    assert.equal(document.savedText, "first edit\n");
    assert.equal(document.acknowledgedRevision, 1);
    assert.equal(document.editRevision, 2);
    assert.equal(document.state, "dirty");

    state = reduce(state, {type: "save_started", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.saveInFlight.editRevision, 2);
    assert.equal(document.saveInFlight.baseVersion, "version-2");
});

run_test("an inactive document save acknowledges only its original session", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const a_id = identity("workspace-a", "README.md", loaded.request_generation);
    state = source_edit(state, a_id, "workspace a edit\n");
    state = reduce(state, {type: "save_started", ...a_id});

    state = activate(state, "workspace-b");
    const b_open = open_document(state, "workspace-b", "README.md");
    state = load_document(b_open.state, "workspace-b", "README.md", b_open.request_generation, {
        text: "workspace b\n",
    });
    const b_id = identity("workspace-b", "README.md", b_open.request_generation);
    state = source_edit(state, b_id, "workspace b edit\n");

    state = reduce(state, {
        type: "save_succeeded",
        ...a_id,
        editRevision: 1,
        newEntryId: "entry:README.md",
        newVersion: "a-version-2",
        workspaceRevision: 2,
    });

    assert.equal(
        get_document(state, "workspace-a", "README.md", loaded.request_generation).state,
        "clean",
    );
    const b_document = get_document(state, "workspace-b", "README.md", b_open.request_generation);
    assert.equal(b_document.currentText, "workspace b edit\n");
    assert.equal(b_document.savedText, "workspace b\n");
    assert.equal(b_document.state, "dirty");
    assert.equal(state.activeWorkspaceId, "workspace-b");
});

run_test("stale save callbacks cannot consume the single in-flight save", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = source_edit(state, id, "edit\n");
    state = reduce(state, {type: "save_started", ...id});

    state = reduce(state, {
        type: "save_succeeded",
        ...id,
        editRevision: 999,
        newEntryId: "entry:README.md",
        newVersion: "wrong",
        workspaceRevision: 999,
    });
    const document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.saveInFlight.editRevision, 1);
    assert.equal(document.baseVersion, "version-1");
    assert.equal(state.ignoredStaleResponses, 1);
});

run_test("a revision advance invalidates every session except the saved identity", () => {
    const loaded = loaded_store("workspace-a", "A.md");
    let {state} = loaded;
    const a_id = identity("workspace-a", "A.md", loaded.request_generation);
    state = source_edit(state, a_id, "A edit\n");
    state = reduce(state, {type: "save_started", ...a_id});

    const b_open = open_document(state, "workspace-a", "B.md");
    state = load_document(b_open.state, "workspace-a", "B.md", b_open.request_generation, {
        text: "B content\n",
    });
    state = reduce(state, {
        type: "save_succeeded",
        ...a_id,
        editRevision: 1,
        newEntryId: "entry:A.md:v2",
        newVersion: "version-2",
        workspaceRevision: 2,
    });
    const generation_before_invalidation = workspace_store.get_pear_workspace(
        state,
        "workspace-a",
    ).requestGeneration;

    state = reduce(state, {
        type: "invalidate_workspace_revision",
        workspaceId: "workspace-a",
        workspaceRevision: 2,
        preservedEntryId: "entry:A.md:v2",
    });
    const workspace = workspace_store.get_pear_workspace(state, "workspace-a");
    assert.deepEqual([...workspace.documents.keys()], ["entry:A.md:v2"]);
    assert.equal(workspace.activeEntryId, undefined);
    assert.equal(workspace.pendingDocumentRequest, undefined);
    assert.equal(workspace.requestGeneration, generation_before_invalidation + 1);
    assert.equal(
        workspace_store.get_pear_workspace_document(
            state,
            identity("workspace-a", "B.md", b_open.request_generation),
        ),
        undefined,
    );
});

run_test("workspace mode preference is restored without creating a document session", () => {
    let state = activate(workspace_store.create_pear_workspace_store(), "workspace-a");
    state = reduce(state, {
        type: "set_workspace_mode_preference",
        workspaceId: "workspace-a",
        mode: "rich",
    });
    const workspace = workspace_store.get_pear_workspace(state, "workspace-a");
    assert.equal(workspace.markdownModePreference, "rich");
    assert.equal(workspace.documents.size, 0);
});

run_test("an external revision advance can invalidate every cached document", () => {
    const loaded = loaded_store("workspace-a", "A.md");
    let state = activate(loaded.state, "workspace-a", 2);
    state = reduce(state, {
        type: "invalidate_workspace_revision",
        workspaceId: "workspace-a",
        workspaceRevision: 2,
    });
    const workspace = workspace_store.get_pear_workspace(state, "workspace-a");
    assert.equal(workspace.activeEntryId, undefined);
    assert.equal(workspace.documents.size, 0);
    assert.equal(workspace.pendingDocumentRequest, undefined);
});

run_test("conflict Overwrite keeps the latest edit and adopts only the remote base", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = source_edit(state, id, "first edit\n");
    state = reduce(state, {type: "save_started", ...id});
    state = source_edit(state, id, "latest edit\n");
    state = reduce(state, {
        type: "save_conflicted",
        ...id,
        editRevision: 1,
        remoteText: "remote edit\n",
        remoteVersion: "remote-version",
        remoteEntryId: "entry:README.md",
        remoteWorkspaceRevision: 7,
    });

    let document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.state, "conflict");
    assert.equal(document.currentText, "latest edit\n");
    state = source_edit(state, id, "edited after conflict\n");
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.state, "conflict");
    assert.equal(document.conflict.remoteVersion, "remote-version");
    state = reduce(state, {type: "save_started", ...id});
    assert.equal(
        get_document(state, "workspace-a", "README.md", loaded.request_generation).saveInFlight,
        undefined,
    );

    state = reduce(state, {type: "conflict_overwrite", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.baseVersion, "remote-version");
    assert.equal(document.savedText, "remote edit\n");
    assert.equal(document.currentText, "edited after conflict\n");
    assert.equal(document.state, "dirty");
    assert.equal(workspace_store.get_pear_workspace(state, "workspace-a").workspaceRevision, 7);

    state = reduce(state, {type: "save_started", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.saveInFlight.baseVersion, "remote-version");
    assert.equal(document.saveInFlight.editRevision, 3);
});

run_test("conflict Reload discards the scratch edit and restores clean view state", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = reduce(state, {
        type: "source_view_changed",
        ...id,
        selection: {anchor: 100, head: 120},
        scrollTop: 48,
    });
    state = source_edit(state, id, "local edit\n");
    state = reduce(state, {type: "save_started", ...id});
    state = reduce(state, {
        type: "save_conflicted",
        ...id,
        editRevision: 1,
        remoteText: "remote\n",
        remoteVersion: "remote-version",
        remoteEntryId: "entry:README.md",
        remoteWorkspaceRevision: 4,
    });
    state = reduce(state, {type: "conflict_reload", ...id});

    const document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.currentText, "remote\n");
    assert.equal(document.savedText, "remote\n");
    assert.equal(document.baseVersion, "remote-version");
    assert.equal(document.state, "clean");
    assert.equal(document.acknowledgedRevision, document.editRevision);
    assert.deepEqual(document.sourceView, {
        selection: {anchor: 7, head: 7},
        scrollTop: 48,
    });
});

run_test("mode changes preserve per-mode view state and wait for serialization", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);

    state = reduce(state, {type: "set_editor_mode", ...id, mode: "rich"});
    assert.equal(
        get_document(state, "workspace-a", "README.md", loaded.request_generation).editorMode,
        "source",
    );

    state = reduce(state, {
        type: "set_rich_eligibility",
        ...id,
        editRevision: 0,
        eligibility: "supported",
    });
    state = reduce(state, {
        type: "source_view_changed",
        ...id,
        selection: {anchor: 2, head: 4},
        scrollTop: 20,
    });
    state = reduce(state, {type: "set_editor_mode", ...id, mode: "rich"});
    state = reduce(state, {
        type: "rich_view_changed",
        ...id,
        selection: {anchor: 3, head: 3},
        scrollTop: 30,
    });
    state = reduce(state, {type: "rich_edited", ...id});
    state = reduce(state, {type: "set_editor_mode", ...id, mode: "source"});
    assert.equal(
        get_document(state, "workspace-a", "README.md", loaded.request_generation).editorMode,
        "rich",
    );

    state = reduce(state, {type: "serialization_started", ...id, editRevision: 1});
    state = reduce(state, {
        type: "serialization_succeeded",
        ...id,
        editRevision: 1,
        text: "rich edit\n",
    });
    state = reduce(state, {type: "set_editor_mode", ...id, mode: "source"});
    const document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.editorMode, "source");
    assert.deepEqual(document.sourceView, {
        selection: {anchor: 2, head: 4},
        scrollTop: 20,
    });
    assert.deepEqual(document.richView, {
        selection: {anchor: 3, head: 3},
        scrollTop: 30,
    });
});

run_test("serialization failure is visible and blocks save", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = reduce(state, {
        type: "set_rich_eligibility",
        ...id,
        editRevision: 0,
        eligibility: "supported",
    });
    state = reduce(state, {type: "set_editor_mode", ...id, mode: "rich"});
    state = reduce(state, {type: "rich_edited", ...id});
    state = reduce(state, {type: "serialization_started", ...id, editRevision: 1});
    state = reduce(state, {
        type: "serialization_failed",
        ...id,
        editRevision: 1,
        error: "Could not serialize Markdown.",
    });
    let document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.state, "error");
    assert.equal(document.error, "Could not serialize Markdown.");

    state = reduce(state, {type: "serialization_requested", ...id});
    state = reduce(state, {type: "save_started", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.serialization.state, "scheduled");
    assert.equal(document.state, "dirty");
    assert.equal(document.saveInFlight, undefined);
});

run_test("a stale adapter cannot edit after its mode is inactive", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);

    state = reduce(state, {type: "rich_edited", ...id});
    let document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.editRevision, 0);
    assert.equal(state.ignoredStaleResponses, 1);

    state = reduce(state, {
        type: "set_rich_eligibility",
        ...id,
        editRevision: 0,
        eligibility: "supported",
    });
    state = reduce(state, {type: "set_editor_mode", ...id, mode: "rich"});
    state = reduce(state, {type: "source_changed", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.currentText, "initial\n");
    assert.equal(document.editRevision, 0);
    assert.equal(state.ignoredStaleResponses, 2);
});

run_test("the authoritative byte limit blocks a save visibly", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    const workspace = workspace_store.get_pear_workspace(state, "workspace-a");
    const document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    const documents = new Map(workspace.documents);
    documents.set("entry:README.md", {...document, maxBytes: 3});
    state = {
        ...state,
        workspaces: new Map(state.workspaces).set("workspace-a", {...workspace, documents}),
    };

    state = source_edit(state, id, "four");
    state = reduce(state, {type: "save_started", ...id});
    const blocked = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(blocked.state, "error");
    assert.equal(blocked.error, "too_large");
    assert.equal(blocked.saveInFlight, undefined);
});

run_test("Source reports dirty immediately but cannot save before an exact flush", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = reduce(state, {type: "source_changed", ...id});

    let document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.state, "dirty");
    assert.equal(document.currentText, "initial\n");
    assert.equal(document.editRevision, 1);
    assert.equal(document.serializedRevision, 0);
    assert.deepEqual(document.serialization, {
        state: "scheduled",
        requestedRevision: 1,
        error: undefined,
    });

    state = reduce(state, {type: "save_started", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.saveInFlight, undefined);
    state = reduce(state, {
        type: "source_flushed",
        ...id,
        editRevision: 1,
        text: "exact\r\nbytes",
    });
    state = reduce(state, {type: "save_started", ...id});
    document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.saveInFlight.text, "exact\r\nbytes");
});

run_test("stale and no-op transitions fail closed", () => {
    let state = workspace_store.create_pear_workspace_store();
    const missing_id = identity("missing", "README.md", 1);
    assert.equal(workspace_store.get_pear_workspace_document(state, missing_id), undefined);
    state = reduce(state, {type: "open_document", workspaceId: "missing", path: "README.md"});
    assert.equal(state.ignoredStaleResponses, 1);

    const loaded = loaded_store();
    state = loaded.state;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    const unchanged = reduce(state, {
        type: "source_flushed",
        ...id,
        editRevision: 0,
        text: "initial\n",
    });
    assert.equal(unchanged.ignoredStaleResponses, 1);
    state = unchanged;
    state = reduce(state, {
        type: "set_rich_eligibility",
        ...id,
        editRevision: 99,
        eligibility: "supported",
    });
    state = reduce(state, {type: "serialization_requested", ...id});
    state = reduce(state, {type: "serialization_started", ...id, editRevision: 99});
    state = reduce(state, {
        type: "serialization_failed",
        ...id,
        editRevision: 99,
        error: "stale",
    });
    state = reduce(state, {
        type: "save_failed",
        ...id,
        editRevision: 99,
        error: "stale",
    });
    state = reduce(state, {
        type: "save_conflicted",
        ...id,
        editRevision: 99,
        remoteText: "stale",
        remoteVersion: "stale",
        remoteEntryId: "stale-entry",
        remoteWorkspaceRevision: 99,
    });
    const before_empty_resolutions = state;
    state = reduce(state, {type: "conflict_reload", ...id});
    state = reduce(state, {type: "conflict_overwrite", ...id});
    assert.equal(state, before_empty_resolutions);

    state = reduce(state, {
        type: "serialization_requested",
        ...identity("workspace-a", "README.md", 999),
    });
    state = reduce(state, {...id, type: "unknown_transition"});
    assert.equal(state.ignoredStaleResponses, 7);

    state = activate(state, "workspace-b");
    state = reduce(state, {type: "source_changed", ...id});
    assert.equal(state.ignoredStaleResponses, 8);
});

run_test("2,000 seeded save schedules never cross workspace or document authority", () => {
    function random(seed) {
        let value = seed;
        return () => {
            value = (value * 1_664_525 + 1_013_904_223) % 4_294_967_296;
            return value;
        };
    }

    for (let seed = 1; seed <= 2000; seed += 1) {
        const next = random(seed);
        const workspace_id = `workspace-${next() % 2}`;
        const path = ["README.md", "notes/A.md", "notes/B.md"][next() % 3];
        const loaded = loaded_store(workspace_id, path);
        let {state} = loaded;
        const id = identity(workspace_id, path, loaded.request_generation);
        const first_text = `seed ${seed} first\n`;
        state = source_edit(state, id, first_text);
        state = reduce(state, {type: "save_started", ...id});
        const save = get_document(
            state,
            workspace_id,
            path,
            loaded.request_generation,
        ).saveInFlight;
        assert.ok(save !== undefined, `seed ${seed}: save must start`);
        const immutable_save = structuredClone(save);

        const edit_after_start = next() % 2 === 0;
        if (edit_after_start) {
            state = source_edit(state, id, `seed ${seed} second\n`);
        }

        const navigate = next() % 3 !== 0;
        let other_identity;
        let other_before;
        if (navigate) {
            const other_workspace = next() % 2 === 0 ? workspace_id : `${workspace_id}-other`;
            const other_path = path === "README.md" ? "notes/A.md" : "README.md";
            state = activate(state, other_workspace);
            const opened = open_document(state, other_workspace, other_path);
            state = load_document(
                opened.state,
                other_workspace,
                other_path,
                opened.request_generation,
                {text: `other ${seed}\n`},
            );
            other_identity = identity(other_workspace, other_path, opened.request_generation);
            other_before = structuredClone(
                workspace_store.get_pear_workspace_document(state, other_identity),
            );
        }

        const valid_callback = next() % 4 !== 0;
        const ignored_before = state.ignoredStaleResponses;
        state = reduce(state, {
            type: "save_succeeded",
            ...id,
            editRevision: valid_callback ? save.editRevision : save.editRevision + 100,
            newEntryId: `entry:${path}:v2`,
            newVersion: `version-${seed}-2`,
            workspaceRevision: 2,
        });

        assert.deepEqual(save, immutable_save, `seed ${seed}: PUT snapshot mutated`);
        if (other_identity !== undefined) {
            assert.deepEqual(
                workspace_store.get_pear_workspace_document(state, other_identity),
                other_before,
                `seed ${seed}: callback changed another document`,
            );
        }
        const original_workspace = workspace_store.get_pear_workspace(state, workspace_id);
        assert.ok(original_workspace !== undefined);
        const original = [...original_workspace.documents.values()].find(
            (document) => document.path === path,
        );
        assert.ok(original !== undefined, `seed ${seed}: original document disappeared`);
        if (valid_callback) {
            assert.equal(original.savedText, first_text, `seed ${seed}: wrong bytes acknowledged`);
            assert.equal(original.saveInFlight, undefined, `seed ${seed}: save did not settle`);
            assert.equal(original.entryId, `entry:${path}:v2`, `seed ${seed}: ID did not rebind`);
            assert.equal(
                original.state,
                edit_after_start ? "dirty" : "clean",
                `seed ${seed}: clean state is unsound`,
            );
            assert.equal(state.ignoredStaleResponses, ignored_before);
        } else {
            assert.deepEqual(
                original.saveInFlight,
                immutable_save,
                `seed ${seed}: stale callback consumed the save`,
            );
            assert.equal(state.ignoredStaleResponses, ignored_before + 1);
        }
    }
});

run_test("a dirty document rejects a late reload", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = source_edit(state, id, "dirty\n");
    const reopened = open_document(state, "workspace-a", "README.md");
    state = load_document(reopened.state, "workspace-a", "README.md", reopened.request_generation, {
        text: "late reload\n",
    });

    assert.equal(state.ignoredStaleResponses, 1);
    assert.equal(
        workspace_store.get_pear_workspace(state, "workspace-a").documents.get("entry:README.md")
            .currentText,
        "dirty\n",
    );
});

run_test("a clean reload adopts exact bytes and preserves per-mode view state", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = reduce(state, {
        type: "source_view_changed",
        ...id,
        selection: {anchor: 2, head: 4},
        scrollTop: 20,
    });
    const reopened = open_document(state, "workspace-a", "README.md");
    state = load_document(reopened.state, "workspace-a", "README.md", reopened.request_generation, {
        text: "fresh bytes",
        baseVersion: "fresh-version",
    });

    const document = get_document(state, "workspace-a", "README.md", reopened.request_generation);
    assert.equal(document.currentText, "fresh bytes");
    assert.equal(document.baseVersion, "fresh-version");
    assert.deepEqual(document.sourceView, {
        selection: {anchor: 2, head: 4},
        scrollTop: 20,
    });
});

run_test("save failure is scoped and a newer serialization error survives an older ack", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const id = identity("workspace-a", "README.md", loaded.request_generation);
    state = source_edit(state, id, "save me\n");
    state = reduce(state, {type: "save_started", ...id});
    state = reduce(state, {
        type: "save_failed",
        ...id,
        editRevision: 1,
        error: "network_error",
    });
    let document = get_document(state, "workspace-a", "README.md", loaded.request_generation);
    assert.equal(document.state, "error");
    assert.equal(document.error, "network_error");

    const rich_loaded = loaded_store("workspace-c");
    state = rich_loaded.state;
    const rich_id = identity("workspace-c", "README.md", rich_loaded.request_generation);
    state = reduce(state, {
        type: "set_rich_eligibility",
        ...rich_id,
        editRevision: 0,
        eligibility: "supported",
    });
    state = reduce(state, {type: "set_editor_mode", ...rich_id, mode: "rich"});
    state = reduce(state, {type: "rich_edited", ...rich_id});
    state = reduce(state, {type: "serialization_started", ...rich_id, editRevision: 1});
    state = reduce(state, {
        type: "serialization_succeeded",
        ...rich_id,
        editRevision: 1,
        text: "serialized one\n",
    });
    state = reduce(state, {type: "save_started", ...rich_id});
    state = reduce(state, {type: "rich_edited", ...rich_id});
    state = reduce(state, {type: "serialization_started", ...rich_id, editRevision: 2});
    state = reduce(state, {
        type: "serialization_failed",
        ...rich_id,
        editRevision: 2,
        error: "serialization_error",
    });
    state = reduce(state, {
        type: "save_succeeded",
        ...rich_id,
        editRevision: 1,
        newEntryId: "entry:README.md",
        newVersion: "version-2",
        workspaceRevision: 2,
    });
    document = get_document(state, "workspace-c", "README.md", rich_loaded.request_generation);
    assert.equal(document.state, "error");
    assert.equal(document.error, "serialization_error");
});

run_test("a successful save adopts the revision-bound entry identity", () => {
    const loaded = loaded_store();
    let {state} = loaded;
    const old_id = identity("workspace-a", "README.md", loaded.request_generation);
    state = source_edit(state, old_id, "saved\n");
    state = reduce(state, {type: "save_started", ...old_id});
    state = reduce(state, {
        type: "save_succeeded",
        ...old_id,
        editRevision: 1,
        newEntryId: "revision-2-readme",
        newVersion: "version-2",
        workspaceRevision: 2,
    });

    assert.equal(workspace_store.get_pear_workspace_document(state, old_id), undefined);
    const new_id = {...old_id, entryId: "revision-2-readme"};
    const document = workspace_store.get_pear_workspace_document(state, new_id);
    assert.ok(document !== undefined);
    assert.equal(document.entryId, "revision-2-readme");
    assert.equal(document.state, "clean");
    assert.equal(
        workspace_store.get_pear_workspace(state, "workspace-a").activeEntryId,
        "revision-2-readme",
    );

    state = reduce(state, {type: "source_changed", ...old_id});
    assert.equal(state.ignoredStaleResponses, 1);
    assert.equal(workspace_store.get_pear_workspace_document(state, new_id).currentText, "saved\n");
});

run_test("a stale snapshot cannot load bytes under a current entry identity", () => {
    let state = workspace_store.create_pear_workspace_store();
    state = activate(state, "workspace-a", 2);
    const opened = open_document(state, "workspace-a", "README.md");
    state = load_document(opened.state, "workspace-a", "README.md", opened.request_generation, {
        workspaceRevision: 1,
        text: "stale bytes\n",
    });

    assert.equal(state.ignoredStaleResponses, 1);
    assert.equal(workspace_store.get_pear_workspace(state, "workspace-a").documents.size, 0);
});
