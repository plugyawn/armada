export type PearWorkspaceEditorMode = "rich" | "source";

export type PearWorkspaceRichEligibility = "source_only" | "supported" | "unchecked";

export type PearWorkspaceDocumentState = "clean" | "conflict" | "dirty" | "error" | "saving";

export type PearWorkspaceDocumentAuthority = "current" | "detached" | "stale";

export type PearWorkspaceSourceSelection = {
    anchor: number;
    head: number;
};

export type PearWorkspaceRichSelection = {
    anchor: number;
    head: number;
};

export type PearWorkspaceSerialization = {
    state: "error" | "idle" | "scheduled" | "serializing";
    requestedRevision: number | undefined;
    error: string | undefined;
};

export type PearWorkspaceSaveSnapshot = {
    workspaceId: string;
    workspaceRevision: number;
    entryId: string;
    path: string;
    requestGeneration: number;
    editRevision: number;
    baseVersion: string;
    text: string;
};

export type PearWorkspaceConflict = {
    remoteText: string;
    remoteVersion: string;
    remoteEntryId: string;
    remoteWorkspaceRevision: number;
};

export type PearWorkspaceDocumentSession = {
    entryId: string;
    path: string;
    requestGeneration: number;
    baseVersion: string;
    maxBytes: number;
    savedText: string;
    currentText: string;
    editorMode: PearWorkspaceEditorMode;
    richModeEligibility: PearWorkspaceRichEligibility;
    serializedRevision: number;
    serialization: PearWorkspaceSerialization;
    state: PearWorkspaceDocumentState;
    editRevision: number;
    acknowledgedRevision: number;
    saveInFlight: PearWorkspaceSaveSnapshot | undefined;
    conflict: PearWorkspaceConflict | undefined;
    error: string | undefined;
    authority: PearWorkspaceDocumentAuthority;
    sourceView: {
        selection: PearWorkspaceSourceSelection;
        scrollTop: number;
    };
    richView: {
        selection: PearWorkspaceRichSelection | undefined;
        scrollTop: number;
    };
};

export type PearWorkspaceState = {
    workspaceId: string;
    workspaceRevision: number;
    requestGeneration: number;
    activeEntryId: string | undefined;
    pendingDocumentRequest:
        | {
              entryId: string;
              path: string;
              requestGeneration: number;
          }
        | undefined;
    documents: Map<string, PearWorkspaceDocumentSession>;
    markdownModePreference: PearWorkspaceEditorMode;
};

export type PearWorkspaceStoreState = {
    activeWorkspaceId: string | undefined;
    workspaces: Map<string, PearWorkspaceState>;
    ignoredStaleResponses: number;
};

export type PearWorkspaceDocumentIdentity = {
    workspaceId: string;
    entryId: string;
    path: string;
    requestGeneration: number;
};

export type PearWorkspaceStoreAction =
    | {
          type: "activate_workspace";
          workspaceId: string;
          workspaceRevision: number;
      }
    | {
          type: "open_document";
          workspaceId: string;
          entryId: string;
          path: string;
      }
    | ({type: "deactivate_document"} & PearWorkspaceDocumentIdentity)
    | {
          type: "advance_workspace_revision";
          workspaceId: string;
          workspaceRevision: number;
          currentEntryIds?: string[];
      }
    | ({
          type: "rebind_document";
          newEntryId: string;
          newPath: string;
          newVersion: string;
          remoteText: string;
          workspaceRevision: number;
      } & PearWorkspaceDocumentIdentity)
    | ({type: "detach_document"} & PearWorkspaceDocumentIdentity)
    | ({type: "discard_changes"} & PearWorkspaceDocumentIdentity)
    | {
          type: "set_workspace_mode_preference";
          workspaceId: string;
          mode: PearWorkspaceEditorMode;
      }
    | ({
          type: "document_loaded";
          workspaceRevision: number;
          baseVersion: string;
          maxBytes: number;
          text: string;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "set_editor_mode";
          mode: PearWorkspaceEditorMode;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "set_rich_eligibility";
          editRevision: number;
          eligibility: Exclude<PearWorkspaceRichEligibility, "unchecked">;
      } & PearWorkspaceDocumentIdentity)
    | ({type: "source_changed"} & PearWorkspaceDocumentIdentity)
    | ({
          type: "source_flushed";
          editRevision: number;
          text: string;
      } & PearWorkspaceDocumentIdentity)
    | ({type: "rich_edited"} & PearWorkspaceDocumentIdentity)
    | ({
          type: "rich_recovered";
          editRevision: number;
          text: string;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "serialization_requested";
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "serialization_started";
          editRevision: number;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "serialization_succeeded";
          editRevision: number;
          text: string;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "serialization_failed";
          editRevision: number;
          error: string;
      } & PearWorkspaceDocumentIdentity)
    | ({type: "save_started"} & PearWorkspaceDocumentIdentity)
    | ({
          type: "save_succeeded";
          editRevision: number;
          newEntryId: string;
          newVersion: string;
          workspaceRevision: number;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "save_failed";
          editRevision: number;
          error: string;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "save_conflicted";
          editRevision: number;
          remoteText: string;
          remoteVersion: string;
          remoteEntryId: string;
          remoteWorkspaceRevision: number;
      } & PearWorkspaceDocumentIdentity)
    | ({type: "conflict_reload" | "conflict_overwrite"} & PearWorkspaceDocumentIdentity)
    | ({
          type: "source_view_changed";
          selection: PearWorkspaceSourceSelection;
          scrollTop: number;
      } & PearWorkspaceDocumentIdentity)
    | ({
          type: "rich_view_changed";
          selection?: PearWorkspaceRichSelection;
          scrollTop: number;
      } & PearWorkspaceDocumentIdentity);

export function create_pear_workspace_store(): PearWorkspaceStoreState {
    return {
        activeWorkspaceId: undefined,
        workspaces: new Map(),
        ignoredStaleResponses: 0,
    };
}

export function get_pear_workspace(
    state: PearWorkspaceStoreState,
    workspace_id: string,
): PearWorkspaceState | undefined {
    return state.workspaces.get(workspace_id);
}

export function get_pear_workspace_document(
    state: PearWorkspaceStoreState,
    identity: PearWorkspaceDocumentIdentity,
): PearWorkspaceDocumentSession | undefined {
    const document = state.workspaces.get(identity.workspaceId)?.documents.get(identity.entryId);
    if (
        document?.requestGeneration !== identity.requestGeneration ||
        document.path !== identity.path
    ) {
        return undefined;
    }
    return document;
}

function with_workspace(
    state: PearWorkspaceStoreState,
    workspace: PearWorkspaceState,
): PearWorkspaceStoreState {
    const workspaces = new Map(state.workspaces);
    workspaces.set(workspace.workspaceId, workspace);
    return {...state, workspaces};
}

function with_document(
    state: PearWorkspaceStoreState,
    workspace: PearWorkspaceState,
    document: PearWorkspaceDocumentSession,
    previous_entry_id: string = document.entryId,
): PearWorkspaceStoreState {
    const documents = new Map(workspace.documents);
    if (previous_entry_id !== document.entryId) {
        documents.delete(previous_entry_id);
    }
    documents.set(document.entryId, document);
    return with_workspace(state, {...workspace, documents});
}

function ignore_stale_response(state: PearWorkspaceStoreState): PearWorkspaceStoreState {
    return {...state, ignoredStaleResponses: state.ignoredStaleResponses + 1};
}

function matching_document(
    state: PearWorkspaceStoreState,
    identity: PearWorkspaceDocumentIdentity,
): {workspace: PearWorkspaceState; document: PearWorkspaceDocumentSession} | undefined {
    const workspace = state.workspaces.get(identity.workspaceId);
    const document = workspace?.documents.get(identity.entryId);
    if (
        workspace === undefined ||
        document?.requestGeneration !== identity.requestGeneration ||
        document.path !== identity.path
    ) {
        return undefined;
    }
    return {workspace, document};
}

function matching_active_document(
    state: PearWorkspaceStoreState,
    identity: PearWorkspaceDocumentIdentity,
): {workspace: PearWorkspaceState; document: PearWorkspaceDocumentSession} | undefined {
    const match = matching_document(state, identity);
    if (
        match === undefined ||
        state.activeWorkspaceId !== identity.workspaceId ||
        match.workspace.activeEntryId !== identity.entryId
    ) {
        return undefined;
    }
    return match;
}

function document_state_after_change(
    document: PearWorkspaceDocumentSession,
): PearWorkspaceDocumentState {
    if (document.serialization.state === "error") {
        return "error";
    }
    if (document.conflict !== undefined) {
        return "conflict";
    }
    if (document.saveInFlight !== undefined) {
        return "saving";
    }
    return "dirty";
}

function acknowledge_noop_edit(
    document: PearWorkspaceDocumentSession,
): PearWorkspaceDocumentSession {
    if (
        document.saveInFlight === undefined &&
        document.serialization.state === "idle" &&
        document.serializedRevision === document.editRevision &&
        document.currentText === document.savedText
    ) {
        return {
            ...document,
            acknowledgedRevision: document.editRevision,
            state: "clean",
            error: undefined,
        };
    }
    return {...document, state: document_state_after_change(document)};
}

function save_callback_matches(
    document: PearWorkspaceDocumentSession,
    action: {
        requestGeneration: number;
        editRevision: number;
    },
): boolean {
    return (
        document.saveInFlight?.requestGeneration === action.requestGeneration &&
        document.saveInFlight.editRevision === action.editRevision
    );
}

export function reduce_pear_workspace_store(
    state: PearWorkspaceStoreState,
    action: PearWorkspaceStoreAction,
): PearWorkspaceStoreState {
    if (action.type === "activate_workspace") {
        let next_state = state;
        if (
            state.activeWorkspaceId !== undefined &&
            state.activeWorkspaceId !== action.workspaceId
        ) {
            const previous = state.workspaces.get(state.activeWorkspaceId);
            if (previous?.pendingDocumentRequest !== undefined) {
                next_state = with_workspace(state, {
                    ...previous,
                    requestGeneration: previous.requestGeneration + 1,
                    pendingDocumentRequest: undefined,
                });
            }
        }
        const existing = next_state.workspaces.get(action.workspaceId);
        const workspace: PearWorkspaceState =
            existing === undefined
                ? {
                      workspaceId: action.workspaceId,
                      workspaceRevision: action.workspaceRevision,
                      requestGeneration: 0,
                      activeEntryId: undefined,
                      pendingDocumentRequest: undefined,
                      documents: new Map(),
                      markdownModePreference: "source",
                  }
                : {
                      ...existing,
                      // Revision advances are explicit state transitions. Keeping
                      // them out of activation lets the caller mark every
                      // revision-bound document identity stale atomically.
                      workspaceRevision: existing.workspaceRevision,
                  };
        return {
            ...with_workspace(next_state, workspace),
            activeWorkspaceId: action.workspaceId,
        };
    }

    if (action.type === "open_document") {
        const workspace = state.workspaces.get(action.workspaceId);
        if (workspace === undefined || state.activeWorkspaceId !== action.workspaceId) {
            return ignore_stale_response(state);
        }
        const request_generation = workspace.requestGeneration + 1;
        return with_workspace(state, {
            ...workspace,
            requestGeneration: request_generation,
            activeEntryId: action.entryId,
            pendingDocumentRequest: {
                entryId: action.entryId,
                path: action.path,
                requestGeneration: request_generation,
            },
        });
    }

    if (action.type === "deactivate_document") {
        const match = matching_active_document(state, action);
        if (match?.document.state !== "clean") {
            return ignore_stale_response(state);
        }
        return with_workspace(state, {
            ...match.workspace,
            requestGeneration: match.workspace.requestGeneration + 1,
            activeEntryId: undefined,
            pendingDocumentRequest: undefined,
        });
    }

    if (action.type === "advance_workspace_revision") {
        const workspace = state.workspaces.get(action.workspaceId);
        if (workspace === undefined || action.workspaceRevision < workspace.workspaceRevision) {
            return ignore_stale_response(state);
        }
        if (action.workspaceRevision === workspace.workspaceRevision) {
            return state;
        }
        const current_entry_ids = new Set(action.currentEntryIds);
        const documents = new Map(
            [...workspace.documents].map(([entry_id, document]) => [
                entry_id,
                {
                    ...document,
                    authority:
                        document.authority === "detached"
                            ? "detached"
                            : current_entry_ids.has(entry_id)
                              ? "current"
                              : "stale",
                } satisfies PearWorkspaceDocumentSession,
            ]),
        );
        return with_workspace(state, {
            ...workspace,
            workspaceRevision: action.workspaceRevision,
            pendingDocumentRequest: undefined,
            documents,
        });
    }

    if (action.type === "set_workspace_mode_preference") {
        const workspace = state.workspaces.get(action.workspaceId);
        if (workspace === undefined) {
            return ignore_stale_response(state);
        }
        return with_workspace(state, {...workspace, markdownModePreference: action.mode});
    }

    if (action.type === "document_loaded") {
        const workspace = state.workspaces.get(action.workspaceId);
        if (
            workspace === undefined ||
            state.activeWorkspaceId !== action.workspaceId ||
            workspace.workspaceRevision !== action.workspaceRevision ||
            workspace.activeEntryId !== action.entryId ||
            workspace.pendingDocumentRequest?.entryId !== action.entryId ||
            workspace.pendingDocumentRequest?.path !== action.path ||
            workspace.pendingDocumentRequest.requestGeneration !== action.requestGeneration
        ) {
            return ignore_stale_response(state);
        }
        const existing = workspace.documents.get(action.entryId);
        if (existing !== undefined && existing.state !== "clean") {
            return ignore_stale_response(state);
        }
        const document: PearWorkspaceDocumentSession = {
            entryId: action.entryId,
            path: action.path,
            requestGeneration: action.requestGeneration,
            baseVersion: action.baseVersion,
            maxBytes: action.maxBytes,
            savedText: action.text,
            currentText: action.text,
            editorMode: existing?.editorMode ?? workspace.markdownModePreference,
            richModeEligibility: "unchecked",
            serializedRevision: 0,
            serialization: {state: "idle", requestedRevision: undefined, error: undefined},
            state: "clean",
            editRevision: 0,
            acknowledgedRevision: 0,
            saveInFlight: undefined,
            conflict: undefined,
            error: undefined,
            authority: "current",
            sourceView: existing?.sourceView ?? {
                selection: {anchor: 0, head: 0},
                scrollTop: 0,
            },
            richView: existing?.richView ?? {selection: undefined, scrollTop: 0},
        };
        return with_document(
            state,
            {
                ...workspace,
                workspaceRevision: action.workspaceRevision,
                pendingDocumentRequest: undefined,
            },
            document,
        );
    }

    const identity: PearWorkspaceDocumentIdentity = action;
    const active_match = matching_active_document(state, identity);
    const any_match = matching_document(state, identity);

    if (
        action.type === "source_changed" ||
        action.type === "rich_edited" ||
        action.type === "rich_recovered" ||
        action.type === "set_editor_mode" ||
        action.type === "set_rich_eligibility" ||
        action.type === "source_view_changed" ||
        action.type === "rich_view_changed"
    ) {
        if (active_match === undefined) {
            return ignore_stale_response(state);
        }
    } else if (any_match === undefined) {
        return ignore_stale_response(state);
    }

    const {workspace, document} = active_match ?? any_match!;

    if (action.type === "rebind_document") {
        if (
            action.workspaceRevision !== workspace.workspaceRevision ||
            document.saveInFlight !== undefined
        ) {
            return ignore_stale_response(state);
        }
        const remote_changed = action.remoteText !== document.savedText;
        const has_local_changes = document.currentText !== document.savedText;
        const next_document: PearWorkspaceDocumentSession = {
            ...document,
            entryId: action.newEntryId,
            path: action.newPath,
            baseVersion: action.newVersion,
            authority: "current",
            ...(remote_changed && has_local_changes
                ? {
                      conflict: {
                          remoteText: action.remoteText,
                          remoteVersion: action.newVersion,
                          remoteEntryId: action.newEntryId,
                          remoteWorkspaceRevision: action.workspaceRevision,
                      },
                      state: "conflict" as const,
                      error: undefined,
                  }
                : remote_changed
                  ? {
                        savedText: action.remoteText,
                        currentText: action.remoteText,
                        serializedRevision: document.editRevision,
                        acknowledgedRevision: document.editRevision,
                        serialization: {
                            state: "idle" as const,
                            requestedRevision: undefined,
                            error: undefined,
                        },
                        conflict: undefined,
                        state: "clean" as const,
                        error: undefined,
                    }
                  : {conflict: undefined}),
        };
        return with_document(
            state,
            {
                ...workspace,
                activeEntryId:
                    workspace.activeEntryId === document.entryId
                        ? action.newEntryId
                        : workspace.activeEntryId,
            },
            remote_changed && has_local_changes
                ? next_document
                : acknowledge_noop_edit(next_document),
            document.entryId,
        );
    }

    if (action.type === "detach_document") {
        if (document.saveInFlight !== undefined) {
            return ignore_stale_response(state);
        }
        return with_document(state, workspace, {
            ...document,
            authority: "detached",
            error: undefined,
        });
    }

    if (action.type === "discard_changes") {
        if (document.saveInFlight !== undefined || document.conflict !== undefined) {
            return state;
        }
        return with_document(state, workspace, {
            ...document,
            currentText: document.savedText,
            editRevision: document.editRevision + 1,
            serializedRevision: document.editRevision + 1,
            acknowledgedRevision: document.editRevision + 1,
            serialization: {state: "idle", requestedRevision: undefined, error: undefined},
            state: "clean",
            error: undefined,
        });
    }

    if (action.type === "set_editor_mode") {
        if (
            (action.mode === "rich" && document.richModeEligibility !== "supported") ||
            (document.editorMode === "rich" &&
                action.mode === "source" &&
                document.serializedRevision !== document.editRevision)
        ) {
            return state;
        }
        return with_document(
            state,
            {...workspace, markdownModePreference: action.mode},
            {...document, editorMode: action.mode},
        );
    }

    if (action.type === "set_rich_eligibility") {
        if (action.editRevision !== document.editRevision) {
            return ignore_stale_response(state);
        }
        return with_document(state, workspace, {
            ...document,
            richModeEligibility: action.eligibility,
        });
    }

    if (action.type === "source_changed") {
        if (document.editorMode !== "source") {
            return ignore_stale_response(state);
        }
        const edit_revision = document.editRevision + 1;
        const next_document: PearWorkspaceDocumentSession = {
            ...document,
            editRevision: edit_revision,
            serialization: {
                state: "scheduled",
                requestedRevision: edit_revision,
                error: undefined,
            },
            richModeEligibility: "unchecked",
            error: undefined,
        };
        return with_document(state, workspace, {
            ...next_document,
            state: document_state_after_change(next_document),
        });
    }

    if (action.type === "source_flushed") {
        if (
            document.editorMode !== "source" ||
            action.editRevision !== document.editRevision ||
            document.serialization.requestedRevision !== action.editRevision
        ) {
            return ignore_stale_response(state);
        }
        return with_document(
            state,
            workspace,
            acknowledge_noop_edit({
                ...document,
                currentText: action.text,
                serializedRevision: action.editRevision,
                serialization: {state: "idle", requestedRevision: undefined, error: undefined},
                error: undefined,
            }),
        );
    }

    if (action.type === "rich_edited") {
        if (document.editorMode !== "rich") {
            return ignore_stale_response(state);
        }
        const edit_revision = document.editRevision + 1;
        const next_document: PearWorkspaceDocumentSession = {
            ...document,
            editRevision: edit_revision,
            serialization: {
                state: "scheduled",
                requestedRevision: edit_revision,
                error: undefined,
            },
            error: undefined,
        };
        return with_document(state, workspace, {
            ...next_document,
            state: document_state_after_change(next_document),
        });
    }

    if (action.type === "rich_recovered") {
        if (document.editorMode !== "rich" || action.editRevision < document.editRevision) {
            return ignore_stale_response(state);
        }
        return with_document(
            state,
            workspace,
            acknowledge_noop_edit({
                ...document,
                currentText: action.text,
                editRevision: action.editRevision,
                serializedRevision: action.editRevision,
                serialization: {state: "idle", requestedRevision: undefined, error: undefined},
                error: undefined,
            }),
        );
    }

    if (action.type === "serialization_requested") {
        if (document.serializedRevision === document.editRevision) {
            return state;
        }
        const next_document: PearWorkspaceDocumentSession = {
            ...document,
            serialization: {
                state: "scheduled",
                requestedRevision: document.editRevision,
                error: undefined,
            },
            error: undefined,
        };
        return with_document(state, workspace, {
            ...next_document,
            state: document_state_after_change(next_document),
        });
    }

    if (action.type === "serialization_started") {
        if (
            action.editRevision !== document.editRevision ||
            document.serialization.requestedRevision !== action.editRevision
        ) {
            return ignore_stale_response(state);
        }
        return with_document(state, workspace, {
            ...document,
            serialization: {
                state: "serializing",
                requestedRevision: action.editRevision,
                error: undefined,
            },
        });
    }

    if (action.type === "serialization_succeeded") {
        if (
            action.editRevision !== document.editRevision ||
            document.serialization.requestedRevision !== action.editRevision
        ) {
            const next_document = {
                ...document,
                serialization: {
                    state: "scheduled" as const,
                    requestedRevision: document.editRevision,
                    error: undefined,
                },
            };
            return with_document(ignore_stale_response(state), workspace, next_document);
        }
        return with_document(
            state,
            workspace,
            acknowledge_noop_edit({
                ...document,
                currentText: action.text,
                serializedRevision: action.editRevision,
                serialization: {state: "idle", requestedRevision: undefined, error: undefined},
                error: undefined,
            }),
        );
    }

    if (action.type === "serialization_failed") {
        if (
            action.editRevision !== document.editRevision ||
            document.serialization.requestedRevision !== action.editRevision
        ) {
            return ignore_stale_response(state);
        }
        return with_document(state, workspace, {
            ...document,
            serialization: {
                state: "error",
                requestedRevision: action.editRevision,
                error: action.error,
            },
            state: "error",
            error: action.error,
        });
    }

    if (action.type === "save_started") {
        if (document.authority !== "current") {
            return with_document(state, workspace, {
                ...document,
                state: "error",
                error:
                    document.authority === "detached"
                        ? "This file was deleted outside the editor. Your local buffer is still available."
                        : "Workspace changed; validating this file before saving.",
            });
        }
        if (new TextEncoder().encode(document.currentText).length > document.maxBytes) {
            return with_document(state, workspace, {
                ...document,
                state: "error",
                error: "too_large",
            });
        }
        if (
            document.saveInFlight !== undefined ||
            document.conflict !== undefined ||
            document.serialization.state !== "idle" ||
            document.serializedRevision !== document.editRevision ||
            document.currentText === document.savedText
        ) {
            return state;
        }
        const snapshot: PearWorkspaceSaveSnapshot = {
            workspaceId: workspace.workspaceId,
            workspaceRevision: workspace.workspaceRevision,
            entryId: document.entryId,
            path: document.path,
            requestGeneration: document.requestGeneration,
            editRevision: document.editRevision,
            baseVersion: document.baseVersion,
            text: document.currentText,
        };
        return with_document(state, workspace, {
            ...document,
            saveInFlight: snapshot,
            state: "saving",
            error: undefined,
        });
    }

    if (action.type === "save_succeeded") {
        if (!save_callback_matches(document, action)) {
            return ignore_stale_response(state);
        }
        const snapshot = document.saveInFlight!;
        const next_document = {
            ...document,
            entryId: action.newEntryId,
            baseVersion: action.newVersion,
            savedText: snapshot.text,
            acknowledgedRevision: action.editRevision,
            saveInFlight: undefined,
            error: document.serialization.state === "error" ? document.error : undefined,
            authority: "current" as const,
        };
        const documents = new Map(
            [...workspace.documents].map(([entry_id, cached]) => [
                entry_id,
                entry_id === document.entryId
                    ? cached
                    : cached.authority === "detached"
                      ? cached
                      : ({...cached, authority: "stale"} satisfies PearWorkspaceDocumentSession),
            ]),
        );
        return with_document(
            state,
            {
                ...workspace,
                workspaceRevision: action.workspaceRevision,
                documents,
                activeEntryId:
                    workspace.activeEntryId === document.entryId
                        ? action.newEntryId
                        : workspace.activeEntryId,
            },
            acknowledge_noop_edit(next_document),
            document.entryId,
        );
    }

    if (action.type === "save_failed") {
        if (!save_callback_matches(document, action)) {
            return ignore_stale_response(state);
        }
        return with_document(state, workspace, {
            ...document,
            saveInFlight: undefined,
            state: "error",
            error: action.error,
        });
    }

    if (action.type === "save_conflicted") {
        if (!save_callback_matches(document, action)) {
            return ignore_stale_response(state);
        }
        return with_document(state, workspace, {
            ...document,
            saveInFlight: undefined,
            conflict: {
                remoteText: action.remoteText,
                remoteVersion: action.remoteVersion,
                remoteEntryId: action.remoteEntryId,
                remoteWorkspaceRevision: action.remoteWorkspaceRevision,
            },
            state: "conflict",
            error: undefined,
        });
    }

    if (action.type === "conflict_reload") {
        if (document.conflict === undefined) {
            return state;
        }
        const conflict = document.conflict;
        const max_offset = conflict.remoteText.length;
        const edit_revision = document.editRevision;
        return with_document(
            state,
            {
                ...workspace,
                workspaceRevision: conflict.remoteWorkspaceRevision,
                activeEntryId:
                    workspace.activeEntryId === document.entryId
                        ? conflict.remoteEntryId
                        : workspace.activeEntryId,
            },
            {
                ...document,
                entryId: conflict.remoteEntryId,
                baseVersion: conflict.remoteVersion,
                savedText: conflict.remoteText,
                currentText: conflict.remoteText,
                serializedRevision: edit_revision,
                serialization: {state: "idle", requestedRevision: undefined, error: undefined},
                acknowledgedRevision: edit_revision,
                conflict: undefined,
                state: "clean",
                error: undefined,
                sourceView: {
                    ...document.sourceView,
                    selection: {
                        anchor: Math.min(document.sourceView.selection.anchor, max_offset),
                        head: Math.min(document.sourceView.selection.head, max_offset),
                    },
                },
                richView: {...document.richView, selection: undefined},
            },
            document.entryId,
        );
    }

    if (action.type === "conflict_overwrite") {
        if (document.conflict === undefined) {
            return state;
        }
        const conflict = document.conflict;
        const next_document = {
            ...document,
            entryId: conflict.remoteEntryId,
            baseVersion: conflict.remoteVersion,
            savedText: conflict.remoteText,
            conflict: undefined,
            error: undefined,
        };
        return with_document(
            state,
            {
                ...workspace,
                workspaceRevision: conflict.remoteWorkspaceRevision,
                activeEntryId:
                    workspace.activeEntryId === document.entryId
                        ? conflict.remoteEntryId
                        : workspace.activeEntryId,
            },
            acknowledge_noop_edit(next_document),
            document.entryId,
        );
    }

    if (action.type === "source_view_changed") {
        return with_document(state, workspace, {
            ...document,
            sourceView: {selection: action.selection, scrollTop: action.scrollTop},
        });
    }

    if (action.type === "rich_view_changed") {
        return with_document(state, workspace, {
            ...document,
            richView: {selection: action.selection, scrollTop: action.scrollTop},
        });
    }

    return state;
}
