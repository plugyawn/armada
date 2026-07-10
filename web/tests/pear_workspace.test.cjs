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
                        <button class="pear-workspace-mode-tab" data-pear-tab="workspace" role="tab">Workspace</button>
                    </span>
                </div>
                <div class="message_header_stream" data-height="42" data-header="active">
                    <span class="pear-workspace-tabs pear-workspace-tabs-hidden" role="tablist">
                        <button class="pear-workspace-mode-tab pear-workspace-tab-selected" data-pear-tab="chat" role="tab">Chat</button>
                        <button class="pear-workspace-mode-tab" data-pear-tab="workspace" role="tab">Workspace</button>
                    </span>
                </div>
            </div>
        </div>
        <div id="compose"></div>
    </body></html>`,
    {pretendToBeVisual: true, url: "http://zulip.zulipdev.com/"},
);
const {window} = dom;
window.scrollTo = () => {};
const resize_observers = [];
class ResizeObserver {
    constructor(callback) {
        this.callback = callback;
        resize_observers.push(this);
    }

    observe(target) {
        this.target = target;
    }

    disconnect() {
        this.target = undefined;
    }

    trigger() {
        this.callback([], this);
    }
}
let compose_height = 144;
window.document.querySelector("#compose").getBoundingClientRect = () => ({height: compose_height});
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    sessionStorage: window.sessionStorage,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    CustomEvent: window.CustomEvent,
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    ResizeObserver,
})) {
    set_global(name, value);
}
mock_jquery(jQueryFactory(window));

const mounts = [];
let reject_next_ready = false;
mock_esm("../src/pear_workspace_shell", {
    create_pear_workspace_shell(options) {
        const reject_ready = reject_next_ready;
        reject_next_ready = false;
        const mounted = {
            options,
            refreshes: 0,
            quickOpens: 0,
            locationApplies: 0,
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
            async destroy() {
                this.destroys += 1;
            },
        };
        mounts.push(mounted);
        return mounted;
    },
});

const pear_workspace = zrequire("pear_workspace");

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

run_test("Chat and Workspace keep one shell mounted and reject retired tab state", async () => {
    window.history.replaceState({}, "", "/");
    sessionStorage.setItem('pear-tab:["devel","topic"]', "files");
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
    const chat_tab = active_header.querySelector('[data-pear-tab="chat"]');
    chat_tab.focus();
    keydown(chat_tab, "ArrowRight");
    await settle();
    assert.equal(pear_workspace.pane_is_open(), true);
    assert.equal(new URL(window.location.href).searchParams.get("pear_view"), "workspace");
    assert.equal(mounts.length, 1);
    assert.equal(mounts[0].options.workspaceId, "workspace-1");
    assert.equal(document.querySelector("#pear-workspace-pane").hasAttribute("hidden"), false);
    assert.equal(
        document.querySelector('[data-pear-tab="workspace"]').getAttribute("aria-selected"),
        "true",
    );
    assert.equal(document.activeElement.dataset.pearTab, "workspace");
    const quick_open_event = new window.Event("pear:workspace-quick-open", {cancelable: true});
    assert.equal(document.dispatchEvent(quick_open_event), false);
    assert.equal(mounts[0].quickOpens, 1);
    assert.equal(
        document
            .querySelector(".column-middle-inner")
            .style.getPropertyValue("--pear-workspace-compose-height"),
        "144px",
    );
    compose_height = 216;
    resize_observers.at(-1).trigger();
    assert.equal(
        document
            .querySelector(".column-middle-inner")
            .style.getPropertyValue("--pear-workspace-compose-height"),
        "216px",
    );

    keydown(document.querySelector("#pear-workspace-pane"), "Escape");
    assert.equal(pear_workspace.pane_is_open(), false);
    assert.equal(new URL(window.location.href).searchParams.has("pear_view"), false);
    assert.equal(document.activeElement.dataset.pearTab, "chat");
    assert.equal(
        document.activeElement.closest(".message_header_stream"),
        active_header,
        "Escape restores focus to the current visible topic header, not an older duplicate",
    );
    assert.equal(mounts[0].destroys, 0, "Escape keeps the warm shell mounted");
    click('[data-pear-tab="workspace"]');
    await settle();
    assert.equal(mounts.length, 1);

    click('[data-pear-tab="chat"]');
    assert.equal(pear_workspace.pane_is_open(), false);
    assert.equal(mounts[0].destroys, 0, "Chat keeps the workspace shell warm");
    click('[data-pear-tab="workspace"]');
    await settle();
    assert.equal(mounts.length, 1, "returning to Workspace reuses the shell");

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
        true,
    );
    await settle();

    pear_workspace.sync({
        stream: "Devel",
        topic: "Retry topic",
        workspaceId: "workspace-retry",
    });
    reject_next_ready = true;
    blueslip.expect("error", "Pear workspace shell failed to load");
    click('[data-pear-tab="workspace"]');
    await settle();
    assert.equal(mounts.length, 2);
    assert.equal(mounts[1].destroys, 1);
    assert.match(document.querySelector(".pear-workspace-host").textContent, /failed to load/);

    click('[data-pear-tab="chat"]');
    click('[data-pear-tab="workspace"]');
    await settle();
    assert.equal(mounts.length, 3);
    assert.equal(mounts[2].options.workspaceId, "workspace-retry");
    assert.equal(document.querySelector(".pear-workspace-host").hasAttribute("aria-busy"), false);

    window.history.replaceState(
        {},
        "",
        "/?pear_view=workspace&pear_workspace=workspace-retry&pear_path=README.md&pear_mode=rich",
    );
    window.dispatchEvent(new window.PopStateEvent("popstate"));
    await settle();
    assert.equal(pear_workspace.pane_is_open(), true);
    assert.equal(mounts[2].locationApplies, 1);
});
