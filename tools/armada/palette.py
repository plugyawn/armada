#!/usr/bin/env python3
"""Armada's palettes: derivation, every colour gate, and the files.

    tools/armada/palette.py            print each palette's values and each gate with its figure
    tools/armada/palette.py --write    also write web/src/armada/tokens/palette-*.css
    tools/armada/palette.py --check    also fail if a palette file differs from what --write
                                       would write (run by tools/lint, with --quiet)

The chosen values are in tools/armada/palette.json, in OKLCH.  Everything
else follows from them here: fills, edges, bubble tints, face stones, the
default button's states.  A palette that fails a gate is not written, and
the exit status is 1.

A palette is one file of colour tokens in both themes.  The default palette
is unscoped; the others are scoped by `:root[data-palette="<name>"]`, which
the server template writes from the ARMADA_PALETTE setting.  The files hold
every colour of the rebuilt frontend and are never edited by hand.

Gates.  Enforced: surfaces apart; inks on every surface text can touch; text
on my bubble and on the default button; the ring; the three news hues as
text and as glyphs, and at least 8 apart (OKLab x 100) from each other and
from a hued accent for normal sight, deuteranopia and protanopia; code and
syntax inks on a block and on both diff grounds; added against removed by
luminance; face letters; neutral chroma.  Reported and not enforced: the
colour-blind distance between the two diff grounds and among the syntax
roles.

Colour-blind simulation is Machado, Oliveira and Fernandes (2009) at
severity 1.0, in linear RGB.  A pair under 8 reads as one colour at glyph
size.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOURCE = os.path.join(ROOT, "tools", "armada", "palette.json")
TOKENS = os.path.join(ROOT, "web", "src", "armada", "tokens")

THEMES = ("light", "dark")
SYNTAX_ROLES = {"k": "keyword", "s": "string", "n": "number", "c": "comment"}

CVD = {
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
}

RGB = tuple[float, float, float]


def decode(x: float) -> float:
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def encode(x: float) -> float:
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def channels(colour: str) -> list[int]:
    return [int(colour[i : i + 2], 16) for i in (1, 3, 5)]


def linear(colour: str) -> RGB:
    r, g, b = (decode(value / 255) for value in channels(colour))
    return (r, g, b)


def hex_of(rgb: RGB) -> str:
    return "#" + "".join(f"{round(encode(min(1, max(0, x))) * 255):02x}" for x in rgb)


def oklch_to_linear(lightness: float, chroma: float, hue: float) -> RGB:
    h = math.radians(hue)
    a = chroma * math.cos(h)
    b = chroma * math.sin(h)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    long, medium, short_ = l_**3, m_**3, s_**3
    return (
        4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short_,
        -1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short_,
        -0.0041960863 * long - 0.7034186147 * medium + 1.7076147010 * short_,
    )


def oklch(lightness: float, chroma: float, hue: float) -> str:
    """The sRGB colour at this lightness and hue; chroma gives way until it fits."""
    rgb = oklch_to_linear(lightness, chroma, hue)
    for _ in range(400):
        rgb = oklch_to_linear(lightness, chroma, hue)
        if all(-1e-4 <= x <= 1 + 1e-4 for x in rgb):
            break
        chroma *= 0.98
    return hex_of(rgb)


def over(foreground: str, alpha: float, background: str) -> str:
    f, b = channels(foreground), channels(background)
    return "#" + "".join(f"{round(f[i] * alpha + b[i] * (1 - alpha)):02x}" for i in range(3))


def oklab(colour: str) -> RGB:
    r, g, b = linear(colour)
    long = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    medium = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    short_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * long + 0.7936177850 * medium - 0.0040720468 * short_,
        1.9779984951 * long - 2.4285922050 * medium + 0.4505937099 * short_,
        0.0259040371 * long + 0.7827717662 * medium - 0.8086757660 * short_,
    )


def lch(colour: str) -> RGB:
    lightness, a, b = oklab(colour)
    return (lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360)


def simulate(colour: str, kind: str | None) -> str:
    if kind is None:
        return colour
    rgb = linear(colour)
    r, g, b = (sum(row[i] * rgb[i] for i in range(3)) for row in CVD[kind])
    return hex_of((r, g, b))


def distance(a: str, b: str) -> float:
    return 100 * math.dist(oklab(a), oklab(b))


def luminance(colour: str) -> float:
    r, g, b = linear(colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    ya, yb = luminance(a), luminance(b)
    return (max(ya, yb) + 0.05) / (min(ya, yb) + 0.05)


def numbers(value: object, count: int) -> list[float]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"expected {count} numbers, found {value!r}")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"expected a number, found {item!r}")
        result.append(float(item))
    return result


def number(value: object) -> float:
    return numbers([value], 1)[0]


def colour_of(value: object) -> str:
    """A colour of palette.json: a hex string, or [lightness, chroma, hue]."""
    if isinstance(value, str):
        if len(value) != 7 or value[0] != "#":
            raise ValueError(f"expected #rrggbb, found {value!r}")
        return value.lower()
    lightness, chroma, hue = numbers(value, 3)
    return oklch(lightness, chroma, hue)


def table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"expected an object, found {value!r}")
    return {str(key): item for key, item in value.items()}


def strings(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"expected a list of strings, found {value!r}")
    return [str(item) for item in value]


@dataclass
class Theme:
    """One palette in one theme: the chosen values, then all that follows."""

    theme: str
    ground: str
    content: str
    raised: str
    ink1: str
    ink2: str
    dim: str
    accent: str
    on_accent: str
    accent_text: str
    me: str
    on_me: str
    success: str
    attention: str
    failure: str
    bubble: str
    alphas: tuple[float, float, float]
    fill1: str = ""
    fill2: str = ""
    fill3: str = ""
    block: str = ""
    chip: str = ""
    edge: str = ""
    edge2: str = ""
    edge_ctl: str = ""
    line: str = ""
    accent_hover: str = ""
    accent_press: str = ""
    ring_alpha: float = 1.0
    diff_add: str = ""
    diff_del: str = ""
    add_ink: str = ""
    del_ink: str = ""
    tints: dict[str, str] = field(default_factory=dict)
    stones: dict[str, tuple[str, str]] = field(default_factory=dict)
    syntax: dict[str, str] = field(default_factory=dict)
    shadows: dict[str, list[str]] = field(default_factory=dict)

    def get(self, name: str) -> str:
        value = getattr(self, name)
        assert isinstance(value, str)
        return value


def derive(chosen: dict[str, object], theme: str, spec: dict[str, object]) -> Theme:
    """Everything that follows from the chosen values."""
    dark = theme == "dark"
    a1, a2, a3 = numbers(chosen["fills"], 3)
    bubble_l, bubble_c, bubble_h = numbers(chosen["bubble"], 3)

    def chosen_colour(name: str) -> str:
        return colour_of(chosen[name])

    t = Theme(
        theme=theme,
        ground=chosen_colour("ground"),
        content=chosen_colour("content"),
        raised=chosen_colour("raised"),
        ink1=chosen_colour("ink1"),
        ink2=chosen_colour("ink2"),
        dim=chosen_colour("dim"),
        accent=chosen_colour("accent"),
        on_accent=chosen_colour("on_accent"),
        accent_text=chosen_colour("accent_text"),
        me=chosen_colour("me"),
        on_me=chosen_colour("on_me"),
        success=chosen_colour("success"),
        attention=chosen_colour("attention"),
        failure=chosen_colour("failure"),
        bubble=oklch(bubble_l, bubble_c, bubble_h),
        alphas=(a1, a2, a3),
    )
    t.fill1, t.fill2, t.fill3 = (over(t.ink1, alpha, t.content) for alpha in t.alphas)

    stones = {name: number(hue) for name, hue in table(spec["stones"]).items()}
    tint = number(chosen["tint"])
    # A speaker's bubble carries a hint of their stone. A palette may set the
    # tint to 0, and every bubble is the neutral one.
    t.tints = {
        name: oklch(bubble_l, tint, hue) if tint and name != "flint" else t.bubble
        for name, hue in stones.items()
    }
    face_l, face_c = numbers(chosen["face"], 2)
    t.stones = {
        name: (
            oklch(face_l, face_c if name != "flint" else 0.008, hue),
            oklch(0.93 if dark else 0.33, 0.05 if name != "flint" else 0.008, hue),
        )
        for name, hue in stones.items()
    }
    t.block = over(t.ink1, number(chosen["block"]), t.content)

    diff = table(table(spec["diff"])[theme])
    t.diff_add, t.diff_del = colour_of(diff["add"]), colour_of(diff["del"])
    t.add_ink, t.del_ink = colour_of(diff["add_ink"]), colour_of(diff["del_ink"])
    syntax = table(table(spec["syntax"])[theme])
    t.syntax = {key: colour_of(syntax[role]) for key, role in SYNTAX_ROLES.items()}
    t.shadows = {
        name: strings(layers) for name, layers in table(table(spec["shadows"])[theme]).items()
    }

    t.edge = over(t.ink1, 0.20 if dark else 0.13, t.raised)
    t.edge2 = over(t.ink1, 0.36 if dark else 0.28, t.raised)
    t.edge_ctl = over(t.ink1, 0.50, t.content)
    t.line = over(t.ink1, 0.12 if dark else 0.10, t.content)

    accent_l, on_accent_l = lch(t.accent)[0], lch(t.on_accent)[0]
    if accent_l < 0.35:
        # A near-black button lightens.
        mix, hover, press = "#ffffff", 0.12, 0.22
    elif on_accent_l > accent_l:
        # Light ink on a hue: the hue deepens, so its ink only gains.
        mix, hover, press = "#000000", 0.12, 0.24
    else:
        # Dark ink on a light hue: the hue deepens.
        mix, hover, press = "#000000", 0.07, 0.14
    t.accent_hover = over(mix, hover, t.accent)
    t.accent_press = over(mix, press, t.accent)
    t.ring_alpha = 1.0 if lch(t.accent_text)[1] >= 0.03 else 0.58
    # A reaction: one step off any bubble.
    t.chip = over(t.ink1, 0.10, t.raised) if dark else t.raised
    return t


def load() -> tuple[str, dict[str, str], dict[str, dict[str, Theme]]]:
    with open(SOURCE, encoding="utf-8") as f:
        spec = table(json.load(f))
    palettes: dict[str, dict[str, Theme]] = {}
    about: dict[str, str] = {}
    for name, value in table(spec["palettes"]).items():
        chosen = table(value)
        about[name] = str(chosen["about"])
        palettes[name] = {theme: derive(table(chosen[theme]), theme, spec) for theme in THEMES}
    default = str(spec["default"])
    if default not in palettes:
        raise ValueError(f"the default palette {default!r} is not one of {sorted(palettes)}")
    return default, about, palettes


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def say(self, text: str = "") -> None:
        self.lines.append(text)


def worst(colour: str, surfaces: dict[str, str]) -> tuple[float, str]:
    return min((contrast(colour, surface), name) for name, surface in surfaces.items())


def gates(name: str, t: Theme, report: Report) -> None:
    theme = t.theme

    def gate(ok: bool, text: str) -> None:
        report.say(f"  {'ok  ' if ok else 'FAIL'} {text}")
        if not ok:
            report.failures.append(f"{name} {theme}: {text}")

    planes = {plane: t.get(plane) for plane in ("ground", "content", "raised")}
    fills = {
        f"fill-{step}/{plane}": over(t.ink1, t.alphas[step - 1], colour)
        for plane, colour in planes.items()
        for step in (1, 2, 3)
    }
    held = {
        "bubble": t.bubble,
        "block": t.block,
        "chip": t.chip,
        **{f"bubble/{stone}": colour for stone, colour in t.tints.items()},
    }
    neutral = {**planes, **fills, **held}

    report.say(f"== {name} {theme}")
    for row in (
        ("ground", "content", "raised", "block", "bubble", "chip"),
        ("ink1", "ink2", "dim", "fill1", "fill2", "fill3"),
        ("accent", "accent_hover", "accent_press", "on_accent", "accent_text", "me", "on_me"),
        ("success", "attention", "failure", "edge", "edge2", "edge_ctl", "line"),
        ("diff_add", "diff_del", "add_ink", "del_ink"),
    ):
        report.say("  " + "  ".join(f"{key} {t.get(key)}" for key in row))
    report.say("  bubbles " + "  ".join(f"{stone} {colour}" for stone, colour in t.tints.items()))
    report.say("  syntax  " + "  ".join(f"{key} {colour}" for key, colour in t.syntax.items()))

    c, g = t.content, t.ground
    tint_steps = [contrast(colour, c) for colour in t.tints.values()]
    report.say(
        f"       steps against the plane: ground {contrast(g, c):.3f}"
        f"  their bubble {contrast(t.bubble, c):.3f}"
        f" (tints {min(tint_steps):.3f} to {max(tint_steps):.3f})"
        f"  block {contrast(t.block, c):.3f}  my bubble {contrast(t.me, c):.2f}"
        f"  field edge {contrast(t.edge, c):.3f}"
    )
    hover, pressed, selected = (over(t.ink1, alpha, g) for alpha in t.alphas)
    report.say(
        f"       states on the nav's ground: hover {contrast(hover, g):.3f}"
        f"  pressed {contrast(pressed, g):.3f}  selected {contrast(selected, g):.3f};"
        f" pressed over hover {contrast(pressed, hover):.3f},"
        f" selected over pressed {contrast(selected, pressed):.3f},"
        f" selected over hover {contrast(selected, hover):.3f}"
    )

    gate(contrast(g, c) >= 1.10, f"ground/content {contrast(g, c):.3f} >= 1.10")
    if theme == "dark":
        gate(
            contrast(c, t.raised) >= 1.25,
            f"content/raised {contrast(c, t.raised):.3f} >= 1.25",
        )
    gate(
        contrast(hover, g) >= 1.08
        and contrast(pressed, hover) >= 1.05
        and contrast(selected, pressed) >= 1.05
        and contrast(selected, hover) >= 1.12,
        f"hover, pressed and selected are each a step: {contrast(hover, g):.3f},"
        f" {contrast(pressed, hover):.3f}, {contrast(selected, pressed):.3f};"
        f" selected over hover {contrast(selected, hover):.3f} >= 1.12",
    )
    gate(
        min(tint_steps) >= 1.22,
        f"their bubble against the plane, weakest {min(tint_steps):.3f} >= 1.22",
    )
    gate(
        contrast(t.block, t.bubble) >= 1.10,
        f"a block against a neutral bubble {contrast(t.block, t.bubble):.3f} >= 1.10"
        " (the craft critic's defect 15: was 1.06)",
    )
    gate(
        contrast(t.edge_ctl, c) >= 3,
        f"a control's edge (checkbox) {contrast(t.edge_ctl, c):.2f} >= 3 on the plane",
    )

    diff_grounds = {"add": t.diff_add, "dele": t.diff_del}
    resting = {
        **planes,
        **held,
        **{
            key: colour
            for key, colour in fills.items()
            if key.startswith("fill-3") and not key.endswith("raised")
        },
        **diff_grounds,
    }
    ink1_resting = worst(t.ink1, resting)
    ink1_transient = worst(t.ink1, neutral)
    ink2_worst = worst(t.ink2, {**neutral, **diff_grounds})
    floor = 11.5 if theme == "light" else 8.4
    gate(
        ink1_resting[0] >= floor,
        f"ink-1 worst {ink1_resting[0]:.2f} on {ink1_resting[1]} >= {floor:.1f}"
        " on every resting surface",
    )
    gate(
        ink1_transient[0] >= 7,
        f"ink-1 worst {ink1_transient[0]:.2f} on {ink1_transient[1]} >= 7 on transient states",
    )
    gate(ink2_worst[0] >= 4.5, f"ink-2 worst {ink2_worst[0]:.2f} on {ink2_worst[1]} >= 4.5")
    gate(
        contrast(t.on_me, t.me) >= 4.5,
        f"text on my bubble {contrast(t.on_me, t.me):.2f} >= 4.5",
    )
    for key in ("accent", "accent_hover", "accent_press"):
        gate(
            contrast(t.on_accent, t.get(key)) >= 4.5,
            f"ink on the default button ({key}) {contrast(t.on_accent, t.get(key)):.2f} >= 4.5",
        )
    gate(
        contrast(t.accent_hover, t.accent) >= 1.08
        and contrast(t.accent_press, t.accent_hover) >= 1.08,
        f"default button: hover/rest {contrast(t.accent_hover, t.accent):.2f},"
        f" press/hover {contrast(t.accent_press, t.accent_hover):.2f} >= 1.08",
    )
    accent_worst = worst(t.accent_text, {**planes, **held})
    gate(
        accent_worst[0] >= 4.5,
        f"accent as text {accent_worst[0]:.2f} on {accent_worst[1]} >= 4.5"
        " (planes, bubbles, block)",
    )
    ring_surfaces = {
        **planes,
        **held,
        **{key: colour for key, colour in fills.items() if key.startswith("fill-3")},
    }
    ring_worst = min(
        (contrast(over(t.accent_text, t.ring_alpha, surface), surface), key)
        for key, surface in ring_surfaces.items()
    )
    gate(
        ring_worst[0] >= 3,
        f"keyboard ring (accent-text at {round(t.ring_alpha * 100)}%)"
        f" worst {ring_worst[0]:.2f} on {ring_worst[1]} >= 3",
    )
    dim_worst = worst(t.dim, planes)[0]
    gate(
        2.0 <= dim_worst < 4.5,
        f"dim {dim_worst:.2f} on planes (disabled only, 2.0 to 4.5)",
    )

    printed = {plane: planes[plane] for plane in ("content", "raised")}
    news = {key: t.get(key) for key in ("success", "attention", "failure")}
    for key, colour in news.items():
        as_text, as_glyph = worst(colour, printed), worst(colour, neutral)
        gate(
            as_text[0] >= 4.5 and as_glyph[0] >= 3,
            f"{key:<9} text {as_text[0]:.2f} on {as_text[1]} >= 4.5;"
            f" glyph {as_glyph[0]:.2f} on {as_glyph[1]} >= 3",
        )
    roles = dict(news)
    if lch(t.accent)[1] >= 0.03:
        roles["accent"] = t.accent
    if lch(t.me)[1] >= 0.03 and t.me != t.accent:
        roles["mine"] = t.me
    sights: tuple[tuple[str, str | None], ...] = (
        ("normal", None),
        ("deutan", "deutan"),
        ("protan", "protan"),
    )
    for label, kind in sights:
        seen = {key: simulate(colour, kind) for key, colour in roles.items()}
        closest = min(
            (distance(seen[a], seen[b]), f"{a}/{b}")
            for a, b in itertools.combinations(seen, 2)
            if not {a, b} <= {"accent", "mine"}
        )
        gate(
            closest[0] >= 8,
            f"{label}: closest of {', '.join(roles)} is {closest[0]:.1f} ({closest[1]}) >= 8",
        )

    # Code.
    step = contrast(t.diff_add, t.diff_del)
    add_l, del_l = oklab(t.diff_add)[0], oklab(t.diff_del)[0]
    gate(
        step >= 1.08 and add_l > del_l,
        f"diff: added against removed {step:.3f} >= 1.08 by luminance,"
        f" added the lighter (L {add_l:.3f}, {del_l:.3f})",
    )
    add_block, del_block = contrast(t.diff_add, t.block), contrast(t.diff_del, t.block)
    gate(
        min(add_block, del_block) >= 1.04,
        f"diff: added/block {add_block:.3f}, removed/block {del_block:.3f} >= 1.04",
    )
    gate(
        contrast(t.add_ink, t.diff_add) >= 4.5
        and contrast(t.del_ink, t.diff_del) >= 4.5
        and min(contrast(t.add_ink, surface) for surface in (c, t.block)) >= 4.5
        and min(contrast(t.del_ink, surface) for surface in (c, t.block)) >= 4.5,
        f"diff: the plus on its line {contrast(t.add_ink, t.diff_add):.2f},"
        f" the minus on its line {contrast(t.del_ink, t.diff_del):.2f};"
        f" as counts on the plane {contrast(t.add_ink, c):.2f}, {contrast(t.del_ink, c):.2f};"
        " all >= 4.5",
    )
    code_on = {"block": t.block, "plane": c, "added": t.diff_add, "removed": t.diff_del}
    for key, colour in t.syntax.items():
        syntax_worst = worst(colour, code_on)
        gate(
            syntax_worst[0] >= 4.5,
            f"syntax {SYNTAX_ROLES[key]} {colour} worst {syntax_worst[0]:.2f}"
            f" on {syntax_worst[1]} >= 4.5",
        )
    for label, kind in sights:
        hues = {key: simulate(t.syntax[key], kind) for key in ("k", "s", "n")}
        closest = min(
            (distance(hues[a], hues[b]), f"{a}/{b}") for a, b in itertools.combinations(hues, 2)
        )
        grounds = distance(simulate(t.diff_add, kind), simulate(t.diff_del, kind))
        keyword = distance(simulate(t.syntax["k"], kind), simulate(t.ink1, kind))
        report.say(
            f"  note reported, {label}: added against removed ground {grounds:.1f};"
            f" closest syntax hues {closest[0]:.1f} ({closest[1]});"
            f" keyword to code ink {keyword:.1f}"
        )
    letters = min((contrast(ink, fill), stone) for stone, (fill, ink) in t.stones.items())
    gate(
        letters[0] >= 4.5,
        f"face letters on their stone, worst {letters[0]:.2f} ({letters[1]}) >= 4.5",
    )
    neutrals = ("ground", "content", "ink1", "ink2", "dim")
    gate(
        all(0.002 <= lch(t.get(key))[1] <= 0.0145 for key in neutrals),
        "neutral chroma "
        + ", ".join(f"{key} {lch(t.get(key))[1]:.4f}" for key in neutrals)
        + " inside 0.002 to 0.014",
    )
    accent_lch, me_lch = lch(t.accent), lch(t.me)
    report.say(
        f"       accent L {accent_lch[0]:.3f} C {accent_lch[1]:.3f} H {accent_lch[2]:.0f};"
        f" my bubble L {me_lch[0]:.3f} C {me_lch[1]:.3f} H {me_lch[2]:.0f};"
        f" ground H {lch(t.ground)[2]:.0f}"
    )


def short(colour: str) -> str:
    """#aabbcc as #abc, the spelling stylelint asks for."""
    if all(colour[i] == colour[i + 1] for i in (1, 3, 5)):
        return "#" + colour[1] + colour[3] + colour[5]
    return colour


def percent(alpha: float) -> str:
    return f"{alpha * 100:.3f}".rstrip("0").rstrip(".") + "%"


def declarations(t: Theme) -> list[tuple[str, str]]:
    ink = " ".join(str(value) for value in channels(t.ink1))
    ring = " ".join(str(value) for value in channels(t.accent_text))
    pairs: list[tuple[str, str]] = [
        ("ground", short(t.ground)),
        ("content", short(t.content)),
        ("raised", short(t.raised)),
        ("ink-1", short(t.ink1)),
        ("ink-2", short(t.ink2)),
        ("dim", short(t.dim)),
        *((f"fill-{step}", f"rgb({ink} / {percent(t.alphas[step - 1])})") for step in (1, 2, 3)),
        ("block", short(t.block)),
        ("line", short(t.line)),
        ("edge", short(t.edge)),
        ("edge-2", short(t.edge2)),
        ("edge-ctl", short(t.edge_ctl)),
        ("bubble", short(t.bubble)),
        ("bubble-me", short(t.me)),
        ("on-me", short(t.on_me)),
        ("chip", short(t.chip)),
        *((f"b-{stone}", short(colour)) for stone, colour in t.tints.items()),
        ("accent", short(t.accent)),
        ("accent-hover", short(t.accent_hover)),
        ("accent-press", short(t.accent_press)),
        ("on-accent", short(t.on_accent)),
        ("accent-text", short(t.accent_text)),
        ("ring", f"rgb({ring} / {percent(t.ring_alpha)})"),
        ("success", short(t.success)),
        ("attention", short(t.attention)),
        ("failure", short(t.failure)),
        ("tint-add", short(t.diff_add)),
        ("tint-del", short(t.diff_del)),
        ("add-ink", short(t.add_ink)),
        ("del-ink", short(t.del_ink)),
        *((f"syn-{key}", short(colour)) for key, colour in t.syntax.items()),
    ]
    for stone, (fill, letters) in t.stones.items():
        pairs.append((f"p-{stone}", short(fill)))
        pairs.append((f"p-{stone}-ink", short(letters)))
    pairs.append(("shadow-raised", ", ".join(t.shadows["raised"])))
    pairs.append(("shadow-field", ", ".join(t.shadows["field"])))
    pairs.append(("shadow-field-focus", ", ".join(t.shadows["focus"])))
    return pairs


WIDTH = 80


def rule(selector: str, t: Theme, depth: int) -> list[str]:
    """One rule, laid out as Prettier lays it out."""
    pad = "    " * depth
    inner = pad + "    "
    lines = [f"{pad}{selector} {{"]
    for name, value in declarations(t):
        one_line = f"{inner}--{name}: {value};"
        if len(one_line) <= WIDTH:
            lines.append(one_line)
            continue
        # Prettier fills the lines with as many layers as fit.
        lines.append(f"{inner}--{name}:")
        filled: list[str] = []
        for layer in value.split(", "):
            if filled and len(f"{inner}    {filled[-1]}, {layer},") <= WIDTH:
                filled[-1] += f", {layer}"
            else:
                filled.append(layer)
        lines.extend(
            f"{inner}    {line}{';' if index == len(filled) - 1 else ','}"
            for index, line in enumerate(filled)
        )
    lines.append(f"{pad}}}")
    return lines


def comment(text: str) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = "/*"
    for word in words:
        if len(line) + 1 + len(word) > WIDTH - 3:
            lines.append(line)
            line = "  "
        line += " " + word
    lines.append(line + " */")
    return lines


def palette_css(name: str, about: str, themes: dict[str, Theme], default: bool) -> str:
    scope = ":root" if default else f':root[data-palette="{name}"]'
    lines = comment(
        f"Armada palette: {name}. Written by tools/armada/palette.py from"
        " tools/armada/palette.json; never edit it by hand. " + about
    )
    lines.append("@layer armada.tokens.palette {")
    lines.extend(rule(scope, themes["light"], 1))
    lines.append("")
    lines.append("    @media (prefers-color-scheme: dark) {")
    lines.extend(rule(f'{scope}:not([data-theme="light"])', themes["dark"], 2))
    lines.append("    }")
    lines.append("")
    lines.extend(rule(f'{scope}[data-theme="dark"]', themes["dark"], 1))
    lines.append("}")
    return "\n".join(lines) + "\n"


# What every palette derives from its own inks, in the stylesheet: the
# pressed fill, the plane a shape stands on, and Increase Contrast. It comes
# after the palettes in a layer of its own, so that it holds whichever
# palette and theme is chosen.
SHARED = """\
/* Armada palettes: what follows from any palette's inks. Written by
   tools/armada/palette.py; never edit it by hand. */
@layer armada.tokens.palette {
    :root {
        /* Pressed: the deepest of the fills, in the frame of the press. */
        --fill-press: color-mix(in srgb, var(--ink-1) 22%, transparent);
        /* The plane a shape stands on; a surface that is not the content
           plane sets it for what it holds. */
        --plane: var(--content);
    }
}

/* Increase Contrast: the second ink becomes the first, the fills deepen,
   edges take the second ink. */
@layer armada.tokens.contrast {
    @media (prefers-contrast: more) {
        :root {
            --ink-2: var(--ink-1);
            --fill-1: color-mix(in srgb, var(--ink-1) 10%, transparent);
            --fill-2: color-mix(in srgb, var(--ink-1) 15%, transparent);
            --fill-3: color-mix(in srgb, var(--ink-1) 20%, transparent);
            --fill-press: color-mix(in srgb, var(--ink-1) 30%, transparent);
            --edge: var(--ink-2);
            --line: var(--ink-2);
        }
    }
}
"""


def files(
    default: str, about: dict[str, str], palettes: dict[str, dict[str, Theme]]
) -> dict[str, str]:
    result = {
        f"palette-{name}.css": palette_css(name, about[name], themes, name == default)
        for name, themes in palettes.items()
    }
    result["palette-shared.css"] = SHARED
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--write", action="store_true", help="write the palette files")
    parser.add_argument(
        "--check", action="store_true", help="fail if a palette file is not what --write writes"
    )
    parser.add_argument("--quiet", action="store_true", help="print failures only")
    options = parser.parse_args()

    default, about, palettes = load()
    report = Report()
    for name, themes in palettes.items():
        for theme in THEMES:
            gates(name, themes[theme], report)
        report.say()
    report.say(
        f"{len(report.failures)} gate(s) failed"
        + ("" if not report.failures else ":\n  " + "\n  ".join(report.failures))
    )
    if not options.quiet:
        print("\n".join(report.lines))
    elif report.failures:
        print("\n".join(report.failures))
    if report.failures:
        print("tools/armada/palette.py: a palette fails a gate; nothing is written.")
        return 1

    status = 0
    written = files(default, about, palettes)
    if options.check:
        # A palette-*.css this tool does not write is a hand-made palette:
        # lint lets files of that name define colour tokens.
        for name in sorted(os.listdir(TOKENS)):
            if name.startswith("palette-") and name.endswith(".css") and name not in written:
                print(
                    f"{os.path.relpath(os.path.join(TOKENS, name), ROOT)} is not a file"
                    " tools/armada/palette.py writes. A palette is added in"
                    " tools/armada/palette.json; delete this file."
                )
                status = 1
    for name, content in written.items():
        path = os.path.join(TOKENS, name)
        if options.write:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            if not options.quiet:
                print(f"wrote {os.path.relpath(path, ROOT)}")
        elif options.check:
            try:
                with open(path, encoding="utf-8") as f:
                    current = f.read()
            except FileNotFoundError:
                current = None
            if current != content:
                print(
                    f"{os.path.relpath(path, ROOT)} is not what tools/armada/palette.py writes."
                    " Change tools/armada/palette.json and run tools/armada/palette.py --write;"
                    " a palette file is never edited by hand."
                )
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
