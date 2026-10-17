"use strict";

// Who may add people to a channel, and whom Add people offers
// (web/src/armada/beta/channel_rules.js): the server's rule, read the way
// the page holds it. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {addRefusal, addable, mayAddPeople, memberOrder} = zrequire("armada/beta/channel_rules.js");

// The groups I am in, and how a group setting is read: a named group by
// its id, an anonymous one by its direct members (enough here).
const ME = 3;
const MINE = new Set([14]);
function holds(setting) {
    if (setting === null || setting === undefined) {
        return false;
    }
    return typeof setting === "number" ? MINE.has(setting) : setting.direct_members.includes(ME);
}
const NOBODY = {direct_members: [], direct_subgroups: []};
const channel = (o = {}) => ({
    stream_id: 10,
    name: "ops",
    can_add_subscribers_group: NOBODY,
    can_administer_channel_group: NOBODY,
    ...o,
});
const member = {admin: false, guest: false, addSubscribers: 99};

run_test("who may add people: an administrator, the realm's group, the channel's groups", () => {
    assert.equal(mayAddPeople(channel(), member, holds), false);
    assert.equal(mayAddPeople(channel(), {...member, admin: true}, holds), true);
    // The realm's can_add_subscribers_group, a named group I am in.
    assert.equal(mayAddPeople(channel(), {...member, addSubscribers: 14}, holds), true);
    // The channel's own groups, anonymous ones that name me.
    const me_only = {direct_members: [ME], direct_subgroups: []};
    assert.equal(mayAddPeople(channel({can_add_subscribers_group: me_only}), member, holds), true);
    assert.equal(
        mayAddPeople(channel({can_administer_channel_group: me_only}), member, holds),
        true,
    );
    assert.equal(mayAddPeople(channel({can_administer_channel_group: 14}), member, holds), true);
});

run_test("never a guest, an archived channel, or one the page does not hold", () => {
    const guest = {admin: false, guest: true, addSubscribers: 14};
    assert.equal(mayAddPeople(channel({can_add_subscribers_group: 14}), guest, holds), false);
    assert.equal(
        mayAddPeople(channel({is_archived: true}), {...member, admin: true}, holds),
        false,
    );
    assert.equal(mayAddPeople(undefined, {...member, admin: true}, holds), false);
    // A realm whose setting the page was not given grants nothing.
    assert.equal(mayAddPeople(channel(), {...member, addSubscribers: null}, holds), false);
});

const PEOPLE = [
    {id: 1, name: "Mihir Kumar", active: true, bot: false, pres: "away"},
    {id: 2, name: "Ana Lucía Ferreira", active: true, bot: false, pres: "on"},
    {id: 4, name: "Kim Ito", active: true, bot: false, pres: ""},
    {id: 5, name: "Mirela", active: true, bot: false, pres: ""},
    {id: 6, name: "Old Account", active: false, bot: false, pres: ""},
    {id: 7, name: "Onix", active: true, bot: true, pres: ""},
    {id: 8, name: "Desdemona", active: true, bot: false, pres: "on"},
];
const names = (list) => list.map((p) => p.name);

run_test("Add offers active people not in the channel, here now first", () => {
    assert.deepEqual(names(addable(PEOPLE, new Set([8]))), [
        "Ana Lucía Ferreira",
        "Mihir Kumar",
        "Kim Ito",
        "Mirela",
    ]);
});

run_test("Add finds by a name's start, a word's start, then anywhere", () => {
    const none = new Set();
    assert.deepEqual(names(addable(PEOPLE, none, "mi")), ["Mihir Kumar", "Mirela"]);
    // A word's start; accents do not matter.
    assert.deepEqual(names(addable(PEOPLE, none, "lucia")), ["Ana Lucía Ferreira"]);
    assert.deepEqual(names(addable(PEOPLE, none, "ito")), ["Kim Ito"]);
    // Anywhere, from two letters: "re" is in Ferreira and Mirela, and
    // starts no word.
    assert.deepEqual(names(addable(PEOPLE, none, "re")), ["Ana Lucía Ferreira", "Mirela"]);
    assert.deepEqual(names(addable(PEOPLE, none, "r")), []);
    assert.deepEqual(names(addable(PEOPLE, none, "  MIH ")), ["Mihir Kumar"]);
});

run_test("the members are listed people first, here now first, then bots", () => {
    assert.deepEqual(names(memberOrder(PEOPLE.filter((p) => p.active))), [
        "Ana Lucía Ferreira",
        "Desdemona",
        "Mihir Kumar",
        "Kim Ito",
        "Mirela",
        "Onix",
    ]);
});

run_test("a refusal is said in words", () => {
    assert.equal(
        addRefusal({message: "Insufficient permission"}, "ops"),
        "You can't add people to #ops.",
    );
    assert.equal(
        addRefusal({message: "Unable to access channel (ops)."}, "ops"),
        "Not added: Unable to access channel (ops).",
    );
});
