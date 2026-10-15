import * as z from "zod/mini";

import * as channel from "./channel.ts";

export const armada_workspace_entry_schema = z.object({
    entry_id: z.string(),
    path: z.string(),
    name: z.string(),
    kind: z.enum(["directory", "file"]),
    status: z.string(),
    has_children: z.boolean(),
    size: z.nullable(z.number()),
});

const armada_workspace_capabilities_schema = z.object({
    read_file: z.boolean(),
    write_file: z.boolean(),
    refresh: z.boolean(),
    create_entry: z.boolean(),
    rename_entry: z.boolean(),
    delete_entry: z.boolean(),
    history: z.boolean(),
});

const armada_workspace_limits_schema = z.object({
    text_file_max_bytes: z.number(),
    raw_file_max_bytes: z.nullable(z.number()),
});

export const armada_workspace_descriptor_schema = z.object({
    uuid: z.string(),
    display_root: z.string(),
    stream: z.string(),
    topic: z.string(),
    branch: z.string(),
    head: z.string(),
    dirty: z.boolean(),
    dirty_count: z.number(),
    workspace_revision: z.number(),
    root: armada_workspace_entry_schema,
    default_path: z.nullable(z.string()),
    default_entry: z.nullable(armada_workspace_entry_schema),
    capabilities: armada_workspace_capabilities_schema,
    limits: armada_workspace_limits_schema,
});

export const armada_workspace_descriptor_response_schema = z.object({
    armada_workspace: armada_workspace_descriptor_schema,
});

export const armada_workspace_tree_response_schema = z.object({
    armada_workspace_tree: z.object({
        workspace_uuid: z.string(),
        workspace_revision: z.number(),
        directory: armada_workspace_entry_schema,
        entries: z.array(armada_workspace_entry_schema),
        next_cursor: z.nullable(z.string()),
    }),
});

const armada_workspace_search_entry_schema = z.extend(armada_workspace_entry_schema, {
    ancestors: z.array(armada_workspace_entry_schema),
});

export const armada_workspace_search_response_schema = z.object({
    armada_workspace_search: z.object({
        workspace_uuid: z.string(),
        workspace_revision: z.number(),
        query: z.string(),
        entries: z.array(armada_workspace_search_entry_schema),
    }),
});

export const armada_workspace_file_schema = z.object({
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

export const armada_workspace_file_response_schema = z.object({
    armada_workspace_file: armada_workspace_file_schema,
});

export const armada_workspace_refresh_response_schema = z.object({
    armada_workspace_refresh: z.object({
        changed: z.boolean(),
        workspace: armada_workspace_descriptor_schema,
    }),
});

export const armada_workspace_mutation_schema = z.object({
    operation: z.enum(["create", "rename", "delete"]),
    workspace_uuid: z.string(),
    workspace_revision: z.number(),
    previous_entry_id: z.nullable(z.string()),
    entry: z.nullable(armada_workspace_entry_schema),
    path: z.string(),
    removed_entries: z.optional(z.number()),
});

export const armada_workspace_mutation_response_schema = z.object({
    armada_workspace_mutation: armada_workspace_mutation_schema,
});

const armada_workspace_history_commit_schema = z.object({
    commit: z.string(),
    authored_at: z.number(),
    author_name: z.string(),
    subject: z.string(),
});

export const armada_workspace_history_response_schema = z.object({
    armada_workspace_history: z.object({
        workspace_uuid: z.string(),
        workspace_revision: z.number(),
        entry: armada_workspace_entry_schema,
        commits: z.array(armada_workspace_history_commit_schema),
        next_cursor: z.nullable(z.string()),
    }),
});

export const armada_workspace_error_schema = z.object({
    result: z.literal("error"),
    msg: z.string(),
    error_code: z.string(),
    workspace_revision: z.optional(z.number()),
    remote_file: z.optional(armada_workspace_file_schema),
});

export type ArmadaWorkspaceEntry = z.infer<typeof armada_workspace_entry_schema>;
export type ArmadaWorkspaceDescriptor = z.infer<typeof armada_workspace_descriptor_schema>;
export type ArmadaWorkspaceTreeResponse = z.infer<typeof armada_workspace_tree_response_schema>;
export type ArmadaWorkspaceSearchResponse = z.infer<typeof armada_workspace_search_response_schema>;
export type ArmadaWorkspaceFile = z.infer<typeof armada_workspace_file_schema>;
export type ArmadaWorkspaceRefreshResponse = z.infer<
    typeof armada_workspace_refresh_response_schema
>;
export type ArmadaWorkspaceMutation = z.infer<typeof armada_workspace_mutation_schema>;
export type ArmadaWorkspaceHistoryResponse = z.infer<
    typeof armada_workspace_history_response_schema
>;
export type ArmadaWorkspaceServerError = z.infer<typeof armada_workspace_error_schema>;

export type ArmadaWorkspaceApiError =
    | {
          kind: "invalid_response";
          message: string;
      }
    | {
          kind: "server";
          status: number;
          error: ArmadaWorkspaceServerError;
      }
    | {
          kind: "transport";
          status: number;
          message: string;
      };

export type ArmadaWorkspaceRequestCallbacks<T> = {
    success: (value: T) => void;
    error: (error: ArmadaWorkspaceApiError) => void;
};

type AbortableRequest = {
    abort: () => unknown;
};

type ActiveRequest = {
    generation: number;
    request: AbortableRequest | undefined;
};

export type ArmadaWorkspaceRequestGuard = {
    begin: (lane: string) => number;
    attach: (lane: string, generation: number, request: AbortableRequest | undefined) => boolean;
    isCurrent: (lane: string, generation: number) => boolean;
    settle: (lane: string, generation: number) => boolean;
    cancel: (lane: string) => void;
    cancelAll: () => void;
};

export function create_armada_workspace_request_guard(): ArmadaWorkspaceRequestGuard {
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
    return `/json/armada/workspaces/${encodeURIComponent(workspace_uuid)}${suffix}`;
}

function parse_success<T>(
    schema: z.ZodMiniType<T>,
    raw: unknown,
    callbacks: ArmadaWorkspaceRequestCallbacks<T>,
): void {
    const parsed = schema.safeParse(raw);
    if (!parsed.success) {
        callbacks.error({kind: "invalid_response", message: "Invalid workspace response."});
        return;
    }
    callbacks.success(parsed.data);
}

function parse_error(xhr: JQuery.jqXHR<unknown>): ArmadaWorkspaceApiError {
    const parsed = armada_workspace_error_schema.safeParse(xhr.responseJSON);
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
    callbacks: ArmadaWorkspaceRequestCallbacks<T>,
): JQuery.Ajax.ErrorCallback<unknown> {
    return (xhr: JQuery.jqXHR<unknown>) => {
        callbacks.error(parse_error(xhr));
    };
}

export function get_armada_workspace(
    workspace_uuid: string,
    callbacks: ArmadaWorkspaceRequestCallbacks<ArmadaWorkspaceDescriptor>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.get({
        url: workspace_url(workspace_uuid),
        success(raw): void {
            parse_success(armada_workspace_descriptor_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function get_armada_workspace_tree(
    workspace_uuid: string,
    options: {
        directoryId?: string;
        cursor?: string;
        pageSize?: number;
    },
    callbacks: ArmadaWorkspaceRequestCallbacks<
        ArmadaWorkspaceTreeResponse["armada_workspace_tree"]
    >,
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
            parse_success(armada_workspace_tree_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_tree);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function search_armada_workspace(
    workspace_uuid: string,
    query: string,
    limit: number | undefined,
    callbacks: ArmadaWorkspaceRequestCallbacks<
        ArmadaWorkspaceSearchResponse["armada_workspace_search"]
    >,
): JQuery.jqXHR<unknown> | undefined {
    const data: Record<string, string | number> = {q: query};
    if (limit !== undefined) {
        data["limit"] = limit;
    }
    return channel.get({
        url: workspace_url(workspace_uuid, "/search"),
        data,
        success(raw): void {
            parse_success(armada_workspace_search_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_search);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function get_armada_workspace_file(
    workspace_uuid: string,
    entry_id: string,
    callbacks: ArmadaWorkspaceRequestCallbacks<ArmadaWorkspaceFile>,
): JQuery.jqXHR<unknown> | undefined {
    return channel.get({
        url: workspace_url(workspace_uuid, "/file"),
        data: {entry_id},
        success(raw): void {
            parse_success(armada_workspace_file_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_file);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function put_armada_workspace_file(
    workspace_uuid: string,
    request: {
        entryId: string;
        expectedPath: string;
        content: string;
        baseVersion: string;
        workspaceRevision: number;
        editRevision: number;
    },
    callbacks: ArmadaWorkspaceRequestCallbacks<ArmadaWorkspaceFile>,
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
            parse_success(armada_workspace_file_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_file);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function create_armada_workspace_entry(
    workspace_uuid: string,
    request: {
        destinationPath: string;
        kind: "file" | "directory";
        workspaceRevision: number;
        content?: string;
    },
    callbacks: ArmadaWorkspaceRequestCallbacks<ArmadaWorkspaceMutation>,
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
            parse_success(armada_workspace_mutation_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_mutation);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function rename_armada_workspace_entry(
    workspace_uuid: string,
    request: {
        entryId: string;
        expectedPath: string;
        destinationPath: string;
        workspaceRevision: number;
    },
    callbacks: ArmadaWorkspaceRequestCallbacks<ArmadaWorkspaceMutation>,
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
            parse_success(armada_workspace_mutation_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_mutation);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function delete_armada_workspace_entry(
    workspace_uuid: string,
    request: {
        entryId: string;
        expectedPath: string;
        workspaceRevision: number;
        recursive?: boolean;
        confirmIrreversible?: boolean;
    },
    callbacks: ArmadaWorkspaceRequestCallbacks<ArmadaWorkspaceMutation>,
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
            parse_success(armada_workspace_mutation_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_mutation);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function get_armada_workspace_history(
    workspace_uuid: string,
    entry_id: string,
    options: {cursor?: string; limit?: number},
    callbacks: ArmadaWorkspaceRequestCallbacks<
        ArmadaWorkspaceHistoryResponse["armada_workspace_history"]
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
            parse_success(armada_workspace_history_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_history);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function refresh_armada_workspace(
    workspace_uuid: string,
    callbacks: ArmadaWorkspaceRequestCallbacks<
        ArmadaWorkspaceRefreshResponse["armada_workspace_refresh"]
    >,
): JQuery.jqXHR<unknown> | undefined {
    return channel.post({
        url: workspace_url(workspace_uuid, "/refresh"),
        success(raw): void {
            parse_success(armada_workspace_refresh_response_schema, raw, {
                success(value): void {
                    callbacks.success(value.armada_workspace_refresh);
                },
                error: callbacks.error,
            });
        },
        error: error_callback(callbacks),
    });
}

export function armada_workspace_raw_url(
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
