// The showroom: the dev page /devtools/armada/. Every component of the kit
// in every state, as Preact and as the DOM builder from one fixture; the
// tokens of every palette with their contrast; the sprite; and the motion
// and sound benches. It is the visual contract `arm check kit` shoots, and
// the page a person judges the kit on.
//
//   ?as=preact|dom|both   which idiom draws the specimens (both)
//   ?theme=light|dark     overrides the system's
//   ?p=<palette>          the palette, by the server
//   ?motion=reduced       as with the system's setting
//   ?section=<id>         one section only; `tokens` is what the palette
//                         frames show

import "./showroom.css";

import {render} from "preact";
import type {JSX} from "preact";
import {useState} from "preact/hooks";

import {Segments, View} from "../kit/index.ts";
import {build, n} from "../kit/tree.ts";
import type {Tree} from "../kit/tree.ts";
import * as motion from "../motion/index.ts";
import * as sound from "../sound/sound.ts";

import {motion_bench, sound_bench} from "./bench.ts";
import {colour_roles, contrast_pairs, sections} from "./fixtures.ts";
import type {Item, Section} from "./fixtures.ts";
import {surface_sections} from "./surfaces.ts";

const MODES = ["both", "preact", "dom"] as const;
type Mode = (typeof MODES)[number];

// The page's own words.
const WORDS = {
    title: "Armada component sheet",
    intro: "Every value on this sheet is a token; every component is the kit's, drawn through Preact and through the DOM builder from one fixture. The licensed face falls back to Hanken Grotesk for what the test files lack.",
    draw: "Drawn by",
    modes: {both: "Both, side by side", preact: "Preact", dom: "DOM builder"},
    preact: "Preact",
    dom: "DOM builder",
    theme: "Theme",
    themes: {system: "System", light: "Light", dark: "Dark"},
    palettes:
        "The four palettes, side by side, with the contrast of what is read on what it stands on",
    contrast: ["Text", "On", "Contrast", "At least"],
    frame: "Palette",
};

function mode_of(query: URLSearchParams): Mode {
    const asked = query.get("as");
    return MODES.find((mode) => mode === asked) ?? "both";
}

// A fixture drawn by the DOM builder, in a host that takes no room of its
// own, so both idioms stand in the same layout.
function Built({tree}: {tree: Tree}): JSX.Element {
    return (
        <div
            class="armada-showroom-built"
            data-drawn="dom"
            ref={(host) => {
                host?.replaceChildren(build(tree));
            }}
        />
    );
}

function Specimen({item, mode}: {item: Item; mode: Mode}): JSX.Element {
    const caption =
        item.caption === undefined ? null : (
            <span class="armada-showroom-caption">{item.caption}</span>
        );
    if (mode === "both") {
        return (
            <div class="armada-showroom-stack" data-fill>
                <div class="armada-showroom-pair">
                    <div data-drawn="preact">
                        <View tree={item.body} />
                    </div>
                    <div>
                        <Built tree={item.body} />
                    </div>
                </div>
                {caption}
            </div>
        );
    }
    return (
        <div class="armada-showroom-stack" data-fill>
            {mode === "preact" ? (
                <View tree={item.body} />
            ) : (
                <Built tree={item.body} />
            )}
            {caption}
        </div>
    );
}

function SectionView({
    section,
    mode,
}: {
    section: Section;
    mode: Mode;
}): JSX.Element {
    return (
        <section data-armada={`showroom-${section.id}`}>
            <h2 class="armada-showroom-section">{section.title}</h2>
            {section.note === undefined ? null : (
                <span class="armada-showroom-caption" data-wide>
                    {section.note}
                </span>
            )}
            <div
                class="armada-showroom-columns"
                data-columns={
                    mode === "both"
                        ? Math.min(2, section.columns)
                        : section.columns
                }
                data-gap={section.gap}
            >
                {section.items.map((item) => (
                    <Specimen item={item} mode={mode} />
                ))}
            </div>
        </section>
    );
}

// ---- tokens: one palette's colours and the contrast of what is read

type Colour = [number, number, number, number];

function parse(value: string): Colour | undefined {
    const hex = /^#([\da-f]{3,8})$/i.exec(value.trim())?.[1];
    if (hex !== undefined) {
        const wide =
            hex.length <= 4
                ? [...hex].map((digit) => digit + digit).join("")
                : hex;
        const channel = (at: number): number =>
            Number.parseInt(wide.slice(at, at + 2), 16);
        return [
            channel(0),
            channel(2),
            channel(4),
            wide.length === 8 ? channel(6) / 255 : 1,
        ];
    }
    const parts = /^rgba?\(([^)]+)\)$/
        .exec(value.trim())?.[1]
        ?.split(/[\s,/]+/);
    if (parts === undefined || parts.length < 3) {
        return undefined;
    }
    const alpha =
        parts[3] === undefined
            ? 1
            : Number.parseFloat(parts[3]) / (parts[3].endsWith("%") ? 100 : 1);
    return [Number(parts[0]), Number(parts[1]), Number(parts[2]), alpha];
}

function luminance([red, green, blue]: Colour): number {
    const linear = (channel: number): number => {
        const unit = channel / 255;
        return unit <= 0.040_45
            ? unit / 12.92
            : ((unit + 0.055) / 1.055) ** 2.4;
    };
    return (
        0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    );
}

function over(top: Colour, under: Colour): Colour {
    const mix = (index: 0 | 1 | 2): number =>
        top[index] * top[3] + under[index] * (1 - top[3]);
    return [mix(0), mix(1), mix(2), 1];
}

function contrast(text: Colour, ground: Colour): number {
    const [lighter, darker] = [
        luminance(over(text, ground)),
        luminance(ground),
    ].toSorted((a, b) => b - a);
    return (lighter! + 0.05) / (darker! + 0.05);
}

// A token's value as the page computes it. This reads a custom property,
// not layout.
function token(name: string): string {
    return String(
        document.documentElement.computedStyleMap().get(`--${name}`) ?? "",
    );
}

function Tokens({palette}: {palette: string}): JSX.Element {
    return (
        <section data-armada="showroom-tokens" data-palette-shown={palette}>
            <h3 class="armada-showroom-group">{palette}</h3>
            <div class="armada-showroom-row">
                {colour_roles.map(([name]) => (
                    <View
                        tree={n("i", {
                            class: "armada-showroom-swatch",
                            style: `background:var(--${name})`,
                            title: name,
                        })}
                    />
                ))}
            </div>
            <table class="armada-showroom-table">
                <thead>
                    <tr>
                        {WORDS.contrast.map((label) => (
                            <th>{label}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {contrast_pairs.map(([text, ground, least]) => {
                        const [ink, plane] = [
                            parse(token(text)),
                            parse(token(ground)),
                        ];
                        const ratio =
                            ink === undefined || plane === undefined
                                ? 0
                                : contrast(ink, plane);
                        return (
                            <tr
                                data-pair={`${text}/${ground}`}
                                data-passes={ratio >= least}
                            >
                                <td>{text}</td>
                                <td>{ground}</td>
                                <td
                                    data-tone={
                                        ratio >= least ? undefined : "failure"
                                    }
                                >
                                    {ratio.toFixed(2)}
                                </td>
                                <td>{least}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </section>
    );
}

function Palettes({
    palettes,
    query,
}: {
    palettes: string[];
    query: URLSearchParams;
}): JSX.Element {
    const theme = query.get("theme");
    const frame = (palette: string): string =>
        `?p=${palette}&section=tokens${theme === null ? "" : `&theme=${theme}`}`;
    return (
        <section data-armada="showroom-palettes">
            <h2 class="armada-showroom-section">{WORDS.palettes}</h2>
            <div
                class="armada-showroom-columns"
                data-columns="4"
                data-gap="lists"
            >
                {palettes.map((palette) => (
                    <iframe
                        class="armada-showroom-frame"
                        title={palette}
                        src={frame(palette)}
                    />
                ))}
            </div>
        </section>
    );
}

// ---- the page

function Showroom({
    agent,
    palettes,
    query,
}: {
    agent: string;
    palettes: string[];
    query: URLSearchParams;
}): JSX.Element {
    const [mode, set_mode] = useState<Mode>(mode_of(query));
    const [theme, set_theme] = useState(query.get("theme") ?? "system");
    const only = query.get("section");
    const shown = [...sections(agent), ...surface_sections()].filter(
        (section) => only === null || section.id === only,
    );
    const choose_theme = (value: string): void => {
        if (value === "system") {
            delete document.documentElement.dataset["theme"];
        } else {
            document.documentElement.dataset["theme"] = value;
        }
        set_theme(value);
    };
    return (
        <>
            {only === null ? (
                <>
                    <h1 class="armada-showroom-title">{WORDS.title}</h1>
                    <span class="armada-showroom-caption" data-wide>
                        {WORDS.intro}
                    </span>
                    <div
                        class="armada-raised armada-showroom-toolbar"
                        data-armada="showroom-toolbar"
                    >
                        <span class="armada-showroom-caption">
                            {WORDS.draw}
                        </span>
                        <Segments
                            label={WORDS.draw}
                            options={MODES.map((value) => ({
                                label: WORDS.modes[value],
                                selected: value === mode,
                                on_press() {
                                    set_mode(value);
                                },
                            }))}
                        />
                        <span class="armada-showroom-caption">
                            {WORDS.theme}
                        </span>
                        <Segments
                            label={WORDS.theme}
                            options={(["system", "light", "dark"] as const).map(
                                (value) => ({
                                    label: WORDS.themes[value],
                                    selected: value === theme,
                                    on_press() {
                                        choose_theme(value);
                                    },
                                }),
                            )}
                        />
                    </div>
                </>
            ) : null}
            {shown.map((section) => (
                <SectionView section={section} mode={mode} />
            ))}
            {only === null ? (
                <Palettes palettes={palettes} query={query} />
            ) : null}
        </>
    );
}

// The field's bounds, printed beside each of its states. Sizes come from
// the observer's own entries: nothing here forces a layout.
function print_bounds(root: HTMLElement): void {
    const observer = new ResizeObserver((entries) => {
        for (const entry of entries) {
            const label = entry.target
                .closest(".armada-showroom-dock")
                ?.querySelector(".armada-showroom-bounds");
            const size = entry.borderBoxSize[0];
            if (label instanceof HTMLElement && size !== undefined) {
                label.textContent = `${Math.round(size.inlineSize * 100) / 100} × ${Math.round(size.blockSize * 100) / 100}`;
            }
        }
    });
    for (const shape of root.querySelectorAll(
        ".armada-showroom-dock .armada-field-shape",
    )) {
        observer.observe(shape);
    }
}

export function start(): void {
    const root = document.querySelector<HTMLElement>(
        "[data-armada='showroom']",
    );
    if (root === null) {
        return;
    }
    const query = new URLSearchParams(window.location.search);
    const theme = query.get("theme");
    if (theme === "light" || theme === "dark") {
        document.documentElement.dataset["theme"] = theme;
    }
    if (query.get("motion") === "reduced") {
        document.documentElement.dataset["motion"] = "reduced";
    }
    const agent = root.dataset["agent"] ?? "";
    const palette = document.documentElement.dataset["palette"] ?? "";
    const palettes = (root.dataset["palettes"] ?? "")
        .split(" ")
        .filter(Boolean);
    const only = query.get("section");
    motion.wire();
    if (DEVELOPMENT) {
        motion.watch();
    }
    sound.arm();
    if (only === "tokens") {
        root.dataset["embedded"] = "";
        render(<Tokens palette={palette} />, root);
        return;
    }
    const page = build(n("div"));
    root.append(page);
    render(<Showroom agent={agent} palettes={palettes} query={query} />, page);
    if (only === null || only === "motion") {
        root.append(motion_bench(agent));
    }
    if (only === null || only === "sound") {
        root.append(sound_bench());
    }
    print_bounds(root);
    motion.warm();
}

start();
