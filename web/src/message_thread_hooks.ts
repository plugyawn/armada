type TraceEnhancer = (root: HTMLElement) => void;

// The human thread always leads the cycler ("Main" in codex-cli terms).
export const HUMAN_THREAD_KEY = "replies";

export type ThreadComposeTarget = {
    textarea: HTMLTextAreaElement;
    notify_content_changed: () => void;
};

type MessageThreadHooks = {
    close: () => void;
    destroy_for_message: (message_id: number) => void;
    handle_topic_marked_read: (stream_id: number, topic: string) => void;
    get_compose_target: (message_id: number) => ThreadComposeTarget | undefined;
    open_for_draft: (thread_root_message_id: number) => void;
    open_thread: (
        message_id: number,
        thread_key: string,
        anchor: HTMLElement,
        enhance_trace: TraceEnhancer,
    ) => void;
    render_for_message_row: (
        row: HTMLElement,
        message_id: number,
        enhance_trace: TraceEnhancer,
    ) => void;
};

let hooks: MessageThreadHooks | undefined;

export function register(message_thread_hooks: MessageThreadHooks): void {
    hooks = message_thread_hooks;
}

export function close(): void {
    hooks?.close();
}

export function destroy_for_message(message_id: number): void {
    hooks?.destroy_for_message(message_id);
}

export function handle_topic_marked_read(stream_id: number, topic: string): void {
    hooks?.handle_topic_marked_read(stream_id, topic);
}

export function get_compose_target(message_id: number): ThreadComposeTarget | undefined {
    return hooks?.get_compose_target(message_id);
}

export function focus_composer(message_id: number): boolean {
    const target = get_compose_target(message_id);
    if (target === undefined) {
        return false;
    }
    target.textarea.focus();
    return true;
}

export function open_for_draft(thread_root_message_id: number): void {
    hooks?.open_for_draft(thread_root_message_id);
}

export function open_thread(
    message_id: number,
    thread_key: string,
    anchor: HTMLElement,
    enhance_trace: TraceEnhancer,
): void {
    hooks?.open_thread(message_id, thread_key, anchor, enhance_trace);
}

export function render_for_message_row(
    row: HTMLElement,
    message_id: number,
    enhance_trace: TraceEnhancer,
): void {
    hooks?.render_for_message_row(row, message_id, enhance_trace);
}
