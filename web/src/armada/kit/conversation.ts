// The conversation: people in bubbles in their own stone, the agent's
// words on the plane. These are the pieces the feed builds its rows from.

import {glyph_control, text_control} from "./controls.ts";
import type {Common} from "./controls.ts";
import type {GlyphName, MarkName} from "./icons/names.ts";
import {face, faces, glyph, mark, sigil} from "./icons.ts";
import type {FaceProps, Stone} from "./icons.ts";
import {n} from "./tree.ts";
import type {Child, Force, Handler, Tree} from "./tree.ts";

// The lane: the conversation's column. Every shape of the conversation
// starts on its left edge or ends on its right.
export function lane(children: Child[]): Tree {
    return n("div", {class: "armada-lane"}, children);
}

export type TurnProps = {
    side: "theirs" | "mine" | "agent";
    stone?: Stone | undefined;
    // A person's face stands in the gutter on every turn; the agent's
    // sigil stands there on its own.
    face?: FaceProps | undefined;
    // A name when the speaker on that side changes.
    name?: string | undefined;
    live?: boolean | undefined;
    children: Child[];
};

// A turn: one speaker's run of bubbles, words and blocks.
export function turn(props: TurnProps): Tree {
    return n(
        "section",
        {
            class: "armada-turn",
            "data-side": props.side,
            "data-stone": props.stone,
            "data-named": props.name !== undefined,
            "data-live": props.live,
        },
        [
            props.side === "agent"
                ? sigil("28")
                : props.face === undefined
                  ? undefined
                  : face(props.face),
            props.name === undefined
                ? undefined
                : n("div", {class: "armada-turn-name"}, [props.name]),
            props.children,
        ],
    );
}

export type Reaction = {
    emoji: string;
    // What a screen reader says: who reacted with what.
    label: string;
    count?: number | undefined;
    mine?: boolean | undefined;
    on_press?: Handler | undefined;
    force?: Force | undefined;
};

// Real emoji in 20 px chips. After four kinds, one chip counts the rest.
export function reactions(list: Reaction[], more?: {label: string; count: number}): Tree {
    return n("span", {class: "armada-reactions"}, [
        list.map((reaction) =>
            n(
                "button",
                {
                    type: "button",
                    class: "armada-reaction armada-hover",
                    "aria-pressed": String(reaction.mine === true),
                    "aria-label": reaction.label,
                    "data-force": reaction.force,
                },
                [
                    n("span", {class: "armada-reaction-emoji"}, [reaction.emoji]),
                    reaction.count !== undefined && reaction.count > 1
                        ? n("span", {}, [reaction.count])
                        : undefined,
                ],
                {on: {click: reaction.on_press}},
            ),
        ),
        more === undefined
            ? undefined
            : n(
                  "button",
                  {
                      type: "button",
                      class: "armada-reaction armada-hover",
                      "aria-pressed": "false",
                      "aria-label": more.label,
                  },
                  [n("span", {}, [`+${more.count}`])],
              ),
    ]);
}

export type BubbleProps = {
    content: Child[];
    // Stacked bubbles close their facing corners on the speaker's side.
    join?: "above" | "below" | "above below" | undefined;
    kind?: "jumbo" | "gone" | "unsent" | "image" | undefined;
    time?: string | undefined;
    // In the time's place: Edited, Not sent.
    meta?: {text: string; tone?: "failure" | undefined} | undefined;
    reactions?: Tree | undefined;
    // I reacted to it: it keeps a faint wash from where the chip sits.
    liked?: boolean | undefined;
    // One verb beside it: Retry.
    verb?: (Common & {label: string}) | undefined;
    overlay?: Tree | undefined;
};

// A bubble and what rides beside it on its last line: reactions seated on
// its outer edge, then the time.
export function bubble(props: BubbleProps): Tree {
    return n("div", {class: "armada-bubble-row"}, [
        n(
            "p",
            {
                class: "armada-bubble",
                "data-join": props.join,
                "data-kind": props.kind,
                "data-liked": props.liked,
            },
            [props.content, props.overlay],
        ),
        props.reactions,
        props.verb === undefined ? undefined : text_control(props.verb),
        props.meta === undefined
            ? undefined
            : n("span", {class: "armada-time", "data-tone": props.meta.tone}, [props.meta.text]),
        props.time === undefined ? undefined : n("time", {class: "armada-time"}, [props.time]),
    ]);
}

// The agent's words: on the plane, at the text edge.
export function answer(content: Child[]): Tree {
    return n("div", {class: "armada-answer"}, content);
}

// On the agent's words the chips follow the turn's last word, on its line;
// on a turn that ends in a block they stand on the block's top right
// corner.
export function reaction_anchor(chips: Tree): Tree {
    return n("span", {class: "armada-reaction-anchor"}, [chips]);
}

export function reaction_corner(chips: Tree): Tree {
    return n("div", {class: "armada-reaction-corner"}, [chips]);
}

// A mention is Strong; a mention of me is Strong in my colour.
export function mention(props: {name: string; me?: boolean | undefined}): Tree {
    return n("span", {class: "armada-mention", "data-me": props.me}, [props.name]);
}

// One Caption line under a bubble: steered, queued, edited.
export function receipt(text: string): Tree {
    return n("div", {class: "armada-receipt"}, [text]);
}

// The thread line: who replied, the count as the control, the last
// reply's first words.
export function thread_line(props: {
    faces: FaceProps[];
    // Pressed while the thread stands open under its root.
    count: Common & {label: string; pressed?: boolean | undefined};
    snippet: string;
}): Tree {
    return n("div", {class: "armada-thread-line"}, [
        faces(props.faces, {size: 18}),
        text_control(props.count),
        n("span", {class: "armada-thread-snippet"}, [props.snippet]),
    ]);
}

// A thread opened where it is: its replies under the root bubble, set in
// from it, on one stone line.
export function thread(children: Child[]): Tree {
    return n("div", {class: "armada-thread"}, children);
}

export function day(label: string): Tree {
    return n("div", {class: "armada-day", role: "separator"}, [label]);
}

export function new_line(label: string): Tree {
    return n("div", {class: "armada-new", role: "separator"}, [label]);
}

// A run's footer: one line under a turn that did work, with at most one
// verb. The failed and the stopped keep their mark.
export function run_footer(props: {
    text: string;
    mark?: MarkName | undefined;
    tone?: "failure" | undefined;
    verb?: (Common & {label: string}) | undefined;
}): Tree {
    return n("div", {class: "armada-footer"}, [
        props.mark === undefined ? undefined : mark(props.mark),
        n("span", {class: "armada-footer-text", "data-tone": props.tone}, [props.text]),
        props.verb === undefined ? undefined : text_control(props.verb),
    ]);
}

// The hover overlay: one raised object with at most four glyph controls,
// on the bubble's open side. Never in flow.
export function overlay(
    controls: (Common & {glyph: GlyphName; label: string})[],
    options: {label: string},
): Tree {
    return n(
        "span",
        {class: "armada-overlay", role: "toolbar", "aria-label": options.label},
        controls.map((control) => glyph_control({...control, small: true})),
    );
}

// A link's preview: a block of its own under the bubble.
export function link_preview(props: {title: string; caption: string; glyph: GlyphName}): Tree {
    return n("div", {class: "armada-preview"}, [
        n("div", {}, [
            n("b", {class: "armada-preview-title"}, [props.title]),
            n("span", {class: "armada-preview-caption"}, [props.caption]),
        ]),
        n("span", {class: "armada-preview-thumb"}, [glyph(props.glyph)]),
    ]);
}

// An empty worktree: the one place the 64 px sigil lives.
export function hello(children: Child[]): Tree {
    return n("div", {class: "armada-hello"}, [sigil("64"), children]);
}
