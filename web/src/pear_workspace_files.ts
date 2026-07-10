import type {
    PearWorkspaceApiError,
    PearWorkspaceDescriptor,
    PearWorkspaceEntry,
    PearWorkspaceHistoryResponse,
    PearWorkspaceMutation,
} from "./pear_workspace_api.ts";
import * as pear_workspace_api from "./pear_workspace_api.ts";

export type PearWorkspaceReadmeState = "absent" | "present" | "unknown";

export type PearWorkspaceFilesContext = {
    descriptor: PearWorkspaceDescriptor | undefined;
    activeEntry: PearWorkspaceEntry | undefined;
    selectedEntry: PearWorkspaceEntry | undefined;
    readmeState: PearWorkspaceReadmeState;
    authorityStale: boolean;
};

export type PearWorkspaceFileMutationEvent = {
    mutation: PearWorkspaceMutation;
    operation: "create" | "delete" | "rename";
    sourcePath: string | undefined;
    destinationPath: string | undefined;
    kind: "directory" | "file";
};

export type PearWorkspaceFilesDependencies = {
    createEntry: typeof pear_workspace_api.create_pear_workspace_entry;
    renameEntry: typeof pear_workspace_api.rename_pear_workspace_entry;
    deleteEntry: typeof pear_workspace_api.delete_pear_workspace_entry;
    getHistory: typeof pear_workspace_api.get_pear_workspace_history;
    copyText: (text: string) => Promise<void>;
};

export type PearWorkspaceFilesOptions = {
    menuButton: HTMLButtonElement;
    menu: HTMLElement;
    dialogLayer: HTMLElement;
    announce: (message: string) => void;
    onMutation: (event: PearWorkspaceFileMutationEvent) => void;
    onStaleAuthority: () => void;
    onReveal: (entry: PearWorkspaceEntry) => void;
    dependencies?: Partial<PearWorkspaceFilesDependencies>;
};

export type PearWorkspaceFilesController = {
    updateContext: (context: PearWorkspaceFilesContext) => void;
    getDebugState: () => {
        menuOpen: boolean;
        dialogKind: "create" | "delete" | "history" | "rename" | undefined;
        mutationBusy: boolean;
        awaitingRevision: number | undefined;
    };
    destroy: () => void;
};

type FileAction =
    | "copy-path"
    | "create-file"
    | "create-folder"
    | "create-readme"
    | "delete"
    | "history"
    | "rename"
    | "reveal";

type DialogState = {
    kind: "create" | "delete" | "history" | "rename";
    overlay: HTMLElement;
    panel: HTMLElement;
    error: HTMLElement;
    closeButton: HTMLButtonElement;
};

type MutationRequest = {
    operation: "create" | "delete" | "rename";
    kind: "directory" | "file";
    sourceEntry: PearWorkspaceEntry | undefined;
    destinationPath: string | undefined;
};

function parse_file_action(value: string | undefined): FileAction | undefined {
    switch (value) {
        case "copy-path":
        case "create-file":
        case "create-folder":
        case "create-readme":
        case "delete":
        case "history":
        case "rename":
        case "reveal":
            return value;
        default:
            return undefined;
    }
}

const default_dependencies: PearWorkspaceFilesDependencies = {
    createEntry: pear_workspace_api.create_pear_workspace_entry,
    renameEntry: pear_workspace_api.rename_pear_workspace_entry,
    deleteEntry: pear_workspace_api.delete_pear_workspace_entry,
    getHistory: pear_workspace_api.get_pear_workspace_history,
    async copyText(text): Promise<void> {
        if (globalThis.navigator.clipboard === undefined) {
            throw new Error("Clipboard access is unavailable.");
        }
        await globalThis.navigator.clipboard.writeText(text);
    },
};

function api_error_message(error: PearWorkspaceApiError): string {
    return error.kind === "server" ? error.error.msg : error.message;
}

function is_stale_error(error: PearWorkspaceApiError): boolean {
    return (
        error.kind === "server" &&
        ["stale_entry", "stale_workspace"].includes(error.error.error_code)
    );
}

function valid_relative_path(path: string): boolean {
    return (
        path !== "" &&
        !path.startsWith("/") &&
        !path.endsWith("/") &&
        !path.includes("\0") &&
        path.split("/").every((part) => part !== "" && part !== "." && part !== "..")
    );
}

function parent_path(path: string): string {
    return path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";
}

function context_identity(context: PearWorkspaceFilesContext): string {
    return JSON.stringify([
        context.descriptor?.uuid,
        context.descriptor?.workspace_revision,
        context.activeEntry?.entry_id,
        context.activeEntry?.path,
        context.selectedEntry?.entry_id,
        context.selectedEntry?.path,
        context.readmeState,
        context.authorityStale,
    ]);
}

function mutation_is_valid(
    mutation: PearWorkspaceMutation,
    workspace_id: string,
    workspace_revision: number,
    request: MutationRequest,
): boolean {
    if (
        mutation.workspace_uuid !== workspace_id ||
        mutation.workspace_revision <= workspace_revision ||
        mutation.operation !== request.operation
    ) {
        return false;
    }
    if (request.operation === "create") {
        const entry = mutation.entry;
        return (
            request.destinationPath !== undefined &&
            mutation.previous_entry_id === null &&
            mutation.path === request.destinationPath &&
            entry !== null &&
            entry.path === request.destinationPath &&
            entry.kind === request.kind &&
            entry.status !== "deleted"
        );
    }
    if (request.sourceEntry === undefined) {
        return false;
    }
    if (
        mutation.previous_entry_id !== request.sourceEntry.entry_id ||
        mutation.path !== (request.destinationPath ?? request.sourceEntry.path)
    ) {
        return false;
    }
    if (request.operation === "delete") {
        return (
            mutation.entry === null ||
            (mutation.entry.path === request.sourceEntry.path &&
                mutation.entry.kind === request.sourceEntry.kind &&
                mutation.entry.status === "deleted")
        );
    }
    return (
        mutation.entry !== null &&
        mutation.entry.path === request.destinationPath &&
        mutation.entry.kind === request.sourceEntry.kind &&
        mutation.entry.status !== "deleted"
    );
}

function visible_enabled_items(menu: HTMLElement): HTMLButtonElement[] {
    return [...menu.querySelectorAll<HTMLButtonElement>("[role='menuitem']")].filter(
        (item) => !item.hidden && !item.disabled,
    );
}

export function create_pear_workspace_files(
    options: PearWorkspaceFilesOptions,
): PearWorkspaceFilesController {
    const dependencies = {...default_dependencies, ...options.dependencies};
    const guard = pear_workspace_api.create_pear_workspace_request_guard();
    const menu_items = new Map<FileAction, HTMLButtonElement>();
    for (const item of options.menu.querySelectorAll<HTMLButtonElement>("[data-file-action]")) {
        const action = parse_file_action(item.dataset["fileAction"]);
        if (action !== undefined) {
            menu_items.set(action, item);
        }
    }
    let context: PearWorkspaceFilesContext = {
        descriptor: undefined,
        activeEntry: undefined,
        selectedEntry: undefined,
        readmeState: "unknown",
        authorityStale: true,
    };
    let current_context_identity = context_identity(context);
    let destroyed = false;
    let menu_open = false;
    let dialog: DialogState | undefined;
    let dialog_serial = 0;
    let mutation_busy = false;
    let awaiting_revision: number | undefined;
    let history_target: PearWorkspaceEntry | undefined;
    let history_commits: PearWorkspaceHistoryResponse["pear_workspace_history"]["commits"] = [];
    let history_cursor: string | null = null;

    const mutation_target = (): PearWorkspaceEntry | undefined =>
        context.selectedEntry ?? context.activeEntry;

    const set_dialog_error = (message: string): void => {
        if (dialog !== undefined) {
            dialog.error.textContent = message;
            dialog.error.hidden = false;
        }
        options.announce(message);
    };

    const close_menu = (restore_focus: boolean): void => {
        menu_open = false;
        options.menu.hidden = true;
        options.menuButton.setAttribute("aria-expanded", "false");
        if (restore_focus && !destroyed) {
            options.menuButton.focus();
        }
    };

    const focus_menu_item = (item: HTMLButtonElement | undefined): void => {
        if (item === undefined) {
            return;
        }
        for (const candidate of menu_items.values()) {
            candidate.tabIndex = -1;
        }
        item.tabIndex = 0;
        item.focus();
    };

    const open_menu = (): void => {
        if (options.menuButton.hidden || options.menuButton.disabled) {
            return;
        }
        menu_open = true;
        options.menu.hidden = false;
        options.menuButton.setAttribute("aria-expanded", "true");
        focus_menu_item(visible_enabled_items(options.menu)[0]);
    };

    const close_dialog = (restore_focus: boolean): void => {
        if (dialog === undefined || mutation_busy) {
            return;
        }
        guard.cancel("history");
        dialog.overlay.remove();
        dialog = undefined;
        history_target = undefined;
        history_commits = [];
        history_cursor = null;
        if (restore_focus && !destroyed) {
            options.menuButton.focus();
        }
    };

    const create_dialog = (
        kind: DialogState["kind"],
        title: string,
    ): {state: DialogState; body: HTMLElement; footer: HTMLElement} => {
        if (dialog !== undefined) {
            close_dialog(false);
        }
        dialog_serial += 1;
        const overlay = document.createElement("div");
        overlay.className = "pear-workspace-file-dialog-overlay";
        const panel = document.createElement("section");
        panel.className = "pear-workspace-file-dialog";
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-modal", "true");
        const title_id = `pear-workspace-file-dialog-${dialog_serial}`;
        panel.setAttribute("aria-labelledby", title_id);
        const header = document.createElement("header");
        const heading = document.createElement("h2");
        heading.id = title_id;
        heading.textContent = title;
        const close_button = document.createElement("button");
        close_button.type = "button";
        close_button.dataset["dialogAction"] = "close";
        close_button.setAttribute("aria-label", "Close dialog");
        close_button.textContent = "Close";
        header.append(heading, close_button);
        const body = document.createElement("div");
        body.className = "pear-workspace-file-dialog__body";
        const error = document.createElement("p");
        error.className = "pear-workspace-file-dialog__error";
        error.setAttribute("role", "alert");
        error.hidden = true;
        const footer = document.createElement("footer");
        panel.append(header, body, error, footer);
        overlay.append(panel);
        options.dialogLayer.replaceChildren(overlay);
        const state = {kind, overlay, panel, error, closeButton: close_button};
        dialog = state;
        return {state, body, footer};
    };

    const set_mutation_busy = (busy: boolean): void => {
        mutation_busy = busy;
        if (dialog !== undefined) {
            for (const control of dialog.panel.querySelectorAll<
                HTMLButtonElement | HTMLInputElement
            >("button, input")) {
                control.disabled = busy || control.dataset["initiallyDisabled"] === "true";
            }
            dialog.closeButton.disabled = busy;
        }
    };

    const action_available = (action: FileAction): boolean => {
        const descriptor = context.descriptor;
        const active = context.activeEntry;
        const target = mutation_target();
        const usable = descriptor !== undefined && !context.authorityStale;
        const capabilities = descriptor?.capabilities;
        switch (action) {
            case "create-file":
            case "create-folder":
                return usable && capabilities?.create_entry === true;
            case "create-readme":
                return (
                    usable &&
                    capabilities?.create_entry === true &&
                    context.readmeState === "absent"
                );
            case "rename":
                return usable && capabilities?.rename_entry === true && target !== undefined;
            case "delete":
                return usable && capabilities?.delete_entry === true && target !== undefined;
            case "copy-path":
            case "reveal":
                return usable && active !== undefined;
            case "history":
                return usable && capabilities?.history === true && active?.kind === "file";
            default:
                return false;
        }
    };

    const render_menu = (): void => {
        for (const [action, item] of menu_items) {
            item.hidden = !action_available(action);
            item.disabled = mutation_busy || awaiting_revision !== undefined;
            item.tabIndex = -1;
        }
        const has_items = [...menu_items.values()].some((item) => !item.hidden);
        options.menuButton.hidden = !has_items;
        options.menuButton.disabled =
            context.descriptor === undefined ||
            context.authorityStale ||
            mutation_busy ||
            awaiting_revision !== undefined;
        if (!has_items || options.menuButton.disabled) {
            close_menu(false);
        }
    };

    const finish_mutation_error = (error: PearWorkspaceApiError): void => {
        set_mutation_busy(false);
        if (is_stale_error(error)) {
            close_dialog(false);
            close_menu(false);
            options.announce("Workspace changed. Refreshing file identities…");
            options.onStaleAuthority();
            return;
        }
        set_dialog_error(api_error_message(error));
        render_menu();
    };

    const start_mutation = (
        request: MutationRequest,
        invoke: (
            workspace_id: string,
            workspace_revision: number,
            callbacks: pear_workspace_api.PearWorkspaceRequestCallbacks<PearWorkspaceMutation>,
        ) => {abort: () => unknown} | undefined,
    ): void => {
        const descriptor = context.descriptor;
        if (
            descriptor === undefined ||
            context.authorityStale ||
            mutation_busy ||
            awaiting_revision !== undefined
        ) {
            return;
        }
        const workspace_id = descriptor.uuid;
        const workspace_revision = descriptor.workspace_revision;
        const generation = guard.begin("mutation");
        set_mutation_busy(true);
        render_menu();
        const pending = invoke(workspace_id, workspace_revision, {
            success(mutation) {
                if (!guard.settle("mutation", generation)) {
                    return;
                }
                set_mutation_busy(false);
                if (!mutation_is_valid(mutation, workspace_id, workspace_revision, request)) {
                    close_dialog(false);
                    close_menu(false);
                    options.announce(
                        "Workspace returned an invalid mutation receipt. Refreshing file identities…",
                    );
                    options.onStaleAuthority();
                    return;
                }
                awaiting_revision = mutation.workspace_revision;
                close_dialog(false);
                close_menu(false);
                render_menu();
                options.announce(`${request.operation} complete. Refreshing workspace…`);
                options.onMutation({
                    mutation,
                    operation: request.operation,
                    sourcePath: request.sourceEntry?.path,
                    destinationPath: request.destinationPath,
                    kind: request.kind,
                });
            },
            error(error) {
                if (!guard.settle("mutation", generation)) {
                    return;
                }
                finish_mutation_error(error);
            },
        });
        guard.attach("mutation", generation, pending);
    };

    const open_create_dialog = (kind: "directory" | "file"): void => {
        close_menu(false);
        const {body, footer} = create_dialog(
            "create",
            kind === "file" ? "Create file" : "Create folder",
        );
        const form = document.createElement("form");
        const label = document.createElement("label");
        label.textContent = "Workspace-relative path";
        const input = document.createElement("input");
        input.name = "path";
        input.required = true;
        input.autocomplete = "off";
        const selected = mutation_target();
        input.value =
            selected?.kind === "directory"
                ? `${selected.path}/`
                : parent_path(selected?.path ?? "");
        label.append(input);
        form.append(label);
        body.append(form);
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.dataset["dialogAction"] = "close";
        cancel.textContent = "Cancel";
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.textContent = kind === "file" ? "Create file" : "Create folder";
        footer.append(cancel, submit);
        form.append(footer);
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            const destination_path = input.value.trim();
            if (!valid_relative_path(destination_path)) {
                set_dialog_error("Enter a valid workspace-relative path without . or .. segments.");
                return;
            }
            start_mutation(
                {
                    operation: "create",
                    kind,
                    sourceEntry: undefined,
                    destinationPath: destination_path,
                },
                (workspace_id, workspace_revision, callbacks) =>
                    dependencies.createEntry(
                        workspace_id,
                        {
                            destinationPath: destination_path,
                            kind,
                            workspaceRevision: workspace_revision,
                            ...(kind === "file" ? {content: ""} : {}),
                        },
                        callbacks,
                    ),
            );
        });
        input.focus();
        input.select();
    };

    const create_readme = (): void => {
        close_menu(false);
        start_mutation(
            {
                operation: "create",
                kind: "file",
                sourceEntry: undefined,
                destinationPath: "README.md",
            },
            (workspace_id, workspace_revision, callbacks) =>
                dependencies.createEntry(
                    workspace_id,
                    {
                        destinationPath: "README.md",
                        kind: "file",
                        content: "",
                        workspaceRevision: workspace_revision,
                    },
                    callbacks,
                ),
        );
    };

    const open_rename_dialog = (): void => {
        const target = mutation_target();
        if (target === undefined) {
            return;
        }
        close_menu(false);
        const {body, footer} = create_dialog("rename", "Rename or move");
        const form = document.createElement("form");
        const label = document.createElement("label");
        label.textContent = "New workspace-relative path";
        const input = document.createElement("input");
        input.name = "path";
        input.required = true;
        input.autocomplete = "off";
        input.value = target.path;
        label.append(input);
        form.append(label);
        body.append(form);
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.dataset["dialogAction"] = "close";
        cancel.textContent = "Cancel";
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.textContent = "Rename / Move";
        footer.append(cancel, submit);
        form.append(footer);
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            const destination_path = input.value.trim();
            if (!valid_relative_path(destination_path) || destination_path === target.path) {
                set_dialog_error("Enter a different valid workspace-relative path.");
                return;
            }
            start_mutation(
                {
                    operation: "rename",
                    kind: target.kind,
                    sourceEntry: target,
                    destinationPath: destination_path,
                },
                (workspace_id, workspace_revision, callbacks) =>
                    dependencies.renameEntry(
                        workspace_id,
                        {
                            entryId: target.entry_id,
                            expectedPath: target.path,
                            destinationPath: destination_path,
                            workspaceRevision: workspace_revision,
                        },
                        callbacks,
                    ),
            );
        });
        input.focus();
        input.select();
    };

    const open_delete_dialog = (): void => {
        const target = mutation_target();
        if (target === undefined) {
            return;
        }
        close_menu(false);
        const {body, footer} = create_dialog("delete", "Delete workspace entry");
        const form = document.createElement("form");
        const warning = document.createElement("p");
        warning.textContent = `Delete ${target.path}?`;
        form.append(warning);
        const recursive_label = document.createElement("label");
        recursive_label.className = "pear-workspace-file-dialog__check";
        const recursive = document.createElement("input");
        recursive.type = "checkbox";
        recursive.name = "recursive";
        recursive_label.append(recursive, " Delete this folder and all contents recursively");
        recursive_label.hidden = target.kind !== "directory";
        form.append(recursive_label);
        const irreversible_label = document.createElement("label");
        irreversible_label.className = "pear-workspace-file-dialog__check";
        const irreversible = document.createElement("input");
        irreversible.type = "checkbox";
        irreversible.name = "irreversible";
        irreversible_label.append(irreversible, " I understand this deletion is irreversible");
        form.append(irreversible_label);
        body.append(form);
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.dataset["dialogAction"] = "close";
        cancel.textContent = "Cancel";
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.textContent = "Delete";
        submit.dataset["initiallyDisabled"] = "true";
        submit.disabled = true;
        footer.append(cancel, submit);
        form.append(footer);
        const update_delete_button = (): void => {
            const recursive_required = target.kind === "directory" && target.has_children;
            const enabled = (!recursive_required || recursive.checked) && irreversible.checked;
            submit.disabled = !enabled;
            submit.dataset["initiallyDisabled"] = String(!enabled);
        };
        recursive.addEventListener("change", update_delete_button);
        irreversible.addEventListener("change", update_delete_button);
        update_delete_button();
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            update_delete_button();
            if (submit.disabled) {
                return;
            }
            start_mutation(
                {
                    operation: "delete",
                    kind: target.kind,
                    sourceEntry: target,
                    destinationPath: undefined,
                },
                (workspace_id, workspace_revision, callbacks) =>
                    dependencies.deleteEntry(
                        workspace_id,
                        {
                            entryId: target.entry_id,
                            expectedPath: target.path,
                            workspaceRevision: workspace_revision,
                            recursive: recursive.checked,
                            confirmIrreversible: irreversible.checked,
                        },
                        callbacks,
                    ),
            );
        });
        (target.kind === "directory" ? recursive : irreversible).focus();
    };

    const render_history = (): void => {
        if (dialog?.kind !== "history") {
            return;
        }
        const body = dialog.panel.querySelector<HTMLElement>("[data-role='history-body']");
        const more = dialog.panel.querySelector<HTMLButtonElement>("[data-history-more]");
        if (body === null || more === null) {
            return;
        }
        body.replaceChildren();
        if (history_commits.length === 0) {
            body.textContent = "No commits found for this file.";
        } else {
            const list = document.createElement("ol");
            for (const commit of history_commits) {
                const item = document.createElement("li");
                const subject = document.createElement("strong");
                subject.textContent = commit.subject;
                const detail = document.createElement("span");
                const time = document.createElement("time");
                time.dateTime = new Date(commit.authored_at * 1000).toISOString();
                time.textContent = new Date(commit.authored_at * 1000).toLocaleString();
                detail.append(`${commit.author_name} · ${commit.commit.slice(0, 8)} · `, time);
                item.append(subject, detail);
                list.append(item);
            }
            body.append(list);
        }
        more.hidden = history_cursor === null;
        more.disabled = false;
    };

    const load_history = (cursor: string | undefined): void => {
        const descriptor = context.descriptor;
        const target = history_target;
        if (descriptor === undefined || target === undefined || dialog?.kind !== "history") {
            return;
        }
        const generation = guard.begin("history");
        const more = dialog.panel.querySelector<HTMLButtonElement>("[data-history-more]");
        if (more !== null) {
            more.disabled = true;
        }
        const request = dependencies.getHistory(
            descriptor.uuid,
            target.entry_id,
            {...(cursor === undefined ? {} : {cursor}), limit: 50},
            {
                success(response) {
                    if (!guard.settle("history", generation) || dialog?.kind !== "history") {
                        return;
                    }
                    if (
                        response.workspace_uuid !== descriptor.uuid ||
                        response.workspace_revision !== descriptor.workspace_revision ||
                        response.entry.entry_id !== target.entry_id ||
                        response.entry.path !== target.path
                    ) {
                        set_dialog_error("Workspace history changed. Refresh before retrying.");
                        options.onStaleAuthority();
                        return;
                    }
                    const seen = new Set(history_commits.map((commit) => commit.commit));
                    history_commits.push(
                        ...response.commits.filter((commit) => !seen.has(commit.commit)),
                    );
                    history_cursor = response.next_cursor;
                    render_history();
                },
                error(error) {
                    if (!guard.settle("history", generation)) {
                        return;
                    }
                    if (is_stale_error(error)) {
                        close_dialog(false);
                        options.announce("Workspace changed. Refreshing file identities…");
                        options.onStaleAuthority();
                        return;
                    }
                    set_dialog_error(api_error_message(error));
                    if (more !== null) {
                        more.disabled = false;
                    }
                },
            },
        );
        guard.attach("history", generation, request);
    };

    const open_history_dialog = (): void => {
        const target = context.activeEntry;
        if (target?.kind !== "file") {
            return;
        }
        close_menu(false);
        history_target = target;
        history_commits = [];
        history_cursor = null;
        const {body, footer} = create_dialog("history", `History · ${target.name}`);
        body.dataset["role"] = "history-body";
        body.textContent = "Loading history…";
        const more = document.createElement("button");
        more.type = "button";
        more.dataset["historyMore"] = "true";
        more.textContent = "Load more";
        more.hidden = true;
        const close = document.createElement("button");
        close.type = "button";
        close.dataset["dialogAction"] = "close";
        close.textContent = "Close";
        footer.append(more, close);
        more.addEventListener("click", () => {
            if (history_cursor !== null) {
                load_history(history_cursor);
            }
        });
        load_history(undefined);
        dialog?.closeButton.focus();
    };

    const copy_active_path = async (): Promise<void> => {
        const entry = context.activeEntry;
        if (entry === undefined) {
            return;
        }
        const identity = current_context_identity;
        close_menu(true);
        try {
            await dependencies.copyText(entry.path);
        } catch (error) {
            if (identity === current_context_identity) {
                options.announce(error instanceof Error ? error.message : "Could not copy path.");
            }
            return;
        }
        if (identity === current_context_identity) {
            options.announce(`Copied ${entry.path}`);
        }
    };

    const run_action = (action: FileAction): void => {
        if (!action_available(action)) {
            return;
        }
        switch (action) {
            case "create-file":
                open_create_dialog("file");
                break;
            case "create-folder":
                open_create_dialog("directory");
                break;
            case "create-readme":
                create_readme();
                break;
            case "rename":
                open_rename_dialog();
                break;
            case "delete":
                open_delete_dialog();
                break;
            case "copy-path":
                void copy_active_path();
                break;
            case "reveal": {
                const active = context.activeEntry;
                close_menu(false);
                if (active !== undefined) {
                    options.onReveal(active);
                }
                break;
            }
            case "history":
                open_history_dialog();
                break;
        }
    };

    const on_menu_button_click = (): void => {
        if (menu_open) {
            close_menu(true);
        } else {
            open_menu();
        }
    };

    const on_menu_click = (event: MouseEvent): void => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const action = parse_file_action(
            target.closest<HTMLButtonElement>("[data-file-action]")?.dataset["fileAction"],
        );
        if (action !== undefined && menu_items.get(action)?.disabled === false) {
            run_action(action);
        }
    };

    const on_menu_keydown = (event: KeyboardEvent): void => {
        const items = visible_enabled_items(options.menu);
        const active_item = document.activeElement?.closest<HTMLButtonElement>("[role='menuitem']");
        const index =
            active_item === null || active_item === undefined ? -1 : items.indexOf(active_item);
        let next: HTMLButtonElement | undefined;
        switch (event.key) {
            case "ArrowDown":
                next = items[(index + 1 + items.length) % items.length];
                break;
            case "ArrowUp":
                next = items[(index - 1 + items.length) % items.length];
                break;
            case "Home":
                next = items[0];
                break;
            case "End":
                next = items.at(-1);
                break;
            case "Escape":
                event.preventDefault();
                close_menu(true);
                return;
            default:
                return;
        }
        event.preventDefault();
        focus_menu_item(next);
    };

    const on_document_click = (event: MouseEvent): void => {
        const target = event.target;
        if (
            menu_open &&
            target instanceof Element &&
            target !== options.menuButton &&
            !options.menu.contains(target)
        ) {
            close_menu(false);
        }
    };

    const on_dialog_click = (event: MouseEvent): void => {
        const target = event.target;
        if (
            target instanceof Element &&
            target.closest<HTMLElement>("[data-dialog-action]")?.dataset["dialogAction"] === "close"
        ) {
            close_dialog(true);
        }
    };

    const on_dialog_keydown = (event: KeyboardEvent): void => {
        if (dialog === undefined) {
            return;
        }
        if (event.key === "Escape") {
            if (!mutation_busy) {
                event.preventDefault();
                close_dialog(true);
            }
            return;
        }
        if (event.key !== "Tab") {
            return;
        }
        const focusable = [
            ...dialog.panel.querySelectorAll<HTMLButtonElement | HTMLInputElement>(
                "button:not([disabled]), input:not([disabled])",
            ),
        ].filter((element) => !element.hidden && !element.closest<HTMLElement>("[hidden]"));
        if (focusable.length === 0) {
            return;
        }
        const first = focusable[0]!;
        const last = focusable.at(-1)!;
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    };

    options.menuButton.addEventListener("click", on_menu_button_click);
    options.menu.addEventListener("click", on_menu_click);
    options.menu.addEventListener("keydown", on_menu_keydown);
    document.addEventListener("click", on_document_click);
    options.dialogLayer.addEventListener("click", on_dialog_click);
    options.dialogLayer.addEventListener("keydown", on_dialog_keydown);
    render_menu();

    return {
        updateContext(next_context): void {
            if (destroyed) {
                return;
            }
            const next_identity = context_identity(next_context);
            if (next_identity !== current_context_identity) {
                current_context_identity = next_identity;
                if (mutation_busy) {
                    guard.cancel("mutation");
                    mutation_busy = false;
                }
                if (dialog !== undefined) {
                    close_dialog(false);
                }
            }
            context = next_context;
            if (
                awaiting_revision !== undefined &&
                next_context.descriptor !== undefined &&
                next_context.descriptor.workspace_revision >= awaiting_revision
            ) {
                awaiting_revision = undefined;
            }
            render_menu();
        },
        getDebugState() {
            return {
                menuOpen: menu_open,
                dialogKind: dialog?.kind,
                mutationBusy: mutation_busy,
                awaitingRevision: awaiting_revision,
            };
        },
        destroy(): void {
            if (destroyed) {
                return;
            }
            destroyed = true;
            guard.cancelAll();
            mutation_busy = false;
            if (dialog !== undefined) {
                dialog.overlay.remove();
                dialog = undefined;
            }
            close_menu(false);
            options.menuButton.removeEventListener("click", on_menu_button_click);
            options.menu.removeEventListener("click", on_menu_click);
            options.menu.removeEventListener("keydown", on_menu_keydown);
            document.removeEventListener("click", on_document_click);
            options.dialogLayer.removeEventListener("click", on_dialog_click);
            options.dialogLayer.removeEventListener("keydown", on_dialog_keydown);
        },
    };
}
