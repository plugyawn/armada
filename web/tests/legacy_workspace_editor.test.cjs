"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    pretendToBeVisual: true,
    url: "http://zulip.zulipdev.com/",
});
const {window} = dom;
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    navigator: window.navigator,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    HTMLImageElement: window.HTMLImageElement,
    Node: window.Node,
    MutationObserver: window.MutationObserver,
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
})) {
    set_global(name, value);
}

const workspace_editor = zrequire("legacy_workspace_editor");

function entry(path, overrides = {}) {
    return {
        entry_id: `entry:${path}`,
        path,
        name: path.split("/").at(-1),
        kind: "file",
        status: "clean",
        has_children: false,
        size: 20,
        ...overrides,
    };
}

function file(path, overrides = {}) {
    return {
        workspace_uuid: "workspace-a",
        workspace_revision: 1,
        entry_id: `entry:${path}`,
        path,
        content: "initial\r\n",
        version: "a".repeat(64),
        size: 9,
        mime_type: "text/plain",
        language: "plain",
        editable: true,
        rich_mode_eligible: path.endsWith(".md"),
        max_bytes: 1000,
        edit_revision: 1,
        ...overrides,
    };
}

function harness() {
    const root = document.createElement("div");
    document.body.append(root);
    const gets = [];
    const puts = [];
    const deferred = [];
    const autosaves = [];
    const updates = [];
    const saved = [];
    const source_editors = [];
    const rich_editors = [];
    let source_loads = 0;
    let rich_loads = 0;
    let rich_should_fail = false;
    let rich_destroy_gate;

    function scheduled(queue, callback) {
        const work = {callback, cancelled: false};
        queue.push(work);
        return {cancel: () => (work.cancelled = true)};
    }

    function create_source(options) {
        const host = document.createElement("div");
        host.className = "fake-source";
        options.root.replaceChildren(host);
        const fake = {
            text: options.text,
            initialSelection: options.selection,
            readOnly: options.readOnly,
            destroyed: false,
            dirty(selection) {
                options.onDirty({origin: "user", selection: selection ?? {anchor: 1, head: 1}});
            },
            languageReady: Promise.resolve("applied"),
            hydrate(value) {
                this.text = value.text;
            },
            setReadOnly() {},
            setLanguage: async () => "applied",
            getText() {
                return this.text;
            },
            getViewState: () => ({selection: {anchor: 1, head: 1}, scrollTop: 7}),
            getSelectionContext: () => ({
                selectedText: "source selection",
                startLine: 2,
                endLine: 3,
            }),
            focus() {},
            hasFocus: () => false,
            destroy() {
                this.destroyed = true;
                host.remove();
            },
        };
        source_editors.push(fake);
        return fake;
    }

    async function load_rich() {
        rich_loads += 1;
        if (rich_should_fail) {
            throw new Error("chunk failed");
        }
        return {
            async create_pear_workspace_markdown_editor(options) {
                const host = document.createElement("div");
                host.className = "fake-rich";
                options.root.replaceChildren(host);
                const fake = {
                    revision: options.editRevision,
                    cursorLine: 0,
                    markdown: options.markdown,
                    initialSelection: options.selection,
                    initialScrollTop: options.scrollTop,
                    readOnly: options.readOnly,
                    destroyed: false,
                    dirty() {
                        this.revision += 1;
                        options.onDirty({origin: "user", editRevision: this.revision});
                    },
                    serialize(markdown = this.markdown) {
                        this.markdown = markdown;
                        options.onSerialized({
                            status: "serialized",
                            editRevision: this.revision,
                            markdown,
                        });
                    },
                    async flush(expected = this.revision) {
                        if (expected !== this.revision) {
                            return {
                                status: "stale",
                                requestedRevision: expected,
                                currentRevision: this.revision,
                            };
                        }
                        this.serialize();
                        return {
                            status: "serialized",
                            editRevision: this.revision,
                            markdown: this.markdown,
                        };
                    },
                    hydrate(value) {
                        this.markdown = value.markdown;
                        this.revision = value.editRevision;
                        return {eligible: true};
                    },
                    setReadOnly() {},
                    getViewState: () => ({selection: {anchor: 1, head: 1}, scrollTop: 3}),
                    getSelectionContext: () => ({selectedText: "rich selection"}),
                    getCursorLine() {
                        return this.cursorLine;
                    },
                    setCursorLine(line) {
                        this.cursorLine = line;
                    },
                    getEditRevision() {
                        return this.revision;
                    },
                    getStats: () => ({serializations: 0}),
                    focus() {},
                    hasFocus: () => false,
                    async destroy() {
                        this.destroyed = true;
                        host.remove();
                        await rich_destroy_gate?.promise;
                        rich_destroy_gate = undefined;
                    },
                };
                rich_editors.push(fake);
                return {ok: true, editor: fake};
            },
        };
    }

    const controller = workspace_editor.create_pear_workspace_editor({
        root,
        onUpdate(snapshot) {
            updates.push(snapshot);
        },
        onSaved(event) {
            saved.push(event);
        },
        dependencies: {
            getFile(workspace_id, entry_id, callbacks) {
                const request = {workspace_id, entry_id, callbacks, aborted: false};
                gets.push(request);
                return {abort: () => (request.aborted = true)};
            },
            putFile(workspace_id, request, callbacks) {
                const pending = {workspace_id, request, callbacks};
                puts.push(pending);
                return {abort() {}};
            },
            rawUrl(workspace_id, entry_id, download = false) {
                return `/raw/${workspace_id}/${entry_id}${download ? "/download" : ""}`;
            },
            async loadSourceEditor() {
                source_loads += 1;
                return {create_pear_workspace_source_editor: create_source};
            },
            loadRichEditor: load_rich,
            scheduleSourceFlush: (callback) => scheduled(deferred, callback),
            scheduleAutosave: (callback) => scheduled(autosaves, callback),
        },
    });
    controller.activateWorkspace("workspace-a", 1, {
        rawBytes: 2000,
        textBytes: 1000,
        writable: true,
    });

    function run_next(queue) {
        const work = queue.shift();
        assert.ok(work !== undefined);
        if (!work.cancelled) {
            work.callback();
        }
    }

    return {
        root,
        controller,
        gets,
        puts,
        deferred,
        autosaves,
        updates,
        saved,
        source_editors,
        rich_editors,
        get source_loads() {
            return source_loads;
        },
        get rich_loads() {
            return rich_loads;
        },
        failRich() {
            rich_should_fail = true;
        },
        deferRichDestroy() {
            let release;
            const promise = new Promise((resolve) => {
                release = resolve;
            });
            rich_destroy_gate = {promise};
            return release;
        },
        run_next,
    };
}

async function open_text(test, path = "README.md", expected_source_loads = 1) {
    await test.controller.open(entry(path));
    assert.equal(test.gets.length, 1);
    assert.equal(test.source_loads, 0, "Source code must not load before text content arrives");
    test.gets[0].callbacks.success(file(path));
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(test.source_loads, expected_source_loads);
}

async function begin_local_save(test, text = "local") {
    await open_text(test);
    const source = test.source_editors[0];
    source.text = text;
    source.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);
    assert.equal(test.puts.length, 1);
}

run_test("a preferred Rich mode survives the asynchronous file fetch", async () => {
    const test = harness();
    await test.controller.open(entry("README.md"), "rich");
    assert.equal(test.rich_loads, 0);
    assert.equal(test.source_loads, 0);

    test.gets[0].callbacks.success(file("README.md"));
    for (let index = 0; index < 4; index += 1) {
        await Promise.resolve();
    }
    assert.equal(test.rich_loads, 1);
    assert.equal(test.source_loads, 0);
    assert.equal(test.controller.getSnapshot().activeDocument.editorMode, "rich");

    await test.controller.destroy();
    test.root.remove();
});

run_test("Source dirty feedback precedes exact flush and a revision-bound PUT", async () => {
    const test = harness();
    await open_text(test);
    assert.equal(test.rich_loads, 0, "Rich must not load on the cold Source path");
    const source = test.source_editors[0];
    source.text = "edited\r\nexact  ";
    source.dirty();

    let snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.activeDocument.state, "dirty");
    assert.equal(snapshot.activeDocument.currentText, "initial\r\n");
    assert.equal(test.puts.length, 0);

    test.run_next(test.deferred);
    snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.activeDocument.currentText, "edited\r\nexact  ");
    assert.equal(snapshot.activeDocument.serializedRevision, 1);
    test.run_next(test.autosaves);
    assert.equal(test.puts.length, 1);
    assert.deepEqual(test.puts[0].request, {
        entryId: "entry:README.md",
        expectedPath: "README.md",
        content: "edited\r\nexact  ",
        baseVersion: "a".repeat(64),
        workspaceRevision: 1,
        editRevision: 1,
    });
    await test.controller.destroy();
    test.root.remove();
});

run_test("Source reconstruction is trailing-debounced across rapid edits", async () => {
    const test = harness();
    await open_text(test);
    const source = test.source_editors[0];

    source.text = "first";
    source.dirty();
    source.text = "second";
    source.dirty();
    assert.equal(test.deferred.length, 2);
    assert.equal(test.deferred[0].cancelled, true);
    assert.equal(test.controller.getSnapshot().activeDocument.currentText, "initial\r\n");

    test.run_next(test.deferred);
    test.run_next(test.deferred);
    assert.equal(test.controller.getSnapshot().activeDocument.currentText, "second");
    assert.equal(test.autosaves.length, 1);

    await test.controller.destroy();
    test.root.remove();
});

run_test("each document save is single-flight and a newer edit stays dirty", async () => {
    const test = harness();
    await open_text(test);
    const source = test.source_editors[0];
    source.text = "first";
    source.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);
    assert.equal(test.puts.length, 1);

    source.text = "second";
    source.dirty();
    test.run_next(test.deferred);
    assert.equal(test.puts.length, 1, "a second PUT cannot start while the first is active");

    test.puts[0].callbacks.success(
        file("README.md", {
            workspace_revision: 2,
            entry_id: "entry:README.md:v2",
            content: "first",
            version: "b".repeat(64),
        }),
    );
    assert.equal(test.controller.getSnapshot().activeDocument.state, "dirty");
    test.run_next(test.autosaves);
    assert.equal(test.puts.length, 2);
    assert.equal(test.puts[1].request.entryId, "entry:README.md:v2");
    assert.equal(test.puts[1].request.content, "second");
    await test.controller.destroy();
    test.root.remove();
});

run_test("a delayed A save keeps active B mounted while its authority is reconciled", async () => {
    const test = harness();
    await open_text(test, "A.md");
    const source_a = test.source_editors[0];
    source_a.text = "A edit";
    source_a.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);

    await test.controller.open(entry("B.md"));
    test.gets[1].callbacks.success(file("B.md", {content: "B content"}));
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(test.controller.getSnapshot().surface.entry.path, "B.md");

    test.puts[0].callbacks.success(
        file("A.md", {
            workspace_revision: 2,
            entry_id: "entry:A.md:v2",
            content: "A edit",
            version: "b".repeat(64),
        }),
    );
    const snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.surface.kind, "text");
    assert.equal(snapshot.surface.entry.path, "B.md");
    assert.equal(snapshot.activeDocument.path, "B.md");
    assert.equal(snapshot.activeDocument.currentText, "B content");
    assert.equal(snapshot.activeDocument.authority, "stale");
    assert.equal(snapshot.mountedAdapter, "source");
    await test.controller.destroy();
    test.root.remove();
});

run_test("a dirty background tab resumes autosave after revision reconciliation", async () => {
    const test = harness();
    await open_text(test, "A.md");
    const source_a = test.source_editors[0];
    source_a.text = "A edit";
    source_a.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);

    await test.controller.open(entry("B.md"));
    test.gets[1].callbacks.success(file("B.md", {content: "B content"}));
    await Promise.resolve();
    await Promise.resolve();
    const source_b = test.source_editors[1];
    source_b.text = "B edit";
    source_b.dirty();
    test.run_next(test.deferred);
    assert.equal(test.autosaves.length, 1);

    test.puts[0].callbacks.success(
        file("A.md", {
            workspace_revision: 2,
            entry_id: "entry:A.md:v2",
            content: "A edit",
            version: "b".repeat(64),
        }),
    );
    assert.equal(test.autosaves[0].cancelled, true);
    const reconciliation = test.controller.reconcileEntry(
        entry("B.md", {entry_id: "entry:B.md:v2"}),
        2,
    );
    assert.equal(test.gets.length, 3);
    test.gets[2].callbacks.success(
        file("B.md", {
            workspace_revision: 2,
            entry_id: "entry:B.md:v2",
            content: "B content",
            version: "c".repeat(64),
        }),
    );
    assert.equal(await reconciliation, "current");
    assert.equal(test.controller.getSnapshot().activeDocument.currentText, "B edit");
    assert.equal(test.controller.getSnapshot().activeDocument.authority, "current");
    assert.equal(test.autosaves.length, 2, "reconciliation must re-arm the dirty tab");
    test.run_next(test.autosaves);
    test.run_next(test.autosaves);
    assert.equal(test.puts.length, 2);
    assert.equal(test.puts[1].request.entryId, "entry:B.md:v2");
    assert.equal(test.puts[1].request.content, "B edit");

    await test.controller.destroy();
    test.root.remove();
});

run_test("a conflict blocks autosave until explicit Overwrite adopts the remote base", async () => {
    const test = harness();
    await open_text(test);
    const source = test.source_editors[0];
    source.text = "local";
    source.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);
    test.puts[0].callbacks.error({
        kind: "server",
        status: 409,
        error: {
            result: "error",
            msg: "changed",
            error_code: "version_conflict",
            workspace_revision: 2,
            remote_file: file("README.md", {
                workspace_revision: 2,
                entry_id: "entry:README.md:v2",
                content: "remote",
                version: "c".repeat(64),
            }),
        },
    });
    assert.equal(test.controller.getSnapshot().activeDocument.state, "conflict");
    assert.equal(test.autosaves.length, 0);

    await test.controller.resolveConflict("overwrite");
    assert.equal(test.controller.getSnapshot().activeDocument.currentText, "local");
    assert.equal(test.controller.getSnapshot().activeDocument.baseVersion, "c".repeat(64));
    test.run_next(test.autosaves);
    assert.equal(test.puts.length, 2);
    assert.equal(test.puts[1].request.entryId, "entry:README.md:v2");
    assert.equal(test.puts[1].request.baseVersion, "c".repeat(64));
    assert.deepEqual(test.saved[0], {
        oldEntryId: "entry:README.md",
        newEntryId: "entry:README.md:v2",
        path: "README.md",
        workspaceRevision: 2,
    });
    await test.controller.destroy();
    test.root.remove();
});

run_test("conflict Reload publishes the adopted identity and workspace revision", async () => {
    const test = harness();
    await open_text(test);
    const source = test.source_editors[0];
    source.text = "local";
    source.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);
    test.puts[0].callbacks.error({
        kind: "server",
        status: 409,
        error: {
            result: "error",
            msg: "changed",
            error_code: "version_conflict",
            workspace_revision: 2,
            remote_file: file("README.md", {
                workspace_revision: 2,
                entry_id: "entry:README.md:v2",
                content: "remote",
                version: "c".repeat(64),
            }),
        },
    });
    await test.controller.resolveConflict("reload");
    const snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.activeIdentity.entryId, "entry:README.md:v2");
    assert.equal(snapshot.surface.entry.entry_id, "entry:README.md:v2");
    assert.equal(snapshot.activeDocument.currentText, "remote");
    assert.deepEqual(test.saved[0], {
        oldEntryId: "entry:README.md",
        newEntryId: "entry:README.md:v2",
        path: "README.md",
        workspaceRevision: 2,
    });
    await test.controller.destroy();
    test.root.remove();
});

run_test(
    "read-only workspace capability mounts text without accepting edits or saves",
    async () => {
        const test = harness();
        test.controller.activateWorkspace("workspace-a", 1, {
            rawBytes: 2000,
            textBytes: 1000,
            writable: false,
        });
        await open_text(test);
        const source = test.source_editors[0];
        assert.equal(source.readOnly, true);
        source.text = "must not save";
        source.dirty();
        assert.equal(test.controller.getSnapshot().activeDocument.state, "clean");
        assert.equal(test.deferred.length, 0);
        assert.equal(test.autosaves.length, 0);
        assert.equal(test.puts.length, 0);
        assert.match(test.controller.getSnapshot().notice, /Read-only/);
        await test.controller.destroy();
        test.root.remove();
    },
);

run_test("a slow Rich teardown cannot revive an older file-open intent", async () => {
    const test = harness();
    await open_text(test, "A.md");
    assert.equal(await test.controller.switchMode("rich"), true);
    const release = test.deferRichDestroy();
    const opening_b = test.controller.open(entry("B.md"));
    await Promise.resolve();
    await Promise.resolve();
    const opening_c = test.controller.open(entry("C.md"));
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(test.gets.length, 2);
    assert.equal(test.gets[1].entry_id, "entry:C.md");
    release();
    await Promise.all([opening_b, opening_c]);
    assert.equal(test.controller.getSnapshot().surface.entry.path, "C.md");
    assert.equal(
        test.gets.some((request) => request.entry_id === "entry:B.md"),
        false,
    );
    await test.controller.destroy();
    test.root.remove();
});

run_test("a mismatched save receipt cannot acknowledge local content", async () => {
    const test = harness();
    await open_text(test);
    const source = test.source_editors[0];
    source.text = "local";
    source.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);
    test.puts[0].callbacks.success(
        file("README.md", {
            content: "different",
            workspace_revision: 2,
            entry_id: "entry:README.md:v2",
            version: "d".repeat(64),
        }),
    );
    const document = test.controller.getSnapshot().activeDocument;
    assert.equal(document.state, "error");
    assert.equal(document.savedText, "initial\r\n");
    assert.equal(document.currentText, "local");
    assert.match(document.error, /invalid save receipt/);
    await test.controller.destroy();
    test.root.remove();
});

run_test(
    "save receipts require exact authority, a revision advance, and a rebound ID",
    async () => {
        const invalid_receipts = [
            ["workspace", {workspace_uuid: "workspace-b"}],
            ["path", {path: "OTHER.md"}],
            ["revision", {workspace_revision: 1}],
            ["entry ID", {entry_id: "entry:README.md"}],
        ];
        for (const [label, override] of invalid_receipts) {
            const test = harness();
            await begin_local_save(test);
            test.puts[0].callbacks.success(
                file("README.md", {
                    workspace_revision: 2,
                    entry_id: "entry:README.md:v2",
                    content: "local",
                    version: "d".repeat(64),
                    ...override,
                }),
            );
            const document = test.controller.getSnapshot().activeDocument;
            assert.equal(document.state, "error", `${label} mismatch must fail closed`);
            assert.equal(document.savedText, "initial\r\n");
            assert.equal(document.currentText, "local");
            assert.match(document.error, /invalid save receipt/);
            await test.controller.destroy();
            test.root.remove();
        }
    },
);

run_test("409 remote files require exact newer authority before conflict adoption", async () => {
    const invalid_conflicts = [
        ["workspace", {remote: {workspace_uuid: "workspace-b"}}],
        ["path", {remote: {path: "OTHER.md"}}],
        ["revision", {remote: {workspace_revision: 1}, envelopeRevision: 1}],
        ["entry ID", {remote: {entry_id: "entry:README.md"}}],
        ["envelope revision", {envelopeRevision: 3}],
        ["base version", {remote: {version: "a".repeat(64)}}],
    ];
    for (const [label, invalid] of invalid_conflicts) {
        const test = harness();
        await begin_local_save(test);
        const remote = file("README.md", {
            workspace_revision: 2,
            entry_id: "entry:README.md:v2",
            content: "remote",
            version: "c".repeat(64),
            ...invalid.remote,
        });
        test.puts[0].callbacks.error({
            kind: "server",
            status: 409,
            error: {
                result: "error",
                msg: "changed",
                error_code: "version_conflict",
                workspace_revision: invalid.envelopeRevision ?? remote.workspace_revision,
                remote_file: remote,
            },
        });
        const document = test.controller.getSnapshot().activeDocument;
        assert.equal(document.state, "error", `${label} mismatch must fail closed`);
        assert.equal(document.conflict, undefined);
        assert.match(document.error, /invalid conflict receipt/);
        await test.controller.destroy();
        test.root.remove();
    }
});

run_test("an old save callback cannot mutate a rebound save session", async () => {
    const test = harness();
    await begin_local_save(test);
    test.puts[0].callbacks.error({
        kind: "server",
        status: 409,
        error: {
            result: "error",
            msg: "changed",
            error_code: "version_conflict",
            workspace_revision: 2,
            remote_file: file("README.md", {
                workspace_revision: 2,
                entry_id: "entry:README.md:v2",
                content: "remote",
                version: "c".repeat(64),
            }),
        },
    });
    await test.controller.resolveConflict("overwrite");
    test.run_next(test.autosaves);
    const before = test.controller.getSnapshot().activeDocument;
    const saved_count = test.saved.length;
    assert.equal(before.state, "saving");
    assert.equal(test.puts.length, 2);

    test.puts[0].callbacks.success(
        file("README.md", {
            workspace_revision: 2,
            entry_id: "entry:README.md:v2",
            content: "local",
            version: "d".repeat(64),
        }),
    );
    const after = test.controller.getSnapshot().activeDocument;
    assert.equal(after, before);
    assert.equal(test.saved.length, saved_count);
    assert.equal(after.state, "saving");
    await test.controller.destroy();
    test.root.remove();
});

run_test("selection context remains bound to the active editor and path", async () => {
    const test = harness();
    await open_text(test);
    assert.deepEqual(test.controller.getSelectionContext(), {
        path: "README.md",
        selectedText: "source selection",
        startLine: 2,
        endLine: 3,
    });

    await test.controller.switchMode("rich");
    assert.deepEqual(test.controller.getSelectionContext(), {
        path: "README.md",
        selectedText: "rich selection",
    });

    await test.controller.destroy();
    test.root.remove();
});

run_test("clean active documents close and reopen without stale adapter authority", async () => {
    const test = harness();
    await open_text(test);
    assert.equal(await test.controller.closeActive(), true);
    assert.equal(test.controller.getSnapshot().activeDocument, undefined);
    assert.equal(test.controller.getSnapshot().surface.kind, "empty");
    assert.equal(test.source_editors[0].destroyed, true);

    const reopened = test.controller.open(entry("README.md"));
    await reopened;
    assert.equal(test.gets.length, 1, "reopening a clean cached file avoids a duplicate GET");
    assert.equal(test.controller.getSnapshot().activeDocument.path, "README.md");

    test.source_editors[1].text = "dirty";
    test.source_editors[1].dirty();
    assert.equal(await test.controller.closeActive(), false);
    assert.equal(test.controller.getSnapshot().activeDocument.path, "README.md");
    await test.controller.destroy();
    test.root.remove();
});

run_test("Rich loads only on demand and shares the document save session", async () => {
    const test = harness();
    await open_text(test);
    assert.equal(await test.controller.switchMode("rich"), true);
    assert.equal(test.rich_loads, 1);
    assert.equal(test.source_editors[0].destroyed, true);
    assert.equal(test.root.querySelectorAll(".fake-source, .fake-rich").length, 1);

    const rich = test.rich_editors[0];
    rich.dirty();
    assert.equal(test.controller.getSnapshot().activeDocument.state, "dirty");
    rich.serialize("# rich\n");
    test.run_next(test.autosaves);
    await Promise.resolve();
    assert.equal(test.puts[0].request.content, "# rich\n");
    assert.equal(test.puts[0].request.editRevision, 1);
    await test.controller.destroy();
    test.root.remove();
});

run_test("Source and Rich switches preserve the cursor at line granularity", async () => {
    const test = harness();
    await test.controller.open(entry("README.md"));
    test.gets[0].callbacks.success(
        file("README.md", {content: "first\nsecond\nthird\n", size: 19}),
    );
    await Promise.resolve();
    await Promise.resolve();
    test.source_editors[0].getViewState = () => ({
        selection: {anchor: 13, head: 13},
        scrollTop: 7,
    });

    assert.equal(await test.controller.switchMode("rich"), true);
    assert.equal(test.rich_editors[0].cursorLine, 2);
    test.rich_editors[0].cursorLine = 1;
    assert.equal(await test.controller.switchMode("source"), true);
    assert.deepEqual(test.source_editors.at(-1).initialSelection, {anchor: 6, head: 6});

    await test.controller.destroy();
    test.root.remove();
});

run_test("explicit Rich save consumes one serialization callback for the revision", async () => {
    const test = harness();
    await open_text(test);
    assert.equal(await test.controller.switchMode("rich"), true);
    const rich = test.rich_editors[0];
    rich.markdown = "# explicit\n";
    rich.dirty();

    await test.controller.save();
    assert.equal(test.puts.length, 1);
    assert.equal(test.puts[0].request.content, "# explicit\n");
    const snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.activeDocument.serialization.state, "idle");
    assert.equal(snapshot.store.ignoredStaleResponses, 0);
    await test.controller.destroy();
    test.root.remove();
});

run_test("Rich revision drift has a lossless one-click Source recovery", async () => {
    const test = harness();
    await open_text(test);
    assert.equal(await test.controller.switchMode("rich"), true);
    const rich = test.rich_editors[0];
    rich.markdown = "# recovered\n";
    rich.revision += 2;
    rich.dirty();

    let snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.surface.kind, "error");
    assert.equal(snapshot.surface.action, "source");
    assert.equal(await test.controller.switchMode("source"), true);

    snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.surface.kind, "text");
    assert.equal(snapshot.activeDocument.editorMode, "source");
    assert.equal(snapshot.activeDocument.currentText, "# recovered\n");
    assert.equal(snapshot.activeDocument.editRevision, rich.revision);
    assert.equal(snapshot.mountedAdapter, "source");
    assert.equal(test.source_editors.at(-1).text, "# recovered\n");
    await test.controller.destroy();
    test.root.remove();
});

run_test("a failed Rich chunk restores the exact Source surface", async () => {
    const test = harness();
    await open_text(test);
    test.failRich();
    assert.equal(await test.controller.switchMode("rich"), false);
    const snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.surface.kind, "text");
    assert.equal(snapshot.activeDocument.editorMode, "source");
    assert.equal(snapshot.mountedAdapter, "source");
    assert.match(snapshot.notice, /Source remains available/);
    assert.deepEqual(snapshot.richModeIssue, {
        kind: "load_error",
        message: "Rich editor failed to load. Source remains available.",
        retryable: true,
    });
    assert.equal(test.root.querySelectorAll(".fake-source").length, 1);
    test.source_editors.at(-1).dirty();
    assert.equal(test.controller.getSnapshot().richModeIssue, undefined);
    await test.controller.destroy();
    test.root.remove();
});

run_test("restored Rich preference mounts directly with saved non-content view state", async () => {
    const test = harness();
    test.controller.activateWorkspace(
        "workspace-a",
        1,
        {rawBytes: 2000, textBytes: 1000, writable: true},
        {
            mode: "rich",
            documents: {
                "README.md": {
                    source: {selection: {anchor: 8, head: 2}, scrollTop: 41},
                    rich: {selection: {anchor: 5, head: 3}, scrollTop: 27},
                },
            },
        },
    );
    await open_text(test, "README.md", 0);
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(test.source_editors.length, 0, "restored Rich must not flash Source first");
    assert.equal(test.rich_editors.length, 1);
    assert.deepEqual(test.rich_editors[0].initialSelection, {anchor: 5, head: 3});
    assert.equal(test.rich_editors[0].initialScrollTop, 27);
    assert.equal(test.controller.getSnapshot().mountedAdapter, "rich");
    await test.controller.destroy();
    test.root.remove();
});

run_test("raster, binary, and oversize previews never issue a text GET", async () => {
    const test = harness();
    await test.controller.open(entry("image.png", {size: 100}));
    assert.deepEqual(test.controller.getSnapshot().surface, {
        kind: "raster",
        entry: entry("image.png", {size: 100}),
        rawUrl: "/raw/workspace-a/entry:image.png",
    });
    await test.controller.open(entry("manual.pdf", {size: 100}));
    assert.equal(test.controller.getSnapshot().surface.kind, "binary");
    await test.controller.open(entry("large.txt", {size: 1500}));
    assert.deepEqual(test.controller.getSnapshot().surface, {
        kind: "oversize",
        entry: entry("large.txt", {size: 1500}),
        maxBytes: 1000,
        rawAvailable: true,
    });
    assert.equal(test.gets.length, 0);
    await test.controller.destroy();
    test.root.remove();
});

run_test(
    "an unbounded streamed raw endpoint keeps large raw/download previews available",
    async () => {
        const test = harness();
        test.controller.activateWorkspace("workspace-a", 1, {
            rawBytes: null,
            textBytes: 1000,
            writable: true,
        });
        await test.controller.open(entry("large.png", {size: 50_000_000}));
        assert.equal(test.controller.getSnapshot().surface.kind, "raster");
        await test.controller.open(entry("large.txt", {size: 50_000_000}));
        assert.deepEqual(test.controller.getSnapshot().surface, {
            kind: "oversize",
            entry: entry("large.txt", {size: 50_000_000}),
            maxBytes: 1000,
            rawAvailable: true,
        });
        assert.equal(test.gets.length, 0);
        await test.controller.destroy();
        test.root.remove();
    },
);

run_test("an external workspace revision preserves the mounted save session", async () => {
    const test = harness();
    await open_text(test);
    const source = test.source_editors[0];
    source.text = "stale local edit";
    source.dirty();
    test.run_next(test.deferred);
    test.run_next(test.autosaves);
    test.controller.activateWorkspace("workspace-a", 2, {
        rawBytes: 2000,
        textBytes: 1000,
        writable: true,
    });
    let snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.activeDocument.currentText, "stale local edit");
    assert.equal(snapshot.activeDocument.authority, "stale");
    assert.equal(snapshot.activeDocument.state, "saving");
    assert.equal(snapshot.mountedAdapter, "source");
    assert.equal(snapshot.store.workspaces.get("workspace-a").documents.size, 1);
    assert.match(snapshot.notice, /validating open files/);
    test.puts[0].callbacks.success(
        file("README.md", {
            workspace_revision: 2,
            entry_id: "entry:README.md:v2",
            content: "stale local edit",
            version: "e".repeat(64),
        }),
    );
    snapshot = test.controller.getSnapshot();
    assert.equal(snapshot.activeDocument.currentText, "stale local edit");
    assert.equal(snapshot.activeDocument.authority, "current");
    assert.equal(snapshot.activeDocument.state, "clean");
    assert.equal(snapshot.activeDocument.entryId, "entry:README.md:v2");
    assert.equal(snapshot.store.workspaces.get("workspace-a").documents.size, 1);
    assert.equal(snapshot.mountedAdapter, "source");
    await test.controller.destroy();
    test.root.remove();
});
