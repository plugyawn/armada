// @ts-check

const disallowedValues = {
    // thin/medium/thick is under-specified, please use pixels
    "/^(border(-top|-right|-bottom|-left)?|outline)(-width)?$/": [/\b(thin|medium|thick)\b/],
    // no quotation marks around grid-area; use
    // `grid-area: my_area`, not `grid-area: "my_area"`
    "grid-area": [/".*"/],
};

// The rebuilt frontend's stylesheets live beside their components under
// web/src/armada/. Its design language is enforced here from the first
// file; docs/armada/design-language.md says what each rule protects.
const armada = "web/src/armada";

// The only files that may hold a literal: tokens.css every literal that
// is not a colour, the generated palette files every colour.
const armadaTokenFiles = [`${armada}/tokens/tokens.css`];
const armadaPaletteFiles = [`${armada}/tokens/palette-*.css`];

const colorFunctions = [
    "rgb",
    "rgba",
    "hsl",
    "hsla",
    "hwb",
    "lab",
    "lch",
    "oklab",
    "oklch",
    "color",
    "color-mix",
    "light-dark",
];

// Every length, time and font-relative unit; `%`, `fr` and angles remain.
const viewportUnits = ["", "s", "l", "d"].flatMap((size) =>
    ["vw", "vh", "vi", "vb", "vmin", "vmax"].map((unit) => size + unit),
);
const literalUnits = [
    "px",
    "em",
    "rem",
    "ms",
    "s",
    "pt",
    "pc",
    "in",
    "cm",
    "mm",
    "q",
    "ch",
    "ex",
    "cap",
    "ic",
    "lh",
    "rlh",
    "rch",
    "rex",
    "rcap",
    "ric",
    ...viewportUnits,
    "cqw",
    "cqh",
    "cqi",
    "cqb",
    "cqmin",
    "cqmax",
];

const token = String.raw`var\(--[a-z0-9-]+\)`;

// A curve is a token: `linear` the keyword is allowed, `linear()` is not.
const curveFunctions = ["cubic-bezier", "steps", "linear"];

const armadaDisallowedProperties = {
    spacing: ["letter-spacing", "word-spacing", "text-transform", "font-variant-caps"],
    filter: ["backdrop-filter", "filter"],
    // A raised surface's edge is a 1px shadow, so that no border exists.
    border: ["/^border(?!.*-radius$)/"],
    // The frame owns the five named layers.
    z_index: ["z-index"],
};

/** @param {string[]} allowed */
const disallowedProperties = (allowed = []) =>
    Object.entries(armadaDisallowedProperties).flatMap(([name, properties]) =>
        allowed.includes(name) ? [] : properties,
    );

// A shadow token, or a 1px edge drawn as a shadow so that no border exists:
// the ring of a raised surface, or one side of it (the header's line).
const shadowPart = String.raw`(?:var\(--shadow-[a-z-]+\)|(?:inset )?(?:0|-?1px) (?:0|-?1px) 0(?: (?:0|1px))? var\(--(?:edge(?:-[a-z0-9]+)?|line)\))`;

/** @type {import("stylelint").Config["rules"]} */
const armadaRules = {
    // No literal outside the token files.
    "color-no-hex": true,
    "color-named": "never",
    "function-disallowed-list": [...colorFunctions, ...curveFunctions],
    "unit-disallowed-list": [
        literalUnits,
        {
            ignoreProperties: {
                // The 1px edge of a raised surface.
                px: ["box-shadow"],
            },
            // A breakpoint cannot be a custom property.
            ignoreMediaFeatureNames: {
                px: ["/width$/", "/height$/"],
            },
        },
    ],

    // Hierarchy comes from size, weight and space; chrome has no surface at rest.
    "property-disallowed-list": disallowedProperties(),
    "declaration-property-value-allowed-list": {
        "box-shadow": ["none", new RegExp(`^${shadowPart}(?:, ${shadowPart})*$`)],
        "/^border(-[a-z]+)*-radius$/": [
            "0",
            "inherit",
            /^var\(--r-[a-z-]+\)( var\(--r-[a-z-]+\))*$/,
        ],
        "transition-property": [
            /^(transform|opacity|background-color|color)(, (transform|opacity|background-color|color))*$/,
            "none",
        ],
        "/^(transition|animation)-timing-function$/": [/^var\(--spring-[a-z-]+\)$/, "linear"],
        // The two faces and the ten styles, and nothing set beside them.
        "font-family": [/^var\(--f-[a-z]+\)$/, "inherit"],
        font: [/^var\(--t-[a-z-]+\)$/, "inherit"],
        "/^font-(size|weight)$/": [new RegExp(`^${token}$`), "inherit"],
        // Inline code sets no line box of its own.
        "line-height": [new RegExp(`^${token}$`), "inherit", "0"],
        // The window has one ring, drawn by motion/ring.ts.
        "/^outline(-width|-offset|-style|-color)?$/": [
            new RegExp(`^(${token}|solid)( (${token}|solid))*$`),
            "none",
            "0",
            "inherit",
        ],
    },
    "declaration-property-value-disallowed-list": {
        ...disallowedValues,
        "/^transition/": [/\ball\b/],
        // Capitals are never a label.
        "font-variant": [/caps\b|\bunicase\b/],
        "/^(transition|animation)$/": [
            /\b(ease|ease-in|ease-out|ease-in-out|step-start|step-end)\b/,
        ],
    },

    // Motion: transform and opacity, colour on the fades; loops live in two files.
    "plugin/no-low-performance-animation-properties": [
        true,
        {ignoreProperties: ["color", "background-color"]},
    ],
    "at-rule-disallowed-list": ["keyframes"],

    // The new rules win by cascade layer, never by weight.
    "selector-max-id": 0,
    "selector-attribute-name-disallowed-list": ["id"],
    "declaration-no-important": true,
    "selector-max-specificity": "0,3,0",
    "selector-class-pattern": [
        "^armada-[a-z0-9-]+$",
        {
            message:
                "Classes are armada-<component> or armada-<component>-<part>; state is an attribute",
        },
    ],
};

/** @type {import("stylelint").Config} */
export default {
    // INTERIM BETA (/beta/): the design prototype's stylesheets, moved in
    // whole and deleted when the rebuild's conversation lands; not held to
    // web/src/armada/'s design language (web/src/armada/beta/README.md).
    ignoreFiles: ["web/src/armada/beta/**"],
    extends: ["stylelint-config-standard"],
    plugins: ["stylelint-high-performance-animation"],
    rules: {
        // Add some exceptions for recommended rules
        "at-rule-no-unknown": [true, {ignoreAtRules: ["extend"]}],
        "font-family-no-missing-generic-family-keyword": [
            true,
            {ignoreFontFamilies: ["FontAwesome"]},
        ],

        // Disable recommended rules we don't comply with yet
        "media-query-no-invalid": null,
        "no-descending-specificity": null,

        // Disable standard rules we don't comply with yet
        "comment-empty-line-before": null,
        "declaration-empty-line-before": null,
        "keyframes-name-pattern": null,
        "selector-class-pattern": null,
        "selector-id-pattern": null,

        // Limit language features
        "color-no-hex": true,
        "color-named": "never",
        "declaration-property-value-disallowed-list": disallowedValues,
        "function-disallowed-list": [
            // We use hsl instead of rgb
            "rgb",
        ],
        "plugin/no-low-performance-animation-properties": [true, {ignore: "paint-properties"}],

        // Zulip CSS should have no dependencies on external resources
        "function-url-no-scheme-relative": true,
        "function-url-scheme-allowed-list": [
            "data", // Allow data URLs
        ],
    },
    // A later override replaces a rule's options whole, so each exemption
    // restates the rule it relaxes.
    overrides: [
        {
            files: [`${armada}/**/*.css`],
            rules: armadaRules,
        },
        {
            files: [`${armada}/kit/table.css`],
            rules: {"property-disallowed-list": disallowedProperties(["border"])},
        },
        {
            // The frame names the five layers, and holds the composition's
            // table as the design writes it: lengths in px, the plane's
            // width in cqi and the window's height in dvh
            // (SPEC-ENGINEERING section 2).
            files: [`${armada}/frame/frame.css`],
            rules: {
                "property-disallowed-list": disallowedProperties(["z_index"]),
                "unit-disallowed-list": [
                    literalUnits.filter((unit) => !["px", "cqi", "dvh"].includes(unit)),
                    {ignoreMediaFeatureNames: {px: ["/width$/", "/height$/"]}},
                ],
            },
        },
        {
            // The bridges place old markup, which has ids and old class
            // names, inside the frame until each is deleted
            // (web/src/armada/bridge/README.md).
            files: [`${armada}/bridge/*.css`],
            rules: {
                "selector-max-id": null,
                "selector-attribute-name-disallowed-list": null,
                "selector-max-specificity": null,
                "selector-class-pattern": null,
            },
        },
        {
            files: [`${armada}/kit/hover.css`, `${armada}/kit/marks.css`],
            rules: {"at-rule-disallowed-list": null},
        },
        {
            files: armadaTokenFiles,
            rules: {
                "unit-disallowed-list": null,
                // tokens.css defines the springs with linear() and the
                // working mark's stepped loop with steps().
                "function-disallowed-list": [...colorFunctions, "cubic-bezier"],
                "declaration-property-value-allowed-list": null,
            },
        },
        {
            // @font-face names a family and a weight as literals.
            files: [`${armada}/tokens/fonts.css`],
            rules: {"declaration-property-value-allowed-list": null},
        },
        {
            files: armadaPaletteFiles,
            rules: {
                "color-no-hex": null,
                "function-disallowed-list": ["cubic-bezier"],
                "unit-disallowed-list": null,
                "declaration-property-value-allowed-list": null,
            },
        },
    ],
};
