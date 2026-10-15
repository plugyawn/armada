import DOMPurify from "dompurify";
import {micromark} from "micromark";
import {gfm, gfmHtml} from "micromark-extension-gfm";

// Read-only rendering for Markdown that Rich mode refuses to edit (raw
// HTML, frontmatter, footnotes, reference links). Rich mode's round-trip
// guard exists to protect edits; reading has no such constraint, so a
// README with a centered logo should still read as a README.

export type ArmadaWorkspaceMarkdownPreviewOptions = {
    // Path of the document being rendered, used to resolve relative URLs.
    path: string;
    // Maps a workspace-relative file path to a URL that serves its bytes,
    // or undefined when the workspace has no such file loaded.
    rawUrlForPath: (path: string) => string | undefined;
};

const frontmatter_pattern = /^(?:---|\+\+\+)\r?\n[\s\S]*?\r?\n(?:---|\+\+\+)[ \t]*(?:\r?\n|$)/;
const absolute_url_pattern = /^(?:[a-z][a-z\d+.-]*:|\/\/|#)/i;

function resolve_workspace_path(document_path: string, reference: string): string | undefined {
    const [pathname = ""] = reference.split(/[?#]/);
    if (pathname === "") {
        return undefined;
    }
    const segments = pathname.startsWith("/") ? [] : document_path.split("/").slice(0, -1);
    for (const segment of pathname.split("/")) {
        if (segment === "" || segment === ".") {
            continue;
        }
        if (segment === "..") {
            if (segments.length === 0) {
                return undefined;
            }
            segments.pop();
            continue;
        }
        segments.push(segment);
    }
    return segments.length === 0 ? undefined : segments.join("/");
}

export function render_armada_workspace_markdown_preview(
    markdown: string,
    options: ArmadaWorkspaceMarkdownPreviewOptions,
): DocumentFragment {
    const html = micromark(markdown.replace(frontmatter_pattern, ""), {
        allowDangerousHtml: true,
        extensions: [gfm()],
        htmlExtensions: [gfmHtml()],
    });
    const fragment = DOMPurify.sanitize(html, {
        RETURN_DOM_FRAGMENT: true,
        FORBID_TAGS: ["style", "form", "button", "textarea", "select", "iframe"],
        FORBID_ATTR: ["style", "id", "name"],
    });

    // Task-list checkboxes are the only inputs Markdown produces.
    for (const input of fragment.querySelectorAll("input")) {
        if (input.type === "checkbox") {
            input.disabled = true;
        } else {
            input.remove();
        }
    }

    for (const image of fragment.querySelectorAll("img")) {
        const source = image.getAttribute("src") ?? "";
        image.loading = "lazy";
        image.decoding = "async";
        image.referrerPolicy = "no-referrer";
        // The app's base styles force `height: auto` on images, which
        // defeats the sizing attributes READMEs use for logos and badges.
        for (const [attribute, property] of [
            ["width", "inlineSize"],
            ["height", "blockSize"],
        ] as const) {
            const size = /^(\d+(?:\.\d+)?)(?:px)?$/.exec(image.getAttribute(attribute) ?? "");
            if (size !== null) {
                image.style[property] = `${size[1]}px`;
            }
        }
        if (absolute_url_pattern.test(source)) {
            continue;
        }
        const workspace_path = resolve_workspace_path(options.path, source);
        const raw_url =
            workspace_path === undefined ? undefined : options.rawUrlForPath(workspace_path);
        if (raw_url === undefined) {
            image.removeAttribute("src");
        } else {
            image.src = raw_url;
        }
    }

    for (const link of fragment.querySelectorAll("a[href]")) {
        const reference = link.getAttribute("href") ?? "";
        if (reference.startsWith("#")) {
            // In-page anchors have no target once ids are stripped.
            link.removeAttribute("href");
            continue;
        }
        if (absolute_url_pattern.test(reference)) {
            link.setAttribute("target", "_blank");
            link.setAttribute("rel", "noopener noreferrer");
            continue;
        }
        const workspace_path = resolve_workspace_path(options.path, reference);
        link.removeAttribute("href");
        if (workspace_path !== undefined) {
            link.setAttribute("role", "link");
            link.setAttribute("tabindex", "0");
            link.setAttribute("data-workspace-path", workspace_path);
        }
    }

    for (const table of fragment.querySelectorAll("table")) {
        const scroller = document.createElement("div");
        scroller.className = "legacy-workspace-preview__table";
        table.replaceWith(scroller);
        scroller.append(table);
    }

    return fragment;
}
