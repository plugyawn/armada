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
                    <span class="pear-workspace-tabs pear-workspace-tabs-hidden" role="tablist">
                        <button class="pear-workspace-mode-tab pear-workspace-tab-selected" data-pear-tab="chat" role="tab">Chat</button>
                        <button class="pear-workspace-mode-tab" data-pear-tab="readme" role="tab">README</button>
                        <button class="pear-workspace-mode-tab" data-pear-tab="files" role="tab">Files</button>
                    </span>
                </div>
            </div>
        </div>
        <header data-armada="header">
            <div class="message_header_stream" data-header="active">
                <span class="pear-workspace-tabs pear-workspace-tabs-hidden" role="tablist">
                    <button class="pear-workspace-mode-tab pear-workspace-tab-selected" data-pear-tab="chat" role="tab">Chat</button>
                    <button class="pear-workspace-mode-tab" data-pear-tab="readme" role="tab">README</button>
                    <button class="pear-workspace-mode-tab" data-pear-tab="files" role="tab">Files</button>
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
    create_pear_workspace_shell(options) {
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
const pear_workspace = zrequire("legacy_workspace");

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
    sessionStorage.setItem('pear-tab:["devel","topic"]', "retired-document-view");
    pear_workspace.initialize();
    pear_workspace.sync({
        stream: "Devel",
        topic: "Topic",
        workspaceId: "workspace-1",
    });
    assert.equal(pear_workspace.pane_is_open(), false);
    assert.equal(
        document
            .querySelector(".pear-workspace-tabs")
            .classList.contains("pear-workspace-tabs-hidden"),
        false,
    );

    const active_header = document.querySelector("[data-header='active']");
    route.set({place: "worktree", view: "chat"});
    const chat_tab = active_header.querySelector('[data-pear-tab="chat"]');
    chat_tab.focus();
    keydown(chat_tab, "ArrowRight");
    await settle();
    assert.equal(pear_workspace.pane_is_open(), true);
    assert.equal(new URL(window.location.href).searchParams.get("pear_view"), "readme");
    assert.equal(mounts.length, 1);
    assert.equal(mounts[0].options.workspaceId, "workspace-1");
    assert.equal(document.querySelector("#pear-workspace-pane").hasAttribute("hidden"), false);
    // The frame shows the view in front; the chat keeps its place under it.
    assert.deepEqual(route.get(), {place: "worktree", view: "readme"});
    assert.equal(
        document.querySelector('[data-pear-tab="readme"]').getAttribute("aria-selected"),
        "true",
    );
    assert.equal(document.activeElement.dataset.pearTab, "readme");
    const quick_open_event = new window.Event("pear:workspace-quick-open", {cancelable: true});
    assert.equal(document.dispatchEvent(quick_open_event), false);
    assert.equal(mounts[0].quickOpens, 1);
    assert.equal(
        document.querySelector('[data-pear-tab="files"]').getAttribute("aria-selected"),
        "true",
    );
    assert.deepEqual(mounts[0].rooms, ["readme", "files"]);
    assert.deepEqual(route.get(), {place: "worktree", view: "files"});

    keydown(document.querySelector("#pear-workspace-pane"), "Escape");
    assert.equal(pear_workspace.pane_is_open(), false);
    assert.equal(new URL(window.location.href).searchParams.has("pear_view"), false);
    assert.equal(document.activeElement.dataset.pearTab, "chat");
    assert.equal(
        document.activeElement.closest(".message_header_stream"),
        active_header,
        "Escape restores focus to the header's views, not the feed's older copy",
    );
    assert.equal(mounts[0].destroys, 0, "Escape keeps the warm shell mounted");
    assert.deepEqual(route.get(), {place: "worktree", view: "chat"});
    click('[data-pear-tab="readme"]');
    await settle();
    assert.equal(mounts.length, 1);

    click('[data-pear-tab="chat"]');
    assert.equal(pear_workspace.pane_is_open(), false);
    assert.equal(mounts[0].destroys, 0, "Chat keeps the workspace shell warm");
    click('[data-pear-tab="files"]');
    await settle();
    assert.equal(mounts.length, 1, "returning to Files reuses the shell");

    pear_workspace.refresh_if_open_on_settle(true);
    pear_workspace.refresh_if_open_on_settle(false);
    assert.equal(mounts[0].refreshes, 1);

    pear_workspace.sync({
        stream: "Devel",
        topic: "Other topic",
        workspaceId: "workspace-2",
    });
    await settle();
    assert.equal(mounts[0].destroys, 1);
    assert.equal(pear_workspace.pane_is_open(), false, "a new context starts in Chat");

    pear_workspace.sync({stream: "Devel", topic: "Other topic", workspaceId: undefined});
    assert.equal(
        document
            .querySelector(".pear-workspace-tabs")
            .classList.contains("pear-workspace-tabs-hidden"),
        false,
        "the views stand before the worktree's context arrives",
    );
    await settle();

    pear_workspace.sync({
        stream: "Devel",
        topic: "Retry topic",
        workspaceId: "workspace-retry",
    });
    reject_next_ready = true;
    blueslip.expect("error", "Pear workspace shell failed to load");
    click('[data-pear-tab="readme"]');
    await settle();
    assert.equal(mounts.length, 2);
    assert.equal(mounts[1].destroys, 1);
    assert.match(document.querySelector(".pear-workspace-host").textContent, /failed to load/);

    document.querySelector(".pear-workspace-load-error button").focus();
    click(".pear-workspace-load-error button");
    assert.equal(document.activeElement, document.querySelector(".pear-workspace-host"));
    await settle();
    assert.equal(mounts.length, 3);
    assert.equal(mounts[2].options.workspaceId, "workspace-retry");
    assert.equal(document.querySelector(".pear-workspace-host").hasAttribute("aria-busy"), false);

    window.history.replaceState(
        {},
        "",
        "/?pear_view=readme&pear_workspace=workspace-retry&pear_path=README.md&pear_mode=rich",
    );
    window.dispatchEvent(new window.PopStateEvent("popstate"));
    await settle();
    assert.equal(pear_workspace.pane_is_open(), true);
    assert.equal(mounts[2].locationApplies, 1);
});
