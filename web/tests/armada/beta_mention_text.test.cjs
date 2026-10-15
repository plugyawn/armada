"use strict";

// The interim beta's field words (web/src/armada/beta/mention_text.js):
// what a field with pills sends, how a typed agent name becomes its
// mention in place (statement 27), the pickers' triggers and order.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {make_real_dom} = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const words = zrequire("armada/beta/mention_text.js");

const AGENT = "@**Onix**";

function field(dom) {
    const f = dom.find("#field");
    f.textContent = "";
    return f;
}

function put(f, ...parts) {
    for (const part of parts) {
        f.append(
            typeof part === "string"
                ? f.ownerDocument.createTextNode(part)
                : words.pillNode(f.ownerDocument, part),
        );
    }
    return f;
}

const iago = {wire: "@**Iago|5**", label: "@Iago", uid: 5};
const seed = {wire: AGENT, label: "@Onix", uid: 9, agent: true, seed: true};

run_test("the agent's names typed by hand become its mention where they stand", () => {
    for (const name of ["@onix", "@Onix", "@ONYX", "@**onix**"]) {
        assert.equal(words.typedAliases(`hey ${name}, look`, AGENT), `hey ${AGENT}, look`, name);
    }
    // Not a mention: inside a word, an e-mail address, a longer name, code.
    assert.equal(words.typedAliases("me@onix.dev", AGENT), "me@onix.dev");
    assert.equal(words.typedAliases("@onixbot hi", AGENT), "@onixbot hi");
    assert.equal(words.typedAliases("@Onix|12 hi", AGENT), "@Onix|12 hi");
    assert.equal(words.typedAliases("run `@onix` and", AGENT), "run `@onix` and");
    assert.equal(words.typedAliases("```\n@onix\n```", AGENT), "```\n@onix\n```");
});

run_test("what is sent is what the field shows, mentions in place", () => {
    const dom = make_real_dom(`<div id="field"></div>`);
    // The bug of statement 27: a typed name was moved to the front, or
    // sent twice with a mention made up in front of it.
    assert.equal(
        words.wireOf(put(field(dom), "can you check this, @onix?"), AGENT),
        `can you check this, ${AGENT}?`,
    );
    assert.equal(
        words.wireOf(put(field(dom), "ask ", iago, " about it"), AGENT),
        "ask @**Iago|5** about it",
    );
    // The switch's pill is sent when the words do not name the agent ...
    assert.equal(
        words.wireOf(put(field(dom), seed, " fix the test"), AGENT),
        `${AGENT} fix the test`,
    );
    // ... and dropped when they do, so it is never named twice.
    assert.equal(
        words.wireOf(put(field(dom), seed, " hey @onix fix it"), AGENT),
        `hey ${AGENT} fix it`,
    );
    // The switch's pill alone is nothing to send.
    const only = put(field(dom), seed, " ");
    assert.equal(words.wireOf(only, AGENT), "");
    assert.ok(words.fieldEmpty(only));
    assert.equal(only.textContent.trim(), "");
    assert.ok(!words.fieldEmpty(put(field(dom), iago)));
    // A block after a leading mention keeps its own line.
    assert.equal(
        words.wireOf(put(field(dom), seed, " ```\nx\n```"), AGENT),
        `${AGENT}\n\n\`\`\`\nx\n\`\`\``,
    );
    // Lines the browser wrapped in elements stay lines.
    const f = field(dom);
    f.innerHTML = "one<div>two</div><div>three<br>four</div>";
    assert.equal(words.wireOf(f, AGENT), "one\ntwo\nthree\nfour");
});

run_test("words sent from a view name the file after the agent's mention", () => {
    assert.equal(words.withPath(`${AGENT} fix it`, "a.py", AGENT), `${AGENT} \`a.py\` fix it`);
    assert.equal(words.withPath("fix it", "a.py", AGENT), "`a.py` fix it");
    // A block that starts the words keeps its own line after the path.
    assert.equal(
        words.withPath(`${AGENT}\n\n\`\`\`\nx\n\`\`\``, "a.py", AGENT),
        `${AGENT} \`a.py\`\n\n\`\`\`\nx\n\`\`\``,
    );
    assert.equal(words.withPath("- one\n- two", "a.py", AGENT), "`a.py`\n\n- one\n- two");
});

run_test("an edit's wire text comes back as pills, code as written", () => {
    const dom = make_real_dom(`<div id="field"></div>`);
    const f = field(dom);
    const resolve = (r) =>
        r.kind === "person" && r.id === 5
            ? {wire: r.wire, label: "@Iago", uid: 5}
            : r.kind === "person" && r.wire === AGENT
              ? {wire: AGENT, label: "@Onix", agent: true}
              : null;
    words.fillField(f, `${AGENT} ask @**Iago|5** about \`@**Iago|5**\` and @**Nobody**`, resolve);
    assert.deepEqual(
        [...f.querySelectorAll("[data-wire]")].map((p) => p.textContent),
        ["@Onix", "@Iago"],
    );
    assert.equal(
        words.wireOf(f, AGENT),
        `${AGENT} ask @**Iago|5** about \`@**Iago|5**\` and @**Nobody**`,
    );
});

run_test("a picker opens on @ or # after a space, never in a word or in code", () => {
    assert.deepEqual(words.triggerAt("hi @ia"), {kind: "@", q: "ia", length: 3});
    assert.deepEqual(words.triggerAt("@"), {kind: "@", q: "", length: 1});
    assert.deepEqual(words.triggerAt("ask @iago g"), {kind: "@", q: "iago g", length: 7});
    assert.equal(words.triggerAt("ask @iago gomes now"), null);
    assert.equal(words.triggerAt("mail me@ia"), null);
    assert.equal(words.triggerAt("run `@ia"), null);
    assert.deepEqual(words.triggerAt("see #arm"), {kind: "#", q: "arm", length: 4});
    assert.deepEqual(words.triggerAt("see #armada>click opt"), {
        kind: "#",
        q: "armada>click opt",
        length: 17,
    });
    assert.equal(words.triggerAt("# heading"), null);
});

run_test(
    "@ lists the best match first, and at a tie people (here now first), then the agent, then @topic",
    () => {
        const people = [
            {id: 1, name: "Zoe Ivers", pres: ""},
            {id: 2, name: "Ivan Ito", pres: "away"},
            {id: 3, name: "Iago Gomes", pres: "on"},
            {id: 4, name: "Mira Patel", pres: "on"},
        ];
        const agent = {id: 9, name: "Onix"};
        assert.deepEqual(
            words.rankPeople("", people, {agent, topic: true}).map((c) => c.name),
            ["Iago Gomes", "Mira Patel", "Ivan Ito", "Zoe Ivers", "Onix", "topic"],
        );
        assert.deepEqual(
            words.rankPeople("i", people, {agent, topic: true}).map((c) => c.name),
            ["Iago Gomes", "Ivan Ito", "Zoe Ivers"],
        );
        assert.deepEqual(
            words.rankPeople("on", people, {agent}).map((c) => c.kind),
            ["agent"],
        );
        // The agent matched from the start of its name ranks above a person
        // matched inside theirs, so Enter on "@Oni" starts a run.
        const inside = [...people, {id: 7, name: "Polonius"}, {id: 8, name: "aaron"}];
        assert.deepEqual(
            words.rankPeople("Oni", inside, {agent}).map((c) => c.name),
            ["Onix", "Polonius"],
        );
        assert.deepEqual(
            words.rankPeople("on", inside, {agent}).map((c) => c.name),
            ["Onix", "aaron", "Polonius"],
        );
        assert.deepEqual(
            words.rankPeople("iago g", people, {}).map((c) => c.id),
            [3],
        );
        assert.deepEqual(words.rankPeople("xyz", people, {agent, topic: true}), []);
    },
);

run_test("# lists repositories, then their worktrees", () => {
    const streams = [
        {stream_id: 1, name: "armada"},
        {stream_id: 2, name: "website"},
    ];
    const topics = {
        1: [
            {name: "click-options", max_id: 5, worktree: true},
            {name: "general chat", max_id: 9},
        ],
        2: [{name: "click copy", max_id: 3}],
    };
    const topics_of = (sid) => topics[sid];
    assert.deepEqual(
        words.rankPlaces("", streams, topics_of).map((c) => c.stream.name),
        ["armada", "website"],
    );
    assert.deepEqual(
        words.rankPlaces("click", streams, topics_of).map((c) => c.topic),
        ["click-options", "click copy"],
    );
    assert.deepEqual(
        words.rankPlaces("armada>", streams, topics_of).map((c) => c.topic),
        ["click-options", "general chat"],
    );
    const [first] = words.rankPlaces("armada>click", streams, topics_of);
    assert.deepEqual(words.pillFor(first, AGENT), {
        wire: "#**armada>click-options**",
        label: "#armada › click-options",
        chip: true,
    });
    assert.equal(words.pillFor({kind: "topic"}, AGENT).wire, "@**topic**");
    assert.equal(words.pillFor({kind: "agent", id: 9, name: "Onix"}, AGENT).wire, AGENT);
});

run_test("the echo renders mentions and links as the server does", () => {
    const look = {
        personByName: (n) => (n === "Onix" ? 9 : null),
        streamByName: (n) => (n === "armada" ? 1 : null),
    };
    assert.equal(
        words.echoRefs("@**Iago|5** and @**Onix** and @_**Iago|5** and @**topic**", look),
        '<span class="user-mention" data-user-id="5">@Iago</span> and <span class="user-mention" data-user-id="9">@Onix</span> and <span class="user-mention silent" data-user-id="5">Iago</span> and <span class="topic-mention">@topic</span>',
    );
    assert.equal(words.echoRefs("@**Nobody**", look), "@**Nobody**");
    assert.equal(
        words.echoRefs("#**armada&gt;fix it** #**armada**", look),
        '<a class="stream-topic" data-stream-id="1">#armada &gt; fix it</a> <a class="stream" data-stream-id="1">#armada</a>',
    );
});
