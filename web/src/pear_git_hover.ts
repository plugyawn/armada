// Commit tree on hover (R3): hovering the panel's git affordance
// (.pear-git-tree-target) opens a tippy popover with the
// `git log --graph`-style commit tree for the topic's worktree,
// fetched on demand from GET /json/pear/git-tree. Results are cached
// per stream/topic with a short TTL and only ever fetched on hover —
// this surface never polls.

import * as tippy from "tippy.js";

import render_pear_git_tree_popover from "../templates/pear_git_tree_popover.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {LONG_HOVER_DELAY} from "./tippyjs.ts";
import * as ui_util from "./ui_util.ts";

export type PearGitTree = {
    available: boolean;
    workdir?: string;
    graph?: string;
    status_short?: string;
};

export const GIT_TREE_CACHE_TTL_MS = 60 * 1000;

type PearGitTreeCacheEntry = {
    tree: PearGitTree;
    fetched_at: number;
};

let initialized = false;
const git_tree_cache = new Map<string, PearGitTreeCacheEntry>();
const inflight_fetches = new Map<string, Promise<PearGitTree>>();

function cache_key(stream: string, topic: string): string {
    return `${stream}\n${topic}`;
}

export function clear_cache(): void {
    git_tree_cache.clear();
    inflight_fetches.clear();
}

export async function fetch_git_tree(stream: string, topic: string): Promise<PearGitTree> {
    const key = cache_key(stream, topic);
    const cached = git_tree_cache.get(key);
    if (cached !== undefined && Date.now() - cached.fetched_at < GIT_TREE_CACHE_TTL_MS) {
        return cached.tree;
    }
    const pending = inflight_fetches.get(key);
    if (pending !== undefined) {
        return pending;
    }
    // Register the inflight entry before issuing the request, so the
    // callbacks' cleanup works even if the transport settles
    // synchronously.
    let resolve_fetch!: (tree: PearGitTree) => void;
    let reject_fetch!: (error: Error) => void;
    const fetch_promise = new Promise<PearGitTree>((resolve, reject) => {
        resolve_fetch = resolve;
        reject_fetch = reject;
    });
    inflight_fetches.set(key, fetch_promise);
    void channel.get({
        url: "/json/pear/git-tree",
        data: {stream, topic},
        success(raw_data) {
            const data = raw_data as {git_tree?: PearGitTree};
            const tree = data.git_tree ?? {available: false};
            git_tree_cache.set(key, {tree, fetched_at: Date.now()});
            inflight_fetches.delete(key);
            resolve_fetch(tree);
        },
        error(xhr) {
            inflight_fetches.delete(key);
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to fetch Pear git tree: ${xhr.responseText}`);
            }
            reject_fetch(new Error("Failed to fetch Pear git tree"));
        },
    });
    return fetch_promise;
}

function prefers_reduced_motion(): boolean {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

function set_popover_content(
    instance: tippy.Instance,
    context: {loading?: boolean; available?: boolean; graph?: string; status_short?: string},
): void {
    instance.setContent(ui_util.parse_html(render_pear_git_tree_popover(context)));
}

export function initialize(): void {
    if (initialized || typeof document === "undefined") {
        // Guarded for the node-test environment, where there is no
        // real DOM to delegate hover handling on.
        return;
    }
    initialized = true;
    tippy.delegate("body", {
        target: ".pear-git-tree-target",
        // mouseenter+focus (not click) keeps this a pure disclosure;
        // focus makes the tree keyboard-reachable via the affordance's
        // tabindex.
        trigger: "mouseenter focus",
        delay: LONG_HOVER_DELAY,
        appendTo: () => document.body,
        placement: "left-start",
        // Width is controlled by .pear-git-tree-popover in pear.css.
        maxWidth: "none",
        // Interactive so long graphs can be scrolled and hashes copied.
        interactive: true,
        onShow(instance) {
            const reference = instance.reference;
            const stream = reference.getAttribute("data-stream") ?? "";
            const topic = reference.getAttribute("data-topic") ?? "";
            if (stream === "" || topic === "") {
                return false;
            }
            if (prefers_reduced_motion()) {
                instance.setProps({duration: 0});
            }
            set_popover_content(instance, {loading: true});
            void (async () => {
                try {
                    const tree = await fetch_git_tree(stream, topic);
                    if (!instance.state.isDestroyed) {
                        set_popover_content(instance, {
                            available: tree.available && (tree.graph ?? "") !== "",
                            graph: tree.graph ?? "",
                            status_short: tree.status_short ?? "",
                        });
                    }
                } catch {
                    if (!instance.state.isDestroyed) {
                        set_popover_content(instance, {available: false});
                    }
                }
            })();
            return undefined;
        },
        onHidden(instance) {
            instance.destroy();
        },
    });
}
