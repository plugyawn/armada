"""The drawings of Armada's kit, as data: the stroked glyphs, the run and
step marks, and the agent's sigil. tools/armada/icons.py turns them into
web/src/armada/kit/icons/sprite.svg and names.ts; nothing else reads this
file, and nothing is drawn anywhere else.

To add a glyph: add a g(...) line to its group, run tools/armada/icons.py,
and look at it on /devtools/armada/ at 1x and at 2x.
"""

from typing import Any


# ---------------------------------------------------------------- stroked glyphs
# 16 px box, 14 px live area. Every horizontal and vertical stroke is centred on a half pixel, so a 1 px stroke
# fills whole device pixels at 1x and a 1.5 px stroke, nudged a quarter pixel by --ic-n, fills three at 2x.
# "s" is stroked geometry; "f" is filled geometry, used only for dots, and always whole-pixel squares.
def R(x: float, y: float, w: float, h: float, rx: float = 0) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}"' + (f' rx="{rx}"' if rx else "") + "/>"


def C(cx: float, cy: float, r: float) -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="{r}"/>'


def P(d: str) -> str:
    return f'<path d="{d}"/>'


# name -> (group, stroked, filled, what it means)
G: dict[str, tuple[str, str, str, str]] = {}


def g(group: str, name: str, s: str, f: str = "", means: str = "") -> None:
    G[name] = (group, s, f, means)


# nav kinds
g("Nav", "search", C(7, 7, 4.5) + P("M10.5 10.5 14 14"), means="Jump to; find in files")
g("Nav", "home", P("M2.5 7.5 8 2.5l5.5 5v6h-11z") + P("M6.5 13.5v-4h3v4"), means="Home")
g(
    "Nav",
    "repo",
    P("M12.5 10.5v-9h-7a2 2 0 0 0-2 2v9") + P("M12.5 14.5h-7a2 2 0 0 1 0-4h7") + P("M10.5 10.5v4"),
    means="repository, folded",
)
g(
    "Nav",
    "repo-open",
    P("M7.5 4C6.3 3 4.5 2.5 1.5 2.5v9c3 0 4.8.5 6 1.5 1.2-1 3-1.5 6-1.5v-9c-3 0-4.8.5-6 1.5z")
    + P("M7.5 4v9"),
    means="repository, unfolded",
)
g(
    "Nav",
    "hash",
    P("M6.5 2.5 4.5 13.5M11.5 2.5 9.5 13.5M3 5.5h11M2 10.5h11"),
    means="channel with no repository",
)
g(
    "Nav",
    "lock",
    R(3.5, 7.5, 9, 6, 1.5) + P("M5.5 7.5V5a2.5 2.5 0 0 1 5 0v2.5"),
    means="private channel",
)
g("Nav", "plus", P("M7.5 2.5v10M2.5 7.5h10"), means="new; add; stage")
g("Nav", "nav", R(1.5, 2.5, 13, 11, 2) + P("M5.5 2.5v11"), means="show or hide the nav")

# header
g(
    "Header",
    "branch",
    C(4.5, 3.5, 2)
    + C(4.5, 12.5, 2)
    + C(11.5, 4.5, 2)
    + P("M4.5 5.5v5M11.5 6.5V7A2.5 2.5 0 0 1 9 9.5H4.5"),
    means="branch",
)
g("Header", "commit", C(7.5, 7.5, 3) + P("M1 7.5h3.5M10.5 7.5H14"), means="commit")
g(
    "Header",
    "pr",
    C(3.5, 3.5, 2)
    + C(3.5, 12.5, 2)
    + C(12.5, 12.5, 2)
    + P("M3.5 5.5v5M12.5 10.5v-4a2 2 0 0 0-2-2h-3M10 2 7.5 4.5 10 7"),
    means="pull request",
)
g("Header", "more", "", R(2, 7, 2, 2) + R(7, 7, 2, 2) + R(12, 7, 2, 2), means="more actions")
g("Header", "panel", R(1.5, 2.5, 13, 11, 2) + P("M10.5 2.5v11"), means="show or hide the panel")

# composer
g(
    "Composer",
    "clip",
    P("M11.5 7 7.25 11.25a1.9 1.9 0 0 1-2.7-2.7L9.3 3.8a3 3 0 0 1 4.25 4.25L8.6 13"),
    means="attach",
)
g("Composer", "send", P("M7.5 13.5v-11M3.5 6.5l4-4 4 4"), means="send")
g("Composer", "steer", P("M4.5 13.5v-5h6v-6M7.5 5.5l3-3 3 3"), means="steer the live run")
g(
    "Composer",
    "answer",
    P("M5.5 3.5l-3 3 3 3M2.5 6.5h7a3 3 0 0 1 3 3v4"),
    means="answer the agent's question",
)
g(
    "Composer",
    "queue",
    P("M7.5 9.5v-7M4.5 5.5l3-3 3 3M3.5 11.5h8M3.5 13.5h8"),
    means="queue behind the run",
)
g("Composer", "stop", "", R(4, 4, 8, 8, 1), means="stop the run")
g("Composer", "down", P("M3.5 5.5l4 4 4-4"), means="opens a picker; expanded")

# message
g(
    "Message",
    "react",
    C(8, 8, 5.5) + P("M5.5 9.5a3 3 0 0 0 5 0"),
    R(6, 6, 1, 2) + R(9, 6, 1, 2),
    means="add a reaction",
)
g(
    "Message",
    "thread",
    P("M4.5 10.5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-4l-3 3z"),
    means="reply in thread",
)
g(
    "Message",
    "copy",
    R(5.5, 5.5, 8, 8, 2) + P("M10.5 5.5v-1a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h1"),
    means="copy",
)
g(
    "Message",
    "link",
    P("M6.75 9.25a2.75 2.75 0 0 0 3.9 0l2.1-2.1a2.75 2.75 0 0 0-3.9-3.9l-.85.85")
    + P("M9.25 6.75a2.75 2.75 0 0 0-3.9 0l-2.1 2.1a2.75 2.75 0 0 0 3.9 3.9l.85-.85"),
    means="copy link",
)
g(
    "Message",
    "edit",
    P("M2.5 13.5l.75-3L10.5 3.25a1.6 1.6 0 0 1 2.25 2.25L5.5 12.75z") + P("M9.25 4.5l2.25 2.25"),
    means="edit; the agent edited a file",
)
g("Message", "retry", P("M13.5 8.5a5.5 5.5 0 1 1-1.6-3.9") + P("M12.5 1.5v3.5H9"), means="retry")
g("Message", "open", P("M6.5 3.5h6v6M12.5 3.5l-8 8"), means="open in the panel or outside")
g("Message", "latest", P("M7.5 2.5v8M3.5 6.5l4 4 4-4M3.5 13.5h8"), means="jump to latest")

# files and artifacts
g(
    "Files",
    "folder",
    P("M1.5 4.5v8a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1H8l-1.5-2h-4a1 1 0 0 0-1 1z"),
    means="folder, shut",
)
g(
    "Files",
    "folder-open",
    P("M1.5 12.5v-8a1 1 0 0 1 1-1h4l1.5 2h3.5a1 1 0 0 1 1 1v1")
    + P("M1.5 12.5l2-5h11l-2 5.4a1 1 0 0 1-.9.6H2.5a1 1 0 0 1-1-1z"),
    means="folder, open",
)
g("Files", "file", P("M3.5 1.5h6l3 3v10h-9z") + P("M9.5 1.5v3h3"), means="file")
g(
    "Files",
    "doc",
    P("M3.5 1.5h6l3 3v10h-9z") + P("M9.5 1.5v3h3") + P("M5.5 8.5h5M5.5 11.5h3"),
    means="document; README",
)
g("Files", "image", R(2.5, 2.5, 11, 11, 2) + P("M2.5 10.5l3-3 6 6"), R(10, 5, 2, 2), means="image")
g("Files", "table", R(1.5, 1.5, 13, 12, 1) + P("M1.5 5.5h13M1.5 9.5h13M6.5 5.5v8"), means="table")
g("Files", "chart", R(1.5, 8.5, 3, 5) + R(6.5, 2.5, 3, 11) + R(11.5, 5.5, 3, 8), means="chart")
g("Files", "term", P("M2.5 3.5l4 4-4 4M8.5 12.5H14"), means="command; terminal output")

# changes and lists
g("Changes", "diff", P("M7.5 2.5v6M4.5 5.5h6M4.5 12.5h6"), means="changed; a diff")
g("Changes", "minus", P("M2.5 7.5h10"), means="remove; unstage")
g("Changes", "check", P("M2.5 8.5l3 3 7-7"), means="chosen; passed")
g("Changes", "x", P("M3.5 3.5l8 8M11.5 3.5l-8 8"), means="close; dismiss")
g(
    "Changes",
    "expand",
    P("M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"),
    means="give it the pane",
)
g("Changes", "right", P("M5.5 3.5l4 4-4 4"), means="collapsed; goes deeper")

# trace and tools
g("Trace", "trace", P("M1.5 8.5h3l2-5 3 9 2-4h3"), means="the run's trace")
g(
    "Trace",
    "eye",
    P("M1.5 8C3 5 5.2 3.5 8 3.5S13 5 14.5 8c-1.5 3-3.7 4.5-6.5 4.5S3 11 1.5 8z") + C(8, 8, 2),
    means="the agent read; a monitor",
)
g(
    "Trace",
    "globe",
    C(7.5, 7.5, 5)
    + P("M2.5 7.5h10M7.5 2.5c-1.7 1.4-2.5 3-2.5 5s.8 3.6 2.5 5c1.7-1.4 2.5-3 2.5-5s-.8-3.6-2.5-5z"),
    means="the agent fetched from the web",
)

# people and settings
g(
    "Settings",
    "person",
    C(8, 5, 2.5) + P("M2.5 13.5c.5-2.5 2.5-4 5.5-4s5 1.5 5.5 4"),
    means="person; members",
)
g(
    "Settings",
    "settings",
    C(9.5, 4.5, 2) + C(5.5, 11.5, 2) + P("M1.5 4.5h6M11.5 4.5h3M1.5 11.5h2M7.5 11.5h7"),
    means="settings",
)
g(
    "Settings",
    "sound",
    P("M2.5 6.5H5l3.5-3v9L5 9.5H2.5z") + P("M11.5 4.5a4.5 4.5 0 0 1 0 7"),
    means="sounds on",
)
g(
    "Settings",
    "mute",
    P("M2.5 6.5H5l3.5-3v9L5 9.5H2.5z") + P("M10.5 5.5l4 4M14.5 5.5l-4 4"),
    means="sounds off",
)

# ---------------------------------------------------------------- the agent's marks: pixels, 7 x 7 cells of 2 px
PX: dict[str, list[str]] = {
    "done": [".......", "......#", ".....##", "#...##.", "##.##..", ".###...", "..#...."],
    "live": [".......", "..###..", ".#####.", ".#####.", ".#####.", "..###..", "......."],
    "waiting": [".......", ".#####.", ".#...#.", ".#...#.", ".#...#.", ".#####.", "......."],
    "needs": ["...#...", "..###..", ".#####.", "#######", ".#####.", "..###..", "...#..."],
    "failed": ["#.....#", "##...##", ".##.##.", "..###..", ".##.##.", "##...##", "#.....#"],
    "paused": [".......", ".##.##.", ".##.##.", ".##.##.", ".##.##.", ".##.##.", "......."],
    "stopped": [".......", ".......", "#######", "#######", ".......", ".......", "......."],
}


# ---------------------------------------------------------------- the sigil
# A cut stone seen face on: an octagon whose four corner facets are unequal, crossed by tilted beds that climb one
# cell for every two across. The beds are of unequal thickness, as onyx is banded, and never sit on the centre line.
# Drawn again for each size on its own grid; the cell is 2 px at every size, so the big one carries more beds.
def stone(n: int, tl: int, tr: int, bl: int, br: int, beds: set[int], ring: int = 0) -> list[str]:
    """beds: indexes d = y + x // 2 that are cut away. A bed is cut only
    where it crosses at least 70 percent of the stone's width. ring: cells
    of outline that no bed cuts, so the stone is one object with bands
    inside it and never three slabs."""

    def inside(x: int, y: int) -> bool:
        return (
            x + y >= tl
            and x + y <= 2 * (n - 1) - br
            and x - y <= n - 1 - tr
            and y - x <= n - 1 - bl
        )

    span: dict[int, set[int]] = {}
    for y in range(n):
        for x in range(n):
            if inside(x, y):
                span.setdefault(y + x // 2, set()).add(x)
    ok = {d for d, xs in span.items() if len(xs) >= 0.7 * n}
    grid = [
        [
            "." if not inside(x, y) or ((y + x // 2) in beds and (y + x // 2) in ok) else "#"
            for x in range(n)
        ]
        for y in range(n)
    ]
    # No slivers: a dark piece smaller than one cell in forty is dropped.
    seen: set[tuple[int, int]] = set()
    for y in range(n):
        for x in range(n):
            if grid[y][x] != "#" or (x, y) in seen:
                continue
            piece, todo = [], [(x, y)]
            seen.add((x, y))
            while todo:
                cx, cy = todo.pop()
                piece.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    q = (cx + dx, cy + dy)
                    if (
                        0 <= q[0] < n
                        and 0 <= q[1] < n
                        and grid[q[1]][q[0]] == "#"
                        and q not in seen
                    ):
                        seen.add(q)
                        todo.append(q)
            if len(piece) < n * n / 40:
                for cx, cy in piece:
                    grid[cy][cx] = "."
    if ring:
        around = [
            (dx, dy)
            for dx in range(-ring, ring + 1)
            for dy in range(-ring, ring + 1)
            if abs(dx) + abs(dy) <= ring
        ]
        for y in range(n):
            for x in range(n):
                on_edge = any(
                    not (0 <= x + dx < n and 0 <= y + dy < n and inside(x + dx, y + dy))
                    for dx, dy in around
                )
                if inside(x, y) and on_edge:
                    grid[y][x] = "#"
    return ["".join(row) for row in grid]


# key: size in px -> drawing. The cell is 2 px at 20, 28 and 64 (10, 14 and 32 cells), so the stone is the same pixel
# object at every size it is shown. One broad bed and one vein, as onyx is banded: the dark
# mass is the stone, and its outline is never cut, so it reads as one object and never as three slabs.
SIGIL: dict[int, dict[str, Any]] = {
    20: dict(n=10, tl=3, tr=1, bl=1, br=3, beds={3, 4, 8}, ring=1),
    28: dict(n=14, tl=4, tr=1, bl=1, br=4, beds={5, 6, 11}, ring=1),
    64: dict(n=32, tl=9, tr=2, bl=2, br=9, beds={13, 14, 15, 26, 27}, ring=2),
}


def sigil_rows(size: int) -> list[str]:
    return stone(**SIGIL[size])
