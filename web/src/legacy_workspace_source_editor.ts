import {autocompletion, closeBrackets, closeBracketsKeymap} from "@codemirror/autocomplete";
import {defaultKeymap, history, historyKeymap, indentWithTab} from "@codemirror/commands";
import {
    HighlightStyle,
    bracketMatching,
    foldGutter,
    foldKeymap,
    indentOnInput,
    syntaxHighlighting,
} from "@codemirror/language";
import {highlightSelectionMatches, searchKeymap} from "@codemirror/search";
import {Annotation, ChangeSet, Compartment, EditorState, type Extension} from "@codemirror/state";
import {
    EditorView,
    crosshairCursor,
    drawSelection,
    dropCursor,
    highlightActiveLine,
    highlightActiveLineGutter,
    highlightSpecialChars,
    keymap,
    lineNumbers,
    rectangularSelection,
} from "@codemirror/view";
import {tags} from "@lezer/highlight";

import type {ArmadaWorkspaceSourceLanguage} from "./legacy_workspace_source_language.ts";

export {
    armada_workspace_source_language_for_path,
    type ArmadaWorkspaceSourceLanguage,
} from "./legacy_workspace_source_language.ts";

export type ArmadaWorkspaceSourceSelection = {
    anchor: number;
    head: number;
};

export type ArmadaWorkspaceSourceViewState = {
    selection: ArmadaWorkspaceSourceSelection;
    scrollTop: number;
};

export type ArmadaWorkspaceSourceSelectionContext = {
    selectedText: string;
    startLine: number;
    endLine: number;
};

export type ArmadaWorkspaceSourceDirtyEvent = {
    origin: "user";
    selection: ArmadaWorkspaceSourceSelection;
};

export type ArmadaWorkspaceSourceLanguageError = {
    language: Exclude<ArmadaWorkspaceSourceLanguage, "plain">;
    error: unknown;
};

export type ArmadaWorkspaceSourceLanguageResult = "applied" | "failed" | "stale";

export type ArmadaWorkspaceSourceEditorOptions = {
    root: HTMLElement;
    text: string;
    selection?: ArmadaWorkspaceSourceSelection;
    scrollTop?: number;
    readOnly?: boolean;
    language?: ArmadaWorkspaceSourceLanguage;
    ariaLabel?: string;
    onDirty: (event: ArmadaWorkspaceSourceDirtyEvent) => void;
    onLanguageError?: (event: ArmadaWorkspaceSourceLanguageError) => void;
};

export type ArmadaWorkspaceSourceHydration = {
    text: string;
    selection?: ArmadaWorkspaceSourceSelection;
    scrollTop?: number;
};

export type ArmadaWorkspaceSourceEditor = {
    languageReady: Promise<ArmadaWorkspaceSourceLanguageResult>;
    hydrate: (hydration: ArmadaWorkspaceSourceHydration) => void;
    setReadOnly: (read_only: boolean) => void;
    setLanguage: (
        language: ArmadaWorkspaceSourceLanguage,
    ) => Promise<ArmadaWorkspaceSourceLanguageResult>;
    getText: () => string;
    getViewState: () => ArmadaWorkspaceSourceViewState;
    getSelectionContext: () => ArmadaWorkspaceSourceSelectionContext;
    focus: () => void;
    hasFocus: () => boolean;
    destroy: () => void;
};

type TransactionOrigin = "hydrate" | "user";

const transaction_origin = Annotation.define<TransactionOrigin>();

const language_loaders: Record<
    Exclude<ArmadaWorkspaceSourceLanguage, "plain">,
    () => Promise<Extension>
> = {
    async markdown() {
        const {markdown} = await import("@codemirror/lang-markdown");
        return markdown();
    },
    async javascript() {
        const {javascript} = await import("@codemirror/lang-javascript");
        return javascript({jsx: true});
    },
    async typescript() {
        const {javascript} = await import("@codemirror/lang-javascript");
        return javascript({jsx: true, typescript: true});
    },
    async json() {
        const {json} = await import("@codemirror/lang-json");
        return json();
    },
    async python() {
        const {python} = await import("@codemirror/lang-python");
        return python();
    },
    async css() {
        const {css} = await import("@codemirror/lang-css");
        return css();
    },
    async html() {
        const {html} = await import("@codemirror/lang-html");
        return html();
    },
    async yaml() {
        const {yaml} = await import("@codemirror/lang-yaml");
        return yaml();
    },
};

const editor_theme = EditorView.theme({
    "&": {
        height: "100%",
    },
    ".cm-scroller": {
        overflow: "auto",
    },
});

const armada_highlight_style = HighlightStyle.define([
    {tag: tags.heading, color: "var(--legacy-ink-1)", fontWeight: "700"},
    {tag: [tags.link, tags.url], color: "var(--legacy-accent-ink)", textDecoration: "underline"},
    {tag: [tags.comment, tags.meta], color: "var(--legacy-ink-3)", fontStyle: "italic"},
    {tag: [tags.keyword, tags.operatorKeyword], color: "var(--legacy-accent-ink)"},
    {tag: [tags.string, tags.special(tags.string)], color: "var(--legacy-ink-2)"},
    {tag: [tags.number, tags.bool, tags.null], color: "var(--legacy-accent-ink)"},
    {tag: [tags.typeName, tags.className, tags.namespace], color: "var(--legacy-ink-1)"},
    {tag: [tags.variableName, tags.propertyName], color: "var(--legacy-ink-1)"},
    {tag: [tags.punctuation, tags.bracket], color: "var(--legacy-ink-2)"},
    {tag: tags.invalid, color: "var(--legacy-danger)", textDecoration: "underline wavy"},
]);

function normalize_text(text: string): string {
    return text.replaceAll(/\r\n?|\n/g, "\n");
}

function preferred_line_separator(text: string): string {
    return /\r\n|\r|\n/.exec(text)?.[0] ?? "\n";
}

function clamp_position(position: number, document_length: number): number {
    if (!Number.isFinite(position)) {
        return 0;
    }
    return Math.max(0, Math.min(Math.trunc(position), document_length));
}

function clamp_selection(
    selection: ArmadaWorkspaceSourceSelection,
    document_length: number,
): ArmadaWorkspaceSourceSelection {
    return {
        anchor: clamp_position(selection.anchor, document_length),
        head: clamp_position(selection.head, document_length),
    };
}

function exact_offset_mapper(exact_text: string): (normalized_offset: number) => number {
    let exact_offset = 0;
    let normalized_offset = 0;

    return (target: number): number => {
        while (normalized_offset < target && exact_offset < exact_text.length) {
            if (exact_text[exact_offset] === "\r" && exact_text[exact_offset + 1] === "\n") {
                exact_offset += 2;
            } else {
                exact_offset += 1;
            }
            normalized_offset += 1;
        }

        return exact_offset;
    };
}

function reconstruct_exact_text(
    baseline_exact: string,
    baseline_normalized: string,
    changes: ChangeSet,
    current_normalized: string,
): string {
    if (current_normalized === baseline_normalized) {
        return baseline_exact;
    }

    const separator = preferred_line_separator(baseline_exact);
    const map_offset = exact_offset_mapper(baseline_exact);
    const parts: string[] = [];
    let exact_cursor = 0;

    changes.iterChanges((from_a, to_a, _from_b, _to_b, inserted) => {
        const exact_from = map_offset(from_a);
        const exact_to = map_offset(to_a);
        parts.push(
            baseline_exact.slice(exact_cursor, exact_from),
            inserted.toString().replaceAll("\n", separator),
        );
        exact_cursor = exact_to;
    });
    parts.push(baseline_exact.slice(exact_cursor));
    return parts.join("");
}

function extension_for_read_only(read_only: boolean): Extension {
    return [EditorState.readOnly.of(read_only), EditorView.editable.of(!read_only)];
}

export function create_armada_workspace_source_editor(
    options: ArmadaWorkspaceSourceEditorOptions,
): ArmadaWorkspaceSourceEditor {
    const language_compartment = new Compartment();
    const read_only_compartment = new Compartment();
    const history_compartment = new Compartment();
    let baseline_exact = options.text;
    let baseline_normalized = normalize_text(options.text);
    let composed_changes = ChangeSet.empty(baseline_normalized.length);
    let destroyed = false;
    let language_generation = 0;

    const user_origin = EditorState.transactionExtender.of((transaction) => {
        if (transaction.docChanged && transaction.annotation(transaction_origin) === undefined) {
            return {annotations: transaction_origin.of("user")};
        }
        return null;
    });

    const update_listener = EditorView.updateListener.of((update) => {
        let changed_by_user = false;
        for (const transaction of update.transactions) {
            if (transaction.docChanged && transaction.annotation(transaction_origin) === "user") {
                composed_changes = composed_changes.compose(transaction.changes);
                changed_by_user = true;
            }
        }
        if (changed_by_user) {
            const selection = update.state.selection.main;
            options.onDirty({
                origin: "user",
                selection: {anchor: selection.anchor, head: selection.head},
            });
        }
    });

    const initial_selection = clamp_selection(
        options.selection ?? {anchor: 0, head: 0},
        baseline_normalized.length,
    );
    const state = EditorState.create({
        doc: baseline_normalized,
        selection: initial_selection,
        extensions: [
            lineNumbers(),
            highlightActiveLineGutter(),
            highlightSpecialChars(),
            history_compartment.of(history()),
            foldGutter(),
            drawSelection(),
            dropCursor(),
            EditorState.allowMultipleSelections.of(true),
            indentOnInput(),
            syntaxHighlighting(armada_highlight_style, {fallback: true}),
            bracketMatching(),
            closeBrackets(),
            autocompletion(),
            rectangularSelection(),
            crosshairCursor(),
            highlightActiveLine(),
            highlightSelectionMatches(),
            keymap.of([
                ...closeBracketsKeymap,
                ...defaultKeymap,
                ...searchKeymap,
                ...historyKeymap,
                ...foldKeymap,
                indentWithTab,
            ]),
            EditorView.contentAttributes.of({
                "aria-label": options.ariaLabel ?? "Source editor",
            }),
            editor_theme,
            language_compartment.of([]),
            read_only_compartment.of(extension_for_read_only(options.readOnly ?? false)),
            user_origin,
            update_listener,
        ],
    });
    const view = new EditorView({state, parent: options.root});
    view.scrollDOM.scrollTop = Math.max(0, options.scrollTop ?? 0);

    function assert_live(): void {
        if (destroyed) {
            throw new Error("The workspace Source editor has been destroyed.");
        }
    }

    async function set_language(
        language: ArmadaWorkspaceSourceLanguage,
    ): Promise<ArmadaWorkspaceSourceLanguageResult> {
        assert_live();
        language_generation += 1;
        const generation = language_generation;
        if (language === "plain") {
            view.dispatch({effects: language_compartment.reconfigure([])});
            return "applied";
        }

        let extension: Extension;
        try {
            extension = await language_loaders[language]();
        } catch (error: unknown) {
            if (destroyed || generation !== language_generation) {
                return "stale";
            }
            options.onLanguageError?.({language, error});
            return "failed";
        }

        if (destroyed || generation !== language_generation) {
            return "stale";
        }
        view.dispatch({effects: language_compartment.reconfigure(extension)});
        return "applied";
    }

    const language_ready = set_language(options.language ?? "plain");

    return {
        languageReady: language_ready,
        hydrate(hydration): void {
            assert_live();
            const normalized = normalize_text(hydration.text);
            const current_selection = view.state.selection.main;
            const selection = clamp_selection(
                hydration.selection ?? {
                    anchor: current_selection.anchor,
                    head: current_selection.head,
                },
                normalized.length,
            );
            baseline_exact = hydration.text;
            baseline_normalized = normalized;
            composed_changes = ChangeSet.empty(normalized.length);
            view.dispatch({
                changes: {from: 0, to: view.state.doc.length, insert: normalized},
                selection,
                effects: history_compartment.reconfigure([]),
                annotations: transaction_origin.of("hydrate"),
            });
            view.dispatch({
                effects: history_compartment.reconfigure(history()),
                annotations: transaction_origin.of("hydrate"),
            });
            view.scrollDOM.scrollTop = Math.max(0, hydration.scrollTop ?? view.scrollDOM.scrollTop);
        },
        setReadOnly(read_only): void {
            assert_live();
            view.dispatch({
                effects: read_only_compartment.reconfigure(extension_for_read_only(read_only)),
            });
        },
        setLanguage: set_language,
        getText(): string {
            assert_live();
            return reconstruct_exact_text(
                baseline_exact,
                baseline_normalized,
                composed_changes,
                view.state.doc.toString(),
            );
        },
        getViewState(): ArmadaWorkspaceSourceViewState {
            assert_live();
            const selection = view.state.selection.main;
            return {
                selection: {anchor: selection.anchor, head: selection.head},
                scrollTop: view.scrollDOM.scrollTop,
            };
        },
        getSelectionContext(): ArmadaWorkspaceSourceSelectionContext {
            assert_live();
            const selection = view.state.selection.main;
            const from = Math.min(selection.anchor, selection.head);
            const to = Math.max(selection.anchor, selection.head);
            return {
                selectedText: view.state.sliceDoc(from, to),
                startLine: view.state.doc.lineAt(from).number,
                endLine: view.state.doc.lineAt(to).number,
            };
        },
        focus(): void {
            assert_live();
            view.focus();
        },
        hasFocus(): boolean {
            return !destroyed && view.hasFocus;
        },
        destroy(): void {
            if (destroyed) {
                return;
            }
            destroyed = true;
            language_generation += 1;
            view.destroy();
        },
    };
}
