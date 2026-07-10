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
    DocumentFragment: window.DocumentFragment,
    Event: window.Event,
    SubmitEvent: window.SubmitEvent,
    MouseEvent: window.MouseEvent,
    KeyboardEvent: window.KeyboardEvent,
    MutationObserver: window.MutationObserver,
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
})) {
    set_global(name, value);
}

const workspace_shell = zrequire("pear_workspace_shell");

function entry(path, kind = "file", overrides = {}) {
    return {
        entry_id: `r1:${path || "root"}`,
        path,
        name: path.split("/").at(-1) || "workspace",
        kind,
        status: "clean",
        has_children: kind === "directory",
        size: kind === "file" ? 10 : null,
        ...overrides,
    };
}

function descriptor(workspace_id, revision = 1, overrides = {}) {
    return {
        uuid: workspace_id,
        display_root: "Scratch workspace",
        stream: "pear",
        topic: "scratch",
        branch: "pear-main",
        head: "abcdef1234567890",
        dirty: false,
        dirty_count: 0,
        workspace_revision: revision,
        root: entry("", "directory", {entry_id: `r${revision}:root`}),
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
        ...overrides,
    };
}

function harness(workspace_id) {
    const root = document.createElement("div");
    document.body.append(root);
    const descriptors = [];
    const trees = [];
    const searches = [];
    const refreshes = [];
    const editors = [];
    const file_controllers = [];
    const abortable = (record) => ({abort: () => (record.aborted = true)});

    function create_editor(options) {
        const state = {
            options,
            opens: [],
            activations: [],
            snapshot: {
                surface: {kind: "empty"},
                store: {
                    activeWorkspaceId: undefined,
                    workspaces: new Map(),
                    ignoredStaleResponses: 0,
                },
                activeDocument: undefined,
                activeIdentity: undefined,
                mountedAdapter: undefined,
                richModeLoading: false,
            },
            preferences: {
                mode: "source",
                documents: {
                    "README.md": {
                        source: {selection: {anchor: 4, head: 2}, scrollTop: 31},
                        rich: {selection: {anchor: 3, head: 3}, scrollTop: 17},
                    },
                },
            },
            activateWorkspace(id, revision, limits, preferences) {
                this.activations.push({id, revision, limits, preferences});
            },
            async open(opened_entry) {
                const selected = root.querySelector(
                    `[data-entry-id='${opened_entry.entry_id}'][aria-selected='true']`,
                );
                const tab = root.querySelector(`[data-tab-path='${opened_entry.path}']`);
                this.opens.push({
                    entry: opened_entry,
                    selectedBeforeOpen: selected !== null,
                    tabBeforeOpen: tab !== null,
                });
            },
            async switchMode() {
                return true;
            },
            async save() {},
            async resolveConflict() {},
            getSnapshot() {
                return this.snapshot;
            },
            getPreferences() {
                return this.preferences;
            },
            async destroy() {},
            dirty(path) {
                options.onDirty(path);
            },
            saved(event) {
                options.onSaved(event);
            },
            update(snapshot) {
                this.snapshot = {...this.snapshot, ...snapshot};
                options.onUpdate(this.snapshot);
            },
        };
        editors.push(state);
        return state;
    }

    function create_files(options) {
        const state = {
            options,
            contexts: [],
            destroyed: false,
            updateContext(context) {
                this.contexts.push(context);
            },
            getDebugState() {
                return {
                    menuOpen: false,
                    dialogKind: undefined,
                    mutationBusy: false,
                    awaitingRevision: undefined,
                };
            },
            destroy() {
                this.destroyed = true;
            },
        };
        file_controllers.push(state);
        return state;
    }

    const controller = workspace_shell.create_pear_workspace_shell({
        root,
        workspaceId: workspace_id,
        dependencies: {
            getWorkspace(id, callbacks) {
                const record = {id, callbacks, aborted: false};
                descriptors.push(record);
                return abortable(record);
            },
            getTree(id, request, callbacks) {
                const record = {id, request, callbacks, aborted: false};
                trees.push(record);
                return abortable(record);
            },
            search(id, query, limit, callbacks) {
                const record = {id, query, limit, callbacks, aborted: false};
                searches.push(record);
                return abortable(record);
            },
            refresh(id, callbacks) {
                const record = {id, callbacks, aborted: false};
                refreshes.push(record);
                return abortable(record);
            },
            rawUrl(id, entry_id, download = false) {
                return `/raw/${id}/${entry_id}${download ? "/download" : ""}`;
            },
            createEditor: create_editor,
            createFiles: create_files,
        },
    });

    function send_descriptor(value) {
        descriptors.at(-1).callbacks.success(value);
    }

    function send_tree(index, directory, entries, next_cursor = null, revision = 1) {
        trees[index].callbacks.success({
            workspace_uuid: workspace_id,
            workspace_revision: revision,
            directory,
            entries,
            next_cursor,
        });
    }

    function click(element, type = "click") {
        assert.ok(element !== null);
        element.dispatchEvent(new window.MouseEvent(type, {bubbles: true}));
    }

    return {
        root,
        controller,
        descriptors,
        trees,
        searches,
        refreshes,
        editors,
        file_controllers,
        send_descriptor,
        send_tree,
        click,
    };
}

run_test(
    "selection and preview tab chrome update before open; edit and double-click pin",
    async () => {
        const test = harness("workspace-selection");
        const current = descriptor("workspace-selection");
        test.send_descriptor(current);
        const files = [entry("A.md"), entry("B.md"), entry("C.md")];
        test.send_tree(0, current.root, files);

        test.click(test.root.querySelector("[data-entry-id='r1:A.md']"));
        const file_tab = test.root.querySelector("[data-tab-path='A.md']");
        assert.equal(file_tab.classList.contains("pear-workspace-file-tab"), true);
        assert.equal(
            file_tab.classList.contains("pear-workspace-tab"),
            false,
            "file tabs must not collide with the top-level Chat/Workspace delegation",
        );
        assert.deepEqual(test.editors[0].opens[0], {
            entry: files[0],
            selectedBeforeOpen: true,
            tabBeforeOpen: true,
        });
        test.click(test.root.querySelector("[data-entry-id='r1:B.md']"));
        assert.deepEqual(
            test.controller.getDebugState().tabs.map((tab) => tab.path),
            ["B.md"],
            "a clean preview replaces the prior preview",
        );
        test.editors[0].dirty("B.md");
        test.click(test.root.querySelector("[data-entry-id='r1:C.md']"));
        assert.deepEqual(
            test.controller.getDebugState().tabs.map((tab) => [tab.path, tab.preview]),
            [
                ["B.md", false],
                ["C.md", true],
            ],
        );
        test.click(test.root.querySelector("[data-entry-id='r1:C.md']"), "dblclick");
        assert.equal(test.controller.getDebugState().tabs.at(-1).preview, false);
        await test.controller.destroy();
        test.root.remove();
    },
);

run_test(
    "tree is bounded and keyboard/cached filter/collapse paths issue no extra request",
    async () => {
        const test = harness("workspace-tree");
        const current = descriptor("workspace-tree");
        test.send_descriptor(current);
        const folder = entry("docs", "directory");
        const many = Array.from({length: 1000}, (_value, index) => entry(`file-${index}.txt`));
        test.send_tree(0, current.root, [folder, ...many]);
        assert.ok(test.controller.getDebugState().renderedTreeRows <= 200);
        assert.equal(test.root.querySelector("[data-role='tree']").getAttribute("role"), "tree");

        const first = test.root.querySelector("[data-entry-id='r1:docs']");
        first.focus();
        first.dispatchEvent(new window.KeyboardEvent("keydown", {key: "ArrowDown", bubbles: true}));
        assert.equal(document.activeElement.getAttribute("role"), "treeitem");
        test.click(first);
        assert.equal(test.trees.length, 2);
        test.send_tree(1, folder, [entry("docs/guide.md")]);
        test.click(test.root.querySelector("[data-entry-id='r1:docs']"));
        test.click(test.root.querySelector("[data-entry-id='r1:docs']"));
        assert.equal(test.trees.length, 2, "re-expanding cached children must stay local");

        const input = test.root.querySelector("[data-role='search-input']");
        input.value = "guide";
        input.dispatchEvent(new window.Event("input", {bubbles: true}));
        assert.equal(test.searches.length, 0, "typing filters only the cached tree");
        test.root
            .querySelector("[data-role='search-form']")
            .dispatchEvent(new window.SubmitEvent("submit", {bubbles: true, cancelable: true}));
        assert.equal(test.searches.length, 1, "explicit submit reaches complete server search");
        await test.controller.destroy();
        test.root.remove();
    },
);

run_test("file tabs, mobile drawer, and Escape preserve focus and unsaved work", async () => {
    const test = harness("workspace-keyboard");
    const current = descriptor("workspace-keyboard");
    test.send_descriptor(current);
    const a = entry("A.md");
    const b = entry("B.md");
    test.send_tree(0, current.root, [a, b]);
    test.click(test.root.querySelector("[data-entry-id='r1:A.md']"), "dblclick");
    test.click(test.root.querySelector("[data-entry-id='r1:B.md']"), "dblclick");

    const first_tab = test.root.querySelector("[data-tab-path='A.md']");
    first_tab.focus();
    first_tab.dispatchEvent(
        new window.KeyboardEvent("keydown", {
            key: "ArrowRight",
            bubbles: true,
            cancelable: true,
        }),
    );
    assert.equal(test.controller.getDebugState().activePath, "B.md");
    assert.equal(document.activeElement.dataset.tabPath, "B.md");

    const explorer_toggle = test.root.querySelector("[data-action='toggle-explorer']");
    test.click(explorer_toggle);
    assert.equal(explorer_toggle.getAttribute("aria-expanded"), "true");
    assert.equal(document.activeElement, test.root.querySelector("[data-role='search-input']"));
    document.activeElement.dispatchEvent(
        new window.KeyboardEvent("keydown", {
            key: "Tab",
            shiftKey: true,
            bubbles: true,
            cancelable: true,
        }),
    );
    assert.equal(
        test.root.querySelector("#pear-workspace-explorer").contains(document.activeElement),
        true,
    );
    assert.notEqual(document.activeElement, test.root.querySelector("[data-role='search-input']"));
    document.activeElement.dispatchEvent(
        new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}),
    );
    assert.equal(explorer_toggle.getAttribute("aria-expanded"), "false");
    assert.equal(document.activeElement, explorer_toggle);

    test.click(explorer_toggle);
    test.click(document.body);
    assert.equal(explorer_toggle.getAttribute("aria-expanded"), "false");

    let escaped_to_parent = 0;
    const count_escape = (event) => {
        if (event.key === "Escape") {
            escaped_to_parent += 1;
        }
    };
    document.body.addEventListener("keydown", count_escape);
    test.editors[0].update({
        activeDocument: {path: "B.md", state: "dirty", editorMode: "source"},
    });
    test.root
        .querySelector("[data-role='editor']")
        .dispatchEvent(
            new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}),
        );
    assert.equal(escaped_to_parent, 0, "dirty documents keep Escape inside Workspace");
    assert.match(test.root.querySelector("[data-role='status']").textContent, /Finish saving/);

    test.editors[0].update({
        activeDocument: {path: "B.md", state: "conflict", editorMode: "source"},
    });
    test.root
        .querySelector("[data-role='editor']")
        .dispatchEvent(
            new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}),
        );
    assert.equal(escaped_to_parent, 0, "conflicts keep Escape inside Workspace");

    test.editors[0].update({
        activeDocument: {path: "B.md", state: "clean", editorMode: "source"},
    });
    test.root
        .querySelector("[data-role='editor']")
        .dispatchEvent(
            new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}),
        );
    assert.equal(escaped_to_parent, 1, "a clean editor lets the pane own Escape");
    document.body.removeEventListener("keydown", count_escape);

    await test.controller.destroy();
    test.root.remove();
});

run_test("root cursor pagination remains reachable and completes README absence", async () => {
    const test = harness("workspace-root-pages");
    const current = descriptor("workspace-root-pages");
    test.send_descriptor(current);
    const first_page = Array.from({length: 200}, (_value, index) => entry(`file-${index}.txt`));
    test.send_tree(0, current.root, first_page, "root-page-2");
    assert.equal(test.file_controllers[0].contexts.at(-1).readmeState, "unknown");
    const load_more = test.root.querySelector("[data-action='load-more-root']");
    assert.ok(load_more !== null);
    test.click(load_more);
    assert.equal(test.trees.length, 2);
    assert.deepEqual(test.trees[1].request, {
        directoryId: current.root.entry_id,
        cursor: "root-page-2",
        pageSize: 200,
    });
    test.send_tree(1, current.root, [entry("file-200.txt")], null);
    assert.equal(test.controller.getDebugState().tree.entries.size, 202);
    assert.equal(test.file_controllers[0].contexts.at(-1).readmeState, "absent");
    await test.controller.destroy();
    test.root.remove();
});

run_test("clicking a folder makes it the authoritative file-operation target", async () => {
    const test = harness("workspace-folder-target");
    const current = descriptor("workspace-folder-target");
    test.send_descriptor(current);
    const docs = entry("docs", "directory");
    const readme = entry("README.md");
    test.send_tree(0, current.root, [docs, readme]);
    test.click(test.root.querySelector("[data-entry-id='r1:README.md']"));
    test.click(test.root.querySelector("[data-entry-id='r1:docs']"));
    const context = test.file_controllers[0].contexts.at(-1);
    assert.equal(context.activeEntry.path, "README.md");
    assert.equal(context.selectedEntry.path, "docs");
    assert.equal(test.controller.getDebugState().tree.selectedPath, "docs");
    await test.controller.destroy();
    test.root.remove();
});

run_test("a save revision blocks stale tab IDs until refresh and path rebind", async () => {
    const test = harness("workspace-revision");
    const first = descriptor("workspace-revision");
    test.send_descriptor(first);
    const a = entry("A.md");
    const b = entry("B.md");
    test.send_tree(0, first.root, [a, b]);
    test.click(test.root.querySelector("[data-entry-id='r1:A.md']"), "dblclick");
    test.click(test.root.querySelector("[data-entry-id='r1:B.md']"), "dblclick");
    const opens_before_save = test.editors[0].opens.length;

    test.editors[0].saved({
        oldEntryId: a.entry_id,
        newEntryId: "r2:A.md",
        path: "A.md",
        workspaceRevision: 2,
    });
    assert.equal(test.refreshes.length, 1);
    test.click(test.root.querySelector("[data-tab-path='B.md']"));
    assert.equal(test.editors[0].opens.length, opens_before_save, "stale B cannot open");

    const second = descriptor("workspace-revision", 2);
    test.refreshes[0].callbacks.success({changed: true, workspace: second});
    assert.equal(test.searches.at(-1).query, "B.md");
    assert.equal(test.editors[0].opens.length, opens_before_save);
    const rebound_b = entry("B.md", "file", {entry_id: "r2:B.md"});
    test.searches.at(-1).callbacks.success({
        workspace_uuid: "workspace-revision",
        workspace_revision: 2,
        query: "B.md",
        entries: [{...rebound_b, ancestors: [second.root]}],
    });
    assert.equal(test.editors[0].opens.at(-1).entry.entry_id, "r2:B.md");
    assert.ok(
        test.editors[0].opens.at(-1).selectedBeforeOpen,
        "the rebound identity is selected before open",
    );
    await test.controller.destroy();
    test.root.remove();
});

run_test("Refresh retries an initial descriptor failure without remounting", async () => {
    const test = harness("workspace-descriptor-retry");
    test.descriptors[0].callbacks.error({
        kind: "transport",
        status: 503,
        message: "Temporarily unavailable",
    });
    await test.controller.ready;
    assert.match(test.root.querySelector("[data-role='status']").textContent, /unavailable/);
    test.click(test.root.querySelector("[data-action='refresh']"));
    assert.equal(test.descriptors.length, 2);
    assert.equal(test.refreshes.length, 0);
    const current = descriptor("workspace-descriptor-retry");
    test.descriptors[1].callbacks.success(current);
    assert.equal(test.controller.getDebugState().descriptor.uuid, "workspace-descriptor-retry");
    assert.equal(test.trees.length, 1);
    await test.controller.destroy();
    test.root.remove();
});

run_test(
    "shell restore keeps only tabs, active path, expansion, mode, and view state",
    async () => {
        const workspace_id = "workspace-restore";
        const first = harness(workspace_id);
        const current = descriptor(workspace_id);
        first.send_descriptor(current);
        const docs = entry("docs", "directory");
        const readme = entry("README.md");
        first.send_tree(0, current.root, [docs, readme]);
        first.click(first.root.querySelector("[data-entry-id='r1:docs']"));
        first.send_tree(1, docs, [entry("docs/guide.md")]);
        first.click(first.root.querySelector("[data-entry-id='r1:README.md']"), "dblclick");
        first.editors[0].preferences.mode = "rich";
        await first.controller.destroy();
        first.root.remove();

        const second = harness(workspace_id);
        second.send_descriptor(current);
        const restored = second.controller.getDebugState();
        assert.deepEqual(
            restored.tabs.map((tab) => [tab.path, tab.preview, tab.dirty]),
            [["README.md", false, false]],
        );
        assert.equal(restored.activePath, "README.md");
        assert.equal(restored.tree.expandedPaths.has("docs"), true);
        assert.deepEqual(second.editors[0].activations[0].preferences, {
            mode: "rich",
            documents: {
                "README.md": {
                    source: {selection: {anchor: 4, head: 2}, scrollTop: 31},
                    rich: {selection: {anchor: 3, head: 3}, scrollTop: 17},
                },
            },
        });
        assert.equal(
            Object.hasOwn(
                second.editors[0].activations[0].preferences.documents["README.md"],
                "content",
            ),
            false,
            "the shell cache must never retain document content",
        );
        await second.controller.destroy();
        second.root.remove();
    },
);

run_test("file mutations reconcile tabs and force revision-safe descriptor rebinds", async () => {
    const test = harness("workspace-mutations");
    const first = descriptor("workspace-mutations", 1, {
        capabilities: {
            read_file: true,
            write_file: true,
            refresh: true,
            create_entry: true,
            rename_entry: true,
            delete_entry: true,
            history: true,
        },
    });
    test.send_descriptor(first);
    const docs = entry("docs", "directory");
    test.send_tree(0, first.root, [docs]);
    assert.equal(test.file_controllers[0].contexts.at(-1).readmeState, "absent");
    test.click(test.root.querySelector("[data-entry-id='r1:docs']"));
    const a = entry("docs/A.md");
    const b = entry("docs/B.md");
    test.send_tree(1, docs, [a, b]);
    test.click(test.root.querySelector("[data-entry-id='r1:docs/A.md']"), "dblclick");
    test.click(test.root.querySelector("[data-entry-id='r1:docs/B.md']"), "dblclick");
    const opens_before_mutation = test.editors[0].opens.length;

    const archive = entry("archive", "directory", {entry_id: "r2:archive"});
    test.file_controllers[0].options.onMutation({
        operation: "rename",
        sourcePath: "docs",
        destinationPath: "archive",
        kind: "directory",
        mutation: {
            operation: "rename",
            workspace_uuid: "workspace-mutations",
            workspace_revision: 2,
            previous_entry_id: docs.entry_id,
            entry: archive,
            path: "archive",
        },
    });
    assert.deepEqual(
        test.controller.getDebugState().tabs.map((tab) => tab.path),
        ["archive/A.md", "archive/B.md"],
    );
    assert.equal(test.controller.getDebugState().activePath, "archive/B.md");
    assert.equal(test.refreshes.length, 1);
    assert.equal(test.file_controllers[0].contexts.at(-1).authorityStale, true);
    assert.equal(test.file_controllers[0].contexts.at(-1).activeEntry, undefined);
    assert.equal(test.editors[0].opens.length, opens_before_mutation);

    test.refreshes[0].callbacks.success({changed: false, workspace: first});
    assert.equal(test.file_controllers[0].contexts.at(-1).authorityStale, true);
    test.click(test.root.querySelector("[data-tab-path='archive/B.md']"));
    assert.equal(
        test.editors[0].opens.length,
        opens_before_mutation,
        "a refresh below the mutation receipt revision cannot revive stale IDs",
    );
    test.controller.refresh();
    assert.equal(test.refreshes.length, 2);
    const second = descriptor("workspace-mutations", 2);
    test.refreshes[1].callbacks.success({changed: true, workspace: second});
    const tab_rebind = test.searches.find((request) => request.query === "archive/B.md");
    assert.ok(tab_rebind !== undefined);
    const rebound_b = entry("archive/B.md", "file", {entry_id: "r2:archive/B.md"});
    tab_rebind.callbacks.success({
        workspace_uuid: "workspace-mutations",
        workspace_revision: 2,
        query: "archive/B.md",
        entries: [{...rebound_b, ancestors: [second.root, archive]}],
    });
    assert.equal(test.editors[0].opens.at(-1).entry.entry_id, "r2:archive/B.md");

    test.file_controllers[0].options.onMutation({
        operation: "delete",
        sourcePath: "archive",
        destinationPath: undefined,
        kind: "directory",
        mutation: {
            operation: "delete",
            workspace_uuid: "workspace-mutations",
            workspace_revision: 3,
            previous_entry_id: archive.entry_id,
            entry: null,
            path: "archive",
            removed_entries: 3,
        },
    });
    assert.deepEqual(test.controller.getDebugState().tabs, []);
    assert.equal(test.controller.getDebugState().activePath, undefined);
    assert.equal(test.refreshes.length, 3);
    const third = descriptor("workspace-mutations", 3);
    test.refreshes[2].callbacks.success({changed: true, workspace: third});

    const created = entry("New.md", "file", {entry_id: "r4:New.md"});
    test.file_controllers[0].options.onMutation({
        operation: "create",
        sourcePath: undefined,
        destinationPath: "New.md",
        kind: "file",
        mutation: {
            operation: "create",
            workspace_uuid: "workspace-mutations",
            workspace_revision: 4,
            previous_entry_id: null,
            entry: created,
            path: "New.md",
        },
    });
    assert.deepEqual(
        test.controller.getDebugState().tabs.map((tab) => [tab.path, tab.workspaceRevision]),
        [["New.md", 4]],
    );
    assert.equal(test.refreshes.length, 4);
    const fourth = descriptor("workspace-mutations", 4);
    test.refreshes[3].callbacks.success({changed: true, workspace: fourth});
    assert.equal(test.editors[0].opens.at(-1).entry.entry_id, "r4:New.md");

    await test.controller.destroy();
    assert.equal(test.file_controllers[0].destroyed, true);
    test.root.remove();
});
