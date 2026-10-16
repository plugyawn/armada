# api

The only network code of the rebuild: `client.ts`, `schemas.ts` (zod/mini), and one file per resource. `plugin_contract_generated.ts` is the plugin contract's schemas, written by `tools/armada/artifact_contract.py`; never edit it. The server's wire names (`armada_*` keys, `/json/armada/` routes) are translated here into the words the rest of the tree uses (agent, run, worktree, repository).

Not here: anything that draws, and any caller of `channel` outside this directory.

To add one: copy a resource file, give its response a schema in `schemas.ts`, and cover it to 100% in `web/tests/armada/`.
