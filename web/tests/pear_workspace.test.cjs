"use strict";

const assert = require("node:assert/strict");

const {jQueryFactory} = require("jquery/factory");
const {JSDOM} = require("jsdom");

const {mock_esm, mock_jquery, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM(
    `<!doctype html><html><body>
        <div class="column-middle-inner">
            <div id="message_feed_container">
                <div class="message_header_stream" data-height="42"></div>
            </div>
        </div>
        <span class="pear-workspace-tabs pear-workspace-tabs-hidden" role="tablist">
            <button class="pear-workspace-mode-tab pear-workspace-tab-selected" data-pear-tab="chat" role="tab">Chat</button>
            <button class="pear-workspace-mode-tab" data-pear-tab="workspace" role="tab">Workspace</button>
        </span>
    </body></html>`,
    {pretendToBeVisual: true, url: "http://zulip.zulipdev.com/"},
);
const {window} = dom;
window.scrollTo = () => {};
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
mock_esm("../src/pear_workspace_shell", {
    create_pear_workspace_shell(options) {
        const reject_ready = reject_next_ready;
        reject_next_ready = false;
        const mounted = {
            options,
            refreshes: 0,
            destroys: 0,
            ready: reject_ready ? Promise.reject(new Error("chunk failed")) : Promise.resolve(),
            refresh() {
                this.refreshes += 1;
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

async function settle() {
    await new Promise((resolve) => setImmediate(resolve));
    await Promise.resolve();
}

run_test("Chat and Workspace keep one shell mounted and reject retired tab state", async () => {
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

    click('[data-pear-tab="workspace"]');
    await settle();
    assert.equal(pear_workspace.pane_is_open(), true);
    assert.equal(mounts.length, 1);
    assert.equal(mounts[0].options.workspaceId, "workspace-1");
    assert.equal(document.querySelector("#pear-workspace-pane").hasAttribute("hidden"), false);
    assert.equal(
        document.querySelector('[data-pear-tab="workspace"]').getAttribute("aria-selected"),
        "true",
    );

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
});
