# api

The only network code of the rebuild, and the only place the wire name `pear` may appear: `client.ts`, `schemas.ts` (zod/mini), and one file per resource. Server field names are translated here into the words the rest of the tree uses (agent, run, worktree, repository).

Not here: anything that draws, and any caller of `channel` outside this directory.

To add one: copy a resource file, give its response a schema in `schemas.ts`, and cover it to 100% in `web/tests/armada/`.
