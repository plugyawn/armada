import type {PearWorkspaceDescriptor, PearWorkspaceEntry} from "./pear_workspace_api.ts";
import type {PearWorkspaceEditorSnapshot} from "./pear_workspace_editor_contract.ts";

export type PearWorkspaceShellTab = {
    path: string;
    entry: PearWorkspaceEntry;
    preview: boolean;
    dirty: boolean;
    workspaceRevision: number;
    previousPath?: string;
};

export type PearWorkspaceShellSurfaceElements = {
    editorRoot: HTMLElement;
    nontextRoot: HTMLElement;
    conflictRoot: HTMLElement;
    conflictRemote: HTMLElement;
    status: HTMLElement;
    statusActions: HTMLElement;
    sourceButton: HTMLButtonElement;
    richButton: HTMLButtonElement;
    previewButton: HTMLButtonElement;
    previewRoot: HTMLElement;
    modeFeedback: HTMLElement;
    rawLink: HTMLAnchorElement;
    downloadLink: HTMLAnchorElement;
};

function format_bytes(bytes: number): string {
    if (bytes < 1024) {
        return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
        return `${Math.ceil(bytes / 1024)} KiB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function render_pear_workspace_tabs(
    root: HTMLElement,
    tabs: PearWorkspaceShellTab[],
    active_path: string | undefined,
): void {
    const fragment = document.createDocumentFragment();
    for (const tab of tabs) {
        const item = document.createElement("div");
        item.className = "pear-workspace-file-tab-item";
        const button = document.createElement("button");
        button.type = "button";
        button.dataset["tabPath"] = tab.path;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-controls", "pear-workspace-document-panel");
        button.setAttribute("aria-selected", String(tab.path === active_path));
        button.tabIndex = tab.path === active_path ? 0 : -1;
        button.className = tab.preview
            ? "pear-workspace-file-tab pear-workspace-file-tab--preview"
            : "pear-workspace-file-tab";
        button.textContent = `${tab.entry.name}${tab.dirty ? " •" : ""}`;
        const close = document.createElement("button");
        close.type = "button";
        close.className = "pear-workspace-file-tab-close";
        close.dataset["closeTabPath"] = tab.path;
        close.setAttribute("aria-label", `Close ${tab.entry.name}`);
        const close_icon = document.createElement("i");
        close_icon.className = "zulip-icon zulip-icon-close-small";
        close_icon.setAttribute("aria-hidden", "true");
        close.append(close_icon);
        item.append(button, close);
        fragment.append(item);
    }
    root.replaceChildren(fragment);
}

export function render_pear_workspace_breadcrumb(
    root: HTMLElement,
    tab: PearWorkspaceShellTab | undefined,
): void {
    root.replaceChildren();
    if (tab === undefined) {
        return;
    }
    const fragment = document.createDocumentFragment();
    const parts = tab.path.split("/");
    let current_path = "";
    for (const [index, part_text] of parts.entries()) {
        current_path = current_path === "" ? part_text : `${current_path}/${part_text}`;
        if (index > 0) {
            const separator = document.createElement("span");
            separator.setAttribute("aria-hidden", "true");
            separator.className = "pear-workspace-breadcrumb-separator";
            separator.textContent = "/";
            fragment.append(separator);
        }
        let part: HTMLElement;
        if (index === parts.length - 1) {
            part = document.createElement("span");
            part.setAttribute("aria-current", "page");
        } else {
            const button = document.createElement("button");
            button.type = "button";
            button.dataset["breadcrumbPath"] = current_path;
            button.setAttribute("aria-label", `Reveal ${current_path} in explorer`);
            part = button;
        }
        part.textContent = part_text;
        fragment.append(part);
    }
    root.append(fragment);
}

export function render_pear_workspace_descriptor(
    title: HTMLElement,
    meta: HTMLElement,
    descriptor: PearWorkspaceDescriptor,
): void {
    title.textContent = descriptor.display_root;
    meta.textContent = `${descriptor.branch || "detached"} · ${descriptor.head.slice(0, 8)} · ${
        descriptor.dirty ? `${descriptor.dirty_count} changed` : "clean"
    }`;
}

function render_links(
    elements: PearWorkspaceShellSurfaceElements,
    workspace_id: string,
    tab: PearWorkspaceShellTab | undefined,
    snapshot: PearWorkspaceEditorSnapshot | undefined,
    raw_url: (workspaceId: string, entryId: string, download?: boolean) => string,
): void {
    const identity = snapshot?.activeIdentity;
    const entry_id =
        identity !== undefined && identity.path === tab?.path
            ? identity.entryId
            : tab?.entry.entry_id;
    const raw_available = snapshot?.surface.kind !== "oversize" || snapshot.surface.rawAvailable;
    if (tab === undefined || entry_id === undefined || !raw_available) {
        elements.rawLink.removeAttribute("href");
        elements.downloadLink.removeAttribute("href");
        elements.rawLink.setAttribute("aria-disabled", "true");
        elements.downloadLink.setAttribute("aria-disabled", "true");
        return;
    }
    elements.rawLink.href = raw_url(workspace_id, entry_id);
    elements.downloadLink.href = raw_url(workspace_id, entry_id, true);
    elements.rawLink.removeAttribute("aria-disabled");
    elements.downloadLink.removeAttribute("aria-disabled");
}

export function render_pear_workspace_surface(options: {
    elements: PearWorkspaceShellSurfaceElements;
    workspaceId: string;
    descriptor: PearWorkspaceDescriptor | undefined;
    activeTab: PearWorkspaceShellTab | undefined;
    snapshot: PearWorkspaceEditorSnapshot | undefined;
    // Read-only Markdown rendering: `available` shows the control,
    // `active` puts the rendering in place of the editor.
    preview: {available: boolean; active: boolean};
    rawUrl: (workspaceId: string, entryId: string, download?: boolean) => string;
}): void {
    const {elements, snapshot, preview} = options;
    const surface = snapshot?.surface;
    const document_session = snapshot?.activeDocument;
    elements.conflictRoot.hidden = document_session?.state !== "conflict";
    elements.conflictRemote.textContent = document_session?.conflict?.remoteText ?? "";
    elements.sourceButton.hidden = surface?.kind !== "text";
    elements.richButton.hidden = surface?.kind !== "text" || !surface.file.rich_mode_eligible;
    elements.previewButton.hidden = !preview.available;
    elements.previewButton.setAttribute("aria-pressed", String(preview.active));
    elements.sourceButton.setAttribute(
        "aria-pressed",
        String(!preview.active && document_session?.editorMode === "source"),
    );
    elements.richButton.setAttribute(
        "aria-pressed",
        String(!preview.active && document_session?.editorMode === "rich"),
    );
    const rich_issue = snapshot?.richModeIssue;
    const rich_loading = snapshot?.richModeLoading ?? false;
    elements.editorRoot.setAttribute("aria-busy", String(rich_loading));
    const existing_loading = elements.editorRoot.querySelector<HTMLElement>(
        ":scope > .pear-workspace-rich-loading",
    );
    if (!rich_loading) {
        existing_loading?.remove();
    }
    if (rich_loading && elements.editorRoot.childElementCount === 0) {
        const loading = document.createElement("div");
        loading.className = "pear-workspace-rich-loading";
        loading.setAttribute("role", "status");
        loading.setAttribute("aria-label", "Loading Rich editor");
        for (let index = 0; index < 3; index += 1) {
            const line = document.createElement("span");
            line.setAttribute("aria-hidden", "true");
            loading.append(line);
        }
        elements.editorRoot.append(loading);
    }
    if (elements.richButton.disabled !== rich_loading) {
        elements.richButton.disabled = rich_loading;
    }
    const rich_source_only = rich_issue !== undefined && !rich_issue.retryable;
    if (rich_source_only && elements.richButton.getAttribute("aria-disabled") !== "true") {
        elements.richButton.setAttribute("aria-disabled", "true");
    } else if (!rich_source_only && elements.richButton.hasAttribute("aria-disabled")) {
        elements.richButton.removeAttribute("aria-disabled");
    }
    const rich_label = rich_issue?.retryable ? "Retry Rich" : "Rich";
    if (elements.richButton.textContent !== rich_label) {
        elements.richButton.textContent = rich_label;
    }
    const rich_state = rich_loading ? "loading" : (rich_issue?.kind ?? "idle");
    if (elements.richButton.dataset["state"] !== rich_state) {
        elements.richButton.dataset["state"] = rich_state;
    }
    if (rich_issue === undefined) {
        if (elements.richButton.hasAttribute("title")) {
            elements.richButton.removeAttribute("title");
        }
        if (!elements.modeFeedback.hidden) {
            elements.modeFeedback.hidden = true;
        }
        if (elements.modeFeedback.textContent !== "") {
            elements.modeFeedback.textContent = "";
        }
        if (elements.modeFeedback.dataset["state"] !== undefined) {
            delete elements.modeFeedback.dataset["state"];
        }
    } else {
        if (elements.richButton.title !== rich_issue.message) {
            elements.richButton.title = rich_issue.message;
        }
        if (elements.modeFeedback.hidden) {
            elements.modeFeedback.hidden = false;
        }
        if (elements.modeFeedback.textContent !== rich_issue.message) {
            elements.modeFeedback.textContent = rich_issue.message;
        }
        if (elements.modeFeedback.dataset["state"] !== rich_issue.kind) {
            elements.modeFeedback.dataset["state"] = rich_issue.kind;
        }
    }
    elements.editorRoot.hidden = surface?.kind !== "text" || preview.active;
    elements.previewRoot.hidden = !preview.active;
    elements.nontextRoot.hidden = surface?.kind === "text" || surface?.kind === "empty";
    elements.nontextRoot.replaceChildren();
    switch (surface?.kind) {
        case "raster": {
            const image = document.createElement("img");
            image.src = surface.rawUrl;
            image.alt = `Preview of ${surface.entry.name}`;
            image.loading = "lazy";
            image.decoding = "async";
            elements.nontextRoot.append(image);

            break;
        }
        case "binary": {
            elements.nontextRoot.textContent = surface.message;

            break;
        }
        case "oversize": {
            elements.nontextRoot.textContent = `This file exceeds the ${format_bytes(surface.maxBytes)} preview limit.`;

            break;
        }
        case "loading": {
            elements.nontextRoot.textContent = `Opening ${surface.entry.name}…`;

            break;
        }
        case "error": {
            const message = document.createElement("span");
            message.textContent = surface.message;
            const action = document.createElement("button");
            action.type = "button";
            action.dataset["action"] = surface.action === "source" ? "mode-source" : "retry-file";
            action.textContent = surface.action === "source" ? "Switch to Source" : "Retry";
            elements.nontextRoot.append(message, action);

            break;
        }
        // No default
    }

    if (surface?.kind === "loading") {
        elements.status.textContent = `Loading ${surface.entry.name}…`;
    } else if (snapshot?.richModeLoading) {
        elements.status.textContent = "Loading Rich editor…";
    } else {
        switch (document_session?.state) {
            case "saving": {
                elements.status.textContent = "Saving…";

                break;
            }
            case "dirty": {
                elements.status.textContent = "Unsaved changes";

                break;
            }
            case "conflict": {
                elements.status.textContent = "Save conflict: choose Reload or Overwrite";

                break;
            }
            case "error": {
                elements.status.textContent =
                    document_session.error === "too_large"
                        ? "This edit is larger than the workspace file limit. Shorten it before retrying."
                        : (document_session.error ?? "Workspace editor error");

                break;
            }
            default:
                if (snapshot?.notice !== undefined) {
                    elements.status.textContent = snapshot.notice;
                } else if (document_session?.state === "clean") {
                    elements.status.textContent = "Saved";
                } else {
                    switch (surface?.kind) {
                        case "error": {
                            elements.status.textContent = surface.message;

                            break;
                        }
                        case "raster":
                        case "binary": {
                            elements.status.textContent = "Preview";

                            break;
                        }
                        case "oversize": {
                            elements.status.textContent = "Preview unavailable";

                            break;
                        }
                        default: {
                            elements.status.textContent =
                                options.descriptor === undefined
                                    ? "Loading workspace…"
                                    : "Workspace ready";
                        }
                    }
                }
        }
    }
    elements.statusActions.hidden = document_session?.state !== "error";
    render_links(elements, options.workspaceId, options.activeTab, snapshot, options.rawUrl);
}

export function set_pear_workspace_mobile_explorer_open(
    root: HTMLElement,
    button: HTMLButtonElement,
    open: boolean,
): void {
    const shell = root.querySelector<HTMLElement>(".pear-workspace-shell");
    if (shell === null) {
        return;
    }
    shell.classList.toggle("pear-workspace-shell--explorer-open", open);
    button.setAttribute("aria-expanded", String(open));
}
