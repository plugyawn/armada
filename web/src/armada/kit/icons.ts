// Glyphs, marks, the sigil and faces: every drawing is a symbol of
// icons/sprite.svg, used by its generated name. Nothing is drawn here.

import type {GlyphName, MarkName, SigilName} from "./icons/names.ts";
import {n} from "./tree.ts";
import type {Tree} from "./tree.ts";

function use(id: string, attributes: Record<string, number> = {}): Tree {
    return n("use", {href: `#armada-${id}`, ...attributes});
}

// A stroked glyph: 16 px, never scaled, except the tick in a checkbox.
export function glyph(name: GlyphName, options: {size?: 12 | undefined} = {}): Tree {
    return n("svg", {class: "armada-glyph", "aria-hidden": "true", "data-size": options.size}, [
        use(`glyph-${name}`),
    ]);
}

// A run or step mark: 14 px; 28 on Home's rows that need me. Told apart by
// shape; colour only on needs-you and failed, and on a passed check.
export function mark(
    name: MarkName,
    options: {size?: 28 | undefined; tone?: "success" | undefined} = {},
): Tree {
    return n(
        "svg",
        {
            class: "armada-mark",
            "aria-hidden": "true",
            "data-mark": name,
            "data-size": options.size,
            "data-tone": options.tone,
        },
        [use(`mark-${name}`)],
    );
}

// The working mark: the one loop a live run has. One element for the life
// of an answer; at rest it is the whole stone.
export function working_mark(options: {live: boolean}): Tree {
    return n(
        "svg",
        {
            class: "armada-mark",
            viewBox: "0 0 7 7",
            "aria-hidden": "true",
            "data-mark": "live",
            "data-working": options.live ? "live" : "rest",
        },
        [use("mark-working", {width: 28, height: 7})],
    );
}

// The head of the stream: the working mark where the next word will be,
// taking no room.
export function stream_head(options: {first?: boolean | undefined} = {}): Tree {
    return n("span", {class: "armada-head", "data-first": options.first}, [
        working_mark({live: true}),
    ]);
}

// The sigil: the agent, and nothing else. 28 where a person shows a face,
// 20 in a list and in the field (its outline when the agent will not
// answer), 64 on an empty worktree. It never loops.
export function sigil(name: SigilName): Tree {
    return n("svg", {class: "armada-sigil", "aria-hidden": "true", "data-size": name.slice(0, 2)}, [
        use(`sigil-${name}`),
    ]);
}

// A person typing: three round dots that grow and darken in turn.
export function typing_dots(options: {small?: boolean | undefined} = {}): Tree {
    return n(
        "svg",
        {
            class: "armada-dots",
            viewBox: "0 0 26 6",
            "aria-hidden": "true",
            "data-size": options.small ? "small" : undefined,
        },
        [0, 10, 20].map((x) => use("dot", {x, width: 6, height: 6})),
    );
}

export const stones = ["moss", "larimar", "lapis", "amethyst", "rose", "flint"] as const;
export type Stone = (typeof stones)[number];

// Six stones, assigned by user id, the same wherever that person shows.
export function stone_of(user_id: number): Stone {
    return stones[Math.abs(Math.trunc(user_id)) % stones.length]!;
}

export type FaceProps = {
    initials: string;
    stone: Stone | "mine";
    size?: 28 | 20 | 18 | 14 | undefined;
    presence?: "here" | "away" | undefined;
    // A face beside its person's name is decoration; alone it is named.
    name?: string | undefined;
    photo?: string | undefined;
};

export function face(props: FaceProps): Tree {
    const size = props.size ?? 28;
    // A 14 px disc is too small for a letter; a small one holds one.
    const letters = size === 14 ? "" : size === 28 ? props.initials : props.initials.slice(0, 1);
    return n(
        "i",
        {
            class: "armada-face",
            "data-stone": props.stone,
            "data-size": size === 28 ? undefined : size,
            "data-presence": props.presence,
            role: props.name === undefined ? undefined : "img",
            "aria-label": props.name,
            "aria-hidden": props.name === undefined ? "true" : undefined,
        },
        [props.photo === undefined ? letters : n("img", {src: props.photo, alt: ""})],
    );
}

// A group is overlapped faces, never two people's initials in one disc.
export function faces(
    list: FaceProps[],
    options: {size?: FaceProps["size"]; deep?: boolean | undefined} = {},
): Tree {
    return n(
        "span",
        {class: "armada-faces", "data-overlap": options.deep ? "deep" : undefined},
        list.map((props) => face({...props, size: options.size ?? props.size})),
    );
}

// A figure in fixed cells: digits one width, punctuation half of it,
// letters their own. A figure that changes keeps its place, and
// motion.figure moves only the characters that changed.
export function figure(value: string | number): Tree {
    return n(
        "span",
        {class: "armada-figure"},
        [...String(value)].map((character) =>
            n(
                "span",
                {
                    "data-cell": /\d/.test(character)
                        ? undefined
                        : /[A-Za-z−+]/.test(character)
                          ? "word"
                          : "mark",
                },
                [character],
            ),
        ),
    );
}

// A value in a cell as wide as its longest form: the value can change and
// nothing beside it moves.
export function cell(
    value: Tree | string,
    sizers: string[],
    options: {align?: "start" | "end" | undefined} = {},
): Tree {
    return n("span", {class: "armada-cell", "data-align": options.align}, [
        typeof value === "string" ? n("span", {}, [value]) : value,
        sizers.map((sizer) => n("i", {"data-sizer": true, "aria-hidden": "true"}, [sizer])),
    ]);
}
