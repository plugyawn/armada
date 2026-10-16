"use strict";

// The interim beta's question (web/src/armada/beta/parts.js and runs.js).
// It is read from the message Onix posts when Codex asks something with its
// question tool, as the server renders it:
// zerver/tests/fixtures/armada_question_message.json, which the server's
// own test (test_armada_endpoint.py) renders from the message it builds.
// So the server's words and the beta's reading of them cannot drift apart.
// The options become buttons, and the caption under them says what answers
// the question while the run waits, and says that the run no longer waits
// once it does not. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const question = require("../../../zerver/tests/fixtures/armada_question_message.json");
const {mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`<div id="col"></div>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("localStorage", dom.window.localStorage);
set_global("M", {T: {}});

const AGENT = 9;
const lane = document.querySelector("#col");
// The rest of the page is loaded only where it is used, and nothing here
// uses it.
mock_esm("../../src/armada/beta/frame.js", {lane});
mock_esm("../../src/armada/beta/faces.js", {isOnix: (id) => id === AGENT, nameOf: () => "Onix"});

const beta_state = zrequire("armada/beta/state.js");
const parts = zrequire("armada/beta/parts.js");
const runs = zrequire("armada/beta/runs.js");

// Onix's question, drawn in the conversation as the feed draws a message:
// each of its parts in turn.
function draw_question() {
    lane.replaceChildren();
    const message = {id: 70, sender_id: AGENT, content: question.rendered};
    for (const part of parts.partsOf(message)) {
        lane.append(parts.partEl(part, message));
    }
}

const answers = () => [...lane.querySelectorAll("button")].map((button) => button.dataset.answer);
const offered = () =>
    [...lane.querySelectorAll("button")].map(
        (button) => button.getAttribute("aria-disabled") !== "true",
    );
const caption = () => lane.lastElementChild.textContent.trim();

run_test("the options of Onix's question are the answers its buttons send", () => {
    draw_question();

    assert.deepEqual(answers(), ["Red", "Blue"]);
});

run_test("the caption says what answers the question while the run waits", () => {
    const run = {status: "running", waiting: true};
    beta_state.state.P = {runs: [run], msgs: []};
    draw_question();

    runs.drawAsks();
    assert.equal(
        caption(),
        "Press an answer, or reply to Onix in the topic. The run waits up to 10 minutes.",
    );
    assert.deepEqual(offered(), [true, true]);

    // Answered, or out of time: the question is spent, and says so.
    run.waiting = false;
    runs.drawAsks();
    assert.equal(caption(), "The run no longer waits for an answer here.");
    assert.deepEqual(offered(), [false, false]);
});
