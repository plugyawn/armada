// The split of a rendered message into bubbles and blocks, as
// SPEC-ENGINEERING.md section 5 defines it. This is the reference the
// `parts-corpus` check holds the client's and the server's Markdown to;
// the feed's own `parts_from_html` replaces it when it exists.
//
// Runs in the page (it needs a DOM): pass it to `page.evaluate`.

/* global DOMParser */

export function parts_from_html(html) {
    const root = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html").body
        .firstElementChild;
    const text_of = (element) => element.textContent.replaceAll(/\s+/g, " ").trim();
    const is_block = (element) =>
        element.matches(
            "div.codehilite, pre, table, div.spoiler-block, div.message_inline_image, div.message_embed, div.message_inline_video, div.youtube-video, div.legacy-diff, div.inline-preview-twitter",
        ) ||
        (element.matches("p") &&
            element.children.length === 1 &&
            element.firstElementChild.matches("span.katex-display") &&
            text_of(element) === text_of(element.firstElementChild));
    const parts = [];
    // The bubble that the next prose element may join, and what its last
    // element was.
    let open = null;
    let previous = "";
    const bubble = (text) => {
        open = {kind: "bubble", text};
        parts.push(open);
    };
    for (const element of root.children) {
        const tag = element.tagName.toLowerCase();
        if (tag === "hr") {
            open = null;
            previous = "";
        } else if (is_block(element)) {
            parts.push({kind: "block", text: text_of(element)});
            open = null;
            previous = "";
        } else if (/^h[1-6]$/.test(tag)) {
            bubble(text_of(element));
            previous = "heading";
        } else if (tag === "ul" || tag === "ol" || tag === "blockquote") {
            if (open !== null && previous !== "") {
                open.text = `${open.text} ${text_of(element)}`.trim();
            } else {
                bubble(text_of(element));
            }
            previous = "prose";
        } else if (previous === "heading" && open !== null) {
            // The element after a heading joins the heading's bubble.
            open.text = `${open.text} ${text_of(element)}`.trim();
            previous = "prose";
        } else {
            bubble(text_of(element));
            previous = "prose";
        }
    }
    return parts.filter((part) => part.kind === "block" || part.text !== "");
}
