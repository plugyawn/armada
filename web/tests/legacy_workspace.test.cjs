"use strict";

const assert = require("node:assert/strict");

const {jQueryFactory} = require("jquery/factory");
const {JSDOM} = require("jsdom");

const {clock, mock_esm, mock_jquery, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const blueslip = require("./lib/zblueslip.cjs");

const dom = new JSDOM(
    `<!doctype html><html><body>
        <div class="column-middle-inner">
            <div id="message_feed_container">
                <div class="message_header_stream" data-height="42" data-header="old">
                    <span class="legacy-workspace-tabs legacy-workspace-tabs-hidden" role="tablist">
                        <button class="legacy-workspace-mode-tab legacy-workspace-tab-selected" data-legacy-tab="chat" role="tab">Chat</button>
                        <button class="legacy-workspace-mode-tab" data-legacy-tab="readme" role="tab">README</button>
                        <button class="legacy-workspace-mode-tab" data-legacy-tab="files" role="tab">Files</button>
                    </span>
                </div>
            </div>
        </div>
        <header data-armada="header">
            <div class="message_header_stream" data-header="active">
                <span class="legacy-workspace-tabs legacy-workspace-tabs-hidden" role="tablist">
                    <button class="legacy-workspace-mode-tab legacy-workspace-tab-selected" data-legacy-tab="chat" role="tab">Chat</button>
                    <button class="legacy-workspace-mode-tab" data-legacy-tab="readme" role="tab">README</button>
                    <button class="legacy-workspace-mode-tab" data-legacy-tab="files" role="tab">Files</button>
                </span>
            </div>
        </header>
        <div id="compose"></div>
    </body></html>`,
    {pretendToBeVisual: true, url: "http://zulip.zulipdev.com/"},
);
const {window} = dom;
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    sessionStorage: window.sessionStorage,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    CustomEvent: window.CustomEvent,
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
})) {
    set_global(name, value);
}
mock_jquery(jQueryFactory(window));

const mounts = [];
let reject_next_ready = false;
mock_esm("../src/legacy_workspace_shell", {
    create_armada_workspace_shell(options) {
        const reject_ready = reject_next_ready;
        reject_next_ready = false;
        const mounted = {
            options,
            refreshes: 0,
            quickOpens: 0,
            locationApplies: 0,
            rooms: [options.initialRoom],
            destroys: 0,
            ready: reject_ready ? Promise.reject(new Error("chunk failed")) : Promise.resolve(),
            refresh() {
                this.refreshes += 1;
            },
            openQuickOpen() {
                this.quickOpens += 1;
            },
            applyLocation() {
                this.locationApplies += 1;
            },
            setRoom(room) {
                this.rooms.push(room);
            },
            async destroy() {
                this.destroys += 1;
            },
        };
        mounts.push(mounted);
        return mounted;
    },
});

const {route} = zrequire("armada/state/route");
const legacy_workspace = zrequire("legacy_workspace");

function click(selector) {
    const element = document.querySelector(selector);
    assert.ok(element !== null);
    element.dispatchEvent(new window.MouseEvent("click", {bubbles: true}));
}

function keydown(element, key) {
    element.dispatchEvent(
        new window.KeyboardEvent("keydown", {key, bubbles: true, cancelable: true}),
    );
}

async function settle() {
    await Promise.resolve();
    await clock.runAllAsync();
    await Promise.resolve();
}

run_test("Chat, README, and Files keep one warm shell with separate rooms", async () => {
    window.history.replaceState({}, "", "/");
    sessionStorage.setItem('legacy-tab:["devel","topic"]', "retired-document-view");
    legacy_workspace.initialize();
    legacy_workspace.sync({
        stream: "Devel",
        topic: "Topic",
        workspaceId: "workspace-1",
    });
    assert.equal(legacy_workspace.pane_is_open(), false);
    assert.equal(
        document
            .querySelector(".legacy-workspace-tabs")
            .classList.contains("legacy-workspace-tabs-hidden"),
        false,
    );

    const active_header = document.querySelector("[data-header='active']");
    route.set({place: "worktree", view: "chat"});
    const chat_tab = active_header.querySelector('[data-legacy-tab="chat"]');
    chat_tab.focus();
    keydown(chat_tab, "ArrowRight");
    await settle();
    assert.equal(legacy_workspace.pane_is_open(), true);
    assert.equal(new URL(window.location.href).searchParams.get("armada_view"), "readme");
    assert.equal(mounts.length, 1);
    assert.equal(mounts[0].options.workspaceId, "workspace-1");
    assert.equal(document.querySelector("#legacy-workspace-pane").hasAttribute("hidden"), false);
    // The frame shows the view in front; the chat keeps its place under it.
    assert.deepEqual(route.get(), {place: "worktree", view: "readme"});
    assert.equal(
        document.querySelector('[data-legacy-tab="readme"]').getAttribute("aria-selected"),
        "true",
    );
    assert.equal(document.activeElement.dataset.legacyTab, "readme");
    const quick_open_event = new window.Event("armada:workspace-quick-open", {cancelable: true});
    assert.equal(document.dispatchEvent(quick_open_event), false);
    assert.equal(mounts[0].quickOpens, 1);
    assert.equal(
        document.querySelector('[data-legacy-tab="files"]').getAttribute("aria-selected"),
        "true",
    );
    assert.deepEqual(mounts[0].rooms, ["readme", "files"]);
    assert.deepEqual(route.get(), {place: "worktree", view: "files"});

    keydown(document.querySelector("#legacy-workspace-pane"), "Escape");
    assert.equal(legacy_workspace.pane_is_open(), false);
    assert.equal(new URL(window.location.href).searchParams.has("armada_view"), false);
    assert.equal(document.activeElement.dataset.legacyTab, "chat");
    assert.equal(
        document.activeElement.closest(".message_header_stream"),
        active_header,
        "Escape restores focus to the header's views, not the feed's older copy",
    );
    assert.equal(mounts[0].destroys, 0, "Escape keeps the warm shell mounted");
    assert.deepEqual(route.get(), {place: "worktree", view: "chat"});
    click('[data-legacy-tab="readme"]');
    await settle();
    assert.equal(mounts.length, 1);

    click('[data-legacy-tab="chat"]');
    assert.equal(legacy_workspace.pane_is_open(), false);
    assert.equal(mounts[0].destroys, 0, "Chat keeps the workspace shell warm");
    click('[data-legacy-tab="files"]');
    await settle();
    assert.equal(mounts.length, 1, "returning to Files reuses the shell");

    legacy_workspace.refresh_if_open_on_settle(true);
    legacy_workspace.refresh_if_open_on_settle(false);
    assert.equal(mounts[0].refreshes, 1);

    legacy_workspace.sync({
        stream: "Devel",
        topic: "Other topic",
        workspaceId: "workspace-2",
    });
    await settle();
    assert.equal(mounts[0].destroys, 1);
    assert.equal(legacy_workspace.pane_is_open(), false, "a new context starts in Chat");

    legacy_workspace.sync({stream: "Devel", topic: "Other topic", workspaceId: undefined});
    assert.equal(
        document
            .querySelector(".legacy-workspace-tabs")
            .classList.contains("legacy-workspace-tabs-hidden"),
        false,
        "the views stand before the worktree's context arrives",
    );
    await settle();

    legacy_workspace.sync({
        stream: "Devel",
        topic: "Retry topic",
        workspaceId: "workspace-retry",
    });
    reject_next_ready = true;
    blueslip.expect("error", "Workspace shell failed to load");
    click('[data-legacy-tab="readme"]');
    await settle();
    assert.equal(mounts.length, 2);
    assert.equal(mounts[1].destroys, 1);
    assert.match(document.querySelector(".legacy-workspace-host").textContent, /failed to load/);

    document.querySelector(".legacy-workspace-load-error button").focus();
    click(".legacy-workspace-load-error button");
    assert.equal(document.activeElement, document.querySelector(".legacy-workspace-host"));
    await settle();
    assert.equal(mounts.length, 3);
    assert.equal(mounts[2].options.workspaceId, "workspace-retry");
    assert.equal(document.querySelector(".legacy-workspace-host").hasAttribute("aria-busy"), false);

    window.history.replaceState(
        {},
        "",
        "/?armada_view=readme&armada_workspace=workspace-retry&armada_path=README.md&armada_mode=rich",
    );
    window.dispatchEvent(new window.PopStateEvent("popstate"));
    await settle();
    assert.equal(legacy_workspace.pane_is_open(), true);
    assert.equal(mounts[2].locationApplies, 1);
});
