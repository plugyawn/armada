import type {
    PearWorkspaceEntry,
    PearWorkspaceFile,
    get_pear_workspace_file,
    pear_workspace_raw_url,
    put_pear_workspace_file,
} from "./legacy_workspace_api.ts";
import type {
    PearWorkspaceMarkdownEditor,
    PearWorkspaceMarkdownEditorResult,
} from "./legacy_workspace_markdown_editor.ts";
import type {
    PearWorkspaceSourceEditor,
    create_pear_workspace_source_editor,
} from "./legacy_workspace_source_editor.ts";
import type {
    PearWorkspaceDocumentIdentity,
    PearWorkspaceDocumentSession,
    PearWorkspaceStoreState,
} from "./legacy_workspace_store.ts";

export type PearWorkspaceEditorSurface =
    | {kind: "empty"}
    | {kind: "loading"; entry: PearWorkspaceEntry}
    | {kind: "text"; entry: PearWorkspaceEntry; file: PearWorkspaceFile}
    | {kind: "raster"; entry: PearWorkspaceEntry; rawUrl: string}
    | {kind: "binary"; entry: PearWorkspaceEntry; message: string}
    | {
          kind: "oversize";
          entry: PearWorkspaceEntry;
          maxBytes: number;
          rawAvailable: boolean;
      }
    | {
          kind: "error";
          entry: PearWorkspaceEntry | undefined;
          message: string;
          action?: "retry" | "source";
      };

export type PearWorkspaceEditorSnapshot = {
    surface: PearWorkspaceEditorSurface;
    store: PearWorkspaceStoreState;
    activeDocument: PearWorkspaceDocumentSession | undefined;
    activeIdentity: PearWorkspaceDocumentIdentity | undefined;
    mountedAdapter: "rich" | "source" | undefined;
    richModeLoading: boolean;
    richModeIssue:
        | {kind: "source_only" | "load_error"; message: string; retryable: boolean}
        | undefined;
    notice: string | undefined;
};

export type PearWorkspaceEditorSaveEvent = {
    oldEntryId: string;
    newEntryId: string;
    path: string;
    workspaceRevision: number;
};

export type PearWorkspaceEditorSelectionContext = {
    path: string;
    selectedText: string;
    startLine?: number;
    endLine?: number;
};

export type PearWorkspaceEditorPreferences = {
    mode: "rich" | "source";
    documents: Record<
        string,
        {
            source: {selection: {anchor: number; head: number}; scrollTop: number};
            rich: {selection?: {anchor: number; head: number}; scrollTop: number};
        }
    >;
};

export type PearWorkspaceRichEditorModule = {
    create_pear_workspace_markdown_editor: (options: {
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
    }) => Promise<PearWorkspaceMarkdownEditorResult>;
};

export type PearWorkspaceSourceEditorModule = {
    create_pear_workspace_source_editor: typeof create_pear_workspace_source_editor;
};

export type PearWorkspaceScheduledWork = {cancel: () => void};

export type PearWorkspaceEditorDependencies = {
    getFile: typeof get_pear_workspace_file;
    putFile: typeof put_pear_workspace_file;
    rawUrl: typeof pear_workspace_raw_url;
    loadSourceEditor: () => Promise<PearWorkspaceSourceEditorModule>;
    loadRichEditor: () => Promise<PearWorkspaceRichEditorModule>;
    scheduleSourceFlush: (callback: () => void) => PearWorkspaceScheduledWork;
    scheduleAutosave: (callback: () => void) => PearWorkspaceScheduledWork;
};

export type PearWorkspaceEditorController = {
    activateWorkspace: (
        workspaceId: string,
        workspaceRevision: number,
        limits: {rawBytes: number | null; textBytes: number; writable: boolean},
        preferences?: PearWorkspaceEditorPreferences,
    ) => void;
    open: (entry: PearWorkspaceEntry, preferredMode?: "rich" | "source") => Promise<void>;
    reconcileEntry: (
        entry: PearWorkspaceEntry,
        workspaceRevision: number,
        previousPath?: string,
    ) => Promise<"conflict" | "current" | "error" | "missing">;
    detachPath: (path: string) => void;
    closeActive: () => Promise<boolean>;
    switchMode: (mode: "rich" | "source") => Promise<boolean>;
    save: () => Promise<void>;
    discardChanges: () => Promise<void>;
    resolveConflict: (resolution: "overwrite" | "reload") => Promise<void>;
    getSnapshot: () => PearWorkspaceEditorSnapshot;
    getSelectionContext: () => PearWorkspaceEditorSelectionContext | undefined;
    getPreferences: () => PearWorkspaceEditorPreferences;
    destroy: () => Promise<void>;
};

export type PearWorkspaceEditorOptions = {
    root: HTMLElement;
    onUpdate: (snapshot: PearWorkspaceEditorSnapshot) => void;
    onSaved?: (event: PearWorkspaceEditorSaveEvent) => void;
    dependencies?: Partial<PearWorkspaceEditorDependencies>;
};

export type PearWorkspaceMountedAdapter =
    | {
          kind: "source";
          identity: PearWorkspaceDocumentIdentity;
          editor: PearWorkspaceSourceEditor;
      }
    | {
          kind: "rich";
          identity: PearWorkspaceDocumentIdentity;
          editor: PearWorkspaceMarkdownEditor;
      };
