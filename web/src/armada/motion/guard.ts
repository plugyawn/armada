// The rule that keeps motion from costing a frame: no rectangle is read
// after a write inside an input task. The helpers that measure call
// `reading`; in a development build, once `watch` is on, a read made after
// the document changed in the task of an input throws, and names itself.
// What must be measured after a write runs in the after-layout phase
// (frame/after_layout.ts), which announces itself with `after_layout`.

const INPUT_EVENTS = [
    "pointerdown",
    "pointerup",
    "click",
    "keydown",
    "keyup",
    "input",
    "beforeinput",
    "compositionend",
    "paste",
    "drop",
] as const;

let observer: MutationObserver | undefined;
let in_input_task = false;
let wrote = false;
let measuring_allowed = false;

function begin_input_task(): void {
    observer?.takeRecords();
    in_input_task = true;
    wrote = false;
}

function end_input_task(): void {
    in_input_task = false;
    wrote = false;
    if (observer !== undefined) {
        requestAnimationFrame(end_input_task);
    }
}

// Development builds only. Returns the function that turns it off.
export function watch(): () => void {
    observer = new MutationObserver(() => {
        // Records are read with takeRecords(), when a helper measures.
    });
    observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        characterData: true,
    });
    for (const type of INPUT_EVENTS) {
        window.addEventListener(type, begin_input_task, true);
    }
    requestAnimationFrame(end_input_task);
    return () => {
        observer?.disconnect();
        observer = undefined;
        in_input_task = false;
        for (const type of INPUT_EVENTS) {
            window.removeEventListener(type, begin_input_task, true);
        }
    };
}

// Called by every helper that reads a rectangle.
export function reading(helper: string): void {
    if (observer === undefined || !in_input_task || measuring_allowed) {
        return;
    }
    wrote ||= observer.takeRecords().length > 0;
    if (wrote) {
        throw new Error(
            `motion.${helper} read layout after a write in an input task: read before the first write, or measure in the after-layout phase`,
        );
    }
}

// The after-layout phase: layout is clean again, and everything in it
// reads first and then writes transforms only.
export function after_layout(run: () => void): void {
    const was = measuring_allowed;
    measuring_allowed = true;
    try {
        run();
    } finally {
        measuring_allowed = was;
    }
}
