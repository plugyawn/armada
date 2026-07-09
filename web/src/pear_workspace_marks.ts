import * as z from "zod/mini";

import * as channel from "./channel.ts";
import * as state_data from "./state_data.ts";

// The left sidebar's workspace marks (design §8.3): repo-bound
// channels wear a quiet ◉ and worktree-backed topics wear the branch
// glyph. Which channels are bound arrives with /register
// (pear_project_stream_ids); which topics have worktrees is fetched
// lazily per channel, the first time its topic list renders.

const workspace_topics_response_schema = z.object({
    pear_workspace_topics: z.array(
        z.object({
            topic: z.string(),
            branch: z.string(),
        }),
    ),
});

// stream_id -> lowercased topic names that have a worktree.
const workspace_topics_by_stream = new Map<number, Set<string>>();
const inflight_stream_ids = new Set<number>();

type Hooks = {
    redraw_stream_list: () => void;
    redraw_stream_topics: (stream_id: number) => void;
};
let hooks: Hooks | undefined;

export function initialize(initial_hooks: Hooks): void {
    hooks = initial_hooks;
}

export function topic_has_pear_worktree(stream_id: number, topic_name: string): boolean {
    return workspace_topics_by_stream.get(stream_id)?.has(topic_name.toLowerCase()) ?? false;
}

export function ensure_workspace_topics(stream_id: number, stream_name: string): void {
    if (!state_data.stream_has_pear_project(stream_id)) {
        return;
    }
    if (workspace_topics_by_stream.has(stream_id) || inflight_stream_ids.has(stream_id)) {
        return;
    }
    inflight_stream_ids.add(stream_id);
    void channel.get({
        url: "/json/pear/workspace-topics",
        data: {stream: stream_name},
        success(raw_data) {
            inflight_stream_ids.delete(stream_id);
            const parsed = workspace_topics_response_schema.safeParse(raw_data);
            if (!parsed.success) {
                return;
            }
            workspace_topics_by_stream.set(
                stream_id,
                new Set(parsed.data.pear_workspace_topics.map((row) => row.topic.toLowerCase())),
            );
            hooks?.redraw_stream_topics(stream_id);
        },
        error() {
            inflight_stream_ids.delete(stream_id);
        },
    });
}

// There is no server event stream for project bindings, so the client
// patches its own view when the topic context it fetched anyway
// reports a bound repository or a fresh worktree.
export function note_pinned_repo_for_stream(stream_id: number): void {
    if (state_data.add_pear_project_stream_id(stream_id)) {
        workspace_topics_by_stream.delete(stream_id);
        hooks?.redraw_stream_list();
    }
}

export function note_topic_workspace(stream_id: number, topic_name: string): void {
    const topics = workspace_topics_by_stream.get(stream_id);
    if (topics === undefined) {
        // Nothing cached yet; the next topic-list render fetches fresh.
        return;
    }
    if (!topics.has(topic_name.toLowerCase())) {
        topics.add(topic_name.toLowerCase());
        hooks?.redraw_stream_topics(stream_id);
    }
}
