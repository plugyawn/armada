"use strict";

// Who may add people to a channel, whom Add people offers, and whom a
// message's mentions will not reach (web/src/armada/beta/channel_rules.js):
// the server's rules and the old interface's (compose_validate.ts), read the
// way the page holds them. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {
    addRefusal,
    addable,
    addedWords,
    groupMembers,
    groupOutside,
    groupWords,
    mayAddPeople,
    memberOrder,
    mentionsIn,
    noticeWords,
    outsiders,
} = zrequire("armada/beta/channel_rules.js");
const {outsideCode} = zrequire("armada/beta/mention_text.js");

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

// Names to ids, as the field's words name people (mentions.js, personByName).
const BY_NAME = new Map([
    ["mihir kumar", 1],
    ["ana lucía ferreira", 2],
    ["onix", 7],
]);
const look = (name) => BY_NAME.get(name.toLowerCase()) ?? null;
const mentioned = (wire) => mentionsIn(wire, look, outsideCode);

run_test("the mentions in a message's words, as Zulip's renderer finds them", () => {
    assert.deepEqual(mentioned("@**Mihir Kumar** and @**Ana Lucía Ferreira|2**, look"), {
        people: [1, 2],
        groups: [],
    });
    // By id alone, more than once, or by a name the page does not know.
    assert.deepEqual(
        mentioned("@**|4** @**Mihir Kumar|1** @**Mihir Kumar** @**Nobody**").people,
        [4, 1],
    );
    // Never a silent mention, a wildcard, one inside a word, or code.
    assert.deepEqual(
        mentioned("@_**Mihir Kumar|1** @**all** @**topic** @**Channel** x@**Mihir Kumar**").people,
        [],
    );
    assert.deepEqual(
        mentioned("`@**Mihir Kumar**` and\n```\n@**Ana Lucía Ferreira**\n```").people,
        [],
    );
    // After a bracket or a quote it is one.
    assert.deepEqual(mentioned("(@**Mihir Kumar**)").people, [1]);
    // A group: @*name*, never silent, never a person's double stars.
    assert.deepEqual(mentioned("@*infra* and @_*design* and @**Mihir Kumar**"), {
        people: [1],
        groups: ["infra"],
    });
});

run_test("whom a mention will not reach: people not in the channel, never a bot or me", () => {
    const people = new Map([
        [1, {bot: false, active: true}],
        [2, {bot: false, active: true}],
        [3, {bot: false, active: true}],
        [5, {bot: false, active: false}],
        [7, {bot: true, active: true}],
    ]);
    // 3 is me, 2 is in the channel, 5 is gone, 7 is a bot, 9 the page
    // does not know.
    assert.deepEqual(outsiders([1, 2, 3, 5, 7, 9], new Set([2]), people, 3), [1]);
});

run_test("a group reaches no one when none of its active members is in the channel", () => {
    const groups = new Map([
        [70, {members: new Set([1]), subgroups: new Set([71])}],
        [71, {members: new Set([2, 5]), subgroups: new Set([70])}],
        [72, {members: new Set(), subgroups: new Set()}],
    ]);
    assert.deepEqual([...groupMembers(70, groups)].toSorted(), [1, 2, 5]);
    const people = new Map([
        [1, {active: true}],
        [2, {active: true}],
        [5, {active: false}],
    ]);
    assert.equal(groupOutside(groupMembers(70, groups), new Set([9]), people), true);
    assert.equal(groupOutside(groupMembers(70, groups), new Set([2]), people), false);
    // Someone deactivated in the channel reaches no one either.
    assert.equal(groupOutside(new Set([1, 5]), new Set([5]), people), true);
    // An empty group says nothing.
    assert.equal(groupOutside(groupMembers(72, groups), new Set(), people), false);
});

run_test("what the notice says", () => {
    assert.equal(noticeWords(["Mihir"], "ops"), "Mihir isn't in #ops, so they won't see this.");
    assert.equal(
        noticeWords(["Mihir", "Ana"], "ops"),
        "Mihir and Ana aren't in #ops, so they won't see this.",
    );
    assert.equal(
        noticeWords(["Mihir", "Ana", "Kim"], "ops"),
        "Mihir, Ana and Kim aren't in #ops, so they won't see this.",
    );
    assert.equal(
        noticeWords(["Mihir", "Ana", "Kim", "Bo"], "ops"),
        "Mihir, Ana and 2 others aren't in #ops, so they won't see this.",
    );
    assert.equal(
        groupWords("infra", "ops"),
        "No one in infra is in #ops, so no one there will see this.",
    );
    assert.equal(addedWords(["Mihir"], "ops"), "Mihir is in #ops now.");
    assert.equal(addedWords(["Mihir", "Ana"], "ops"), "Mihir and Ana are in #ops now.");
});
