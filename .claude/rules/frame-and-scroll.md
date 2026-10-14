---
paths:
  - "web/src/armada/**"
  - "web/styles/**"
  - "web/templates/**"
  - "templates/zerver/app/**"
---

# Frame and scroll

The frame is one CSS grid written once (F5 of the plan; `frame/` holds only
its README today). `docs/armada/architecture.md` ("Frame") has the reasoning
and `docs/armada/design-language.md` the measurements. All checks are
`limactl shell armada -- arm check <name>`.

- Nothing the person did not act on moves, by one pixel: header, views, nav
  rows, band, field, conversation and column stay within 0.5 px through
  every interaction. Enforced by: `arm check frame-stability`,
  `topic-entry-stability`, `sidebar-stability`, `interaction-stability`,
  `sidebar-toggle-stability` (which F5 replaces with `column-stability`).
- The document never scrolls. Each pane owns one native scroller.
  Enforced by: `arm check frame-stability` (`long.document-scroll.drift`,
  `long.document-height.drift`).
- The composition (conversation and column) is computed in CSS from the
  pane's width. No script writes a width, a breakpoint or `--col`,
  `--side-w`, `--comp`, `--lx`. Enforced by: nothing yet; F5 adds to
  `frame-stability` the rectangles at 1100, 1440 and 1920 and a resize with
  no script task.
- Layout is read only in the after-layout phase: `motion/`,
  `frame/after_layout.ts`, `feed/scroll.ts`, `surfaces/composer/origin.ts`.
  Never read a rectangle after a write inside an input task.
  Enforced by: eslint `no-restricted-syntax` ("No layout read here");
  `arm check typing-cost` and `send-cost` (`*.handler-forced-layouts`,
  `plain.enter-forced-layouts`).
- A value that changes sits in a cell as wide as its longest form, or is
  anchored to a far end; things that appear fill slots that already exist.
  Enforced by: `arm check interaction-stability`, `topic-entry-stability`.
- The first paint is the frame: no splash, no logo, no late font.
  Enforced by: `arm check startup` (`cumulative-layout-shift.median`;
  `late-font-requests`, known at 3: the two old icon fonts and Söhne's
  italic, loaded on use; `build/F3.md`).
- `z-index` has five named layers, all in `frame/frame.css`.
  Enforced by: stylelint `property-disallowed-list`.
- A popover or a sheet is placed in CSS from fixed cells, not by measuring
  its anchor in script. Enforced by: eslint `no-restricted-syntax`
  ("No layout read here").
- Old Zulip code scrolls inside the pane it is hosted in; nothing new reads
  `window.scrollY` or calls `window.scroll`. Enforced by: eslint ("No layout
  read here" covers `scrollX`, `scrollY` under `web/src/armada/`; a call to
  `window.scroll` is not seen); F5's acceptance grep for old code.
