import render_pear_workspace_shell from "../templates/pear_workspace_shell.hbs";

import type {PearWorkspaceDescriptor, PearWorkspaceEntry} from "./pear_workspace_api.ts";
import * as pear_workspace_api from "./pear_workspace_api.ts";
import type {fill_pear_compose_with_workspace_context} from "./pear_workspace_compose.ts";
import type {
    PearWorkspaceEditorController,
    PearWorkspaceEditorOptions,
    PearWorkspaceEditorPreferences,
    PearWorkspaceEditorSnapshot,
} from "./pear_workspace_editor.ts";
import {create_pear_workspace_editor} from "./pear_workspace_editor.ts";
import type {PearWorkspaceExplorerController} from "./pear_workspace_explorer.ts";
import {create_pear_workspace_explorer} from "./pear_workspace_explorer.ts";
import type {
    PearWorkspaceFileMutationEvent,
    PearWorkspaceFilesController,
    PearWorkspaceFilesOptions,
    PearWorkspaceReadmeState,
} from "./pear_workspace_files.ts";
import {create_pear_workspace_files} from "./pear_workspace_files.ts";
import {
    read_pear_workspace_location,
    write_pear_workspace_location,
} from "./pear_workspace_location.ts";
import {
    begin_pear_workspace_metric,
    finish_pear_workspace_metric,
    pear_workspace_metrics_snapshot,
    record_pear_workspace_error,
} from "./pear_workspace_metrics.ts";
import type {PearWorkspaceShellTab} from "./pear_workspace_shell_view.ts";
import {
    render_pear_workspace_breadcrumb,
    render_pear_workspace_descriptor,
    render_pear_workspace_surface,
    render_pear_workspace_tabs,
    set_pear_workspace_mobile_explorer_open,
} from "./pear_workspace_shell_view.ts";
import type {PearWorkspaceTreeState} from "./pear_workspace_tree.ts";
import {
    begin_pear_workspace_directory_request,
    create_pear_workspace_tree,
    fail_pear_workspace_directory_request,
    receive_pear_workspace_directory_page,
    replace_pear_workspace_tree_revision,
    reveal_pear_workspace_search_result,
    select_pear_workspace_entry,
    set_pear_workspace_directory_expanded,
} from "./pear_workspace_tree.ts";

type AbortableRequest = {abort: () => unknown};

const shell_styles_ready: Promise<unknown> =
    globalThis.process?.env["NODE_ENV"] === "test"
        ? Promise.resolve()
        : import("../styles/pear_workspace.css");

export type PearWorkspaceShellDependencies = {
    getWorkspace: typeof pear_workspace_api.get_pear_workspace;
    getTree: typeof pear_workspace_api.get_pear_workspace_tree;
    search: typeof pear_workspace_api.search_pear_workspace;
    refresh: typeof pear_workspace_api.refresh_pear_workspace;
    rawUrl: typeof pear_workspace_api.pear_workspace_raw_url;
    createEditor: (options: PearWorkspaceEditorOptions) => PearWorkspaceEditorController;
    createFiles: (options: PearWorkspaceFilesOptions) => PearWorkspaceFilesController;
    loadComposeIntegration: () => Promise<{
        fill_pear_compose_with_workspace_context: typeof fill_pear_compose_with_workspace_context;
    }>;
};

export type PearWorkspaceShellOptions = {
    root: HTMLElement;
    workspaceId: string;
    dependencies?: Partial<PearWorkspaceShellDependencies>;
};

type WorkspaceTab = PearWorkspaceShellTab;
type LocationHistoryMode = "none" | "push" | "replace";

type CachedWorkspaceShellState = {
    tabs: WorkspaceTab[];
    activePath: string | undefined;
    expandedPaths: string[];
    editor: PearWorkspaceEditorPreferences;
};

const shell_state_cache = new Map<string, CachedWorkspaceShellState>();
const shell_state_cache_limit = 8;
const shell_cached_tab_limit = 24;
const shell_cached_expansion_limit = 256;
const explorer_width_default = 270;
const explorer_width_min = 180;
const explorer_width_max = 480;

function explorer_width_storage_key(workspace_id: string): string {
    return `pear-workspace-explorer-width:${workspace_id}`;
}

function restored_explorer_width(workspace_id: string): number {
    try {
        const value = Number.parseInt(
            localStorage.getItem(explorer_width_storage_key(workspace_id)) ?? "",
            10,
        );
        return Number.isFinite(value)
            ? Math.min(explorer_width_max, Math.max(explorer_width_min, value))
            : explorer_width_default;
    } catch {
        return explorer_width_default;
    }
}

function cache_shell_state(workspace_id: string, state: CachedWorkspaceShellState): void {
    shell_state_cache.delete(workspace_id);
    shell_state_cache.set(workspace_id, state);
    while (shell_state_cache.size > shell_state_cache_limit) {
        shell_state_cache.delete(shell_state_cache.keys().next().value!);
    }
}

export type PearWorkspaceShellDebugState = {
    descriptor: PearWorkspaceDescriptor | undefined;
    tree: PearWorkspaceTreeState | undefined;
    tabs: readonly WorkspaceTab[];
    activePath: string | undefined;
    renderedTreeRows: number;
    explorerWidth: number;
    metrics: ReturnType<typeof pear_workspace_metrics_snapshot>;
};

export type PearWorkspaceShellController = {
    ready: Promise<void>;
    refresh: () => void;
    openQuickOpen: () => void;
    applyLocation: () => void;
    getDebugState: () => PearWorkspaceShellDebugState;
    destroy: () => Promise<void>;
};

const default_dependencies: PearWorkspaceShellDependencies = {
    getWorkspace: pear_workspace_api.get_pear_workspace,
    getTree: pear_workspace_api.get_pear_workspace_tree,
    search: pear_workspace_api.search_pear_workspace,
    refresh: pear_workspace_api.refresh_pear_workspace,
    rawUrl: pear_workspace_api.pear_workspace_raw_url,
    createEditor: create_pear_workspace_editor,
    createFiles: create_pear_workspace_files,
    async loadComposeIntegration() {
        return import("./pear_workspace_compose.ts");
    },
};

function required_element(root: ParentNode, selector: string): HTMLElement {
    const element = root.querySelector<HTMLElement>(selector);
    if (element === null) {
        throw new Error(`Pear workspace shell is missing ${selector}.`);
    }
    return element;
}

function required_typed_element<T extends HTMLElement>(
    root: ParentNode,
    selector: string,
    expected: (element: HTMLElement) => element is T,
): T {
    const element = required_element(root, selector);
    if (!expected(element)) {
        throw new Error(`Pear workspace shell has the wrong element for ${selector}.`);
    }
    return element;
}

function is_form(element: HTMLElement): element is HTMLFormElement {
    return element.tagName === "FORM";
}

function is_input(element: HTMLElement): element is HTMLInputElement {
    return element.tagName === "INPUT";
}

function is_button(element: HTMLElement): element is HTMLButtonElement {
    return element.tagName === "BUTTON";
}

function is_anchor(element: HTMLElement): element is HTMLAnchorElement {
    return element.tagName === "A";
}

function error_message(error: pear_workspace_api.PearWorkspaceApiError): string {
    return error.kind === "server" ? error.error.msg : error.message;
}

function record_api_error(error: pear_workspace_api.PearWorkspaceApiError): void {
    record_pear_workspace_error(error.kind === "server" ? error.status : undefined);
}

export function create_pear_workspace_shell(
    options: PearWorkspaceShellOptions,
): PearWorkspaceShellController {
    const dependencies = {...default_dependencies, ...options.dependencies};
    const restored_state = shell_state_cache.get(options.workspaceId);
    if (globalThis.process?.env["NODE_ENV"] !== "test") {
        options.root.hidden = true;
    }
    options.root.innerHTML = render_pear_workspace_shell({});
    const title = required_element(options.root, "[data-role='workspace-title']");
    const meta = required_element(options.root, "[data-role='workspace-meta']");
    const tree_root = required_element(options.root, "[data-role='tree']");
    const search_form = required_typed_element(options.root, "[data-role='search-form']", is_form);
    const search_input = required_typed_element(
        options.root,
        "[data-role='search-input']",
        is_input,
    );
    const search_results = required_element(options.root, "[data-role='search-results']");
    const explorer_root = required_element(options.root, "#pear-workspace-explorer");
    const body = required_element(options.root, ".pear-workspace-shell__body");
    const explorer_splitter = required_element(options.root, "[data-role='explorer-splitter']");
    const tabs_root = required_element(options.root, "[data-role='tabs']");
    const breadcrumb = required_element(options.root, "[data-role='breadcrumb']");
    const document_panel = required_element(options.root, "#pear-workspace-document-panel");
    const editor_root = required_element(options.root, "[data-role='editor']");
    const nontext_root = required_element(options.root, "[data-role='nontext']");
    const conflict_root = required_element(options.root, "[data-role='conflict']");
    const status = required_element(options.root, "[data-role='status']");
    const source_button = required_typed_element(
        options.root,
        "[data-action='mode-source']",
        is_button,
    );
    const rich_button = required_typed_element(
        options.root,
        "[data-action='mode-rich']",
        is_button,
    );
    const ask_pear_button = required_typed_element(
        options.root,
        "[data-action='ask-pear']",
        is_button,
    );
    const raw_link = required_typed_element(options.root, "[data-role='raw-link']", is_anchor);
    const download_link = required_typed_element(
        options.root,
        "[data-role='download-link']",
        is_anchor,
    );
    const explorer_toggle = required_typed_element(
        options.root,
        "[data-action='toggle-explorer']",
        is_button,
    );
    const file_menu_button = required_typed_element(
        options.root,
        "[data-role='file-menu-button']",
        is_button,
    );
    const file_menu = required_element(options.root, "[data-role='file-menu']");
    const file_dialog_layer = required_element(options.root, "[data-role='file-dialog-layer']");

    let descriptor: PearWorkspaceDescriptor | undefined;
    let tree: PearWorkspaceTreeState | undefined;
    let destroyed = false;
    let filter = "";
    let active_path = restored_state?.activePath;
    const tabs: WorkspaceTab[] =
        restored_state?.tabs.map((tab) => ({...tab, entry: {...tab.entry}, dirty: false})) ?? [];
    let editor_snapshot: PearWorkspaceEditorSnapshot | undefined;
    let authority_stale = false;
    let restored_preferences_applied = false;
    let descriptor_request: AbortableRequest | undefined;
    let search_request: AbortableRequest | undefined;
    let refresh_request: AbortableRequest | undefined;
    let reveal_request: AbortableRequest | undefined;
    let search_generation = 0;
    let refresh_generation = 0;
    let reveal_generation = 0;
    let descriptor_generation = 0;
    let pending_reveal: {path: string; revision: number} | undefined;
    let required_workspace_revision: number | undefined;
    let mobile_explorer_open = false;
    let quick_open_active = false;
    let quick_open_previous_filter = "";
    let quick_open_return_focus: HTMLElement | undefined;
    let quick_open_opened_drawer = false;
    let quick_open_timer: ReturnType<typeof setTimeout> | undefined;
    let compose_integration_loading = false;
    let closing_tab = false;
    let location_mode_request: "rich" | "source" | undefined;
    let explorer_width = restored_explorer_width(options.workspaceId);
    let resize_pointer_id: number | undefined;
    const directory_requests = new Map<string, AbortableRequest>();
    const rebind_requests = new Map<string, AbortableRequest>();
    let files: PearWorkspaceFilesController | undefined;
    let ready_resolve: (() => void) | undefined;
    const descriptor_ready = new Promise<void>((resolve) => {
        ready_resolve = resolve;
    });
    const ready = (async (): Promise<void> => {
        try {
            await Promise.all([descriptor_ready, shell_styles_ready]);
        } finally {
            if (!destroyed) {
                options.root.hidden = false;
            }
        }
    })();

    const active_tab = (): WorkspaceTab | undefined => tabs.find((tab) => tab.path === active_path);

    const sync_location = (history_mode: Exclude<LocationHistoryMode, "none">): void => {
        const mode = editor_snapshot?.activeDocument?.editorMode ?? editor.getPreferences().mode;
        write_pear_workspace_location(
            {
                view: "workspace",
                workspaceId: options.workspaceId,
                path: active_path ?? null,
                mode: active_path === undefined ? null : mode,
            },
            history_mode,
        );
    };

    const set_explorer_width = (width: number, persist: boolean): void => {
        explorer_width = Math.min(
            explorer_width_max,
            Math.max(explorer_width_min, Math.round(width)),
        );
        body.style.setProperty("--pear-workspace-explorer-width", `${explorer_width}px`);
        explorer_splitter.setAttribute("aria-valuenow", String(explorer_width));
        if (persist) {
            try {
                localStorage.setItem(
                    explorer_width_storage_key(options.workspaceId),
                    String(explorer_width),
                );
            } catch {
                // Preference persistence is optional; resizing still works.
            }
        }
    };

    set_explorer_width(explorer_width, false);

    const finish_explorer_resize = (): void => {
        if (resize_pointer_id === undefined) {
            return;
        }
        resize_pointer_id = undefined;
        explorer_splitter.removeAttribute("data-resizing");
        set_explorer_width(explorer_width, true);
    };

    const on_explorer_resize_move = (event: PointerEvent): void => {
        if (resize_pointer_id === undefined || event.pointerId !== resize_pointer_id) {
            return;
        }
        set_explorer_width(event.clientX - body.getBoundingClientRect().left, false);
    };

    const on_explorer_resize_end = (event: PointerEvent): void => {
        if (resize_pointer_id !== undefined && event.pointerId === resize_pointer_id) {
            finish_explorer_resize();
        }
    };

    const on_explorer_resize_start = (event: PointerEvent): void => {
        if (event.button !== 0 || resize_pointer_id !== undefined) {
            return;
        }
        event.preventDefault();
        resize_pointer_id = event.pointerId;
        explorer_splitter.dataset["resizing"] = "true";
        set_explorer_width(event.clientX - body.getBoundingClientRect().left, false);
    };

    const on_explorer_splitter_keydown = (event: KeyboardEvent): void => {
        let next_width: number | undefined;
        switch (event.key) {
            case "ArrowLeft":
                next_width = explorer_width - 16;
                break;
            case "ArrowRight":
                next_width = explorer_width + 16;
                break;
            case "Home":
                next_width = explorer_width_min;
                break;
            case "End":
                next_width = explorer_width_max;
                break;
            // No default
        }
        if (next_width === undefined) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        set_explorer_width(next_width, true);
    };

    const set_mobile_explorer_open = (open: boolean, restore_focus: boolean): void => {
        mobile_explorer_open = open;
        set_pear_workspace_mobile_explorer_open(options.root, explorer_toggle, open);
        if (open) {
            search_input.focus();
        } else if (restore_focus && !destroyed) {
            explorer_toggle.focus();
        }
    };

    const close_quick_open = (restore_focus: boolean): void => {
        if (!quick_open_active) {
            return;
        }
        quick_open_active = false;
        if (quick_open_timer !== undefined) {
            clearTimeout(quick_open_timer);
            quick_open_timer = undefined;
        }
        search_request?.abort();
        search_request = undefined;
        search_generation += 1;
        explorer.renderSearchResults(undefined);
        search_input.placeholder = "Filter files";
        search_input.value = quick_open_previous_filter;
        filter = quick_open_previous_filter;
        render_tree();
        if (quick_open_opened_drawer) {
            set_mobile_explorer_open(false, false);
        }
        quick_open_opened_drawer = false;
        if (restore_focus && quick_open_return_focus?.isConnected === true) {
            quick_open_return_focus.focus();
        }
        quick_open_return_focus = undefined;
    };

    const open_quick_open = (): void => {
        if (destroyed) {
            return;
        }
        if (!quick_open_active) {
            quick_open_active = true;
            quick_open_previous_filter = search_input.value;
            quick_open_return_focus =
                document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
            quick_open_opened_drawer =
                !mobile_explorer_open && explorer_toggle.offsetParent !== null;
            if (quick_open_opened_drawer) {
                set_mobile_explorer_open(true, false);
            }
        }
        search_input.placeholder = "Quick open files";
        search_input.focus();
        search_input.select();
    };

    const readme_state = (): PearWorkspaceReadmeState => {
        if (
            [...(tree?.entries.values() ?? [])].some(
                (entry) => entry.kind === "file" && entry.path.toLowerCase() === "readme.md",
            )
        ) {
            return "present";
        }
        const root = tree?.directories.get(tree.rootId);
        return root?.complete === true ? "absent" : "unknown";
    };

    const update_files_context = (): void => {
        const current_tab = active_tab();
        const identities_current =
            !authority_stale &&
            descriptor !== undefined &&
            tree?.workspaceRevision === descriptor.workspace_revision;
        const selected_entry =
            identities_current && tree?.selectedId !== undefined
                ? tree.entries.get(tree.selectedId)
                : undefined;
        const active_entry =
            identities_current &&
            current_tab !== undefined &&
            current_tab.workspaceRevision === descriptor?.workspace_revision
                ? current_tab.entry
                : undefined;
        files?.updateContext({
            descriptor,
            activeEntry: active_entry,
            selectedEntry: selected_entry,
            readmeState: readme_state(),
            authorityStale: authority_stale,
        });
    };

    const render_tree = (): void => {
        if (tree !== undefined) {
            explorer.render(tree, filter);
        }
        update_files_context();
    };

    const render_tabs = (): void => {
        render_pear_workspace_tabs(tabs_root, tabs, active_path);
        update_files_context();
    };

    const render_breadcrumb = (): void => {
        render_pear_workspace_breadcrumb(breadcrumb, active_tab());
    };

    const render_surface = (): void => {
        render_pear_workspace_surface({
            elements: {
                editorRoot: editor_root,
                nontextRoot: nontext_root,
                conflictRoot: conflict_root,
                status,
                sourceButton: source_button,
                richButton: rich_button,
                rawLink: raw_link,
                downloadLink: download_link,
            },
            workspaceId: options.workspaceId,
            descriptor,
            activeTab: active_tab(),
            snapshot: editor_snapshot,
            rawUrl: dependencies.rawUrl,
        });
        ask_pear_button.hidden = active_tab() === undefined;
        ask_pear_button.disabled = compose_integration_loading;
    };

    const render_links = render_surface;

    const editor = dependencies.createEditor({
        root: editor_root,
        onUpdate(snapshot) {
            editor_snapshot = snapshot;
            if (snapshot.surface.kind !== "empty" && snapshot.surface.kind !== "loading") {
                finish_pear_workspace_metric("workspace_file_interactive");
            }
            const document = snapshot.activeDocument;
            if (document?.state === "dirty") {
                const tab = tabs.find((candidate) => candidate.path === document.path);
                if (tab !== undefined) {
                    tab.preview = false;
                    tab.dirty = true;
                    render_tabs();
                }
            } else if (document !== undefined) {
                const tab = tabs.find((candidate) => candidate.path === document.path);
                if (tab !== undefined) {
                    tab.dirty = document.state !== "clean";
                    render_tabs();
                }
            }
            render_surface();
        },
        onDirty(path) {
            const tab = tabs.find((candidate) => candidate.path === path);
            if (tab !== undefined) {
                tab.preview = false;
                tab.dirty = true;
                render_tabs();
            }
        },
        onSaved(event) {
            const tab = tabs.find((candidate) => candidate.path === event.path);
            if (tab !== undefined) {
                tab.entry = {...tab.entry, entry_id: event.newEntryId};
                tab.dirty = false;
                tab.workspaceRevision = event.workspaceRevision;
                render_tabs();
                render_links();
            }
            authority_stale = true;
            required_workspace_revision = Math.max(
                required_workspace_revision ?? event.workspaceRevision,
                event.workspaceRevision,
            );
            status.textContent = "Refreshing file identities…";
            update_files_context();
            refresh_workspace();
        },
    });

    const open_entry = async (entry: PearWorkspaceEntry): Promise<void> => {
        await editor.open(entry);
        if (destroyed || active_path !== entry.path || location_mode_request === undefined) {
            return;
        }
        const requested_mode = location_mode_request;
        location_mode_request = undefined;
        await editor.switchMode(requested_mode);
        sync_location("replace");
    };

    const activate_entry = (
        entry: PearWorkspaceEntry,
        pin: boolean,
        history_mode: LocationHistoryMode = "push",
    ): void => {
        if (
            destroyed ||
            authority_stale ||
            entry.kind !== "file" ||
            tree === undefined ||
            entry.entry_id === ""
        ) {
            if (authority_stale) {
                status.textContent = "Refreshing file identities…";
            }
            return;
        }
        tree = select_pear_workspace_entry(tree, entry.entry_id);
        active_path = entry.path;
        const existing = tabs.find((tab) => tab.path === entry.path);
        if (existing !== undefined) {
            existing.entry = entry;
            existing.workspaceRevision = tree.workspaceRevision;
            existing.preview &&= !pin;
        } else if (pin) {
            tabs.push({
                path: entry.path,
                entry,
                preview: false,
                dirty: false,
                workspaceRevision: tree.workspaceRevision,
            });
        } else {
            const replace_at = tabs.findIndex((tab) => tab.preview && !tab.dirty);
            const tab = {
                path: entry.path,
                entry,
                preview: true,
                dirty: false,
                workspaceRevision: tree.workspaceRevision,
            };
            if (replace_at === -1) {
                tabs.push(tab);
            } else {
                tabs.splice(replace_at, 1, tab);
            }
        }
        // Selection and tab chrome must paint before the document request starts.
        begin_pear_workspace_metric("workspace_file_interactive");
        render_tree();
        render_tabs();
        render_breadcrumb();
        render_links();
        if (history_mode !== "none") {
            location_mode_request = undefined;
            sync_location(history_mode);
        }
        void open_entry(entry);
    };

    const focus_file_tab = (path: string): void => {
        const button = [...tabs_root.querySelectorAll<HTMLButtonElement>("[data-tab-path]")].find(
            (candidate) => candidate.dataset["tabPath"] === path,
        );
        button?.focus();
    };

    const activate_tab_path = (tab_path: string, restore_focus: boolean): void => {
        const tab = tabs.find((candidate) => candidate.path === tab_path);
        if (tab === undefined) {
            return;
        }
        active_path = tab.path;
        render_tabs();
        render_breadcrumb();
        if (authority_stale || tab.workspaceRevision !== descriptor?.workspace_revision) {
            status.textContent = "Refreshing file identity…";
            if (!authority_stale) {
                resolve_tab_path(tab);
            }
        } else {
            activate_entry(tab.entry, !tab.preview, "push");
        }
        if (restore_focus) {
            focus_file_tab(tab.path);
        }
    };

    const close_tab_path = async (tab_path: string): Promise<void> => {
        if (closing_tab) {
            return;
        }
        const index = tabs.findIndex((tab) => tab.path === tab_path);
        const tab = tabs[index];
        if (tab === undefined) {
            return;
        }
        const active_document = editor_snapshot?.activeDocument;
        const document_is_unsafe =
            active_document?.path === tab_path && active_document.state !== "clean";
        if (tab.dirty || document_is_unsafe) {
            status.textContent = "Save or resolve this file before closing its tab";
            return;
        }
        const was_active = active_path === tab_path;
        tabs.splice(index, 1);
        if (!was_active) {
            render_tabs();
            return;
        }
        closing_tab = true;
        active_path = undefined;
        render_tabs();
        render_breadcrumb();
        render_links();
        const closed = await editor.closeActive();
        closing_tab = false;
        if (!closed || destroyed) {
            tabs.splice(Math.min(index, tabs.length), 0, tab);
            active_path = tab.path;
            render_tabs();
            render_breadcrumb();
            render_links();
            return;
        }
        const next = tabs[index] ?? tabs[index - 1];
        if (next === undefined) {
            sync_location("push");
            document_panel.focus();
            return;
        }
        activate_tab_path(next.path, true);
    };

    const load_directory = (directory_id: string, first_page_only: boolean): void => {
        if (destroyed || tree === undefined || directory_requests.has(directory_id)) {
            return;
        }
        const directory = tree.directories.get(directory_id);
        if (first_page_only && (directory?.childIds.length ?? 0) > 0) {
            return;
        }
        const started = begin_pear_workspace_directory_request(tree, directory_id);
        tree = started.state;
        render_tree();
        const request_state = started.request;
        if (request_state === undefined) {
            return;
        }
        const request = dependencies.getTree(
            request_state.workspaceId,
            {
                directoryId: request_state.directoryId,
                ...(request_state.cursor === undefined ? {} : {cursor: request_state.cursor}),
                pageSize: 200,
            },
            {
                success(response) {
                    directory_requests.delete(directory_id);
                    if (tree === undefined) {
                        return;
                    }
                    tree = receive_pear_workspace_directory_page(tree, request_state, response);
                    for (const tab of tabs) {
                        const rebound = [...tree.entries.values()].find(
                            (entry) => entry.path === tab.path && entry.kind === "file",
                        );
                        if (rebound !== undefined) {
                            tab.entry = rebound;
                            tab.workspaceRevision = tree.workspaceRevision;
                        }
                    }
                    render_tree();
                    render_tabs();
                    if (tree.rootId === directory_id) {
                        finish_pear_workspace_metric("workspace_tree_usable");
                    }
                    const expanded_unloaded = [...tree.entries.values()].filter(
                        (entry) =>
                            entry.kind === "directory" &&
                            tree!.expandedPaths.has(entry.path) &&
                            (tree!.directories.get(entry.entry_id)?.childIds.length ?? 0) === 0,
                    );
                    for (const entry of expanded_unloaded) {
                        load_directory(entry.entry_id, true);
                    }
                },
                error(error) {
                    directory_requests.delete(directory_id);
                    if (tree === undefined) {
                        return;
                    }
                    tree = fail_pear_workspace_directory_request(
                        tree,
                        request_state,
                        error_message(error),
                    );
                    status.textContent = error_message(error);
                    record_api_error(error);
                    render_tree();
                },
            },
        );
        if (request !== undefined) {
            directory_requests.set(directory_id, request);
        }
    };

    const explorer: PearWorkspaceExplorerController = create_pear_workspace_explorer({
        treeRoot: tree_root,
        searchResultsRoot: search_results,
        onSelect(entry_id) {
            if (tree === undefined) {
                return;
            }
            tree = select_pear_workspace_entry(tree, entry_id);
            render_tree();
        },
        onActivate(entry_id, pin) {
            const entry = tree?.entries.get(entry_id);
            if (entry !== undefined) {
                activate_entry(entry, pin);
                if (mobile_explorer_open && entry.kind === "file") {
                    set_mobile_explorer_open(false, true);
                }
            }
        },
        onToggle(entry_id, expanded) {
            if (tree === undefined) {
                return;
            }
            tree = set_pear_workspace_directory_expanded(tree, entry_id, expanded);
            render_tree();
            if (expanded) {
                load_directory(entry_id, true);
            }
        },
        onLoadMore(entry_id) {
            load_directory(entry_id, false);
        },
        onRevealSearchResult(response, entry_id) {
            if (tree === undefined) {
                return;
            }
            tree = reveal_pear_workspace_search_result(tree, response, entry_id);
            search_results.hidden = true;
            filter = "";
            search_input.value = "";
            render_tree();
            const entry = tree.entries.get(entry_id);
            if (entry !== undefined) {
                const focus_document = quick_open_active;
                if (quick_open_active) {
                    close_quick_open(false);
                }
                activate_entry(entry, false);
                explorer.focusEntry(entry_id);
                if (mobile_explorer_open && entry.kind === "file") {
                    set_mobile_explorer_open(false, !focus_document);
                }
                if (focus_document && entry.kind === "file") {
                    document_panel.focus();
                }
            }
        },
    });

    const reveal_path = (path: string, activate = false): void => {
        if (destroyed || authority_stale || descriptor === undefined || tree === undefined) {
            return;
        }
        const cached = [...tree.entries.values()].find((entry) => entry.path === path);
        if (cached !== undefined) {
            tree = select_pear_workspace_entry(tree, cached.entry_id);
            render_tree();
            explorer.focusEntry(cached.entry_id);
            if (activate && cached.kind === "file") {
                activate_entry(cached, false, "none");
            }
            return;
        }
        reveal_request?.abort();
        reveal_generation += 1;
        const generation = reveal_generation;
        const workspace_revision = descriptor.workspace_revision;
        reveal_request = dependencies.search(descriptor.uuid, path, 100, {
            success(response) {
                if (
                    destroyed ||
                    generation !== reveal_generation ||
                    authority_stale ||
                    tree === undefined ||
                    response.workspace_revision !== workspace_revision ||
                    response.workspace_revision !== descriptor?.workspace_revision
                ) {
                    return;
                }
                reveal_request = undefined;
                const exact = response.entries.find((entry) => entry.path === path);
                if (exact === undefined) {
                    status.textContent = `${path} is not in this workspace`;
                    return;
                }
                tree = reveal_pear_workspace_search_result(tree, response, exact.entry_id);
                render_tree();
                explorer.focusEntry(exact.entry_id);
                if (activate && exact.kind === "file") {
                    activate_entry(exact, false, "none");
                }
            },
            error(error) {
                if (destroyed || generation !== reveal_generation || authority_stale) {
                    return;
                }
                reveal_request = undefined;
                record_api_error(error);
                status.textContent = error_message(error);
            },
        });
    };

    const apply_location = (): void => {
        const location = read_pear_workspace_location();
        if (location.view !== "workspace" || location.workspaceId !== options.workspaceId) {
            return;
        }
        location_mode_request = location.mode;
        if (location.path === undefined) {
            if (location_mode_request !== undefined && active_path !== undefined) {
                const requested_mode = location_mode_request;
                location_mode_request = undefined;
                void editor.switchMode(requested_mode);
            }
            return;
        }
        if (
            active_path === location.path &&
            active_tab()?.workspaceRevision === descriptor?.workspace_revision &&
            editor_snapshot?.activeDocument !== undefined
        ) {
            if (location_mode_request !== undefined) {
                const requested_mode = location_mode_request;
                location_mode_request = undefined;
                void editor.switchMode(requested_mode);
            }
            return;
        }
        reveal_path(location.path, true);
    };

    const path_is_entry_or_child = (path: string, entry_path: string): boolean =>
        path === entry_path || path.startsWith(`${entry_path}/`);

    const renamed_path = (path: string, source_path: string, destination_path: string): string =>
        `${destination_path}${path.slice(source_path.length)}`;

    const reconcile_file_mutation = (event: PearWorkspaceFileMutationEvent): void => {
        authority_stale = true;
        required_workspace_revision = Math.max(
            required_workspace_revision ?? event.mutation.workspace_revision,
            event.mutation.workspace_revision,
        );
        search_request?.abort();
        search_request = undefined;
        search_generation += 1;
        reveal_request?.abort();
        reveal_request = undefined;
        reveal_generation += 1;
        for (const request of directory_requests.values()) {
            request.abort();
        }
        directory_requests.clear();
        for (const request of rebind_requests.values()) {
            request.abort();
        }
        rebind_requests.clear();

        if (event.operation === "create") {
            const created = event.mutation.entry;
            if (created !== null) {
                pending_reveal = {
                    path: created.path,
                    revision: event.mutation.workspace_revision,
                };
                if (created.kind === "file") {
                    const existing = tabs.find((tab) => tab.path === created.path);
                    if (existing === undefined) {
                        tabs.push({
                            path: created.path,
                            entry: created,
                            preview: false,
                            dirty: false,
                            workspaceRevision: event.mutation.workspace_revision,
                        });
                    } else {
                        existing.entry = created;
                        existing.preview = false;
                        existing.dirty = false;
                        existing.workspaceRevision = event.mutation.workspace_revision;
                    }
                    active_path = created.path;
                }
            }
        } else if (
            event.operation === "rename" &&
            event.sourcePath !== undefined &&
            event.destinationPath !== undefined
        ) {
            const source_path = event.sourcePath;
            const destination_path = event.destinationPath;
            pending_reveal = {
                path: destination_path,
                revision: event.mutation.workspace_revision,
            };
            for (const tab of tabs) {
                if (!path_is_entry_or_child(tab.path, source_path)) {
                    continue;
                }
                const next_path = renamed_path(tab.path, source_path, destination_path);
                tab.path = next_path;
                if (tab.entry.path === source_path && event.mutation.entry?.kind === "file") {
                    tab.entry = event.mutation.entry;
                    tab.workspaceRevision = event.mutation.workspace_revision;
                } else {
                    tab.entry = {
                        ...tab.entry,
                        path: next_path,
                        name: next_path.split("/").at(-1) ?? next_path,
                    };
                }
            }
            if (active_path !== undefined && path_is_entry_or_child(active_path, source_path)) {
                active_path = renamed_path(active_path, source_path, destination_path);
            }
        } else if (event.operation === "delete" && event.sourcePath !== undefined) {
            const source_path = event.sourcePath;
            for (let index = tabs.length - 1; index >= 0; index -= 1) {
                if (path_is_entry_or_child(tabs[index]!.path, source_path)) {
                    tabs.splice(index, 1);
                }
            }
            if (active_path !== undefined && path_is_entry_or_child(active_path, source_path)) {
                active_path = tabs.at(-1)?.path;
            }
        }
        render_tabs();
        render_breadcrumb();
        render_links();
        sync_location("replace");
        status.textContent = "Refreshing file identities…";
        refresh_workspace();
    };

    const resolve_tab_path = (tab: WorkspaceTab): void => {
        if (descriptor === undefined || rebind_requests.has(tab.path)) {
            return;
        }
        status.textContent = `Refreshing ${tab.entry.name}…`;
        const request = dependencies.search(descriptor.uuid, tab.path, 100, {
            success(response) {
                rebind_requests.delete(tab.path);
                if (
                    destroyed ||
                    authority_stale ||
                    response.workspace_revision !== descriptor?.workspace_revision
                ) {
                    return;
                }
                const rebound = response.entries.find(
                    (entry) => entry.path === tab.path && entry.kind === "file",
                );
                if (rebound === undefined || tree === undefined) {
                    status.textContent = `${tab.path} is no longer in this workspace`;
                    return;
                }
                tree = reveal_pear_workspace_search_result(tree, response, rebound.entry_id);
                tab.entry = rebound;
                tab.workspaceRevision = response.workspace_revision;
                render_tree();
                render_tabs();
                if (active_path === tab.path) {
                    activate_entry(rebound, !tab.preview, "replace");
                }
            },
            error(error) {
                rebind_requests.delete(tab.path);
                record_api_error(error);
                if (!authority_stale) {
                    status.textContent = error_message(error);
                }
            },
        });
        if (request !== undefined) {
            rebind_requests.set(tab.path, request);
        }
    };

    const apply_descriptor = (next: PearWorkspaceDescriptor): void => {
        for (const request of directory_requests.values()) {
            request.abort();
        }
        directory_requests.clear();
        descriptor = next;
        tree =
            tree === undefined
                ? create_pear_workspace_tree(next)
                : replace_pear_workspace_tree_revision(tree, next);
        if (restored_state !== undefined && !restored_preferences_applied) {
            tree = {...tree, expandedPaths: new Set(restored_state.expandedPaths)};
        }
        authority_stale = false;
        render_pear_workspace_descriptor(title, meta, next);
        editor.activateWorkspace(
            next.uuid,
            next.workspace_revision,
            {
                rawBytes: next.limits.raw_file_max_bytes,
                textBytes: next.limits.text_file_max_bytes,
                writable: next.capabilities.write_file,
            },
            restored_preferences_applied ? undefined : restored_state?.editor,
        );
        restored_preferences_applied = true;
        render_tree();
        render_tabs();
        render_breadcrumb();
        load_directory(tree.rootId, true);
        const location = read_pear_workspace_location();
        const restored_active = active_tab();
        if (
            location.view === "workspace" &&
            location.workspaceId === options.workspaceId &&
            location.path !== undefined
        ) {
            if (next.default_entry?.kind === "file" && next.default_entry.path === location.path) {
                location_mode_request = location.mode;
                activate_entry(next.default_entry, false, "none");
            } else if (
                restored_active?.path === location.path &&
                restored_active.workspaceRevision === next.workspace_revision
            ) {
                location_mode_request = location.mode;
                activate_entry(restored_active.entry, !restored_active.preview, "none");
            } else {
                apply_location();
            }
        } else if (restored_active !== undefined) {
            if (restored_active.workspaceRevision === next.workspace_revision) {
                activate_entry(restored_active.entry, !restored_active.preview, "replace");
            } else {
                resolve_tab_path(restored_active);
            }
        } else if (active_path === undefined && next.default_entry?.kind === "file") {
            activate_entry(next.default_entry, false, "replace");
        }
        if (pending_reveal !== undefined && next.workspace_revision >= pending_reveal.revision) {
            const {path} = pending_reveal;
            pending_reveal = undefined;
            if (active_path === undefined) {
                reveal_path(path);
            }
        }
    };

    const load_descriptor = (): void => {
        if (destroyed) {
            return;
        }
        descriptor_request?.abort();
        descriptor_generation += 1;
        const generation = descriptor_generation;
        status.textContent =
            descriptor === undefined ? "Loading workspace…" : "Reloading workspace…";
        if (descriptor === undefined) {
            begin_pear_workspace_metric("workspace_tree_usable");
        }
        descriptor_request = dependencies.getWorkspace(options.workspaceId, {
            success(response) {
                if (destroyed || generation !== descriptor_generation) {
                    return;
                }
                descriptor_request = undefined;
                if (response.uuid !== options.workspaceId) {
                    status.textContent = "Workspace returned the wrong identity. Retry refresh.";
                } else {
                    apply_descriptor(response);
                }
                ready_resolve?.();
                ready_resolve = undefined;
            },
            error(error) {
                if (destroyed || generation !== descriptor_generation) {
                    return;
                }
                descriptor_request = undefined;
                status.textContent = error_message(error);
                record_api_error(error);
                ready_resolve?.();
                ready_resolve = undefined;
            },
        });
    };

    const refresh_workspace = (): void => {
        if (destroyed) {
            return;
        }
        if (descriptor === undefined) {
            load_descriptor();
            return;
        }
        refresh_request?.abort();
        refresh_generation += 1;
        const generation = refresh_generation;
        status.textContent = "Refreshing workspace…";
        const request = dependencies.refresh(descriptor.uuid, {
            success(response) {
                if (destroyed || generation !== refresh_generation) {
                    return;
                }
                refresh_request = undefined;
                if (
                    required_workspace_revision !== undefined &&
                    response.workspace.workspace_revision < required_workspace_revision
                ) {
                    authority_stale = true;
                    update_files_context();
                    status.textContent = `Workspace refresh has not reached revision ${required_workspace_revision}; refresh again`;
                    return;
                }
                required_workspace_revision = undefined;
                apply_descriptor(response.workspace);
                status.textContent = response.changed
                    ? "Workspace updated"
                    : "Workspace is current";
            },
            error(error) {
                if (destroyed || generation !== refresh_generation) {
                    return;
                }
                refresh_request = undefined;
                record_api_error(error);
                status.textContent = error_message(error);
            },
        });
        refresh_request = request;
    };

    files = dependencies.createFiles({
        menuButton: file_menu_button,
        menu: file_menu,
        dialogLayer: file_dialog_layer,
        announce(message) {
            status.textContent = message;
        },
        onMutation: reconcile_file_mutation,
        onStaleAuthority() {
            authority_stale = true;
            update_files_context();
            status.textContent = "Refreshing file identities…";
            refresh_workspace();
        },
        onReveal(entry) {
            reveal_path(entry.path);
        },
    });
    update_files_context();

    const on_search_input = (): void => {
        filter = search_input.value;
        explorer.renderSearchResults(undefined);
        render_tree();
        if (!quick_open_active) {
            return;
        }
        if (quick_open_timer !== undefined) {
            clearTimeout(quick_open_timer);
        }
        const query = search_input.value.trim();
        if (query === "") {
            quick_open_timer = undefined;
            return;
        }
        quick_open_timer = setTimeout(() => {
            quick_open_timer = undefined;
            search_workspace(query);
        }, 120);
    };

    const search_workspace = (query: string): void => {
        if (descriptor === undefined || query === "") {
            return;
        }
        search_request?.abort();
        search_generation += 1;
        const generation = search_generation;
        search_request = dependencies.search(descriptor.uuid, query, 100, {
            success(response) {
                if (destroyed || generation !== search_generation) {
                    return;
                }
                search_request = undefined;
                explorer.renderSearchResults(response);
                status.textContent = `${response.entries.length} search results`;
            },
            error(error) {
                if (destroyed || generation !== search_generation) {
                    return;
                }
                search_request = undefined;
                record_api_error(error);
                explorer.renderSearchResults(undefined);
                status.textContent = error_message(error);
            },
        });
    };

    const on_search_submit = (event: SubmitEvent): void => {
        event.preventDefault();
        if (quick_open_timer !== undefined) {
            clearTimeout(quick_open_timer);
            quick_open_timer = undefined;
        }
        search_workspace(search_input.value.trim());
    };

    const on_search_input_keydown = (event: KeyboardEvent): void => {
        if (event.key !== "ArrowDown") {
            return;
        }
        const first_result =
            search_results.querySelector<HTMLButtonElement>("[data-search-entry-id]");
        if (first_result !== null) {
            event.preventDefault();
            first_result.focus();
        }
    };

    const add_selection_to_compose = async (): Promise<void> => {
        const context = editor.getSelectionContext();
        if (context === undefined || compose_integration_loading) {
            return;
        }
        compose_integration_loading = true;
        render_surface();
        status.textContent = "Opening topic compose…";
        let feedback = "";
        try {
            const module = await dependencies.loadComposeIntegration();
            if (destroyed) {
                return;
            }
            module.fill_pear_compose_with_workspace_context(context);
            feedback = "Added workspace context to compose";
        } catch {
            if (!destroyed) {
                feedback = "Could not open the topic compose. Try again.";
            }
        } finally {
            compose_integration_loading = false;
            if (!destroyed) {
                render_surface();
                status.textContent = feedback;
            }
        }
    };

    const switch_editor_mode = async (mode: "rich" | "source"): Promise<void> => {
        if (await editor.switchMode(mode)) {
            sync_location("push");
        }
    };

    const on_root_click = (event: MouseEvent): void => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const action = target.closest<HTMLElement>("[data-action]")?.dataset["action"];
        switch (action) {
            case "refresh": {
                refresh_workspace();

                break;
            }
            case "toggle-explorer": {
                set_mobile_explorer_open(!mobile_explorer_open, false);

                break;
            }
            case "mode-source": {
                void switch_editor_mode("source");

                break;
            }
            case "mode-rich": {
                void switch_editor_mode("rich");

                break;
            }
            case "ask-pear": {
                void add_selection_to_compose();

                break;
            }
            case "conflict-reload": {
                void editor.resolveConflict("reload");

                break;
            }
            case "conflict-overwrite": {
                void editor.resolveConflict("overwrite");

                break;
            }
            // No default
        }
        const close_tab_path_value =
            target.closest<HTMLElement>("[data-close-tab-path]")?.dataset["closeTabPath"];
        if (close_tab_path_value !== undefined) {
            void close_tab_path(close_tab_path_value);
            return;
        }
        const tab_path = target.closest<HTMLElement>("[data-tab-path]")?.dataset["tabPath"];
        if (tab_path !== undefined) {
            activate_tab_path(tab_path, true);
        }
        const breadcrumb_path =
            target.closest<HTMLElement>("[data-breadcrumb-path]")?.dataset["breadcrumbPath"];
        if (breadcrumb_path !== undefined) {
            reveal_path(breadcrumb_path);
        }
    };

    const on_root_keydown = (event: KeyboardEvent): void => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const tab_button = target.closest<HTMLButtonElement>("[data-tab-path]");
        if (tab_button !== null && tabs_root.contains(tab_button) && event.key === "Delete") {
            event.preventDefault();
            event.stopPropagation();
            void close_tab_path(tab_button.dataset["tabPath"] ?? "");
            return;
        }
        if (
            tab_button !== null &&
            tabs_root.contains(tab_button) &&
            ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
        ) {
            const tab_paths = tabs.map((tab) => tab.path);
            const current_index = tab_paths.indexOf(tab_button.dataset["tabPath"] ?? "");
            if (current_index === -1 || tab_paths.length === 0) {
                return;
            }
            let next_index: number;
            switch (event.key) {
                case "Home":
                    next_index = 0;
                    break;
                case "End":
                    next_index = tab_paths.length - 1;
                    break;
                case "ArrowLeft":
                    next_index = (current_index - 1 + tab_paths.length) % tab_paths.length;
                    break;
                default:
                    next_index = (current_index + 1) % tab_paths.length;
            }
            const next_path = tab_paths[next_index];
            if (next_path !== undefined) {
                event.preventDefault();
                event.stopPropagation();
                activate_tab_path(next_path, true);
            }
            return;
        }
        if (event.key === "Escape") {
            if (quick_open_active) {
                event.preventDefault();
                event.stopPropagation();
                close_quick_open(true);
                return;
            }
            if (mobile_explorer_open) {
                event.preventDefault();
                event.stopPropagation();
                set_mobile_explorer_open(false, true);
                return;
            }
            const document_state = editor_snapshot?.activeDocument?.state;
            if (
                active_tab()?.dirty === true ||
                document_state === "dirty" ||
                document_state === "saving" ||
                document_state === "conflict" ||
                document_state === "error"
            ) {
                event.preventDefault();
                event.stopPropagation();
                status.textContent =
                    "Finish saving or resolve the conflict before returning to Chat";
            }
            return;
        }
        if (event.key !== "Tab" || !mobile_explorer_open) {
            return;
        }
        const focusable = [
            ...explorer_root.querySelectorAll<HTMLElement>(
                "button:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])",
            ),
        ].filter((element) => !element.hidden && element.closest<HTMLElement>("[hidden]") === null);
        if (focusable.length === 0) {
            return;
        }
        const first = focusable[0]!;
        const last = focusable.at(-1)!;
        if (
            event.shiftKey &&
            (document.activeElement === first || !explorer_root.contains(document.activeElement))
        ) {
            event.preventDefault();
            event.stopPropagation();
            last.focus();
        } else if (
            !event.shiftKey &&
            (document.activeElement === last || !explorer_root.contains(document.activeElement))
        ) {
            event.preventDefault();
            event.stopPropagation();
            first.focus();
        }
    };

    const on_document_click = (event: MouseEvent): void => {
        const target = event.target;
        if (
            mobile_explorer_open &&
            target instanceof Element &&
            !explorer_root.contains(target) &&
            !explorer_toggle.contains(target)
        ) {
            set_mobile_explorer_open(false, false);
        }
    };

    const on_popstate = (): void => {
        apply_location();
    };

    options.root.addEventListener("click", on_root_click);
    options.root.addEventListener("keydown", on_root_keydown);
    document.addEventListener("click", on_document_click);
    search_input.addEventListener("input", on_search_input);
    search_input.addEventListener("keydown", on_search_input_keydown);
    search_form.addEventListener("submit", on_search_submit);
    explorer_splitter.addEventListener("pointerdown", on_explorer_resize_start);
    explorer_splitter.addEventListener("keydown", on_explorer_splitter_keydown);
    document.addEventListener("pointermove", on_explorer_resize_move);
    document.addEventListener("pointerup", on_explorer_resize_end);
    document.addEventListener("pointercancel", on_explorer_resize_end);
    window.addEventListener("popstate", on_popstate);

    load_descriptor();

    return {
        ready,
        refresh: refresh_workspace,
        openQuickOpen: open_quick_open,
        applyLocation: apply_location,
        getDebugState(): PearWorkspaceShellDebugState {
            return {
                descriptor,
                tree,
                tabs: tabs.map((tab) => ({...tab, entry: {...tab.entry}})),
                activePath: active_path,
                renderedTreeRows: explorer.getRenderedRowCount(),
                explorerWidth: explorer_width,
                metrics: pear_workspace_metrics_snapshot(),
            };
        },
        async destroy(): Promise<void> {
            if (destroyed) {
                return;
            }
            destroyed = true;
            descriptor_request?.abort();
            search_request?.abort();
            refresh_request?.abort();
            reveal_request?.abort();
            for (const request of directory_requests.values()) {
                request.abort();
            }
            directory_requests.clear();
            for (const request of rebind_requests.values()) {
                request.abort();
            }
            rebind_requests.clear();
            const recent_tabs = tabs.slice(-shell_cached_tab_limit);
            const current_tab = active_tab();
            const tabs_to_cache =
                current_tab !== undefined && !recent_tabs.includes(current_tab)
                    ? [current_tab, ...recent_tabs.slice(-(shell_cached_tab_limit - 1))]
                    : recent_tabs;
            const cached_tabs = tabs_to_cache.map((tab) => ({
                ...tab,
                entry: {...tab.entry},
                dirty: false,
            }));
            const preferences = editor.getPreferences();
            const cached_paths = new Set(cached_tabs.map((tab) => tab.path));
            const cached_documents = Object.fromEntries(
                Object.entries(preferences.documents).filter(([path]) => cached_paths.has(path)),
            );
            cache_shell_state(options.workspaceId, {
                tabs: cached_tabs,
                activePath: active_path,
                expandedPaths: [...(tree?.expandedPaths ?? [])].slice(
                    -shell_cached_expansion_limit,
                ),
                editor: {...preferences, documents: cached_documents},
            });
            options.root.removeEventListener("click", on_root_click);
            options.root.removeEventListener("keydown", on_root_keydown);
            document.removeEventListener("click", on_document_click);
            search_input.removeEventListener("input", on_search_input);
            search_input.removeEventListener("keydown", on_search_input_keydown);
            search_form.removeEventListener("submit", on_search_submit);
            explorer_splitter.removeEventListener("pointerdown", on_explorer_resize_start);
            explorer_splitter.removeEventListener("keydown", on_explorer_splitter_keydown);
            document.removeEventListener("pointermove", on_explorer_resize_move);
            document.removeEventListener("pointerup", on_explorer_resize_end);
            document.removeEventListener("pointercancel", on_explorer_resize_end);
            window.removeEventListener("popstate", on_popstate);
            finish_explorer_resize();
            if (quick_open_timer !== undefined) {
                clearTimeout(quick_open_timer);
                quick_open_timer = undefined;
            }
            files?.destroy();
            files = undefined;
            explorer.destroy();
            await editor.destroy();
            options.root.replaceChildren();
            ready_resolve?.();
            ready_resolve = undefined;
        },
    };
}
