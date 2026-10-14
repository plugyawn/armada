// The window's one tooltip: one object. It waits before the first and
// never between neighbours. A control names its tip with data-tip and its
// key with data-key.

import {in_top_layer, into_top_layer, raise} from "./layer.ts";
import {fade, halt, now, release} from "./motion.ts";
import {amounts, times} from "./springs.ts";

let tip_element: HTMLElement | undefined;
let tip_for: HTMLElement | null = null;
let tip_at: {x: number; y: number} | null = null;
let tip_timer: ReturnType<typeof setTimeout> | undefined;
let tip_gone = Number.NEGATIVE_INFINITY;

function show(target: HTMLElement): void {
    if (tip_element === undefined) {
        tip_element = document.createElement("div");
        tip_element.className = "armada-tip";
        tip_element.dataset["live"] = "";
        tip_element.dataset["armada"] = "tip";
        tip_element.setAttribute("role", "tooltip");
        document.body.append(tip_element);
        into_top_layer(tip_element);
    } else if (in_top_layer(target)) {
        raise(tip_element);
    }
    const words = document.createElement("span");
    words.textContent = target.dataset["tip"] ?? "";
    tip_element.replaceChildren(words);
    const key = target.dataset["key"];
    if (key !== undefined) {
        const hint = document.createElement("kbd");
        hint.className = "armada-tip-key";
        hint.textContent = key;
        tip_element.append(hint);
    }
    const box = target.getBoundingClientRect();
    const width = tip_element.offsetWidth;
    const height = tip_element.offsetHeight;
    const x = Math.round(
        Math.max(8, Math.min(window.innerWidth - width - 8, box.left + box.width / 2 - width / 2)),
    );
    const y = Math.round(box.top - height - 6);
    const was = tip_for === null ? null : tip_at;
    tip_element.style.translate = `${x}px ${y}px`;
    tip_for = target;
    tip_at = {x, y};
    fade(tip_element, 1);
    if (was !== null && Math.abs(was.x - x) <= amounts["ring-near"]) {
        release(tip_element, {x: was.x - x, y: was.y - y}, "touch");
    } else {
        halt(tip_element);
        release(tip_element, {y: amounts["pane-rise"]}, "touch");
    }
}

export function tip(target: HTMLElement | null, options: {now?: boolean | undefined} = {}): void {
    clearTimeout(tip_timer);
    if (target === null) {
        if (tip_for !== null && tip_element !== undefined) {
            tip_for = null;
            tip_gone = now();
            fade(tip_element, 0);
        }
        return;
    }
    if (target === tip_for) {
        return;
    }
    if (tip_for !== null || options.now || now() - tip_gone < times["tip-linger"]) {
        show(target);
    } else {
        tip_timer = setTimeout(() => {
            show(target);
        }, times["tip-delay"]);
    }
}

// The tooltip object, used once as a short confirmation: "Copied".
export function say(target: HTMLElement, text: string, lasts = times["tip-delay"] * 2): void {
    const tip_was = target.dataset["tip"];
    const key_was = target.dataset["key"];
    target.dataset["tip"] = text;
    delete target.dataset["key"];
    tip_for = null;
    clearTimeout(tip_timer);
    show(target);
    if (tip_was === undefined) {
        delete target.dataset["tip"];
    } else {
        target.dataset["tip"] = tip_was;
    }
    if (key_was !== undefined) {
        target.dataset["key"] = key_was;
    }
    tip_timer = setTimeout(() => {
        tip(null);
    }, lasts);
}
