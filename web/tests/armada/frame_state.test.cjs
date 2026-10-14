"use strict";

// The frame's state and its command skeletons: the route a place and a view
// are drawn from, the device's preferences, the one list of commands and the
// keys bound to them.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {store} = zrequire("armada/state/store.ts");
const route = zrequire("armada/state/route.ts");
const prefs = zrequire("armada/state/prefs.ts");
const registry = zrequire("armada/commands/registry.ts");
const keymap = zrequire("armada/commands/keymap.ts");
const places = zrequire("armada/commands/places.ts");

run_test("a store tells who listens of a change, once, and not of the same value", () => {
    const counter = store(1);
    const seen = [];
    const stop = counter.subscribe((value) => seen.push(value));
    counter.set(1);
    counter.set(2);
    assert.equal(counter.get(), 2);
    stop();
    counter.set(3);
    assert.deepEqual(seen, [2]);
});

run_test("a store takes its own idea of equal", () => {
    const pair = store({a: 1}, (x, y) => x.a === y.a);
    const seen = [];
    pair.subscribe((value) => seen.push(value.a));
    pair.set({a: 1});
    pair.set({a: 2});
    assert.deepEqual(seen, [2]);
});

run_test("the route: only a worktree has views, and a place changes the view once", () => {
    const seen = [];
    const stop = route.route.subscribe((value) => seen.push(`${value.place}:${value.view}`));
    route.go("worktree", "files");
    assert.deepEqual(route.route.get(), {place: "worktree", view: "files"});
    assert.equal(route.folds_column(route.route.get()), true);
    route.show_view("readme");
    assert.equal(route.folds_column(route.route.get()), false);
    route.show_view("readme");
    route.go("channel", "files");
    assert.deepEqual(route.route.get(), {place: "channel", view: "chat"});
    route.go("home");
    stop();
    assert.deepEqual(seen, ["worktree:files", "worktree:readme", "channel:chat", "home:chat"]);
    assert.equal(route.folds_column({place: "worktree", view: "changes"}), true);
    assert.equal(route.is_place("dm"), true);
    assert.equal(route.is_place("settings"), false);
    assert.equal(route.is_view("changes"), true);
    assert.equal(route.is_view("trace"), false);
});

run_test("the nav's width is clamped to whole pixels between 200 and 320", () => {
    assert.equal(prefs.clamp_nav_width(150), 200);
    assert.equal(prefs.clamp_nav_width(400), 320);
    assert.equal(prefs.clamp_nav_width(250.4), 250);
    assert.equal(prefs.clamp_nav_width(Number.NaN), 248);
    assert.deepEqual(prefs.prefs_from(undefined), {nav_width: 248});
    assert.deepEqual(prefs.prefs_from({nav_width: "300"}), {nav_width: 300});
    assert.deepEqual(prefs.prefs.get(), {nav_width: 248});
    prefs.prefs.set({nav_width: 248});
    prefs.prefs.set({nav_width: 260});
    assert.deepEqual(prefs.prefs.get(), {nav_width: 260});
});

run_test("one command has one name, and runs by it", () => {
    registry.clear_for_testing();
    let ran = 0;
    registry.register([
        {id: "column.open", label: "Open the column", key: "Mod+J", run: () => (ran += 1)},
    ]);
    assert.throws(
        () => registry.register([{id: "column.open", label: "again", run() {}}]),
        /registered twice: column.open/,
    );
    assert.equal(registry.get("column.open").label, "Open the column");
    assert.equal(registry.all().length, 1);
    assert.equal(registry.run("column.open"), true);
    assert.equal(registry.run("nothing"), false);
    assert.equal(ran, 1);
    registry.clear_for_testing();
    assert.equal(registry.all().length, 0);
});

run_test("a key is Command on a Mac and Control elsewhere", () => {
    registry.clear_for_testing();
    registry.register([
        {id: "column.open", label: "Open", key: "Mod+J", run() {}},
        {id: "run.stop", label: "Stop", key: "Mod+.", run() {}},
        {id: "plain", label: "No key", run() {}},
        {id: "search", label: "Search", key: "Shift+Alt+Escape", run() {}},
    ]);
    const press = (key, mods = {}) => ({
        key,
        metaKey: false,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
        ...mods,
    });
    assert.deepEqual(keymap.parse("Mod+J"), {key: "j", mod: true, shift: false, alt: false});
    assert.equal(keymap.command_for(press("J", {metaKey: true}), true).id, "column.open");
    assert.equal(keymap.command_for(press("j", {ctrlKey: true}), false).id, "column.open");
    assert.equal(keymap.command_for(press("j", {ctrlKey: true}), true), undefined);
    assert.equal(keymap.command_for(press(".", {metaKey: true}), true).id, "run.stop");
    assert.equal(
        keymap.command_for(press("Escape", {shiftKey: true, altKey: true}), false).id,
        "search",
    );
    assert.equal(keymap.command_for(press("Escape"), false), undefined);
    registry.clear_for_testing();
});

run_test("Home is the one place that is not a conversation; a worktree the one with views", () => {
    assert.deepEqual(
        Object.values(places.PLACE_KINDS)
            .filter((kind) => !kind.conversation)
            .map((kind) => kind.place),
        ["home"],
    );
    assert.deepEqual(
        Object.values(places.PLACE_KINDS)
            .filter((kind) => kind.views)
            .map((kind) => kind.place),
        ["worktree"],
    );
});
