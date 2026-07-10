import * as z from "zod/mini";

import * as channel from "./channel.ts";

export const pear_workspace_entry_schema = z.object({
    entry_id: z.string(),
    path: z.string(),
    name: z.string(),
    kind: z.enum(["directory", "file"]),
    status: z.string(),
    has_children: z.boolean(),
    size: z.nullable(z.number()),
});

const pear_workspace_capabilities_schema = z.object({
    read_file: z.boolean(),
    write_file: z.boolean(),
    refresh: z.boolean(),
    create_entry: z.boolean(),
    rename_entry: z.boolean(),
    delete_entry: z.boolean(),
    history: z.boolean(),
});

const pear_workspace_limits_schema = z.object({
    text_file_max_bytes: z.number(),
    raw_file_max_bytes: z.nullable(z.number()),
});

export const pear_workspace_descriptor_schema = z.object({
    uuid: z.string(),
    display_root: z.string(),
    stream: z.string(),
    topic: z.string(),
    branch: z.string(),
    head: z.string(),
    dirty: z.boolean(),
    dirty_count: z.number(),
    workspace_revision: z.number(),
    root: pear_workspace_entry_schema,
    default_path: z.nullable(z.string()),
    default_entry: z.nullable(pear_workspace_entry_schema),
    capabilities: pear_workspace_capabilities_schema,
    limits: pear_workspace_limits_schema,
});

export const pear_workspace_descriptor_response_schema = z.object({
    pear_workspace: pear_workspace_descriptor_schema,
});

export const pear_workspace_tree_response_schema = z.object({
    pear_workspace_tree: z.object({
        workspace_uuid: z.string(),
        workspace_revision: z.number(),
        directory: pear_workspace_entry_schema,
        entries: z.array(pear_workspace_entry_schema),
        next_cursor: z.nullable(z.string()),
    }),
});

const pear_workspace_search_entry_schema = z.extend(pear_workspace_entry_schema, {
    ancestors: z.array(pear_workspace_entry_schema),
});

export const pear_workspace_search_response_schema = z.object({
    pear_workspace_search: z.object({
        workspace_uuid: z.string(),
        workspace_revision: z.number(),
        query: z.string(),
        entries: z.array(pear_workspace_search_entry_schema),
    }),
});

export const pear_workspace_file_schema = z.object({
    workspace_uuid: z.string(),
    workspace_revision: z.number(),
    entry_id: z.string(),
    path: z.string(),
    content: z.string(),
    version: z.string(),
    size: z.number(),
    mime_type: z.string(),
    language: z.string(),
    editable: z.boolean(),
    rich_mode_eligible: z.boolean(),
    max_bytes: z.number(),
    edit_revision: z.optional(z.number()),
});

export const pear_workspace_file_response_schema = z.object({
    pear_workspace_file: pear_workspace_file_schema,
});

export const pear_workspace_refresh_response_schema = z.object({
    pear_workspace_refresh: z.object({
        changed: z.boolean(),
        workspace: pear_workspace_descriptor_schema,
    }),
});

export const pear_workspace_mutation_schema = z.object({
    operation: z.enum(["create", "rename", "delete"]),
    workspace_uuid: z.string(),
    workspace_revision: z.number(),
    previous_entry_id: z.nullable(z.string()),
    entry: z.nullable(pear_workspace_entry_schema),
    path: z.string(),
    removed_entries: z.optional(z.number()),
});

export const pear_workspace_mutation_response_schema = z.object({
    pear_workspace_mutation: pear_workspace_mutation_schema,
});

const pear_workspace_history_commit_schema = z.object({
    commit: z.string(),
    authored_at: z.number(),
    author_name: z.string(),
    subject: z.string(),
});

export const pear_workspace_history_response_schema = z.object({
    pear_workspace_history: z.object({
        workspace_uuid: z.string(),
        workspace_revision: z.number(),
        entry: pear_workspace_entry_schema,
        commits: z.array(pear_workspace_history_commit_schema),
        next_cursor: z.nullable(z.string()),
    }),
});

export const pear_workspace_error_schema = z.object({
    result: z.literal("error"),
    msg: z.string(),
    error_code: z.string(),
    workspace_revision: z.optional(z.number()),
    remote_file: z.optional(pear_workspace_file_schema),
});

export type PearWorkspaceEntry = z.infer<typeof pear_workspace_entry_schema>;
export type PearWorkspaceDescriptor = z.infer<typeof pear_workspace_descriptor_schema>;
export type PearWorkspaceTreeResponse = z.infer<typeof pear_workspace_tree_response_schema>;
export type PearWorkspaceSearchResponse = z.infer<typeof pear_workspace_search_response_schema>;
export type PearWorkspaceFile = z.infer<typeof pear_workspace_file_schema>;
export type PearWorkspaceRefreshResponse = z.infer<typeof pear_workspace_refresh_response_schema>;
export type PearWorkspaceMutation = z.infer<typeof pear_workspace_mutation_schema>;
export type PearWorkspaceHistoryResponse = z.infer<typeof pear_workspace_history_response_schema>;
export type PearWorkspaceServerError = z.infer<typeof pear_workspace_error_schema>;

export type PearWorkspaceApiError =
    | {
          kind: "invalid_response";
          message: string;
      }
    | {
          kind: "server";
          status: number;
          error: PearWorkspaceServerError;
      }
    | {
          kind: "transport";
          status: number;
          message: string;
      };

export type PearWorkspaceRequestCallbacks<T> = {
    success: (value: T) => void;
    error: (error: PearWorkspaceApiError) => void;
};

type AbortableRequest = {
    abort: () => unknown;
};

type ActiveRequest = {
    generation: number;
    request: AbortableRequest | undefined;
};

export type PearWorkspaceRequestGuard = {
    begin: (lane: string) => number;
    attach: (lane: string, generation: number, request: AbortableRequest | undefined) => boolean;
    isCurrent: (lane: string, generation: number) => boolean;
    settle: (lane: string, generation: number) => boolean;
    cancel: (lane: string) => void;
    cancelAll: () => void;
};

export function create_pear_workspace_request_guard(): PearWorkspaceRequestGuard {
    const active = new Map<string, ActiveRequest>();
    const last_generation = new Map<string, number>();

    function cancel(lane: string): void {
        const current = active.get(lane);
        active.delete(lane);
        current?.request?.abort();
    }

    return {
        begin(lane): number {
            cancel(lane);
            const generation = (last_generation.get(lane) ?? 0) + 1;
            last_generation.set(lane, generation);
            active.set(lane, {generation, request: undefined});
            return generation;
        },
        attach(lane, generation, request): boolean {
            const current = active.get(lane);
            if (current?.generation !== generation) {
                request?.abort();
                return false;
            }
            current.request = request;
            return true;
        },
        isCurrent(lane, generation): boolean {
            return active.get(lane)?.generation === generation;
        },
        settle(lane, generation): boolean {
            if (active.get(lane)?.generation !== generation) {
                return false;
            }
            active.delete(lane);
            return true;
        },
        cancel,
        cancelAll(): void {
            for (const lane of active.keys()) {
                cancel(lane);
            }
        },
    };
}

function workspace_url(workspace_uuid: string, suffix = ""): string {
    return `/json/pear/workspaces/${encodeURIComponent(workspace_uuid)}${suffix}`;
}

function parse_success<T>(
    schema: z.ZodMiniType<T>,
    raw: unknown,
    callbacks: PearWorkspaceRequestCallbacks<T>,
): void {
    const parsed = schema.safeParse(raw);
    if (!parsed.success) {
        callbacks.error({kind: "invalid_response", message: "Invalid workspace response."});
        return;
    }
    callbacks.success(parsed.data);
}

function parse_error(xhr: JQuery.jqXHR<unknown>): PearWorkspaceApiError {
    const parsed = pear_workspace_error_schema.safeParse(xhr.responseJSON);
    if (parsed.success) {
        return {kind: "server", status: xhr.status, error: parsed.data};
    }
    return {
        kind: "transport",
        status: xhr.status,
        message: xhr.statusText || "Workspace request failed.",
    };
}

function error_callback<T>(
    callbacks: PearWorkspaceRequestCallbacks<T>,
): JQuery.Ajax.ErrorCallback<unknown> {
    return (xhr: JQuery.jqXHR<unknown>) => {
        callbacks.error(parse_error(xhr));
    };
}

export function get_pear_workspace(
    workspace_uuid: string,
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceDescriptor>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.get({
        url: workspace_url(workspace_uuid),
        success(raw): void {
            parse_success(pear_workspace_descriptor_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function get_pear_workspace_tree(
    workspace_uuid: string,
    options: {
        directoryId?: string;
        cursor?: string;
        pageSize?: number;
    },
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceTreeResponse["pear_workspace_tree"]>,
): JQuery.jqXHR<unknown> | undefined {
    const data: Record<string, string | number> = {};
    if (options.directoryId !== undefined) {
        data["directory_id"] = options.directoryId;
    }
    if (options.cursor !== undefined) {
        data["cursor"] = options.cursor;
    }
    if (options.pageSize !== undefined) {
        data["page_size"] = options.pageSize;
    }
    return channel.get({
        url: workspace_url(workspace_uuid, "/tree"),
        data,
        success(raw): void {
            parse_success(pear_workspace_tree_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_tree);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function search_pear_workspace(
    workspace_uuid: string,
    query: string,
    limit: number | undefined,
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceSearchResponse["pear_workspace_search"]>,
): JQuery.jqXHR<unknown> | undefined {
    const data: Record<string, string | number> = {q: query};
    if (limit !== undefined) {
        data["limit"] = limit;
    }
    return channel.get({
        url: workspace_url(workspace_uuid, "/search"),
        data,
        success(raw): void {
            parse_success(pear_workspace_search_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_search);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function get_pear_workspace_file(
    workspace_uuid: string,
    entry_id: string,
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceFile>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.get({
        url: workspace_url(workspace_uuid, "/file"),
        data: {entry_id},
        success(raw): void {
            parse_success(pear_workspace_file_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_file);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function put_pear_workspace_file(
    workspace_uuid: string,
    request: {
        entryId: string;
        expectedPath: string;
        content: string;
        baseVersion: string;
        workspaceRevision: number;
        editRevision: number;
    },
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceFile>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.put({
        url: workspace_url(workspace_uuid, "/file"),
        data: {
            entry_id: request.entryId,
            expected_path: request.expectedPath,
            content: request.content,
            base_version: request.baseVersion,
            workspace_revision: request.workspaceRevision,
            edit_revision: request.editRevision,
        },
        success(raw): void {
            parse_success(pear_workspace_file_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_file);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function create_pear_workspace_entry(
    workspace_uuid: string,
    request: {
        destinationPath: string;
        kind: "file" | "directory";
        workspaceRevision: number;
        content?: string;
    },
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceMutation>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.post({
        url: workspace_url(workspace_uuid, "/entries"),
        data: {
            destination_path: request.destinationPath,
            kind: request.kind,
            workspace_revision: request.workspaceRevision,
            ...(request.content === undefined ? {} : {content: request.content}),
        },
        success(raw): void {
            parse_success(pear_workspace_mutation_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_mutation);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function rename_pear_workspace_entry(
    workspace_uuid: string,
    request: {
        entryId: string;
        expectedPath: string;
        destinationPath: string;
        workspaceRevision: number;
    },
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceMutation>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.patch({
        url: workspace_url(workspace_uuid, "/entry"),
        data: {
            entry_id: request.entryId,
            expected_path: request.expectedPath,
            destination_path: request.destinationPath,
            workspace_revision: request.workspaceRevision,
        },
        success(raw): void {
            parse_success(pear_workspace_mutation_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_mutation);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function delete_pear_workspace_entry(
    workspace_uuid: string,
    request: {
        entryId: string;
        expectedPath: string;
        workspaceRevision: number;
        recursive?: boolean;
        confirmIrreversible?: boolean;
    },
    callbacks: PearWorkspaceRequestCallbacks<PearWorkspaceMutation>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.del({
        url: workspace_url(workspace_uuid, "/entry"),
        data: {
            entry_id: request.entryId,
            expected_path: request.expectedPath,
            workspace_revision: request.workspaceRevision,
            recursive: request.recursive ?? false,
            confirm_irreversible: request.confirmIrreversible ?? false,
        },
        success(raw): void {
            parse_success(pear_workspace_mutation_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_mutation);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function get_pear_workspace_history(
    workspace_uuid: string,
    entry_id: string,
    options: {cursor?: string; limit?: number},
    callbacks: PearWorkspaceRequestCallbacks<
        PearWorkspaceHistoryResponse["pear_workspace_history"]
    >,
): JQuery.jqXHR<unknown> | undefined {
    return channel.get({
        url: workspace_url(workspace_uuid, "/history"),
        data: {
            entry_id,
            ...(options.cursor === undefined ? {} : {cursor: options.cursor}),
            ...(options.limit === undefined ? {} : {limit: options.limit}),
        },
        success(raw): void {
            parse_success(pear_workspace_history_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_history);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function refresh_pear_workspace(
    workspace_uuid: string,
    callbacks: PearWorkspaceRequestCallbacks<
        PearWorkspaceRefreshResponse["pear_workspace_refresh"]
    >,
): JQuery.jqXHR<unknown> | undefined {
    return channel.post({
        url: workspace_url(workspace_uuid, "/refresh"),
        success(raw): void {
            parse_success(pear_workspace_refresh_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.pear_workspace_refresh);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function pear_workspace_raw_url(
    workspace_uuid: string,
    entry_id: string,
    download = false,
): string {
    const query = new URLSearchParams({entry_id});
    if (download) {
        query.set("download", "true");
    }
    return `${workspace_url(workspace_uuid, "/raw")}?${query.toString()}`;
}
