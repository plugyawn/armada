import type {ArmadaWorkspaceSearchResponse} from "./legacy_workspace_api.ts";
import type {ArmadaWorkspaceTreeRow, ArmadaWorkspaceTreeState} from "./legacy_workspace_tree.ts";
import {armada_workspace_tree_window} from "./legacy_workspace_tree.ts";

const tree_window_limit = 200;
const tree_window_step = 100;

export type ArmadaWorkspaceExplorerOptions = {
    treeRoot: HTMLElement;
    searchResultsRoot: HTMLElement;
    onSelect: (entryId: string) => void;
    onActivate: (entryId: string, pin: boolean) => void;
    onToggle: (entryId: string, expanded: boolean) => void;
    onLoadMore: (entryId: string) => void;
    onRevealSearchResult: (
        response: ArmadaWorkspaceSearchResponse["armada_workspace_search"],
        entryId: string,
    ) => void;
};

export type ArmadaWorkspaceExplorerController = {
    render: (tree: ArmadaWorkspaceTreeState, filter: string) => void;
    renderSearchResults: (
        response: ArmadaWorkspaceSearchResponse["armada_workspace_search"] | undefined,
    ) => void;
    focusEntry: (entryId: string) => void;
    getRenderedRowCount: () => number;
    destroy: () => void;
};

type VisibleRows = {
    rows: ArmadaWorkspaceTreeRow[];
    hasMore: boolean;
    skipped: number;
};

function normalized_filter(value: string): string {
    return value.trim().toLocaleLowerCase();
}

function filtered_rows(tree: ArmadaWorkspaceTreeState, filter: string): VisibleRows {
    const query = normalized_filter(filter);
    if (query === "") {
        return armada_workspace_tree_window(tree, {
            offset: 0,
            limit: tree_window_limit,
        });
    }
    const rows: ArmadaWorkspaceTreeRow[] = [];
    let skipped = 0;
    let has_more = false;
    for (const entry of tree.entries.values()) {
        if (entry.entry_id === tree.rootId) {
            continue;
        }
        if (!entry.path.toLocaleLowerCase().includes(query)) {
            continue;
        }
        if (rows.length === tree_window_limit) {
            has_more = true;
            break;
        }
        rows.push({
            entry,
            depth: Math.max(0, entry.path.split("/").length - 1),
            expanded: entry.kind === "directory" && tree.expandedPaths.has(entry.path),
            loading: tree.directories.get(entry.entry_id)?.loading ?? false,
        });
        skipped += 1;
    }
    return {rows, hasMore: has_more, skipped};
}

function row_label(row: ArmadaWorkspaceTreeRow): string {
    if (row.entry.kind === "directory") {
        return `${row.expanded ? "Expanded" : "Collapsed"} folder ${row.entry.name}`;
    }
    return row.entry.name;
}

export function create_armada_workspace_explorer(
    options: ArmadaWorkspaceExplorerOptions,
): ArmadaWorkspaceExplorerController {
    const keyed_rows = new Map<string, HTMLElement>();
    let current_tree: ArmadaWorkspaceTreeState | undefined;
    let current_filter = "";
    let visible: VisibleRows = {rows: [], skipped: 0, hasMore: false};
    let window_offset = 0;
    let focused_entry_id: string | undefined;
    let typeahead = "";
    let typeahead_timer: ReturnType<typeof setTimeout> | undefined;
    let search_response: ArmadaWorkspaceSearchResponse["armada_workspace_search"] | undefined;

    const focus_row = (entry_id: string): void => {
        if (!visible.rows.some((row) => row.entry.entry_id === entry_id)) {
            return;
        }
        focused_entry_id = entry_id;
        for (const [id, element] of keyed_rows) {
            element.tabIndex = id === entry_id ? 0 : -1;
        }
        options.onSelect(entry_id);
        keyed_rows.get(entry_id)?.focus();
    };

    const create_row = (entry_id: string): HTMLElement => {
        const element = document.createElement("div");
        element.className = "legacy-workspace-tree-row";
        element.dataset["entryId"] = entry_id;
        element.setAttribute("role", "treeitem");
        element.tabIndex = -1;
        const disclosure = document.createElement("span");
        disclosure.className = "legacy-workspace-tree-row__disclosure";
        disclosure.setAttribute("aria-hidden", "true");
        disclosure.dataset["role"] = "disclosure";
        const chevron = document.createElement("i");
        chevron.className = "zulip-icon zulip-icon-chevron-right";
        disclosure.append(chevron);
        const icon = document.createElement("i");
        icon.className = "legacy-workspace-tree-row__icon zulip-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.dataset["role"] = "icon";
        const name = document.createElement("span");
        name.className = "legacy-workspace-tree-row__name";
        name.dataset["role"] = "name";
        const status = document.createElement("span");
        status.className = "legacy-workspace-tree-row__status";
        status.dataset["role"] = "entry-status";
        element.append(disclosure, icon, name, status);
        return element;
    };

    const update_row = (
        element: HTMLElement,
        row: ArmadaWorkspaceTreeRow,
        tree: ArmadaWorkspaceTreeState,
    ): void => {
        const {entry} = row;
        element.dataset["entryId"] = entry.entry_id;
        element.dataset["path"] = entry.path;
        element.dataset["kind"] = entry.kind;
        element.style.setProperty("--legacy-tree-depth", String(row.depth));
        element.setAttribute("aria-level", String(row.depth + 1));
        element.setAttribute("aria-label", row_label(row));
        element.setAttribute("aria-selected", String(tree.selectedId === entry.entry_id));
        if (entry.kind === "directory") {
            element.setAttribute("aria-expanded", String(row.expanded));
        } else {
            element.removeAttribute("aria-expanded");
        }
        // A folder only reports loading once it is open: closed folders
        // are fetched ahead of time and should not flicker while that
        // happens.
        const disclosure = element.querySelector<HTMLElement>("[data-role='disclosure']")!;
        disclosure.dataset["state"] =
            entry.kind !== "directory"
                ? "none"
                : row.expanded
                  ? row.loading
                      ? "loading"
                      : "expanded"
                  : "collapsed";
        const icon = element.querySelector<HTMLElement>("[data-role='icon']")!;
        icon.classList.toggle("zulip-icon-folder", entry.kind === "directory");
        icon.classList.toggle("zulip-icon-file-text", entry.kind !== "directory");
        const name = element.querySelector<HTMLElement>("[data-role='name']")!;
        if (name.textContent !== entry.name) {
            name.textContent = entry.name;
        }
        const status = element.querySelector<HTMLElement>("[data-role='entry-status']")!;
        const status_text = entry.status === "clean" ? "" : entry.status;
        if (status.textContent !== status_text) {
            status.textContent = status_text;
        }
        status.dataset["status"] = entry.status;

        element.querySelector("[data-action='load-more']")?.remove();
        const directory = tree.directories.get(entry.entry_id);
        if (
            entry.kind === "directory" &&
            row.expanded &&
            directory !== undefined &&
            directory.childIds.length > 0 &&
            !directory.complete &&
            !directory.loading
        ) {
            const more = document.createElement("button");
            more.type = "button";
            more.dataset["action"] = "load-more";
            more.setAttribute("aria-label", `Load more files in ${entry.name}`);
            more.textContent = "More";
            element.append(more);
        }
    };

    const render_current = (): void => {
        const tree = current_tree;
        if (tree === undefined) {
            options.treeRoot.replaceChildren();
            visible = {rows: [], skipped: 0, hasMore: false};
            return;
        }
        if (normalized_filter(current_filter) === "") {
            visible = armada_workspace_tree_window(tree, {
                offset: window_offset,
                limit: tree_window_limit,
            });
        } else {
            window_offset = 0;
            visible = filtered_rows(tree, current_filter);
        }
        if (
            focused_entry_id === undefined ||
            !visible.rows.some((row) => row.entry.entry_id === focused_entry_id)
        ) {
            focused_entry_id =
                visible.rows.find((row) => row.entry.entry_id === tree.selectedId)?.entry
                    .entry_id ?? visible.rows[0]?.entry.entry_id;
        }
        const next_children: Element[] = [];
        const live_ids = new Set<string>();
        if (visible.skipped > 0 && normalized_filter(current_filter) === "") {
            const previous = document.createElement("button");
            previous.type = "button";
            previous.dataset["action"] = "previous-window";
            previous.textContent = "Earlier files";
            previous.setAttribute("role", "treeitem");
            previous.setAttribute("aria-level", "1");
            previous.tabIndex = -1;
            next_children.push(previous);
        }
        for (const row of visible.rows) {
            const id = row.entry.entry_id;
            const element = keyed_rows.get(id) ?? create_row(id);
            keyed_rows.set(id, element);
            live_ids.add(id);
            update_row(element, row, tree);
            element.tabIndex = id === focused_entry_id ? 0 : -1;
            next_children.push(element);
        }
        if (visible.hasMore && normalized_filter(current_filter) === "") {
            const next = document.createElement("button");
            next.type = "button";
            next.dataset["action"] = "next-window";
            next.textContent = "More files";
            next.setAttribute("role", "treeitem");
            next.setAttribute("aria-level", "1");
            next.tabIndex = -1;
            next_children.push(next);
        }
        const root_directory = tree.directories.get(tree.rootId);
        if (
            normalized_filter(current_filter) === "" &&
            !visible.hasMore &&
            root_directory !== undefined &&
            root_directory.childIds.length > 0 &&
            !root_directory.complete &&
            !root_directory.loading
        ) {
            const load_more = document.createElement("button");
            load_more.type = "button";
            load_more.dataset["action"] = "load-more-root";
            load_more.textContent = "Load more workspace files";
            load_more.setAttribute("role", "treeitem");
            load_more.setAttribute("aria-level", "1");
            next_children.push(load_more);
        }
        for (const id of keyed_rows.keys()) {
            if (!live_ids.has(id)) {
                keyed_rows.delete(id);
            }
        }
        // Selecting, expanding and loading each re-render the tree. Most
        // of those leave the row order untouched, and re-attaching every
        // row then would cost a full layout for nothing.
        const current_children = options.treeRoot.children;
        const unchanged =
            next_children.length === current_children.length &&
            next_children.every((child, index) => child === current_children[index]);
        if (!unchanged) {
            options.treeRoot.replaceChildren(...next_children);
        }
        options.treeRoot.setAttribute(
            "aria-busy",
            String([...tree.directories.values()].some((directory) => directory.loading)),
        );
    };

    const activate_row = (entry_id: string, pin: boolean): void => {
        const entry = current_tree?.entries.get(entry_id);
        if (entry === undefined) {
            return;
        }
        if (entry.kind === "directory") {
            const expanded = current_tree!.expandedPaths.has(entry.path);
            options.onToggle(entry_id, !expanded);
        } else {
            options.onActivate(entry_id, pin);
        }
    };

    const on_click = (event: MouseEvent): void => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const action = target.closest<HTMLElement>("[data-action]")?.dataset["action"];
        if (action === "previous-window") {
            window_offset = Math.max(0, window_offset - tree_window_step);
            render_current();
            focus_row(visible.rows.at(-1)?.entry.entry_id ?? "");
            return;
        }
        if (action === "next-window") {
            window_offset += tree_window_step;
            render_current();
            focus_row(visible.rows[0]?.entry.entry_id ?? "");
            return;
        }
        if (action === "load-more-root") {
            if (current_tree !== undefined) {
                options.onLoadMore(current_tree.rootId);
            }
            return;
        }
        const row = target.closest<HTMLElement>("[role='treeitem']");
        const entry_id = row?.dataset["entryId"];
        if (entry_id === undefined) {
            return;
        }
        focus_row(entry_id);
        if (action === "load-more") {
            options.onLoadMore(entry_id);
            return;
        }
        activate_row(entry_id, false);
    };

    const on_double_click = (event: MouseEvent): void => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const entry_id = target.closest<HTMLElement>("[role='treeitem']")?.dataset["entryId"];
        if (entry_id !== undefined && current_tree?.entries.get(entry_id)?.kind === "file") {
            activate_row(entry_id, true);
        }
    };

    const on_keydown = (event: KeyboardEvent): void => {
        const target = event.target;
        if (!(target instanceof HTMLElement) || target.getAttribute("role") !== "treeitem") {
            return;
        }
        const entry_id = target.dataset["entryId"];
        const index = visible.rows.findIndex((row) => row.entry.entry_id === entry_id);
        const row = visible.rows[index];
        if (row === undefined) {
            return;
        }
        let next_id: string | undefined;
        switch (event.key) {
            case "ArrowDown": {
                next_id = visible.rows[index + 1]?.entry.entry_id;
                if (
                    next_id === undefined &&
                    visible.hasMore &&
                    normalized_filter(current_filter) === ""
                ) {
                    window_offset += tree_window_step;
                    render_current();
                    next_id = visible.rows[0]?.entry.entry_id;
                }

                break;
            }
            case "ArrowUp": {
                next_id = visible.rows[index - 1]?.entry.entry_id;
                if (
                    next_id === undefined &&
                    visible.skipped > 0 &&
                    normalized_filter(current_filter) === ""
                ) {
                    window_offset = Math.max(0, window_offset - tree_window_step);
                    render_current();
                    next_id = visible.rows.at(-1)?.entry.entry_id;
                }

                break;
            }
            case "Home": {
                window_offset = 0;
                render_current();
                next_id = visible.rows[0]?.entry.entry_id;

                break;
            }
            case "End": {
                next_id = visible.rows.at(-1)?.entry.entry_id;

                break;
            }
            default:
                if (event.key === "ArrowRight" && row.entry.kind === "directory") {
                    if (!row.expanded) {
                        options.onToggle(row.entry.entry_id, true);
                    } else {
                        next_id = visible.rows[index + 1]?.entry.entry_id;
                    }
                } else if (event.key === "ArrowLeft") {
                    if (row.entry.kind === "directory" && row.expanded) {
                        options.onToggle(row.entry.entry_id, false);
                    } else {
                        const parent_path = row.entry.path.split("/").slice(0, -1).join("/");
                        next_id = [...(current_tree?.entries.values() ?? [])].find(
                            (entry) => entry.path === parent_path,
                        )?.entry_id;
                    }
                } else if (event.key === "Enter") {
                    activate_row(row.entry.entry_id, true);
                } else if (
                    event.key.length === 1 &&
                    !event.ctrlKey &&
                    !event.metaKey &&
                    !event.altKey
                ) {
                    typeahead += event.key.toLocaleLowerCase();
                    if (typeahead_timer !== undefined) {
                        clearTimeout(typeahead_timer);
                    }
                    typeahead_timer = setTimeout(() => {
                        typeahead = "";
                        typeahead_timer = undefined;
                    }, 700);
                    const after = [
                        ...visible.rows.slice(index + 1),
                        ...visible.rows.slice(0, index + 1),
                    ];
                    next_id = after.find((candidate) =>
                        candidate.entry.name.toLocaleLowerCase().startsWith(typeahead),
                    )?.entry.entry_id;
                } else {
                    return;
                }
        }
        event.preventDefault();
        if (next_id !== undefined) {
            focus_row(next_id);
        }
    };

    const on_search_result_click = (event: MouseEvent): void => {
        const target = event.target;
        if (!(target instanceof Element) || search_response === undefined) {
            return;
        }
        const entry_id =
            target.closest<HTMLElement>("[data-search-entry-id]")?.dataset["searchEntryId"];
        if (entry_id !== undefined) {
            options.onRevealSearchResult(search_response, entry_id);
        }
    };

    const on_search_result_keydown = (event: KeyboardEvent): void => {
        const result_options = [
            ...options.searchResultsRoot.querySelectorAll<HTMLButtonElement>(
                "[data-search-entry-id]",
            ),
        ];
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const target_button = target.closest<HTMLButtonElement>("[data-search-entry-id]");
        if (target_button === null) {
            return;
        }
        const index = result_options.indexOf(target_button);
        if (index === -1) {
            return;
        }
        let next: HTMLButtonElement | undefined;
        switch (event.key) {
            case "ArrowDown":
                next = result_options[(index + 1) % result_options.length];
                break;
            case "ArrowUp":
                next = result_options[(index - 1 + result_options.length) % result_options.length];
                break;
            case "Home":
                next = result_options[0];
                break;
            case "End":
                next = result_options.at(-1);
                break;
            case "Enter":
                event.preventDefault();
                target_button.click();
                return;
            default:
                return;
        }
        event.preventDefault();
        next?.focus();
    };

    options.treeRoot.addEventListener("click", on_click);
    options.treeRoot.addEventListener("dblclick", on_double_click);
    options.treeRoot.addEventListener("keydown", on_keydown);
    options.searchResultsRoot.addEventListener("click", on_search_result_click);
    options.searchResultsRoot.addEventListener("keydown", on_search_result_keydown);

    return {
        render(tree, filter): void {
            const workspace_changed = current_tree?.workspaceId !== tree.workspaceId;
            const filter_changed = normalized_filter(current_filter) !== normalized_filter(filter);
            current_tree = tree;
            current_filter = filter;
            if (workspace_changed || filter_changed) {
                window_offset = 0;
            }
            render_current();
        },
        renderSearchResults(response): void {
            search_response = response;
            options.searchResultsRoot.replaceChildren();
            if (response === undefined || response.entries.length === 0) {
                options.searchResultsRoot.hidden = true;
                return;
            }
            const fragment = document.createDocumentFragment();
            for (const entry of response.entries) {
                const option = document.createElement("button");
                option.type = "button";
                option.dataset["searchEntryId"] = entry.entry_id;
                option.setAttribute("role", "option");
                option.textContent = entry.path;
                fragment.append(option);
            }
            options.searchResultsRoot.append(fragment);
            options.searchResultsRoot.hidden = false;
        },
        focusEntry: focus_row,
        getRenderedRowCount: () => keyed_rows.size,
        destroy(): void {
            options.treeRoot.removeEventListener("click", on_click);
            options.treeRoot.removeEventListener("dblclick", on_double_click);
            options.treeRoot.removeEventListener("keydown", on_keydown);
            options.searchResultsRoot.removeEventListener("click", on_search_result_click);
            options.searchResultsRoot.removeEventListener("keydown", on_search_result_keydown);
            if (typeahead_timer !== undefined) {
                clearTimeout(typeahead_timer);
            }
            keyed_rows.clear();
        },
    };
}
