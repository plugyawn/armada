// The after-layout phase (SPEC-ENGINEERING section 2): the one place in the
// frame where layout is read. A handler that needs a rectangle after its
// own writes queues the read here; the queue runs at the start of the next
// animation frame, reads everything, and writes nothing but attributes and
// transforms.
//
// The header's scroll edge also lives here: a scroll event reads its own
// scroller's offset while layout is clean, and the header's attribute is
// written only when it changes.

const queue: (() => void)[] = [];
let scheduled = false;

function run(): void {
    scheduled = false;
    for (const read of queue.splice(0)) {
        read();
    }
}

export function after_layout(read: () => void): void {
    queue.push(read);
    if (!scheduled) {
        scheduled = true;
        requestAnimationFrame(run);
    }
}

let edge_shown = false;

function show_edge(header: Element, shown: boolean): void {
    if (shown !== edge_shown) {
        edge_shown = shown;
        header.toggleAttribute("data-edge", shown);
    }
}

// The scroller in front is the only one that is visible: the feed under a
// document view is hidden, Inbox and Recent are shown one at a time.
function front_scroller(pane: Element): Element | undefined {
    return [...pane.querySelectorAll("[data-armada$=-scroller]")].find((scroller) =>
        scroller.checkVisibility({visibilityProperty: true}),
    );
}

export function watch_scroll_edge(pane: Element, header: Element): void {
    pane.addEventListener(
        "scroll",
        (event) => {
            const scroller = event.target;
            if (scroller instanceof Element && scroller === front_scroller(pane)) {
                show_edge(header, scroller.scrollTop > 0);
            }
        },
        {capture: true, passive: true},
    );
}

// After the view in front changes: its own offset decides.
export function refresh_scroll_edge(pane: Element, header: Element): void {
    after_layout(() => {
        show_edge(header, (front_scroller(pane)?.scrollTop ?? 0) > 0);
    });
}
