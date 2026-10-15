"use strict";

const assert = require("node:assert/strict");

const {jQueryFactory} = require("jquery/factory");
const {JSDOM} = require("jsdom");

const {mock_jquery, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body><div id='host'></div></body></html>", {
    pretendToBeVisual: true,
});
const {window} = dom;
let frame_count = 0;
let reduced_motion = false;
let copied_text = "";
let fullscreen_count = 0;
const motion_listeners = new Set();
const motion_query = {
    get matches() {
        return reduced_motion;
    },
    addEventListener(_event, listener) {
        motion_listeners.add(listener);
    },
    removeEventListener(_event, listener) {
        motion_listeners.delete(listener);
    },
};
const intersection_observers = [];
class TestIntersectionObserver {
    constructor(callback) {
        this.callback = callback;
        this.disconnected = false;
        intersection_observers.push(this);
    }

    observe(target) {
        this.target = target;
    }

    disconnect() {
        this.disconnected = true;
    }

    set_visible(visible) {
        this.callback([{target: this.target, isIntersecting: visible}]);
    }
}
const resize_observers = [];
class TestResizeObserver {
    constructor(callback) {
        this.callback = callback;
        this.disconnected = false;
        resize_observers.push(this);
    }

    observe(target) {
        this.target = target;
    }

    disconnect() {
        this.disconnected = true;
    }

    set_height(height) {
        this.callback([{target: this.target, contentRect: {height}}]);
    }
}
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    AbortController: window.AbortController,
    MutationObserver: window.MutationObserver,
    TextEncoder,
    requestAnimationFrame(callback) {
        frame_count += 1;
        return callback(0);
    },
    matchMedia: () => motion_query,
    IntersectionObserver: TestIntersectionObserver,
    ResizeObserver: TestResizeObserver,
    CSS: {escape: (value) => value.replaceAll('"', '\\"')},
    navigator: {
        clipboard: {
            async writeText(value) {
                copied_text = value;
            },
        },
    },
})) {
    set_global(name, value);
}
window.HTMLElement.prototype.requestFullscreen = async () => {
    fullscreen_count += 1;
};
const $ = jQueryFactory(window);
mock_jquery($);

// KaTeX decides when it is first loaded whether the document is in
// standards mode, and replaces katex.render with a function that always
// throws if it is not. The test runner keeps node_modules loaded across
// test files, so a file that ran earlier with a stub `document` (for
// example compose_paste) would leave that broken copy in place. Load a
// fresh copy against this file's standards-mode document.
// eslint-disable-next-line @typescript-eslint/no-dynamic-delete
delete require.cache[require.resolve("katex")];

const artifact = zrequire("legacy_artifact");

function graph_column(node) {
    return node.style.getPropertyValue("--pear-artifact-grid-column");
}

function graph_row(node) {
    return node.style.getPropertyValue("--pear-artifact-grid-row");
}

function valid_document() {
    return {
        version: 1,
        title: "Newton's laws",
        blocks: [
            {type: "copy", eyebrow: "mechanics", heading: "Force changes motion", body: "F = ma"},
            {
                type: "scene",
                label: "Force moving from body A to body B",
                aspect: "wide",
                root: {
                    type: "group",
                    layout: "graph",
                    align: "center",
                    children: [
                        {type: "shape", id: "a", shape: "circle", label: "A"},
                        {type: "shape", id: "b", shape: "circle", label: "B"},
                        {
                            type: "connector",
                            id: "force-edge",
                            from: "a",
                            to: "b",
                            directed: true,
                            label: "force",
                            state: "selected",
                        },
                    ],
                },
            },
        ],
        actions: [{label: "Explain", prompt: "Explain the relationship."}],
    };
}

run_test("parses the generated artifact contract and rejects authored presentation", () => {
    const parsed = artifact.parse_pear_artifact(JSON.stringify(valid_document()));
    assert.equal(parsed.title, "Newton's laws");

    for (const hostile of [
        {style: "color:red"},
        {svg: "<svg><script/></svg>"},
        {url: "https://example.com/pixel"},
        {onclick: "alert(1)"},
    ]) {
        const document = valid_document();
        Object.assign(document.blocks[1].root, hostile);
        assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
    }
});

run_test("renders durable theme-aware media and rejects workspace paths", () => {
    const document = {
        version: 1,
        blocks: [
            {
                type: "media",
                kind: "image",
                src: "/user_uploads/1/ab/diagram.svg",
                srcDark: "/user_uploads/1/cd/diagram-dark.svg",
                alt: "A geometric construction.",
                caption: "Constraints determine the geometry.",
                aspect: "standard",
            },
        ],
    };
    window.document.documentElement.classList.add("dark-theme");
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    const image = rendered.querySelector(".pear-artifact-media-content");
    assert.equal(image.getAttribute("src"), "/user_uploads/1/cd/diagram-dark.svg");
    assert.equal(image.getAttribute("alt"), "A geometric construction.");
    assert.equal(
        rendered.querySelector(".pear-artifact-media-status").textContent,
        "Loading visual…",
    );
    image.dispatchEvent(new window.Event("load"));
    assert.equal(rendered.querySelector(".pear-artifact-media").dataset.status, "ready");
    assert.equal(
        rendered.querySelector("figcaption").textContent,
        "Constraints determine the geometry.",
    );
    window.document.documentElement.classList.remove("dark-theme");

    document.blocks[0] = {
        type: "media",
        kind: "image",
        source: ".pear/artifacts/diagram.svg",
        alt: "Unresolved workspace image.",
    };
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
});

run_test("renders bounded KaTeX in artifact prose, captions, and scene labels", () => {
    const document = valid_document();
    document.blocks[0].body =
        "At $$x_t$$, project the gradient.\n```math\nx_{t+1}=R_{x_t}(v_t)\n```";
    document.blocks[1].root.children[0].label = "$$x_t$$";
    document.blocks[1].steps = [
        {label: "Start", changes: [{target: "a", label: "$$x_t$$"}]},
        {label: "Advance", changes: [{target: "a", label: "$$x_{t+1}$$"}]},
    ];
    document.blocks.push({
        type: "media",
        kind: "image",
        src: "/user_uploads/1/ab/diagram.svg",
        alt: "A tangent construction.",
        caption: "The step stays in $$T_{x_t}M$$.",
    });
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(rendered.querySelectorAll(".pear-artifact-copy .katex").length, 2);
    assert.equal(rendered.querySelectorAll(".pear-artifact-copy .katex-display").length, 1);
    assert.equal(rendered.querySelectorAll(".pear-artifact-shape-label .katex").length, 1);
    assert.equal(rendered.querySelectorAll(".pear-artifact-media-caption .katex").length, 1);
    const visible_artifact_text = [
        ...rendered.querySelectorAll(
            ".pear-artifact-copy, .pear-artifact-shape-label, .pear-artifact-media-caption",
        ),
    ]
        .map((node) => node.textContent)
        .join(" ");
    assert.equal(visible_artifact_text.includes("$$"), false);
    assert.equal(visible_artifact_text.includes("```"), false);

    rendered.querySelector('[aria-label="Next step"]').click();
    assert.equal(
        rendered.querySelector('[data-pear-artifact-target="a"] annotation').textContent,
        "x_{t+1}",
    );
});

run_test("rejects the coordinate-free free layout", () => {
    const document = valid_document();
    document.blocks[1].root.layout = "free";
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
});

run_test("rejects unresolved connectors and duplicate ids", () => {
    const missing = valid_document();
    missing.blocks[1].root.children[2].to = "missing";
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(missing)), undefined);

    const duplicate = valid_document();
    duplicate.blocks[1].root.children[1].id = "a";
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(duplicate)), undefined);
});

run_test("rejects primitive fields that the renderer would ignore", () => {
    for (const ignored of [
        {children: [{type: "text", text: "never rendered"}]},
        {
            points: [
                [0, 0],
                [1, 1],
            ],
        },
        {control: "button"},
    ]) {
        const document = valid_document();
        Object.assign(document.blocks[1].root.children[0], ignored);
        assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
    }
});

run_test("renders native semantic nodes without exposing source JSON or adopting SVG", () => {
    const document = artifact.parse_pear_artifact(JSON.stringify(valid_document()));
    assert.ok(document);
    const host = window.document.querySelector("#host");
    const rendered = artifact.mount_pear_artifact($(host), document, {density: "chat"});
    assert.equal(rendered.dataset.density, "chat");
    assert.equal(rendered.querySelector(".pear-artifact-title").textContent, "Newton's laws");
    assert.equal(rendered.querySelectorAll(".pear-artifact-shape").length, 2);
    const authored_action = [...rendered.querySelectorAll(".pear-artifact-action")].find(
        (control) => control.textContent === "Explain",
    );
    assert.ok(authored_action);
    assert.equal(authored_action.classList.contains("pear-artifact-utility"), false);
    assert.equal(rendered.querySelector(".pear-artifact-raw-json").hidden, true);
    assert.equal(rendered.querySelectorAll("script, iframe, foreignObject").length, 0);
    assert.equal(rendered.querySelectorAll(".pear-artifact-connectors line").length, 1);
    assert.equal(rendered.querySelector(".pear-artifact-scene").getAttribute("role"), "img");
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="a"]')), "1");
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="b"]')), "2");
    assert.equal(
        rendered.querySelector(".pear-artifact-connectors line").dataset.state,
        "selected",
    );
    assert.match(
        rendered.querySelector(".pear-artifact-connectors line").getAttribute("marker-end"),
        /^url\(#pear-artifact-arrow-/,
    );
    assert.equal(rendered.querySelector(".pear-artifact-connector-label").textContent, "force");
    assert.equal(
        rendered.querySelector('.pear-artifact-connectors [data-pear-artifact-target="force-edge"]')
            .dataset.state,
        "selected",
    );
});

run_test("offers one shared utility surface for copy, media, and raw JSON", async () => {
    copied_text = "";
    fullscreen_count = 0;
    const document = {
        version: 1,
        blocks: [
            {type: "copy", body: "A compact explanation."},
            {type: "log", label: "Trace", lines: ["one", "two"]},
            {
                type: "media",
                kind: "image",
                src: "/user_uploads/1/ab/diagram.png",
                alt: "A diagram.",
            },
        ],
    };
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(rendered.querySelectorAll(".pear-artifact-actions").length, 1);
    assert.equal(rendered.querySelectorAll(".pear-artifact-utility").length, 5);
    for (const utility of rendered.querySelectorAll(".pear-artifact-utility")) {
        assert.equal(utility.textContent, "");
        assert.equal(utility.querySelectorAll(".zulip-icon").length, 1);
        assert.equal(
            utility.getAttribute("data-tippy-content"),
            utility.getAttribute("aria-label"),
        );
    }

    const copy_text = rendered.querySelector('[aria-label="Copy text block 1"]');
    assert.ok(copy_text.querySelector(".zulip-icon-copy"));
    copy_text.click();
    await Promise.resolve();
    assert.equal(copied_text, "A compact explanation.");
    assert.equal(copy_text.getAttribute("aria-label"), "Copied");
    assert.ok(copy_text.querySelector(".zulip-icon-check"));

    rendered.querySelector('[aria-label="Copy log block 2"]').click();
    await Promise.resolve();
    assert.equal(copied_text, "one\ntwo");

    const expand = rendered.querySelector('[aria-label="Expand media 1"]');
    assert.ok(expand.querySelector(".zulip-icon-expand-both-diagonals"));
    await expand.click();
    await Promise.resolve();
    assert.equal(fullscreen_count, 1);
    const download = rendered.querySelector('[aria-label="Download media 1"]');
    assert.equal(download.getAttribute("href"), "/user_uploads/1/ab/diagram.png");
    assert.ok(download.querySelector(".zulip-icon-download"));

    const raw_toggle = rendered.querySelector('[aria-label="View raw artifact JSON"]');
    assert.ok(raw_toggle.querySelector(".zulip-icon-code"));
    const raw = rendered.querySelector(".pear-artifact-raw-json");
    assert.equal(raw.textContent, "");
    raw_toggle.click();
    assert.equal(raw.hidden, false);
    assert.equal(raw_toggle.getAttribute("aria-expanded"), "true");
    assert.equal(raw_toggle.getAttribute("aria-label"), "Hide raw artifact JSON");
    assert.equal(raw_toggle.textContent, "");
    assert.match(raw.textContent, /"version": 1/);
});

run_test("plays cumulative semantic scene steps with local controls", () => {
    const document = valid_document();
    document.blocks[1].root.children[2].state = "neutral";
    document.blocks[1].steps = [
        {
            label: "Start at A",
            changes: [{target: "a", state: "running", label: "A  0"}],
        },
        {
            label: "Relax A to B",
            changes: [
                {target: "a", state: "success"},
                {target: "force-edge", state: "selected"},
                {target: "b", state: "running", label: "B  1"},
            ],
        },
    ];
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    const a = rendered.querySelector('[data-pear-artifact-target="a"]');
    const b = rendered.querySelector('[data-pear-artifact-target="b"]');
    const edge = () => rendered.querySelector('[data-pear-artifact-target="force-edge"]');
    const status = rendered.querySelector(".pear-artifact-step-status");
    assert.equal(a.dataset.state, "running");
    assert.equal(a.textContent, "A  0");
    assert.equal(edge().dataset.state, "neutral");
    assert.equal(status.textContent, "1/2  Start at A");
    assert.match(
        rendered.querySelector(".pear-artifact-step-announcement").textContent,
        /Step 1 of 2: Start at A\. a is running\. a label: A {2}0\./,
    );
    assert.equal(
        rendered.querySelector(".pear-artifact-scene").getAttribute("aria-describedby"),
        rendered.querySelector(".pear-artifact-step-announcement").id,
    );
    assert.equal(rendered.querySelector(".pear-artifact-scene").getAttribute("role"), "group");
    const track = rendered.querySelector(".pear-artifact-step-track");
    assert.equal(track.getAttribute("role"), "slider");
    assert.equal(track.getAttribute("aria-valuemin"), "1");
    assert.equal(track.getAttribute("aria-valuemax"), "2");
    assert.equal(track.getAttribute("aria-valuenow"), "1");
    assert.equal(track.getAttribute("aria-valuetext"), "Start at A");

    rendered.querySelector('[aria-label="Next step"]').click();
    assert.equal(a.dataset.state, "success");
    assert.equal(b.dataset.state, "running");
    assert.equal(b.textContent, "B  1");
    assert.equal(edge().dataset.state, "selected");
    assert.equal(status.textContent, "2/2  Relax A to B");
    assert.equal(track.getAttribute("aria-valuenow"), "2");

    track.dispatchEvent(new window.KeyboardEvent("keydown", {key: "Home", bubbles: true}));
    assert.equal(status.textContent, "1/2  Start at A");
    track.dispatchEvent(new window.KeyboardEvent("keydown", {key: "End", bubbles: true}));
    assert.equal(status.textContent, "2/2  Relax A to B");

    rendered.querySelector('[aria-label="Previous step"]').click();
    assert.equal(a.dataset.state, "running");
    assert.equal(b.dataset.state, undefined);
    assert.equal(b.textContent, "B");
    assert.equal(edge().dataset.state, "neutral");
});

run_test("creates and resets labels on initially unlabeled shapes", () => {
    const document = valid_document();
    delete document.blocks[1].root.children[0].label;
    document.blocks[1].steps = [
        {label: "Name A", changes: [{target: "a", label: "A  0"}]},
        {label: "Update A", changes: [{target: "a", label: "A  1"}]},
    ];
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(rendered.querySelector('[data-pear-artifact-target="a"]').textContent, "A  0");
    rendered.querySelector('[aria-label="Next step"]').click();
    assert.equal(rendered.querySelector('[data-pear-artifact-target="a"]').textContent, "A  1");
    rendered.querySelector('[aria-label="Previous step"]').click();
    assert.equal(rendered.querySelector('[data-pear-artifact-target="a"]').textContent, "A  0");
});

run_test("rejects scene steps with missing or empty targets", () => {
    const document = valid_document();
    document.blocks[1].steps = [
        {label: "One", changes: [{target: "missing", state: "running"}]},
        {label: "Two", changes: [{target: "a", state: "success"}]},
    ];
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);

    document.blocks[1].steps[0].changes[0] = {target: "a"};
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);

    document.blocks[1].steps[0].changes[0] = {target: "a", state: "warning"};
    document.blocks[1].root.children.push({type: "text", id: "caption", text: "caption"});
    document.blocks[1].steps[0].changes[0] = {target: "caption", state: "warning"};
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
});

run_test("owns autoplay and connector observers across remounts", () => {
    const document = valid_document();
    document.blocks[1].steps = [
        {label: "Start", changes: [{target: "a", state: "running"}]},
        {label: "Finish", changes: [{target: "a", state: "success"}]},
    ];
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const host = window.document.querySelector("#host");
    const first = artifact.mount_pear_artifact($(host), parsed, {density: "chat"});
    const first_intersection = intersection_observers.at(-1);
    const first_resize = resize_observers.at(-1);
    first_intersection.set_visible(true);
    assert.equal(first.querySelector(".is-play").dataset.playing, "true");

    const before_step = frame_count;
    first.querySelector('[aria-label="Next step"]').click();
    assert.ok(frame_count > before_step);

    artifact.mount_pear_artifact($(host), parsed, {density: "chat"});
    assert.equal(first_intersection.disconnected, true);
    assert.equal(first_resize.disconnected, true);
    assert.equal(first.querySelector(".is-play").dataset.playing, "false");

    reduced_motion = true;
    for (const listener of motion_listeners) {
        listener();
    }
    const latest_intersection = intersection_observers.at(-1);
    latest_intersection.set_visible(true);
    assert.equal(host.querySelector(".is-play").dataset.playing, "false");
    assert.equal(host.querySelector(".pear-artifact-step-fill").style.transition, "none");
    reduced_motion = false;
});

run_test("reports mounted geometry changes and stops observing on unmount", () => {
    const parsed = artifact.parse_pear_artifact(JSON.stringify(valid_document()));
    assert.ok(parsed);
    const host = window.document.querySelector("#host");
    const changes = [];
    const rendered = artifact.mount_pear_artifact($(host), parsed, {
        density: "chat",
        onGeometryChange(element, previous_height, next_height) {
            changes.push({element, previous_height, next_height});
        },
    });
    const observer = resize_observers.find(({target}) => target === rendered);
    assert.ok(observer);
    observer.set_height(240);
    observer.set_height(240);
    observer.set_height(320);
    assert.deepEqual(changes, [{element: rendered, previous_height: 240, next_height: 320}]);

    artifact.mount_pear_artifact($(host), parsed, {density: "chat"});
    assert.equal(observer.disconnected, true);
});

run_test("uses intrinsic minimum geometry when an aspect was not authored", () => {
    const scene_document = valid_document();
    delete scene_document.blocks[1].aspect;
    const parsed_scene = artifact.parse_pear_artifact(JSON.stringify(scene_document));
    assert.ok(parsed_scene);
    const rendered_scene = artifact.render_pear_artifact(parsed_scene, {density: "chat"});
    assert.equal(rendered_scene.querySelector(".pear-artifact-scene").dataset.aspect, undefined);

    const media_document = {
        version: 1,
        blocks: [
            {
                type: "media",
                kind: "image",
                src: "/user_uploads/1/ab/diagram.png",
                alt: "Diagram without authored dimensions.",
            },
        ],
    };
    const parsed_media = artifact.parse_pear_artifact(JSON.stringify(media_document));
    assert.ok(parsed_media);
    const rendered_media = artifact.render_pear_artifact(parsed_media, {density: "chat"});
    const media = rendered_media.querySelector(".pear-artifact-media");
    const image = media.querySelector(".pear-artifact-media-content");
    const frame = media.querySelector(".pear-artifact-media-frame");
    assert.equal(media.dataset.aspect, undefined);
    Object.defineProperties(image, {
        naturalWidth: {value: 1000},
        naturalHeight: {value: 700},
    });
    image.dispatchEvent(new window.Event("load"));
    assert.equal(media.dataset.intrinsicAspect, "standard");
    assert.equal(frame.style.getPropertyValue("--pear-artifact-media-ratio"), `${1000 / 700}`);
});

run_test("lays out undirected graph branches by topological distance", () => {
    const document = valid_document();
    document.blocks[1].root.children = [
        {type: "shape", id: "a", shape: "circle", label: "A"},
        {type: "shape", id: "b", shape: "circle", label: "B"},
        {type: "shape", id: "c", shape: "circle", label: "C"},
        {type: "shape", id: "d", shape: "circle", label: "D"},
        {type: "connector", from: "a", to: "b", directed: false},
        {type: "connector", from: "a", to: "c", directed: false},
        {type: "connector", from: "b", to: "d", directed: false},
        {type: "connector", from: "c", to: "d", directed: false},
    ];
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="a"]')), "1");
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="b"]')), "2");
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="c"]')), "2");
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="d"]')), "3");
    assert.notEqual(
        graph_row(rendered.querySelector('[data-pear-artifact-node="b"]')),
        graph_row(rendered.querySelector('[data-pear-artifact-node="c"]')),
    );
});

run_test("stacks dense explanatory chains instead of squeezing them into one row", () => {
    const document = valid_document();
    document.blocks[1].root.children = [
        {type: "shape", id: "a", shape: "circle", label: "x_k"},
        {type: "shape", id: "b", shape: "pill", label: "compute the ambient gradient"},
        {type: "shape", id: "c", shape: "pill", label: "project onto the tangent space"},
        {type: "shape", id: "d", shape: "pill", label: "retract onto the manifold"},
        {type: "connector", from: "a", to: "b", directed: true},
        {type: "connector", from: "b", to: "c", directed: true},
        {type: "connector", from: "c", to: "d", directed: true},
    ];
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    const graph = rendered.querySelector('.pear-artifact-group[data-layout="graph"]');
    assert.equal(graph.dataset.graphDirection, "vertical");
    assert.equal(graph.style.getPropertyValue("--pear-artifact-graph-columns"), "1");
    assert.equal(graph.style.getPropertyValue("--pear-artifact-graph-rows"), "4");
    assert.equal(graph_row(rendered.querySelector('[data-pear-artifact-node="a"]')), "1");
    assert.equal(graph_row(rendered.querySelector('[data-pear-artifact-node="d"]')), "4");
});

run_test("falls back to distance layout for directed cycles", () => {
    const document = valid_document();
    document.blocks[1].root.children = [
        {type: "shape", id: "a", shape: "circle", label: "A"},
        {type: "shape", id: "b", shape: "circle", label: "B"},
        {type: "shape", id: "c", shape: "circle", label: "C"},
        {type: "connector", from: "a", to: "b", directed: true},
        {type: "connector", from: "b", to: "c", directed: true},
        {type: "connector", from: "c", to: "a", directed: true},
    ];
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="a"]')), "1");
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="b"]')), "2");
    assert.equal(graph_column(rendered.querySelector('[data-pear-artifact-node="c"]')), "2");
});

run_test("orders graph layers to reduce connector crossings", () => {
    const document = valid_document();
    document.blocks[1].root.children = [
        {type: "shape", id: "a", shape: "circle", label: "A"},
        {type: "shape", id: "b", shape: "circle", label: "B"},
        {type: "shape", id: "c", shape: "circle", label: "C"},
        {type: "shape", id: "d", shape: "circle", label: "D"},
        {type: "shape", id: "e", shape: "circle", label: "E"},
        {type: "connector", from: "a", to: "b", directed: false},
        {type: "connector", from: "a", to: "c", directed: false},
        {type: "connector", from: "b", to: "d", directed: false},
        {type: "connector", from: "c", to: "d", directed: false},
        {type: "connector", from: "b", to: "e", directed: false},
    ];
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(graph_row(rendered.querySelector('[data-pear-artifact-node="e"]')), "1");
    assert.equal(graph_row(rendered.querySelector('[data-pear-artifact-node="d"]')), "2");
});

run_test("renders bounded repeat data and keeps interactions local", () => {
    const document = {
        version: 1,
        data: {items: ["alpha", "beta", "gamma"]},
        blocks: [
            {
                type: "scene",
                label: "Three values",
                root: {
                    type: "text",
                    bind: "data.item",
                    repeat: {source: "data.items", limit: 3},
                    interaction: "select",
                    ariaLabel: "Select value",
                },
            },
        ],
    };
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    const values = [...rendered.querySelectorAll(".pear-artifact-text")];
    assert.deepEqual(
        values.map((node) => node.textContent),
        ["alpha", "beta", "gamma"],
    );
    values[0].click();
    assert.equal(values[0].getAttribute("aria-pressed"), "true");

    document.blocks[0].root.id = "unsafe-repeat-id";
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
});

run_test("renders every accepted numeric field without leaking invalid live values", () => {
    const document = {
        version: 1,
        data: {bad: "not-a-number"},
        blocks: [
            {type: "metric", label: "accuracy", value: 94, delta: 2, unit: "%"},
            {type: "meter", label: "progress", value: 4, total: 8, unit: "steps"},
            {type: "series", label: "signal", values: [-2, 0, 3], unit: "ms"},
            {
                type: "scene",
                label: "safe derivation",
                root: {
                    type: "group",
                    layout: "column",
                    children: [
                        {
                            type: "text",
                            text: "unavailable",
                            derive: {op: "divide", args: ["data.bad", 0]},
                        },
                        {
                            type: "control",
                            control: "stepper",
                            label: "tick",
                            action: "advance",
                            value: 1,
                            max: 2,
                        },
                        {type: "mark", mark: "point", values: [-1, 0, 2]},
                        {type: "mark", mark: "area", values: [-1, 0, 2]},
                        {type: "mark", mark: "arc", values: [1, 2, 3]},
                    ],
                },
            },
        ],
    };
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(rendered.querySelector(".pear-artifact-delta").textContent, "+2");
    assert.equal(rendered.querySelector(".pear-artifact-meter-meta").textContent, "4 / 8 steps");
    assert.equal(rendered.querySelector(".pear-artifact-series-unit").textContent, "ms");
    assert.equal(rendered.textContent.includes("NaN"), false);
    assert.equal(rendered.querySelector(".pear-artifact-text").textContent, "unavailable");
    assert.equal(rendered.querySelectorAll(".pear-artifact-series-point").length, 3);
    assert.equal(rendered.querySelectorAll(".pear-artifact-series-area").length, 1);
    assert.equal(rendered.querySelectorAll(".pear-artifact-series-arc").length, 3);
    const stepper = rendered.querySelector(".pear-artifact-control-button");
    assert.equal(stepper.dataset.action, "advance");
    stepper.click();
    stepper.click();
    assert.equal(stepper.textContent, "tick  2");
});
