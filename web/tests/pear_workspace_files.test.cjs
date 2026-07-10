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
    Event: window.Event,
    MouseEvent: window.MouseEvent,
    KeyboardEvent: window.KeyboardEvent,
    SubmitEvent: window.SubmitEvent,
})) {
    set_global(name, value);
}

const workspace_files = zrequire("pear_workspace_files");

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

function descriptor(revision = 1, capabilities = {}) {
    return {
        uuid: "workspace-files",
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
            create_entry: true,
            rename_entry: true,
            delete_entry: true,
            history: true,
            ...capabilities,
        },
        limits: {text_file_max_bytes: 1000, raw_file_max_bytes: null},
    };
}

function harness(copy_text = async () => {}) {
    const root = document.createElement("div");
    root.innerHTML = `
        <button type="button" data-role="button" aria-haspopup="menu" aria-expanded="false">File</button>
        <div data-role="menu" role="menu" hidden>
            <button type="button" role="menuitem" data-file-action="create-file">New file</button>
            <button type="button" role="menuitem" data-file-action="create-folder">New folder</button>
            <button type="button" role="menuitem" data-file-action="create-readme">Create README</button>
            <button type="button" role="menuitem" data-file-action="rename">Rename</button>
            <button type="button" role="menuitem" data-file-action="delete">Delete</button>
            <button type="button" role="menuitem" data-file-action="copy-path">Copy path</button>
            <button type="button" role="menuitem" data-file-action="reveal">Reveal</button>
            <button type="button" role="menuitem" data-file-action="history">History</button>
        </div>
        <div data-role="dialogs"></div>
    `;
    document.body.append(root);
    const menu_button = root.querySelector("[data-role='button']");
    const menu = root.querySelector("[data-role='menu']");
    const dialog_layer = root.querySelector("[data-role='dialogs']");
    const mutations = [];
    const histories = [];
    const announcements = [];
    const mutation_events = [];
    const reveals = [];
    let stale_count = 0;
    const abortable = (record) => ({
        abort() {
            record.aborted = true;
        },
    });
    const controller = workspace_files.create_pear_workspace_files({
        menuButton: menu_button,
        menu,
        dialogLayer: dialog_layer,
        announce(message) {
            announcements.push(message);
        },
        onMutation(event) {
            mutation_events.push(event);
        },
        onStaleAuthority() {
            stale_count += 1;
        },
        onReveal(revealed_entry) {
            reveals.push(revealed_entry);
        },
        dependencies: {
            createEntry(id, request, callbacks) {
                const record = {operation: "create", id, request, callbacks, aborted: false};
                mutations.push(record);
                return abortable(record);
            },
            renameEntry(id, request, callbacks) {
                const record = {operation: "rename", id, request, callbacks, aborted: false};
                mutations.push(record);
                return abortable(record);
            },
            deleteEntry(id, request, callbacks) {
                const record = {operation: "delete", id, request, callbacks, aborted: false};
                mutations.push(record);
                return abortable(record);
            },
            getHistory(id, entry_id, options, callbacks) {
                const record = {id, entry_id, options, callbacks, aborted: false};
                histories.push(record);
                return abortable(record);
            },
            copyText: copy_text,
        },
    });

    function set_context({
        revision = 1,
        active_entry = entry("README.md"),
        selected_entry,
        readme_state = "present",
        authority_stale = false,
        capabilities = {},
    } = {}) {
        controller.updateContext({
            descriptor: descriptor(revision, capabilities),
            activeEntry: active_entry,
            selectedEntry: selected_entry,
            readmeState: readme_state,
            authorityStale: authority_stale,
        });
    }

    function action(name) {
        return root.querySelector(`[data-file-action='${name}']`);
    }

    function open_action(name) {
        menu_button.click();
        action(name).click();
    }

    return {
        root,
        menu_button,
        menu,
        dialog_layer,
        controller,
        mutations,
        histories,
        announcements,
        mutation_events,
        reveals,
        stale_count: () => stale_count,
        set_context,
        action,
        open_action,
    };
}

function mutation_receipt(operation, revision, path, mutation_entry, previous_entry_id = null) {
    return {
        operation,
        workspace_uuid: "workspace-files",
        workspace_revision: revision,
        previous_entry_id,
        entry: mutation_entry,
        path,
    };
}

run_test("file menu capability gates actions and implements menu keyboard behavior", () => {
    const test = harness();
    test.set_context({
        capabilities: {
            create_entry: false,
            rename_entry: false,
            delete_entry: false,
        },
    });
    assert.equal(test.action("create-file").hidden, true);
    assert.equal(test.action("rename").hidden, true);
    assert.equal(test.action("copy-path").hidden, false);

    test.menu_button.click();
    assert.equal(document.activeElement, test.action("copy-path"));
    test.menu.dispatchEvent(new window.KeyboardEvent("keydown", {key: "ArrowDown", bubbles: true}));
    assert.equal(document.activeElement, test.action("reveal"));
    test.menu.dispatchEvent(new window.KeyboardEvent("keydown", {key: "End", bubbles: true}));
    assert.equal(document.activeElement, test.action("history"));
    let escaped_to_parent = 0;
    test.root.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            escaped_to_parent += 1;
        }
    });
    test.menu.dispatchEvent(new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    assert.equal(test.menu.hidden, true);
    assert.equal(document.activeElement, test.menu_button);
    assert.equal(escaped_to_parent, 0, "the menu consumes Escape before the workspace pane");

    test.set_context({authority_stale: true});
    assert.equal(test.menu_button.hidden, true);
    test.action("create-file").click();
    assert.equal(test.mutations.length, 0, "hidden actions cannot bypass capability authority");
    test.controller.destroy();
    test.root.remove();
});

run_test("create retries transport failure and revision-gates a validated receipt", () => {
    const test = harness();
    test.set_context({readme_state: "absent"});
    test.open_action("create-file");
    const form = test.dialog_layer.querySelector("form");
    const input = form.querySelector("input[name='path']");
    input.value = "notes/today.md";
    form.dispatchEvent(new window.SubmitEvent("submit", {bubbles: true, cancelable: true}));
    assert.deepEqual(test.mutations[0].request, {
        destinationPath: "notes/today.md",
        kind: "file",
        workspaceRevision: 1,
        content: "",
    });
    test.mutations[0].callbacks.error({kind: "transport", status: 503, message: "Try again"});
    assert.equal(test.dialog_layer.querySelector("[role='alert']").textContent, "Try again");
    form.dispatchEvent(new window.SubmitEvent("submit", {bubbles: true, cancelable: true}));
    const created = entry("notes/today.md", "file", {entry_id: "r2:notes/today.md"});
    test.mutations[1].callbacks.success(mutation_receipt("create", 2, created.path, created));
    assert.equal(test.mutation_events.length, 1);
    assert.equal(test.controller.getDebugState().awaitingRevision, 2);
    assert.equal(test.menu_button.disabled, true);
    test.set_context({revision: 2, active_entry: created});
    assert.equal(test.controller.getDebugState().awaitingRevision, undefined);
    assert.equal(test.menu_button.disabled, false);

    test.open_action("create-folder");
    const second_form = test.dialog_layer.querySelector("form");
    second_form.querySelector("input[name='path']").value = "archive";
    second_form.dispatchEvent(new window.SubmitEvent("submit", {bubbles: true, cancelable: true}));
    test.mutations[2].callbacks.success(
        mutation_receipt("create", 3, "wrong-path", entry("wrong-path", "directory")),
    );
    assert.equal(test.mutation_events.length, 1, "invalid receipts never mutate local state");
    assert.equal(test.stale_count(), 1);
    assert.equal(test.controller.getDebugState().dialogKind, undefined);
    test.controller.destroy();
    test.root.remove();
});

run_test("README creation is offered only after a complete absence is known", () => {
    const test = harness();
    test.set_context({active_entry: undefined, readme_state: "unknown"});
    assert.equal(test.action("create-readme").hidden, true);
    test.set_context({active_entry: undefined, readme_state: "absent"});
    assert.equal(test.action("create-readme").hidden, false);
    test.open_action("create-readme");
    assert.deepEqual(test.mutations[0].request, {
        destinationPath: "README.md",
        kind: "file",
        content: "",
        workspaceRevision: 1,
    });
    test.set_context({active_entry: undefined, readme_state: "present"});
    assert.equal(test.mutations[0].aborted, true);
    assert.equal(test.action("create-readme").hidden, true);
    test.controller.destroy();
    test.root.remove();
});

run_test("rename races are aborted and recursive deletion requires explicit confirmation", () => {
    const test = harness();
    const file = entry("draft.md");
    test.set_context({active_entry: file, selected_entry: file});
    test.open_action("rename");
    let form = test.dialog_layer.querySelector("form");
    form.querySelector("input[name='path']").value = "done.md";
    form.dispatchEvent(new window.SubmitEvent("submit", {bubbles: true, cancelable: true}));
    test.set_context({revision: 2, active_entry: entry("other.md")});
    assert.equal(test.mutations[0].aborted, true);
    test.mutations[0].callbacks.success(
        mutation_receipt("rename", 2, "done.md", entry("done.md"), file.entry_id),
    );
    assert.equal(test.mutation_events.length, 0, "late rename response is ignored");

    const folder = entry("scratch", "directory", {has_children: true, entry_id: "r2:scratch"});
    test.set_context({revision: 2, active_entry: undefined, selected_entry: folder});
    test.open_action("delete");
    form = test.dialog_layer.querySelector("form");
    const submit = form.querySelector("button[type='submit']");
    const recursive = form.querySelector("input[name='recursive']");
    const irreversible = form.querySelector("input[name='irreversible']");
    assert.equal(submit.disabled, true);
    recursive.checked = true;
    recursive.dispatchEvent(new window.Event("change", {bubbles: true}));
    assert.equal(submit.disabled, true, "recursive deletion still requires irreversible consent");
    irreversible.checked = true;
    irreversible.dispatchEvent(new window.Event("change", {bubbles: true}));
    assert.equal(submit.disabled, false);
    form.dispatchEvent(new window.SubmitEvent("submit", {bubbles: true, cancelable: true}));
    assert.deepEqual(test.mutations[1].request, {
        entryId: folder.entry_id,
        expectedPath: "scratch",
        workspaceRevision: 2,
        recursive: true,
        confirmIrreversible: true,
    });
    test.controller.destroy();
    test.root.remove();
});

run_test("copy, reveal, and paginated history stay bound to the active identity", async () => {
    const copied = [];
    const test = harness(async (text) => copied.push(text));
    const file = entry("docs/guide.md");
    test.set_context({active_entry: file});
    test.open_action("copy-path");
    await Promise.resolve();
    assert.deepEqual(copied, ["docs/guide.md"]);
    assert.equal(test.announcements.at(-1), "Copied docs/guide.md");

    test.open_action("reveal");
    assert.deepEqual(test.reveals, [file]);

    test.open_action("history");
    assert.deepEqual(test.histories[0].options, {limit: 50});
    test.histories[0].callbacks.success({
        workspace_uuid: "workspace-files",
        workspace_revision: 1,
        entry: file,
        commits: [
            {
                commit: "111111111111",
                authored_at: 1_700_000_000,
                author_name: "Ada",
                subject: "One",
            },
        ],
        next_cursor: "next-page",
    });
    assert.match(test.dialog_layer.textContent, /One/);
    test.dialog_layer.querySelector("[data-history-more]").click();
    assert.equal(test.histories[1].options.cursor, "next-page");
    test.histories[1].callbacks.success({
        workspace_uuid: "workspace-files",
        workspace_revision: 1,
        entry: file,
        commits: [
            {
                commit: "111111111111",
                authored_at: 1_700_000_000,
                author_name: "Ada",
                subject: "One",
            },
            {
                commit: "222222222222",
                authored_at: 1_700_000_100,
                author_name: "Lin",
                subject: "Two",
            },
        ],
        next_cursor: null,
    });
    assert.equal(test.dialog_layer.querySelectorAll("li").length, 2);
    const close = test.dialog_layer.querySelector("[aria-label='Close dialog']");
    let escaped_to_parent = 0;
    test.root.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            escaped_to_parent += 1;
        }
    });
    close.focus();
    close.dispatchEvent(new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    assert.equal(test.controller.getDebugState().dialogKind, undefined);
    assert.equal(document.activeElement, test.menu_button);
    assert.equal(escaped_to_parent, 0, "the dialog consumes Escape before the workspace pane");

    test.open_action("history");
    test.histories[2].callbacks.error({
        kind: "server",
        status: 409,
        error: {result: "error", msg: "Changed", error_code: "stale_entry"},
    });
    assert.equal(test.controller.getDebugState().dialogKind, undefined);
    assert.equal(test.stale_count(), 1, "stale history identities force a descriptor refresh");
    test.controller.destroy();
    test.root.remove();
});
