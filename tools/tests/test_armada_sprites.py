import json
import os
import struct
import tempfile
import zlib
from unittest import TestCase

from tools.armada.sprites import SV_ALPHA_MIN, build, read_png_alpha_box


def write_png(path: str, width: int, height: int, alpha: dict[tuple[int, int], int]) -> None:
    """An RGBA PNG, transparent but for the pixels given with their alpha."""
    rows = b"".join(
        b"\x00" + b"".join(bytes((0, 0, 0, alpha.get((x, y), 0))) for x in range(width))
        for y in range(height)
    )

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(rows)))
        f.write(chunk(b"IEND", b""))


def write_palette_png(path: str, rows: list[list[int]], alphas: list[int]) -> None:
    """A palette PNG (8 bits an index) whose entries have the alphas given."""
    data = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 3, 0, 0, 0)))
        f.write(chunk(b"PLTE", bytes(3 * len(alphas))))
        f.write(chunk(b"tRNS", bytes(alphas)))
        f.write(chunk(b"IDAT", zlib.compress(data)))
        f.write(chunk(b"IEND", b""))


POKEDEX = """export const Pokedex = {
\tcharmander: {
\t\tnum: 4,
\t\tname: "Charmander",
\t\ttypes: ["Fire"],
\t\tevos: ["Charmeleon"],
\t},
\tcharmeleon: {
\t\tnum: 5,
\t\tname: "Charmeleon",
\t\ttypes: ["Fire"],
\t\tprevo: "Charmander",
\t\tevoLevel: 16,
\t},
\tpidgey: {
\t\tnum: 16,
\t\tname: "Pidgey",
\t\ttypes: ["Normal", "Flying"],
\t},
\tvulpixalola: {
\t\tnum: 37,
\t\tname: "Vulpix-Alola",
\t\tforme: "Alola",
\t\ttypes: ["Ice"],
\t},
};
"""


class ArmadaSpritesTest(TestCase):
    def test_alpha_box_counts_from_the_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "icon.png")
            # a faint antialiased fringe at (1, 1), the drawing from (3, 2) to (5, 6)
            alpha = {(1, 1): 10} | {(x, y): 255 for x in range(3, 6) for y in range(2, 7)}
            write_png(path, 8, 9, alpha)
            self.assertEqual(read_png_alpha_box(path), ((8, 9), (1, 1, 5, 6)))
            self.assertEqual(read_png_alpha_box(path, SV_ALPHA_MIN), ((8, 9), (3, 2, 3, 5)))
            write_png(path, 4, 4, {(0, 0): 10})
            self.assertEqual(read_png_alpha_box(path, SV_ALPHA_MIN), ((4, 4), (0, 0, 0, 0)))
            # a palette image: entry 0 clear, 1 faint, 2 opaque
            write_palette_png(path, [[0, 1, 0, 0], [0, 0, 2, 2], [0, 0, 2, 0]], [0, 10, 255])
            self.assertEqual(read_png_alpha_box(path), ((4, 3), (1, 0, 3, 3)))
            self.assertEqual(read_png_alpha_box(path, SV_ALPHA_MIN), ((4, 3), (2, 1, 2, 2)))

    def test_species_carry_their_icon_and_only_iconed_first_forms_are_offered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.makedirs(os.path.join(directory, "showdown"))
            with open(os.path.join(directory, "showdown", "pokedex.ts"), "w") as f:
                f.write(POKEDEX)
            os.makedirs(os.path.join(directory, "pokesprite", "data"))
            with open(os.path.join(directory, "pokesprite", "data", "pokemon.json"), "w") as f:
                json.dump(
                    {
                        str(n): {"slug": {"eng": slug}}
                        for n, slug in [
                            (4, "charmander"),
                            (5, "charmeleon"),
                            (16, "pidgey"),
                            (37, "vulpix"),
                        ]
                    },
                    f,
                )
            for slug in ["charmander", "charmeleon", "pidgey", "vulpix-alola"]:
                write_png(
                    os.path.join(directory, "pokesprite", "pokemon-gen8", "regular", f"{slug}.png"),
                    6,
                    6,
                    {(2, 2): 255},
                )
            # Scarlet/Violet icons for Charmander and Charmeleon only; an
            # icon for a regional form is never looked for.
            write_png(
                os.path.join(directory, "sv", "4.png"),
                10,
                12,
                {(x, y): 255 for x in range(1, 9) for y in range(2, 12)},
            )
            write_png(os.path.join(directory, "sv", "5.png"), 9, 9, {(4, 4): 255})
            write_png(os.path.join(directory, "sv", "37.png"), 9, 9, {(4, 4): 255})

            species = build(directory)["species"]

        self.assertEqual(
            species["4"]["sv"], {"path": "sv/4.png", "size": [10, 12], "box": [1, 2, 8, 10]}
        )
        self.assertEqual(
            species["5"]["sv"], {"path": "sv/5.png", "size": [9, 9], "box": [4, 4, 1, 1]}
        )
        self.assertIsNone(species["16"]["sv"])
        self.assertIsNone(species["37-alola"]["sv"])
        self.assertEqual(
            {key: entry["offer"] for key, entry in species.items()},
            # Charmeleon has an icon but is not a first form; Pidgey and
            # Alolan Vulpix are first forms with no icon.
            {"4": True, "5": False, "16": False, "37-alola": False},
        )
