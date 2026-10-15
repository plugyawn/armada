import type {
    ArmadaWorkspaceEntry,
    ArmadaWorkspaceFile,
    armada_workspace_raw_url,
    get_armada_workspace_file,
    put_armada_workspace_file,
} from "./legacy_workspace_api.ts";
import type {
    ArmadaWorkspaceMarkdownEditor,
    ArmadaWorkspaceMarkdownEditorResult,
} from "./legacy_workspace_markdown_editor.ts";
import type {
    ArmadaWorkspaceSourceEditor,
    create_armada_workspace_source_editor,
} from "./legacy_workspace_source_editor.ts";
import type {
    ArmadaWorkspaceDocumentIdentity,
    ArmadaWorkspaceDocumentSession,
    ArmadaWorkspaceStoreState,
} from "./legacy_workspace_store.ts";

export type ArmadaWorkspaceEditorSurface =
    | {kind: "empty"}
    | {kind: "loading"; entry: ArmadaWorkspaceEntry}
    | {kind: "text"; entry: ArmadaWorkspaceEntry; file: ArmadaWorkspaceFile}
    | {kind: "raster"; entry: ArmadaWorkspaceEntry; rawUrl: string}
    | {kind: "binary"; entry: ArmadaWorkspaceEntry; message: string}
    | {
          kind: "oversize";
          entry: ArmadaWorkspaceEntry;
          maxBytes: number;
          rawAvailable: boolean;
      }
    | {
          kind: "error";
          entry: ArmadaWorkspaceEntry | undefined;
          message: string;
          action?: "retry" | "source";
      };

export type ArmadaWorkspaceEditorSnapshot = {
    surface: ArmadaWorkspaceEditorSurface;
    store: ArmadaWorkspaceStoreState;
    activeDocument: ArmadaWorkspaceDocumentSession | undefined;
    activeIdentity: ArmadaWorkspaceDocumentIdentity | undefined;
    mountedAdapter: "rich" | "source" | undefined;
    richModeLoading: boolean;
    richModeIssue:
        | {kind: "source_only" | "load_error"; message: string; retryable: boolean}
        | undefined;
    notice: string | undefined;
};

export type ArmadaWorkspaceEditorSaveEvent = {
    oldEntryId: string;
    newEntryId: string;
    path: string;
    workspaceRevision: number;
};

export type ArmadaWorkspaceEditorSelectionContext = {
    path: string;
    selectedText: string;
    startLine?: number;
    endLine?: number;
};

export type ArmadaWorkspaceEditorPreferences = {
    mode: "rich" | "source";
    documents: Record<
        string,
        {
            source: {selection: {anchor: number; head: number}; scrollTop: number};
            rich: {selection?: {anchor: number; head: number}; scrollTop: number};
        }
    >;
};

export type ArmadaWorkspaceRichEditorModule = {
    create_armada_workspace_markdown_editor: (options: {
        root: HTMLElement;
        markdown: string;
        editRevision: number;
        readOnly?: boolean;
        placeholder?: string;
        selection?: {anchor: number; head: number};
        scrollTop?: number;
        onDirty: (event: {origin: "user"; editRevision: number}) => void;
        onSerialized: (result: {
            status: "serialized";
            editRevision: number;
            markdown: string;
        }) => void;
        onSerializationError?: (result: {
            status: "error";
            editRevision: number;
            message: string;
        }) => void;
    }) => Promise<ArmadaWorkspaceMarkdownEditorResult>;
};

export type ArmadaWorkspaceSourceEditorModule = {
    create_armada_workspace_source_editor: typeof create_armada_workspace_source_editor;
};

export type ArmadaWorkspaceScheduledWork = {cancel: () => void};

export type ArmadaWorkspaceEditorDependencies = {
    getFile: typeof get_armada_workspace_file;
    putFile: typeof put_armada_workspace_file;
    rawUrl: typeof armada_workspace_raw_url;
    loadSourceEditor: () => Promise<ArmadaWorkspaceSourceEditorModule>;
    loadRichEditor: () => Promise<ArmadaWorkspaceRichEditorModule>;
    scheduleSourceFlush: (callback: () => void) => ArmadaWorkspaceScheduledWork;
    scheduleAutosave: (callback: () => void) => ArmadaWorkspaceScheduledWork;
};

export type ArmadaWorkspaceEditorController = {
    activateWorkspace: (
        workspaceId: string,
        workspaceRevision: number,
        limits: {rawBytes: number | null; textBytes: number; writable: boolean},
        preferences?: ArmadaWorkspaceEditorPreferences,
    ) => void;
    open: (entry: ArmadaWorkspaceEntry, preferredMode?: "rich" | "source") => Promise<void>;
    reconcileEntry: (
        entry: ArmadaWorkspaceEntry,
        workspaceRevision: number,
        previousPath?: string,
    ) => Promise<"conflict" | "current" | "error" | "missing">;
    detachPath: (path: string) => void;
    closeActive: () => Promise<boolean>;
    switchMode: (mode: "rich" | "source") => Promise<boolean>;
    save: () => Promise<void>;
    discardChanges: () => Promise<void>;
    resolveConflict: (resolution: "overwrite" | "reload") => Promise<void>;
    getSnapshot: () => ArmadaWorkspaceEditorSnapshot;
    getSelectionContext: () => ArmadaWorkspaceEditorSelectionContext | undefined;
    getPreferences: () => ArmadaWorkspaceEditorPreferences;
    destroy: () => Promise<void>;
};

export type ArmadaWorkspaceEditorOptions = {
    root: HTMLElement;
    onUpdate: (snapshot: ArmadaWorkspaceEditorSnapshot) => void;
    onSaved?: (event: ArmadaWorkspaceEditorSaveEvent) => void;
    dependencies?: Partial<ArmadaWorkspaceEditorDependencies>;
};

export type ArmadaWorkspaceMountedAdapter =
    | {
          kind: "source";
          identity: ArmadaWorkspaceDocumentIdentity;
          editor: ArmadaWorkspaceSourceEditor;
      }
    | {
          kind: "rich";
          identity: ArmadaWorkspaceDocumentIdentity;
          editor: ArmadaWorkspaceMarkdownEditor;
      };
