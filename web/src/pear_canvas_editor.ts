// The canvas editor: Milkdown Crepe (ProseMirror) themed for Pear.
// CANVAS.md stays the storage format — this module turns it into a
// Notion-grade surface (slash menu, drag handles, tables, KaTeX) and
// carries the ask primitive as ProseMirror decorations: pending ask
// spans shimmer in place, resolved [label](#aside-N) links become
// popover chips (design: pear/PEAR_CANVAS_TABS_DESIGN.md, T2).

// The Crepe theme CSS ships from bundles/app.ts alongside Zulip's
// other third-party styles; katex.css is already imported there.

import {Crepe} from "@milkdown/crepe";
import {editorViewCtx, prosePluginsCtx} from "@milkdown/kit/core";
import {Plugin, PluginKey} from "@milkdown/kit/prose/state";
import {Decoration, DecorationSet} from "@milkdown/kit/prose/view";
import {replaceAll} from "@milkdown/kit/utils";

export type PendingAskSpans = {
    // Ask spans that survive as literal text ("[request]", or a
    // labeled form whose target markdown refused to parse as a link).
    literals: string[];
    // Labeled asks that markdown parsed into a link: href → request.
    labeled: Map<string, string>;
};

export type CanvasEditorOptions = {
    markdown: string;
    placeholder: string;
    on_markdown_updated: (markdown: string) => void;
    on_set_gesture: () => void;
    get_pending_spans: () => PendingAskSpans;
};

let crepe: Crepe | undefined;
let mount_generation = 0;

const ask_plugin_key = new PluginKey<DecorationSet>("pear-canvas-asks");

function build_ask_decorations(
    doc: Parameters<typeof DecorationSet.create>[0],
    get_pending_spans: () => PendingAskSpans,
): DecorationSet {
    const pending = get_pending_spans();
    const decorations: Decoration[] = [];
    doc.descendants((node, pos) => {
        if (!node.isText || node.text === undefined) {
            return true;
        }
        const text = node.text;
        const link = node.marks.find((mark) => mark.type.name === "link");
        if (link !== undefined) {
            const href = String(link.attrs["href"] ?? "");
            const aside_match = /^#(aside-\d+)$/.exec(href);
            if (aside_match?.[1] !== undefined) {
                decorations.push(
                    Decoration.inline(pos, pos + node.nodeSize, {
                        class: "pear-aside-chip",
                        "data-pear-aside": aside_match[1],
                    }),
                );
            } else if (pending.labeled.get(href) === text) {
                decorations.push(
                    Decoration.inline(pos, pos + node.nodeSize, {
                        class: "pear-ask-pending",
                    }),
                );
            }
            return true;
        }
        for (const span_text of pending.literals) {
            let index = text.indexOf(span_text);
            while (index !== -1) {
                decorations.push(
                    Decoration.inline(pos + index, pos + index + span_text.length, {
                        class: "pear-ask-pending",
                    }),
                );
                index = text.indexOf(span_text, index + span_text.length);
            }
        }
        return true;
    });
    return DecorationSet.create(doc, decorations);
}

export async function mount(root: HTMLElement, options: CanvasEditorOptions): Promise<boolean> {
    mount_generation += 1;
    const generation = mount_generation;
    await destroy();
    if (generation !== mount_generation) {
        return false;
    }
    const instance = new Crepe({
        root,
        defaultValue: options.markdown,
        features: {
            [Crepe.Feature.ImageBlock]: false,
            [Crepe.Feature.TopBar]: false,
            [Crepe.Feature.AI]: false,
            // Link clicks belong to the aside-chip popover; Crepe's
            // link edit/preview UI fights it and lingers on screen.
            [Crepe.Feature.LinkTooltip]: false,
        },
        featureConfigs: {
            [Crepe.Feature.Placeholder]: {
                text: options.placeholder,
                mode: "block",
            },
            [Crepe.Feature.Cursor]: {
                // The virtual caret redraws through decorations on
                // every keystroke; the native caret is free.
                virtual: false,
            },
        },
    });
    instance.editor.config((ctx) => {
        ctx.update(prosePluginsCtx, (plugins) => [
            ...plugins,
            new Plugin<DecorationSet>({
                key: ask_plugin_key,
                state: {
                    init(_config, state) {
                        return build_ask_decorations(state.doc, options.get_pending_spans);
                    },
                    apply(tr, old_set, _old_state, new_state) {
                        // Rebuild only when the document or the
                        // pending-ask set changed; caret movement and
                        // selection just map the existing set.
                        if (!tr.docChanged && tr.getMeta(ask_plugin_key) !== true) {
                            // DecorationSet.map, not Array#map.
                            // eslint-disable-next-line unicorn/no-array-callback-reference, unicorn/no-array-method-this-argument
                            return old_set.map(tr.mapping, tr.doc);
                        }
                        return build_ask_decorations(new_state.doc, options.get_pending_spans);
                    },
                },
                props: {
                    decorations(state) {
                        return ask_plugin_key.getState(state) ?? null;
                    },
                },
            }),
        ]);
    });
    instance.on((api) => {
        api.markdownUpdated((_ctx, markdown) => {
            options.on_markdown_updated(markdown);
        });
    });
    await instance.create();
    if (generation !== mount_generation) {
        await instance.destroy();
        return false;
    }
    crepe = instance;
    // The set gesture: Shift+Enter hands any new bracketed asks to
    // the agent (capture phase, so ProseMirror's hardbreak binding
    // never sees it).
    root.addEventListener(
        "keydown",
        (event) => {
            if (event.key === "Enter" && event.shiftKey) {
                event.preventDefault();
                event.stopPropagation();
                options.on_set_gesture();
            }
        },
        {capture: true},
    );
    return true;
}

export function is_mounted(): boolean {
    return crepe !== undefined;
}

export function get_markdown(): string | undefined {
    return crepe?.getMarkdown();
}

export function set_markdown(markdown: string): void {
    crepe?.editor.action(replaceAll(markdown));
}

export function has_focus(): boolean {
    if (crepe === undefined) {
        return false;
    }
    let focused = false;
    crepe.editor.action((ctx) => {
        focused = ctx.get(editorViewCtx).hasFocus();
    });
    return focused;
}

// Decorations recompute on every transaction; when the pending-ask
// set changes without an edit, nudge the view with an empty one.
export function refresh_decorations(): void {
    crepe?.editor.action((ctx) => {
        const view = ctx.get(editorViewCtx);
        view.dispatch(view.state.tr.setMeta(ask_plugin_key, true));
    });
}

export async function destroy(): Promise<void> {
    if (crepe === undefined) {
        return;
    }
    const instance = crepe;
    crepe = undefined;
    try {
        await instance.destroy();
    } catch {
        // A mid-teardown editor (e.g. during rapid narrow switches)
        // must never take the pane down with it.
    }
}
