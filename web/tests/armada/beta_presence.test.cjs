"use strict";

// The interim beta's presence (web/src/armada/beta/presence.js and presOf
// in faces.js), with a real DOM and a stand-in server: the page reports
// itself active at load and on the server's interval, idle after five
// quiet minutes and active again at the next sign of the person; each
// report's answer and each presence event change who shows as here, and
// the faces are drawn again only when that changes, and not while the
// page is hidden. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(
    `<div id="beta-data" data-presence-ping="60" data-presence-offline="200"></div>`,
);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global("M", {T: {}});
dom.document.hasFocus = () => true;

// The server: what each report asked, and the presences its answer brings.
const reports = [];
let answer = {};
mock_esm("../../src/armada/beta/connection.js", {
    async api(method, path, params) {
        reports.push({method, path, ...params});
        return {result: "success", ...(params.ping_only ? {} : answer)};
    },
});
let drawn = 0;
mock_esm("../../src/armada/beta/nav.js", {
    drawNav() {
        drawn += 1;
    },
});
mock_esm("../../src/armada/beta/partner_card.js", {card: null, drawCard() {}});

const {people, state} = zrequire("armada/beta/state.js");
const {presOf} = zrequire("armada/beta/faces.js");
const presence = zrequire("armada/beta/presence.js");

const ME = 1;
const IAGO = 2;
const KING = 3;
const seconds = () => Math.floor(Date.now() / 1000);
state.ME = ME;
for (const id of [ME, IAGO, KING]) {
    people.set(id, {id, name: `Person ${id}`, email: `${id}@example.com`});
}
// What register said: Iago was active half a minute ago, the King never.
state.presences = {[IAGO]: {active_timestamp: seconds() - 30, idle_timestamp: seconds() - 30}};
state.presenceId = 4;

async function settle() {
    for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
    }
}

run_test("at load the page says it is here, and faces show who was active lately", async () => {
    presence.startPresence();
    await settle();
    assert.deepEqual(reports, [
        {
            method: "POST",
            path: "users/me/presence",
            status: "active",
            ping_only: true,
            new_user_input: true,
            last_update_id: 4,
        },
    ]);
    assert.equal(presOf(IAGO), "on");
    assert.equal(presOf(KING), "");
});

run_test(
    "on the server's interval it reports again and hears everyone's changes since",
    async () => {
        answer = {
            presences: {[KING]: {active_timestamp: seconds() + 50, idle_timestamp: seconds() + 50}},
            presence_last_update_id: 9,
        };
        clock.tick(60_000);
        await settle();
        assert.equal(reports.length, 2);
        assert.equal(reports[1].ping_only, false);
        assert.equal(reports[1].last_update_id, 4);
        assert.equal(reports[1].status, "active");
        assert.equal(presOf(KING), "on");
        assert.equal(drawn, 1, "the King came: faces are drawn again");

        answer = {presences: {}, presence_last_update_id: 9};
        clock.tick(60_000);
        await settle();
        assert.equal(reports[2].last_update_id, 9);
        assert.equal(drawn, 1, "nobody changed: nothing is drawn");

        // Iago's last activity passes the server's offline threshold.
        clock.tick(60_000);
        await settle();
        assert.equal(presOf(IAGO), "");
        assert.equal(drawn, 2);
    },
);

run_test(
    "five quiet minutes make it idle; a sign of the person makes it active at once",
    async () => {
        clock.tick(180_000);
        await settle();
        assert.equal(reports.at(-1).status, "idle");
        const before = reports.length;
        dom.window.dispatchEvent(new dom.window.KeyboardEvent("keydown", {key: "a"}));
        await settle();
        assert.equal(reports.length, before + 1);
        assert.deepEqual(
            [reports.at(-1).status, reports.at(-1).ping_only, reports.at(-1).new_user_input],
            ["active", true, true],
        );
        dom.window.dispatchEvent(new dom.window.MouseEvent("mousemove"));
        await settle();
        assert.equal(reports.length, before + 1, "already active: a move says nothing more");
    },
);

run_test(
    "a presence event shows someone at once; a hidden page draws when it is seen",
    async () => {
        let hidden = true;
        Object.defineProperty(dom.document, "hidden", {configurable: true, get: () => hidden});
        const was = drawn;
        presence.onPresence({
            type: "presence",
            presences: {[IAGO]: {active_timestamp: seconds(), idle_timestamp: seconds()}},
        });
        assert.equal(presOf(IAGO), "on");
        assert.equal(drawn, was, "nobody sees a hidden page");
        hidden = false;
        dom.document.dispatchEvent(new dom.window.Event("visibilitychange"));
        assert.equal(drawn, was + 1);
    },
);
