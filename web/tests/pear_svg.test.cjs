"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>");
const {window} = dom;
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    DOMParser: window.DOMParser,
    Element: window.Element,
    SVGElement: window.SVGElement,
})) {
    set_global(name, value);
}

const {sanitize_pear_svg, PEAR_SVG_SOURCE_LIMIT} = zrequire("pear_svg");

const REJECTED = undefined;

// ---- hostile fixtures: whole-document rejection ----

run_test("script element anywhere rejects", () => {
    assert.equal(
        sanitize_pear_svg('<svg viewBox="0 0 10 10"><script>alert(1)</script></svg>'),
        REJECTED,
    );
});

run_test("script nested inside a dropped subtree still rejects", () => {
    assert.equal(
        sanitize_pear_svg(
            '<svg viewBox="0 0 10 10"><metadata><script>alert(1)</script></metadata></svg>',
        ),
        REJECTED,
    );
});

run_test("foreignObject rejects", () => {
    assert.equal(
        sanitize_pear_svg(
            '<svg viewBox="0 0 10 10"><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject></svg>',
        ),
        REJECTED,
    );
});

run_test("event handler attribute rejects, even on a dropped element", () => {
    assert.equal(
        sanitize_pear_svg('<svg viewBox="0 0 10 10"><image onerror="alert(1)"/></svg>'),
        REJECTED,
    );
    assert.equal(
        sanitize_pear_svg('<svg viewBox="0 0 10 10" onload="alert(1)"><rect/></svg>'),
        REJECTED,
    );
});

run_test("javascript: URL anywhere rejects", () => {
    assert.equal(
        sanitize_pear_svg(
            '<svg viewBox="0 0 10 10"><a href="javascript:alert(1)"><text>x</text></a></svg>',
        ),
        REJECTED,
    );
    assert.equal(
        sanitize_pear_svg(
            '<svg viewBox="0 0 10 10"><a href="java\nscript:alert(1)"><text>x</text></a></svg>',
        ),
        REJECTED,
    );
});

run_test("data: URL anywhere rejects", () => {
    assert.equal(
        sanitize_pear_svg(
            '<svg viewBox="0 0 10 10"><image href="data:image/svg+xml,<svg onload=alert(1)>"/></svg>',
        ),
        REJECTED,
    );
});

run_test("DOCTYPE / entity machinery rejects", () => {
    assert.equal(
        sanitize_pear_svg(
            '<!DOCTYPE svg [<!ENTITY x "y">]><svg viewBox="0 0 10 10"><text>&x;</text></svg>',
        ),
        REJECTED,
    );
});

run_test("external url() paint reference rejects", () => {
    assert.equal(
        sanitize_pear_svg(
            '<svg viewBox="0 0 10 10"><rect width="5" height="5" fill="url(https://evil.example/p.svg#f)"/></svg>',
        ),
        REJECTED,
    );
});

run_test("malformed XML rejects", () => {
    assert.equal(sanitize_pear_svg('<svg viewBox="0 0 10 10"><rect'), REJECTED);
    assert.equal(sanitize_pear_svg("<div>not svg</div>"), REJECTED);
});

run_test("oversize source rejects", () => {
    const big = `<svg viewBox="0 0 10 10"><desc>${"x".repeat(PEAR_SVG_SOURCE_LIMIT)}</desc></svg>`;
    assert.equal(sanitize_pear_svg(big), REJECTED);
});

run_test("missing coordinate system rejects", () => {
    assert.equal(sanitize_pear_svg("<svg><rect width='5' height='5'/></svg>"), REJECTED);
});

// ---- benign-but-unsupported: silent drops, document survives ----

run_test("unknown elements and attributes drop silently", () => {
    const svg = sanitize_pear_svg(
        '<svg viewBox="0 0 10 10"><filter id="f"/><rect width="5" height="5" data-custom="x" style="position:fixed"/></svg>',
    );
    assert.notEqual(svg, REJECTED);
    assert.equal(svg.querySelector("filter"), null);
    const rect = svg.querySelector("rect");
    assert.notEqual(rect, null);
    assert.equal(rect.hasAttribute("style"), false);
    assert.equal(rect.hasAttribute("data-custom"), false);
});

run_test("only catalogued psvg class tokens survive", () => {
    const svg = sanitize_pear_svg(
        '<svg viewBox="0 0 10 10"><rect class="psvg-accent psvg-invented sneaky-app-class" width="5" height="5"/></svg>',
    );
    const rect = svg.querySelector("rect");
    assert.equal(rect.getAttribute("class"), "psvg-accent");
});

run_test("duplicate ids reject the ambiguous document", () => {
    assert.equal(
        sanitize_pear_svg(
            '<svg viewBox="0 0 10 10"><rect id="same"/><circle id="same" r="2"/></svg>',
        ),
        REJECTED,
    );
});

run_test("event-based SMIL begin is dropped (no click handlers via animation)", () => {
    const svg = sanitize_pear_svg(
        '<svg viewBox="0 0 10 10"><rect width="5" height="5"><animate attributeName="opacity" begin="click" dur="1s" values="0;1"/></rect></svg>',
    );
    const animate = svg.querySelector("animate");
    assert.notEqual(animate, null);
    assert.equal(animate.hasAttribute("begin"), false);
    assert.equal(animate.getAttribute("dur"), "1s");
});

run_test("SMIL cannot animate href", () => {
    const svg = sanitize_pear_svg(
        '<svg viewBox="0 0 10 10"><defs><rect id="a" width="5" height="5"/></defs><use href="#a"><animate attributeName="href" to="#b" dur="1s"/></use></svg>',
    );
    const animate = svg.querySelector("animate");
    assert.equal(animate.hasAttribute("attributeName"), false);
});

// ---- the real thing: a living diagram survives intact ----

run_test("gradient + use + text + SMIL diagram rebuilds with rewritten ids", () => {
    const source = `<svg viewBox="0 0 120 40" width="120" height="40">
      <defs>
        <linearGradient id="g1" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="#9be29b"/>
          <stop offset="1" stop-color="#2e8b57"/>
        </linearGradient>
        <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0 0L6 3L0 6z" class="psvg-accent"/>
        </marker>
        <symbol id="node" viewBox="0 0 20 12">
          <rect width="20" height="12" rx="2" fill="url(#g1)"/>
        </symbol>
      </defs>
      <use href="#node" x="4" y="14"/>
      <line x1="26" y1="20" x2="60" y2="20" marker-end="url(#arrow)" class="psvg-hairline psvg-flow"/>
      <text x="64" y="24" class="psvg-label">queue<tspan dx="2" class="psvg-ink2">ok</tspan></text>
      <rect x="100" y="14" width="12" height="12">
        <animate attributeName="opacity" values="1;0.3;1" dur="1.8s" repeatCount="indefinite"/>
      </rect>
    </svg>`;
    const svg = sanitize_pear_svg(source);
    assert.notEqual(svg, REJECTED);
    assert.equal(svg.namespaceURI, "http://www.w3.org/2000/svg");
    assert.equal(svg.getAttribute("role"), "img");
    assert.match(svg.getAttribute("class"), /^psvg\b/);

    const gradient = svg.querySelector("linearGradient");
    assert.match(gradient.getAttribute("id"), /^psvg-i\d+-g1$/);
    const symbol_rect = svg.querySelector("symbol rect");
    assert.equal(symbol_rect.getAttribute("fill"), `url(#${gradient.getAttribute("id")})`);
    const use = svg.querySelector("use");
    assert.equal(use.getAttribute("href"), `#${svg.querySelector("symbol").getAttribute("id")}`);
    const line = svg.querySelector("line");
    assert.equal(
        line.getAttribute("marker-end"),
        `url(#${svg.querySelector("marker").getAttribute("id")})`,
    );
    assert.equal(svg.querySelector("text").textContent, "queueok");
    assert.equal(svg.querySelector("animate").getAttribute("repeatCount"), "indefinite");
});

run_test("xlink:href is accepted and normalized to href", () => {
    const svg = sanitize_pear_svg(
        '<svg viewBox="0 0 10 10" xmlns:xlink="http://www.w3.org/1999/xlink"><defs><rect id="a" width="5" height="5"/></defs><use xlink:href="#a"/></svg>',
    );
    assert.notEqual(svg, REJECTED);
    const use = svg.querySelector("use");
    assert.match(use.getAttribute("href"), /^#psvg-i\d+-a$/);
    assert.equal(use.hasAttribute("xlink:href"), false);
});

run_test("dangling fragment reference drops the referencing element", () => {
    const svg = sanitize_pear_svg(
        '<svg viewBox="0 0 10 10"><use href="#nowhere"/><rect width="5" height="5"/></svg>',
    );
    assert.notEqual(svg, REJECTED);
    assert.equal(svg.querySelector("use"), null);
    assert.notEqual(svg.querySelector("rect"), null);
});

run_test("two instances get distinct id prefixes", () => {
    const source =
        '<svg viewBox="0 0 10 10"><defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs><rect width="5" height="5" fill="url(#g)"/></svg>';
    const first = sanitize_pear_svg(source);
    const second = sanitize_pear_svg(source);
    const first_id = first.querySelector("linearGradient").getAttribute("id");
    const second_id = second.querySelector("linearGradient").getAttribute("id");
    assert.notEqual(first_id, second_id);
    assert.equal(first.querySelector("rect").getAttribute("fill"), `url(#${first_id})`);
    assert.equal(second.querySelector("rect").getAttribute("fill"), `url(#${second_id})`);
});

run_test("node budget rejects runaway documents", () => {
    const rects = '<rect width="1" height="1"/>'.repeat(4001);
    assert.equal(sanitize_pear_svg(`<svg viewBox="0 0 10 10">${rects}</svg>`), REJECTED);
});
