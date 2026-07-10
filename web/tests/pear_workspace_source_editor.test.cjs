"use strict";

const assert = require("node:assert/strict");

const {undo, undoDepth} = require("@codemirror/commands");
const {language: language_facet} = require("@codemirror/language");
const {EditorState} = require("@codemirror/state");
const {EditorView} = require("@codemirror/view");
const {JSDOM} = require("jsdom");

const {set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    pretendToBeVisual: true,
    url: "http://zulip.zulipdev.com/",
});
const {window} = dom;
window.setTimeout = global.setTimeout;
const fake_clear_timeout = global.clearTimeout;
function clear_timeout(timer) {
    if (timer !== -1) {
        fake_clear_timeout(timer);
    }
}
window.clearTimeout = clear_timeout;
set_global("clearTimeout", clear_timeout);

class ResizeObserver {
    observe() {}

    unobserve() {}

    disconnect() {}
}

for (const [name, value] of Object.entries({
    window,
    document: window.document,
    navigator: window.navigator,
    MutationObserver: window.MutationObserver,
    ResizeObserver,
    getComputedStyle: window.getComputedStyle.bind(window),
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
    DOMRect: window.DOMRect,
    Range: window.Range,
    Selection: window.Selection,
    Node: window.Node,
    Text: window.Text,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    KeyboardEvent: window.KeyboardEvent,
})) {
    set_global(name, value);
}
set_global("ResizeObserver", ResizeObserver);

const source_editor = zrequire("pear_workspace_source_editor");

function find_view(root) {
    const editor_dom = root.querySelector(".cm-editor");
    assert.ok(editor_dom instanceof window.HTMLElement);
    return EditorView.findFromDOM(editor_dom);
}

function make_editor(overrides = {}) {
    const root = document.createElement("div");
    root.style.height = "400px";
    document.body.append(root);
    const dirty_events = [];
    const language_errors = [];
    const editor = source_editor.create_pear_workspace_source_editor({
        root,
        text: "initial\n",
        onDirty(event) {
            dirty_events.push(event);
        },
        onLanguageError(event) {
            language_errors.push(event);
        },
        ...overrides,
    });
    return {root, editor, view: find_view(root), dirty_events, language_errors};
}

run_test("maps only supported filename extensions to Source grammars", () => {
    const language_for_path = source_editor.pear_workspace_source_language_for_path;
    const cases = new Map([
        ["README.md", "markdown"],
        ["docs/GUIDE.MARKDOWN", "markdown"],
        ["component.mdx", "markdown"],
        ["src/index.js", "javascript"],
        ["src/index.JSX", "javascript"],
        ["worker.mjs", "javascript"],
        ["config.cjs", "javascript"],
        ["src/index.ts", "typescript"],
        ["src/index.tsx", "typescript"],
        ["module.mts", "typescript"],
        ["module.cts", "typescript"],
        ["data.json", "json"],
        ["script.py", "python"],
        ["types.pyi", "python"],
        ["theme.css", "css"],
        ["index.html", "html"],
        ["legacy.htm", "html"],
        ["config.yaml", "yaml"],
        ["config.yml", "yaml"],
        ["Makefile", "plain"],
        ["data.jsonc", "plain"],
        ["trailing.", "plain"],
        ["", "plain"],
    ]);

    for (const [path, expected] of cases) {
        assert.equal(language_for_path(path), expected, path);
    }
});

run_test("mounts synchronously with exact pristine text and restored view state", async () => {
    const exact = "\uFEFFfirst\r\nsecond\rthird\nlast  \t";
    const {root, editor, view, dirty_events} = make_editor({
        text: exact,
        selection: {anchor: 10_000, head: -4},
        scrollTop: 73,
        language: "markdown",
        ariaLabel: "README source",
    });

    assert.ok(root.querySelector(".cm-editor"), "base editor must paint before grammar loading");
    assert.equal(view.contentDOM.getAttribute("aria-label"), "README source");
    assert.equal(view.state.doc.toString(), "\uFEFFfirst\nsecond\nthird\nlast  \t");
    assert.equal(editor.getText(), exact);
    assert.deepEqual(editor.getViewState(), {
        selection: {anchor: view.state.doc.length, head: 0},
        scrollTop: 73,
    });
    assert.deepEqual(dirty_events, []);
    assert.equal(await editor.languageReady, "applied");
    assert.ok(view.state.facet(language_facet));
    editor.destroy();
});

run_test("non-finite selection positions clamp safely", () => {
    const {editor} = make_editor({
        selection: {anchor: Number.NaN, head: Number.POSITIVE_INFINITY},
    });
    assert.deepEqual(editor.getViewState().selection, {anchor: 0, head: 0});
    editor.destroy();
});

run_test("user transactions stay cheap while flush reconstructs exact mixed line endings", () => {
    const exact = "\uFEFFfirst\r\nsecond\rthird\nlast  \t";
    const {editor, view, dirty_events} = make_editor({text: exact});
    const start = view.state.doc.toString().indexOf("second");

    view.dispatch({
        changes: {from: start, to: start + "second".length, insert: "changed\nmore"},
        selection: {anchor: start + "changed\nmore".length},
    });

    assert.deepEqual(dirty_events, [
        {
            origin: "user",
            selection: {
                anchor: start + "changed\nmore".length,
                head: start + "changed\nmore".length,
            },
        },
    ]);
    assert.ok(
        !Object.hasOwn(dirty_events[0], "text"),
        "keystrokes must not serialize the document",
    );
    assert.equal(editor.getText(), "\uFEFFfirst\r\nchanged\r\nmore\rthird\nlast  \t");
    editor.destroy();
});

run_test("changes spanning normalized newlines preserve untouched exact bytes", () => {
    const exact = "one\r\ntwo\rthree\nfour";
    const {editor, view} = make_editor({text: exact});
    const normalized = view.state.doc.toString();
    const from = normalized.indexOf("two");
    const to = normalized.indexOf("four");

    view.dispatch({changes: {from, to, insert: "replacement\n"}});

    assert.equal(editor.getText(), "one\r\nreplacement\r\nfour");
    editor.destroy();
});

run_test("sequential edits compose against one exact baseline", () => {
    const {editor, view} = make_editor({text: "a\r\nb\rc\nd"});
    view.dispatch({changes: {from: 2, insert: "X\n"}});

    let current = view.state.doc.toString();
    const b = current.indexOf("b");
    view.dispatch({changes: {from: b, to: b + 1, insert: "bee"}});

    current = view.state.doc.toString();
    const x = current.indexOf("X");
    view.dispatch({changes: {from: x, to: x + 2, insert: ""}});

    current = view.state.doc.toString();
    const c = current.indexOf("c");
    view.dispatch({changes: {from: c, to: c + 1, insert: "see\nnew"}});

    assert.equal(editor.getText(), "a\r\nbee\rsee\r\nnew\nd");
    editor.destroy();
});

run_test("undoing back to the baseline restores the original exact text", () => {
    const exact = "alpha\r\nbeta\rgamma\n";
    const {editor, view, dirty_events} = make_editor({text: exact});
    view.dispatch({changes: {from: 0, to: 5, insert: "changed"}});
    assert.equal(editor.getText(), "changed\r\nbeta\rgamma\n");
    assert.equal(undoDepth(view.state), 1);

    assert.equal(undo(view), true);
    assert.equal(editor.getText(), exact);
    assert.equal(dirty_events.length, 2);
    assert.ok(dirty_events.every((event) => event.origin === "user"));
    editor.destroy();
});

run_test("hydration is non-dirty, restores state, and clears prior-document undo history", () => {
    const {editor, view, dirty_events} = make_editor({text: "old\n"});
    view.dispatch({changes: {from: 0, to: 3, insert: "edited"}});
    assert.equal(undoDepth(view.state), 1);
    assert.equal(dirty_events.length, 1);

    editor.hydrate({
        text: "remote\r\ntext\r",
        selection: {anchor: 9_999, head: 2},
        scrollTop: 41,
    });

    assert.equal(editor.getText(), "remote\r\ntext\r");
    assert.equal(view.state.doc.toString(), "remote\ntext\n");
    assert.equal(undoDepth(view.state), 0);
    assert.equal(dirty_events.length, 1);
    assert.deepEqual(editor.getViewState(), {
        selection: {anchor: view.state.doc.length, head: 2},
        scrollTop: 41,
    });

    view.dispatch({changes: {from: view.state.doc.length, insert: "next\n"}});
    assert.equal(editor.getText(), "remote\r\ntext\rnext\r\n");
    assert.equal(dirty_events.length, 2);
    editor.destroy();
});

run_test("hydration can preserve a clamped current selection and scroll position", () => {
    const {editor, view} = make_editor({
        text: "long initial text",
        selection: {anchor: 12, head: 4},
        scrollTop: 18,
    });

    editor.hydrate({text: "tiny"});
    assert.deepEqual(editor.getViewState(), {
        selection: {anchor: 4, head: 4},
        scrollTop: 18,
    });
    assert.equal(view.state.doc.toString(), "tiny");
    editor.destroy();
});

run_test("read-only, focus, and destroy are instance-scoped capabilities", async () => {
    const first = make_editor({readOnly: true});
    const second = make_editor();

    assert.equal(first.view.state.facet(EditorState.readOnly), true);
    assert.equal(first.view.contentDOM.getAttribute("contenteditable"), "false");
    assert.equal(second.view.state.facet(EditorState.readOnly), false);
    assert.equal(second.view.contentDOM.getAttribute("contenteditable"), "true");

    first.editor.setReadOnly(false);
    assert.equal(first.view.state.facet(EditorState.readOnly), false);
    assert.equal(first.view.contentDOM.getAttribute("contenteditable"), "true");
    assert.equal(second.view.state.facet(EditorState.readOnly), false);

    first.editor.focus();
    assert.equal(first.editor.hasFocus(), true);
    assert.equal(second.editor.hasFocus(), false);

    first.editor.destroy();
    first.editor.destroy();
    assert.equal(first.editor.hasFocus(), false);
    assert.equal(first.root.querySelector(".cm-editor"), null);
    assert.ok(second.root.querySelector(".cm-editor"));
    assert.throws(() => first.editor.getText(), /has been destroyed/);
    assert.throws(() => first.editor.getViewState(), /has been destroyed/);
    assert.throws(() => first.editor.hydrate({text: "late"}), /has been destroyed/);
    assert.throws(() => first.editor.setReadOnly(true), /has been destroyed/);
    assert.throws(() => first.editor.focus(), /has been destroyed/);
    await assert.rejects(first.editor.setLanguage("plain"), /has been destroyed/);
    second.editor.destroy();
});

run_test("each supported grammar loads independently and plain removes it", async () => {
    const {editor, view, language_errors} = make_editor();
    await editor.languageReady;
    assert.equal(view.state.facet(language_facet), null);

    for (const grammar of [
        "markdown",
        "javascript",
        "typescript",
        "json",
        "python",
        "css",
        "html",
        "yaml",
    ]) {
        assert.equal(await editor.setLanguage(grammar), "applied", grammar);
        assert.ok(view.state.facet(language_facet), grammar);
    }

    assert.equal(await editor.setLanguage("plain"), "applied");
    assert.equal(view.state.facet(language_facet), null);
    assert.deepEqual(language_errors, []);
    editor.destroy();
});

run_test("a newer language request prevents an older import from overwriting it", async () => {
    const {editor, view} = make_editor();
    await editor.languageReady;

    const older = editor.setLanguage("markdown");
    const newer = editor.setLanguage("plain");

    assert.equal(await newer, "applied");
    assert.equal(await older, "stale");
    assert.equal(view.state.facet(language_facet), null);
    editor.destroy();
});

run_test("failed and post-destroy language loads cannot mutate the editor", async () => {
    const failed = make_editor();
    await failed.editor.languageReady;
    assert.equal(await failed.editor.setLanguage("unsupported"), "failed");
    assert.equal(failed.language_errors.length, 1);
    assert.equal(failed.language_errors[0].language, "unsupported");
    assert.ok(failed.language_errors[0].error instanceof TypeError);
    assert.equal(failed.view.state.facet(language_facet), null);
    failed.editor.destroy();

    const stale = make_editor();
    await stale.editor.languageReady;
    const pending = stale.editor.setLanguage("javascript");
    stale.editor.destroy();
    assert.equal(await pending, "stale");
    assert.equal(stale.language_errors.length, 0);
});
