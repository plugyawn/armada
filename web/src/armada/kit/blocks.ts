// Blocks: what a machine would accept, and artifacts. Beside talk, never
// inside it.

import {option, text_control} from "./controls.ts";
import type {Common} from "./controls.ts";
import type {GlyphName} from "./icons/names.ts";
import {figure, glyph} from "./icons.ts";
import {n} from "./tree.ts";
import type {Child, Tree} from "./tree.ts";

export type SyntaxRole = "keyword" | "string" | "number" | "comment";
// Code is text, with the parts that have one of the four roles marked.
export type Code = (string | [SyntaxRole, string])[];

export function code(parts: Code): Child[] {
    return parts.map((part) =>
        typeof part === "string" ? part : n("span", {class: `armada-syntax-${part[0]}`}, [part[1]]),
    );
}

export function block(children: Child[]): Tree {
    return n("div", {class: "armada-block"}, children);
}

export function code_block(parts: Code): Tree {
    return n("pre", {class: "armada-block", "data-kind": "code"}, code(parts));
}

export type DiffLine =
    | {kind: "hunk"; text: string}
    | {
          kind: "context" | "added" | "removed";
          old?: number | undefined;
          new?: number | undefined;
          code: Code;
          selected?: boolean | undefined;
      };

const DIFF_SIGN = {context: " ", added: "+", removed: "−"};

// One grid for every line: old number, new number, a sign, the code.
export function diff_block(lines: DiffLine[]): Tree {
    return n(
        "div",
        {class: "armada-block", "data-kind": "diff"},
        lines.map((line) =>
            line.kind === "hunk"
                ? n("div", {class: "armada-diff-line", "data-change": "hunk"}, [
                      n("span", {}, [line.text]),
                  ])
                : n(
                      "div",
                      {
                          class: "armada-diff-line",
                          "data-change": line.kind === "context" ? undefined : line.kind,
                          "aria-selected": line.selected ? "true" : undefined,
                      },
                      [
                          n("i", {}, [line.old]),
                          n("i", {}, [line.new]),
                          n("b", {}, [DIFF_SIGN[line.kind]]),
                          n("span", {}, code(line.code)),
                      ],
                  ),
        ),
    );
}

// Text with figures in fixed cells, the head in the second ink over the
// one permitted separator.
export function table_block(props: {
    head: {label: string; figures?: boolean | undefined}[];
    rows: (Child | number)[][];
}): Tree {
    const align = (column: number): string | undefined =>
        props.head[column]?.figures ? "end" : undefined;
    return n("div", {class: "armada-block"}, [
        n("table", {class: "armada-table"}, [
            n("thead", {}, [
                n(
                    "tr",
                    {},
                    props.head.map((column, index) =>
                        n("th", {"data-align": align(index)}, [column.label]),
                    ),
                ),
            ]),
            n(
                "tbody",
                {},
                props.rows.map((row) =>
                    n(
                        "tr",
                        {},
                        row.map((value, index) =>
                            n("td", {"data-align": align(index)}, [
                                typeof value === "number" ? figure(value) : value,
                            ]),
                        ),
                    ),
                ),
            ),
        ]),
    ]);
}

// An artifact: a kind glyph, a title, one caption, one verb, 36 high.
export function artifact(props: {
    glyph: GlyphName;
    title: string;
    caption: string;
    verb: Common & {label: string};
}): Tree {
    return n("div", {class: "armada-block", "data-kind": "artifact"}, [
        glyph(props.glyph),
        n("span", {}, [props.title]),
        n("span", {class: "armada-artifact-caption"}, [props.caption]),
        text_control(props.verb),
    ]);
}

// A question: its options are buttons under the question, 8 apart.
export function question(props: {
    label: string;
    options: (Common & {label: string; selected?: boolean | undefined})[];
}): Tree {
    return n(
        "div",
        {class: "armada-question", role: "group", "aria-label": props.label},
        props.options.map((choice, index) => option({...choice, number: index + 1})),
    );
}

// Source: a number in the code face at 12, then the code.
export function source(lines: {number: number; code: Code; added?: boolean | undefined}[]): Tree {
    return n(
        "div",
        {class: "armada-source"},
        lines.map((line) =>
            n(
                "div",
                {class: "armada-source-line", "data-change": line.added ? "added" : undefined},
                [n("i", {}, [line.number]), n("span", {}, code(line.code))],
            ),
        ),
    );
}
