# web/src/armada/beta — INTERIM

The hosted beta at `/beta/`: the design's prototype (`var/armada-overhaul/design/final3/mock/`) running on this
server's real data, for the team to use every day ahead of the rebuild. It is not the rebuild and is not written to
its rules: eslint, stylelint, prettier and the custom checks ignore this directory. Nothing outside it imports it.
Delete it when the rebuild's conversation lands.

- `index.js`: the build entry (`armada-beta` in `web/webpack.assets.json`); imports the faces, the kit and the page.
- `kit/`: the prototype's `mock.css` (its `@font-face` lines removed; the app's faces are imported instead),
  `proto.css`, `glyphs.js`, `kit.js`, `motion.js`, `sound.js`. Each script sets window globals (`ic`, `mk`,
  `frame`, `M`, `S`, ...).
- `beta.js`: the page. Reads and writes go to `/json/` with the session cookie and the page's CSRF token.
- `beta.css`: what real data needs beyond the kit. Tokens only.

Served by `zerver/views/armada_beta.py` (`@zulip_login_required`) through `templates/zerver/armada_beta.html`.

To delete the beta, remove all of these in one change:

- this directory
- `zerver/views/armada_beta.py`, `templates/zerver/armada_beta.html`, `zerver/tests/test_armada_beta.py`
- in `zproject/urls.py`, the import of `armada_beta` and the `path("beta/", ...)` line with its comment
- in `web/webpack.assets.json`, the `"armada-beta"` entry
- in `web/templates/navbar.hbs`, the `armada-beta-link` anchor and its comment
- the `web/src/armada/beta` lines (and their comments) in `eslint.config.js`, `stylelint.config.js`
  (`ignoreFiles`), `.prettierignore` and `tools/linter_lib/exclude.py`
- `tools/armada/fork_ledger.d/beta.txt`, and the `beta/` lines in `docs/armada/architecture.md`
  ("Directories"), in `.claude/CLAUDE.md` ("Where the build is") and in `.claude/rules/fork-boundary.md`

`git grep --untracked -n -e "INTERIM BETA" -e armada-beta -e armada_beta -e armada/beta` finds every one of them.
