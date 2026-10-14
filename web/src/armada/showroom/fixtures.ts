// What the showroom shows: the design's component sheet, as descriptions
// built with the kit's own recipes. One fixture is drawn through Preact
// and through the DOM builder, so the two idioms cannot drift. The words
// are the design's specimen text; the page is for the people who build
// Armada and is not translated.

import {glyph_groups, mark_names} from "../kit/icons/names.ts";
import type {MarkName} from "../kit/icons/names.ts";
import * as kit from "../kit/recipes.ts";
import type {FaceProps, FieldProps, HeaderProps, MenuItem, Step} from "../kit/recipes.ts";
import {n} from "../kit/tree.ts";
import type {Child, Force, Tree} from "../kit/tree.ts";

export type Item = {caption?: string; body: Tree; fill?: boolean};
export type Section = {
    id: string;
    title: string;
    columns: 1 | 2 | 3 | 4;
    gap?: "tight" | "lists";
    items: Item[];
    note?: string;
};

const box = (
    name: string,
    children: Child[],
    attributes: Record<string, string | boolean> = {},
): Tree => n("div", {class: `armada-showroom-${name}`, ...attributes}, children);
const row = (children: Child[], attributes: Record<string, string | boolean> = {}): Tree =>
    box("row", children, attributes);
const stack = (children: Child[], attributes: Record<string, string | boolean> = {}): Tree =>
    box("stack", children, attributes);
const cap = (text: string): Tree => n("span", {class: "armada-showroom-caption"}, [text]);
const group = (text: string): Tree => n("h3", {class: "armada-showroom-group"}, [text]);
const stage = (children: Child[], wide = false): Tree =>
    box("stage", [kit.lane(children)], {"data-wide": wide});
const p = (...children: Child[]): Tree => n("p", {}, children);
const c = (text: string): Tree => n("code", {}, [text]);
const em = (text: string): Tree => n("em", {}, [text]);
const strong = (text: string): Tree => n("strong", {}, [text]);
const labelled = (label: string, body: Tree): Tree =>
    stack([body, cap(label)], {"data-align": "center"});

const hamlet: FaceProps = {initials: "KH", stone: "lapis"};
const iago: FaceProps = {initials: "Ia", stone: "rose"};
const cordelia: FaceProps = {initials: "Co", stone: "larimar"};
const othello: FaceProps = {initials: "Ot", stone: "amethyst"};
const me: FaceProps = {initials: "De", stone: "mine"};

// A duration as the interface writes it: seconds, then minutes and seconds.
const lasted = (seconds: number): string =>
    seconds < 60
        ? `${seconds}s`
        : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
const VERBS = ["Send", "Steer", "Answer", "Queue", "Reply"];

const theirs = (who: FaceProps, name: string | undefined, children: Child[]): Tree =>
    kit.turn({
        side: "theirs",
        stone: who.stone === "mine" ? undefined : who.stone,
        face: who,
        name,
        children,
    });
const mine = (children: Child[]): Tree => kit.turn({side: "mine", children});

function type_section(agent: string): Section {
    const style = (name: string, sample: string, says: string, ink2 = false): Child[] => [
        n("span", {class: `armada-type-${name}${ink2 ? " armada-ink-2" : ""}`}, [sample]),
        cap(says),
    ];
    return {
        id: "type",
        title: "Type",
        columns: 2,
        items: [
            {
                body: box("spec", [
                    style(
                        "display",
                        "click",
                        "Display: Söhne Halbfett 600, 28 / 32. A document's h1 and nothing in chrome",
                    ),
                    style(
                        "title",
                        "option-groups",
                        "Title: Söhne Halbfett 600, 20 / 28. The pane's one name; a document's h2",
                    ),
                    style(
                        "heading",
                        "What changed",
                        "Heading: Söhne Halbfett 600, 16 / 24. A heading in an answer; a document's h3",
                    ),
                    style(
                        "talk",
                        "does help print groups in declaration order",
                        `Talk: Söhne Buch 400, 16 / 24. What is said: a bubble, ${agent}'s words, the field. And a document's prose, on a 608 px measure`,
                    ),
                    style(
                        "strong",
                        "Review, Retry, Send, Changes, Runs",
                        "Strong: Söhne Kräftig 500, 14 / 20. A verb that is a control; a list's heading; a repository in the nav",
                    ),
                    style(
                        "text",
                        "Print one section per group",
                        "Text: Söhne Buch 400, 14 / 20. Every label, row and fact in chrome; a view's name",
                    ),
                    style(
                        "label",
                        `King Hamlet, ${agent}, Today, New`,
                        "Label: Söhne Kräftig 500, 12 / 16. A name over a turn, a day, initials in a face",
                        true,
                    ),
                    style(
                        "caption",
                        `11:36, ${lasted(125)}, Finished 11:21, 3 replies`,
                        "Caption: Söhne Buch 400, 12 / 16, tabular. Times, durations, counts, receipts",
                        true,
                    ),
                    style(
                        "code",
                        "armada/option-groups 9c04e1b",
                        "Code: Geist Mono 400, 14 / 20. What a machine would accept. 80 columns and two insets are the conversation's 704",
                    ),
                    style(
                        "code-small",
                        "208 209 210",
                        "Line number: Geist Mono 400, 12 / 20. The numbers in a source or a diff gutter",
                        true,
                    ),
                ]),
            },
            {
                body: stack(
                    [
                        cap(
                            `What is said is 16 on a 24 line: a person in a bubble, ${agent} on the plane, code inside each on the line's own height`,
                        ),
                        stage([
                            theirs(hamlet, "King Hamlet", [
                                kit.bubble({
                                    content: [
                                        "People speak in bubbles, with ",
                                        c("code_in_mono"),
                                        " and ",
                                        em("Kursiv"),
                                        " for stress",
                                    ],
                                }),
                            ]),
                            kit.turn({
                                side: "agent",
                                name: agent,
                                children: [
                                    kit.answer([
                                        p(
                                            `${agent} speaks on the plane in the same style, with `,
                                            strong("Kräftig"),
                                            " for stress, ",
                                            em("Kursiv"),
                                            " and ",
                                            c("code_in_mono"),
                                            ".",
                                        ),
                                    ]),
                                ],
                            }),
                        ]),
                        cap(
                            "Numerals that change, or that stand in a column, sit in fixed cells: the band's clock, an unread count, a duration, a table's figures",
                        ),
                        row([
                            kit.figure("0:09"),
                            kit.figure("4:12"),
                            kit.figure("9:59"),
                            n("span", {class: "armada-ink-2"}, ["then"]),
                            n("span", {}, ["12m"]),
                        ]),
                        cap(
                            "Code is coloured in four roles, the same in both themes: keyword, string, number, comment. Red and green are left to the diff",
                        ),
                        kit.code_block([
                            ["keyword", "def"],
                            " write_dl(self, rows, col_max: int = ",
                            ["number", "30"],
                            ") -> ",
                            ["number", "None"],
                            ":\n    ",
                            ["comment", '"""Writes a definition list into the buffer."""'],
                            "\n    ",
                            ["keyword", "for"],
                            " first, second ",
                            ["keyword", "in"],
                            " iter_rows(rows, ",
                            ["number", "2"],
                            "):   ",
                            ["comment", "# one row a term"],
                            "\n        self.write(",
                            ["string", '"  "'],
                            " * self.current_indent)",
                        ]),
                    ],
                    {"data-gap": "loose", "data-fill": true},
                ),
            },
        ],
    };
}

// The colour roles, by token. `edge` draws an outline so a swatch of the
// plane's own colour can be seen.
export const colour_roles: [string, string][] = [
    ["ground", "window, nav"],
    ["content", "the plane"],
    ["raised", "field, menus, sheets"],
    ["ink-1", "what is read"],
    ["ink-2", "captions, quiet rows"],
    ["dim", "disabled only"],
    ["fill-1", "hover"],
    ["fill-2", "a waiting draft"],
    ["fill-3", "selected"],
    ["fill-press", "pressed"],
    ["block", "code, diff, table, artifact"],
    ["bubble", "everyone else, no stone"],
    ["bubble-me", "me"],
    ["accent", "default button"],
    ["accent-hover", "hover"],
    ["accent-press", "pressed"],
    ["on-accent", "its ink"],
    ["ring", "keyboard focus"],
    ["edge", "the field's edge"],
    ["success", "passed, merged"],
    ["attention", "needs a person"],
    ["failure", "failed"],
    ["tint-add", "diff, added"],
    ["tint-del", "diff, removed"],
    ["edge-ctl", "a checkbox's edge"],
    ["syn-k", "keyword"],
    ["syn-s", "string"],
    ["syn-n", "number"],
    ["syn-c", "comment"],
    ["add-ink", "a plus, a count"],
    ["del-ink", "a minus, a count"],
];

// Text and what it stands on: the pairs whose contrast the page computes.
export const contrast_pairs: [string, string, number][] = [
    ["ink-1", "content", 4.5],
    ["ink-2", "content", 4.5],
    ["ink-2", "ground", 4.5],
    ["ink-2", "raised", 4.5],
    ["ink-2", "block", 4.5],
    ["ink-1", "bubble", 4.5],
    ["on-me", "bubble-me", 4.5],
    ["on-accent", "accent", 4.5],
    ["accent-text", "content", 4.5],
    ["success", "content", 4.5],
    ["attention", "content", 4.5],
    ["failure", "content", 4.5],
    ["syn-k", "block", 4.5],
    ["syn-s", "block", 4.5],
    ["syn-n", "block", 4.5],
    ["syn-c", "block", 4.5],
    ["add-ink", "tint-add", 4.5],
    ["del-ink", "tint-del", 4.5],
    ["edge-ctl", "content", 3],
];

function colour_section(): Section {
    const swatch = (token: string, says: string): Tree =>
        stack([
            n("i", {class: "armada-showroom-swatch", style: `background:var(--${token})`}),
            n("span", {class: "armada-type-text"}, [token]),
            cap(says),
        ]);
    const stone = (who: FaceProps, name: string, fill: string, says: string): Tree =>
        stack([
            kit.face(who),
            n("i", {
                class: "armada-showroom-swatch",
                "data-shape": "bubble",
                style: `background:var(--${fill})`,
            }),
            n("span", {class: "armada-type-text"}, [name]),
            cap(says),
        ]);
    return {
        id: "colour",
        title: "Colour",
        columns: 1,
        items: [
            {
                body: row(
                    colour_roles.map(([token, says]) => swatch(token, says)),
                    {"data-gap": "wide", "data-align": "start"},
                ),
            },
            {
                body: row(
                    [
                        stone(
                            {initials: "Mo", stone: "moss"},
                            "moss",
                            "b-moss",
                            "a face, a bubble",
                        ),
                        stone(
                            {initials: "La", stone: "larimar"},
                            "larimar",
                            "b-larimar",
                            "a face, a bubble",
                        ),
                        stone(
                            {initials: "La", stone: "lapis"},
                            "lapis",
                            "b-lapis",
                            "a face, a bubble",
                        ),
                        stone(
                            {initials: "Am", stone: "amethyst"},
                            "amethyst",
                            "b-amethyst",
                            "a face, a bubble",
                        ),
                        stone(
                            {initials: "Ro", stone: "rose"},
                            "rose",
                            "b-rose",
                            "a face, a bubble",
                        ),
                        stone(
                            {initials: "Fl", stone: "flint"},
                            "flint",
                            "b-flint",
                            "a face, a bubble",
                        ),
                        stone(me, "me", "bubble-me", "my face, my bubble"),
                        cap(
                            "Six stones, assigned by user id, the same wherever that person shows: a face is the stone, a bubble is stone with a hint of it. A photograph replaces the face. My colour is the palette's one hue. The hues that mean news are three: passed, needs you, failed.",
                        ),
                    ],
                    {"data-gap": "wide", "data-align": "start"},
                ),
            },
        ],
    };
}

function space_section(): Section {
    const shape = (
        width: number,
        height: number,
        radius: number | undefined,
        label: string,
    ): Tree =>
        labelled(
            label,
            n("i", {
                class: "armada-showroom-shape",
                style: `width:calc(var(--px) * ${width});height:calc(var(--px) * ${height})${radius === undefined ? "" : `;border-radius:calc(var(--px) * ${radius})`}`,
            }),
        );
    return {
        id: "space",
        title: "Space, radius, height",
        columns: 3,
        items: [
            {
                caption:
                    "Unit 4. In the conversation: 4 inside one turn, 8 words to a block, 12 paragraphs, 16 turn to turn, 32 a day. In lists: 20 between groups and sections",
                body: row(
                    [4, 8, 12, 16, 20, 24, 32, 40].map((size) =>
                        shape(size, size, undefined, String(size)),
                    ),
                    {"data-align": "end"},
                ),
            },
            {
                caption:
                    "4 mark, 6 control and row, 10 block, menu and a reaction, 14 popover and sheet, 18 bubble and dialog, 22 the field (its controls are 16: the field less its 6 px inset)",
                body: row(
                    [4, 6, 10, 14, 18, 22].map((radius) => shape(48, 36, radius, String(radius))),
                ),
            },
            {
                caption:
                    "20 reaction, 24 small control, 28 row and control, 32 a control in the field, 36 one-line bubble and the band, 44 the field, 52 and 36 the header's lines",
                body: row(
                    [20, 24, 28, 32, 36, 44, 52].map((height) =>
                        shape(28, height, 6, String(height)),
                    ),
                    {"data-align": "end"},
                ),
            },
        ],
    };
}

const VIEW_NAMES = ["Chat", "README", "Files", "Changes"];

function worktree_header(
    agent: string,
    options: {view: string; steps: MarkName[]; caption: string} & Partial<HeaderProps>,
): HeaderProps {
    const {view, steps, caption, ...rest} = options;
    return {
        path: {label: "pallets/click"},
        title: "option-groups",
        branch: {label: "Branch", distance: "3 ahead of main", distance_sizer: "88 ahead of main"},
        commit: {
            label: "Latest commit",
            by: agent,
            summary: "Add the group keyword to option",
            sha: "9c04e1b",
            age: "17m",
            age_sizer: "88m",
        },
        views: {
            label: "Views",
            views: VIEW_NAMES.map((label) => ({
                label,
                selected: label === view,
                count: label === "Changes" ? 3 : undefined,
            })),
        },
        more: {label: "More"},
        heading: {
            word: "Checklist",
            marks: steps,
            caption: {text: caption, sizers: ["Finished 00:00", "12 of 12"]},
            expanded: false,
        },
        ...rest,
    };
}

function header_section(agent: string): Section {
    const head = (props: HeaderProps): Tree => box("header", [kit.header(props)]);
    return {
        id: "header",
        title: "The header: two lines, the same bounds on every place",
        columns: 2,
        gap: "lists",
        items: [
            {
                caption:
                    "A worktree: path, the worktree as the one Title, the branch as its distance from base, and at the far end the latest commit: who, summary, sha, age. Line 2: the four views, the checklist's standing home, More",
                body: head(
                    worktree_header(agent, {
                        view: "Chat",
                        steps: ["done", "done", "done"],
                        caption: "Finished 11:21",
                    }),
                ),
            },
            {
                caption:
                    "The branch's name is printed when it is not the worktree's own. A pull request joins the facts once it exists",
                body: head(
                    worktree_header(agent, {
                        view: "Changes",
                        steps: ["done", "done", "done"],
                        caption: "Finished 11:21",
                        branch: {
                            label: "Branch",
                            name: "feature/groups",
                            distance: "3 ahead of main",
                            distance_sizer: "88 ahead of main",
                        },
                        pull: {label: "Pull request", number: "2841", state: "Open"},
                    }),
                ),
            },
            {
                caption:
                    "Detached or in conflict: the needs-you diamond and its colour, on the branch fact",
                body: head(
                    worktree_header(agent, {
                        view: "Chat",
                        steps: ["done", "live", "waiting", "waiting"],
                        caption: "1 of 4",
                        branch: {
                            label: "Branch",
                            distance: "conflicts with main",
                            distance_sizer: "conflicts with main",
                            attention: true,
                        },
                        commit: {
                            label: "Latest commit",
                            by: "King Hamlet",
                            summary: "Add the group keyword to option",
                            sha: "9c04e1b",
                            age: "2h",
                            age_sizer: "88m",
                        },
                    }),
                ),
            },
            {
                caption:
                    "A channel with no repository, a direct message, Home: the same two lines. A sentence stands where views would",
                body: head({
                    path: {label: "devel"},
                    title: "release 8.2",
                    say: "Development of click and the tools round it.",
                    more: {label: "More"},
                }),
            },
        ],
    };
}

function controls_section(): Section {
    const send = (verb: string, state?: "armed" | "waiting", force?: Force): Tree =>
        kit.send_button({verb, verbs: VERBS, state, force});
    const glyph_send = (
        verb: string,
        glyph: "send" | "steer" | "answer" | "queue" | "thread",
        state?: "armed",
    ): Tree => kit.send_button({verb, verbs: VERBS, form: "glyph", glyph, state});
    const accent = (label: string, force?: Force, disabled = false): Tree =>
        kit.accent_button({label, force, disabled});
    const more = (extra: {force?: Force; pressed?: boolean; disabled?: boolean} = {}): Tree =>
        kit.glyph_control({glyph: "more", label: "More", ...extra});
    const view = (
        label: string,
        extra: {selected?: boolean; force?: Force; count?: number} = {},
    ): {label: string; selected: boolean; force?: Force; count?: number} => ({
        label,
        selected: false,
        ...extra,
    });
    const home = (
        steps: MarkName[],
        caption: string,
        extra: {force?: Force; expanded?: boolean} = {},
    ): Tree =>
        kit.heading({
            word: "Checklist",
            marks: steps,
            caption: {text: caption, sizers: ["Finished 00:00", "12 of 12"]},
            expanded: false,
            ...extra,
        });
    return {
        id: "controls",
        title: "Controls and states",
        columns: 3,
        items: [
            {
                body: stack(
                    [
                        cap(
                            "The default button, where there is a field: a word in a small bubble of mine. Idle, armed, hover, pressed, focus, and a draft that is waiting",
                        ),
                        row([
                            send("Send"),
                            send("Send", "armed"),
                            send("Send", "armed", "hover"),
                            send("Send", "armed", "press"),
                            send("Send", "armed", "focus"),
                            send("Send", "waiting"),
                        ]),
                        cap(
                            "Its word is the verb Return performs now, in a cell as wide as the longest: Send, Steer, Answer, Queue while ⌘ is held, Reply in a thread",
                        ),
                        row(VERBS.map((verb) => send(verb, "armed"))),
                        cap(
                            "The same button as a glyph, should the design choose it: one 32 px cell, five glyphs",
                        ),
                        row([
                            glyph_send("Send", "send"),
                            glyph_send("Send", "send", "armed"),
                            glyph_send("Steer", "steer", "armed"),
                            glyph_send("Answer", "answer", "armed"),
                            glyph_send("Queue", "queue", "armed"),
                            glyph_send("Reply", "thread", "armed"),
                        ]),
                        cap(
                            "The default button, where there is no field: a word on the accent. Rest, hover, pressed, focus, nothing to do",
                        ),
                        row([
                            accent("Answer"),
                            accent("Answer", "hover"),
                            accent("Answer", "press"),
                            accent("Commit", "focus"),
                            accent("Commit", undefined, true),
                        ]),
                        cap("Button, in dialogs: rest, hover, pressed, disabled, destructive"),
                        row([
                            kit.button({label: "Cancel"}),
                            kit.button({label: "Cancel", force: "hover"}),
                            kit.button({label: "Cancel", force: "press"}),
                            kit.button({label: "Cancel", disabled: true}),
                            kit.button({label: "Discard changes", kind: "danger"}),
                        ]),
                    ],
                    {"data-gap": "loose"},
                ),
            },
            {
                body: stack(
                    [
                        cap(
                            "Text control: a verb in Strong, always. Rest, hover, pressed, focus, on, disabled",
                        ),
                        row(
                            [
                                kit.text_control({label: "Review"}),
                                kit.text_control({label: "Review", force: "hover"}),
                                kit.text_control({label: "Retry", force: "press"}),
                                kit.text_control({label: "Open", force: "focus"}),
                                kit.text_control({label: "2 replies", pressed: true}),
                                kit.text_control({label: "Show", disabled: true}),
                            ],
                            {"data-gap": "wide"},
                        ),
                        cap("Glyph control: rest, hover, pressed, on, focus, disabled"),
                        row([
                            more(),
                            more({force: "hover"}),
                            more({force: "press"}),
                            more({pressed: true}),
                            more({force: "focus"}),
                            more({disabled: true}),
                        ]),
                        cap("Stop and Jump to latest: a glyph and a word on a resting fill"),
                        row([
                            kit.stop_control({label: "Stop", glyph: "stop"}),
                            kit.stop_control({label: "Stop", glyph: "stop", force: "hover"}),
                            kit.stop_control({
                                label: "Stop Iago's run",
                                glyph: "stop",
                                force: "press",
                            }),
                            kit.stop_control({label: "Jump to latest", glyph: "latest"}),
                        ]),
                        cap(
                            "Switch: off, on. Checkbox: off (an edge at 3:1), on. A question's options are buttons: rest, hover, chosen",
                        ),
                        row([
                            kit.toggle({label: "Sound", on: false}),
                            kit.toggle({label: "Sound", on: true}),
                            kit.checkbox({label: "Viewed", checked: false}),
                            kit.checkbox({label: "Viewed", checked: true}),
                        ]),
                        kit.question({
                            label: "Heading for ungrouped options",
                            options: [
                                {label: "Options, as today"},
                                {label: "General options", force: "hover"},
                                {label: "No heading", selected: true},
                            ],
                        }),
                    ],
                    {"data-gap": "loose"},
                ),
            },
            {
                body: stack(
                    [
                        cap(
                            "Views: a selector is Text. Rest, hover, selected, focus; the one count stands against its word",
                        ),
                        row([
                            kit.views({
                                label: "Views",
                                views: [
                                    view("Chat"),
                                    view("README", {force: "hover"}),
                                    view("Files", {selected: true}),
                                    view("Files", {force: "focus"}),
                                    view("Changes", {count: 3}),
                                    view("Changes", {selected: true, count: 12}),
                                ],
                            }),
                        ]),
                        cap("Switch of a pane: Read, Edit, Source; Unified, Split"),
                        row([
                            kit.segments({
                                label: "Document",
                                options: [
                                    {label: "Read", selected: true},
                                    {label: "Edit", selected: false},
                                    {label: "Source", selected: false, force: "hover"},
                                ],
                            }),
                            kit.segments({
                                label: "Diff",
                                options: [
                                    {label: "Unified", selected: true},
                                    {label: "Split", selected: false},
                                ],
                            }),
                        ]),
                        cap(
                            "The checklist's standing home, folded: live, a record, something failed and something waiting, hover, open. The caption counts steps that are done",
                        ),
                        row([
                            home(["done", "live", "waiting", "waiting"], "1 of 4"),
                            home(["done", "done", "done"], "Finished 11:21"),
                            home(["done", "done", "done", "done", "failed", "needs"], "4 of 6"),
                            home(["done", "live", "waiting"], "1 of 3", {force: "hover"}),
                            home(["done", "live", "waiting"], "1 of 3", {expanded: true}),
                        ]),
                        cap(
                            "Keyboard focus: one ring for the window, 1.5 px in the accent, 2 px off the control, on its radius",
                        ),
                        row([
                            kit.views({label: "Views", views: [view("Chat", {force: "focus"})]}),
                            n("div", {style: "width:calc(var(--px) * 160)"}, [
                                kit.nav_row({label: "devel", glyph: "hash", force: "focus"}),
                            ]),
                            kit.text_control({label: "Review", force: "focus"}),
                        ]),
                        cap(
                            "The three state fills are three steps, on a row, a view and a control: hover, pressed, selected",
                        ),
                        row(
                            [
                                n("div", {style: "width:calc(var(--px) * 120)"}, [
                                    kit.nav_row({label: "hover", force: "hover"}),
                                    kit.nav_row({label: "pressed", force: "press"}),
                                    kit.nav_row({label: "selected", current: true}),
                                ]),
                                kit.views({
                                    label: "Views",
                                    views: [
                                        view("hover", {force: "hover"}),
                                        view("pressed", {force: "press"}),
                                        view("selected", {selected: true}),
                                    ],
                                }),
                                row([
                                    more({force: "hover"}),
                                    more({force: "press"}),
                                    more({pressed: true}),
                                ]),
                            ],
                            {"data-align": "start"},
                        ),
                    ],
                    {"data-gap": "loose"},
                ),
            },
        ],
    };
}

export function field_props(
    agent: string,
    extra: Partial<FieldProps> & {verb?: string; mode_text?: string; answers?: boolean} = {},
): FieldProps {
    const {verb = "Send", mode_text = "Sol Fast · High", answers = true, ...rest} = extra;
    return {
        label: "Message",
        placeholder:
            verb === "Steer"
                ? `Steer ${agent}`
                : verb === "Answer"
                  ? `Answer ${agent}`
                  : "Message option-groups",
        send: {verb, verbs: VERBS, key_hint: "Return"},
        plus: {label: "Attach"},
        agent: {
            label: `${agent} answers`,
            answers,
            tip: answers ? `${agent} answers this message` : "For people only",
        },
        mode: {label: "Model and effort", text: mode_text, sizer: "Sol Fast · Medium"},
        ...rest,
    };
}

function composer_section(agent: string): Section {
    const dock = (title: string, band: Tree, props: FieldProps): Item => ({
        body: stack(
            [
                n("span", {class: "armada-type-strong"}, [title]),
                box("dock", [band, kit.field(props), n("span", {class: "armada-showroom-bounds"})]),
            ],
            {"data-gap": "tight"},
        ),
    });
    const quiet = kit.band({});
    const working = (activity?: string): Tree =>
        kit.band({
            run: {
                mark: "live",
                word: "Working",
                clock: "4:12",
                stop: {label: "Stop", tip: "Stop the run", key_hint: "⌘."},
                activity,
            },
        });
    return {
        id: "composer",
        title: "The composer: one raised field. Its bounds are the same in every state",
        columns: 2,
        gap: "tight",
        items: [
            dock("Empty, at rest", quiet, field_props(agent)),
            dock("Focused", quiet, field_props(agent, {force: "focus"})),
            dock(
                "One line typed",
                quiet,
                field_props(agent, {value: "now do the same for arg", force: "focus"}),
            ),
            dock(
                "A run is live: Steer",
                working("Aligning columns across sections"),
                field_props(agent, {
                    value: "and keep the help width at 80 columns",
                    verb: "Steer",
                    mode_text: "Sol · XHigh",
                    force: "focus",
                }),
            ),
            dock(
                `${agent} is waiting: Answer`,
                kit.band({run: {mark: "needs", word: `${agent} asks`, clock: "9m", kind: "asks"}}),
                field_props(agent, {value: "declaration order", verb: "Answer", force: "focus"}),
            ),
            dock(
                "⌘ held while a run is live: Queue",
                working(),
                field_props(agent, {
                    value: "then update the changelog",
                    verb: "Queue",
                    mode_text: "Sol · XHigh",
                    force: "focus",
                }),
            ),
            dock(
                `An attachment added, for people only: ${agent} is an outline and its model is gone`,
                quiet,
                field_props(agent, {
                    value: "this is what it prints today",
                    attachment: "help-output.txt",
                    answers: false,
                    force: "focus",
                }),
            ),
            dock(
                "A draft, focus elsewhere",
                quiet,
                field_props(agent, {value: "now do the same for arg"}),
            ),
            dock(
                "Disabled",
                quiet,
                field_props(agent, {
                    disabled: true,
                    placeholder: "Merged into main. This worktree is read only.",
                }),
            ),
            dock(
                "Four lines typed: only lines of text add height",
                quiet,
                field_props(agent, {
                    value: "now do the same for argument\nkeep the group keyword optional, default None\nprint ungrouped arguments last, under the plain heading\nthen run the formatter tests again",
                    force: "focus",
                }),
            ),
            dock("Paused", kit.band({run: {mark: "paused", word: "Paused"}}), field_props(agent)),
            dock(
                "The default button as a glyph",
                quiet,
                field_props(agent, {
                    value: "now do the same for arg",
                    force: "focus",
                    send: {verb: "Send", verbs: VERBS, form: "glyph", glyph: "send"},
                }),
            ),
            dock("A thread's field: flat until it holds focus", quiet, {
                label: "Reply",
                placeholder: "Reply in thread",
                send: {verb: "Reply", verbs: VERBS},
                flat: true,
            }),
        ],
    };
}

function band_section(agent: string): Section {
    const stop = {label: "Stop", tip: "Stop the run", key_hint: "⌘."};
    const line = (caption: string, band: Tree): Item => ({caption, body: box("dock", [band])});
    const typing_one = {people: [hamlet], stone: "lapis" as const, names: "King Hamlet"};
    return {
        id: "band",
        title: "The band: fixed cells on one line, 8 px clear of the field",
        columns: 2,
        gap: "tight",
        items: [
            line(
                "Working. The clock counts seconds for ten minutes, then minutes. It stands 8 px after the word",
                kit.band({run: {mark: "live", word: "Working", clock: "4:12", stop}}),
            ),
            line(
                "With the column folded or absent the band also carries the step's activity",
                kit.band({
                    run: {
                        mark: "live",
                        word: "Working",
                        clock: "4:12",
                        stop,
                        activity: "Running pytest tests/test_formatting.py -k group -q",
                    },
                }),
            ),
            line(
                "No output for 10 s: the clock drops to the second ink. At 40 s the band says so",
                kit.band({
                    run: {
                        mark: "live",
                        word: `No output for ${lasted(40)}`,
                        clock: "5:02",
                        kind: "quiet",
                        stop,
                    },
                }),
            ),
            line(
                "Needs you: the diamond, its colour, and how long it has waited. No Stop over the field while it only waits",
                kit.band({
                    run: {mark: "needs", word: `${agent} asks`, clock: "9m", kind: "asks", stop},
                }),
            ),
            line(
                "Queued behind another run",
                kit.band({run: {mark: "waiting", word: "Queued behind 1 run"}}),
            ),
            line("Paused", kit.band({run: {mark: "paused", word: "Paused"}})),
            line(
                "A person typing: their face on the feed's face axis, a bubble of their stone where their message will be",
                kit.band({typing: typing_one}),
            ),
            line(
                "Two people typing",
                kit.band({typing: {people: [hamlet, iago], names: "King Hamlet and Iago"}}),
            ),
            line(
                "Typing while a run holds the line: the dots ride on the face",
                kit.band({
                    typing: typing_one,
                    run: {mark: "live", word: "Working", clock: "4:12", stop},
                }),
            ),
            line(
                "Away from the newest: Jump to latest keeps its cell",
                kit.band({
                    run: {mark: "live", word: "Working", clock: "0:41", stop},
                    jump: {label: "Jump to latest", shown: true},
                }),
            ),
            line(
                "Something reversible was done: it is undone from here, not confirmed",
                kit.band({notice: {text: "Topic resolved", verb: {label: "Undo"}}}),
            ),
            line(
                "Offline",
                kit.band({notice: {text: "Offline. Messages send when the network returns"}}),
            ),
        ],
    };
}

const step = (state: MarkName, title: string, duration?: string): Step => ({
    kind: "step",
    state,
    title,
    duration,
});
const live_steps: Step[] = [
    step("done", "Design the OptionGroup API", lasted(72)),
    step("live", "Implement grouped help output"),
    {kind: "event", words: "Aligning columns across sections"},
    {
        kind: "event",
        mark: "failed",
        words: [n("b", {}, ["Hopper"]), " failed on the legacy tests"],
        fresh: true,
        verb: {label: "Show"},
    },
    {
        kind: "event",
        words: [n("b", {}, ["Iago"]), " steered here"],
        fresh: true,
        verb: {label: "Show"},
    },
    step("waiting", "Port the existing formatter tests"),
    step("waiting", "Update docs/options.rst"),
];

function column_section(agent: string): Section {
    const sizers = ["Finished 00:00", "12 of 12"];
    const file = (path: string, added: number, removed?: number): kit.ListRowProps => ({
        lead: kit.glyph("file"),
        label: c(path),
        trailing: kit.counts({added, removed}),
    });
    const changes = (added: number, rows: kit.ListRowProps[], slots?: number): Tree =>
        kit.section({
            heading: "Changes",
            trailing: [
                kit.cell(kit.counts({added, removed: 9}), ["+8888 −8888"], {align: "end"}),
                kit.text_control({label: "Review"}),
            ],
            rows,
            slots,
            more: {label: `${Math.max(0, rows.length - (slots ?? rows.length))} more`},
        });
    const head = (steps: MarkName[], caption: string): Tree =>
        kit.heading({
            word: "Checklist",
            marks: steps,
            caption: {text: caption, sizers},
            expanded: false,
        });
    return {
        id: "column",
        title: "The column: plain lists beside the conversation, on every place",
        columns: 4,
        gap: "lists",
        items: [
            {
                caption:
                    "Live. Every row is 28 high. A step is a mark, a title, a duration. What the step in hand is doing and what happened in it stand under it in the second ink, their marks beside their words and never in the steps' column. A dot marks what is new",
                body: kit.column({
                    heading: head(["done", "live", "waiting", "waiting"], "1 of 4"),
                    children: [
                        kit.checklist({label: "Checklist", steps: live_steps}),
                        changes(52, [
                            file("src/click/core.py", 38, 7),
                            file("src/click/formatting.py", 14, 2),
                        ]),
                    ],
                }),
            },
            {
                caption:
                    "A record: every mark a tick in the second ink, a finishing time in place of a count. Changes: one heading with its totals and the one verb, then a row a file",
                body: kit.column({
                    heading: head(["done", "done", "done"], "Finished 11:21"),
                    children: [
                        kit.checklist({
                            label: "Checklist",
                            steps: [
                                step("done", "Add the group keyword to option", lasted(48)),
                                step("done", "Print one section per group", lasted(125)),
                                step("done", "Port the formatter tests", lasted(31)),
                            ],
                        }),
                        changes(61, [
                            file("src/click/core.py", 38, 7),
                            file("src/click/formatting.py", 14, 2),
                            file("src/click/decorators.py", 9),
                            {
                                lead: kit.mark("done", {tone: "success"}),
                                label: "3 checks passed",
                                trailing: kit.list_figure("14m", {cells: true}),
                            },
                        ]),
                    ],
                }),
            },
            {
                caption:
                    "Failed and needs-you are states of a step: its mark changes shape and takes its colour. Paused and stopped are shapes in ink",
                body: kit.column({
                    heading: head(["done", "failed", "needs", "paused", "stopped"], "1 of 5"),
                    children: [
                        kit.checklist({
                            label: "Checklist",
                            steps: [
                                step("done", "Update docs/options.rst", lasted(24)),
                                step("failed", "Port the legacy formatter tests", lasted(40)),
                                {
                                    kind: "event",
                                    words: [c("conftest.py"), " needs a removed fixture"],
                                    fresh: true,
                                    verb: {label: "Retry"},
                                },
                                step("needs", "Choose a heading for ungrouped options"),
                                {
                                    kind: "event",
                                    words: "Waiting on anyone here",
                                    fresh: true,
                                    verb: {label: "Answer"},
                                },
                                step("paused", "Rebase onto main"),
                                step("stopped", "Rename the schema columns", lasted(12)),
                            ],
                        }),
                    ],
                }),
            },
            {
                caption: `No run yet: one sentence. Runs: a mark, what was asked, when; a monitor is a row with the eye. People: ${agent} first, then who is here`,
                body: kit.column({
                    heading: kit.heading({word: "Checklist"}),
                    children: [
                        kit.empty(
                            `Nothing has run here yet. Ask ${agent} for something and its steps stand here.`,
                        ),
                        kit.section({
                            heading: "Runs",
                            rows: [
                                {
                                    lead: kit.glyph("eye"),
                                    label: c("pytest -k format -q"),
                                    trailing: kit.list_figure("38 of 41", {cells: true}),
                                    live: true,
                                },
                                {
                                    lead: kit.mark("done"),
                                    label: "Sketch the OptionGroup API",
                                    trailing: kit.list_figure("Mon"),
                                },
                                {
                                    lead: kit.mark("failed"),
                                    label: "Port the legacy completion tests",
                                    trailing: kit.list_figure("Mon"),
                                },
                            ],
                        }),
                        kit.section({
                            heading: "People",
                            rows: [
                                {lead: kit.sigil("20"), label: agent},
                                {
                                    lead: kit.face({...hamlet, size: 20, presence: "here"}),
                                    label: "King Hamlet",
                                },
                                {
                                    lead: kit.face({...iago, size: 20, presence: "here"}),
                                    label: "Iago",
                                },
                            ],
                        }),
                    ],
                }),
            },
            {
                caption:
                    "The lists never push each other: a checklist of seven rows with nine steps folds its earliest and says so; Changes is four rows and counts the rest. A row with a second line under its label is 44",
                body: kit.column({
                    heading: head(["done", "done", "done", "live", "waiting"], "3 of 5"),
                    children: [
                        kit.checklist({
                            label: "Checklist",
                            slots: 7,
                            more: {label: "2 earlier"},
                            steps: [
                                step("done", "Read the formatter"),
                                step("done", "Sketch the API"),
                                ...live_steps,
                            ],
                        }),
                        changes(
                            88,
                            [
                                file("src/click/core.py", 38, 7),
                                file("src/click/formatting.py", 14, 2),
                                file("src/click/decorators.py", 9),
                                file("tests/test_formatting.py", 21),
                                file("docs/options.rst", 6),
                                file("CHANGES.rst", 2),
                            ],
                            4,
                        ),
                        kit.section({
                            heading: "Together",
                            rows: [
                                {
                                    lead: kit.mark("live"),
                                    label: "Rename the schema columns",
                                    second: "armada, schema-rename",
                                    trailing: kit.list_figure("4:12", {cells: true}),
                                    live: true,
                                },
                                {
                                    lead: kit.glyph("pr"),
                                    label: "Group options under headings",
                                    second: "pallets/click",
                                    trailing: kit.list_figure("Open"),
                                },
                            ],
                        }),
                    ],
                }),
            },
            {
                caption:
                    "The column's heading on a place with no checklist is one word in Strong; an empty section keeps its heading and says one sentence",
                body: kit.column({
                    heading: kit.heading({word: "Threads"}),
                    children: [
                        kit.section({heading: "Threads", rows: [], empty: "No threads here yet."}),
                        kit.section({
                            heading: "Shared",
                            rows: [
                                {
                                    lead: kit.glyph("doc"),
                                    label: "Help output, before and after",
                                    trailing: kit.list_figure("Tue"),
                                },
                            ],
                        }),
                    ],
                }),
            },
        ],
    };
}

function nav_section(): Section {
    const nested = (label: string, extra: Partial<kit.NavRowProps> = {}): Tree =>
        kit.nav_row({label, nested: true, ...extra});
    return {
        id: "nav",
        title: "Sidebar",
        columns: 3,
        items: [
            {
                body: box("navbox", [
                    kit.nav_row({label: "click", glyph: "repo-open", repository: {expanded: true}}),
                    nested("option-groups"),
                    nested("demo-live-run", {mark: "live"}),
                    nested("demo-needs-input", {mark: "needs", fresh: true}),
                    nested("demo-failed-run", {mark: "failed", fresh: true}),
                    nested("demo-threads", {count: 4, fresh: true}),
                    kit.nav_row({
                        label: "armada",
                        glyph: "repo",
                        repository: {expanded: false},
                        mark: "live",
                    }),
                    kit.nav_gap(),
                    kit.nav_row({label: "design", glyph: "hash"}),
                    kit.nav_row({label: "devel", glyph: "hash", count: 12, fresh: true}),
                    kit.nav_row({label: "core team", glyph: "lock"}),
                    kit.nav_gap(),
                    kit.nav_row({
                        label: "Iago",
                        face: {...iago, presence: "here"},
                        count: 1,
                        fresh: true,
                    }),
                    kit.nav_row({label: "King Hamlet", face: {...hamlet, presence: "away"}}),
                    kit.nav_row({label: "Cordelia, Othello", faces: [cordelia, othello]}),
                ]),
            },
            {
                body: box("navbox", [
                    nested("rest, nothing new"),
                    nested("something new", {count: 3, fresh: true}),
                    nested("hover", {force: "hover"}),
                    nested("pressed", {force: "press"}),
                    nested("selected", {current: true}),
                    nested("focus", {force: "focus"}),
                    nested("a-very-long-worktree-name-that-truncates", {count: 12}),
                ]),
            },
            {
                body: stack([
                    cap(
                        "A row is one kind glyph, a label and one trailing thing, by priority: needs you, failed, unread count, live, nothing. A count ends on the marks' right edge. Hover, pressed and selected are three fills, each a step from the last. A row with nothing new is the second ink and one with news is ink; weight never changes with state. A repository is its section's name, always Strong; its glyph is the disclosure. No group names: 20 px between groups.",
                    ),
                    cap(
                        "Faces: 28 in the conversation, 20 in lists, 18 in a line. Present, away, no dot. A group is two faces, never two people's initials in one disc",
                    ),
                    row([
                        kit.face(hamlet),
                        kit.face({...iago, size: 20, presence: "here"}),
                        kit.face({...hamlet, size: 20, presence: "away"}),
                        kit.face({...cordelia, size: 20}),
                        kit.faces([cordelia, othello], {size: 20}),
                        kit.faces([iago, cordelia, othello], {size: 18}),
                    ]),
                    cap(
                        "The file tree is the same row, starting on the lane's edge, its counts in the diff's inks",
                    ),
                    n("div", {class: "armada-tree"}, [
                        kit.tree_row({label: "src", glyph: "folder-open"}),
                        kit.tree_row({label: "click", glyph: "folder-open", depth: 1}),
                        kit.tree_row({
                            label: "core.py",
                            glyph: "file",
                            depth: 2,
                            current: true,
                            trailing: kit.counts({added: 38, removed: 7}),
                        }),
                        kit.tree_row({
                            label: "formatting.py",
                            glyph: "file",
                            depth: 2,
                            trailing: kit.counts({added: 14, removed: 2}),
                        }),
                        kit.tree_row({label: "README.md", glyph: "doc"}),
                    ]),
                ]),
            },
        ],
    };
}

const chips = (list: [string, number?, ("mine" | Force)?][]): kit.Reaction[] =>
    list.map(([emoji, count, flag]) => ({
        emoji,
        count,
        label: `${emoji} ${count ?? 1}`,
        mine: flag === "mine",
        force: flag === "mine" ? undefined : flag,
    }));

function conversation_section(agent: string): Section {
    const says = (text: Child[], extra: Partial<kit.BubbleProps> = {}): Tree =>
        kit.bubble({content: text, ...extra});
    const agent_turn = (children: Child[], named = true, live = false): Tree =>
        kit.turn({side: "agent", name: named ? agent : undefined, live, children});
    const over = (): Tree =>
        kit.overlay(
            [
                {glyph: "react", label: "React"},
                {glyph: "thread", label: "Reply in thread"},
                {glyph: "copy", label: "Copy text"},
                {glyph: "more", label: "More"},
            ],
            {label: "Message actions"},
        );
    const signature = kit.code_block([
        "group: OptionGroup | ",
        ["number", "None"],
        " = ",
        ["number", "None"],
    ]);
    return {
        id: "conversation",
        title: `The conversation: people in bubbles, ${agent} on the plane`,
        columns: 3,
        items: [
            {
                caption:
                    "Theirs: stone with a hint of the speaker's own, a face on every turn, a name when the speaker on that side changes. Stacked bubbles are 4 apart and close their corners to 6 on the speaker's side. The time sits beside the last line",
                body: stage([
                    theirs(hamlet, "King Hamlet", [
                        says(["is the keyword public"], {join: "below"}),
                        says(["or underscore for now"], {join: "above", time: "11:36"}),
                    ]),
                    mine([says(["public"], {time: "11:36"})]),
                    theirs(hamlet, undefined, [says(["good"], {time: "11:37"})]),
                    theirs(iago, "Iago", [
                        says(["then it needs a changelog line"], {time: "11:37"}),
                    ]),
                ]),
            },
            {
                caption: `${agent}: no bubble. Its sigil at a face's size in the gutter, its name where a person's name stands. 16 px from the turn before it, measured from its line`,
                body: stage([
                    mine([says(["and options with no group"])]),
                    agent_turn([
                        kit.answer([
                            p(
                                "They stay under the plain ",
                                c("Options:"),
                                " heading, printed last.",
                            ),
                        ]),
                    ]),
                    mine([says(["show me the signature"], {time: "11:37"})]),
                    agent_turn(
                        [kit.answer([p("One new keyword, default ", c("None"), ".")]), signature],
                        false,
                    ),
                ]),
            },
            {
                caption:
                    "A long answer keeps the one size: a Heading, then Kräftig; a 20 px marker column; 12 between paragraphs. Its footer is the record the conversation keeps",
                body: stage([
                    agent_turn([
                        kit.answer([
                            p("Option groups are in."),
                            n("h3", {}, ["What changed"]),
                            n("ul", {}, [
                                n("li", {}, [
                                    c("OptionGroup(title, help=None)"),
                                    " in ",
                                    c("core.py"),
                                    ".",
                                ]),
                                n("li", {}, [
                                    "Ungrouped options stay under ",
                                    strong("Options"),
                                    ".",
                                ]),
                            ]),
                            n("h4", {}, ["Not done"]),
                            p("Groups do ", em("not"), " nest."),
                        ]),
                        kit.run_footer({
                            text: `Changed 2 files in ${lasted(368)}`,
                            verb: {label: "Show"},
                        }),
                    ]),
                ]),
            },
            {
                caption:
                    "Sent, 0 ms: the row, the sigil and the working mark exist in the send task. The mark stands where the first word will be",
                body: stage([
                    mine([says(["and options with no group"])]),
                    agent_turn([kit.answer([p(kit.stream_head({first: true}), "​")])], true, true),
                ]),
            },
            {
                caption:
                    "First words: appended as they arrive, whole. The mark rides the head of the stream and takes no room. It is the one loop",
                body: stage([
                    mine([says(["and options with no group"])]),
                    agent_turn(
                        [kit.answer([p("They stay under the plain", kit.stream_head())])],
                        true,
                        true,
                    ),
                ]),
            },
            {
                caption: "Settled: the mark leaves on the 60 ms fade. No word moved",
                body: stage([
                    mine([says(["and options with no group"])]),
                    agent_turn([
                        kit.answer([
                            p(
                                "They stay under the plain ",
                                c("Options:"),
                                " heading, printed last.",
                            ),
                        ]),
                    ]),
                ]),
            },
            {
                caption:
                    "Reactions: real emoji in a 20 px chip seated on the bubble's outer edge, on its last line; the time follows. A chip overlaps only its own bubble. Mine wears my colour and leaves a wash",
                body: stage([
                    theirs(iago, "Iago", [
                        says(["that leaves my three callers alone"], {join: "below"}),
                        says(["so I can drop the shim next week"], {
                            join: "above",
                            time: "11:44",
                            liked: true,
                            reactions: kit.reactions(chips([["❤️", 1, "mine"]])),
                        }),
                    ]),
                    mine([
                        says(["drop it"], {
                            time: "11:45",
                            reactions: kit.reactions(chips([["👍", 3]])),
                        }),
                    ]),
                ]),
            },
            {
                caption: "Six kinds: four, then one that counts the rest. Rest, hover, pressed",
                body: stage([
                    theirs(cordelia, "Cordelia", [
                        says(["shipped to staging"], {
                            time: "12:02",
                            liked: true,
                            reactions: kit.reactions(
                                chips([
                                    ["🎉", 4, "mine"],
                                    ["👍", 3, "hover"],
                                    ["❤️", 2, "press"],
                                ]),
                                {label: "3 more reactions", count: 3},
                            ),
                        }),
                    ]),
                    mine([
                        says(["fix is up"], {
                            time: "12:04",
                            reactions: kit.reactions(
                                chips([["🙏", 1, "mine"], ["👀", 2], ["😅"]]),
                                {label: "3 more reactions", count: 3},
                            ),
                        }),
                    ]),
                ]),
            },
            {
                caption: `On ${agent}'s words there is no edge to sit on: they follow the turn's last word, on its line. On a turn that ends in a block: the block's top right corner`,
                body: stage([
                    agent_turn([
                        kit.answer([
                            p(
                                "They stay under ",
                                c("Options:"),
                                ", printed last.",
                                kit.reaction_anchor(
                                    kit.reactions(chips([["👍", 2, "mine"], ["🙏"]])),
                                ),
                            ),
                        ]),
                    ]),
                    agent_turn(
                        [
                            kit.answer([p("One new keyword, default ", c("None"), ".")]),
                            kit.reaction_corner(kit.reactions(chips([["👍", 2], ["👀"]]))),
                            signature,
                        ],
                        false,
                    ),
                ]),
            },
            {
                caption:
                    "A day, and where what is new begins: one Label line each, centred. No rule. New is in my colour",
                body: stage([
                    mine([says(["leaving it there for today"], {time: "18:20"})]),
                    kit.day("Tuesday 13 October"),
                    theirs(cordelia, "Cordelia", [
                        says(["morning, the docs build is green"], {time: "09:02"}),
                    ]),
                    kit.new_line("New"),
                    theirs(iago, "Iago", [says(["can I rebase on it"], {time: "09:14"})]),
                ]),
            },
            {
                caption:
                    "The thread line is three things: who replied, the count as the control, the last reply's first words. New replies are told in the count's words",
                body: stage([
                    theirs(cordelia, "Cordelia", [
                        says(["the docs page lists them by name today"], {time: "11:38"}),
                        kit.thread_line({
                            faces: [iago, hamlet],
                            count: {label: "3 replies"},
                            snippet: "Iago: keep the anchors as they are",
                        }),
                    ]),
                    mine([
                        says(["who owns the migration note"], {time: "11:43"}),
                        kit.thread_line({
                            faces: [othello],
                            count: {label: "1 new reply"},
                            snippet: "Othello: me, by Friday",
                        }),
                    ]),
                ]),
            },
            {
                caption:
                    "Steering and queueing leave a receipt under the bubble. A mention is Kräftig; a mention of me is Kräftig in my colour, with no fill",
                body: stage([
                    theirs(iago, "Iago", [
                        says(["keep ungrouped options last, please"], {time: "11:36"}),
                        kit.receipt(`Steered ${agent} at step 2`),
                    ]),
                    mine([
                        says(["then update the changelog"], {time: "11:37"}),
                        kit.receipt("Queued behind this run"),
                    ]),
                    theirs(hamlet, "King Hamlet", [
                        says([
                            kit.mention({name: "Desdemona", me: true}),
                            " can you review after ",
                            kit.mention({name: agent}),
                            " is done",
                        ]),
                    ]),
                ]),
            },
            {
                caption:
                    "Edited, deleted by someone else (kept as an outline until I leave), not sent",
                body: stage([
                    theirs(othello, "Othello", [
                        says(["the anchors stay as they are"], {
                            join: "below",
                            meta: {text: "Edited"},
                        }),
                        says(["Deleted"], {join: "above", kind: "gone"}),
                    ]),
                    mine([
                        says(["run the formatter tests"], {join: "below", time: "11:38"}),
                        says(["and the fish ones"], {
                            join: "above",
                            kind: "unsent",
                            verb: {label: "Retry"},
                            meta: {text: "Not sent", tone: "failure"},
                        }),
                    ]),
                ]),
            },
            {
                caption:
                    "A link is underlined in my colour; its preview is a block under the bubble. An emoji alone has no fill",
                body: stage([
                    theirs(othello, "Othello", [
                        says(
                            [
                                "upstream asked for the same thing in ",
                                n("a", {href: "#conversation"}, ["pallets/click#2841"]),
                            ],
                            {join: "below", time: "11:46"},
                        ),
                        kit.link_preview({
                            title: "Group options under headings in help output",
                            caption: "github.com, issue 2841, open, 14 comments",
                            glyph: "link",
                        }),
                    ]),
                    mine([says(["🎉🎉"], {kind: "jumbo"})]),
                ]),
            },
            {
                caption: `Hover or keyboard selection: four verbs in a raised overlay on the bubble's open side, or at the lane's edge on ${agent}'s first line. Never in flow`,
                body: stage([
                    theirs(hamlet, "King Hamlet", [
                        says(["does help print groups in declaration order"], {overlay: over()}),
                    ]),
                    agent_turn([kit.answer([p("Declaration order.")]), over()]),
                ]),
            },
            {
                caption:
                    "A person's bubble in each of the six stones and the neutral one, and a face in each",
                body: stage([
                    ...(["moss", "larimar", "lapis", "amethyst", "rose", "flint"] as const).map(
                        (stone) =>
                            kit.turn({
                                side: "theirs",
                                stone,
                                face: {
                                    initials: stone
                                        .slice(0, 2)
                                        .replace(/^./, (letter) => letter.toUpperCase()),
                                    stone,
                                },
                                children: [says([`a bubble in ${stone}`])],
                            }),
                    ),
                    kit.turn({
                        side: "theirs",
                        face: {initials: "Gu", stone: "flint"},
                        children: [says(["a bubble with no stone"])],
                    }),
                    mine([says(["and mine"])]),
                ]),
            },
            {
                caption: "An empty worktree: the one place the 64 px sigil lives",
                body: stage([
                    kit.hello([p(`Ask ${agent} for something, or say it to the people here.`)]),
                ]),
            },
            {
                caption:
                    "A thread opened where it is: its replies stand under the root bubble, set in from it, on one stone line, with a flat field of their own",
                body: stage([
                    theirs(cordelia, "Cordelia", [
                        says(["the docs page lists them by name today"], {time: "11:38"}),
                        kit.thread_line({
                            faces: [iago, hamlet],
                            count: {label: "2 replies", pressed: true},
                            snippet: "",
                        }),
                        kit.thread([
                            theirs(iago, "Iago", [
                                says(["keep the anchors as they are"], {time: "11:40"}),
                            ]),
                            theirs(hamlet, "King Hamlet", [says(["agreed"], {time: "11:41"})]),
                            kit.field({
                                label: "Reply",
                                placeholder: "Reply in thread",
                                send: {verb: "Reply", verbs: VERBS},
                                flat: true,
                            }),
                        ]),
                    ]),
                ]),
            },
        ],
    };
}

function blocks_section(agent: string): Section {
    const open = {label: "Open"};
    return {
        id: "blocks",
        title: "Blocks: what a machine would accept, and artifacts. Beside talk, never inside it",
        columns: 2,
        items: [
            {
                body: stack(
                    [
                        cap(
                            "Code: one quiet opaque fill, lighter than a bubble, the lane's width, coloured in four roles",
                        ),
                        kit.code_block([
                            ["keyword", "def"],
                            " option(*param_decls, group: OptionGroup | ",
                            ["number", "None"],
                            " = ",
                            ["number", "None"],
                            ", **attrs):",
                        ]),
                        cap(
                            "Diff: one grid for every line. Old number, new number, a sign, the code. Added has a green ground and a plus; removed a red ground and a minus. A selected line takes Fill-2 and a bar of my colour",
                        ),
                        kit.diff_block([
                            {
                                kind: "hunk",
                                text: "@@ −222,7 +222,8 @@ def write_dl(self, rows, col_max=30, col_spacing=2)",
                            },
                            {
                                kind: "context",
                                old: 222,
                                new: 222,
                                code: ["        widths = measure_table(rows)"],
                            },
                            {
                                kind: "removed",
                                old: 226,
                                code: [
                                    "        first_col = min(widths[",
                                    ["number", "0"],
                                    "], col_max) + col_spacing",
                                ],
                            },
                            {
                                kind: "added",
                                new: 226,
                                code: [
                                    "        first_col = min(shared ",
                                    ["keyword", "or"],
                                    " widths[",
                                    ["number", "0"],
                                    "], col_max) + col_spacing",
                                ],
                            },
                            {
                                kind: "context",
                                old: 227,
                                new: 227,
                                code: [
                                    "        ",
                                    ["keyword", "for"],
                                    " first, second ",
                                    ["keyword", "in"],
                                    " iter_rows(rows, ",
                                    ["number", "2"],
                                    "):",
                                ],
                                selected: true,
                            },
                        ]),
                        cap(
                            "Table: Text with figures in fixed cells, the head in the second ink over the one permitted separator",
                        ),
                        kit.table_block({
                            head: [
                                {label: "File"},
                                {label: "Added", figures: true},
                                {label: "Removed", figures: true},
                                {label: "Tests", figures: true},
                            ],
                            rows: [
                                [c("src/click/core.py"), 64, 9, 12],
                                [c("src/click/formatting.py"), 21, 3, 6],
                            ],
                        }),
                        cap(
                            "Source: line numbers in the code face at 12; a changed line's ground is a shape, set in 4 px",
                        ),
                        kit.source([
                            {
                                number: 208,
                                code: [
                                    ["keyword", "def"],
                                    " write_dl(self, rows, col_max=",
                                    ["number", "30"],
                                    "):",
                                ],
                            },
                            {number: 209, code: ["    widths = measure_table(rows)"], added: true},
                            {number: 210, code: ["    shared = self.shared_width"], added: true},
                            {number: 211, code: ["    ", ["keyword", "return"], " widths"]},
                        ]),
                    ],
                    {"data-gap": "loose", "data-fill": true},
                ),
            },
            {
                body: stack(
                    [
                        cap("Artifacts: a kind glyph, a title, one caption, one verb"),
                        kit.artifact({
                            glyph: "doc",
                            title: "Help output, before and after",
                            caption: "2 pages",
                            verb: open,
                        }),
                        kit.artifact({
                            glyph: "table",
                            title: "Help width by command",
                            caption: "12 rows",
                            verb: open,
                        }),
                        kit.artifact({
                            glyph: "chart",
                            title: "Suite time by commit",
                            caption: "40 points",
                            verb: open,
                        }),
                        kit.artifact({
                            glyph: "image",
                            title: "Rendered help",
                            caption: "1 image",
                            verb: open,
                        }),
                        cap(
                            "Question: its options are buttons with their numbers. 1 to 9 answer while the field is empty",
                        ),
                        kit.question({
                            label: "Heading",
                            options: [{label: "Options, as today"}, {label: "General options"}],
                        }),
                        cap(
                            "Run footers: finished, failed, stopped. One line, one verb. The footer is the record the conversation keeps of a run",
                        ),
                        kit.run_footer({
                            text: `Changed 3 files in ${lasted(204)}`,
                            verb: {label: "Show"},
                        }),
                        kit.run_footer({
                            mark: "failed",
                            text: `Failed after ${lasted(40)}`,
                            tone: "failure",
                            verb: {label: "Retry"},
                        }),
                        kit.run_footer({mark: "stopped", text: "Stopped by Desdemona"}),
                        cap(
                            "Home: a row is 56 high. A gutter object, a sentence with its place under it, and one trailing thing: the verb on a row that needs me, the time on the rest. Rest, hover, selected",
                        ),
                        n("div", {role: "list", style: "padding-left:var(--gutter)"}, [
                            kit.home_row({
                                lead: kit.mark("needs", {size: 28}),
                                sentence: `${agent} asks which heading ungrouped options take`,
                                place: "click, demo-needs-input",
                                verb: {label: "Answer"},
                            }),
                            kit.home_row({
                                lead: kit.sigil("28"),
                                sentence: `${agent} finished option groups`,
                                place: "click, option-groups",
                                time: "11:21",
                                force: "hover",
                            }),
                            kit.home_row({
                                lead: kit.face(iago),
                                sentence: "Iago mentioned you",
                                place: "devel, release 8.2",
                                time: "10:02",
                                selected: true,
                            }),
                        ]),
                    ],
                    {"data-gap": "loose", "data-fill": true},
                ),
            },
        ],
    };
}

function raised_section(agent: string): Section {
    const models: MenuItem[] = [
        {label: "Sol", caption: "gpt-5.6-sol", current: false},
        {label: "Sol Fast", caption: "gpt-5.6-sol, ultrafast", current: true, highlighted: true},
        {label: "Mini", caption: "gpt-5.6-mini", current: false},
    ];
    return {
        id: "raised",
        title: "Raised: menu, picker, tooltip, dialog, palette, the checklist as a sheet",
        columns: 1,
        note: "The highlighted row of a menu, picker or palette is a neutral fill: a resting selection is not news. The tick marks the current value. A raised surface is opaque, casts the one shadow and carries the 1 px edge, so its shape holds where a shadow cannot: over a block, and in dark.",
        items: [
            {
                body: row(
                    [
                        kit.menu({
                            still: true,
                            label: "Message",
                            items: [
                                {label: "Reply in thread", key_hint: "R"},
                                {label: "Copy text", key_hint: "⌘C", highlighted: true},
                                {label: "Copy link"},
                                "gap",
                                {label: "Mark unread from here"},
                                {label: "Edit", disabled: true},
                                {label: "Delete"},
                            ],
                        }),
                        kit.picker({
                            still: true,
                            label: "Model and effort",
                            models,
                            efforts: {
                                label: "Effort",
                                steps: ["Low", "Medium", "High", "XHigh"].map((label) => ({
                                    label,
                                    selected: label === "High",
                                })),
                            },
                            caption: "48 percent of context used",
                        }),
                        stack(
                            [
                                kit.tooltip({text: "Checklist", key_hint: "⌘J"}),
                                kit.tooltip({text: "Stop the run", key_hint: "⌘."}),
                                kit.tooltip({text: "Working since 11:33:08"}),
                                kit.tooltip({text: "Steer", key_hint: "Return"}),
                            ],
                            {"data-gap": "loose"},
                        ),
                        kit.dialog({
                            title: "Discard the changes in demo-live-run",
                            body: "3 files have changes that are not committed. This cannot be undone.",
                            buttons: [
                                {label: "Cancel"},
                                {label: "Discard changes", kind: "danger"},
                            ],
                        }),
                        kit.palette({
                            still: true,
                            label: "Jump to",
                            input: n("span", {}, ["opt"]),
                            items: [
                                {label: "option-groups", key_hint: "click", highlighted: true},
                                {label: "Open the checklist", key_hint: "⌘J"},
                                {label: "Stop the run", key_hint: "⌘."},
                            ],
                        }),
                        stack([
                            kit.sheet({
                                still: true,
                                label: "Checklist",
                                children: [
                                    kit.checklist({
                                        label: "Checklist",
                                        steps: live_steps.slice(0, 5),
                                    }),
                                ],
                            }),
                            cap(
                                `Below 1408 px there is no column: the same checklist opens from its standing home as a raised sheet (⌘J). ${agent}'s marks are the column's`,
                            ),
                        ]),
                    ],
                    {"data-gap": "wide", "data-align": "start"},
                ),
            },
        ],
    };
}

const MARK_SAYS: Record<MarkName, string> = {
    done: "done",
    live: "live, now",
    waiting: "waiting, queued",
    needs: "needs you",
    failed: "failed",
    paused: "paused",
    stopped: "stopped",
};

function marks_section(agent: string): Section {
    return {
        id: "marks",
        title: `${agent}, and the marks of what it is doing: pixels`,
        columns: 2,
        items: [
            {
                body: stack([
                    cap(
                        "Run and step marks: 7 by 7 cells of 2 px, told apart by shape alone. Colour only on needs you and failed. A waiting step is an empty box, a done one a tick with one bend",
                    ),
                    row(
                        mark_names.map((name) => labelled(MARK_SAYS[name], kit.mark(name))),
                        {"data-gap": "wide"},
                    ),
                    box("onbar", [
                        ...mark_names.map((name) =>
                            n("span", {style: "color:var(--ink-1)"}, [
                                n("svg", {class: "armada-mark", "aria-hidden": "true"}, [
                                    n("use", {href: `#armada-mark-${name}`}),
                                ]),
                            ]),
                        ),
                        cap("all in one ink, on the ground: shape alone"),
                    ]),
                    row(
                        [
                            labelled("28, on Home", kit.mark("needs", {size: 28})),
                            labelled("28, on Home", kit.mark("failed", {size: 28})),
                            labelled("28", kit.mark("live", {size: 28})),
                        ],
                        {"data-gap": "wide"},
                    ),
                    cap(
                        "The working mark, the one loop a live run has: the live stone with one bed stepping down through it, four steps of 200 ms, one sprite moved in steps so no frame is blank. It is the head of the stream. A person's typing mark is three round dots that grow and darken in turn",
                    ),
                    row([
                        n(
                            "svg",
                            {
                                class: "armada-mark",
                                viewBox: "0 0 28 7",
                                style: "width:calc(var(--px) * 56)",
                                "aria-hidden": "true",
                            },
                            [n("use", {href: "#armada-mark-working"})],
                        ),
                        kit.working_mark({live: true}),
                        kit.working_mark({live: false}),
                        kit.typing_dots(),
                        kit.typing_dots({small: true}),
                    ]),
                ]),
            },
            {
                body: stack([
                    cap(
                        `The sigil is ${agent} and nothing else: a cut stone with an unbroken outline and two tilted beds inside it, as the stone is banded. An original mark. It never loops`,
                    ),
                    row(
                        [
                            labelled("20", kit.sigil("20")),
                            labelled("28", kit.sigil("28")),
                            labelled("64", kit.sigil("64")),
                            labelled("20, outline", kit.sigil("20-outline")),
                        ],
                        {"data-gap": "wide", "data-align": "end"},
                    ),
                    cap(
                        `Where each size lives: 28 in the gutter where a person shows a face, and beside a Home row ${agent} is the actor of; 20 in People and in the field, where it is whole when ${agent} will answer and an outline when it will not; 64 on an empty worktree and nowhere else. A commit ${agent} made carries its name, not its sigil`,
                    ),
                ]),
            },
        ],
    };
}

function glyphs_section(): Section {
    const all = glyph_groups.flatMap((entry) => entry.glyphs.map((item) => item.name));
    return {
        id: "glyphs",
        title: "Glyphs: strokes are for what people use",
        columns: 2,
        gap: "lists",
        note: `${all.length} glyphs. 16 px box, 14 live, one weight on whole device pixels (1 px at 1x, 1.5 px at 2x, nudged a quarter pixel), butt caps, round joins, currentColor; the only fills are whole-pixel squares. One glyph a concept. Never scaled. The check renders this sheet at 1x and at 2x.`,
        items: [
            ...glyph_groups.map((entry) => ({
                body: stack(
                    [
                        group(entry.group),
                        box(
                            "icons",
                            entry.glyphs.map((item) =>
                                n(
                                    "div",
                                    {
                                        class: "armada-showroom-icon",
                                        "data-glyph": item.name,
                                        title: item.means,
                                    },
                                    [kit.glyph(item.name), n("span", {}, [item.name])],
                                ),
                            ),
                        ),
                    ],
                    {"data-gap": "tight", "data-fill": true},
                ),
            })),
            {
                body: box(
                    "onbar",
                    all.map((name) => kit.glyph(name)),
                ),
            },
        ],
    };
}

export function sections(agent: string): Section[] {
    return [
        type_section(agent),
        colour_section(),
        space_section(),
        header_section(agent),
        controls_section(),
        composer_section(agent),
        band_section(agent),
        column_section(agent),
        nav_section(),
        conversation_section(agent),
        blocks_section(agent),
        raised_section(agent),
        marks_section(agent),
        glyphs_section(),
    ];
}
