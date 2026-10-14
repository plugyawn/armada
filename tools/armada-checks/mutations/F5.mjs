// F5's breakages (PLAN.md, rule 11): the scroll code it moved into the
// frame's pane, broken the way the built-in entry broke it on a scrolling
// document. Loaded by lib/mutate.mjs over the entry of the same name.

const ident = String.raw`[\w$]+`;

export const MUTATIONS = {
    // Built in, the marks moved to 40% and 60%. In the frame's pane the
    // feed has no blank tail, and the page keys' scenario starts two pages
    // up (feed-semantics.mjs): a page down then leaves the row after the
    // selected one with its top just inside the readable area, which the
    // rule accepts whatever the lower mark is, so both rules pick it. Here
    // a row going down must reach 70%, its top included, and a row going
    // up must rise to 50%: another row for every key and for the wheel.
    "keep-in-view-at-other-marks": {
        about: "a scroll takes the selection along at 70% and 50% of the readable area instead of 10% and 90%",
        check: "feed-semantics",
        only: "pointer",
        breaks: [
            "feed-semantics/pointer.keep-in-view",
            "feed-semantics/pointer.keys.PageUp.selected-id",
            "feed-semantics/pointer.keys.PageDown.selected-id",
            "feed-semantics/pointer.keys.Space.selected-id",
        ],
        edits: [
            {
                find: /\(?1 \/ 10\)? \* info\.visible_height/g,
                replace: "(7 / 10) * info.visible_height",
                min: 1,
            },
            {
                find: /\(?9 \/ 10\)? \* info\.visible_height/g,
                replace: "(5 / 10) * info.visible_height",
                min: 1,
            },
            {
                find: /if \(message_top >= info\.visible_top\) \{/g,
                replace: "if (message_top >= info.visible_top + (7 / 10) * info.visible_height) {",
                min: 1,
            },
        ],
    },
    // Built in, the edit scrolled the window; since F5 the document never
    // scrolls and the feed scrolls in its pane, so the edit scrolls that.
    "new-messages-always-scroll": {
        about: "a new message scrolls the feed even when the reader has scrolled up",
        check: "feed-semantics",
        only: "autoscroll",
        breaks: [
            "feed-semantics/autoscroll.scrolled-up.stays",
            "feed-semantics/anchor.append.offset",
        ],
        edits: [
            {
                find: new RegExp(
                    String.raw`const started_scrolled_up = this\.manages_primary_viewport && ${ident}\.is_scrolled_up\(\);`,
                    "g",
                ),
                replace:
                    'const started_scrolled_up = false; if (messages_are_new && this.manages_primary_viewport) { document.querySelector("[data-armada=feed-scroller]").scrollTop += 300; }',
                min: 1,
            },
        ],
    },
};
