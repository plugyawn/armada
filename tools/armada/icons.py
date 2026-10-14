#!/usr/bin/env python3
"""Armada's sprite: every glyph and mark the interface draws, in one file.

    tools/armada/icons.py            write the sprite and its names
    tools/armada/icons.py --check    fail if either file is not what this writes

Writes web/src/armada/kit/icons/sprite.svg, one <symbol> a drawing, and
names.ts, the typed names a component uses them by. The drawings are data
in icons_gen.py. Two systems that borrow nothing from each other: strokes
for what people use (49 glyphs, 16 px box, one weight on whole device
pixels), pixels for what the agent is doing (seven marks, the working mark
as one sprite of four steps, the sigil at 20, 28 and 64 with its outline at
20). A person's typing dot is the one round thing.
"""

import argparse
import json
import os
import sys

import icons_gen as g

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "web", "src", "armada", "kit", "icons")
SPRITE_LIMIT = 16 * 1024
WORKING_STEPS = 4


def hollow(size: int) -> list[str]:
    """The same stone with nothing in it: its outline, one cell thick."""
    rows = g.stone(**{**g.SIGIL[size], "beds": set(), "ring": 0})
    n = len(rows)

    def inside(x: int, y: int) -> bool:
        return 0 <= x < n and 0 <= y < n and rows[y][x] == "#"

    around = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
    return [
        "".join(
            "#" if inside(x, y) and not all(inside(x + dx, y + dy) for dx, dy in around) else "."
            for x in range(n)
        )
        for y in range(n)
    ]


def working_rows() -> list[str]:
    """The live stone with one bed stepping down through it: three steps
    with the bed on rows 2, 3 and 4, then the whole stone, side by side.
    One sprite moved in steps, so no frame of the loop can be blank."""
    live = g.PX["live"]
    steps = [
        [("......." if y == bed else row) for y, row in enumerate(live)] for bed in (2, 3, 4, None)
    ]
    assert len(steps) == WORKING_STEPS
    return ["".join(step[y] for step in steps) for y in range(7)]


def cells(rows: list[str]) -> str:
    """Pixel rows as one path: a rectangle for each run of cells in a row."""
    d = ""
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            if row[x] != "#":
                x += 1
                continue
            start = x
            while x < len(row) and row[x] == "#":
                x += 1
            d += f"M{start} {y}h{x - start}v1h-{x - start}z"
    return f'<path d="{d}"/>'


def symbol(name: str, width: int, height: int, body: str, crisp: bool = False) -> str:
    crisp_attribute = ' shape-rendering="crispEdges"' if crisp else ""
    return f'<symbol id="armada-{name}" viewBox="0 0 {width} {height}"{crisp_attribute}>{body}</symbol>'


def sigils() -> dict[str, list[str]]:
    drawings = {str(size): g.sigil_rows(size) for size in g.SIGIL}
    drawings["20-outline"] = hollow(20)
    return drawings


def sprite() -> str:
    # Inlined once in a page, it takes no room and draws nothing itself.
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true">'
    ]
    for name, (_group, stroked, filled, _means) in g.G.items():
        # Strokes carry the stylesheet's nudge, which puts a 1.5 px stroke on
        # whole device pixels at 2x; fills are whole-pixel squares and stay.
        body = f'<g class="armada-glyph-stroke">{stroked}</g>' if stroked else ""
        if filled:
            body += f'<g fill="currentColor" stroke="none">{filled}</g>'
        out.append(symbol(f"glyph-{name}", 16, 16, body))
    for name, rows in g.PX.items():
        out.append(symbol(f"mark-{name}", 7, 7, cells(rows), crisp=True))
    out.append(symbol("mark-working", 7 * WORKING_STEPS, 7, cells(working_rows()), crisp=True))
    for name, rows in sigils().items():
        out.append(symbol(f"sigil-{name}", len(rows), len(rows), cells(rows), crisp=True))
    out.append(symbol("dot", 6, 6, '<circle cx="3" cy="3" r="3"/>'))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def names() -> str:
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, (group, _stroked, _filled, means) in g.G.items():
        groups.setdefault(group, []).append((name, means))
    q = json.dumps
    out = [
        "// Written by tools/armada/icons.py from tools/armada/icons_gen.py; never",
        "// edit it by hand. The names of the drawings in sprite.svg: a component",
        "// uses a glyph, a mark or the sigil by one of these and draws none itself.",
        "",
        "export const glyph_groups = [",
    ]
    for group, glyphs in groups.items():
        out += ["    {", f"        group: {q(group)},", "        glyphs: ["]
        for name, means in glyphs:
            out += [
                "            {",
                f"                name: {q(name)},",
                f"                means: {q(means)},",
                "            },",
            ]
        out += ["        ],", "    },"]
    out += [
        "] as const;",
        "",
        'export type GlyphName = (typeof glyph_groups)[number]["glyphs"][number]["name"];',
        "",
        "export const mark_names = [",
        *(f"    {q(name)}," for name in g.PX),
        "] as const;",
        "",
        "export type MarkName = (typeof mark_names)[number];",
        "",
        f"export const sigil_names = [{', '.join(q(name) for name in sigils())}] as const;",
        "",
        "export type SigilName = (typeof sigil_names)[number];",
        "",
        "// The working mark is one sprite of this many drawings side by side.",
        f"export const working_steps = {WORKING_STEPS};",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="fail if a file is not what this writes"
    )
    parser.add_argument("--quiet", action="store_true", help="print failures only")
    options = parser.parse_args()

    files = {"sprite.svg": sprite(), "names.ts": names()}
    failed = False
    size = len(files["sprite.svg"].encode())
    if size > SPRITE_LIMIT:
        print(f"sprite.svg is {size} bytes; the limit is {SPRITE_LIMIT}")
        failed = True
    for name, text in files.items():
        path = os.path.normpath(os.path.join(OUT, name))
        if options.check:
            current = None
            if os.path.exists(path):
                with open(path) as f:
                    current = f.read()
            if current != text:
                print(f"{os.path.relpath(path)} is not what tools/armada/icons.py writes; run it")
                failed = True
        else:
            with open(path, "w") as f:
                f.write(text)
    if not options.quiet:
        print(
            f"{len(g.G)} glyphs in {len({group for group, *_ in g.G.values()})} groups, "
            f"{len(g.PX)} marks, the working mark in {WORKING_STEPS} steps, "
            f"{len(sigils())} sigil drawings, the dot; sprite.svg {size} bytes "
            f"(limit {SPRITE_LIMIT})"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
