import {LanguageDescription} from "@codemirror/language";
import {CrepeBuilder} from "@milkdown/crepe/builder";
import {blockEdit} from "@milkdown/crepe/feature/block-edit";
import {codeMirror} from "@milkdown/crepe/feature/code-mirror";
import {latex} from "@milkdown/crepe/feature/latex";
import {linkTooltip} from "@milkdown/crepe/feature/link-tooltip";
import {listItem} from "@milkdown/crepe/feature/list-item";
import {placeholder} from "@milkdown/crepe/feature/placeholder";
import {table} from "@milkdown/crepe/feature/table";
import {toolbar} from "@milkdown/crepe/feature/toolbar";
import {editorViewCtx, parserCtx, prosePluginsCtx, serializerCtx} from "@milkdown/kit/core";
import {EditorState, Plugin, PluginKey, TextSelection} from "@milkdown/kit/prose/state";

// Webpack extracts these dynamic imports into the Rich editor's lazy CSS
// chunk. Chat keeps only the shared KaTeX CSS in bundles/app.ts.
const rich_styles_ready =
    globalThis.process?.env["NODE_ENV"] === "test"
        ? Promise.resolve()
        : Promise.all([
              import("@milkdown/crepe/theme/common/style.css"),
              import("@milkdown/crepe/theme/frame.css"),
          ]).then(() => undefined);

export type ArmadaWorkspaceRichSelection = {
    anchor: number;
    head: number;
};

export type ArmadaWorkspaceRichViewState = {
    selection: ArmadaWorkspaceRichSelection;
    scrollTop: number;
};

export type ArmadaWorkspaceRichSelectionContext = {
    selectedText: string;
};

export type ArmadaWorkspaceRichEligibility =
    | {eligible: true}
    | {
          eligible: false;
          code:
              | "frontmatter"
              | "raw_html"
              | "reference"
              | "footnote"
              | "mdx"
              | "escape"
              | "extension"
              | "round_trip"
              | "parse_error"
              | "style_error";
          reason: string;
      };

export type ArmadaWorkspaceRichDirtyEvent = {
    origin: "user";
    editRevision: number;
};

export type ArmadaWorkspaceRichSerializationResult =
    | {
          status: "serialized";
          editRevision: number;
          markdown: string;
      }
    | {
          status: "stale";
          requestedRevision: number;
          currentRevision: number;
      }
    | {
          status: "error";
          editRevision: number;
          message: string;
      };

export type ArmadaWorkspaceMarkdownEditorOptions = {
    root: HTMLElement;
    markdown: string;
    editRevision: number;
    readOnly?: boolean;
    placeholder?: string;
    selection?: ArmadaWorkspaceRichSelection | undefined;
    scrollTop?: number;
    onDirty: (event: ArmadaWorkspaceRichDirtyEvent) => void;
    onSerialized: (
        result: Extract<ArmadaWorkspaceRichSerializationResult, {status: "serialized"}>,
    ) => void;
    onSerializationError?: (
        result: Extract<ArmadaWorkspaceRichSerializationResult, {status: "error"}>,
    ) => void;
};

export type ArmadaWorkspaceMarkdownHydration = {
    markdown: string;
    editRevision: number;
    selection?: ArmadaWorkspaceRichSelection | undefined;
    scrollTop?: number;
};

export type ArmadaWorkspaceMarkdownEditorStats = {
    serializations: number;
};

export type ArmadaWorkspaceMarkdownEditor = {
    flush: (expected_revision?: number) => Promise<ArmadaWorkspaceRichSerializationResult>;
    hydrate: (hydration: ArmadaWorkspaceMarkdownHydration) => ArmadaWorkspaceRichEligibility;
    setReadOnly: (read_only: boolean) => void;
    getViewState: () => ArmadaWorkspaceRichViewState;
    getSelectionContext: () => ArmadaWorkspaceRichSelectionContext;
    getCursorLine: () => number;
    setCursorLine: (line: number) => void;
    getEditRevision: () => number;
    getStats: () => ArmadaWorkspaceMarkdownEditorStats;
    focus: () => void;
    hasFocus: () => boolean;
    destroy: () => Promise<void>;
};

export type ArmadaWorkspaceMarkdownEditorResult =
    | {ok: true; editor: ArmadaWorkspaceMarkdownEditor}
    | {ok: false; eligibility: Exclude<ArmadaWorkspaceRichEligibility, {eligible: true}>};

type TransactionOrigin = "hydrate";
type TransactionTracker = {userChanges: number};

const transaction_origin_key = new PluginKey<TransactionTracker>("legacy-workspace-rich-origin");

const rich_code_languages = [
    LanguageDescription.of({
        name: "Markdown",
        extensions: ["md", "markdown"],
        async load() {
            const {markdown} = await import("@codemirror/lang-markdown");
            return markdown();
        },
    }),
    LanguageDescription.of({
        name: "JavaScript",
        alias: ["ecmascript", "js", "jsx"],
        extensions: ["js", "jsx", "mjs", "cjs"],
        async load() {
            const {javascript} = await import("@codemirror/lang-javascript");
            return javascript({jsx: true});
        },
    }),
    LanguageDescription.of({
        name: "TypeScript",
        alias: ["ts", "tsx"],
        extensions: ["ts", "tsx", "mts", "cts"],
        async load() {
            const {javascript} = await import("@codemirror/lang-javascript");
            return javascript({jsx: true, typescript: true});
        },
    }),
    LanguageDescription.of({
        name: "JSON",
        extensions: ["json"],
        async load() {
            const {json} = await import("@codemirror/lang-json");
            return json();
        },
    }),
    LanguageDescription.of({
        name: "Python",
        alias: ["py"],
        extensions: ["py", "pyi"],
        async load() {
            const {python} = await import("@codemirror/lang-python");
            return python();
        },
    }),
    LanguageDescription.of({
        name: "CSS",
        extensions: ["css"],
        async load() {
            const {css} = await import("@codemirror/lang-css");
            return css();
        },
    }),
    LanguageDescription.of({
        name: "HTML",
        extensions: ["html", "htm"],
        async load() {
            const {html} = await import("@codemirror/lang-html");
            return html();
        },
    }),
    LanguageDescription.of({
        name: "YAML",
        alias: ["yml"],
        extensions: ["yaml", "yml"],
        async load() {
            const {yaml} = await import("@codemirror/lang-yaml");
            return yaml();
        },
    }),
];

function replace_non_newlines_with_spaces(value: string): string {
    return value.replaceAll(/[^\n]/g, " ");
}

function mask_fenced_and_indented_code(markdown: string): string {
    const lines = markdown.split(/(?<=\n)/);
    let fence: {character: "`" | "~"; length: number} | undefined;
    return lines
        .map((line) => {
            const content = line.endsWith("\n") ? line.slice(0, -1) : line;
            if (fence !== undefined) {
                const closing = new RegExp(
                    `^ {0,3}${fence.character === "`" ? "`" : "~"}{${fence.length},}\\s*$`,
                );
                if (closing.test(content)) {
                    fence = undefined;
                }
                return replace_non_newlines_with_spaces(line);
            }

            const opening = /^ {0,3}(`{3,}|~{3,})/.exec(content);
            const marker = opening?.[1];
            if (marker !== undefined) {
                fence = {
                    character: marker.startsWith("`") ? "`" : "~",
                    length: marker.length,
                };
                return replace_non_newlines_with_spaces(line);
            }
            if (/^(?: {4}|\t)/.test(content)) {
                return replace_non_newlines_with_spaces(line);
            }
            return line;
        })
        .join("");
}

function mask_inline_code(markdown: string): string {
    // String offsets used below are UTF-16 code-unit offsets. Do not spread
    // the string: spreading combines surrogate pairs and shifts the mask.
    const characters = Array.from(
        {length: markdown.length},
        (_unused, index) => markdown[index] ?? "",
    );
    let index = 0;
    while (index < markdown.length) {
        if (markdown[index] !== "`") {
            index += 1;
            continue;
        }
        let run_length = 1;
        while (markdown[index + run_length] === "`") {
            run_length += 1;
        }
        const marker = "`".repeat(run_length);
        const close = markdown.indexOf(marker, index + run_length);
        if (close === -1) {
            index += run_length;
            continue;
        }
        for (let masked = index; masked < close + run_length; masked += 1) {
            if (characters[masked] !== "\n") {
                characters[masked] = " ";
            }
        }
        index = close + run_length;
    }
    return characters.join("");
}

function is_escaped(value: string, index: number): boolean {
    let slashes = 0;
    for (let cursor = index - 1; cursor >= 0 && value[cursor] === "\\"; cursor -= 1) {
        slashes += 1;
    }
    return slashes % 2 === 1;
}

function mask_math(markdown: string): string {
    const characters = Array.from(
        {length: markdown.length},
        (_unused, index) => markdown[index] ?? "",
    );
    let index = 0;
    while (index < markdown.length) {
        if (markdown[index] !== "$" || is_escaped(markdown, index)) {
            index += 1;
            continue;
        }
        const marker = markdown[index + 1] === "$" ? "$$" : "$";
        const start = index;
        index += marker.length;
        let close = -1;
        while (index < markdown.length) {
            const candidate = markdown.indexOf(marker, index);
            if (candidate === -1) {
                break;
            }
            if (!is_escaped(markdown, candidate)) {
                close = candidate;
                break;
            }
            index = candidate + marker.length;
        }
        if (close === -1) {
            index = start + marker.length;
            continue;
        }
        for (let masked = start; masked < close + marker.length; masked += 1) {
            if (characters[masked] !== "\n") {
                characters[masked] = " ";
            }
        }
        index = close + marker.length;
    }
    return characters.join("");
}

export function normalize_safe_rich_breaks(markdown: string): string {
    // Only normalize forms with an unambiguous Markdown equivalent: an inline
    // break between text becomes a hard break, and a standalone spacer already
    // surrounded by blank lines is redundant. Boundary and line-adjacent tags
    // remain visible to the raw-HTML guard below.
    markdown = markdown.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
    const visible = mask_math(mask_inline_code(mask_fenced_and_indented_code(markdown)));
    const matches = [...visible.matchAll(/<br\s*\/?>/gi)];
    const line_starts = [0];
    for (const newline of visible.matchAll(/\n/g)) {
        line_starts.push(newline.index + 1);
    }
    let line_index = 0;
    const located_matches = matches.flatMap((match) => {
        const start = match.index;
        if (start === undefined) {
            return [];
        }
        while (line_starts[line_index + 1] !== undefined && line_starts[line_index + 1]! <= start) {
            line_index += 1;
        }
        return [{match, start, lineIndex: line_index}];
    });
    const matches_per_line = new Map<number, number>();
    for (const {lineIndex} of located_matches) {
        matches_per_line.set(lineIndex, (matches_per_line.get(lineIndex) ?? 0) + 1);
    }
    const replacements: {start: number; end: number; value: string}[] = [];
    for (const {match, start, lineIndex} of located_matches) {
        const end = start + match[0].length;
        const line_start = line_starts[lineIndex]!;
        const next_line_start = line_starts[lineIndex + 1];
        const next_newline = next_line_start === undefined ? -1 : next_line_start - 1;
        const line_end = next_newline === -1 ? visible.length : next_newline;
        if (matches_per_line.get(lineIndex) !== 1 || is_escaped(visible, start)) {
            continue;
        }
        const has_prefix = visible.slice(line_start, start).trim() !== "";
        const has_suffix = visible.slice(end, line_end).trim() !== "";
        if (has_prefix && has_suffix) {
            replacements.push({start, end, value: "  \n"});
            continue;
        }
        if (has_prefix || has_suffix || lineIndex === 0 || next_line_start === undefined) {
            continue;
        }
        const previous_line_start = line_starts[lineIndex - 1]!;
        const previous_line = visible.slice(previous_line_start, line_start - 1).trim();
        const following_line_start = next_line_start;
        const following_line_end = line_starts[lineIndex + 2] ?? visible.length;
        const following_line = visible.slice(following_line_start, following_line_end).trim();
        if (previous_line === "" && following_line === "") {
            replacements.push({start: line_start, end: next_line_start, value: ""});
        }
    }
    const parts: string[] = [];
    let cursor = 0;
    for (const replacement of replacements) {
        if (replacement.start < cursor) {
            continue;
        }
        parts.push(markdown.slice(cursor, replacement.start), replacement.value);
        cursor = replacement.end;
    }
    parts.push(markdown.slice(cursor));
    return parts.join("");
}

function source_only(
    code: Exclude<ArmadaWorkspaceRichEligibility, {eligible: true}>["code"],
    reason: string,
): Exclude<ArmadaWorkspaceRichEligibility, {eligible: true}> {
    return {eligible: false, code, reason};
}

function parse_failure(error: unknown): Exclude<ArmadaWorkspaceRichEligibility, {eligible: true}> {
    const detail = error instanceof Error ? ` ${error.message}` : "";
    return source_only("parse_error", `Rich mode could not parse this Markdown safely.${detail}`);
}

export function rich_source_only_reason(
    markdown: string,
): Exclude<ArmadaWorkspaceRichEligibility, {eligible: true}> | undefined {
    const normalized = markdown.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
    const without_bom = normalized.startsWith("\uFEFF") ? normalized.slice(1) : normalized;
    const lines = without_bom.split("\n");
    const first_line = lines[0]?.trim();
    if (first_line === "---" && lines.slice(1).some((line) => /^(?:---|\.\.\.)\s*$/.test(line))) {
        return source_only("frontmatter", "YAML frontmatter is available in Source mode only.");
    }
    if (first_line === "+++" && lines.slice(1).some((line) => /^\+\+\+\s*$/.test(line))) {
        return source_only("frontmatter", "TOML frontmatter is available in Source mode only.");
    }

    const original_visible = mask_math(
        mask_inline_code(mask_fenced_and_indented_code(without_bom)),
    );
    if (/\\[!-/:-@[-`{-~]/.test(original_visible)) {
        return source_only(
            "escape",
            "Backslash-escaped Markdown is available in Source mode only.",
        );
    }
    const rich_markdown = normalize_safe_rich_breaks(without_bom);
    const visible = mask_math(mask_inline_code(mask_fenced_and_indented_code(rich_markdown)));
    if (/<!--|<!\[CDATA\[|<!DOCTYPE\s|<\?|<\/?[A-Za-z][\w:.-]*(?:\s|\/?>)/i.test(visible)) {
        return source_only(
            "raw_html",
            "Raw HTML and HTML comments are available in Source mode only.",
        );
    }
    if (/^[ \t]{0,3}\[(?!\^)[^\]\n]+\]:[ \t]*\S/m.test(visible)) {
        return source_only(
            "reference",
            "Reference-style link definitions are available in Source mode only.",
        );
    }
    if (/!?\[[^\]\n]*\][ \t]*\[[^\]\n]*\]/.test(visible)) {
        return source_only("reference", "Reference-style links are available in Source mode only.");
    }
    if (/\[\^[^\]\n]+\]/.test(visible)) {
        return source_only("footnote", "Footnotes are available in Source mode only.");
    }
    if (
        /^[ \t]{0,3}(?:import\s+(?:[\w*{]|["'])|export\s+(?:default|const|let|var|function|class|\{|\*))/m.test(
            visible,
        ) ||
        /[{}]/.test(visible) ||
        /<\/?>/.test(visible)
    ) {
        return source_only(
            "mdx",
            "MDX expressions, brace extensions, and module syntax are available in Source mode only.",
        );
    }
    if (
        /^[ \t]{0,3}:::+(?:\s|$)/m.test(visible) ||
        /(^|\s):[A-Za-z][\w-]*\[[^\]\n]*\]/m.test(visible) ||
        /\[\[[^\]\n]+\]\]/.test(visible)
    ) {
        return source_only("extension", "This Markdown extension is not supported by Rich mode.");
    }
    return undefined;
}

function build_editor(
    root: HTMLElement,
    markdown: string,
    placeholder_text: string,
    on_user_doc_changed?: () => void,
): CrepeBuilder {
    const builder = new CrepeBuilder({root, defaultValue: markdown})
        .addFeature(codeMirror, {languages: rich_code_languages})
        .addFeature(listItem)
        .addFeature(linkTooltip)
        .addFeature(table)
        .addFeature(latex, {katexOptions: {throwOnError: false}})
        .addFeature(toolbar)
        .addFeature(blockEdit, {advancedGroup: {image: null}})
        .addFeature(placeholder, {text: placeholder_text, mode: "block"});

    if (on_user_doc_changed !== undefined) {
        builder.editor.config((ctx) => {
            ctx.update(prosePluginsCtx, (plugins) => [
                ...plugins,
                new Plugin<TransactionTracker>({
                    key: transaction_origin_key,
                    state: {
                        init: () => ({userChanges: 0}),
                        apply(transaction, tracker) {
                            if (
                                transaction.docChanged &&
                                transaction.getMeta("appendedTransaction") === undefined &&
                                transaction.getMeta("addToHistory") !== false &&
                                transaction.getMeta(transaction_origin_key) !== "hydrate"
                            ) {
                                return {userChanges: tracker.userChanges + 1};
                            }
                            return tracker;
                        },
                    },
                    view(editor_view) {
                        let delivered_changes =
                            transaction_origin_key.getState(editor_view.state)?.userChanges ?? 0;
                        return {
                            update(next_view) {
                                const next_changes =
                                    transaction_origin_key.getState(next_view.state)?.userChanges ??
                                    delivered_changes;
                                if (next_changes < delivered_changes) {
                                    // A remote hydration replaces EditorState
                                    // to clear history and resets plugin state.
                                    delivered_changes = next_changes;
                                }
                                while (delivered_changes < next_changes) {
                                    delivered_changes += 1;
                                    on_user_doc_changed();
                                }
                            },
                        };
                    },
                }),
            ]);
        });
    }
    return builder;
}

function semantic_round_trip(
    builder: CrepeBuilder,
    markdown: string,
): ArmadaWorkspaceRichEligibility {
    try {
        let equivalent = false;
        builder.editor.action((ctx) => {
            const parse = ctx.get(parserCtx);
            const serialize = ctx.get(serializerCtx);
            const parsed = parse(markdown);
            const reparsed = parse(serialize(parsed));
            equivalent = documents_are_semantically_equal(parsed.toJSON(), reparsed.toJSON());
        });
        if (!equivalent) {
            return source_only(
                "round_trip",
                "Rich mode cannot preserve this document's Markdown semantics.",
            );
        }
        return {eligible: true};
    } catch {
        return source_only("parse_error", "Rich mode could not parse this Markdown safely.");
    }
}

type SemanticJsonObject = {
    [key: string]: unknown;
    type?: unknown;
    content?: unknown;
};

function semantic_json(value: unknown): unknown {
    if (Array.isArray(value)) {
        return value.map((item) => semantic_json(item));
    }
    if (value === null || typeof value !== "object") {
        return value;
    }
    const normalized: SemanticJsonObject = Object.fromEntries(
        Object.entries(value)
            // Milkdown's parser can toggle these source-formatting attributes
            // while serializing the same list/mark semantics. Content-bearing
            // attributes (href, title, checked, order, language, math value,
            // table alignment, and so on) remain part of the comparison.
            .filter(([key]) => !["id", "label", "listType", "marker", "spread"].includes(key))
            .map(([key, nested]) => [key, semantic_json(nested)]),
    );
    if (normalized.type === "doc" && Array.isArray(normalized.content)) {
        const content = normalized.content.map((item: unknown) => item);
        while (is_empty_paragraph(content.at(-1))) {
            content.pop();
        }
        if (content.length === 0) {
            delete normalized.content;
        } else {
            normalized.content = content;
        }
    }
    return normalized;
}

function is_empty_paragraph(value: unknown): boolean {
    return (
        value !== null &&
        typeof value === "object" &&
        "type" in value &&
        value.type === "paragraph" &&
        !("content" in value)
    );
}

function documents_are_semantically_equal(left: unknown, right: unknown): boolean {
    return JSON.stringify(semantic_json(left)) === JSON.stringify(semantic_json(right));
}

export async function can_enter_rich(markdown: string): Promise<ArmadaWorkspaceRichEligibility> {
    const unsupported = rich_source_only_reason(markdown);
    if (unsupported !== undefined) {
        return unsupported;
    }
    const rich_markdown = normalize_safe_rich_breaks(markdown);
    const detached_root = document.createElement("div");
    const builder = build_editor(detached_root, rich_markdown, "Write Markdown…");
    builder.setReadonly(true);
    let created = false;
    try {
        await builder.create();
        created = true;
        return semantic_round_trip(builder, rich_markdown);
    } catch (error) {
        // Milkdown leaves the editor in OnCreate after a rejected create(). Its
        // destroy() polls forever in that state, so only destroy completed
        // instances. The candidate root is detached and removed either way.
        return parse_failure(error);
    } finally {
        if (created) {
            await builder.destroy();
        }
        detached_root.remove();
    }
}

function clamp_position(position: number, document_size: number): number {
    if (!Number.isFinite(position)) {
        return 0;
    }
    return Math.max(0, Math.min(Math.trunc(position), document_size));
}

export async function create_armada_workspace_markdown_editor(
    options: ArmadaWorkspaceMarkdownEditorOptions,
): Promise<ArmadaWorkspaceMarkdownEditorResult> {
    const unsupported = rich_source_only_reason(options.markdown);
    if (unsupported !== undefined) {
        return {ok: false, eligibility: unsupported};
    }
    const rich_markdown = normalize_safe_rich_breaks(options.markdown);
    try {
        await rich_styles_ready;
    } catch {
        return {
            ok: false,
            eligibility: source_only(
                "style_error",
                "Rich editor styles could not be loaded safely.",
            ),
        };
    }

    const host = document.createElement("div");
    host.className = "legacy-workspace-rich-editor";
    let current_revision = options.editRevision;
    let destroyed = false;
    let scheduled:
        | {kind: "idle"; handle: number}
        | {kind: "timeout"; handle: ReturnType<typeof setTimeout>}
        | undefined;
    let cached_result: ArmadaWorkspaceRichSerializationResult | undefined;
    let serialization_count = 0;
    let accept_user_changes = false;

    const cancel_scheduled = (): void => {
        if (scheduled?.kind === "idle") {
            globalThis.cancelIdleCallback?.(scheduled.handle);
        } else if (scheduled?.kind === "timeout") {
            clearTimeout(scheduled.handle);
        }
        scheduled = undefined;
    };

    let flush_current: () => Promise<ArmadaWorkspaceRichSerializationResult>;
    const schedule_serialization = (): void => {
        if (destroyed || scheduled !== undefined) {
            return;
        }
        const callback = (): void => {
            scheduled = undefined;
            void flush_current();
        };
        if (typeof globalThis.requestIdleCallback === "function") {
            scheduled = {
                kind: "idle",
                handle: globalThis.requestIdleCallback(callback, {timeout: 250}),
            };
        } else {
            scheduled = {kind: "timeout", handle: setTimeout(callback, 0)};
        }
    };

    const builder = build_editor(
        host,
        rich_markdown,
        options.placeholder ?? "Write Markdown…",
        () => {
            if (!accept_user_changes) {
                return;
            }
            current_revision += 1;
            cached_result = undefined;
            options.onDirty({origin: "user", editRevision: current_revision});
            schedule_serialization();
        },
    );
    builder.setReadonly(options.readOnly ?? false);

    try {
        await builder.create();
    } catch (error) {
        host.remove();
        return {
            ok: false,
            eligibility: parse_failure(error),
        };
    }
    const eligibility = semantic_round_trip(builder, rich_markdown);
    if (!eligibility.eligible) {
        await builder.destroy();
        return {ok: false, eligibility};
    }

    const view = builder.editor.action((ctx) => ctx.get(editorViewCtx));
    const apply_selection = (selection: ArmadaWorkspaceRichSelection | undefined): void => {
        if (selection === undefined) {
            return;
        }
        const document_size = view.state.doc.content.size;
        const anchor = clamp_position(selection.anchor, document_size);
        const head = clamp_position(selection.head, document_size);
        const transaction = view.state.tr
            .setSelection(
                TextSelection.between(view.state.doc.resolve(anchor), view.state.doc.resolve(head)),
            )
            .setMeta(transaction_origin_key, "hydrate" satisfies TransactionOrigin);
        view.dispatch(transaction);
    };
    const rendered_line_at_position = (position: number): number => {
        const document_size = view.state.doc.content.size;
        const prefix = view.state.doc.textBetween(0, clamp_position(position, document_size), "\n");
        return prefix.split("\n").length - 1;
    };
    const set_cursor_line = (line: number): void => {
        if (destroyed) {
            throw new Error("Cannot set selection on a destroyed Rich Markdown editor.");
        }
        const document_size = view.state.doc.content.size;
        const target_line = Math.max(
            0,
            Math.min(Math.trunc(line), rendered_line_at_position(document_size)),
        );
        let low = 0;
        let high = document_size;
        while (low < high) {
            const middle = Math.floor((low + high) / 2);
            if (rendered_line_at_position(middle) < target_line) {
                low = middle + 1;
            } else {
                high = middle;
            }
        }
        const selection = TextSelection.near(view.state.doc.resolve(low), 1);
        view.dispatch(
            view.state.tr
                .setSelection(selection)
                .setMeta(transaction_origin_key, "hydrate" satisfies TransactionOrigin)
                .setMeta("addToHistory", false),
        );
    };
    apply_selection(options.selection);
    view.dom.scrollTop = Math.max(0, options.scrollTop ?? 0);
    options.root.replaceChildren(host);
    accept_user_changes = true;

    const serialize_revision = (revision: number): ArmadaWorkspaceRichSerializationResult => {
        if (
            cached_result !== undefined &&
            cached_result.status !== "stale" &&
            cached_result.editRevision === revision
        ) {
            return cached_result;
        }
        serialization_count += 1;
        let result: ArmadaWorkspaceRichSerializationResult;
        try {
            let markdown = "";
            let equivalent = false;
            builder.editor.action((ctx) => {
                const serialize = ctx.get(serializerCtx);
                const parse = ctx.get(parserCtx);
                const current_document = ctx.get(editorViewCtx).state.doc;
                markdown = serialize(current_document);
                equivalent = documents_are_semantically_equal(
                    current_document.toJSON(),
                    parse(markdown).toJSON(),
                );
            });
            if (!equivalent) {
                throw new Error("Rich serialization changed the Markdown document semantics.");
            }
            result = {
                status: "serialized" as const,
                editRevision: revision,
                markdown,
            };
        } catch (error) {
            result = {
                status: "error" as const,
                editRevision: revision,
                message:
                    error instanceof Error ? error.message : "Rich Markdown serialization failed.",
            };
        }
        cached_result = result;
        if (result.status === "serialized") {
            options.onSerialized(result);
        } else {
            options.onSerializationError?.(result);
        }
        return result;
    };

    // Serialization itself is intentionally synchronous once a flush is due;
    // the Promise keeps the orchestrator contract uniform with future workers.
    /* eslint-disable @typescript-eslint/promise-function-async */
    const flush = (
        expected_revision = current_revision,
    ): Promise<ArmadaWorkspaceRichSerializationResult> => {
        if (destroyed) {
            return Promise.resolve({
                status: "error",
                editRevision: expected_revision,
                message: "The Rich Markdown editor has been destroyed.",
            });
        }
        if (expected_revision !== current_revision) {
            return Promise.resolve({
                status: "stale",
                requestedRevision: expected_revision,
                currentRevision: current_revision,
            });
        }
        cancel_scheduled();
        return Promise.resolve(serialize_revision(expected_revision));
    };
    /* eslint-enable @typescript-eslint/promise-function-async */
    flush_current = flush;

    const hydrate = (
        hydration: ArmadaWorkspaceMarkdownHydration,
    ): ArmadaWorkspaceRichEligibility => {
        if (destroyed) {
            throw new Error("Cannot hydrate a destroyed Rich Markdown editor.");
        }
        if (
            cached_result?.status === "serialized" &&
            cached_result.editRevision === hydration.editRevision &&
            cached_result.markdown === hydration.markdown
        ) {
            // Acknowledge the adapter's own serialized snapshot without
            // replacing/reparsing the ProseMirror document or its history.
            apply_selection(hydration.selection);
            view.dom.scrollTop = Math.max(0, hydration.scrollTop ?? view.dom.scrollTop);
            return {eligible: true};
        }
        const source_reason = rich_source_only_reason(hydration.markdown);
        if (source_reason !== undefined) {
            return source_reason;
        }
        const rich_markdown = normalize_safe_rich_breaks(hydration.markdown);
        const next_eligibility = semantic_round_trip(builder, rich_markdown);
        if (!next_eligibility.eligible) {
            return next_eligibility;
        }

        let next_document = view.state.doc;
        builder.editor.action((ctx) => {
            next_document = ctx.get(parserCtx)(rich_markdown);
        });
        const requested_selection = hydration.selection ?? {
            anchor: view.state.selection.anchor,
            head: view.state.selection.head,
        };
        const document_size = next_document.content.size;
        const anchor = clamp_position(requested_selection.anchor, document_size);
        const head = clamp_position(requested_selection.head, document_size);
        const selection = TextSelection.between(
            next_document.resolve(anchor),
            next_document.resolve(head),
        );
        // A replacement transaction with addToHistory=false still maps the
        // previous history branches across this new document. Constructing a
        // fresh state is the fail-closed file/revision boundary: undo/redo and
        // every other per-document plugin field are reinitialized, while the
        // production schema and plugin set remain identical.
        view.updateState(
            EditorState.create({
                doc: next_document,
                selection,
                plugins: view.state.plugins,
            }),
        );
        // Let Milkdown's trailing-node plugin restore its editor-only final
        // paragraph without making that normalization undoable or dirty.
        view.dispatch(
            view.state.tr
                .setMeta(transaction_origin_key, "hydrate" satisfies TransactionOrigin)
                .setMeta("addToHistory", false),
        );
        view.dom.scrollTop = Math.max(0, hydration.scrollTop ?? view.dom.scrollTop);
        current_revision = hydration.editRevision;
        cached_result = undefined;
        cancel_scheduled();
        return {eligible: true};
    };

    let destroy_promise: Promise<void> | undefined;
    // Keep repeated teardown calls on the exact same promise.
    // eslint-disable-next-line @typescript-eslint/promise-function-async
    const destroy = (): Promise<void> => {
        if (destroy_promise !== undefined) {
            return destroy_promise;
        }
        destroyed = true;
        cancel_scheduled();
        destroy_promise = (async () => {
            try {
                await builder.destroy();
            } finally {
                host.remove();
            }
        })();
        return destroy_promise;
    };

    return {
        ok: true,
        editor: {
            flush,
            hydrate,
            setReadOnly(read_only) {
                if (!destroyed) {
                    builder.setReadonly(read_only);
                }
            },
            getViewState() {
                if (destroyed) {
                    throw new Error(
                        "Cannot read view state from a destroyed Rich Markdown editor.",
                    );
                }
                const {anchor, head} = view.state.selection;
                return {selection: {anchor, head}, scrollTop: view.dom.scrollTop};
            },
            getSelectionContext() {
                if (destroyed) {
                    throw new Error("Cannot read selection from a destroyed Rich Markdown editor.");
                }
                const {from, to} = view.state.selection;
                return {selectedText: view.state.doc.textBetween(from, to, "\n")};
            },
            getCursorLine: () => rendered_line_at_position(view.state.selection.head),
            setCursorLine: set_cursor_line,
            getEditRevision: () => current_revision,
            getStats: () => ({serializations: serialization_count}),
            focus() {
                if (destroyed) {
                    throw new Error("Cannot focus a destroyed Rich Markdown editor.");
                }
                view.focus();
            },
            hasFocus: () => !destroyed && view.hasFocus(),
            destroy,
        },
    };
}
