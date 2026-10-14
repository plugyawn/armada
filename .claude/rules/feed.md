---
paths:
  - "web/src/armada/feed/**"
  - "web/src/message_*.ts"
  - "web/src/echo*.ts"
  - "web/src/unread*.ts"
  - "tools/armada-checks/**"
---

# The feed

The conversation will be `web/src/armada/feed/`: an imperative keyed renderer
in plain TypeScript (C1 and C2 of the plan; today the directory holds only its
README). Until it passes the behaviour pins, the old `message_list_view.ts`
stays, hosted in the new frame from F5; the list model (`message_list.ts`,
`message_list_data.ts`) and the read flags stay for good.

- The must-keep behaviours of today's feed are pinned and may not change:
  selection keys, recentring, read marking, anchoring, autoscroll, history
  fetches, Back and Forward, local echo, Return and Esc.
  Enforced by: `arm check feed-semantics echo-semantics keyboard-semantics`.
- A pin that moves to new code keeps its proof: write the equivalent
  breakage and run `tools/armada-checks/lib/prove.mjs <names>` in the VM
  (the command is in its header).
  Enforced by: `prove.mjs` (it reports a mutation that no longer matches).
- No Preact, jQuery or Handlebars in the feed; elements are built with
  `kit/dom.ts`. Enforced by: eslint `@typescript-eslint/no-restricted-imports`
  and `import/no-restricted-paths` ("The feed imports kit/dom.ts, state and
  motion only").
- A row is never replaced and never removed: the element created at send is
  the element that holds the answer. Enforced by: `arm check send-cost`
  (`plain.row-replaced-by-server-echo`, `plain.content-replaced-by-server-echo`)
  and `stream-cost` (`ack.same-element`).
- My message, the empty field and the agent's row all exist in the keydown
  task, with no layout read and no request first. Enforced by:
  `arm check send-cost` (`plain.row-in-first-frame`, `*.row-before-server`,
  `plain.enter-forced-layouts`).
- Streamed text is written once a frame, forces no layout, and no word waits
  more than two frames. Enforced by: `arm check stream-cost`.
- HTML strings enter the DOM only in `feed/parts.ts` and
  `feed/stream_writer.ts`; the server's HTML is the final content.
  Enforced by: eslint `no-restricted-syntax` ("HTML strings enter the DOM
  only in"); `arm check echo-semantics` (`echo.server-html-final`).
- Client and server Markdown split into the same bubbles and blocks.
  Enforced by: `arm check parts-corpus`.
- One scroll controller (`feed/scroll.ts`); it is the only feed file that
  reads layout. Enforced by: eslint `no-restricted-syntax` ("No layout read
  here"). That it has one ResizeObserver is review.
- No timers in the feed except the two named thresholds (`pending`, `quiet`).
  Enforced by: eslint `no-restricted-syntax` ("No timers in the feed").
- A topic revisit renders no row again. Enforced by:
  `arm check topic-switch-cost` (`*.rows-rendered-again`).
- `feed/parts.ts` is pure and fully covered. Enforced by:
  `tools/test-js-with-node --coverage` (`ARMADA_FULLY_COVERED_GLOBS`).
