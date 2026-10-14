# state

One small store per domain, built on `store.ts`: runs, worktrees, checklists, the route, preferences. Views read stores and emit intents; a store is changed by server events and by `api/` results.

Not here: the DOM, Preact, jQuery, or a network call. A store imports `api/` types and other stores only.

To add one: copy the smallest store, keep it pure, and cover it to 100% in `web/tests/armada/`.
