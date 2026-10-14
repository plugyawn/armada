"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {run_test} = require("../lib/test.cjs");

// The tokens are the design's, by name and by value. tokens.design.json
// is the design's set as these files spell it; a change to the design
// changes that file and the token files together, and a synonym, a stray
// colour or a hand-edited value fails here.
const tokens_dir = path.resolve(__dirname, "../../src/armada/tokens");
const design = JSON.parse(fs.readFileSync(path.join(__dirname, "tokens.design.json"), "utf8"));

const palettes = ["lapis", "tide", "citrine", "onyx"];
const dark_by_media = '@media (prefers-color-scheme: dark) :root:not([data-theme="light"])';
const dark_by_attribute = ':root[data-theme="dark"]';

function normalize(value) {
    return value.replaceAll(/\s+/g, " ").replaceAll(/\(\s+/g, "(").replaceAll(/\s+\)/g, ")").trim();
}

// {scope: {name: value}} for the custom properties of a stylesheet, where
// a scope is its selector with the at-rules around it, less the layer.
function read_tokens(file) {
    const css = fs
        .readFileSync(path.join(tokens_dir, file), "utf8")
        .replaceAll(/\/\*[\s\S]*?\*\//g, "");
    const scopes = {};
    const selectors = [];
    let text = "";
    let parentheses = 0;
    const declare = () => {
        const match = /^\s*(--[\w-]+)\s*:([\s\S]*)$/.exec(text);
        if (match) {
            const scope = selectors.filter((part) => !part.startsWith("@layer")).join(" ");
            scopes[scope] ??= {};
            scopes[scope][match[1]] = normalize(match[2]);
        }
        text = "";
    };
    for (const character of css) {
        if (character === "(") {
            parentheses += 1;
        } else if (character === ")") {
            parentheses -= 1;
        }
        if (parentheses > 0) {
            text += character;
        } else {
            switch (character) {
                case "{": {
                    selectors.push(normalize(text));
                    text = "";

                    break;
                }
                case "}": {
                    declare();
                    selectors.pop();

                    break;
                }
                case ";": {
                    declare();

                    break;
                }
                default: {
                    text += character;
                }
            }
        }
    }
    return scopes;
}

const colour_value = /#[\da-f]{3,8}\b|\b(rgb|hsl|oklch|oklab|color-mix|light-dark)\(/i;

run_test("every token file holds the design's names and values", () => {
    for (const file of [
        "tokens.css",
        "palette-shared.css",
        ...palettes.map((palette) => `palette-${palette}.css`),
    ]) {
        assert.deepEqual(read_tokens(file), design[file], file);
    }
});

run_test("tokens.css holds no colour, and the palettes nothing else", () => {
    const tokens = read_tokens("tokens.css");
    const colour_names = new Set(Object.keys(read_tokens("palette-lapis.css")[":root"]));
    for (const [scope, declarations] of Object.entries(tokens)) {
        for (const [name, value] of Object.entries(declarations)) {
            assert.ok(!colour_names.has(name), `${name} is a colour, in tokens.css ${scope}`);
            assert.ok(!colour_value.test(value), `${name}: ${value} is a colour, in tokens.css`);
        }
    }
    for (const name of Object.keys(tokens[":root"])) {
        assert.ok(!name.startsWith("--shadow-"), `${name} belongs to the palette`);
    }
    for (const [name, value] of Object.entries(read_tokens("palette-lapis.css")[":root"])) {
        assert.ok(colour_value.test(value), `${name}: ${value} is no colour, in a palette file`);
    }
});

run_test("the three names beyond the design's are the only ones", () => {
    assert.deepEqual(design.added, ["--overlay-rest", "--mod-hold", "--announce"]);
    const tokens = read_tokens("tokens.css")[":root"];
    assert.equal(tokens["--overlay-rest"], "120ms");
    assert.equal(tokens["--mod-hold"], "150ms");
    assert.equal(tokens["--announce"], "2s");
});

run_test("a palette is the same names in both themes, and every palette has them", () => {
    const names = Object.keys(read_tokens("palette-lapis.css")[":root"]);
    for (const palette of palettes) {
        // The default palette is unscoped; the others answer to the
        // attribute the server template writes.
        const root = palette === "lapis" ? ":root" : `:root[data-palette="${palette}"]`;
        const scopes = read_tokens(`palette-${palette}.css`);
        const media = dark_by_media.replace(":root", root);
        const attribute = dark_by_attribute.replace(":root", root);
        assert.deepEqual(
            Object.keys(scopes).toSorted(),
            [root, media, attribute].toSorted(),
            palette,
        );
        assert.deepEqual(Object.keys(scopes[root]), names, palette);
        assert.deepEqual(Object.keys(scopes[media]), names, palette);
        // Dark is one set of values, whether the system or the person asks.
        assert.deepEqual(scopes[attribute], scopes[media], palette);
        assert.notDeepEqual(scopes[root], scopes[media], palette);
    }
});

run_test("type is eleven styles on five sizes, and code is 14 on 20", () => {
    const tokens = read_tokens("tokens.css")[":root"];
    const styles = Object.entries(tokens).filter(([name]) => name.startsWith("--t-"));
    assert.equal(styles.length, 11);
    const sizes = new Set();
    for (const [name, value] of styles) {
        if (value.startsWith("var(")) {
            // One style may be another by a second name.
            assert.ok(value.slice(4, -1) in tokens, name);
            continue;
        }
        const match = /^(400|500|600) (\d+)px\/(\d+)px var\(--f-(human|code)\)$/.exec(value);
        assert.ok(match, `${name}: ${value}`);
        sizes.add(Number(match[2]));
    }
    assert.deepEqual(
        [...sizes].toSorted((a, b) => a - b),
        [12, 14, 16, 20, 28],
    );
    assert.equal(tokens["--t-code"], "400 14px/20px var(--f-code)");
    assert.equal(tokens["--t-talk"], "400 16px/24px var(--f-human)");
    assert.equal(tokens["--t-text"], "400 14px/20px var(--f-human)");
    assert.equal(tokens["--f-human"], '"Söhne", "Hanken Grotesk", sans-serif');
    assert.equal(tokens["--f-code"], '"Geist Mono", monospace');
    assert.ok(!Object.keys(tokens).some((name) => name.startsWith("--f-agent")));
});

run_test("reduced motion collapses the springs and keeps the fades", () => {
    const tokens = read_tokens("tokens.css");
    const by_media = tokens["@media (prefers-reduced-motion: reduce) :root"];
    assert.deepEqual(by_media, tokens[':root[data-motion="reduced"]']);
    for (const spring of ["touch", "move", "settle", "land"]) {
        assert.equal(by_media[`--${spring}-t`], "1ms");
    }
    assert.ok(!("--fade-in" in by_media));
    assert.ok(!("--fade-out" in by_media));
});
