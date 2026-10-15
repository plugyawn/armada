"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const starts = [];
const insertions = [];
mock_esm("../src/compose_actions", {
    start(options) {
        starts.push(options);
    },
});
mock_esm("../src/compose_ui", {
    insert_syntax_and_focus(...args) {
        insertions.push(args);
    },
});

const workspace_compose = zrequire("legacy_workspace_compose");

run_test("file context is formatted without sending a message", () => {
    starts.length = 0;
    insertions.length = 0;
    const text = workspace_compose.format_armada_workspace_compose_context({
        path: "docs/README.md",
        selectedText: "",
    });
    assert.equal(text, "Ask Pear about this workspace file.\n\nPath: `docs/README.md`");

    workspace_compose.fill_armada_compose_with_workspace_context({
        path: "docs/README.md",
        selectedText: "",
    });
    assert.deepEqual(starts, [{message_type: "stream", trigger: "workspace file context"}]);
    assert.equal(insertions.length, 1);
    assert.equal(insertions[0][0], text);
    assert.equal(insertions[0][2], "block");
    assert.equal(insertions[0][3], 2);
});

run_test("selection context includes lines and uses a collision-safe fence", () => {
    assert.equal(
        workspace_compose.format_armada_workspace_compose_context({
            path: "src/`parser`.ts",
            selectedText: "const fence = ```;",
            startLine: 7,
            endLine: 9,
        }),
        [
            "Ask Pear about this workspace selection.",
            "",
            "Path: ``src/`parser`.ts``",
            "Lines: 7-9",
            "",
            "````",
            "const fence = ```;",
            "````",
        ].join("\n"),
    );
});
