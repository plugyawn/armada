import type {PearWorkspaceDescriptor, PearWorkspaceEntry} from "./pear_workspace_api.ts";
import type {PearWorkspaceEditorSnapshot} from "./pear_workspace_editor_contract.ts";

export type PearWorkspaceShellTab = {
    path: string;
    entry: PearWorkspaceEntry;
    preview: boolean;
    dirty: boolean;
    workspaceRevision: number;
};

export type PearWorkspaceShellSurfaceElements = {
    editorRoot: HTMLElement;
    nontextRoot: HTMLElement;
    conflictRoot: HTMLElement;
    status: HTMLElement;
    sourceButton: HTMLButtonElement;
    richButton: HTMLButtonElement;
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
        const button = document.createElement("button");
        button.type = "button";
        button.dataset["tabPath"] = tab.path;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-selected", String(tab.path === active_path));
        button.tabIndex = tab.path === active_path ? 0 : -1;
        button.className = tab.preview
            ? "pear-workspace-file-tab pear-workspace-file-tab--preview"
            : "pear-workspace-file-tab";
        button.textContent = `${tab.entry.name}${tab.dirty ? " •" : ""}`;
        fragment.append(button);
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
    for (const [index, part_text] of tab.path.split("/").entries()) {
        if (index > 0) {
            const separator = document.createElement("span");
            separator.setAttribute("aria-hidden", "true");
            separator.textContent = " / ";
            fragment.append(separator);
        }
        const part = document.createElement("span");
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
    rawUrl: (workspaceId: string, entryId: string, download?: boolean) => string;
}): void {
    const {elements, snapshot} = options;
    const surface = snapshot?.surface;
    const document_session = snapshot?.activeDocument;
    elements.conflictRoot.hidden = document_session?.state !== "conflict";
    elements.sourceButton.hidden = surface?.kind !== "text";
    elements.richButton.hidden = surface?.kind !== "text" || !surface.file.rich_mode_eligible;
    elements.sourceButton.setAttribute(
        "aria-pressed",
        String(document_session?.editorMode === "source"),
    );
    elements.richButton.setAttribute(
        "aria-pressed",
        String(document_session?.editorMode === "rich"),
    );
    elements.richButton.disabled = snapshot?.richModeLoading ?? false;
    elements.editorRoot.hidden = surface?.kind !== "text";
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
            elements.nontextRoot.textContent = surface.message;

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
                elements.status.textContent = document_session.error ?? "Workspace editor error";

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
    render_links(elements, options.workspaceId, options.activeTab, snapshot, options.rawUrl);
}

export function toggle_pear_workspace_mobile_explorer(
    root: HTMLElement,
    button: HTMLButtonElement,
): void {
    const shell = root.querySelector<HTMLElement>(".pear-workspace-shell");
    if (shell === null) {
        return;
    }
    const open = !shell.classList.contains("pear-workspace-shell--explorer-open");
    shell.classList.toggle("pear-workspace-shell--explorer-open", open);
    button.setAttribute("aria-expanded", String(open));
}
