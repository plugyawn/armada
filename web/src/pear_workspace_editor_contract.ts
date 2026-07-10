import type {
    PearWorkspaceEntry,
    PearWorkspaceFile,
    get_pear_workspace_file,
    pear_workspace_raw_url,
    put_pear_workspace_file,
} from "./pear_workspace_api.ts";
import type {
    PearWorkspaceMarkdownEditor,
    PearWorkspaceMarkdownEditorResult,
} from "./pear_workspace_markdown_editor.ts";
import type {
    PearWorkspaceSourceEditor,
    create_pear_workspace_source_editor,
} from "./pear_workspace_source_editor.ts";
import type {
    PearWorkspaceDocumentIdentity,
    PearWorkspaceDocumentSession,
    PearWorkspaceStoreState,
} from "./pear_workspace_store.ts";

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
    | {kind: "error"; entry: PearWorkspaceEntry | undefined; message: string};

export type PearWorkspaceEditorSnapshot = {
    surface: PearWorkspaceEditorSurface;
    store: PearWorkspaceStoreState;
    activeDocument: PearWorkspaceDocumentSession | undefined;
    activeIdentity: PearWorkspaceDocumentIdentity | undefined;
    mountedAdapter: "rich" | "source" | undefined;
    richModeLoading: boolean;
    notice: string | undefined;
};

export type PearWorkspaceEditorSaveEvent = {
    oldEntryId: string;
    newEntryId: string;
    path: string;
    workspaceRevision: number;
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

export type PearWorkspaceScheduledWork = {cancel: () => void};

export type PearWorkspaceEditorDependencies = {
    getFile: typeof get_pear_workspace_file;
    putFile: typeof put_pear_workspace_file;
    rawUrl: typeof pear_workspace_raw_url;
    createSourceEditor: typeof create_pear_workspace_source_editor;
    loadRichEditor: () => Promise<PearWorkspaceRichEditorModule>;
    defer: (callback: () => void) => PearWorkspaceScheduledWork;
    scheduleAutosave: (callback: () => void) => PearWorkspaceScheduledWork;
};

export type PearWorkspaceEditorController = {
    activateWorkspace: (
        workspaceId: string,
        workspaceRevision: number,
        limits: {rawBytes: number | null; textBytes: number},
        preferences?: PearWorkspaceEditorPreferences,
    ) => void;
    open: (entry: PearWorkspaceEntry) => Promise<void>;
    switchMode: (mode: "rich" | "source") => Promise<boolean>;
    save: () => Promise<void>;
    resolveConflict: (resolution: "overwrite" | "reload") => Promise<void>;
    getSnapshot: () => PearWorkspaceEditorSnapshot;
    getPreferences: () => PearWorkspaceEditorPreferences;
    destroy: () => Promise<void>;
};

export type PearWorkspaceEditorOptions = {
    root: HTMLElement;
    onUpdate: (snapshot: PearWorkspaceEditorSnapshot) => void;
    onDirty?: (path: string) => void;
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
