"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {pathToFileURL} = require("node:url");

const {JSDOM} = require("jsdom");

const namespace = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {set_global} = namespace;

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    pretendToBeVisual: true,
    url: "http://zulip.zulipdev.com/",
});
const {window} = dom;
const fake_clear_timeout = global.clearTimeout;
function clear_timeout(timer) {
    if (timer !== -1) {
        fake_clear_timeout(timer);
    }
}

class ResizeObserver {
    observe() {}

    unobserve() {}

    disconnect() {}
}

class IntersectionObserver {
    observe() {}

    unobserve() {}

    disconnect() {}
}

const css = {escape: require("css.escape")};
const match_media = () => ({
    matches: false,
    media: "",
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
        return true;
    },
});
window.matchMedia = match_media;
window.HTMLElement.prototype.scrollIntoView = () => {};
window.Range.prototype.getBoundingClientRect = () => new window.DOMRect();
window.Range.prototype.getClientRects = () => [];

for (const [name, value] of Object.entries({
    window,
    document: window.document,
    navigator: window.navigator,
    MutationObserver: window.MutationObserver,
    ResizeObserver,
    IntersectionObserver,
    getComputedStyle: window.getComputedStyle.bind(window),
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
    clearTimeout: clear_timeout,
    addEventListener: window.addEventListener.bind(window),
    removeEventListener: window.removeEventListener.bind(window),
    dispatchEvent: window.dispatchEvent.bind(window),
    DOMParser: window.DOMParser,
    XMLSerializer: window.XMLSerializer,
    DOMRect: window.DOMRect,
    Range: window.Range,
    Selection: window.Selection,
    Node: window.Node,
    Text: window.Text,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    SVGElement: window.SVGElement,
    DocumentFragment: window.DocumentFragment,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
    KeyboardEvent: window.KeyboardEvent,
    MouseEvent: window.MouseEvent,
    FocusEvent: window.FocusEvent,
    CSS: css,
    matchMedia: match_media,
})) {
    set_global(name, value);
}

let next_idle_handle = 1;
const idle_callbacks = new Map();
set_global("requestIdleCallback", (callback) => {
    const handle = next_idle_handle;
    next_idle_handle += 1;
    idle_callbacks.set(handle, callback);
    return handle;
});
set_global("cancelIdleCallback", (handle) => {
    idle_callbacks.delete(handle);
});

const markdown_editor_path = path.resolve(__dirname, "../src/pear_workspace_markdown_editor.ts");
const app_bundle_path = path.resolve(__dirname, "../src/bundles/app.ts");
const editor_module = import(pathToFileURL(markdown_editor_path).href);

function prose_mirror(root) {
    const element = root.querySelector(".ProseMirror");
    assert.ok(element instanceof window.HTMLElement);
    return element;
}

function paste_text(root, text) {
    const event = new window.Event("paste", {bubbles: true, cancelable: true});
    Object.defineProperty(event, "clipboardData", {
        value: {
            files: [],
            getData(type) {
                return type === "text/plain" || type === "Text" ? text : "";
            },
        },
    });
    prose_mirror(root).dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
}

function undo_with_keyboard(root) {
    const event = new window.KeyboardEvent("keydown", {
        key: "z",
        code: "KeyZ",
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
    });
    prose_mirror(root).dispatchEvent(event);
    return event.defaultPrevented;
}

function run_idle_callbacks() {
    const callbacks = [...idle_callbacks.values()];
    idle_callbacks.clear();
    for (const callback of callbacks) {
        callback({didTimeout: false, timeRemaining: () => 50});
    }
}

async function with_timeout(promise, label) {
    const pending = Symbol("pending");
    for (let attempt = 0; attempt < 20; attempt += 1) {
        const result = await Promise.race([promise, Promise.resolve(pending)]);
        if (result !== pending) {
            return result;
        }
        await namespace.clock.runAllAsync();
    }
    throw new Error(`${label} did not settle after draining timers`);
}

run_test("keeps selective Crepe features and themes behind the Rich lazy boundary", () => {
    const source = fs.readFileSync(markdown_editor_path, "utf8");
    const app_bundle = fs.readFileSync(app_bundle_path, "utf8");
    assert.ok(!source.includes('from "@milkdown/crepe"'));
    assert.ok(!source.includes('from "@codemirror/language-data"'));
    for (const feature of [
        "block-edit",
        "code-mirror",
        "latex",
        "link-tooltip",
        "list-item",
        "placeholder",
        "table",
        "toolbar",
    ]) {
        assert.ok(source.includes(`from "@milkdown/crepe/feature/${feature}"`), feature);
    }
    assert.ok(source.includes('import("@milkdown/crepe/theme/common/style.css")'));
    assert.ok(source.includes('import("@milkdown/crepe/theme/frame.css")'));
    assert.ok(!app_bundle.includes("@milkdown/crepe/theme/"));
});

run_test("fails closed on syntax the production Rich profile cannot preserve", async () => {
    const markdown_editor = await editor_module;
    const cases = new Map([
        ["---\ntitle: Test\n---\n# Body\n", "frontmatter"],
        ["+++\ntitle = 'Test'\n+++\nBody\n", "frontmatter"],
        ["Text <span>raw</span>\n", "raw_html"],
        ["Text <!-- hidden -->\n", "raw_html"],
        ["[label]: https://example.com\n\n[label]\n", "reference"],
        ["[text][label]\n", "reference"],
        ["Text with a footnote[^1].\n\n[^1]: note\n", "footnote"],
        ["export const value = 1;\n\n# MDX\n", "mdx"],
        ["Text {component} expression\n", "mdx"],
        ["Text {value: {nested: true}} expression\n", "mdx"],
        ["<>fragment</>\n", "mdx"],
        ["Escaped \\*asterisk\\*\n", "escape"],
        [":::note\ncustom directive\n:::\n", "extension"],
        ["A [[wiki link]]\n", "extension"],
    ]);

    for (const [markdown, expected_code] of cases) {
        const result = markdown_editor.rich_source_only_reason(markdown);
        assert.equal(result?.eligible, false, markdown);
        assert.equal(result?.code, expected_code, markdown);
        assert.ok(result.reason.length > 0);
    }
});

run_test(
    "does not mistake protected code, math, autolinks, or a thematic break for extensions",
    async () => {
        const markdown_editor = await editor_module;
        const markdown = [
            "---",
            "",
            "`<span>` and `$value`",
            "",
            "```html",
            "<!-- literal code -->",
            "<Component value={x} />",
            "```",
            "",
            "$\\notacommand{<tag>}$",
            "",
            "<https://example.com>",
            "",
        ].join("\n");
        assert.equal(markdown_editor.rich_source_only_reason(markdown), undefined);
    },
);

run_test(
    "production parser round-trips the supported profile and keeps invalid math editable",
    async () => {
        const markdown_editor = await editor_module;
        const markdown = [
            "# Heading",
            "",
            "**bold**, *emphasis*, ~~strike~~, `code`, and [link](https://example.com).",
            "",
            "> quote",
            "",
            "- item",
            "- [x] task",
            "",
            "| Left | Right |",
            "| :--- | ---: |",
            "| one | two |",
            "",
            "```ts",
            "const value: number = 1;",
            "```",
            "",
            "Inline $x^2$ and invalid $\\definitelyUnknown{math}$ remain editable.",
            "",
            "$$",
            "\\sum_{i=1}^n i",
            "$$",
            "",
        ].join("\n");
        const body_children = document.body.childElementCount;

        assert.deepEqual(
            await with_timeout(markdown_editor.can_enter_rich(markdown), "Rich validation"),
            {eligible: true},
        );
        assert.equal(
            document.body.childElementCount,
            body_children,
            "validation must use a detached root",
        );
    },
);

run_test("rejects unsupported input before touching the target root", async () => {
    const markdown_editor = await editor_module;
    const root = document.createElement("div");
    root.textContent = "sentinel";
    document.body.append(root);

    const result = await with_timeout(
        markdown_editor.create_pear_workspace_markdown_editor({
            root,
            markdown: "# Title\n\n<div>unsafe</div>\n",
            editRevision: 1,
            onDirty() {
                assert.fail("unsupported input cannot dirty a document");
            },
            onSerialized() {
                assert.fail("unsupported input cannot serialize");
            },
        }),
        "unsupported Rich creation",
    );

    assert.equal(result.ok, false);
    assert.equal(result.eligibility.code, "raw_html");
    assert.equal(root.textContent, "sentinel");
    root.remove();
});

run_test(
    "tracks only user doc transactions and coalesces one serialization per revision",
    async () => {
        const markdown_editor = await editor_module;
        idle_callbacks.clear();
        const root = document.createElement("div");
        document.body.append(root);
        const dirty_events = [];
        const serialized_results = [];
        const result = await with_timeout(
            markdown_editor.create_pear_workspace_markdown_editor({
                root,
                markdown: "# Start\n\nBody with $x^2$.\n",
                editRevision: 7,
                selection: {anchor: 1, head: 1},
                scrollTop: 12,
                onDirty(event) {
                    dirty_events.push(event);
                },
                onSerialized(serialized) {
                    serialized_results.push(serialized);
                },
            }),
            "Rich creation",
        );
        assert.equal(result.ok, true);
        const {editor} = result;
        prose_mirror(root);

        assert.equal(editor.getStats().serializations, 0);
        assert.deepEqual(dirty_events, []);
        assert.deepEqual(editor.getViewState(), {
            selection: {anchor: 1, head: 1},
            scrollTop: 12,
        });

        paste_text(root, "A");
        paste_text(root, "B");
        assert.deepEqual(dirty_events, [
            {origin: "user", editRevision: 8},
            {origin: "user", editRevision: 9},
        ]);
        assert.equal(editor.getStats().serializations, 0, "typing must not serialize Markdown");
        assert.equal(idle_callbacks.size, 1, "multiple edits share one idle flush");

        run_idle_callbacks();
        await Promise.resolve();
        assert.equal(editor.getStats().serializations, 1);
        assert.equal(serialized_results.length, 1);
        assert.equal(serialized_results[0].editRevision, 9);
        assert.match(serialized_results[0].markdown, /ABStart/);

        const cached = await editor.flush(9);
        assert.equal(cached.status, "serialized");
        assert.equal(editor.getStats().serializations, 1, "a revision serializes at most once");

        paste_text(root, "C");
        assert.equal(idle_callbacks.size, 1);
        assert.deepEqual(await editor.flush(9), {
            status: "stale",
            requestedRevision: 9,
            currentRevision: 10,
        });
        assert.equal(idle_callbacks.size, 1, "a stale flush cannot cancel the current revision");
        const newest = await editor.flush(10);
        assert.equal(newest.status, "serialized");
        assert.equal(editor.getStats().serializations, 2);
        assert.equal(serialized_results.length, 2);

        const first_destroy = editor.destroy();
        const second_destroy = editor.destroy();
        assert.equal(first_destroy, second_destroy);
        await first_destroy;
        assert.equal(root.childElementCount, 0);
        root.remove();
    },
);

run_test("remote hydration clears undo history across the document boundary", async () => {
    const markdown_editor = await editor_module;
    idle_callbacks.clear();
    const root = document.createElement("div");
    document.body.append(root);
    const dirty_events = [];
    const result = await with_timeout(
        markdown_editor.create_pear_workspace_markdown_editor({
            root,
            markdown: "# Original\n",
            editRevision: 1,
            onDirty(event) {
                dirty_events.push(event);
            },
            onSerialized() {},
        }),
        "history Rich creation",
    );
    assert.equal(result.ok, true);
    const {editor} = result;
    editor.focus();

    paste_text(root, "local ");
    assert.match(prose_mirror(root).textContent, /local/);
    assert.equal(undo_with_keyboard(root), true, "control edit must be undoable before hydration");
    assert.doesNotMatch(prose_mirror(root).textContent, /local/);

    paste_text(root, "local ");
    assert.match(prose_mirror(root).textContent, /local/);
    assert.deepEqual(
        editor.hydrate({
            markdown: "# Remote\n\nremote only\n",
            editRevision: 50,
            selection: {anchor: 1, head: 1},
        }),
        {eligible: true},
    );
    const hydrated_text = prose_mirror(root).textContent;
    assert.match(hydrated_text, /Remote.*remote only/s);
    assert.doesNotMatch(hydrated_text, /Original|local/);

    assert.equal(undo_with_keyboard(root), false, "old-document history must be empty");
    assert.equal(prose_mirror(root).textContent, hydrated_text);
    assert.equal(editor.getEditRevision(), 50);
    assert.equal(dirty_events.length, 3, "hydrate and rejected undo are non-dirty");

    await editor.destroy();
    assert.throws(() => editor.getViewState(), /destroyed Rich Markdown editor/);
    assert.equal(editor.hasFocus(), false);
    root.remove();
});

run_test("hydration is non-dirty, fail-closed, and read-only is live", async () => {
    const markdown_editor = await editor_module;
    idle_callbacks.clear();
    const root = document.createElement("div");
    document.body.append(root);
    const dirty_events = [];
    const serialized_results = [];
    const result = await with_timeout(
        markdown_editor.create_pear_workspace_markdown_editor({
            root,
            markdown: "# Old\n",
            editRevision: 2,
            onDirty(event) {
                dirty_events.push(event);
            },
            onSerialized(serialized) {
                serialized_results.push(serialized);
            },
        }),
        "Rich hydration creation",
    );
    assert.equal(result.ok, true);
    const {editor} = result;
    const prose = prose_mirror(root);

    const supported = editor.hydrate({
        markdown: "# Remote\n\n- [ ] task\n",
        editRevision: 20,
        selection: {anchor: 12, head: 12},
        scrollTop: 31,
    });
    assert.deepEqual(supported, {eligible: true});
    assert.deepEqual(dirty_events, []);
    assert.equal(editor.getEditRevision(), 20);
    assert.equal(editor.getViewState().scrollTop, 31);
    assert.deepEqual(editor.getViewState().selection, {anchor: 12, head: 12});
    assert.match(prose.textContent, /Remote.*task/s);

    paste_text(root, "X");
    assert.deepEqual(dirty_events, [{origin: "user", editRevision: 21}]);
    assert.equal((await editor.flush(21)).status, "serialized");
    assert.equal(serialized_results.length, 1);
    assert.deepEqual(
        editor.hydrate({
            markdown: serialized_results[0].markdown,
            editRevision: 21,
            selection: editor.getViewState().selection,
        }),
        {eligible: true},
    );
    assert.equal(editor.getStats().serializations, 1);

    const before_rejected_hydration = prose.textContent;
    const rejected = editor.hydrate({
        markdown: "# Unsafe\n\n<!-- comment -->\n",
        editRevision: 22,
    });
    assert.equal(rejected.eligible, false);
    assert.equal(rejected.code, "raw_html");
    assert.equal(prose.textContent, before_rejected_hydration);
    assert.equal(editor.getEditRevision(), 21);
    assert.deepEqual(dirty_events, [{origin: "user", editRevision: 21}]);

    editor.setReadOnly(true);
    assert.equal(prose.getAttribute("contenteditable"), "false");
    editor.setReadOnly(false);
    assert.equal(prose.getAttribute("contenteditable"), "true");

    paste_text(root, "Y");
    assert.equal(idle_callbacks.size, 1);
    await editor.destroy();
    assert.equal(idle_callbacks.size, 0);
    assert.equal(root.childElementCount, 0);
    root.remove();
});
