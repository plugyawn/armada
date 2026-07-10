#!/usr/bin/env python3
"""Copy a production asset build into a root-owned, no-follow frozen tree."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import NoReturn

DIRECTORY_ROOTS = (
    "prod-static",
    "static/generated",
    "static/webpack-bundles",
    "static/images/landing-page/hello/generated",
    "web/generated",
)
FILE_PATHS = (
    "webpack-stats-production.json",
    "staticfiles.json",
    "locale/language_options.json",
    "locale/language_name_map.json",
)

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


class AssetFreezeError(RuntimeError):
    pass


def _fail(message: str) -> NoReturn:
    raise AssetFreezeError(message)


def _safe_mode(entry_stat: os.stat_result, *, label: str) -> int:
    mode = stat.S_IMODE(entry_stat.st_mode)
    if mode & 0o022:
        _fail(f"asset build entry is group- or world-writable: {label}")
    return mode


def _open_directory(path: Path) -> int:
    try:
        path_stat = path.lstat()
        descriptor = os.open(path, _DIRECTORY_FLAGS)
    except OSError as error:
        _fail(f"could not safely open asset directory {path}: {error}")
    opened_stat = os.fstat(descriptor)
    if (
        stat.S_ISLNK(path_stat.st_mode)
        or not stat.S_ISDIR(path_stat.st_mode)
        or not stat.S_ISDIR(opened_stat.st_mode)
        or opened_stat.st_dev != path_stat.st_dev
        or opened_stat.st_ino != path_stat.st_ino
    ):
        os.close(descriptor)
        _fail(f"asset directory changed while opening: {path}")
    _safe_mode(opened_stat, label=os.fspath(path))
    return descriptor


def _open_directory_chain(root_fd: int, relative_path: str) -> int:
    descriptor = os.dup(root_fd)
    try:
        for component in relative_path.split("/"):
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            child_stat = os.fstat(child)
            if not stat.S_ISDIR(child_stat.st_mode):
                os.close(child)
                _fail(f"asset path is not a directory: {relative_path}")
            _safe_mode(child_stat, label=relative_path)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, AssetFreezeError) as error:
        os.close(descriptor)
        if isinstance(error, AssetFreezeError):
            raise
        _fail(f"could not safely traverse asset directory {relative_path}: {error}")


def _ensure_target_directory(root_fd: int, relative_path: str, mode: int = 0o755) -> int:
    descriptor = os.dup(root_fd)
    try:
        for component in relative_path.split("/"):
            with suppress(FileExistsError):
                os.mkdir(component, mode, dir_fd=descriptor)
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as error:
        os.close(descriptor)
        _fail(f"could not create frozen asset directory {relative_path}: {error}")


def _copy_regular_file(
    source_dir_fd: int,
    target_dir_fd: int,
    name: str,
    *,
    label: str,
) -> None:
    try:
        before = os.stat(name, dir_fd=source_dir_fd, follow_symlinks=False)
    except OSError as error:
        _fail(f"could not stat asset file {label}: {error}")
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        _fail(f"asset file must be regular and single-link: {label}")
    mode = _safe_mode(before, label=label)
    try:
        source_fd = os.open(name, _FILE_FLAGS, dir_fd=source_dir_fd)
    except OSError as error:
        _fail(f"could not safely open asset file {label}: {error}")
    try:
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            _fail(f"asset file changed while opening: {label}")
        try:
            target_fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                mode,
                dir_fd=target_dir_fd,
            )
        except OSError as error:
            _fail(f"could not create frozen asset file {label}: {error}")
        copied = 0
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(target_fd, view)
                    if written <= 0:
                        _fail(f"could not complete frozen asset file {label}")
                    copied += written
                    view = view[written:]
            os.fchmod(target_fd, mode)
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        after = os.fstat(source_fd)
        if (
            copied != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_ctime_ns != before.st_ctime_ns
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            _fail(f"asset file changed while copying: {label}")
    finally:
        os.close(source_fd)


def _copy_directory_contents(
    source_fd: int,
    target_fd: int,
    *,
    label: str,
    generated_locale_only: bool = False,
) -> None:
    try:
        names = sorted(os.listdir(source_fd))
    except OSError as error:
        _fail(f"could not list asset directory {label}: {error}")
    for name in names:
        entry_label = f"{label}/{name}" if label else name
        try:
            entry_stat = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as error:
            _fail(f"could not stat asset entry {entry_label}: {error}")
        if stat.S_ISDIR(entry_stat.st_mode):
            _safe_mode(entry_stat, label=entry_label)
            source_child = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
            try:
                opened_child_stat = os.fstat(source_child)
                if (
                    not stat.S_ISDIR(opened_child_stat.st_mode)
                    or opened_child_stat.st_dev != entry_stat.st_dev
                    or opened_child_stat.st_ino != entry_stat.st_ino
                ):
                    _fail(f"asset directory changed while opening: {entry_label}")
                os.mkdir(name, 0o755, dir_fd=target_fd)
                target_child = os.open(name, _DIRECTORY_FLAGS, dir_fd=target_fd)
                try:
                    _copy_directory_contents(
                        source_child,
                        target_child,
                        label=entry_label,
                        generated_locale_only=generated_locale_only,
                    )
                finally:
                    os.close(target_child)
            finally:
                os.close(source_child)
        elif stat.S_ISREG(entry_stat.st_mode):
            if entry_stat.st_nlink != 1:
                _fail(f"asset file must be single-link: {entry_label}")
            _safe_mode(entry_stat, label=entry_label)
            if not generated_locale_only or name.endswith(".mo") or name == "mobile.json":
                _copy_regular_file(source_fd, target_fd, name, label=entry_label)
        else:
            _fail(f"asset build contains a symlink or special node: {entry_label}")
    if sorted(os.listdir(source_fd)) != names:
        _fail(f"asset directory changed while copying: {label}")
    os.fsync(target_fd)


def freeze_asset_build(source_root: Path, destination_parent: Path) -> Path:
    source_root_fd = _open_directory(source_root)
    destination_parent_fd = _open_directory(destination_parent)
    destination = Path(tempfile.mkdtemp(prefix="pear-frozen-assets.", dir=destination_parent))
    os.chmod(destination, 0o700)
    complete = False
    try:
        destination_fd = _open_directory(destination)
        try:
            for relative_path in DIRECTORY_ROOTS:
                source_fd = _open_directory_chain(source_root_fd, relative_path)
                target_fd = _ensure_target_directory(destination_fd, relative_path)
                try:
                    _copy_directory_contents(source_fd, target_fd, label=relative_path)
                finally:
                    os.close(source_fd)
                    os.close(target_fd)

            for relative_path in FILE_PATHS:
                parent, name = (
                    relative_path.rsplit("/", 1) if "/" in relative_path else ("", relative_path)
                )
                source_fd = (
                    _open_directory_chain(source_root_fd, parent)
                    if parent
                    else os.dup(source_root_fd)
                )
                target_fd = (
                    _ensure_target_directory(destination_fd, parent)
                    if parent
                    else os.dup(destination_fd)
                )
                try:
                    _copy_regular_file(source_fd, target_fd, name, label=relative_path)
                finally:
                    os.close(source_fd)
                    os.close(target_fd)

            source_locale_fd = _open_directory_chain(source_root_fd, "locale")
            target_locale_fd = _ensure_target_directory(destination_fd, "locale")
            try:
                _copy_directory_contents(
                    source_locale_fd,
                    target_locale_fd,
                    label="locale",
                    generated_locale_only=True,
                )
            finally:
                os.close(source_locale_fd)
                os.close(target_locale_fd)
        finally:
            os.close(destination_fd)
        complete = True
        return destination
    finally:
        os.close(source_root_fd)
        os.close(destination_parent_fd)
        if not complete:
            shutil.rmtree(destination, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination-parent", type=Path, default=Path("/root"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if os.geteuid() != 0:
            _fail("production asset freezing must run as root")
        print(freeze_asset_build(args.source_root, args.destination_parent))
        return 0
    except AssetFreezeError as error:
        print(f"asset freeze error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
