"use strict";

const assert = require("node:assert/strict");

const {h, render} = require("preact");

const {set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const tree = zrequire("armada/kit/tree.ts");
const view = zrequire("armada/kit/view.tsx");
const kit = zrequire("armada/kit/recipes.ts");
const dom_kit = zrequire("armada/kit/dom.ts");
const names = zrequire("armada/kit/icons/names.ts");
const fixtures = zrequire("armada/showroom/fixtures.ts");

// The kit builds SVG as well as HTML.
function make_real_dom(body_html) {
    const dom = real_dom.make_real_dom(body_html);
    set_global("SVGElement", dom.window.SVGElement);
    set_global("HTMLTextAreaElement", dom.window.HTMLTextAreaElement);
    return dom;
}

const VERBS = ["Send", "Steer", "Answer", "Queue", "Reply"];

function draw(description) {
    const dom = make_real_dom(`<div id="preact"></div><div id="built"></div>`);
    render(view.to_vnode(description), dom.find("#preact"));
    dom.find("#built").append(tree.build(description));
    return dom;
}

run_test("Preact and the DOM builder draw every showroom fixture the same", () => {
    let compared = 0;
    for (const section of fixtures.sections("Agent")) {
        for (const item of section.items) {
            const dom = draw(item.body);
            assert.equal(
                dom.find("#preact").innerHTML,
                dom.find("#built").innerHTML,
                `${section.id}: ${item.caption ?? ""}`,
            );
            compared += 1;
        }
    }
    assert.ok(compared >= 80, String(compared));
});

run_test("every control in the showroom has a name", () => {
    const dom = make_real_dom();
    let controls = 0;
    for (const section of fixtures.sections("Agent")) {
        for (const item of section.items) {
            dom.body.replaceChildren(tree.build(item.body));
            for (const control of dom.body.querySelectorAll(
                "button, textarea, [role=switch], [role=checkbox]",
            )) {
                const name = control.getAttribute("aria-label") ?? control.textContent.trim();
                assert.notEqual(name, "", `${section.id}: ${control.outerHTML.slice(0, 120)}`);
                controls += 1;
            }
        }
    }
    assert.ok(controls > 200, String(controls));
});

run_test("the sprite's names are the design's sets", () => {
    assert.equal(names.glyph_groups.length, 8);
    assert.equal(names.glyph_groups.flatMap((group) => group.glyphs).length, 49);
    assert.deepEqual(names.mark_names, [
        "done",
        "live",
        "waiting",
        "needs",
        "failed",
        "paused",
        "stopped",
    ]);
    assert.deepEqual(names.sigil_names, ["20", "28", "64", "20-outline"]);
    assert.equal(names.working_steps, 4);
});

run_test("a description becomes elements, with its state as attributes", () => {
    const pressed = [];
    const dom = draw(
        tree.n(
            "div",
            {id: "host", hidden: false, title: undefined, "data-on": true, "data-count": 3},
            [
                "text",
                [
                    7,
                    false,
                    null,
                    undefined,
                    tree.n("svg", {}, [tree.n("use", {href: "#armada-dot"})]),
                ],
                tree.n("button", {disabled: true}, ["off"], {
                    on: {click: () => pressed.push("off"), keydown: undefined},
                }),
            ],
        ),
    );
    const host = dom.find("#built #host");
    assert.equal(host.getAttribute("data-on"), "");
    assert.equal(host.dataset.count, "3");
    assert.equal(host.hasAttribute("hidden"), false);
    assert.equal(host.hasAttribute("title"), false);
    assert.equal(host.textContent, "text7off");
    assert.equal(host.querySelector("use").namespaceURI, "http://www.w3.org/2000/svg");
    assert.equal(dom.find("#preact button").disabled, true);
    assert.equal(dom.find("#built button").disabled, true);
    // A disabled control is not pressed; its handler is still its own.
    dom.find("#built button").disabled = false;
    dom.click(dom.find("#built button"));
    assert.deepEqual(pressed, ["off"]);
    assert.equal(tree.build(kit.block(["text"])).textContent, "text");
    assert.equal(tree.build(kit.home_heading("Needs you")).tagName, "H2");
    assert.equal(
        tree
            .build(kit.nav_top({name: {label: "Armada"}, create: {label: "New"}}))
            .querySelectorAll("button").length,
        2,
    );
    assert.equal(tree.is_tree("text"), false);
    assert.equal(
        tree.forced(kit.button({label: "Cancel"}), "hover").attributes["data-force"],
        "hover",
    );
    assert.equal(
        tree.forced(kit.button({label: "Cancel"}), undefined).attributes["data-force"],
        undefined,
    );

    let seen;
    const referenced = tree.n("p", {}, [], {ref: (element) => (seen = element)});
    const both = draw(referenced);
    assert.equal(seen, both.find("#built p"));
    render(null, both.find("#preact"));
    const Component = view.component(kit.button);
    render(h(Component, {label: "Cancel"}), both.find("#preact"));
    assert.equal(both.find("#preact button").textContent, "Cancel");
    render(
        h(view.View, {tree: kit.button({label: "Discard", kind: "danger"})}),
        both.find("#preact"),
    );
    assert.equal(both.find("#preact button").dataset.kind, "danger");
});

run_test("a control is pressed with a click; a view answers on the press and the keys", () => {
    const pressed = [];
    const dom = make_real_dom();
    const press = (label) => () => pressed.push(label);
    dom.body.append(
        dom_kit.text_control({label: "Review", on_press: press("review")}),
        dom_kit.glyph_control({
            glyph: "more",
            label: "More",
            on_press: press("more"),
            small: true,
            pressed: false,
            expanded: true,
        }),
        tree.build(
            kit.views({
                label: "Views",
                views: [
                    {label: "Chat", selected: true, on_press: press("chat"), count: 3},
                    {label: "Files", selected: false},
                ],
            }),
        ),
        tree.build(
            kit.segments({
                label: "Document",
                options: [{label: "Read", selected: true, on_press: press("read")}],
            }),
        ),
        tree.build(
            kit.nav_row({label: "devel", glyph: "hash", count: 12, on_press: press("devel")}),
        ),
        tree.build(
            kit.tree_row({
                label: "core.py",
                glyph: "file",
                depth: 2,
                current: true,
                trailing: kit.counts({added: 3, removed: 1}),
                on_press: press("file"),
            }),
        ),
    );
    const by_name = (name) =>
        [...dom.body.querySelectorAll("button")].find((button) =>
            (button.getAttribute("aria-label") ?? button.textContent).startsWith(name),
        );
    dom.click(by_name("Review"));
    dom.click(by_name("More"));
    assert.deepEqual(pressed, ["review", "more"]);
    assert.equal(by_name("More").getAttribute("aria-expanded"), "true");
    // A view: the primary button's press, not its release; Return and Space.
    const chat = by_name("Chat");
    assert.equal(chat.getAttribute("aria-current"), "page");
    chat.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
    chat.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 2}));
    dom.click(chat);
    assert.equal(dom.press("Enter", {target: chat}), false);
    assert.equal(dom.press(" ", {target: chat}), false);
    assert.equal(dom.press("x", {target: chat}), true);
    assert.deepEqual(pressed.slice(2), ["chat", "chat", "chat"]);
    for (const name of ["Read", "devel", "core.py"]) {
        by_name(name).dispatchEvent(
            new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}),
        );
    }
    assert.deepEqual(pressed.slice(5), ["read", "devel", "file"]);
    assert.equal(by_name("devel").textContent, "devel12");
    assert.equal(by_name("core.py").getAttribute("aria-current"), "true");
});

run_test("the field knows whether it holds words, with no layout read", () => {
    const typed = [];
    const dom = make_real_dom();
    const field = tree.build(
        kit.field({
            label: "Message",
            placeholder: "Message option-groups",
            send: {verb: "Send", verbs: VERBS},
            plus: {label: "Attach"},
            agent: {label: "Agent answers", answers: false},
            mode: {label: "Model and effort", text: "Sol Fast · High", sizer: "Sol Fast · Medium"},
            attachment: "help-output.txt",
            on_input: () => typed.push("input"),
        }),
    );
    dom.body.append(field);
    const input = field.querySelector("textarea");
    assert.equal(input.getAttribute("aria-label"), "Message");
    assert.equal(field.hasAttribute("data-filled"), false);
    dom.type(input, "hi");
    assert.equal(field.hasAttribute("data-filled"), true);
    assert.deepEqual(typed, ["input", "input"]);
    input.value = "";
    input.dispatchEvent(new dom.window.InputEvent("input", {bubbles: true}));
    assert.equal(field.hasAttribute("data-filled"), false);
    // For people only: the sigil is its outline and the model keeps its cell.
    assert.equal(field.querySelector(".armada-field-agent").getAttribute("aria-pressed"), "false");
    assert.ok(
        field.querySelector(".armada-field-agent use").getAttribute("href").endsWith("20-outline"),
    );
    assert.ok(field.querySelector(".armada-field-mode").hasAttribute("data-off"));
    // The default button names the verb Return performs, as a word in a
    // cell that holds every verb, or as a glyph.
    const send = field.querySelector(".armada-send");
    assert.equal(send.getAttribute("aria-label"), "Send");
    assert.equal(send.querySelectorAll("[data-sizer]").length, VERBS.length);
    const glyph = tree.build(
        kit.send_button({
            verb: "Steer",
            verbs: VERBS,
            form: "glyph",
            glyph: "steer",
            tip: "Steer the run",
        }),
    );
    assert.equal(glyph.dataset.form, "glyph");
    assert.equal(glyph.dataset.tip, "Steer the run");
    assert.equal(glyph.querySelector("[data-sizer]"), null);
    assert.equal(
        tree.build(kit.send_button({verb: "Steer", verbs: VERBS, form: "glyph"})).dataset.form,
        "word",
    );
    // Disabled: one sentence, and nothing to press.
    const off = tree.build(
        kit.field({
            label: "Message",
            placeholder: "Read only.",
            value: "draft",
            send: {verb: "Send", verbs: VERBS},
            plus: {label: "Attach"},
            agent: {label: "Agent answers", answers: true},
            mode: {label: "Model", text: "Sol", sizer: "Sol", open: true},
            disabled: true,
            flat: true,
        }),
    );
    assert.equal(off.querySelector("textarea").disabled, true);
    assert.equal(off.querySelector(".armada-send").disabled, true);
    assert.deepEqual(
        [...off.querySelectorAll("button")].filter((button) => !button.disabled),
        [],
    );
    assert.ok(off.hasAttribute("data-filled"));
    off.querySelector("textarea").dispatchEvent(new dom.window.Event("input"));
    field.dispatchEvent(new dom.window.Event("input"));
});

run_test("a list draws the rows it has slots for and counts the rest", () => {
    const rows = [1, 2, 3, 4, 5];
    assert.deepEqual(kit.fit(rows, undefined, "head"), {shown: rows, hidden: 0});
    assert.deepEqual(kit.fit(rows, 7, "head"), {shown: rows, hidden: 0});
    assert.deepEqual(kit.fit(rows, 4, "head"), {shown: [1, 2, 3, 4], hidden: 1});
    // The checklist folds its earliest rows: the live step stays in sight.
    assert.deepEqual(kit.fit(rows, 3, "tail"), {shown: [3, 4, 5], hidden: 2});
    const steps = rows.map((n) => ({
        kind: "step",
        state: n === 5 ? "live" : "done",
        title: `Step ${n}`,
    }));
    const list = tree.build(
        kit.checklist({label: "Checklist", steps, slots: 3, more: {label: "2 earlier"}}),
    );
    assert.deepEqual(
        [...list.querySelectorAll("li")].map((li) => li.textContent),
        ["Step 3", "Step 4", "Step 5"],
    );
    assert.equal(list.querySelector(".armada-more").textContent, "2 earlier");
    assert.equal(
        tree
            .build(kit.checklist({label: "Checklist", steps, more: {label: "0 earlier"}}))
            .querySelector(".armada-more"),
        null,
    );
    const section = tree.build(
        kit.section({heading: "Threads", rows: [], empty: "No threads here yet."}),
    );
    assert.equal(section.querySelector(".armada-empty").textContent, "No threads here yet.");
});

run_test("faces, stones and figures", () => {
    assert.equal(kit.stone_of(0), "moss");
    assert.equal(kit.stone_of(7), "larimar");
    assert.equal(kit.stone_of(-11), "flint");
    assert.equal(new Set(Array.from({length: 60}, (_, id) => kit.stone_of(id))).size, 6);
    const text = (description) => tree.build(description).textContent;
    const person = {initials: "KH", stone: "lapis"};
    assert.equal(text(kit.face(person)), "KH");
    assert.equal(text(kit.face({...person, size: 20})), "K");
    assert.equal(text(kit.face({...person, size: 14})), "");
    assert.equal(
        tree.build(kit.face({...person, name: "King Hamlet"})).getAttribute("role"),
        "img",
    );
    assert.equal(
        tree
            .build(kit.face({...person, photo: "/avatar/7"}))
            .querySelector("img")
            .getAttribute("src"),
        "/avatar/7",
    );
    assert.deepEqual(
        [...tree.build(kit.figure("1m 05s")).children].map((cell) => cell.dataset.cell),
        [undefined, "word", "mark", undefined, undefined, "word"],
    );
    assert.equal(tree.build(kit.sigil("20-outline")).dataset.size, "20");
    assert.equal(tree.build(kit.working_mark({live: false})).dataset.working, "rest");
    assert.equal(dom_kit.stream_head({first: true}).dataset.first, "");
    assert.equal(dom_kit.face(person).tagName, "I");
    assert.throws(() => dom_kit.build(kit.glyph("more")).click(), TypeError);
});

run_test("the feed's builders make real elements it can keep", () => {
    make_real_dom();
    const row = dom_kit.bubble({content: ["public"], time: "11:36", join: "above"});
    assert.ok(row instanceof globalThis.HTMLElement);
    const turn = dom_kit.turn({side: "mine", children: []});
    turn.append(row);
    assert.equal(turn.querySelector(".armada-bubble").textContent, "public");
    assert.equal(dom_kit.day("Tuesday").getAttribute("role"), "separator");
    assert.equal(dom_kit.recipes.mention({name: "Desdemona", me: true}).attributes["data-me"], "");
});

// The top layer, as a browser gives it: jsdom has neither popovers nor
// modal dialogs. Showing and hiding fire "toggle" and "close" as they do.
function top_layer(dom) {
    const {HTMLElement, HTMLDialogElement, Event} = dom.window;
    const shown = new Set();
    HTMLElement.prototype.showPopover = function () {
        shown.add(this);
        const event = new Event("toggle");
        event.newState = "open";
        this.dispatchEvent(event);
    };
    HTMLElement.prototype.hidePopover = function () {
        shown.delete(this);
        const event = new Event("toggle");
        event.newState = "closed";
        this.dispatchEvent(event);
    };
    HTMLDialogElement.prototype.showModal = function () {
        shown.add(this);
        this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function () {
        shown.delete(this);
        this.removeAttribute("open");
        this.dispatchEvent(new Event("close"));
    };
    set_global("HTMLDialogElement", HTMLDialogElement);
    return shown;
}

const keys = zrequire("armada/kit/keys.ts");

const label_of = (element) => element?.textContent.trim().split(/\s/)[0];

run_test("a group is one Tab stop; the arrows, Home and End move within it", () => {
    const dom = make_real_dom();
    const menu = tree.build(
        kit.menu({
            still: true,
            label: "Message",
            items: [
                {label: "Reply"},
                {label: "Copy", current: true},
                "gap",
                {label: "Edit", disabled: true},
                {label: "Delete"},
            ],
        }),
    );
    dom.body.append(menu);
    const stops = () =>
        [...menu.querySelectorAll("[tabindex='0']")].map((element) => label_of(element));
    // The current value is the Tab stop; every other row is not one.
    assert.deepEqual(stops(), ["Copy"]);
    assert.equal(menu.querySelectorAll("[tabindex='-1']").length, 3);
    menu.querySelector("[tabindex='0']").focus();
    // The disabled row and the gap are passed over; the ends wrap.
    assert.equal(dom.press("ArrowDown"), false);
    assert.equal(label_of(document.activeElement), "Delete");
    dom.press("ArrowDown");
    assert.equal(label_of(document.activeElement), "Reply");
    dom.press("ArrowUp");
    assert.equal(label_of(document.activeElement), "Delete");
    dom.press("Home");
    assert.equal(label_of(document.activeElement), "Reply");
    dom.press("End");
    assert.equal(label_of(document.activeElement), "Delete");
    assert.deepEqual(stops(), ["Delete"]);
    // Across a group's axis, and other keys, are not the group's.
    assert.equal(dom.press("ArrowRight"), true);
    assert.equal(dom.press("a"), true);
    // Only a key moves anything.
    menu.dispatchEvent(new dom.window.Event("keydown"));
    assert.equal(label_of(document.activeElement), "Delete");

    // The picker: its models are one group and its efforts another.
    const picker = tree.build(
        kit.picker({
            still: true,
            label: "Model and effort",
            models: [
                {label: "Sol", current: false},
                {label: "Fast", current: true},
            ],
            efforts: {
                label: "Effort",
                steps: ["Low", "High"].map((label) => ({label, selected: label === "High"})),
            },
            caption: "48 percent of context used",
        }),
    );
    dom.body.append(picker);
    assert.deepEqual(
        [...picker.querySelectorAll("[tabindex='0']")].map((element) => label_of(element)),
        ["Fast", "High"],
    );
    picker.querySelector(".armada-picker-step[tabindex='0']").focus();
    dom.press("ArrowLeft");
    assert.equal(label_of(document.activeElement), "Low");
    // From the efforts the arrows enter the models at their near end.
    dom.press("ArrowUp");
    assert.equal(label_of(document.activeElement), "Fast");
    picker.querySelector(".armada-picker-step").focus();
    dom.press("ArrowDown");
    assert.equal(label_of(document.activeElement), "Sol");

    // Segments and views: the selected one is the stop; the arrows go across.
    const views = tree.build(
        kit.views({
            label: "Views",
            views: [
                {label: "Chat", selected: false},
                {label: "Files", selected: true},
                {label: "Changes", selected: false},
            ],
        }),
    );
    const segments = tree.build(
        kit.segments({
            label: "Document",
            options: [
                {label: "Read", selected: false},
                {label: "Edit", selected: false},
            ],
        }),
    );
    dom.body.append(views, segments);
    assert.equal(label_of(views.querySelector("[tabindex='0']")), "Files");
    assert.equal(label_of(segments.querySelector("[tabindex='0']")), "Read");
    views.querySelector("[tabindex='0']").focus();
    dom.press("ArrowRight");
    assert.equal(label_of(document.activeElement), "Changes");
    dom.press("ArrowRight");
    assert.equal(label_of(document.activeElement), "Chat");
    assert.equal(dom.press("ArrowDown"), true);
});

run_test("a raised surface opens in the top layer and gives focus back", () => {
    const dom = make_real_dom(`<button id="opener">More</button><button id="else">Else</button>`);
    const shown = top_layer(dom);
    const chosen = [];
    const menu = tree.build(
        kit.menu({
            label: "Message",
            items: [{label: "Reply", on_press: () => chosen.push("reply")}, {label: "Copy"}],
        }),
    );
    dom.body.append(menu);
    // A live surface waits closed in the popover state.
    assert.equal(menu.getAttribute("popover"), "auto");
    const opener = dom.find("#opener");
    opener.focus();
    keys.open_raised(menu);
    assert.ok(shown.has(menu));
    assert.equal(label_of(document.activeElement), "Reply");
    dom.press("ArrowDown");
    // Esc and a press outside are the platform's; however it closes, focus
    // goes back to what opened it.
    menu.hidePopover();
    assert.equal(document.activeElement, opener);
    // Choosing closes the menu.
    keys.open_raised(menu, opener);
    dom.click(menu.querySelector("button"));
    assert.deepEqual(chosen, ["reply"]);
    assert.ok(!shown.has(menu));
    assert.equal(document.activeElement, opener);
    // A press elsewhere keeps the focus it moved; an opener that is gone
    // is not focused.
    keys.open_raised(menu, opener);
    dom.find("#else").focus();
    keys.close_raised(menu);
    assert.equal(document.activeElement, dom.find("#else"));
    keys.open_raised(menu, opener);
    opener.remove();
    keys.close_raised(menu);
    assert.notEqual(document.activeElement, opener);
    // A still is not in the top layer and a choice closes nothing.
    const still = tree.build(kit.menu({still: true, label: "Message", items: [{label: "Reply"}]}));
    dom.body.append(still);
    assert.equal(still.hasAttribute("popover"), false);
    dom.click(still.querySelector("button"));
    still.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true}));

    // A dialog is the platform's modal <dialog>; its first button takes focus.
    const dialog = tree.build(
        kit.dialog({
            modal: true,
            title: "Discard the changes",
            body: "This cannot be undone.",
            buttons: [{label: "Cancel"}, {label: "Discard", kind: "danger"}],
        }),
    );
    dom.body.append(dialog, tree.build(kit.text_control({label: "Discard…"})));
    const discard = dom.body.lastElementChild;
    discard.focus();
    keys.open_raised(dialog);
    assert.ok(dialog.hasAttribute("open"));
    assert.equal(label_of(document.activeElement), "Cancel");
    keys.close_raised(dialog);
    assert.equal(document.activeElement, discard);
    // Opened from nothing, it gives focus to nothing.
    document.activeElement.blur();
    keys.open_raised(dialog, null);
    keys.close_raised(dialog);
    // A sheet with no control in it opens all the same.
    const sheet = tree.build(kit.sheet({label: "Checklist", children: ["Nothing yet"]}));
    dom.body.append(sheet);
    keys.open_raised(sheet);
    assert.ok(shown.has(sheet));
    sheet.dispatchEvent(new dom.window.Event("toggle"));
    keys.close_raised(sheet);
});

run_test("a palette keeps focus in its input and moves its highlighted row", () => {
    const dom = make_real_dom();
    const pressed = [];
    const palette = tree.build(
        kit.palette({
            still: true,
            label: "Jump to",
            input: tree.n("input", {"aria-label": "Jump to", role: "combobox"}),
            items: [
                {label: "option-groups", id: "row-1", highlighted: true},
                {label: "Disabled", id: "row-2", disabled: true},
                {label: "Stop the run", id: "row-3", on_press: () => pressed.push("stop")},
            ],
        }),
    );
    dom.body.append(palette);
    const input = palette.querySelector("input");
    // No row is a Tab stop.
    assert.equal(palette.querySelectorAll("[role=option][tabindex='-1']").length, 3);
    input.focus();
    assert.equal(dom.press("ArrowDown"), false);
    assert.equal(document.activeElement, input);
    assert.equal(label_of(palette.querySelector("[data-highlighted]")), "Stop");
    assert.equal(input.getAttribute("aria-activedescendant"), "row-3");
    // Home and End move the caret; other keys type.
    assert.equal(dom.press("Home"), true);
    assert.equal(dom.press("x"), true);
    assert.equal(dom.press("Enter"), false);
    assert.deepEqual(pressed, ["stop"]);
    dom.press("ArrowDown");
    assert.equal(label_of(palette.querySelector("[data-highlighted]")), "option-groups");
    // With nothing highlighted, Return is the input's.
    palette.querySelector("[data-highlighted]").removeAttribute("data-highlighted");
    assert.equal(dom.press("Enter"), true);
    palette.dispatchEvent(new dom.window.Event("keydown"));
    // A row with no id names nothing.
    const plain = tree.build(
        kit.palette({still: true, label: "Jump to", input: "opt", items: [{label: "a"}]}),
    );
    dom.body.append(plain);
    plain.firstElementChild.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", {key: "ArrowDown", bubbles: true}),
    );
    assert.equal(label_of(plain.querySelector("[data-highlighted]")), "a");
});

run_test("what the feed keeps is an HTML element", () => {
    make_real_dom();
    assert.throws(() => tree.build_html(tree.n("svg")), /<svg> is not an HTML element/);
});
