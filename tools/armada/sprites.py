#!/usr/bin/env python3
"""Write the partner pack's species.json from the git-ignored sprite directory.

    tools/armada/sprites.py [--dir DIR]

DIR is $ARMADA_SPRITE_DIR, else web/sprites/licensed/ in this checkout
(web/sprites/licensed/README.md says what it holds and where each file
comes from). The script reads, and never executes:

- showdown/pokedex.ts: species, types and evolutions (read as text);
- pokesprite/data/pokemon.json: the sprite slug of each species of
  generations 1 to 8;
- the sprites themselves: pokesprite/pokemon-gen8/{regular,shiny}/ and
  smogon-gen9/, whose content boxes it measures.

and writes DIR/species.json. The output names Pokémon, which are
trademarks, so it lives in the ignored directory with the sprites and is
never committed (the same rule as the sprites).

species.json:

    {"version": 1, "count": <base species>, "starters": [ids...],
     "species": {"<id>": {
        "num", "name", "form", "gen", "types",
        "prevo": id | null, "evos": [ids], "how": {...} | null,
        "line": <the root's id>, "first": bool, "offer": bool,
        "sprite": null | {"regular": path, "shiny": path | null,
                          "size": [w, h], "box": [x, y, w, h],
                          "shiny_box": [x, y, w, h] | null}}}}

An id is the national number ("4") or the number and a regional form
("37-alola"). "how" says how a form is reached from its prevo, as the
games do it ({"type": "level", "level": 16}, {"type": "useItem", "item":
"Fire Stone"}, ...); Armada's own levels do not use it. A form with no
sprite is kept (its line needs it) and is never offered; "offer" marks
the forms the picker offers: drawn, with nothing drawn before them in
their line.

Content boxes are measured with a small PNG reader below (the standard
library only), so the script runs on any machine with Python 3.
"""

import argparse
import json
import os
import re
import struct
import sys
import zlib
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The regional forms that are species in their own right: they have their
# own lines (Vulpix-Alola evolves into Ninetales-Alola, Meowth-Galar into
# Perrserker). Every other form (Megas, Gigantamax, cosmetic forms) is left out.
REGIONAL = {
    "Alola": "Alolan",
    "Galar": "Galarian",
    "Hisui": "Hisuian",
    "Paldea": "Paldean",
    "Paldea-Combat": "Paldean",
    "Paldea-Blaze": "Paldean",
    "Paldea-Aqua": "Paldean",
}
# Pikachu's regional "forms" are caps it wears, not species.
CAP_ONLY = {25}
LAST_NUMBER = 1025
GENERATION_ENDS = [151, 251, 386, 493, 649, 721, 809, 905, 1025]
# The starters of the nine generations, then Pichu and Eevee, the series'
# two partner Pokémon: the picker shows these first.
STARTERS = [
    1, 4, 7, 152, 155, 158, 252, 255, 258, 387, 390, 393, 495, 498, 501,
    650, 653, 656, 722, 725, 728, 810, 813, 816, 906, 909, 912, 172, 133,
]  # fmt: skip


def read_png_alpha_box(path: str) -> tuple[tuple[int, int], tuple[int, int, int, int]]:
    """The image's size and the box of its pixels that are not fully
    transparent, as (x, y, w, h). An image with no transparency is its
    whole canvas; an empty one is (0, 0, 0, 0)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos = 8
    idat = []
    palette_alpha = b""
    transparent: tuple[int, ...] | None = None
    width = height = depth = color = interlace = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"tRNS":
            if color == 3:
                palette_alpha = body
            else:
                transparent = struct.unpack(f">{len(body) // 2}H", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if interlace:
        raise ValueError(f"{path}: interlaced PNGs are not read")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    bits = channels * depth
    stride = (width * bits + 7) // 8
    step = max(1, bits // 8)
    raw = zlib.decompress(b"".join(idat))
    rows: list[bytearray] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        kind_byte = raw[offset]
        line = bytearray(raw[offset + 1 : offset + 1 + stride])
        offset += 1 + stride
        for i in range(stride):
            left = line[i - step] if i >= step else 0
            up = previous[i]
            corner = previous[i - step] if i >= step else 0
            if kind_byte == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind_byte == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind_byte == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif kind_byte == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else corner
                line[i] = (line[i] + predictor) & 0xFF
        rows.append(line)
        previous = line

    def samples(line: bytearray) -> list[int]:
        if depth == 8:
            return list(line)
        if depth == 16:
            return [(line[i] << 8) | line[i + 1] for i in range(0, len(line), 2)]
        mask = (1 << depth) - 1
        return [(byte >> shift) & mask for byte in line for shift in range(8 - depth, -1, -depth)]

    def opaque(values: list[int], x: int) -> bool:
        if color == 6:
            return values[x * 4 + 3] > 0
        if color == 4:
            return values[x * 2 + 1] > 0
        if color == 3:
            index = values[x]
            return index >= len(palette_alpha) or palette_alpha[index] > 0
        pixel = tuple(values[x * channels : (x + 1) * channels])
        return transparent is None or pixel != transparent

    left_most, top_most, right_most, bottom_most = width, height, -1, -1
    for y, line in enumerate(rows):
        values = samples(line)
        for x in range(width):
            if opaque(values, x):
                left_most = min(left_most, x)
                right_most = max(right_most, x)
                top_most = min(top_most, y)
                bottom_most = y
    if right_most < 0:
        return (width, height), (0, 0, 0, 0)
    return (width, height), (
        left_most,
        top_most,
        right_most - left_most + 1,
        bottom_most - top_most + 1,
    )


def parse_pokedex(text: str) -> list[dict[str, Any]]:
    """Showdown's data/pokedex.ts is a TypeScript object literal: one entry
    a species or form, one field a line. Only the fields this script uses
    are read, each with a pattern; the file is data and is never run."""
    entries = []
    for block in re.finditer(r"^\t[a-z0-9]+: \{\n(.*?)^\t\},?$", text, re.MULTILINE | re.DOTALL):
        body = block.group(1)

        def string(field: str, body: str = body) -> str | None:
            m = re.search(rf'^\t\t{field}: "((?:[^"\\]|\\.)*)",$', body, re.MULTILINE)
            return m.group(1).replace('\\"', '"') if m else None

        def strings(field: str, body: str = body) -> list[str]:
            m = re.search(rf"^\t\t{field}: \[(.*?)\],$", body, re.MULTILINE | re.DOTALL)
            return re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)) if m else []

        number = re.search(r"^\t\tnum: (-?\d+),$", body, re.MULTILINE)
        level = re.search(r"^\t\tevoLevel: (\d+),$", body, re.MULTILINE)
        if number is None:
            continue
        entries.append(
            {
                "num": int(number.group(1)),
                "name": string("name"),
                "forme": string("forme"),
                "types": strings("types"),
                "prevo": string("prevo"),
                "evos": strings("evos"),
                "evoType": string("evoType"),
                "evoLevel": int(level.group(1)) if level else None,
                "evoItem": string("evoItem"),
                "evoCondition": string("evoCondition"),
                "evoMove": string("evoMove"),
            }
        )
    return entries


def generation(num: int) -> int:
    return next(i + 1 for i, end in enumerate(GENERATION_ENDS) if num <= end)


def smogon_file(name: str, forme: str | None) -> str:
    """Smogon's minisprite name: "s" + the species in lower case with
    spaces and hyphens as underscores; a form adds "-o" + the form."""

    def part(s: str) -> str:
        return re.sub(r"[^a-z0-9_]", "", re.sub(r"[ \-]", "_", s.lower()))

    base = name.removesuffix("-" + forme) if forme else name
    return "s" + part(base) + ("-o" + part(forme) if forme else "") + "-vsmogon.png"


def build(directory: str) -> dict[str, Any]:
    with open(os.path.join(directory, "showdown", "pokedex.ts"), encoding="utf-8") as f:
        dex = parse_pokedex(f.read())
    with open(os.path.join(directory, "pokesprite", "data", "pokemon.json"), encoding="utf-8") as f:
        slugs = {int(k): v["slug"]["eng"] for k, v in json.load(f).items()}

    kept = [
        e
        for e in dex
        if 1 <= e["num"] <= LAST_NUMBER
        and (e["forme"] is None or (e["forme"] in REGIONAL and e["num"] not in CAP_ONLY))
    ]
    id_of = {
        e["name"]: str(e["num"]) if e["forme"] is None else f"{e['num']}-{e['forme'].lower()}"
        for e in kept
    }

    def sprite(e: dict[str, Any]) -> dict[str, Any] | None:
        candidates: list[tuple[str, str | None]] = []
        if e["num"] in slugs:
            slug = slugs[e["num"]] + ("-" + e["forme"].lower() if e["forme"] else "")
            candidates.append(
                (
                    f"pokesprite/pokemon-gen8/regular/{slug}.png",
                    f"pokesprite/pokemon-gen8/shiny/{slug}.png",
                )
            )
        candidates.append((f"smogon-gen9/{smogon_file(e['name'], e['forme'])}", None))
        for regular, shiny in candidates:
            if not os.path.isfile(os.path.join(directory, regular)):
                continue
            size, box = read_png_alpha_box(os.path.join(directory, regular))
            if box[2] == 0:
                continue
            out: dict[str, Any] = {
                "regular": regular,
                "shiny": None,
                "size": list(size),
                "box": list(box),
                "shiny_box": None,
            }
            if shiny is not None and os.path.isfile(os.path.join(directory, shiny)):
                _, shiny_box = read_png_alpha_box(os.path.join(directory, shiny))
                if shiny_box[2]:
                    out["shiny"] = shiny
                    out["shiny_box"] = list(shiny_box)
            return out
        return None

    species: dict[str, dict[str, Any]] = {}
    for e in kept:
        name = e["name"]
        if e["forme"]:
            base = name.removesuffix("-" + e["forme"])
            extra = e["forme"].split("-", 1)[1] if "-" in e["forme"] else ""
            name = f"{REGIONAL[e['forme']]} {base}" + (f" ({extra})" if extra else "")
        prevo = id_of.get(e["prevo"]) if e["prevo"] else None
        how = None
        if prevo is not None:
            how = {"type": e["evoType"] or "level"}
            for key, field in [
                ("level", "evoLevel"),
                ("item", "evoItem"),
                ("condition", "evoCondition"),
                ("move", "evoMove"),
            ]:
                if e[field] is not None:
                    how[key] = e[field]
        species[id_of[e["name"]]] = {
            "num": e["num"],
            "name": name,
            "form": e["forme"].lower() if e["forme"] else "",
            "gen": generation(e["num"]),
            "types": e["types"],
            "prevo": prevo,
            "evos": [id_of[x] for x in e["evos"] if x in id_of],
            "how": how,
            "sprite": sprite(e),
        }

    # A line is named by its root's id, so a regional first form (Alolan
    # Vulpix) is a line of its own beside its species' (Vulpix). A form is
    # offered when it can be drawn and nothing before it in its line can:
    # Clodsire, whose Paldean Wooper the pack does not draw, is offered.
    for key, s in species.items():
        root = key
        drawn_before = False
        while species[root]["prevo"] is not None:
            root = species[root]["prevo"]
            drawn_before = drawn_before or species[root]["sprite"] is not None
        s["line"] = root
        s["first"] = s["prevo"] is None
        s["offer"] = s["sprite"] is not None and not drawn_before

    return {
        "version": 1,
        "count": sum(1 for s in species.values() if not s["form"]),
        "starters": [str(n) for n in STARTERS if str(n) in species],
        "species": species,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--dir",
        default=os.environ.get("ARMADA_SPRITE_DIR")
        or os.path.join(REPO, "web", "sprites", "licensed"),
    )
    args = parser.parse_args()
    pack = build(args.dir)
    with open(os.path.join(args.dir, "species.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    species = pack["species"].values()
    drawn = [s for s in species if s["sprite"]]
    print(
        f"{len(pack['species'])} species and forms ({pack['count']} species), "
        f"{len(drawn)} with a sprite, {sum(1 for s in drawn if s['sprite']['shiny'])} with a shiny, "
        f"{sum(1 for s in drawn if s['offer'])} offered; wrote {args.dir}/species.json",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
