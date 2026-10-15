---
paths:
  - "web/src/armada/state/**"
  - "web/src/armada/api/**"
  - "web/src/armada/commands/**"
  - "web/src/armada/surfaces/**"
  - "web/src/server_event*"
  - "zerver/**"
---

# State and API

`state/`, `api/` and `commands/` hold only their READMEs today; A1 of the
plan builds them. The fences below are already lint errors.

- One small store per domain in `web/src/armada/state/`. Views read stores
  and emit intents; they never keep their own copy of server data.
  Enforced by: eslint `import/no-restricted-paths` (a surface may import
  `state/` but not `api/`); that a view keeps no copy is review.
- Stores import no DOM module: of Armada's only `state/` and `api/`, and
  nothing of the old app. Enforced by: eslint `import/no-restricted-paths`
  ("Stores import no DOM module"). A package from `node_modules` is not
  fenced.
- Only `web/src/armada/api/` touches the network. Enforced by: eslint
  `import/no-restricted-paths` (nothing else imports `web/src/channel.ts`)
  and `no-restricted-syntax` (`fetch`, `sendBeacon`, `XMLHttpRequest`,
  `WebSocket`, `EventSource` by those names; an alias is not seen).
- Every response has a schema in `api/schemas.ts`, written with `zod/mini`.
  Enforced by: eslint `@typescript-eslint/no-restricted-imports` (plain
  `zod` is banned); that every response has one is review.
- `api/` translates the server's wire names (`armada_*` keys,
  `/json/armada/` routes) into the words the rest of the tree uses: agent,
  run, worktree, repository. Enforced by: review.
- The agent's name is never a literal; it arrives from the server and
  strings say `{agent}`. Enforced by: eslint `no-restricted-syntax`
  ("The agent's name is never a literal").
- Stores, the API layer and the command registry are pure and covered to
  100% from their first file, `.ts` and `.tsx`. Enforced by:
  `tools/test-js-with-node --coverage` (`ARMADA_FULLY_COVERED_GLOBS`).
- Runs are realm-wide state fed by the register snapshot and by events, not
  by polling. Enforced by: `arm check idle-cost` (`*.requests-per-minute`)
  and `topic-switch-cost` (`*.requests-per-switch`).
- A removed feature's event type leaves `server_event_types.ts` and its
  dispatch arm in the same change, with its tests. Enforced by: the node
  tests (`web/tests/dispatch.test.cjs`) and TypeScript.
- A send goes through the outbox, keyed by a client key; a failed send
  stays with Retry, and unsent text survives a reload. Enforced by:
  `arm check echo-semantics` (`echo.failed.stays-with-retry`,
  `echo.unsent.survives-reload`, `echo.rapid.order`).
- Server work for Armada stays in the `armada` namespace and has tests in
  `zerver/tests/`; run them one at a time
  (`flock /tmp/armada-backend.lock ./tools/test-backend <modules>`).
  Enforced by: `./tools/test-backend` and its coverage check.
