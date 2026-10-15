// @ts-check

import fs from "node:fs";
import path from "node:path";

import {FlatCompat} from "@eslint/eslintrc";
import js from "@eslint/js";
import confusingBrowserGlobals from "confusing-browser-globals";
import {defineConfig} from "eslint/config";
import prettier from "eslint-config-prettier";
import {configs as astroConfigs} from "eslint-plugin-astro";
import formatjs from "eslint-plugin-formatjs";
import importPlugin from "eslint-plugin-import";
import * as mdx from "eslint-plugin-mdx";
import noJquery from "eslint-plugin-no-jquery";
import promise from "eslint-plugin-promise";
import unicorn from "eslint-plugin-unicorn";
import globals from "globals";
import tseslint from "typescript-eslint";

const compat = new FlatCompat({baseDirectory: import.meta.dirname});

// The rebuilt frontend lives in web/src/armada/. Its rules are errors
// from the first file; docs/armada/architecture.md says why each exists.
const armada = "web/src/armada";

// Every directory under surfaces/ is a surface, so that a new one is
// fenced from its first file without being added to a list here.
const armadaSurfaces = fs
    .readdirSync(path.join(import.meta.dirname, armada, "surfaces"), {withFileTypes: true})
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .toSorted();

const layoutReads =
    "offset(Top|Left|Width|Height|Parent)|client(Top|Left|Width|Height)|scroll(Top|Left|Width|Height)|inner(Width|Height)|outer(Width|Height)|scroll[XY]|page[XY]Offset";
const layoutReadMessage =
    "No layout read here: measure in the after-layout phase (motion/, frame/after_layout.ts, feed/scroll.ts, surfaces/composer/origin.ts).";

// The agent's old code name, which the rename (package R1) took out of the
// tree: as a name of its own ("appear", "spear" and "pearl" are other
// words), as a part of a camel-case name, or as a constant's prefix. The
// custom check "The old code name is gone" holds it out of every file; this
// rule also sees the rebuild's file names. The brackets keep the letters
// apart so that this file does not hold the name.
const oldCodeName =
    "(?<![A-Za-z])([Pp]ear|[P]EAR)(?![a-z])|(?<=[a-z0-9])[P]ear(?![a-z])|(?<=[A-Za-z])[P]EAR_";
const oldCodeNameMessage =
    "The old code name is gone (package R1): say armada, agent, run, worktree or repository.";
const agentNameMessage =
    "The agent's name is never a literal and nothing is named after it: it arrives from the server; strings say {agent}.";

const rawControls = "button|input|select|dialog";
const rawControlMessage = "Surfaces use the kit's components, never a raw control element.";
const htmlMessage = "HTML strings enter the DOM only in feed/parts.ts and feed/stream_writer.ts.";
const bannedLibraries = String.raw`^(jquery|handlebars|tippy\.js|micromodal)\b`;
const networkMessage = "Only web/src/armada/api/ touches the network.";
const untaggedMessage =
    "A string a person will read is tagged for translation ($t); this one is set on the DOM as a literal.";
const wordsForPeople = "[A-Za-z]{2}";
const templateWithWords = `TemplateLiteral:has(> TemplateElement[value.raw=/${wordsForPeople}/])`;
const textAttributes = "aria-label|aria-description|aria-placeholder|title|placeholder|alt";

/** @type {Record<string, {selector: string, message: string}[]>} */
const armadaSyntax = {
    jquery: [
        {
            selector: "CallExpression[callee.name='$']",
            message: "No jQuery under web/src/armada/: use the kit, or kit/dom.ts in the feed.",
        },
    ],
    banned_import: [
        {
            selector: `ImportExpression[source.value=/${bannedLibraries}/]`,
            message: "Not under web/src/armada/: the kit and the motion kit replace it.",
        },
        {
            selector: String.raw`ImportExpression[source.value=/\.hbs$/]`,
            message: "No Handlebars under web/src/armada/: chrome is .tsx, the feed is kit/dom.ts.",
        },
    ],
    animate: [
        {
            // Any method of this name, because the receiver's type is not
            // known here; the motion kit exports none called `animate`.
            selector: "CallExpression[callee.property.name='animate']",
            message:
                "Only web/src/armada/motion/ calls element.animate; use the motion kit (none of its functions is called animate, so name yours otherwise).",
        },
        {
            selector: "NewExpression[callee.name=/^(Animation|KeyframeEffect)$/]",
            message: "Only web/src/armada/motion/ creates an animation; use the motion kit.",
        },
    ],
    audio: [
        {
            selector: "Identifier[name=/^(webkit)?(Offline)?AudioContext$/]",
            message: "Only web/src/armada/sound/ may create or name an AudioContext.",
        },
    ],
    layout_read: [
        {
            selector:
                "CallExpression[callee.property.name=/^(getBoundingClientRect|getClientRects|elementFromPoint|elementsFromPoint|caretPositionFromPoint|caretRangeFromPoint)$/]",
            message: layoutReadMessage,
        },
        {
            selector:
                ":matches(CallExpression[callee.name='getComputedStyle'], CallExpression[callee.property.name='getComputedStyle'])",
            message: layoutReadMessage,
        },
        {
            selector: `MemberExpression:matches([property.name=/^(${layoutReads})$/], [property.value=/^(${layoutReads})$/]):not(AssignmentExpression[operator='='] > .left)`,
            message: layoutReadMessage,
        },
        {
            // const {offsetWidth} = element;
            selector: `ObjectPattern > Property:matches([key.name=/^(${layoutReads})$/], [key.value=/^(${layoutReads})$/])`,
            message: layoutReadMessage,
        },
    ],
    timers: [
        {
            selector:
                ":matches(CallExpression[callee.name=/^set(Timeout|Interval)$/], CallExpression[callee.property.name=/^set(Timeout|Interval)$/])[arguments.1.property.name!=/^(pending|quiet)$/]",
            message:
                "No timers in the feed or the composer except the two named thresholds (pending, quiet).",
        },
    ],
    inner_html: [
        {
            selector:
                ":matches(MemberExpression[property.name=/^(innerHTML|outerHTML)$/], JSXAttribute[name.name='dangerouslySetInnerHTML'], Property:matches([key.name=/^(innerHTML|outerHTML|dangerouslySetInnerHTML)$/], [key.value=/^(innerHTML|outerHTML|dangerouslySetInnerHTML)$/]))",
            message: htmlMessage,
        },
        {
            selector:
                ":matches(CallExpression[callee.property.name=/^(insertAdjacentHTML|setHTML|setHTMLUnsafe|parseHTMLUnsafe|parseFromString|createContextualFragment)$/], CallExpression[callee.object.name='document'][callee.property.name=/^write(ln)?$/])",
            message: htmlMessage,
        },
        {
            selector: "Identifier[name='DOMParser']",
            message: htmlMessage,
        },
    ],
    raw_elements: [
        {
            selector: `JSXOpeningElement[name.name=/^(${rawControls})$/]`,
            message: rawControlMessage,
        },
        {
            // h("button", ...) and document.createElement("button")
            selector: `:matches(CallExpression[callee.name=/^(h|createElement)$/], CallExpression[callee.property.name=/^(h|createElement)$/])[arguments.0.value=/^(${rawControls})$/i]`,
            message: rawControlMessage,
        },
    ],
    network: [
        {
            selector:
                ":matches(CallExpression[callee.name='fetch'], CallExpression[callee.object.name=/^(window|globalThis|self|navigator)$/][callee.property.name=/^(fetch|sendBeacon)$/])",
            message: networkMessage,
        },
        {
            selector: "Identifier[name=/^(XMLHttpRequest|WebSocket|EventSource|WebTransport)$/]",
            message: networkMessage,
        },
    ],
    untagged_text: [
        {
            // JSX is covered by formatjs/no-literal-string-in-jsx; this is
            // the same mistake made with the DOM's own properties.
            selector: `AssignmentExpression[left.property.name=/^(textContent|innerText|title|placeholder|alt|ariaLabel|ariaDescription|ariaPlaceholder)$/] > :matches(Literal[value=/${wordsForPeople}/], ${templateWithWords}).right`,
            message: untaggedMessage,
        },
        {
            selector: `CallExpression[callee.property.name='setAttribute'][arguments.0.value=/^(${textAttributes})$/] > :matches(Literal[value=/${wordsForPeople}/], ${templateWithWords}):nth-child(2)`,
            message: untaggedMessage,
        },
        {
            // One argument: URLSearchParams.append(name, value) is not text.
            selector: `CallExpression[callee.property.name=/^(append|prepend|replaceChildren|createTextNode)$/][arguments.length=1] > :matches(Literal[value=/${wordsForPeople}/], ${templateWithWords})`,
            message: untaggedMessage,
        },
    ],
    old_code_name: [
        {
            selector: `:matches(Literal[value=/${oldCodeName}/], TemplateElement[value.raw=/${oldCodeName}/], JSXText[value=/${oldCodeName}/])`,
            message: oldCodeNameMessage,
        },
        {
            selector: `:matches(Identifier, JSXIdentifier)[name=/${oldCodeName}/]`,
            message: oldCodeNameMessage,
        },
    ],
    // The agent's name, Onix, and Onyx, its name before.
    agent_name: [
        {
            selector:
                ":matches(Literal[value=/on[iy]x/i], TemplateElement[value.raw=/on[iy]x/i], JSXText[value=/on[iy]x/i])",
            message: agentNameMessage,
        },
        {
            selector: ":matches(Identifier, JSXIdentifier)[name=/on[iy]x/i]",
            message: agentNameMessage,
        },
    ],
};

// A later configuration object replaces a rule's options whole, so each
// zone restates everything that applies to it. Zones run from general to
// specific; `off` names the restrictions a zone is exempt from.
const armadaSyntaxZones = [
    {files: [`${armada}/**`], off: ["timers", "raw_elements"]},
    {files: [`${armada}/motion/**`], off: ["timers", "raw_elements", "animate", "layout_read"]},
    {files: [`${armada}/sound/**`], off: ["timers", "raw_elements", "audio"]},
    {files: [`${armada}/api/**`], off: ["timers", "raw_elements", "network"]},
    // One of the four palettes is called Onyx, after the stone.
    {files: [`${armada}/tokens/**`], off: ["timers", "raw_elements", "agent_name"]},
    {files: [`${armada}/frame/after_layout.ts`], off: ["timers", "raw_elements", "layout_read"]},
    {files: [`${armada}/feed/**`], off: ["raw_elements"]},
    {files: [`${armada}/feed/scroll.ts`], off: ["raw_elements", "layout_read"]},
    {
        files: [`${armada}/feed/parts.ts`, `${armada}/feed/stream_writer.ts`],
        off: ["raw_elements", "inner_html"],
    },
    {files: [`${armada}/surfaces/**`], off: ["timers"]},
    {files: [`${armada}/surfaces/composer/**`], off: []},
    {files: [`${armada}/surfaces/composer/origin.ts`], off: ["layout_read"]},
];

/**
 * @param {string[]} off
 * @returns {import("eslint").Linter.RuleEntry}
 */
const armadaSyntaxRule = (off) => [
    "error",
    ...Object.entries(armadaSyntax).flatMap(([name, restrictions]) =>
        off.includes(name) ? [] : restrictions,
    ),
];

const armadaBannedImports = {
    paths: [
        {name: "zod", message: "Use zod/mini."},
        ...["jquery", "handlebars", "tippy.js", "micromodal"].map((name) => ({
            name,
            message: "Not under web/src/armada/: the kit and the motion kit replace it.",
        })),
    ],
    patterns: [
        {
            group: ["*.hbs"],
            message: "No Handlebars under web/src/armada/: chrome is .tsx, the feed is kit/dom.ts.",
        },
        {
            group: ["handlebars/*", "tippy.js/*"],
            message: "Not under web/src/armada/: the kit and the motion kit replace it.",
        },
    ],
};

// Who may import whom (docs/armada/architecture.md, "Layers").
const armadaImportZones = [
    {
        // Bundle entry points may import anything; ui_init.js calls boot.ts.
        target: ["./web/src/*", "./web/src/!(armada|bundles)/**/*"],
        from: `./${armada}`,
        except: ["./hooks", "./boot.ts"],
        message:
            "Old modules reach the rebuild only through web/src/armada/hooks/ (and boot.ts, from ui_init.js).",
    },
    {
        target: [`./${armada}/*`, `./${armada}/!(api)/**/*`],
        from: "./web/src/channel.ts",
        message: "Only web/src/armada/api/ touches the network.",
    },
    {
        target: `./${armada}/kit/**/*`,
        from: `./${armada}`,
        except: ["./kit", "./motion", "./tokens"],
        message: "The kit imports motion and nothing else of Armada's.",
    },
    {
        target: `./${armada}/state/**/*`,
        from: `./${armada}`,
        except: ["./state", "./api"],
        message: "Stores import no DOM module: of Armada's, only state/ and api/.",
    },
    {
        target: `./${armada}/state/**/*`,
        from: "./web/src",
        except: ["./armada"],
        message:
            "Stores import no DOM module: nothing of the old app, which a store reaches through api/.",
    },
    {
        target: `./${armada}/feed/**/*`,
        from: `./${armada}`,
        except: ["./feed", "./kit/dom.ts", "./kit/icons", "./state", "./motion", "./tokens"],
        message: "The feed imports kit/dom.ts, state and motion only.",
    },
    ...armadaSurfaces.map((surface) => ({
        target: `./${armada}/surfaces/${surface}/**/*`,
        from: `./${armada}`,
        except: [
            "./kit",
            "./state",
            "./commands",
            "./motion",
            "./sound",
            "./tokens",
            `./surfaces/${surface}`,
            ...armadaSurfaces
                .filter((other) => other !== surface)
                .map((other) => `./surfaces/${other}/index.tsx`),
        ],
        message:
            "A surface imports kit, state, commands, motion and sound, and never another surface's internals.",
    })),
];

// A selector cannot see a file's name, so this one rule is written here.
/** @type {import("eslint").ESLint.Plugin} */
const armadaPlugin = {
    rules: {
        "file-name": {
            meta: {
                type: "problem",
                schema: [],
                messages: {old_code_name: oldCodeNameMessage, agent_name: agentNameMessage},
            },
            create(context) {
                const name = path
                    .relative(path.join(import.meta.dirname, armada), context.filename)
                    .replaceAll(path.sep, "/");
                return {
                    Program(node) {
                        if (new RegExp(oldCodeName).test(name)) {
                            context.report({node, messageId: "old_code_name"});
                        }
                        if (/on[iy]x/i.test(name) && !name.startsWith("tokens/")) {
                            context.report({node, messageId: "agent_name"});
                        }
                    },
                };
            },
        },
    },
};

export default defineConfig(
    {
        // This is intended for generated files and vendored third-party files.
        // For our source code, instead of adding files here, consider using
        // specific eslint-disable comments in the files themselves.
        ignores: [
            "docs/_build",
            "static/generated",
            "static/webpack-bundles",
            "var",
            "web/generated",
            "web/third",
            // INTERIM BETA (/beta/): the design prototype's kit and a spike,
            // moved in whole and deleted when the rebuild's conversation
            // lands; not held to web/src/armada/'s rules
            // (web/src/armada/beta/README.md).
            "web/src/armada/beta",
        ],
    },
    js.configs.recommended,
    importPlugin.flatConfigs.recommended,
    compat.config(noJquery.configs.recommended),
    compat.config(noJquery.configs.deprecated),
    unicorn.configs.recommended,
    promise.configs["flat/recommended"],
    prettier,
    tseslint.configs.strictTypeChecked,
    tseslint.configs.stylisticTypeChecked,
    mdx.flat,
    {
        files: ["**/*.cts", "**/*.mts", "**/*.ts", "**/*.tsx"],
        extends: [importPlugin.flatConfigs.typescript],
    },
    {
        plugins: {
            formatjs,
            "no-jquery": noJquery,
        },
        linterOptions: {
            reportUnusedDisableDirectives: true,
        },
        languageOptions: {
            ecmaVersion: "latest",
            globals: {
                JQuery: "readonly",
            },
            parserOptions: {
                projectService: true,
                tsConfigRootDir: import.meta.dirname,
                warnOnUnsupportedTypeScriptVersion: false,
            },
        },
        settings: {
            formatjs: {
                additionalFunctionNames: ["$t", "$t_html"],
            },
            "import/resolver": {
                node: {
                    extensions: [".ts", ".tsx", ".d.ts", ".js"],
                },
            },
            "no-jquery": {
                collectionReturningPlugins: {expectOne: "always"},
                variablePattern: "^\\$(?!t$|t_html$).",
            },
        },
        rules: {
            "@typescript-eslint/consistent-return": "error",
            "@typescript-eslint/consistent-type-assertions": ["error", {assertionStyle: "never"}],
            "@typescript-eslint/consistent-type-definitions": ["error", "type"],
            "@typescript-eslint/consistent-type-imports": "error",
            "@typescript-eslint/explicit-function-return-type": ["error", {allowExpressions: true}],
            "@typescript-eslint/member-ordering": "error",
            "@typescript-eslint/method-signature-style": "error",
            "@typescript-eslint/no-loop-func": "error",
            "@typescript-eslint/no-misused-spread": "off",
            "@typescript-eslint/no-non-null-assertion": "off",
            "@typescript-eslint/no-restricted-imports": [
                "error",
                {paths: [{name: "zod", message: "Use zod/mini."}]},
            ],
            "@typescript-eslint/no-unnecessary-condition": "off",
            "@typescript-eslint/no-unnecessary-qualifier": "error",
            "@typescript-eslint/no-unused-vars": [
                "error",
                {args: "all", argsIgnorePattern: "^_", ignoreRestSiblings: true},
            ],
            "@typescript-eslint/no-use-before-define": [
                "error",
                {functions: false, variables: false},
            ],
            "@typescript-eslint/parameter-properties": "error",
            "@typescript-eslint/promise-function-async": "error",
            "@typescript-eslint/restrict-plus-operands": ["error", {}],
            "@typescript-eslint/restrict-template-expressions": ["error", {}],
            "array-callback-return": "error",
            "arrow-body-style": "error",
            curly: "error",
            eqeqeq: "error",
            "formatjs/enforce-default-message": ["error", "literal"],
            "formatjs/enforce-placeholders": [
                "error",
                {ignoreList: ["b", "code", "em", "i", "kbd", "p", "strong"]},
            ],
            "formatjs/no-id": "error",
            "guard-for-in": "error",
            "import/extensions": ["error", "ignorePackages"],
            "import/first": "error",
            "import/newline-after-import": "error",
            "import/no-cycle": ["error", {ignoreExternal: true}],
            "import/no-duplicates": "error",
            "import/no-self-import": "error",
            "import/no-unresolved": "off",
            "import/no-useless-path-segments": "error",
            "import/order": ["error", {alphabetize: {order: "asc"}, "newlines-between": "always"}],
            "import/unambiguous": "error",
            "lines-around-directive": "error",
            "new-cap": "error",
            "no-alert": "error",
            "no-bitwise": "error",
            "no-caller": "error",
            "no-constant-condition": ["error", {checkLoops: false}],
            "no-div-regex": "error",
            "no-else-return": "error",
            "no-eval": "error",
            "no-implicit-coercion": "error",
            "no-jquery/no-append-html": "error",
            "no-jquery/no-constructor-attributes": "error",
            "no-jquery/no-parse-html-literal": "error",
            "no-jquery/no-sizzle": ["error", {}],
            "no-label-var": "error",
            "no-labels": "error",
            "no-multi-str": "error",
            "no-new-func": "error",
            "no-new-wrappers": "error",
            "no-object-constructor": "error",
            "no-octal-escape": "error",
            "no-plusplus": "error",
            "no-proto": "error",
            "no-restricted-globals": ["error", ...confusingBrowserGlobals],
            "no-return-assign": "error",
            "no-script-url": "error",
            "no-self-compare": "error",
            "no-undef": "error",
            "no-undef-init": "error",
            "no-unneeded-ternary": ["error", {defaultAssignment: false}],
            "no-useless-concat": "error",
            "no-var": "error",
            "object-shorthand": ["error", "always", {avoidExplicitReturnArrows: true}],
            "one-var": ["error", "never"],
            "prefer-arrow-callback": "error",
            "prefer-const": ["error", {ignoreReadBeforeAssign: true}],
            "promise/no-promise-in-callback": "off",
            "promise/prefer-await-to-then": ["error", {stict: true}],
            radix: "error",
            "sort-imports": ["error", {ignoreDeclarationSort: true}],
            "spaced-comment": ["error", "always", {markers: ["/"]}],
            strict: "error",
            "unicorn/consistent-function-scoping": "off",
            "unicorn/filename-case": "off",
            "unicorn/no-await-expression-member": "off",
            "unicorn/no-negated-condition": "off",
            "unicorn/no-null": "off",
            "unicorn/no-process-exit": "off",
            "unicorn/no-useless-undefined": "off",
            "unicorn/numeric-separators-style": "off",
            "unicorn/prefer-dom-node-dataset": "off",
            "unicorn/prefer-global-this": "off",
            "unicorn/prefer-string-raw": "off",
            "unicorn/prefer-ternary": "off",
            "unicorn/prefer-top-level-await": "off",
            "unicorn/prevent-abbreviations": "off",
            "unicorn/switch-case-braces": "off",
            "valid-typeof": ["error", {requireStringLiterals: true}],
            yoda: "error",
        },
    },
    {
        ignores: ["**/*.cts", "**/*.mts", "**/*.ts", "**/*.tsx"],
        extends: [tseslint.configs.disableTypeChecked],
        rules: {
            "@typescript-eslint/consistent-type-imports": "off",
            "@typescript-eslint/explicit-function-return-type": "off",
            "@typescript-eslint/no-require-imports": "off",
            "consistent-return": "error",
            "dot-notation": "error",
            "no-implied-eval": "error",
            "no-throw-literal": "error",
        },
    },
    {
        files: ["**/*.cjs"],
        languageOptions: {
            sourceType: "commonjs",
        },
    },
    {
        files: ["**/*.mdx"],
        rules: {
            "@typescript-eslint/no-unused-vars": "off",
            "comma-spacing": "error",
            "import/extensions": "off",
            "import/unambiguous": "off",
            quotes: "error",
        },
    },
    {
        files: ["**/*.md"],
        rules: {
            "import/unambiguous": "off",
        },
    },
    {
        files: ["web/tests/**"],
        rules: {
            "@typescript-eslint/no-empty-function": "off",
            "@typescript-eslint/no-extraneous-class": "off",
            "no-jquery/no-selector-prop": "off",
            "no-redeclare": "off",
        },
    },
    {
        files: ["web/e2e-tests/**"],
        languageOptions: {
            globals: {
                zulip_test: "readonly",
            },
        },
    },
    {
        files: ["**/*.d.ts"],
        rules: {
            "import/unambiguous": "off",
        },
    },
    {
        ignores: ["web/src/**"],
        languageOptions: {
            globals: globals.node,
        },
    },
    {
        files: ["web/e2e-tests/**", "web/tests/**"],
        languageOptions: {
            globals: {
                CSS: "readonly",
                document: "readonly",
                navigator: "readonly",
                window: "readonly",
            },
        },
        rules: {
            "formatjs/no-id": "off",
            "new-cap": "off",
        },
    },
    {
        files: ["web/debug-require.cjs"],
        rules: {
            "no-var": "off",
            "object-shorthand": "off",
            "prefer-arrow-callback": "off",
        },
    },
    {
        files: ["web/src/**"],
        settings: {
            "import/resolver": {
                webpack: {
                    config: {},
                },
            },
        },
        rules: {
            "no-console": "error",
        },
    },
    {
        files: ["web/src/**"],
        languageOptions: {
            globals: {
                ...globals.browser,
                DEVELOPMENT: "readonly",
                StripeCheckout: "readonly",
                ZULIP_VERSION: "readonly",
            },
        },
    },
    {
        files: ["web/src/**"],
        rules: {
            "import/no-restricted-paths": [
                "error",
                {basePath: import.meta.dirname, zones: armadaImportZones},
            ],
        },
    },
    {
        files: [`${armada}/**`],
        plugins: {armada: armadaPlugin},
        rules: {
            "armada/file-name": "error",
            "@typescript-eslint/no-restricted-imports": ["error", armadaBannedImports],
            "formatjs/no-literal-string-in-jsx": "error",
        },
    },
    {
        files: [`${armada}/feed/**`],
        rules: {
            "@typescript-eslint/no-restricted-imports": [
                "error",
                {
                    paths: [
                        ...armadaBannedImports.paths,
                        {
                            name: "preact",
                            message: "The feed is an imperative renderer: no Preact in it.",
                        },
                    ],
                    patterns: [
                        ...armadaBannedImports.patterns,
                        {
                            group: ["preact/*", "*.tsx"],
                            message: "The feed is an imperative renderer: no Preact in it.",
                        },
                    ],
                },
            ],
        },
    },
    ...armadaSyntaxZones.map(({files, off}) => ({
        files,
        rules: {"no-restricted-syntax": armadaSyntaxRule(off)},
    })),
    {
        files: ["starlight_help/src/scripts/client/**"],
        rules: {
            "unicorn/prefer-module": "off",
        },
        languageOptions: {
            globals: {
                ...globals.browser,
            },
            sourceType: "script",
        },
    },
    astroConfigs.recommended,
    {
        files: ["starlight_help/src/components/ZulipNote.astro"],
        rules: {
            "import/unambiguous": "off",
        },
    },
    {
        files: ["starlight_help/src/content/include/*"],
        rules: {
            // We need to turn off this rule since we want import statements
            // to be easily copy-paste-able between content/include and
            // content/docs.
            "import/no-useless-path-segments": "off",
        },
    },
);
