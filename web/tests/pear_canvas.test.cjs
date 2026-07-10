"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const pear_canvas = zrequire("pear_canvas");

function make_canvas(overrides = {}) {
    return {
        content: "# Notes\n",
        version: "abc123",
        relative_path: "CANVAS.md",
        max_bytes: 1000,
        ...overrides,
    };
}

run_test("utf8_byte_length", () => {
    assert.equal(pear_canvas.utf8_byte_length(""), 0);
    assert.equal(pear_canvas.utf8_byte_length("abc"), 3);

    // Multi-byte characters count their UTF-8 byte length, not their
    // JavaScript string length, matching the server's len(encode()).
    assert.equal("é".length, 1);
    assert.equal(pear_canvas.utf8_byte_length("é"), 2);
    assert.equal(pear_canvas.utf8_byte_length("😀"), 4);
    assert.equal(pear_canvas.utf8_byte_length("a😀é"), 7);
});

run_test("content_within_byte_limit boundary", () => {
    // Exactly at the limit is allowed; one byte over is not.
    assert.ok(pear_canvas.content_within_byte_limit("abcde", 5));
    assert.ok(!pear_canvas.content_within_byte_limit("abcdef", 5));

    // A multi-byte char is measured in bytes: "é" is 2 bytes, so it
    // fits a 2-byte limit but not a 1-byte limit.
    assert.ok(pear_canvas.content_within_byte_limit("é", 2));
    assert.ok(!pear_canvas.content_within_byte_limit("é", 1));

    // Empty content fits any non-negative limit, including zero.
    assert.ok(pear_canvas.content_within_byte_limit("", 0));
});

run_test("is_canvas_empty", () => {
    assert.ok(pear_canvas.is_canvas_empty(""));
    assert.ok(pear_canvas.is_canvas_empty("   "));
    assert.ok(pear_canvas.is_canvas_empty("\n\t \n"));
    assert.ok(!pear_canvas.is_canvas_empty("x"));
    assert.ok(!pear_canvas.is_canvas_empty("  hello  "));
});

run_test("read_view_plan empty vs rendered", () => {
    // A blank or whitespace-only canvas shows the fixed placeholder;
    // $t returns the "translated: ..." form in the node test i18n mock.
    assert.deepEqual(pear_canvas.read_view_plan(""), {
        mode: "empty",
        text: "translated: This canvas is empty.",
    });
    assert.deepEqual(pear_canvas.read_view_plan("   \n"), {
        mode: "empty",
        text: "translated: This canvas is empty.",
    });

    // Non-empty content is passed through verbatim for server-side
    // Markdown rendering.
    assert.deepEqual(pear_canvas.read_view_plan("# Title\n\nbody"), {
        mode: "render",
        content: "# Title\n\nbody",
    });
});

run_test("parse_canvas_response valid", () => {
    const parsed = pear_canvas.parse_canvas_response({pear_canvas: make_canvas()});
    assert.deepEqual(parsed, {
        content: "# Notes\n",
        version: "abc123",
        relative_path: "CANVAS.md",
        max_bytes: 1000,
    });
});

run_test("parse_canvas_response requires all fields", () => {
    // The canvas schema has no optional fields; the server always
    // sends the full document, so a missing required field is an error.
    for (const missing of ["content", "version", "relative_path", "max_bytes"]) {
        const doc = Object.fromEntries(
            Object.entries(make_canvas()).filter(([key]) => key !== missing),
        );
        assert.throws(() => pear_canvas.parse_canvas_response({pear_canvas: doc}));
    }

    // A wrong-typed field (max_bytes as a string) is also rejected, and
    // a missing envelope key throws too.
    assert.throws(() =>
        pear_canvas.parse_canvas_response({pear_canvas: make_canvas({max_bytes: "1000"})}),
    );
    assert.throws(() => pear_canvas.parse_canvas_response({}));
});

run_test("resolve_canvas_conflict adopts fresh version and keeps draft", () => {
    const fresh = make_canvas({
        content: "# Updated on disk\n",
        version: "def456",
        relative_path: "docs/CANVAS.md",
        max_bytes: 2048,
    });
    const resolution = pear_canvas.resolve_canvas_conflict({pear_canvas: fresh});

    // The next save attempt adopts the fresh version and max_bytes, and
    // surfaces the disk content/path for the read view — while
    // keep_draft signals the editor text must NOT be overwritten.
    assert.deepEqual(resolution, {
        base_version: "def456",
        max_bytes: 2048,
        relative_path: "docs/CANVAS.md",
        content: "# Updated on disk\n",
        keep_draft: true,
    });
});

run_test("resolve_canvas_conflict returns undefined on unparseable body", () => {
    // A body without a valid pear_canvas payload leaves the generic
    // save-failed error visible (the caller does nothing).
    assert.equal(pear_canvas.resolve_canvas_conflict(undefined), undefined);
    assert.equal(pear_canvas.resolve_canvas_conflict({}), undefined);
    assert.equal(pear_canvas.resolve_canvas_conflict({pear_canvas: {version: "x"}}), undefined);
    assert.equal(pear_canvas.resolve_canvas_conflict("nope"), undefined);
});
