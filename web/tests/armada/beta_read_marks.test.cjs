"use strict";

// The interim beta's read marks for messages read as they arrive in the
// open place (web/src/armada/beta/routing.js, markSeen): one request for
// each burst, a second after its first message, and at once when the page
// is hidden or goes away, with keepalive so the request outlives the page.
// Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`<div id="beta-data"></div>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global("M", {T: {}});

const posts = [];
mock_esm("../../src/armada/beta/connection.js", {
    async api(method, path, params, options) {
        posts.push({method, path, params, options});
        return {result: "success"};
    },
});
const routing = zrequire("armada/beta/routing.js");

run_test("a burst of messages read as they arrive is one request, a second after the first", () => {
    routing.markSeen(11);
    clock.tick(400);
    routing.markSeen(12);
    routing.markSeen(13);
    assert.equal(posts.length, 0);
    clock.tick(600);
    assert.deepEqual(posts, [
        {
            method: "POST",
            path: "messages/flags",
            params: {messages: [11, 12, 13], op: "add", flag: "read"},
            options: {keepalive: true},
        },
    ]);
    routing.markSeen(14);
    clock.tick(1000);
    assert.equal(posts.length, 2);
    assert.deepEqual(posts[1].params.messages, [14]);
    clock.tick(5000);
    assert.equal(posts.length, 2, "nothing more to say");
});

run_test("the page hidden or going away marks what it read at once", () => {
    let hidden = false;
    Object.defineProperty(dom.document, "hidden", {configurable: true, get: () => hidden});
    routing.markSeen(15);
    hidden = true;
    dom.document.dispatchEvent(new dom.window.Event("visibilitychange"));
    assert.deepEqual(posts.at(-1).params.messages, [15]);
    hidden = false;
    routing.markSeen(16);
    dom.window.dispatchEvent(new dom.window.Event("pagehide"));
    assert.deepEqual(posts.at(-1).params.messages, [16]);
    assert.equal(posts.length, 4);
    clock.tick(1000);
    assert.equal(posts.length, 4, "the timer went with the request");
});
