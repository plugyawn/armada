// Chrome: the nav's row, the header's two lines, the band and the field.
// The surfaces that own them fill these from their stores; nothing here
// knows a store, a route or the server.

import {glyph_control, send_button, stop_control, text_control, views} from "./controls.ts";
import type {Choice, Common, SendProps} from "./controls.ts";
import type {GlyphName, MarkName} from "./icons/names.ts";
import {cell, face, faces, figure, glyph, mark, sigil, typing_dots} from "./icons.ts";
import type {FaceProps, Stone} from "./icons.ts";
import {n, on_press_down} from "./tree.ts";
import type {Child, Force, Handler, Tree} from "./tree.ts";

// ---- nav

export type NavRowProps = {
    label: string;
    // One leading thing: a kind glyph, a face, or a group's faces.
    // Worktrees are indented and carry none.
    glyph?: GlyphName | undefined;
    face?: FaceProps | undefined;
    faces?: FaceProps[] | undefined;
    // One trailing thing, by priority: needs you, failed, unread count,
    // live.
    mark?: MarkName | undefined;
    count?: number | undefined;
    nested?: boolean | undefined;
    // A repository is its section's name, and its book is the disclosure.
    repository?: {expanded: boolean} | undefined;
    current?: boolean | undefined;
    // Something new: first ink. Weight never changes with state.
    fresh?: boolean | undefined;
    on_press?: Handler | undefined;
    force?: Force | undefined;
};

export function nav_row(props: NavRowProps): Tree {
    return n(
        "button",
        {
            type: "button",
            class: "armada-nav-row armada-hover",
            "data-nested": props.nested,
            "data-repo": props.repository !== undefined,
            "data-fresh": props.fresh,
            "data-force": props.force,
            "aria-current": props.current ? "page" : undefined,
            "aria-expanded":
                props.repository === undefined ? undefined : String(props.repository.expanded),
        },
        [
            props.glyph === undefined ? undefined : glyph(props.glyph),
            props.face === undefined ? undefined : face({...props.face, size: 20}),
            props.faces === undefined ? undefined : faces(props.faces, {size: 14}),
            n("span", {class: "armada-nav-label"}, [props.label]),
            props.mark === undefined
                ? props.count === undefined || props.current
                    ? undefined
                    : n("span", {class: "armada-nav-count"}, [figure(props.count)])
                : mark(props.mark),
        ],
        {on: on_press_down(props.on_press)},
    );
}

// The nav's top row: the workspace's name with its menu, and New.
export function nav_top(props: {
    name: Common & {label: string};
    create: Common & {label: string};
}): Tree {
    return n("div", {class: "armada-nav-top"}, [
        n(
            "button",
            {type: "button", class: "armada-nav-name armada-hover", "aria-haspopup": "menu"},
            [n("span", {}, [props.name.label]), glyph("down")],
            {on: {click: props.name.on_press}},
        ),
        glyph_control({...props.create, glyph: "plus"}),
    ]);
}

export function nav_gap(): Tree {
    return n("div", {class: "armada-nav-gap"});
}

// A row of the file tree: the nav's row, starting on the lane's edge, with
// its counts in the diff's inks.
export function tree_row(props: {
    label: string;
    glyph: GlyphName;
    depth?: 0 | 1 | 2 | undefined;
    current?: boolean | undefined;
    trailing?: Child;
    on_press?: Handler | undefined;
    force?: Force | undefined;
}): Tree {
    return n(
        "button",
        {
            type: "button",
            class: "armada-nav-row armada-hover",
            "data-tree": true,
            "data-depth": props.depth,
            "data-force": props.force,
            "aria-current": props.current ? "true" : undefined,
        },
        [glyph(props.glyph), n("span", {class: "armada-nav-label"}, [props.label]), props.trailing],
        {on: on_press_down(props.on_press)},
    );
}

// What was added and what was removed, as counts in the diff's two inks.
export function counts(props: {added?: number | undefined; removed?: number | undefined}): Tree {
    return n("span", {class: "armada-counts"}, [
        props.added === undefined
            ? undefined
            : n("span", {class: "armada-count-added"}, [figure(`+${props.added}`)]),
        props.added !== undefined && props.removed !== undefined ? " " : undefined,
        props.removed === undefined
            ? undefined
            : n("span", {class: "armada-count-removed"}, [figure(`−${props.removed}`)]),
    ]);
}

// ---- the column's heading

export type HeadingProps = {
    word: string;
    // The caption stands in a cell that holds every form it takes.
    caption?: {text: string; sizers: string[]} | undefined;
    // The marks of its steps, shown while it is folded.
    marks?: MarkName[] | undefined;
    expanded?: boolean | undefined;
    on_press?: Handler | undefined;
    force?: Force | undefined;
};

// The column's first heading, and the checklist's standing home: on the
// header's second line over the column, or folded into the header.
export function heading(props: HeadingProps): Tree {
    return n(
        "button",
        {
            type: "button",
            class: "armada-heading armada-hover",
            "data-force": props.force,
            "aria-expanded": props.expanded === undefined ? undefined : String(props.expanded),
        },
        [
            n("span", {class: "armada-heading-word"}, [props.word]),
            props.marks === undefined
                ? undefined
                : n(
                      "span",
                      {class: "armada-heading-marks"},
                      props.marks.map((name) => mark(name)),
                  ),
            props.caption === undefined
                ? undefined
                : n("span", {class: "armada-heading-caption"}, [
                      cell(props.caption.text, props.caption.sizers, {align: "end"}),
                  ]),
        ],
        {on: {click: props.on_press}},
    );
}

// ---- header

export type HeaderProps = {
    // "owner/repo", or the channel a topic is in.
    path?: (Common & {label: string}) | undefined;
    title: string;
    branch?:
        | (Common & {
              label: string;
              // Printed only when it is not the worktree's own.
              name?: string | undefined;
              // Its distance from base, in a cell as wide as its longest.
              distance: string;
              distance_sizer: string;
              // Detached or in conflict: the diamond and its colour.
              attention?: boolean | undefined;
          })
        | undefined;
    pull?: (Common & {label: string; number: string; state: string; passed?: boolean}) | undefined;
    commit?:
        | (Common & {
              label: string;
              by: string;
              summary: string;
              sha: string;
              age: string;
              age_sizer: string;
          })
        | undefined;
    // Line 2: the views of a worktree, or one sentence where views would
    // stand; then More and the folded heading.
    views?: {label: string; views: (Choice & {count?: number | undefined})[]} | undefined;
    say?: string | undefined;
    more: Common & {label: string};
    heading?: HeadingProps | undefined;
    // A document pane is scrolled under it.
    cut?: boolean | undefined;
};

export function header(props: HeaderProps): Tree {
    const {branch, pull, commit} = props;
    return n("header", {class: "armada-header", "data-cut": props.cut}, [
        n("div", {class: "armada-header-line1"}, [
            props.path === undefined
                ? undefined
                : [
                      n(
                          "button",
                          {type: "button", class: "armada-header-path armada-hover"},
                          [n("span", {}, [props.path.label])],
                          {on: {click: props.path.on_press}},
                      ),
                      n("span", {class: "armada-header-slash", "aria-hidden": "true"}, ["/"]),
                  ],
            n("h1", {class: "armada-header-title"}, [props.title]),
            branch === undefined
                ? undefined
                : n(
                      "button",
                      {
                          type: "button",
                          class: "armada-header-fact armada-hover",
                          "data-fact": "branch",
                          "data-tone": branch.attention ? "attention" : undefined,
                          "aria-label": branch.label,
                      },
                      [
                          branch.attention ? mark("needs") : glyph("branch"),
                          branch.name === undefined ? undefined : n("code", {}, [branch.name]),
                          n("span", {class: "armada-header-distance"}, [
                              cell(branch.distance, [branch.distance_sizer], {align: "start"}),
                          ]),
                      ],
                      {on: {click: branch.on_press}},
                  ),
            pull === undefined
                ? undefined
                : n(
                      "button",
                      {
                          type: "button",
                          class: "armada-header-fact armada-hover",
                          "data-fact": "pull",
                          "aria-label": pull.label,
                      },
                      [
                          glyph("pr"),
                          n("span", {}, [pull.number]),
                          n(
                              "span",
                              {
                                  class: "armada-header-pull-state",
                                  "data-tone": pull.passed ? "success" : undefined,
                              },
                              [pull.state],
                          ),
                      ],
                      {on: {click: pull.on_press}},
                  ),
            commit === undefined
                ? undefined
                : n(
                      "button",
                      {
                          type: "button",
                          class: "armada-header-fact armada-hover",
                          "data-fact": "commit",
                          "aria-label": commit.label,
                          "data-tip": commit.summary,
                      },
                      [
                          n("span", {class: "armada-header-by"}, [commit.by]),
                          n("span", {class: "armada-header-summary"}, [commit.summary]),
                          n("code", {}, [commit.sha]),
                          n("span", {class: "armada-header-age"}, [
                              cell(commit.age, [commit.age_sizer], {align: "end"}),
                          ]),
                      ],
                      {on: {click: commit.on_press}},
                  ),
        ]),
        n("div", {class: "armada-header-line2"}, [
            props.views === undefined ? undefined : views(props.views),
            props.say === undefined ? undefined : n("p", {class: "armada-header-say"}, [props.say]),
            n("span", {class: "armada-header-end"}, [
                glyph_control({...props.more, glyph: "more"}),
                props.heading === undefined ? undefined : heading(props.heading),
            ]),
        ]),
    ]);
}

// ---- band

export type BandProps = {
    // A person typing: their face on the feed's face axis and a bubble of
    // their stone where their message will be.
    typing?: {people: FaceProps[]; stone?: Stone | undefined; names: string} | undefined;
    run?:
        | {
              // The run's mark; never the sigil, and never the working mark,
              // which is the head of the stream and the run's one loop.
              mark: MarkName;
              word: string;
              // The clock, 8 px after the word; counted in fixed cells.
              clock?: string | undefined;
              // quiet: no output for a while. asks: it needs an answer,
              // and holds no Stop over the field.
              kind?: "quiet" | "asks" | undefined;
              stop?: (Common & {label: string}) | undefined;
              // The step's activity, while the column is folded or absent.
              activity?: string | undefined;
          }
        | undefined;
    // Jump to latest keeps its cell whether or not it shows.
    jump?: (Common & {label: string; shown: boolean}) | undefined;
    // Something reversible was done, or the network is away.
    notice?: {text: string; verb?: (Common & {label: string}) | undefined} | undefined;
};

export function band(props: BandProps): Tree {
    const {typing, run, jump, notice} = props;
    return n("div", {class: "armada-band", "data-jump": jump?.shown}, [
        typing === undefined
            ? undefined
            : n("span", {class: "armada-band-typing", "data-chip": run !== undefined}, [
                  typing.people.length > 1
                      ? faces(typing.people, {deep: true})
                      : face(typing.people[0]!),
                  n(
                      "p",
                      {
                          class: "armada-typing-bubble",
                          "data-stone": typing.people.length === 1 ? typing.stone : undefined,
                      },
                      [typing_dots({small: run !== undefined})],
                  ),
                  n("span", {class: "armada-band-name"}, [typing.names]),
              ]),
        notice === undefined
            ? undefined
            : n("span", {class: "armada-band-undo"}, [
                  n("span", {}, [notice.text]),
                  notice.verb === undefined ? undefined : text_control(notice.verb),
              ]),
        run === undefined || notice !== undefined
            ? undefined
            : n("span", {class: "armada-band-run", "data-run": run.kind}, [
                  mark(run.mark),
                  n("span", {class: "armada-band-state"}, [
                      n("span", {class: "armada-band-word"}, [run.word]),
                      run.clock === undefined
                          ? undefined
                          : n("span", {class: "armada-band-clock"}, [figure(run.clock)]),
                  ]),
                  run.stop === undefined || run.kind === "asks"
                      ? undefined
                      : stop_control({...run.stop, glyph: "stop"}),
                  n("span", {class: "armada-band-activity"}, [run.activity]),
              ]),
        jump === undefined
            ? undefined
            : stop_control({...jump, glyph: "latest", role: "jump", off: !jump.shown}),
    ]);
}

// ---- field

export type FieldProps = {
    // The field's name for assistive technology.
    label: string;
    placeholder: string;
    value?: string | undefined;
    send: SendProps;
    plus?: (Common & {label: string}) | undefined;
    // Who will answer: the sigil whole when the message will reach the
    // agent, its outline when it is for people only. Absent where there
    // is no agent.
    agent?: (Common & {label: string; answers: boolean}) | undefined;
    // The model and effort for the next turn, above the field's right end
    // in a cell as wide as its longest form.
    mode?: (Common & {label: string; text: string; sizer: string; open?: boolean}) | undefined;
    attachment?: string | undefined;
    // One sentence in place of the placeholder.
    disabled?: boolean | undefined;
    // A thread's field: flat until it holds focus.
    flat?: boolean | undefined;
    force?: "focus" | undefined;
    on_input?: Handler | undefined;
    on_keydown?: Handler | undefined;
};

// Whether the field holds words is state the stylesheet reads: the
// placeholder, the caret's indent and the default button's colour follow
// it in the frame of the key, with no layout read.
function mark_filled(event: Event): void {
    const input = event.currentTarget;
    if (input instanceof HTMLTextAreaElement) {
        input.closest(".armada-field")?.toggleAttribute("data-filled", input.value !== "");
    }
}

export function field(props: FieldProps): Tree {
    const {agent, mode} = props;
    const value = props.value ?? "";
    return n(
        "div",
        {
            class: "armada-field",
            "data-filled": value !== "",
            "data-disabled": props.disabled,
            "data-flat": props.flat,
            "data-force": props.force,
        },
        [
            n("div", {class: "armada-field-shape"}, [
                props.plus === undefined
                    ? undefined
                    : n(
                          "button",
                          {
                              type: "button",
                              class: "armada-field-plus armada-hover",
                              "data-hover": "square",
                              "aria-label": props.plus.label,
                              "data-tip": props.plus.tip ?? props.plus.label,
                              disabled: props.disabled,
                          },
                          [glyph("plus")],
                          {on: {click: props.plus.on_press}},
                      ),
                n("div", {class: "armada-field-words"}, [
                    props.attachment === undefined
                        ? undefined
                        : n("span", {class: "armada-field-attachment"}, [
                              glyph("file"),
                              n("span", {}, [props.attachment]),
                          ]),
                    n("div", {class: "armada-field-text"}, [
                        n("span", {class: "armada-field-placeholder", "aria-hidden": "true"}, [
                            props.placeholder,
                        ]),
                        n(
                            "textarea",
                            {
                                class: "armada-field-input",
                                rows: 1,
                                "aria-label": props.label,
                                "aria-placeholder": props.placeholder,
                                disabled: props.disabled,
                            },
                            [value],
                            {
                                on: {
                                    input(event) {
                                        mark_filled(event);
                                        props.on_input?.(event);
                                    },
                                    keydown: props.on_keydown,
                                },
                            },
                        ),
                    ]),
                ]),
                agent === undefined
                    ? undefined
                    : n(
                          "button",
                          {
                              type: "button",
                              class: "armada-field-agent armada-hover",
                              "data-hover": "square",
                              "aria-label": agent.label,
                              "aria-pressed": String(agent.answers),
                              "data-tip": agent.tip,
                              disabled: props.disabled,
                          },
                          [sigil(agent.answers ? "20" : "20-outline")],
                          {on: {click: agent.on_press}},
                      ),
                mode === undefined
                    ? undefined
                    : n(
                          "button",
                          {
                              type: "button",
                              class: "armada-field-mode armada-hover",
                              "data-hover": "wide",
                              "data-off": agent?.answers === false,
                              "aria-label": mode.label,
                              "aria-haspopup": "dialog",
                              "aria-expanded": String(mode.open === true),
                              "data-tip": mode.tip ?? mode.label,
                              disabled: props.disabled,
                          },
                          [cell(mode.text, [mode.sizer], {align: "end"}), glyph("down")],
                          {on: {click: mode.on_press}},
                      ),
                send_button({...props.send, disabled: props.disabled ?? props.send.disabled}),
            ]),
        ],
    );
}
