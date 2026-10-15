"use strict";

// The interim beta's faces (web/src/armada/beta/faces.js, partners.js),
// with a real DOM: a face stands its partner's Scarlet/Violet icon on a
// circle, initials stand in where there is no icon, the agent has no face,
// and the picker offers only species with an icon. The beta reads the
// kit's window globals when it draws; the few it needs here are given as
// stand-ins that draw nothing of their own.

const assert = require("node:assert/strict");

const {set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`<div id="beta-data" data-sprite-version="7"></div>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("history", dom.window.history);
set_global("HTMLImageElement", dom.window.HTMLImageElement);
set_global("getSelection", () => dom.window.getSelection());
set_global("requestAnimationFrame", (fn) => setTimeout(fn, 0));
set_global("innerWidth", 1440);
set_global("innerHeight", 900);
const noop = () => {};
set_global("M", {
    T: {},
    SLOW: 1,
    reduced: () => true,
    release: noop,
    appear: noop,
    leave(element) {
        element.remove();
    },
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global("mk", (name) => `<svg class="mk ${name}" data-mark="${name}"></svg>`);
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
const ANSWERS = [
    [/^\/beta\/sprites\/species\.json/, () => PACK],
    [/^\/json\/armada\/partners$/, () => PARTNERS],
];
set_global("fetch", async (url) => {
    const answer = ANSWERS.find(([pattern]) => pattern.test(url));
    assert.ok(answer, `no stand-in answer for ${url}`);
    const body = answer[1](url);
    return {ok: true, status: 200, headers: {get: () => null}, json: async () => body};
});

const beta_state = zrequire("armada/beta/state.js");
const partners = zrequire("armada/beta/partners.js");
const faces = zrequire("armada/beta/faces.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Desdemona";
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Desdemona"],
    [IAGO, "Iago Smith"],
    [CORDELIA, "Cordelia"],
    [KING, "King Hamlet"],
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

function drawn(html) {
    const t = document.createElement("template");
    t.innerHTML = html;
    return t.content.firstElementChild;
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
});
