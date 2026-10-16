"use strict";

// The interim beta's faces and person cards (web/src/armada/beta/faces.js,
// partners.js, partner_card.js), with a real DOM: a face stands its
// partner's Scarlet/Violet icon on a circle, initials stand in where there
// is no icon, the agent has no face, the picker offers only species with
// an icon, a press on a face opens that person's card, and Evolve on my
// own card leaves focus where the card found it. The beta reads
// the kit's window globals when it draws; the few it needs here are given
// as stand-ins that draw nothing of their own.

const assert = require("node:assert/strict");

const {clock, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`<div id="beta-data" data-sprite-version="7"></div>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("history", dom.window.history);
set_global("HTMLImageElement", dom.window.HTMLImageElement);
// What connection.js (which partners.js imports for its requests) listens to
// while the page loads. The tabs of this browser hear nothing here: Node's own
// BroadcastChannel would keep the test process alive after the last test.
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global(
    "BroadcastChannel",
    class {
        postMessage() {}
    },
);
// Where the caret goes when the field takes it back (composer.js, ready).
set_global("getSelection", () => dom.window.getSelection());
set_global("innerWidth", 1440);
set_global("innerHeight", 900);
const noop = () => {};
set_global("M", {
    T: {},
    SLOW: 1,
    release: noop,
    appear: noop,
    // The evolution moment's shorter form, which plays without motion.
    reduced: () => true,
    leave(element) {
        element.remove();
    },
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
// The kit's initials face, as the kit draws it (kit/kit.js).
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);

const ME = 3;
const IAGO = 5;
const CORDELIA = 6;
const KING = 7;
const AGENT = 9;
const EVE = 8;
const EVIL = `<img src="x" onerror="globalThis.pwned = 1">`;

const sv = (num, w, h) => ({path: `sv/${num}.png`, size: [w, h], box: [0, 0, w, h]});
const gen8 = (slug) => ({
    regular: `pokesprite/pokemon-gen8/regular/${slug}.png`,
    shiny: null,
    size: [68, 56],
    box: [20, 20, 28, 30],
    shiny_box: null,
});
const species = (num, name, types, line, prevo, evos, icon) => ({
    num,
    name,
    form: "",
    gen: 1,
    types,
    prevo,
    evos,
    how: null,
    line,
    first: prevo === null,
    offer: prevo === null && icon !== null,
    sprite: gen8(name.toLowerCase()),
    sv: icon,
});
const PACK = {
    version: 1,
    count: 9,
    starters: ["4"],
    species: {
        4: species(4, "Charmander", ["Fire"], "4", null, ["5"], sv(4, 96, 109)),
        5: species(5, "Charmeleon", ["Fire"], "4", "4", ["6"], sv(5, 100, 120)),
        6: species(6, "Charizard", ["Fire", "Flying"], "4", "5", [], sv(6, 140, 130)),
        16: species(16, "Pidgey", ["Normal", "Flying"], "16", null, [], null),
        19: species(19, "Rattata", ["Normal"], "19", null, [], sv(19, 90, 80)),
        95: species(95, "Onix", ["Rock", "Ground"], "95", null, [], null),
        172: species(172, "Pichu", ["Electric"], "172", null, ["25"], sv(172, 80, 90)),
        25: species(
            25,
            "Pikachu",
            ["Electric"],
            "172",
            "172",
            ["26", "26-alola"],
            sv(25, 119, 131),
        ),
        26: species(26, "Raichu", ["Electric"], "172", "25", [], sv(26, 120, 140)),
        "26-alola": species(26, "Alolan Raichu", ["Electric", "Psychic"], "172", "25", [], null),
    },
};
const team = (species, level) => ({
    species,
    shiny: false,
    level,
    stone: "rose",
    evolved_at: null,
    evolved_kind: null,
    may_change: false,
});
const PARTNERS = {
    result: "success",
    on: true,
    version: "7",
    partners: {[IAGO]: team("5", 20), [CORDELIA]: team("16", 4)},
    agent: {user_id: AGENT, species: "95", shiny: false},
    me: {
        species: "25",
        chosen: "172",
        shiny: false,
        held: false,
        later: false,
        xp: 900,
        level: 15,
        level_xp: 900,
        next_level_xp: 1024,
        active_days: 3,
        today_xp: 12,
        ready: null,
        evolved_at: null,
        evolved_kind: null,
        evolve_levels: [16, 30],
    },
};
const NOW = Date.now();
const ANSWERS = [
    [/^\/beta\/sprites\/species\.json/, () => PACK],
    [/^\/json\/armada\/partners$/, () => PARTNERS],
    [
        /^\/json\/armada\/partner\/evolve$/,
        () => ({...PARTNERS, me: {...PARTNERS.me, species: "26", ready: null, held: false}}),
    ],
    [/^\/json\/users\/5$/, () => ({result: "success", user: {timezone: "Asia/Kolkata"}})],
    [/^\/json\/users\/7$/, () => ({result: "success", user: {timezone: ""}})],
    [/^\/json\/users\/8$/, () => ({result: "success", user: {timezone: `Asia/${EVIL}`}})],
    [
        /^\/json\/messages\?/,
        (url) =>
            url.includes("%22operand%22%3A8")
                ? {
                      result: "success",
                      messages: [
                          {
                              id: 12,
                              type: "stream",
                              stream_id: 13,
                              subject: EVIL,
                              display_recipient: EVIL,
                              timestamp: Math.floor(NOW / 1000) - 60,
                          },
                      ],
                  }
                : url.includes("%22operand%22%3A5")
                  ? {
                        result: "success",
                        messages: [
                            {
                                id: 11,
                                type: "stream",
                                stream_id: 10,
                                subject: "option-groups",
                                display_recipient: "click",
                                timestamp: Math.floor(NOW / 1000) - 240,
                            },
                        ],
                    }
                  : {result: "success", messages: []},
    ],
];
set_global("fetch", async (url) => {
    const answer = ANSWERS.find(([pattern]) => pattern.test(url));
    assert.ok(answer, `no stand-in answer for ${url}`);
    const body = answer[1](url);
    return {ok: true, status: 200, json: async () => body};
});

const util = zrequire("armada/beta/util.js");
const beta_state = zrequire("armada/beta/state.js");
const partners = zrequire("armada/beta/partners.js");
const faces = zrequire("armada/beta/faces.js");
const card = zrequire("armada/beta/partner_card.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Desdemona";
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Desdemona"],
    [IAGO, "Iago Smith"],
    [CORDELIA, "Cordelia"],
    [KING, "King Hamlet"],
    [EVE, `${EVIL}Eve`],
    [AGENT, "Onix"],
]) {
    beta_state.people.set(id, {
        id,
        name,
        email: `${id}@example.com`,
        bot: id === AGENT,
        active: true,
    });
}
beta_state.streams.set(10, {stream_id: 10, name: "click"});
beta_state.streams.set(13, {stream_id: 13, name: EVIL});

// What a card fetches resolves through promises alone (the stand-in fetch never waits on a timer).
async function settle() {
    for (let i = 0; i < 50; i += 1) {
        await Promise.resolve();
    }
}
function drawn(html) {
    const t = document.createElement("template");
    t.innerHTML = html;
    return t.content.firstElementChild;
}
function shown(html) {
    const element = drawn(html);
    document.body.append(element);
    return element;
}

run_test("with no pack every face is initials", () => {
    assert.equal(partners.pack, null);
    const face = drawn(faces.faceOf(IAGO));
    assert.equal(face.textContent, "IS");
    assert.equal(face.querySelector("img"), null);
    assert.equal(face.dataset.uid, String(IAGO));
});

run_test("the picker offers first forms with an icon, never the agent's line", async () => {
    await partners.loadPartners();
    assert.ok(partners.pack);
    // Pidgey has no Scarlet/Violet icon and Onix is the agent's; Charmeleon is not a first form.
    assert.deepEqual(partners.OFFERED.toSorted(), ["172", "19", "4"]);
});

run_test("a face stands its partner's icon whole on a circle, head over the rim", () => {
    for (const size of [28, 20, 18]) {
        const at = partners.svPlace(PACK.species[5].sv, size);
        const inset = size < 24 ? 1 : 2;
        // feet just inside the circle's foot, head above its top, centred
        assert.equal(at.top + at.height, size - inset);
        assert.ok(at.top < 0, `the head peeps over a ${size} px circle`);
        assert.ok(Math.abs(at.left + at.width / 2 - size / 2) <= 1);
        // never much wider than the circle
        assert.ok(at.width <= (size < 24 ? size + 10 : Math.round(size * 1.36)) + 1);
    }
    const face = drawn(faces.faceOf(IAGO));
    const img = face.querySelector("img");
    assert.equal(img.getAttribute("src"), "/beta/sprites/sv/5.png");
    assert.equal(img.getAttribute("alt"), "");
    assert.equal(face.dataset.uid, String(IAGO));
    assert.equal(face.getAttribute("aria-hidden"), "true");
});

run_test("a face takes its type's colour; Normal yields to a second type", () => {
    assert.equal(partners.typeOf("4"), "fire");
    assert.equal(partners.typeOf("16"), "flying");
    assert.equal(partners.typeOf("19"), "normal");
    assert.equal(partners.typeOf("26-alola"), "electric");
});

run_test("a partner with no icon, and a list's smallest face, are initials", () => {
    assert.equal(drawn(faces.faceOf(CORDELIA)).textContent, "Co");
    assert.equal(drawn(faces.faceOf(KING)).textContent, "KH");
    assert.equal(drawn(faces.faceOf(IAGO, "s14")).querySelector("img"), null);
});

run_test("the agent has no face: nothing in the gutter, an empty cell in a list", () => {
    assert.equal(faces.faceOf(AGENT), "");
    const cell = drawn(faces.faceOf(AGENT, "s20"));
    assert.equal(cell.textContent, "");
    assert.equal(cell.querySelector("img"), null);
    assert.equal(cell.dataset.uid, undefined);
});

run_test("a partner never grows out of its face", () => {
    assert.deepEqual(partners.withIcons(["26", "26-alola"]), ["26"]);
    assert.deepEqual(partners.withIcons(["16"]), ["16"]);
    const [share, next, at] = card.growth("25", 23, [16, 30]);
    assert.deepEqual(next, ["26"]);
    assert.equal(at, 30);
    assert.equal(share, (23 - 16) / (30 - 16));
    assert.deepEqual(card.growth("6", 50, [16, 30]), [0.5, [], null]);
});

run_test("the card's words", () => {
    assert.equal(card.presenceWords(ME), "Active now");
    assert.equal(card.presenceWords(IAGO, "on"), "Active now");
    assert.equal(card.presenceWords(IAGO, "away"), "Away");
    const seen = {"5@example.com": {aggregated: {status: "idle", timestamp: NOW / 1000 - 7200}}};
    assert.equal(card.presenceWords(IAGO, "", seen, NOW), "Active 2h ago");
    assert.equal(card.presenceWords(IAGO, "", {}, NOW), "Offline");
    assert.match(
        card.localTime("Asia/Kolkata", Date.UTC(2026, 9, 15, 9, 0)),
        /^2:30\s?PM in Kolkata$|^14:30 in Kolkata$/,
    );
    assert.equal(card.localTime(""), "");
    assert.equal(card.localTime("Not/AZone"), "");
    assert.equal(card.mentionOf(IAGO), "@**Iago Smith**");
    beta_state.people.set(12, {
        id: 12,
        name: "Iago Smith",
        email: "12@example.com",
        bot: false,
        active: true,
    });
    assert.equal(card.mentionOf(IAGO), "@**Iago Smith|5**");
    beta_state.people.delete(12);
    assert.equal(card.lastPlace(undefined), null);
    assert.equal(
        card.lastPlace({
            type: "stream",
            stream_id: 10,
            subject: "",
            display_recipient: "click",
            timestamp: 1,
        }).label,
        "click / general chat",
    );
    assert.equal(
        card.lastPlace({type: "private", display_recipient: [{id: ME}, {id: IAGO}], timestamp: 1})
            .label,
        "A direct message",
    );
});

run_test("a press on a face opens that person's card, and again closes it", async () => {
    card.wireCard();
    const face = shown(
        `<section class="cl them">${faces.faceOf(IAGO)}</section>`,
    ).firstElementChild;
    dom.click(face.querySelector("img") ?? face);
    let open = document.querySelector('[role="dialog"]');
    assert.ok(open, "the card opened");
    assert.equal(open.getAttribute("aria-label"), "Iago Smith");
    assert.match(open.textContent, /Iago Smith/);
    assert.match(open.textContent, /Charmeleon/);
    assert.match(open.textContent, /Level 20/);
    assert.match(open.textContent, /Charizard at level 30/);
    const buttons = new Set([...open.querySelectorAll("button")].map((b) => b.textContent));
    assert.ok(buttons.has("Message"));
    assert.ok(!buttons.has("Sign out"));
    // Its facts arrive after it opens, into cells it already has.
    await settle();
    assert.match(open.textContent, /Local time.*in Kolkata/);
    assert.match(open.textContent, /click \/ option-groups · 4m/);
    dom.click(face);
    assert.equal(document.querySelector('[role="dialog"]'), null);

    // Someone with no partner, who has said nothing and set no time zone.
    const king = shown(faces.faceOf(KING));
    dom.click(king);
    await settle();
    open = document.querySelector('[role="dialog"]');
    assert.match(open.textContent, /Not set/);
    assert.match(open.textContent, /Nothing yet/);
    card.closeCard();
});

run_test("a card takes focus when it opens, and Tab goes round its buttons", async () => {
    beta_state.state.place = {key: "s:10:option-groups", kind: "topic"};
    dom.click(shown(faces.faceOf(IAGO)));
    await settle();
    // It takes focus once the press that opened it has ended.
    clock.tick(0);
    const open = document.querySelector('[role="dialog"]');
    // On the card itself, so that Return presses nothing.
    assert.equal(document.activeElement, open);
    const buttons = [...open.querySelectorAll("button")];
    assert.ok(buttons.length > 1);
    for (const button of [...buttons, buttons[0]]) {
        dom.press("Tab");
        assert.equal(document.activeElement, button);
    }
    dom.press("Tab", {shiftKey: true});
    assert.equal(document.activeElement, buttons.at(-1));
    card.closeCard();
    beta_state.state.place = null;
});

run_test("Esc closes a card and gives focus back to what had it", async () => {
    beta_state.state.place = {key: "s:10:option-groups", kind: "topic"};
    // Opened from a face while I was writing: the field has the caret again.
    const field = shown(`<div class="tx" contenteditable="true">hi</div>`);
    field.focus();
    dom.click(shown(faces.faceOf(IAGO)));
    await settle();
    clock.tick(0);
    assert.equal(document.activeElement, document.querySelector('[role="dialog"]'));
    dom.press("Escape");
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(document.activeElement, field);

    // My own card, opened from the account row with the keyboard: the row.
    const row = shown(`<button class="row me">Desdemona</button>`);
    row.focus();
    card.openCard();
    clock.tick(0);
    assert.equal(document.activeElement, document.querySelector('[role="dialog"]'));
    dom.press("Escape");
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(document.activeElement, row);
    beta_state.state.place = null;
});

run_test("a face in the nav opens no card", () => {
    const nav = shown(`<nav>${faces.faceOf(IAGO, "s20")}</nav>`);
    dom.click(nav.firstElementChild);
    assert.equal(document.querySelector('[role="dialog"]'), null);
});

run_test("my own card offers Change partner and Sign out", () => {
    card.openCard();
    const open = document.querySelector('[role="dialog"]');
    assert.equal(open.getAttribute("aria-label"), "You");
    assert.match(open.textContent, /Pikachu/);
    assert.match(open.textContent, /124 of 124 to level 16|0 of 124 to level 16/);
    assert.match(open.textContent, /Next: Raichu at level 30\./);
    const buttons = [...open.querySelectorAll("button")].map((b) => b.textContent);
    assert.deepEqual(buttons, ["Change partner", "Sign out"]);
    card.openCard();
    assert.equal(document.querySelector('[role="dialog"]'), null);
});

run_test("a card writes names, places and zones as text, never as markup", async () => {
    const face = shown(faces.faceOf(EVE));
    assert.equal(face.querySelector("img"), null);
    dom.click(face);
    await settle();
    const open = document.querySelector('[role="dialog"]');
    assert.equal(open.getAttribute("aria-label"), `${EVIL}Eve`);
    assert.equal(open.querySelector("img[onerror]"), null);
    assert.equal(open.querySelector("[onerror]"), null);
    assert.ok(open.querySelector(".ih b").textContent.startsWith(EVIL));
    assert.ok(open.textContent.includes(`${EVIL} / ${EVIL}`));
    assert.equal(globalThis.pwned, undefined);
    card.closeCard();
});

run_test("Mention writes the person into the open field and closes the card", () => {
    const field = shown(`<div class="tx" contenteditable="true">hi</div>`);
    beta_state.state.threadTx = field;
    const face = shown(faces.faceOf(IAGO));
    dom.click(face);
    const open = document.querySelector('[role="dialog"]');
    // Mention is offered only where there is a conversation.
    assert.equal(open.querySelector("[data-card-mention]"), null);
    card.closeCard();
    beta_state.state.place = {key: "s:10:option-groups", kind: "topic"};
    dom.click(face);
    dom.click(document.querySelector("[data-card-mention]"));
    assert.equal(field.textContent, "hi @**Iago Smith** ");
    assert.equal(document.querySelector('[role="dialog"]'), null);
    beta_state.state.threadTx = null;
    beta_state.state.place = null;
});

run_test("Evolve on my own card gives focus back to what opened the card", async () => {
    // As host.js wires it: Evolve plays the moment, which takes the card's place.
    util.press(document.body, ".pcard [data-evolve]", (b) => card.playMoment(b.dataset.evolve));
    // Pikachu is ready to become Raichu, and I kept it as it was: my card
    // offers Evolve.
    partners.applyPartners({
        ...PARTNERS,
        me: {...PARTNERS.me, ready: {kind: "evolve", choices: ["26", "26-alola"]}, held: true},
    });
    beta_state.state.place = {key: "s:10:option-groups", kind: "topic"};

    // My card, opened from the account row with the keyboard; Tab to
    // Evolve, and Return presses it (as a browser does, which jsdom does
    // not do itself).
    const row = shown(`<button class="row me">Desdemona</button>`);
    row.focus();
    card.openCard();
    clock.tick(0);
    assert.equal(document.activeElement, document.querySelector('[role="dialog"]'));
    dom.press("Tab");
    assert.equal(document.activeElement.textContent, "Evolve");
    document.activeElement.click();
    // The moment plays where the card was, and the row has focus again.
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.match(document.querySelector('[role="status"]').textContent, /Pikachu is evolving/);
    assert.equal(document.activeElement, row);
    // Any key ends the moment where it stands: the evolution happens.
    card.cutMoment();
    await settle();
    assert.equal(document.querySelector('[role="status"]'), null);

    // Opened from my face while I was writing, and Evolve pressed: the
    // field has the caret again.
    partners.applyPartners({
        ...PARTNERS,
        me: {...PARTNERS.me, ready: {kind: "evolve", choices: ["26", "26-alola"]}, held: true},
    });
    const field = shown(`<div class="tx" contenteditable="true">hi</div>`);
    field.focus();
    dom.click(shown(faces.faceOf(ME)));
    clock.tick(0);
    assert.equal(document.querySelector('[role="dialog"]').getAttribute("aria-label"), "You");
    dom.click(document.querySelector(".pcard [data-evolve]"));
    assert.ok(document.querySelector('[role="status"]'));
    assert.equal(document.activeElement, field);
    card.cutMoment();
    await settle();

    // Opened with nothing in focus: the card lets go of it before it
    // leaves, and the field I am writing in takes the caret.
    partners.applyPartners({
        ...PARTNERS,
        me: {...PARTNERS.me, ready: {kind: "evolve", choices: ["26", "26-alola"]}, held: true},
    });
    document.activeElement.blur();
    beta_state.state.threadTx = field;
    card.openCard();
    clock.tick(0);
    dom.click(document.querySelector(".pcard [data-evolve]"));
    assert.ok(document.querySelector('[role="status"]'));
    assert.equal(document.activeElement, field);
    card.cutMoment();
    await settle();

    partners.applyPartners(PARTNERS);
    beta_state.state.threadTx = null;
    beta_state.state.place = null;
});
