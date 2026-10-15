import type {
    ArmadaWorkspaceDescriptor,
    ArmadaWorkspaceEntry,
    ArmadaWorkspaceSearchResponse,
    ArmadaWorkspaceTreeResponse,
} from "./legacy_workspace_api.ts";

export type ArmadaWorkspaceDirectoryState = {
    childIds: string[];
    nextCursor: string | null;
    complete: boolean;
    loading: boolean;
    requestGeneration: number;
    requestCursor: string | undefined;
    error: string | undefined;
};

export type ArmadaWorkspaceTreeState = {
    workspaceId: string;
    workspaceRevision: number;
    rootId: string;
    entries: Map<string, ArmadaWorkspaceEntry>;
    directories: Map<string, ArmadaWorkspaceDirectoryState>;
    expandedPaths: Set<string>;
    selectedId: string | undefined;
    selectedPath: string | undefined;
    ignoredStaleResponses: number;
};

export type ArmadaWorkspaceDirectoryRequest = {
    workspaceId: string;
    workspaceRevision: number;
    directoryId: string;
    cursor: string | undefined;
    requestGeneration: number;
};

export type ArmadaWorkspaceTreeRow = {
    entry: ArmadaWorkspaceEntry;
    depth: number;
    expanded: boolean;
    loading: boolean;
};

export type ArmadaWorkspaceTreeWindow = {
    rows: ArmadaWorkspaceTreeRow[];
    skipped: number;
    hasMore: boolean;
};

const empty_directory = (): ArmadaWorkspaceDirectoryState => ({
    childIds: [],
    nextCursor: null,
    complete: false,
    loading: false,
    requestGeneration: 0,
    requestCursor: undefined,
    error: undefined,
});

function with_directory(
    state: ArmadaWorkspaceTreeState,
    directory_id: string,
    directory: ArmadaWorkspaceDirectoryState,
): ArmadaWorkspaceTreeState {
    const directories = new Map(state.directories);
    directories.set(directory_id, directory);
    return {...state, directories};
}

function ignore_stale(state: ArmadaWorkspaceTreeState): ArmadaWorkspaceTreeState {
    return {...state, ignoredStaleResponses: state.ignoredStaleResponses + 1};
}

function is_direct_child(parent_path: string, child_path: string): boolean {
    if (parent_path === "") {
        return child_path !== "" && !child_path.includes("/");
    }
    if (!child_path.startsWith(`${parent_path}/`)) {
        return false;
    }
    return !child_path.slice(parent_path.length + 1).includes("/");
}

export function create_armada_workspace_tree(
    descriptor: ArmadaWorkspaceDescriptor,
): ArmadaWorkspaceTreeState {
    return {
        workspaceId: descriptor.uuid,
        workspaceRevision: descriptor.workspace_revision,
        rootId: descriptor.root.entry_id,
        entries: new Map([[descriptor.root.entry_id, descriptor.root]]),
        directories: new Map([[descriptor.root.entry_id, empty_directory()]]),
        expandedPaths: new Set([""]),
        selectedId: descriptor.default_entry?.entry_id,
        selectedPath: descriptor.default_entry?.path,
        ignoredStaleResponses: 0,
    };
}

export function replace_armada_workspace_tree_revision(
    state: ArmadaWorkspaceTreeState,
    descriptor: ArmadaWorkspaceDescriptor,
): ArmadaWorkspaceTreeState {
    if (descriptor.uuid !== state.workspaceId) {
        return create_armada_workspace_tree(descriptor);
    }
    if (descriptor.workspace_revision < state.workspaceRevision) {
        return ignore_stale(state);
    }
    if (descriptor.workspace_revision === state.workspaceRevision) {
        return state;
    }
    const default_entry = descriptor.default_entry;
    const selected_path = state.selectedPath ?? default_entry?.path;
    return {
        workspaceId: descriptor.uuid,
        workspaceRevision: descriptor.workspace_revision,
        rootId: descriptor.root.entry_id,
        entries: new Map([[descriptor.root.entry_id, descriptor.root]]),
        directories: new Map([[descriptor.root.entry_id, empty_directory()]]),
        expandedPaths: new Set(state.expandedPaths),
        selectedId:
            default_entry !== null && default_entry.path === selected_path
                ? default_entry.entry_id
                : undefined,
        selectedPath: selected_path,
        ignoredStaleResponses: state.ignoredStaleResponses,
    };
}

export function begin_armada_workspace_directory_request(
    state: ArmadaWorkspaceTreeState,
    directory_id: string,
): {state: ArmadaWorkspaceTreeState; request: ArmadaWorkspaceDirectoryRequest | undefined} {
    const entry = state.entries.get(directory_id);
    if (entry?.kind !== "directory") {
        return {state, request: undefined};
    }
    const current = state.directories.get(directory_id) ?? empty_directory();
    if (current.loading || current.complete) {
        return {state, request: undefined};
    }
    const request_generation = current.requestGeneration + 1;
    const cursor = current.childIds.length === 0 ? undefined : (current.nextCursor ?? undefined);
    const directory = {
        ...current,
        loading: true,
        requestGeneration: request_generation,
        requestCursor: cursor,
        error: undefined,
    };
    return {
        state: with_directory(state, directory_id, directory),
        request: {
            workspaceId: state.workspaceId,
            workspaceRevision: state.workspaceRevision,
            directoryId: directory_id,
            cursor,
            requestGeneration: request_generation,
        },
    };
}

export function receive_armada_workspace_directory_page(
    state: ArmadaWorkspaceTreeState,
    request: ArmadaWorkspaceDirectoryRequest,
    response: ArmadaWorkspaceTreeResponse["armada_workspace_tree"],
): ArmadaWorkspaceTreeState {
    const current = state.directories.get(request.directoryId);
    if (
        request.workspaceId !== state.workspaceId ||
        request.workspaceRevision !== state.workspaceRevision ||
        response.workspace_uuid !== state.workspaceId ||
        response.workspace_revision !== state.workspaceRevision ||
        response.directory.entry_id !== request.directoryId ||
        current?.loading !== true ||
        current.requestGeneration !== request.requestGeneration ||
        current.requestCursor !== request.cursor
    ) {
        return ignore_stale(state);
    }
    if (
        response.directory.kind !== "directory" ||
        response.entries.some((entry) => !is_direct_child(response.directory.path, entry.path))
    ) {
        return with_directory(state, request.directoryId, {
            ...current,
            loading: false,
            error: "invalid_tree_page",
        });
    }

    const entries = new Map(state.entries);
    entries.set(response.directory.entry_id, response.directory);
    const child_ids = [...current.childIds];
    const seen = new Set(child_ids);
    const directories = new Map(state.directories);
    for (const entry of response.entries) {
        entries.set(entry.entry_id, entry);
        if (!seen.has(entry.entry_id)) {
            child_ids.push(entry.entry_id);
            seen.add(entry.entry_id);
        }
        if (entry.kind === "directory" && !directories.has(entry.entry_id)) {
            directories.set(entry.entry_id, empty_directory());
        }
    }
    directories.set(request.directoryId, {
        ...current,
        childIds: child_ids,
        nextCursor: response.next_cursor,
        complete: response.next_cursor === null,
        loading: false,
        requestCursor: undefined,
        error: undefined,
    });
    const selected_id =
        state.selectedId ??
        [...entries.values()].find((entry) => entry.path === state.selectedPath)?.entry_id;
    return {...state, entries, directories, selectedId: selected_id};
}

export function fail_armada_workspace_directory_request(
    state: ArmadaWorkspaceTreeState,
    request: ArmadaWorkspaceDirectoryRequest,
    error: string,
): ArmadaWorkspaceTreeState {
    const current = state.directories.get(request.directoryId);
    if (
        request.workspaceId !== state.workspaceId ||
        request.workspaceRevision !== state.workspaceRevision ||
        current?.requestGeneration !== request.requestGeneration ||
        current.requestCursor !== request.cursor
    ) {
        return ignore_stale(state);
    }
    return with_directory(state, request.directoryId, {
        ...current,
        loading: false,
        requestCursor: undefined,
        error,
    });
}

export function set_armada_workspace_directory_expanded(
    state: ArmadaWorkspaceTreeState,
    directory_id: string,
    expanded: boolean,
): ArmadaWorkspaceTreeState {
    const entry = state.entries.get(directory_id);
    if (entry?.kind !== "directory") {
        return state;
    }
    const expanded_paths = new Set(state.expandedPaths);
    if (expanded) {
        expanded_paths.add(entry.path);
    } else {
        expanded_paths.delete(entry.path);
    }
    return {...state, expandedPaths: expanded_paths};
}

export function select_armada_workspace_entry(
    state: ArmadaWorkspaceTreeState,
    entry_id: string,
): ArmadaWorkspaceTreeState {
    const entry = state.entries.get(entry_id);
    if (entry === undefined) {
        return state;
    }
    return {...state, selectedId: entry_id, selectedPath: entry.path};
}

export function reveal_armada_workspace_search_result(
    state: ArmadaWorkspaceTreeState,
    response: ArmadaWorkspaceSearchResponse["armada_workspace_search"],
    entry_id: string,
): ArmadaWorkspaceTreeState {
    if (
        response.workspace_uuid !== state.workspaceId ||
        response.workspace_revision !== state.workspaceRevision
    ) {
        return ignore_stale(state);
    }
    const result = response.entries.find((candidate) => candidate.entry_id === entry_id);
    if (result === undefined) {
        return state;
    }

    const entries = new Map(state.entries);
    const directories = new Map(state.directories);
    const expanded_paths = new Set(state.expandedPaths);
    const chain: ArmadaWorkspaceEntry[] = [];
    const seen_ids = new Set<string>();
    for (const entry of [state.entries.get(state.rootId), ...result.ancestors, result]) {
        if (entry !== undefined && !seen_ids.has(entry.entry_id)) {
            chain.push(entry);
            seen_ids.add(entry.entry_id);
        }
    }
    for (let index = 0; index < chain.length; index += 1) {
        const entry = chain[index]!;
        entries.set(entry.entry_id, entry);
        if (entry.kind === "directory") {
            expanded_paths.add(entry.path);
            const directory = directories.get(entry.entry_id) ?? empty_directory();
            const child = chain[index + 1];
            if (child !== undefined && !directory.childIds.includes(child.entry_id)) {
                directories.set(entry.entry_id, {
                    ...directory,
                    childIds: [...directory.childIds, child.entry_id],
                });
            } else if (!directories.has(entry.entry_id)) {
                directories.set(entry.entry_id, directory);
            }
        }
    }
    return {
        ...state,
        entries,
        directories,
        expandedPaths: expanded_paths,
        selectedId: result.entry_id,
        selectedPath: result.path,
    };
}

export function armada_workspace_tree_window(
    state: ArmadaWorkspaceTreeState,
    options: {offset: number; limit: number},
): ArmadaWorkspaceTreeWindow {
    const offset = Math.max(0, Math.trunc(options.offset));
    const limit = Math.max(1, Math.trunc(options.limit));
    const rows: ArmadaWorkspaceTreeRow[] = [];
    let visible_index = 0;
    let has_more = false;

    const visit = (directory_id: string, depth: number): boolean => {
        const directory = state.directories.get(directory_id);
        if (directory === undefined) {
            return true;
        }
        for (const child_id of directory.childIds) {
            const entry = state.entries.get(child_id);
            if (entry === undefined) {
                continue;
            }
            if (visible_index >= offset) {
                if (rows.length >= limit) {
                    has_more = true;
                    return false;
                }
                const child_directory = state.directories.get(child_id);
                rows.push({
                    entry,
                    depth,
                    expanded: entry.kind === "directory" && state.expandedPaths.has(entry.path),
                    loading: child_directory?.loading ?? false,
                });
            }
            visible_index += 1;
            if (
                entry.kind === "directory" &&
                state.expandedPaths.has(entry.path) &&
                !visit(entry.entry_id, depth + 1)
            ) {
                return false;
            }
        }
        return true;
    };

    visit(state.rootId, 0);
    return {rows, skipped: Math.min(offset, visible_index), hasMore: has_more};
}
