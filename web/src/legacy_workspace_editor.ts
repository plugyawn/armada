import type {ArmadaWorkspaceEntry, ArmadaWorkspaceFile} from "./legacy_workspace_api.ts";
import * as legacy_workspace_api from "./legacy_workspace_api.ts";
import type {
    ArmadaWorkspaceEditorController,
    ArmadaWorkspaceEditorDependencies,
    ArmadaWorkspaceEditorOptions,
    ArmadaWorkspaceEditorPreferences,
    ArmadaWorkspaceEditorSelectionContext,
    ArmadaWorkspaceEditorSnapshot,
    ArmadaWorkspaceEditorSurface,
    ArmadaWorkspaceMountedAdapter,
    ArmadaWorkspaceRichEditorModule,
    ArmadaWorkspaceScheduledWork,
    ArmadaWorkspaceSourceEditorModule,
} from "./legacy_workspace_editor_contract.ts";
import {
    begin_armada_workspace_metric,
    finish_armada_workspace_metric,
    increment_armada_workspace_counter,
    record_armada_workspace_error,
} from "./legacy_workspace_metrics.ts";
import {
    armada_workspace_api_error_message,
    armada_workspace_initial_preview,
} from "./legacy_workspace_preview.ts";
import {armada_workspace_source_language_for_path} from "./legacy_workspace_source_language.ts";
import type {
    ArmadaWorkspaceDocumentIdentity,
    ArmadaWorkspaceDocumentSession,
    ArmadaWorkspaceState,
} from "./legacy_workspace_store.ts";
import {
    create_armada_workspace_store,
    get_armada_workspace,
    get_armada_workspace_document,
    reduce_armada_workspace_store,
} from "./legacy_workspace_store.ts";

export type {
    ArmadaWorkspaceEditorController,
    ArmadaWorkspaceEditorDependencies,
    ArmadaWorkspaceEditorOptions,
    ArmadaWorkspaceEditorPreferences,
    ArmadaWorkspaceEditorSelectionContext,
    ArmadaWorkspaceEditorSaveEvent,
    ArmadaWorkspaceEditorSnapshot,
    ArmadaWorkspaceEditorSurface,
} from "./legacy_workspace_editor_contract.ts";

function default_source_flush(callback: () => void): ArmadaWorkspaceScheduledWork {
    const handle = setTimeout(callback, 650);
    return {
        cancel() {
            clearTimeout(handle);
        },
    };
}

function default_autosave(callback: () => void): ArmadaWorkspaceScheduledWork {
    const handle = setTimeout(callback, 650);
    return {
        cancel() {
            clearTimeout(handle);
        },
    };
}

const default_dependencies: ArmadaWorkspaceEditorDependencies = {
    getFile: legacy_workspace_api.get_armada_workspace_file,
    putFile: legacy_workspace_api.put_armada_workspace_file,
    rawUrl: legacy_workspace_api.armada_workspace_raw_url,
    async loadSourceEditor() {
        return import("./legacy_workspace_source_editor.ts");
    },
    async loadRichEditor() {
        return import("./legacy_workspace_markdown_editor.ts");
    },
    scheduleSourceFlush: default_source_flush,
    scheduleAutosave: default_autosave,
};

function same_identity(
    left: ArmadaWorkspaceDocumentIdentity | undefined,
    right: ArmadaWorkspaceDocumentIdentity,
): boolean {
    return (
        left?.workspaceId === right.workspaceId &&
        left.entryId === right.entryId &&
        left.path === right.path &&
        left.requestGeneration === right.requestGeneration
    );
}

function is_valid_rebound_entry_id(entry_id: string, previous_entry_id: string): boolean {
    return entry_id.trim().length > 0 && entry_id !== previous_entry_id;
}

function line_at_offset(text: string, offset: number): number {
    let line = 0;
    const limit = Math.max(0, Math.min(Math.trunc(offset), text.length));
    for (const match of text.matchAll(/\n/g)) {
        if (match.index >= limit) {
            break;
        }
        line += 1;
    }
    return line;
}

function offset_at_line(text: string, line: number): number {
    const target_line = Math.max(0, Math.trunc(line));
    if (target_line === 0) {
        return 0;
    }
    let current_line = 0;
    for (const match of text.matchAll(/\n/g)) {
        current_line += 1;
        if (current_line === target_line) {
            return match.index + 1;
        }
    }
    return text.length;
}

export function create_armada_workspace_editor(
    options: ArmadaWorkspaceEditorOptions,
): ArmadaWorkspaceEditorController {
    const dependencies = {...default_dependencies, ...options.dependencies};
    const request_guard = legacy_workspace_api.create_armada_workspace_request_guard();
    let store = create_armada_workspace_store();
    let surface: ArmadaWorkspaceEditorSurface = {kind: "empty"};
    let mounted: ArmadaWorkspaceMountedAdapter | undefined;
    let active_file: ArmadaWorkspaceFile | undefined;
    let active_entry: ArmadaWorkspaceEntry | undefined;
    let max_raw_bytes: number | null = 0;
    let max_text_bytes = 0;
    let workspace_writable = false;
    let destroyed = false;
    let mount_generation = 0;
    let open_generation = 0;
    let rich_mode_loading = false;
    let rich_mode_issue: ArmadaWorkspaceEditorSnapshot["richModeIssue"];
    let notice: string | undefined;
    let source_flush: ArmadaWorkspaceScheduledWork | undefined;
    const autosaves = new Map<string, ArmadaWorkspaceScheduledWork>();
    const reconciliation_requests = new Set<{abort: () => void}>();
    const file_metadata = new Map<string, ArmadaWorkspaceFile>();
    const restored_views = new Map<string, ArmadaWorkspaceEditorPreferences["documents"][string]>();

    const dispatch = (action: Parameters<typeof reduce_armada_workspace_store>[1]): void => {
        store = reduce_armada_workspace_store(store, action);
    };

    const current_workspace = (): ArmadaWorkspaceState | undefined =>
        store.activeWorkspaceId === undefined
            ? undefined
            : get_armada_workspace(store, store.activeWorkspaceId);

    const active_document = (): ArmadaWorkspaceDocumentSession | undefined => {
        const workspace = current_workspace();
        return workspace?.activeEntryId === undefined
            ? undefined
            : workspace.documents.get(workspace.activeEntryId);
    };

    const active_identity = (): ArmadaWorkspaceDocumentIdentity | undefined => {
        const document = active_document();
        if (document === undefined || store.activeWorkspaceId === undefined) {
            return undefined;
        }
        return {
            workspaceId: store.activeWorkspaceId,
            entryId: document.entryId,
            path: document.path,
            requestGeneration: document.requestGeneration,
        };
    };

    const identity_for_path = (path: string): ArmadaWorkspaceDocumentIdentity | undefined => {
        const workspace = current_workspace();
        const document = [...(workspace?.documents.values() ?? [])].find(
            (candidate) => candidate.path === path,
        );
        if (workspace === undefined || document === undefined) {
            return undefined;
        }
        return {
            workspaceId: workspace.workspaceId,
            entryId: document.entryId,
            path: document.path,
            requestGeneration: document.requestGeneration,
        };
    };

    const snapshot = (): ArmadaWorkspaceEditorSnapshot => ({
        surface,
        store,
        activeDocument: active_document(),
        activeIdentity: active_identity(),
        mountedAdapter: mounted?.kind,
        richModeLoading: rich_mode_loading,
        richModeIssue: rich_mode_issue,
        notice,
    });

    const emit = (): void => {
        if (!destroyed) {
            options.onUpdate(snapshot());
        }
    };

    const work_key = (identity: ArmadaWorkspaceDocumentIdentity): string =>
        JSON.stringify([identity.workspaceId, identity.path, identity.requestGeneration]);

    const clear_source_flush = (): void => {
        source_flush?.cancel();
        source_flush = undefined;
    };

    const clear_autosave = (identity: ArmadaWorkspaceDocumentIdentity): void => {
        const key = work_key(identity);
        autosaves.get(key)?.cancel();
        autosaves.delete(key);
    };

    let start_save: (identity: ArmadaWorkspaceDocumentIdentity) => Promise<void>;

    const schedule_autosave = (identity: ArmadaWorkspaceDocumentIdentity): void => {
        if (!workspace_writable) {
            return;
        }
        const document = get_armada_workspace_document(store, identity);
        if (
            document?.state !== "dirty" ||
            document.serialization.state !== "idle" ||
            document.conflict !== undefined
        ) {
            return;
        }
        clear_autosave(identity);
        autosaves.set(
            work_key(identity),
            dependencies.scheduleAutosave(() => {
                autosaves.delete(work_key(identity));
                void start_save(identity);
            }),
        );
    };

    const flush_source_now = (identity: ArmadaWorkspaceDocumentIdentity): void => {
        clear_source_flush();
        if (mounted?.kind !== "source" || !same_identity(mounted.identity, identity)) {
            return;
        }
        const document = get_armada_workspace_document(store, identity);
        if (
            document?.serialization.state !== "scheduled" ||
            document.serialization.requestedRevision !== document.editRevision
        ) {
            return;
        }
        const view = mounted.editor.getViewState();
        dispatch({type: "source_view_changed", ...identity, ...view});
        dispatch({
            type: "source_flushed",
            ...identity,
            editRevision: document.editRevision,
            text: mounted.editor.getText(),
        });
        emit();
        schedule_autosave(identity);
    };

    const schedule_source_flush = (identity: ArmadaWorkspaceDocumentIdentity): void => {
        clear_source_flush();
        source_flush = dependencies.scheduleSourceFlush(() => {
            source_flush = undefined;
            flush_source_now(identity);
        });
    };

    const update_rich_serialization = (
        identity: ArmadaWorkspaceDocumentIdentity,
        result:
            | {status: "serialized"; editRevision: number; markdown: string}
            | {status: "error"; editRevision: number; message: string},
    ): void => {
        const document = get_armada_workspace_document(store, identity);
        if (document?.editRevision !== result.editRevision) {
            return;
        }
        if (document.serialization.state === "scheduled") {
            dispatch({
                type: "serialization_started",
                ...identity,
                editRevision: result.editRevision,
            });
        }
        if (result.status === "serialized") {
            dispatch({
                type: "serialization_succeeded",
                ...identity,
                editRevision: result.editRevision,
                text: result.markdown,
            });
            schedule_autosave(identity);
        } else {
            dispatch({
                type: "serialization_failed",
                ...identity,
                editRevision: result.editRevision,
                error: result.message,
            });
        }
        emit();
    };

    const destroy_mounted = async (): Promise<void> => {
        mount_generation += 1;
        const generation = mount_generation;
        rich_mode_loading = false;
        const previous = mounted;
        mounted = undefined;
        clear_source_flush();
        if (previous?.kind === "source") {
            previous.editor.destroy();
        } else if (previous?.kind === "rich") {
            await previous.editor.destroy();
        }
        if (generation === mount_generation) {
            options.root.replaceChildren();
        }
    };

    const mount_source = async (
        identity: ArmadaWorkspaceDocumentIdentity,
        file: ArmadaWorkspaceFile,
    ): Promise<void> => {
        await destroy_mounted();
        if (destroyed) {
            return;
        }
        const document = get_armada_workspace_document(store, identity);
        if (document === undefined) {
            return;
        }
        mount_generation += 1;
        const generation = mount_generation;
        let module: ArmadaWorkspaceSourceEditorModule;
        try {
            module = await dependencies.loadSourceEditor();
        } catch {
            if (generation === mount_generation) {
                notice = "Source editor failed to load.";
                surface = {
                    kind: "error",
                    entry: active_entry,
                    message: "Source editor failed to load. Reopen the file to retry.",
                };
                emit();
            }
            return;
        }
        if (destroyed || generation !== mount_generation) {
            return;
        }
        const editor = module.create_armada_workspace_source_editor({
            root: options.root,
            text: document.currentText,
            selection: document.sourceView.selection,
            scrollTop: document.sourceView.scrollTop,
            readOnly: !workspace_writable || !file.editable,
            language: armada_workspace_source_language_for_path(document.path),
            ariaLabel: `${document.path} source editor`,
            onDirty(event) {
                if (
                    destroyed ||
                    generation !== mount_generation ||
                    mounted?.kind !== "source" ||
                    !same_identity(mounted.identity, identity)
                ) {
                    return;
                }
                if (!workspace_writable) {
                    return;
                }
                begin_armada_workspace_metric("workspace_dirty_feedback");
                if (rich_mode_issue !== undefined) {
                    if (notice === rich_mode_issue.message) {
                        notice = undefined;
                    }
                    rich_mode_issue = undefined;
                }
                dispatch({type: "source_changed", ...identity});
                const view = mounted.editor.getViewState();
                dispatch({
                    type: "source_view_changed",
                    ...identity,
                    selection: event.selection,
                    scrollTop: view.scrollTop,
                });
                emit();
                finish_armada_workspace_metric("workspace_dirty_feedback");
                schedule_source_flush(identity);
            },
            onLanguageError(event) {
                notice = `${event.language} syntax highlighting failed to load; editing remains plain text.`;
                emit();
            },
        });
        mounted = {kind: "source", identity, editor};
        emit();
    };

    const flush_rich = async (identity: ArmadaWorkspaceDocumentIdentity): Promise<boolean> => {
        if (mounted?.kind !== "rich" || !same_identity(mounted.identity, identity)) {
            return true;
        }
        const document = get_armada_workspace_document(store, identity);
        if (document === undefined || document.serializedRevision === document.editRevision) {
            return true;
        }
        if (document.serialization.state === "scheduled") {
            dispatch({
                type: "serialization_started",
                ...identity,
                editRevision: document.editRevision,
            });
            emit();
        }
        const result = await mounted.editor.flush(document.editRevision);
        return result.status === "serialized";
    };

    const mount_rich = async (
        identity: ArmadaWorkspaceDocumentIdentity,
        file: ArmadaWorkspaceFile,
    ): Promise<boolean> => {
        const document = get_armada_workspace_document(store, identity);
        if (document === undefined || !file.rich_mode_eligible) {
            return false;
        }
        let source_cursor_line: number | undefined;
        if (mounted?.kind === "source" && same_identity(mounted.identity, identity)) {
            flush_source_now(identity);
            const source_view = mounted.editor.getViewState();
            const latest_source = get_armada_workspace_document(store, identity);
            source_cursor_line = line_at_offset(
                latest_source?.currentText ?? document.currentText,
                source_view.selection.head,
            );
            dispatch({type: "source_view_changed", ...identity, ...source_view});
        }
        await destroy_mounted();
        if (destroyed) {
            return false;
        }
        rich_mode_loading = true;
        rich_mode_issue = undefined;
        emit();
        mount_generation += 1;
        const generation = mount_generation;
        let module: ArmadaWorkspaceRichEditorModule;
        try {
            module = await dependencies.loadRichEditor();
        } catch {
            rich_mode_loading = false;
            const message = "Rich editor failed to load. Source remains available.";
            rich_mode_issue = {kind: "load_error", message, retryable: true};
            notice = message;
            surface = {kind: "text", entry: active_entry!, file};
            emit();
            await mount_source(identity, file);
            return false;
        }
        if (destroyed || generation !== mount_generation) {
            return false;
        }
        const latest = get_armada_workspace_document(store, identity);
        if (latest === undefined) {
            rich_mode_loading = false;
            return false;
        }
        const rich_options = {
            root: options.root,
            markdown: latest.currentText,
            editRevision: latest.editRevision,
            readOnly: !workspace_writable || !file.editable,
            scrollTop: latest.richView.scrollTop,
            onDirty(event: {origin: "user"; editRevision: number}) {
                if (
                    destroyed ||
                    generation !== mount_generation ||
                    mounted?.kind !== "rich" ||
                    !same_identity(mounted.identity, identity)
                ) {
                    return;
                }
                if (!workspace_writable) {
                    return;
                }
                begin_armada_workspace_metric("workspace_dirty_feedback");
                dispatch({type: "rich_edited", ...identity});
                const changed = get_armada_workspace_document(store, identity);
                if (changed?.editRevision !== event.editRevision) {
                    surface = {
                        kind: "error",
                        entry: active_entry,
                        message:
                            "Rich editor revision drifted; switch to Source before continuing.",
                        action: "source",
                    };
                    emit();
                    finish_armada_workspace_metric("workspace_dirty_feedback");
                    return;
                }
                emit();
                finish_armada_workspace_metric("workspace_dirty_feedback");
            },
            onSerialized(result: {status: "serialized"; editRevision: number; markdown: string}) {
                update_rich_serialization(identity, result);
            },
            onSerializationError(result: {status: "error"; editRevision: number; message: string}) {
                update_rich_serialization(identity, result);
            },
            ...(latest.richView.selection === undefined
                ? {}
                : {selection: latest.richView.selection}),
        };
        const result = await module.create_armada_workspace_markdown_editor(rich_options);
        if (destroyed || generation !== mount_generation) {
            if (result.ok) {
                await result.editor.destroy();
            }
            return false;
        }
        rich_mode_loading = false;
        if (!result.ok) {
            notice = result.eligibility.reason;
            const retryable = ["parse_error", "style_error"].includes(result.eligibility.code);
            rich_mode_issue = {
                kind: retryable ? "load_error" : "source_only",
                message: result.eligibility.reason,
                retryable,
            };
            dispatch({
                type: "set_rich_eligibility",
                ...identity,
                editRevision: latest.editRevision,
                eligibility: "source_only",
            });
            surface = {kind: "text", entry: active_entry!, file};
            emit();
            await mount_source(identity, file);
            return false;
        }
        dispatch({
            type: "set_rich_eligibility",
            ...identity,
            editRevision: latest.editRevision,
            eligibility: "supported",
        });
        dispatch({type: "set_editor_mode", ...identity, mode: "rich"});
        rich_mode_issue = undefined;
        notice = workspace_writable ? undefined : "Read-only workspace";
        if (source_cursor_line !== undefined) {
            result.editor.setCursorLine(source_cursor_line);
            dispatch({type: "rich_view_changed", ...identity, ...result.editor.getViewState()});
        }
        mounted = {kind: "rich", identity, editor: result.editor};
        emit();
        return true;
    };

    const hydrate_after_reload = async (
        previous_identity: ArmadaWorkspaceDocumentIdentity,
    ): Promise<void> => {
        const document = active_document();
        const identity = active_identity();
        if (document === undefined || identity === undefined || active_file === undefined) {
            return;
        }
        active_file = {
            ...active_file,
            entry_id: identity.entryId,
            workspace_revision: current_workspace()!.workspaceRevision,
            version: document.baseVersion,
            content: document.currentText,
            size: new TextEncoder().encode(document.currentText).length,
        };
        file_metadata.delete(previous_identity.entryId);
        file_metadata.set(identity.entryId, active_file);
        active_entry = {
            ...(active_entry ?? {
                entry_id: identity.entryId,
                path: identity.path,
                name: identity.path.split("/").at(-1) ?? identity.path,
                kind: "file" as const,
                status: "clean",
                has_children: false,
                size: active_file.size,
            }),
            entry_id: identity.entryId,
            size: active_file.size,
        };
        surface = {kind: "text", entry: active_entry, file: active_file};
        if (mounted?.kind === "source") {
            mounted.editor.hydrate({
                text: document.currentText,
                selection: document.sourceView.selection,
                scrollTop: document.sourceView.scrollTop,
            });
            mounted.identity = identity;
        } else if (mounted?.kind === "rich") {
            const hydration = {
                markdown: document.currentText,
                editRevision: document.editRevision,
                scrollTop: document.richView.scrollTop,
                ...(document.richView.selection === undefined
                    ? {}
                    : {selection: document.richView.selection}),
            };
            const eligibility = mounted.editor.hydrate(hydration);
            if (!eligibility.eligible) {
                dispatch({type: "set_editor_mode", ...identity, mode: "source"});
                await mount_source(identity, active_file);
            } else {
                mounted.identity = identity;
            }
        }
        emit();
    };

    start_save = async (identity): Promise<void> => {
        if (destroyed || !workspace_writable) {
            return;
        }
        if (mounted?.kind === "source" && same_identity(mounted.identity, identity)) {
            flush_source_now(identity);
        } else if (!(await flush_rich(identity))) {
            return;
        }
        clear_autosave(identity);
        const before = get_armada_workspace_document(store, identity);
        dispatch({type: "save_started", ...identity});
        const document = get_armada_workspace_document(store, identity);
        const save = document?.saveInFlight;
        if (before?.saveInFlight !== undefined || save === undefined) {
            emit();
            return;
        }
        const callback_owns_save = (): boolean => {
            const current = get_armada_workspace_document(store, identity);
            return (
                identity.workspaceId === save.workspaceId &&
                identity.entryId === save.entryId &&
                identity.path === save.path &&
                identity.requestGeneration === save.requestGeneration &&
                current?.entryId === save.entryId &&
                current.path === save.path &&
                current.requestGeneration === save.requestGeneration &&
                current.saveInFlight === save
            );
        };
        const reject_invalid_receipt = (kind: "conflict" | "save"): void => {
            dispatch({
                type: "save_failed",
                ...identity,
                editRevision: save.editRevision,
                error: `Workspace returned an invalid ${kind} receipt. Refresh before retrying.`,
            });
            emit();
        };
        emit();
        begin_armada_workspace_metric("workspace_save_ack");
        dependencies.putFile(
            save.workspaceId,
            {
                entryId: save.entryId,
                expectedPath: save.path,
                content: save.text,
                baseVersion: save.baseVersion,
                workspaceRevision: save.workspaceRevision,
                editRevision: save.editRevision,
            },
            {
                success(file) {
                    if (!callback_owns_save()) {
                        return;
                    }
                    finish_armada_workspace_metric("workspace_save_ack");
                    if (
                        file.workspace_uuid !== save.workspaceId ||
                        file.path !== save.path ||
                        file.content !== save.text ||
                        file.edit_revision !== save.editRevision ||
                        file.workspace_revision <= save.workspaceRevision ||
                        !is_valid_rebound_entry_id(file.entry_id, save.entryId)
                    ) {
                        reject_invalid_receipt("save");
                        return;
                    }
                    const old_entry_id = save.entryId;
                    dispatch({
                        type: "save_succeeded",
                        ...identity,
                        editRevision: save.editRevision,
                        newEntryId: file.entry_id,
                        newVersion: file.version,
                        workspaceRevision: file.workspace_revision,
                    });
                    const saved_workspace = get_armada_workspace(store, save.workspaceId);
                    if (saved_workspace?.documents.has(file.entry_id) !== true) {
                        emit();
                        return;
                    }
                    const next_identity: ArmadaWorkspaceDocumentIdentity = {
                        ...identity,
                        entryId: file.entry_id,
                    };
                    if (mounted !== undefined && same_identity(mounted.identity, identity)) {
                        mounted.identity = next_identity;
                    }
                    file_metadata.delete(old_entry_id);
                    file_metadata.set(file.entry_id, file);
                    const workspace = get_armada_workspace(store, save.workspaceId);
                    const saved_is_active = workspace?.activeEntryId === file.entry_id;
                    for (const [key, work] of autosaves) {
                        if (key !== work_key(next_identity)) {
                            work.cancel();
                            autosaves.delete(key);
                        }
                    }
                    file_metadata.set(file.entry_id, file);
                    if (saved_is_active) {
                        active_file = file;
                        active_entry = {
                            ...(active_entry ?? {
                                entry_id: file.entry_id,
                                path: file.path,
                                name: file.path.split("/").at(-1) ?? file.path,
                                kind: "file" as const,
                                status: "modified",
                                has_children: false,
                                size: file.size,
                            }),
                            entry_id: file.entry_id,
                            size: file.size,
                        };
                        if (surface.kind === "text") {
                            surface = {kind: "text", entry: active_entry, file};
                        }
                    }
                    options.onSaved?.({
                        oldEntryId: old_entry_id,
                        newEntryId: file.entry_id,
                        path: file.path,
                        workspaceRevision: file.workspace_revision,
                    });
                    emit();
                    schedule_autosave(next_identity);
                },
                error(error) {
                    if (!callback_owns_save()) {
                        return;
                    }
                    finish_armada_workspace_metric("workspace_save_ack");
                    record_armada_workspace_error(
                        error.kind === "server" ? error.status : undefined,
                    );
                    if (
                        error.kind === "server" &&
                        error.error.error_code === "version_conflict" &&
                        error.error.remote_file !== undefined
                    ) {
                        const remote = error.error.remote_file;
                        if (
                            remote.workspace_uuid !== save.workspaceId ||
                            remote.path !== save.path ||
                            remote.workspace_revision <= save.workspaceRevision ||
                            error.error.workspace_revision !== remote.workspace_revision ||
                            remote.version === save.baseVersion ||
                            !is_valid_rebound_entry_id(remote.entry_id, save.entryId)
                        ) {
                            reject_invalid_receipt("conflict");
                            return;
                        }
                        dispatch({
                            type: "save_conflicted",
                            ...identity,
                            editRevision: save.editRevision,
                            remoteText: remote.content,
                            remoteVersion: remote.version,
                            remoteEntryId: remote.entry_id,
                            remoteWorkspaceRevision: remote.workspace_revision,
                        });
                    } else {
                        dispatch({
                            type: "save_failed",
                            ...identity,
                            editRevision: save.editRevision,
                            error: armada_workspace_api_error_message(error),
                        });
                    }
                    emit();
                },
            },
        );
    };

    const open = async (
        entry: ArmadaWorkspaceEntry,
        preferred_mode?: "rich" | "source",
    ): Promise<void> => {
        if (destroyed || entry.kind !== "file" || store.activeWorkspaceId === undefined) {
            return;
        }
        open_generation += 1;
        const generation = open_generation;
        const previous_identity = active_identity();
        if (previous_identity !== undefined) {
            if (mounted?.kind === "source") {
                flush_source_now(previous_identity);
                if (mounted?.kind === "source") {
                    dispatch({
                        type: "source_view_changed",
                        ...previous_identity,
                        ...mounted.editor.getViewState(),
                    });
                }
            } else {
                await flush_rich(previous_identity);
                if (generation !== open_generation) {
                    return;
                }
                if (mounted?.kind === "rich") {
                    dispatch({
                        type: "rich_view_changed",
                        ...previous_identity,
                        ...mounted.editor.getViewState(),
                    });
                }
            }
        }
        await destroy_mounted();
        if (generation !== open_generation || destroyed) {
            return;
        }
        request_guard.cancel("file");
        active_entry = entry;
        active_file = undefined;
        notice = workspace_writable ? undefined : "Read-only workspace";
        rich_mode_issue = undefined;
        const workspace = current_workspace()!;
        const cached = workspace.documents.get(entry.entry_id);
        dispatch({
            type: "open_document",
            workspaceId: workspace.workspaceId,
            entryId: entry.entry_id,
            path: entry.path,
        });

        const preview = armada_workspace_initial_preview(
            workspace.workspaceId,
            entry,
            {rawBytes: max_raw_bytes, textBytes: max_text_bytes},
            dependencies.rawUrl,
        );
        if (preview !== undefined) {
            surface = preview;
            emit();
            return;
        }
        if (cached !== undefined) {
            increment_armada_workspace_counter("workspace_file_cache_hit");
            const identity = {
                workspaceId: workspace.workspaceId,
                entryId: cached.entryId,
                path: cached.path,
                requestGeneration: cached.requestGeneration,
            };
            const file = file_metadata.get(cached.entryId) ?? {
                workspace_uuid: workspace.workspaceId,
                workspace_revision: workspace.workspaceRevision,
                entry_id: cached.entryId,
                path: cached.path,
                content: cached.currentText,
                version: cached.baseVersion,
                size: new TextEncoder().encode(cached.currentText).length,
                mime_type: "text/plain",
                language: armada_workspace_source_language_for_path(cached.path),
                editable: true,
                rich_mode_eligible: /\.(?:md|markdown)$/i.test(cached.path),
                max_bytes: cached.maxBytes,
            };
            active_file = file;
            surface = {kind: "text", entry, file};
            emit();
            if (
                file.rich_mode_eligible &&
                (preferred_mode === "rich" ||
                    (preferred_mode === undefined && cached.editorMode === "rich"))
            ) {
                await mount_rich(identity, file);
            } else {
                if (preferred_mode === "source" && cached.editorMode === "rich") {
                    dispatch({type: "set_editor_mode", ...identity, mode: "source"});
                }
                await mount_source(identity, file);
            }
            return;
        }

        increment_armada_workspace_counter("workspace_file_cache_miss");
        surface = {kind: "loading", entry};
        emit();
        const pending = current_workspace()!.pendingDocumentRequest!;
        const request_generation = request_guard.begin("file");
        const request = dependencies.getFile(workspace.workspaceId, entry.entry_id, {
            success(file) {
                if (!request_guard.settle("file", request_generation)) {
                    return;
                }
                const identity: ArmadaWorkspaceDocumentIdentity = {
                    workspaceId: workspace.workspaceId,
                    entryId: entry.entry_id,
                    path: entry.path,
                    requestGeneration: pending.requestGeneration,
                };
                if (
                    file.workspace_uuid !== identity.workspaceId ||
                    file.entry_id !== identity.entryId ||
                    file.path !== identity.path
                ) {
                    surface = {
                        kind: "error",
                        entry,
                        message: "Workspace returned a different file. Refresh and retry.",
                    };
                    emit();
                    return;
                }
                dispatch({
                    type: "document_loaded",
                    ...identity,
                    workspaceRevision: file.workspace_revision,
                    baseVersion: file.version,
                    maxBytes: file.max_bytes,
                    text: file.content,
                });
                const document = get_armada_workspace_document(store, identity);
                if (document === undefined) {
                    surface = {
                        kind: "error",
                        entry,
                        message: "Workspace changed while the file was loading. Refresh and retry.",
                    };
                    emit();
                    return;
                }
                const restored = restored_views.get(identity.path);
                if (restored !== undefined) {
                    dispatch({type: "source_view_changed", ...identity, ...restored.source});
                    dispatch({type: "rich_view_changed", ...identity, ...restored.rich});
                }
                if (!file.rich_mode_eligible) {
                    dispatch({
                        type: "set_rich_eligibility",
                        ...identity,
                        editRevision: document.editRevision,
                        eligibility: "source_only",
                    });
                }
                active_file = file;
                file_metadata.set(file.entry_id, file);
                surface = {kind: "text", entry, file};
                emit();
                if (
                    file.rich_mode_eligible &&
                    (preferred_mode === "rich" ||
                        (preferred_mode === undefined && document.editorMode === "rich"))
                ) {
                    void mount_rich(identity, file);
                } else {
                    if (preferred_mode === "source" && document.editorMode === "rich") {
                        dispatch({type: "set_editor_mode", ...identity, mode: "source"});
                    }
                    void mount_source(identity, file);
                }
            },
            error(error) {
                if (!request_guard.settle("file", request_generation)) {
                    return;
                }
                record_armada_workspace_error(error.kind === "server" ? error.status : undefined);
                if (error.kind === "server" && error.error.error_code === "unsupported_file") {
                    surface = {
                        kind: "binary",
                        entry,
                        message: "This file is not editable UTF-8 text.",
                    };
                } else if (error.kind === "server" && error.error.error_code === "file_too_large") {
                    surface = {
                        kind: "oversize",
                        entry,
                        maxBytes: max_text_bytes,
                        rawAvailable:
                            max_raw_bytes === null ||
                            (entry.size ?? Number.POSITIVE_INFINITY) <= max_raw_bytes,
                    };
                } else {
                    surface = {
                        kind: "error",
                        entry,
                        message: armada_workspace_api_error_message(error),
                    };
                }
                emit();
            },
        });
        request_guard.attach("file", request_generation, request);
    };

    const reconcile_entry = async (
        entry: ArmadaWorkspaceEntry,
        workspace_revision: number,
        previous_path: string = entry.path,
    ): Promise<"conflict" | "current" | "error" | "missing"> => {
        if (destroyed || entry.kind !== "file") {
            return "missing";
        }
        const old_identity = identity_for_path(previous_path);
        const workspace = current_workspace();
        if (old_identity === undefined || workspace?.workspaceRevision !== workspace_revision) {
            return "missing";
        }
        const current = get_armada_workspace_document(store, old_identity);
        if (
            current?.authority === "current" &&
            current.entryId === entry.entry_id &&
            current.path === entry.path
        ) {
            return current.state === "conflict" ? "conflict" : "current";
        }
        return new Promise((resolve) => {
            const request = dependencies.getFile(workspace.workspaceId, entry.entry_id, {
                success(file) {
                    if (request !== undefined) {
                        reconciliation_requests.delete(request);
                    }
                    const latest = current_workspace();
                    const still_owned = get_armada_workspace_document(store, old_identity);
                    if (
                        destroyed ||
                        latest?.workspaceRevision !== workspace_revision ||
                        still_owned === undefined ||
                        file.workspace_uuid !== workspace.workspaceId ||
                        file.workspace_revision !== workspace_revision ||
                        file.entry_id !== entry.entry_id ||
                        file.path !== entry.path
                    ) {
                        resolve("error");
                        return;
                    }
                    dispatch({
                        type: "rebind_document",
                        ...old_identity,
                        newEntryId: file.entry_id,
                        newPath: file.path,
                        newVersion: file.version,
                        remoteText: file.content,
                        workspaceRevision: file.workspace_revision,
                    });
                    const rebound_identity: ArmadaWorkspaceDocumentIdentity = {
                        ...old_identity,
                        entryId: file.entry_id,
                        path: file.path,
                    };
                    const rebound = get_armada_workspace_document(store, rebound_identity);
                    if (rebound === undefined) {
                        resolve("error");
                        return;
                    }
                    file_metadata.delete(old_identity.entryId);
                    file_metadata.set(file.entry_id, file);
                    if (mounted !== undefined && same_identity(mounted.identity, old_identity)) {
                        mounted.identity = rebound_identity;
                    }
                    if (active_identity()?.entryId === rebound_identity.entryId) {
                        active_file = file;
                        active_entry = {
                            ...entry,
                            size: file.size,
                        };
                        surface = {kind: "text", entry: active_entry, file};
                        if (rebound.state !== "conflict") {
                            void hydrate_after_reload(old_identity);
                        }
                    }
                    notice =
                        rebound.state === "conflict"
                            ? "This file changed outside the editor. Review both versions before saving."
                            : undefined;
                    emit();
                    if (rebound.authority === "current" && rebound.state === "dirty") {
                        schedule_autosave(rebound_identity);
                    }
                    resolve(rebound.state === "conflict" ? "conflict" : "current");
                },
                error(error) {
                    if (request !== undefined) {
                        reconciliation_requests.delete(request);
                    }
                    record_armada_workspace_error(
                        error.kind === "server" ? error.status : undefined,
                    );
                    resolve("error");
                },
            });
            if (request !== undefined) {
                reconciliation_requests.add(request);
            }
        });
    };

    return {
        activateWorkspace(workspace_id, workspace_revision, limits, preferences): void {
            if (destroyed) {
                return;
            }
            const previous_workspace = get_armada_workspace(store, workspace_id);
            max_raw_bytes = limits.rawBytes;
            max_text_bytes = limits.textBytes;
            workspace_writable = limits.writable;
            dispatch({
                type: "activate_workspace",
                workspaceId: workspace_id,
                workspaceRevision: workspace_revision,
            });
            if (
                previous_workspace !== undefined &&
                workspace_revision > previous_workspace.workspaceRevision
            ) {
                dispatch({
                    type: "advance_workspace_revision",
                    workspaceId: workspace_id,
                    workspaceRevision: workspace_revision,
                });
                request_guard.cancel("file");
                clear_source_flush();
                for (const work of autosaves.values()) {
                    work.cancel();
                }
                autosaves.clear();
                notice = "Workspace updated; validating open files…";
            }
            if (preferences !== undefined) {
                restored_views.clear();
                for (const [path, view] of Object.entries(preferences.documents)) {
                    restored_views.set(path, view);
                }
                dispatch({
                    type: "set_workspace_mode_preference",
                    workspaceId: workspace_id,
                    mode: preferences.mode,
                });
            }
            emit();
        },
        open,
        reconcileEntry: reconcile_entry,
        detachPath(path): void {
            const identity = identity_for_path(path);
            if (identity === undefined) {
                return;
            }
            clear_autosave(identity);
            dispatch({type: "detach_document", ...identity});
            if (active_identity()?.entryId === identity.entryId) {
                notice =
                    "This file was deleted outside the editor. Your local buffer is preserved.";
            }
            emit();
        },
        async closeActive(): Promise<boolean> {
            const identity = active_identity();
            const document = active_document();
            if (identity === undefined || document === undefined) {
                return true;
            }
            if (document.state !== "clean") {
                return false;
            }
            open_generation += 1;
            mount_generation += 1;
            request_guard.cancel("file");
            clear_source_flush();
            clear_autosave(identity);
            dispatch({type: "deactivate_document", ...identity});
            active_file = undefined;
            active_entry = undefined;
            rich_mode_loading = false;
            rich_mode_issue = undefined;
            notice = undefined;
            surface = {kind: "empty"};
            await destroy_mounted();
            emit();
            return true;
        },
        async switchMode(mode): Promise<boolean> {
            const identity = active_identity();
            const document = active_document();
            const recovering_rich_drift =
                mode === "source" && surface.kind === "error" && surface.action === "source";
            if (
                identity === undefined ||
                document === undefined ||
                active_file === undefined ||
                (surface.kind !== "text" && !recovering_rich_drift)
            ) {
                return false;
            }
            if (mode === document.editorMode) {
                return true;
            }
            if (mode === "rich") {
                return mount_rich(identity, active_file);
            }
            if (recovering_rich_drift && mounted?.kind === "rich") {
                const recovered = await mounted.editor.flush();
                if (recovered.status !== "serialized") {
                    notice =
                        "Rich content could not be recovered. Retry or copy it before leaving Rich mode.";
                    emit();
                    return false;
                }
                dispatch({
                    type: "rich_recovered",
                    ...identity,
                    editRevision: recovered.editRevision,
                    text: recovered.markdown,
                });
                surface = {kind: "text", entry: active_entry!, file: active_file};
            } else if (!(await flush_rich(identity))) {
                return false;
            }
            if (mounted?.kind === "rich") {
                const view = mounted.editor.getViewState();
                dispatch({type: "rich_view_changed", ...identity, ...view});
                const latest = get_armada_workspace_document(store, identity);
                if (latest !== undefined) {
                    const offset = offset_at_line(
                        latest.currentText,
                        mounted.editor.getCursorLine(),
                    );
                    dispatch({
                        type: "source_view_changed",
                        ...identity,
                        selection: {anchor: offset, head: offset},
                        scrollTop: latest.sourceView.scrollTop,
                    });
                }
            }
            dispatch({type: "set_editor_mode", ...identity, mode: "source"});
            await mount_source(identity, active_file);
            return true;
        },
        async save(): Promise<void> {
            const identity = active_identity();
            if (identity !== undefined) {
                await start_save(identity);
            }
        },
        async discardChanges(): Promise<void> {
            const identity = active_identity();
            if (identity === undefined) {
                return;
            }
            clear_autosave(identity);
            dispatch({type: "discard_changes", ...identity});
            await hydrate_after_reload(identity);
        },
        async resolveConflict(resolution): Promise<void> {
            const identity = active_identity();
            if (identity === undefined) {
                return;
            }
            const previous_workspace_revision = current_workspace()?.workspaceRevision;
            clear_autosave(identity);
            dispatch({
                type: resolution === "reload" ? "conflict_reload" : "conflict_overwrite",
                ...identity,
            });
            await hydrate_after_reload(identity);
            const next_identity = active_identity();
            const workspace = current_workspace();
            if (
                next_identity !== undefined &&
                workspace !== undefined &&
                (next_identity.entryId !== identity.entryId ||
                    workspace.workspaceRevision !== previous_workspace_revision)
            ) {
                options.onSaved?.({
                    oldEntryId: identity.entryId,
                    newEntryId: next_identity.entryId,
                    path: next_identity.path,
                    workspaceRevision: workspace.workspaceRevision,
                });
                emit();
            }
            if (resolution === "overwrite" && next_identity !== undefined) {
                schedule_autosave(next_identity);
            }
        },
        getSnapshot: snapshot,
        getSelectionContext(): ArmadaWorkspaceEditorSelectionContext | undefined {
            const identity = active_identity();
            if (identity === undefined) {
                return undefined;
            }
            if (mounted?.kind === "source" && same_identity(mounted.identity, identity)) {
                return {path: identity.path, ...mounted.editor.getSelectionContext()};
            }
            if (mounted?.kind === "rich" && same_identity(mounted.identity, identity)) {
                return {path: identity.path, ...mounted.editor.getSelectionContext()};
            }
            return {path: identity.path, selectedText: ""};
        },
        getPreferences(): ArmadaWorkspaceEditorPreferences {
            const documents: ArmadaWorkspaceEditorPreferences["documents"] = {};
            const workspace = current_workspace();
            if (workspace !== undefined) {
                for (const document of workspace.documents.values()) {
                    documents[document.path] = {
                        source: document.sourceView,
                        rich: {
                            scrollTop: document.richView.scrollTop,
                            ...(document.richView.selection === undefined
                                ? {}
                                : {selection: document.richView.selection}),
                        },
                    };
                }
            }
            for (const [path, view] of restored_views) {
                documents[path] ??= view;
            }
            return {
                mode: workspace?.markdownModePreference ?? "source",
                documents,
            };
        },
        async destroy(): Promise<void> {
            if (destroyed) {
                return;
            }
            destroyed = true;
            open_generation += 1;
            request_guard.cancelAll();
            for (const request of reconciliation_requests) {
                request.abort();
            }
            reconciliation_requests.clear();
            clear_source_flush();
            for (const work of autosaves.values()) {
                work.cancel();
            }
            autosaves.clear();
            await destroy_mounted();
        },
    };
}
