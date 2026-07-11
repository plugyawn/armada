#!/usr/bin/env python3
"""Replace the Zulip production emoji-cache symlink with ordinary files."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path


class EmojiMaterializationError(RuntimeError):
    pass


def materialize_generated_emoji(
    build_root: Path,
    *,
    emoji_cache_root: Path = Path("/srv/zulip-emoji-cache"),
) -> bool:
    emoji_link = build_root / "static/generated/emoji"
    if not emoji_link.is_symlink():
        return False

    try:
        source = emoji_link.resolve(strict=True)
        cache_root = emoji_cache_root.resolve(strict=True)
        relative_source = source.relative_to(cache_root)
    except (OSError, ValueError) as error:
        raise EmojiMaterializationError(
            f"generated emoji link must resolve inside {emoji_cache_root}"
        ) from error

    if (
        len(relative_source.parts) != 2
        or relative_source.parts[1] != "static"
        or not source.is_dir()
    ):
        raise EmojiMaterializationError(
            "generated emoji link must target /srv/zulip-emoji-cache/<build>/static"
        )

    temporary = Path(tempfile.mkdtemp(prefix=".pear-emoji-", dir=emoji_link.parent))
    shutil.rmtree(temporary)
    try:
        # The cache includes aliases as symlinks.  Materialize every one here
        # while still running as the unprivileged build user; the root-owned
        # freezer remains fail-closed for every node in the resulting graph.
        shutil.copytree(source, temporary, symlinks=False, copy_function=shutil.copy2)
        if any(path.is_symlink() for path in temporary.rglob("*")):
            raise EmojiMaterializationError("materialized emoji cache still contains a symlink")
        emoji_link.unlink()
        os.replace(temporary, emoji_link)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument(
        "--emoji-cache-root",
        type=Path,
        default=Path("/srv/zulip-emoji-cache"),
    )
    args = parser.parse_args(argv)
    try:
        materialized = materialize_generated_emoji(
            args.build_root,
            emoji_cache_root=args.emoji_cache_root,
        )
    except EmojiMaterializationError as error:
        parser.error(str(error))
    if materialized:
        print("Materialized cached emoji assets for the frozen production graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
