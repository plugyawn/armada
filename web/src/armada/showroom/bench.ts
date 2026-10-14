// The showroom's bench: every piece of the motion kit and every sound,
// under a control. `arm check motion` drives these by their data-armada
// names; a person presses them. Each handler follows the kit's own rule:
// read before the first write, write, then release the difference in the
// after-layout phase.

import {close_raised, open_raised} from "../kit/keys.ts";
import * as kit from "../kit/recipes.ts";
import {build, build_html, n} from "../kit/tree.ts";
import type {Tree} from "../kit/tree.ts";
import * as motion from "../motion/index.ts";
import {recipes, sound_names, table} from "../sound/recipes.ts";
import * as sound from "../sound/sound.ts";

// The words of the bench. The page is for the people who build Armada.
const WORDS = {
    motion: "Motion: every piece of the kit, under a control",
    sound: "Sound: every recipe, the sets, the limiter",
    reduced: "Reduce motion",
    views: "A view's thumb: one fill that travels on move",
    view_names: ["Chat", "README", "Files", "Changes"],
    popover: "A popover: whole in the frame, settling 4 px; it holds words and does not scale",
    open: "Model and effort",
    nav: "A nav block: what is under a repository travels as one opaque piece",
    repo: "click",
    kids: ["option-groups", "demo-live-run", "demo-threads"],
    below: ["design", "devel", "core team"],
    column: "The column: what is opened is whole in the frame and settles 4 px",
    swap: "Open a thread",
    list: "Selection: one thumb a list, on touch under the keys and on move under a press",
    rows: ["Design the API", "Implement the output", "Port the tests", "Update the docs"],
    ring: "Focus: one ring for the window. Tab between neighbours and it travels; beyond 160 px it fades in",
    near: ["Review", "Retry", "Show"],
    far: "Far away",
    figure: "A figure in fixed cells: only the characters that changed move; a clock cuts",
    more: "More",
    fewer: "Fewer",
    tick: "Tick",
    verb: "A word in a fixed cell: the old one goes, then the new one comes",
    verbs: ["Send", "Steer", "Answer", "Queue", "Reply"],
    next: "Next verb",
    cross: "One mark becomes another in place: never a frame with neither",
    finish: "Finish the step",
    react: "My reaction: the chip lands, three copies leave it, the bubble is knocked 3 px, a wash stays",
    said: "so I can drop the shim next week",
    heart: "❤️",
    react_verb: "React",
    grow: "A typing bubble becomes the message: the same element grows, its corners intact",
    arrive: "Arrive",
    message: "then it needs a changelog line",
    send: "Send: my bubble is born where the default button stands and rises to its row; a second send carries it",
    send_verb: "Send",
    sent: ["public", "and keep it optional", "default None"],
    refuse: "A refusal: the field shakes; with motion reduced its edge takes the failure ink",
    refuse_verb: "Send nothing",
    fade: "Leaving: gone to the application at once, seen leaving on the fade",
    leave: "Dismiss",
    undo: "Topic resolved",
    say: "The tooltip, as a short confirmation",
    copy: "Copy",
    copied: "Copied",
    mute: "Mute",
    full: "Full set",
    audition: "Audition, 12 dB over the shipped level",
    storm: "Five at once: the limiter",
    exchange: "A fast exchange: three sends, then done-short",
    levels: ["Sound", "Set", "Tier", "Loudest 100 ms", "Peak", "Length", "Mine"],
    log: ["Sound", "At", "Played", "Why not"],
    state: "Engine",
    yes: "yes",
    no: "no",
    ms: "ms",
    dbfs: "dBFS",
    keys: "Keys: a group is one Tab stop and the arrows move within it; a menu, the picker and a dialog open in the top layer, and Esc gives focus back to what opened them",
    open_menu: "Open a menu",
    open_picker: "Open the picker",
    open_dialog: "Open a dialog",
    menu: "Message",
    menu_items: ["Reply in thread", "Copy text", "Copy link", "Mark unread from here", "Delete"],
    dialog_title: "Discard the changes in demo-live-run",
    dialog_body: "3 files have changes that are not committed. This cannot be undone.",
    cancel: "Cancel",
    discard: "Discard changes",
};

// The bench's own after-layout phase, as the frame's will be: a resize
// observer's first notice comes after layout and before paint, in the
// frame the write lands in, so a released difference is never painted
// without its transform.
function after_layout(run: () => void): void {
    const observer = new ResizeObserver(() => {
        observer.disconnect();
        motion.after_layout(run);
    });
    observer.observe(document.body);
}

const element = build_html;

function item(name: string, caption: string, ...body: (HTMLElement | Tree)[]): HTMLElement {
    const host = element(
        n("div", {class: "armada-showroom-bench-item", "data-armada": `bench-${name}`}, [
            n("span", {class: "armada-showroom-caption"}, [caption]),
        ]),
    );
    host.append(...body.map((part) => (part instanceof HTMLElement ? part : element(part))));
    return host;
}

function thumb(): HTMLElement {
    return element(n("i", {class: "armada-thumb", "data-off": true, "aria-hidden": "true"}));
}

// ---- selection that travels

function views_bench(): HTMLElement {
    const marker = thumb();
    let current = 0;
    const strip = element(
        kit.views({
            label: WORDS.open,
            views: WORDS.view_names.map((label, index) => ({
                label,
                selected: index === 0,
                on_press(event) {
                    const target = event.currentTarget;
                    if (!(target instanceof HTMLElement) || index === current) {
                        return;
                    }
                    // Read the target before the first write; then the
                    // selection is whole in the frame of the press.
                    motion.thumb(marker, target, "move");
                    strip.children[current + 1]?.removeAttribute("aria-current");
                    target.setAttribute("aria-current", "page");
                    current = index;
                },
            })),
        }),
    );
    strip.dataset["thumb"] = "";
    strip.prepend(marker);
    after_layout(() => {
        const first = strip.children[1];
        if (first instanceof HTMLElement) {
            motion.thumb(marker, first);
        }
    });
    return item("views", WORDS.views, strip);
}

function list_bench(): HTMLElement {
    const marker = thumb();
    let current = -1;
    const rows = WORDS.rows.map((label) => element(kit.nav_row({label})));
    const list = element(n("div", {class: "armada-showroom-list armada-nav", "data-thumb": true}));
    const select = (index: number, token: "touch" | "move"): void => {
        const row = rows[index];
        if (row === undefined || index === current) {
            return;
        }
        motion.thumb(marker, row, token);
        rows[current]?.removeAttribute("aria-current");
        row.setAttribute("aria-current", "true");
        current = index;
        row.focus({preventScroll: true});
    };
    for (const [index, row] of rows.entries()) {
        row.addEventListener("pointerdown", () => {
            select(index, "move");
        });
        row.addEventListener("keydown", (event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                select(index + (event.key === "ArrowDown" ? 1 : -1), "touch");
            }
        });
    }
    list.append(marker, ...rows);
    return item("list", WORDS.list, list);
}

// ---- things that open because I acted

function popover_bench(agent: string): HTMLElement {
    const popover = element(
        kit.picker({
            // The bench shows the motion in place; the keys bench opens one
            // in the top layer.
            still: true,
            label: WORDS.open,
            models: [
                {label: "Sol", caption: "gpt-5.6-sol", current: false},
                {label: "Sol Fast", caption: "gpt-5.6-sol, ultrafast", current: true},
            ],
            efforts: {
                label: WORDS.open,
                steps: ["Low", "Medium", "High"].map((label) => ({
                    label,
                    selected: label === "High",
                })),
            },
            caption: agent,
        }),
    );
    popover.hidden = true;
    const toggle = element(
        kit.text_control({
            label: WORDS.open,
            on_press() {
                const opening = popover.hidden || popover.inert;
                if (opening) {
                    popover.hidden = false;
                    popover.inert = false;
                    motion.fade(popover, 1);
                    motion.release(popover, {y: motion.amounts["pane-rise"]}, "move");
                } else {
                    // Gone to the application at once; seen leaving.
                    popover.inert = true;
                    motion.fade(popover, 0);
                }
            },
        }),
    );
    return item("popover", WORDS.popover, toggle, popover);
}

function nav_bench(): HTMLElement {
    const kids = element(
        n(
            "div",
            {class: "armada-nav-kids"},
            WORDS.kids.map((label) => kit.nav_row({label, nested: true})),
        ),
    );
    const block = element(
        n(
            "div",
            {class: "armada-nav-kids", "data-armada": "bench-nav-block"},
            WORDS.below.map((label) => kit.nav_row({label, glyph: "hash"})),
        ),
    );
    const repository = element(
        kit.nav_row({
            label: WORDS.repo,
            glyph: "repo-open",
            repository: {expanded: true},
            on_press() {
                const before = motion.rest(block);
                kids.hidden = !kids.hidden;
                repository.setAttribute("aria-expanded", String(!kids.hidden));
                after_layout(() => {
                    motion.flip(block, before, "move");
                });
            },
        }),
    );
    const box = element(n("div", {class: "armada-showroom-navbox"}));
    box.append(repository, kids, block);
    return item("nav", WORDS.nav, box);
}

function column_bench(): HTMLElement {
    const pane = element(n("div", {"data-armada": "bench-column-pane"}));
    const panes = [
        (): Tree =>
            kit.checklist({
                label: WORDS.open,
                steps: WORDS.rows.map((title, index) => ({
                    kind: "step",
                    state: index === 0 ? "done" : index === 1 ? "live" : "waiting",
                    title,
                })),
            }),
        (): Tree =>
            kit.lane([
                kit.turn({
                    side: "theirs",
                    stone: "rose",
                    name: WORDS.repo,
                    children: [kit.bubble({content: [WORDS.message]})],
                }),
            ]),
    ];
    let shown = 0;
    pane.append(element(panes[0]!()));
    const swap = element(
        kit.text_control({
            label: WORDS.swap,
            on_press() {
                shown = 1 - shown;
                pane.replaceChildren(element(panes[shown]!()));
                motion.release(pane, {y: motion.amounts["pane-rise"]}, "touch");
            },
        }),
    );
    return item("column", WORDS.column, swap, pane);
}

function ring_bench(): HTMLElement {
    const near = element(
        n(
            "div",
            {class: "armada-showroom-row"},
            WORDS.near.map((label) => kit.text_control({label})),
        ),
    );
    const far = element(
        n("div", {style: "margin-top:calc(var(--px) * 200)"}, [
            kit.text_control({label: WORDS.far}),
        ]),
    );
    return item("ring", WORDS.ring, near, far);
}

// ---- values that change in place

function figure_bench(): HTMLElement {
    let count = 9;
    let seconds = 58;
    const counter = element(kit.figure(count));
    const clock = element(kit.figure("0:58"));
    const change = (by: number) => (): void => {
        count = Math.max(0, count + by);
        motion.figure(counter, count);
    };
    return item(
        "figure",
        WORDS.figure,
        n("div", {class: "armada-showroom-row"}, []),
        counter,
        element(kit.text_control({label: WORDS.more, on_press: change(1)})),
        element(kit.text_control({label: WORDS.fewer, on_press: change(-1)})),
        clock,
        element(
            kit.text_control({
                label: WORDS.tick,
                on_press() {
                    seconds += 1;
                    motion.figure(
                        clock,
                        `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`,
                        {cut: true},
                    );
                },
            }),
        ),
    );
}

function verb_bench(): HTMLElement {
    let index = 0;
    const send = element(
        kit.send_button({verb: WORDS.verbs[0]!, verbs: WORDS.verbs, state: "armed"}),
    );
    const next = element(
        kit.text_control({
            label: WORDS.next,
            on_press() {
                index = (index + 1) % WORDS.verbs.length;
                const cell = send.querySelector(".armada-cell");
                const word = document.createElement("span");
                word.textContent = WORDS.verbs[index]!;
                if (cell !== null) {
                    motion.swap(cell, word, {sequence: true, quiet: true});
                }
                send.setAttribute("aria-label", WORDS.verbs[index]!);
            },
        }),
    );
    return item("verb", WORDS.verb, send, next);
}

function cross_bench(): HTMLElement {
    let mark: Element = build(kit.mark("live"));
    let done = false;
    const host = element(n("div", {class: "armada-showroom-row"}));
    host.append(mark);
    const finish = element(
        kit.text_control({
            label: WORDS.finish,
            on_press() {
                done = !done;
                const next = build(kit.mark(done ? "done" : "live"));
                if (
                    (mark instanceof HTMLElement || mark instanceof SVGElement) &&
                    next instanceof SVGElement
                ) {
                    mark = motion.cross(mark, next);
                    if (done) {
                        motion.land(mark);
                    }
                }
            },
        }),
    );
    return item("cross", WORDS.cross, host, finish);
}

// ---- the conversation's choreography

function react_bench(): HTMLElement {
    const row = element(kit.bubble({content: [WORDS.said], time: "11:44"}));
    const stage = element(n("div", {class: "armada-showroom-stage"}, [kit.lane([])]));
    const turn = element(
        kit.turn({
            side: "theirs",
            stone: "rose",
            face: {initials: "Ia", stone: "rose"},
            children: [],
        }),
    );
    turn.append(row);
    stage.firstElementChild?.append(turn);
    const bubble = row.firstElementChild;
    const react = element(
        kit.text_control({
            label: WORDS.react_verb,
            on_press() {
                if (!(bubble instanceof HTMLElement)) {
                    return;
                }
                const old = row.querySelector(".armada-reactions");
                if (old instanceof HTMLElement) {
                    // Pressing a chip of mine takes it back.
                    motion.leave(old);
                    bubble.removeAttribute("data-liked");
                    return;
                }
                const chips = element(
                    kit.reactions([{emoji: WORDS.heart, label: WORDS.react_verb, mine: true}]),
                );
                bubble.after(chips);
                bubble.dataset["liked"] = "";
                sound.play("react");
                after_layout(() => {
                    const chip = chips.firstElementChild;
                    if (chip === null) {
                        return;
                    }
                    motion.land(chip);
                    motion.burst(chip, () => document.createTextNode(WORDS.heart));
                    motion.kick(bubble, {x: -180});
                    motion.squash(bubble);
                });
            },
        }),
    );
    return item("react", WORDS.react, stage, react);
}

function grow_bench(): HTMLElement {
    const stage = element(n("div", {class: "armada-showroom-stage"}, [kit.lane([])]));
    const lane = stage.firstElementChild;
    const reset = (): HTMLElement => {
        const typing = element(
            n("p", {class: "armada-typing-bubble", "data-stone": "rose"}, [kit.typing_dots()]),
        );
        lane?.replaceChildren(typing);
        return typing;
    };
    let typing = reset();
    const arrive = element(
        kit.text_control({
            label: WORDS.arrive,
            on_press() {
                if (!typing.isConnected) {
                    typing = reset();
                    return;
                }
                // The element that showed the dots is the bubble.
                const before = motion.rest(typing);
                const turn = element(
                    kit.turn({
                        side: "theirs",
                        stone: "rose",
                        children: [kit.bubble({content: [WORDS.message]})],
                    }),
                );
                lane?.replaceChildren(turn);
                typing.remove();
                sound.play("arrive");
                after_layout(() => {
                    const bubble = turn.querySelector(".armada-bubble");
                    if (bubble instanceof HTMLElement) {
                        motion.grow(bubble, before);
                        motion.appear(bubble, 0.6);
                    }
                });
            },
        }),
    );
    return item("grow", WORDS.grow, stage, arrive);
}

function send_bench(agent: string): HTMLElement {
    const stage = element(n("div", {class: "armada-showroom-stage"}, [kit.lane([])]));
    const lane = stage.firstElementChild;
    let sent = 0;
    const field = element(
        kit.field({
            label: WORDS.send_verb,
            placeholder: agent,
            value: WORDS.sent[0],
            send: {verb: WORDS.send_verb, verbs: WORDS.verbs},
            flat: true,
        }),
    );
    const button = field.querySelector(".armada-send");
    const input = field.querySelector("textarea");
    // The default button's cell is one rectangle in every state: it is read
    // once, while layout is clean, and never in the send task.
    let origin: motion.Box | undefined;
    after_layout(() => {
        if (button !== null) {
            origin = motion.rest(button);
        }
    });
    const send = (): void => {
        if (lane === null || input === null || origin === undefined) {
            return;
        }
        if (input.value === "") {
            motion.refuse(field);
            sound.play("nope");
            return;
        }
        // Read while layout is clean; then, in the task of the input, my
        // bubble whole and the field empty.
        const {height} = motion.rest(lane);
        const turn = element(
            kit.turn({
                side: "mine",
                children: [kit.bubble({content: [input.value], time: "11:36"})],
            }),
        );
        lane.append(turn);
        sent += 1;
        input.value = WORDS.sent[sent % WORDS.sent.length]!;
        sound.play("send");
        const from = origin;
        after_layout(() => {
            const bubble = turn.querySelector(".armada-bubble");
            if (!(bubble instanceof HTMLElement)) {
                return;
            }
            const rise = motion.rest(lane).height - height;
            // The feed advances a row, and what is already flying is
            // carried with it.
            motion.carry(rise);
            motion.release(lane, {y: rise}, "touch", {clamp: {y: 80}});
            motion.fly(bubble, from, "move");
        });
    };
    button?.addEventListener("click", send);
    input?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            send();
        }
    });
    return item("send", WORDS.send, stage, field);
}

function refuse_bench(agent: string): HTMLElement {
    const field = element(
        kit.field({
            label: WORDS.refuse_verb,
            placeholder: agent,
            send: {verb: WORDS.send_verb, verbs: WORDS.verbs},
            flat: true,
        }),
    );
    const shape = field.querySelector(".armada-field-shape");
    const refuse = element(
        kit.text_control({
            label: WORDS.refuse_verb,
            on_press() {
                if (shape !== null) {
                    motion.refuse(shape);
                    sound.play("nope");
                }
            },
        }),
    );
    return item("refuse", WORDS.refuse, field, refuse);
}

function leave_bench(): HTMLElement {
    const host = element(n("div", {class: "armada-showroom-row"}));
    const notice = (): HTMLElement => element(kit.band({notice: {text: WORDS.undo}}));
    let shown = notice();
    host.append(shown);
    const leave = element(
        kit.text_control({
            label: WORDS.leave,
            on_press() {
                if (shown.isConnected && !shown.inert) {
                    motion.leave(shown);
                    return;
                }
                shown = notice();
                host.replaceChildren(shown);
                // What I did not ask for fades in; held a step first.
                motion.hold(shown);
                motion.appear(shown, 0, undefined, {wait: motion.times.step});
            },
        }),
    );
    return item("leave", WORDS.fade, host, leave);
}

function say_bench(): HTMLElement {
    const copy = element(
        kit.glyph_control({
            glyph: "copy",
            label: WORDS.copy,
            tip: WORDS.copy,
            key_hint: "⌘C",
            on_press(event) {
                if (event.currentTarget instanceof HTMLElement) {
                    motion.say(event.currentTarget, WORDS.copied);
                }
            },
        }),
    );
    return item("say", WORDS.say, copy);
}

// ---- the keyboard

function keys_bench(agent: string): HTMLElement {
    const menu = element(
        kit.menu({label: WORDS.menu, items: WORDS.menu_items.map((label) => ({label}))}),
    );
    const picker = element(
        kit.picker({
            label: WORDS.open,
            models: [
                {label: "Sol", caption: "gpt-5.6-sol", current: false},
                {label: "Sol Fast", caption: "gpt-5.6-sol, ultrafast", current: true},
                {label: "Mini", caption: "gpt-5.6-mini", current: false},
            ],
            efforts: {
                label: WORDS.open,
                steps: ["Low", "Medium", "High", "XHigh"].map((label) => ({
                    label,
                    selected: label === "High",
                })),
            },
            caption: agent,
        }),
    );
    const close = (): void => {
        close_raised(dialog);
    };
    const dialog = element(
        kit.dialog({
            modal: true,
            title: WORDS.dialog_title,
            body: WORDS.dialog_body,
            buttons: [
                {label: WORDS.cancel, on_press: close},
                {label: WORDS.discard, kind: "danger", on_press: close},
            ],
        }),
    );
    const opener = (label: string, surface: HTMLElement): Tree =>
        kit.text_control({
            label,
            on_press(event) {
                open_raised(
                    surface,
                    event.currentTarget instanceof Element ? event.currentTarget : null,
                );
            },
        });
    return item(
        "keys",
        WORDS.keys,
        n("div", {class: "armada-showroom-row"}, [
            opener(WORDS.open_menu, menu),
            opener(WORDS.open_picker, picker),
            opener(WORDS.open_dialog, dialog),
        ]),
        menu,
        picker,
        dialog,
    );
}

export function motion_bench(agent: string): HTMLElement {
    const reduce = element(
        kit.toggle({
            label: WORDS.reduced,
            on: false,
            on_press(event) {
                const root = document.documentElement;
                const reduced = root.dataset["motion"] !== "reduced";
                if (reduced) {
                    root.dataset["motion"] = "reduced";
                } else {
                    delete root.dataset["motion"];
                }
                if (event.currentTarget instanceof HTMLElement) {
                    event.currentTarget.setAttribute("aria-checked", String(reduced));
                }
                motion.load();
            },
        }),
    );
    reduce.dataset["armada"] = "bench-reduce";
    const bench = element(n("div", {class: "armada-showroom-bench"}));
    bench.append(
        views_bench(),
        list_bench(),
        popover_bench(agent),
        nav_bench(),
        column_bench(),
        ring_bench(),
        figure_bench(),
        verb_bench(),
        cross_bench(),
        react_bench(),
        grow_bench(),
        send_bench(agent),
        refuse_bench(agent),
        leave_bench(),
        say_bench(),
        keys_bench(agent),
    );
    const section = element(
        n("section", {"data-armada": "showroom-motion"}, [
            n("h2", {class: "armada-showroom-section"}, [WORDS.motion]),
            n("div", {class: "armada-showroom-row"}, [
                n("span", {class: "armada-showroom-caption"}, [WORDS.reduced]),
            ]),
        ]),
    );
    section.lastElementChild?.prepend(reduce);
    section.append(bench);
    return section;
}

// ---- sound

function levels(): Tree {
    return n("table", {class: "armada-showroom-table", "data-armada": "sound-levels"}, [
        n("thead", {}, [
            n(
                "tr",
                {},
                WORDS.levels.map((label) => n("th", {}, [label])),
            ),
        ]),
        n(
            "tbody",
            {},
            table().map((row) =>
                n("tr", {"data-sound": row.name}, [
                    n("td", {}, [row.name]),
                    n("td", {}, [row.set]),
                    n("td", {}, [`${row.tier} ${WORDS.dbfs}`]),
                    n("td", {}, [row.rms100.toFixed(2)]),
                    n("td", {}, [row.peak.toFixed(2)]),
                    n("td", {}, [`${row.milliseconds} ${WORDS.ms}`]),
                    n("td", {}, [recipes[row.name].mine ? WORDS.yes : WORDS.no]),
                ]),
            ),
        ),
    ]);
}

export function sound_bench(): HTMLElement {
    const log = element(n("table", {class: "armada-showroom-table", "data-armada": "sound-log"}));
    const engine = element(
        n("span", {class: "armada-showroom-caption", "data-armada": "sound-state"}),
    );
    const report = (): void => {
        engine.textContent = `${WORDS.state}: ${sound.state()}`;
        log.replaceChildren(
            element(
                n("thead", {}, [
                    n(
                        "tr",
                        {},
                        WORDS.log.map((label) => n("th", {}, [label])),
                    ),
                ]),
            ),
            element(
                n(
                    "tbody",
                    {},
                    sound
                        .log()
                        .slice(-12)
                        .map((line) =>
                            n("tr", {}, [
                                n("td", {}, [line.name]),
                                n("td", {}, [line.at]),
                                n("td", {}, [line.played ? WORDS.yes : WORDS.no]),
                                n("td", {}, [line.why]),
                            ]),
                        ),
                ),
            ),
        );
    };
    const switch_ = (
        label: string,
        name: string,
        on: boolean,
        change: (value: boolean) => void,
    ): HTMLElement => {
        const control = element(
            n("div", {class: "armada-showroom-row"}, [
                kit.toggle({
                    label,
                    on,
                    on_press(event) {
                        const target = event.currentTarget;
                        if (target instanceof HTMLElement) {
                            const value = target.getAttribute("aria-checked") !== "true";
                            target.setAttribute("aria-checked", String(value));
                            change(value);
                            report();
                        }
                    },
                }),
                n("span", {class: "armada-showroom-caption"}, [label]),
            ]),
        );
        control.dataset["armada"] = `sound-${name}`;
        return control;
    };
    const each = element(
        n(
            "div",
            {class: "armada-showroom-row"},
            sound_names.map((name) =>
                kit.button({
                    label: name,
                    on_press() {
                        sound.play(name);
                        report();
                    },
                }),
            ),
        ),
    );
    const storm = element(
        kit.text_control({
            label: WORDS.storm,
            on_press() {
                sound.clear();
                for (const name of [
                    "done-short",
                    "done-short",
                    "done",
                    "ask",
                    "mention",
                    "send",
                ] as const) {
                    sound.play(name);
                }
                report();
            },
        }),
    );
    const exchange = element(
        kit.text_control({
            label: WORDS.exchange,
            on_press() {
                sound.clear();
                for (const name of ["send", "send", "send", "done-short"] as const) {
                    sound.play(name);
                }
                report();
            },
        }),
    );
    const section = element(
        n("section", {"data-armada": "showroom-sound"}, [
            n("h2", {class: "armada-showroom-section"}, [WORDS.sound]),
        ]),
    );
    const controls = element(n("div", {class: "armada-showroom-row", "data-gap": "wide"}));
    controls.append(
        switch_(WORDS.mute, "mute", sound.is_muted(), (value) => {
            sound.set_muted(value, {gesture: true});
        }),
        switch_(WORDS.full, "full", false, sound.set_full),
        switch_(WORDS.audition, "audition", false, (value) => {
            sound.set_level(value ? 12 : 0);
        }),
        storm,
        exchange,
        engine,
    );
    section.append(
        controls,
        each,
        element(
            n("div", {class: "armada-showroom-row", "data-align": "start", "data-gap": "wide"}, [
                levels(),
            ]),
        ),
    );
    section.lastElementChild?.append(log);
    report();
    return section;
}
