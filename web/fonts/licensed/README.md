# The licensed typeface

Armada's human face is Söhne, by Klim Type Foundry. It is licensed, so its
files are never committed: everything in this directory except this file is
ignored by git.

Put the four files the design uses here, as `.woff2`:

| File                       | Used as          |
| -------------------------- | ---------------- |
| `soehne-buch.woff2`        | Söhne 400        |
| `soehne-buch-kursiv.woff2` | Söhne 400 italic |
| `soehne-kraftig.woff2`     | Söhne 500        |
| `soehne-halbfett.woff2`    | Söhne 600        |

A `test-` or `web-` prefix is accepted (`test-soehne-buch.woff2`,
`soehne-web-buch.woff2`); every other weight is ignored.

`tools/setup/build_armada_fonts` reads this directory, or the one named by
the `ARMADA_FONT_DIR` environment variable, and writes
`web/generated/armada_fonts.css`. It runs in `tools/provision`, in
`tools/update-prod-static` and whenever webpack starts a build, so dropping
the files in and rebuilding is the whole installation. The app's template
preloads whichever of them the build found.

With no files here the app is set in Hanken Grotesk, the next face of the
stack, and nothing else changes. Klim's free test files work too; they hold
only A to Z, a to z, 0 to 9, the full stop, the comma and the hyphen, so
every other glyph falls through to Hanken Grotesk.
