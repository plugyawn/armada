# Naming

**Armada** is the product and the home of new code: `web/src/armada/`,
`armada-` classes, `docs/armada/`, `tools/armada*`; it is also the server and
wire namespace (`zerver/lib/armada.py`, `/json/armada/`, `ARMADA_*`
settings), where a plain noun does not fit better (`AgentRun`, `Repository`,
`agent_run`). **Onix** is the agent, to people only; the server holds the
name in one constant, `ARMADA_AGENT_NAME`. The old interface, which the
rebuild deletes, says `legacy` (`web/src/legacy_*.ts`, `.legacy-*` classes),
so that `armada-` stays the kit's. **Zulip** appears nowhere in the
interface. This file applies to every change.

- No file, class, token or identifier is named after the agent (it has
  been renamed twice): code says `agent`, `run`, `worktree`, `repository`.
  Enforced by: eslint `no-restricted-syntax` and `armada/file-name`
  ("The agent's name is never a literal": strings, identifiers, JSX names
  and script file names); custom check "No class, token or attribute" for
  stylesheets. Both also refuse Onyx, the agent's earlier name; `tokens/` is
  exempt, because one palette is called Onyx, after the stone. A
  stylesheet's own file name is not checked.
- The old code name is gone (statement 24, package R1) and does not come
  back, in code, strings, settings, routes, docs or file names: say
  `armada`, or `agent`, `run`, `worktree`, `repository`. Only history keeps
  it: the migrations that created the agent's tables and those that renamed
  them, the git branch on GitHub (only the user can rename it), and a fruit
  and a PHP library in Zulip's emoji data and test fixtures. Enforced by:
  custom check "The old code name is gone" (every file `tools/lint` lists),
  eslint `no-restricted-syntax` and `armada/file-name` under
  `web/src/armada/` ("The old code name is gone"); a word that merely
  contains the letters, such as "Appearance", passes. Nothing checks file
  names outside `web/src/armada/`.
- Classes are `armada-<component>` or `armada-<component>-<part>`, flat and
  spelled out. Enforced by: stylelint `selector-class-pattern`.
- State is an attribute (`aria-current`, `aria-pressed`, `data-state`,
  `data-side`), never a second class, so it has one spelling for CSS, tests
  and assistive technology. Enforced by: stylelint `selector-max-specificity`
  and review.
- Tokens keep the design's names exactly (`--ink-1`, `--fill-2`,
  `--r-bubble`, `--spring-move`, `--col`); do not invent a synonym.
  Enforced by: F3's token test (`web/tests/armada/tokens.test.cjs`), a diff
  of names and values against the design.
- One name, one place: a surface's name is its directory, its stylesheet,
  its `commands.ts` and its showroom section. Enforced by: review; the
  frame package creates the seams once.
- Files are `snake_case`; components are `PascalCase`; functions and
  variables follow the surrounding Zulip style (`snake_case`).
  Enforced by: review (eslint `new-cap` sees constructors only).
- Check hooks are `data-armada="<name>"`, named in
  `tools/armada-checks/lib/selectors.mjs`. Enforced by: `arm check` (a
  selector that matches nothing reports "not measurable").
- A command has one name and one key everywhere, from
  `commands/registry.ts` (A5's; not built yet). Enforced by: nothing yet;
  `arm check keyboard-semantics` pins today's keys.
- Names are clear and greppable; the full word `armada` is used because
  shorter prefixes match inside existing names. Enforced by: review.
- A glyph or a mark is used by its generated name. Enforced by: TypeScript
  (`kit/icons/names.ts`, a union of the sprite's ids).
