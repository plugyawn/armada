# hooks

The only modules of the rebuild that old Zulip files may import. `narrow.ts` is called from `message_view_header.ts` with the kind of place in front; `frame.ts` from `pear_workspace.ts` with the worktree's view in front; `events.ts` (A1) will be called first in `server_events_dispatch.js`; a `legacy_<area>.ts` would hold the old initialisers an area still needs. Each call site in an old file is one line marked `// armada:`.

Not here: logic. A hook forwards to `boot.ts`, a store or a surface and returns.

To add one: copy `narrow.ts`; record the old file as `hooked` in `tools/armada/fork_ledger.d/<package>.txt`. A `legacy_*.ts` file is emptied by the package that replaces its area.
