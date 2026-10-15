#!/usr/bin/env python3
"""Write the Armada deployment record without following attacker-controlled links."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import NoReturn

MAX_DEPLOYMENT_RECORD_BYTES = 64 * 1024


class DeploymentRecordError(RuntimeError):
    pass


def _fail(message: str) -> NoReturn:
    raise DeploymentRecordError(message)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            _fail("could not complete the deployment-record write")
        offset += written


def atomic_write_deployment_record(
    destination: Path,
    payload: bytes,
    *,
    owner_uid: int = 0,
    owner_gid: int = 0,
    mode: int = 0o644,
    parent_uid: int = 0,
    parent_gid: int = 0,
) -> None:
    if not payload or b"\x00" in payload:
        _fail("deployment record must be non-empty text data")
    if not destination.is_absolute() or destination.name in {"", ".", ".."}:
        _fail("deployment record destination must be an absolute file path")
    try:
        parent_stat = destination.parent.lstat()
    except OSError as error:
        _fail(f"could not stat deployment-record parent: {error}")
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        _fail("deployment-record parent must be a real directory")
    if (
        parent_stat.st_uid != parent_uid
        or parent_stat.st_gid != parent_gid
        or stat.S_IMODE(parent_stat.st_mode) & 0o022
    ):
        _fail("deployment-record parent must be trusted and non-writable")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        directory_fd = os.open(destination.parent, directory_flags)
    except OSError as error:
        _fail(f"could not safely open deployment-record parent: {error}")
    opened_parent_stat = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(opened_parent_stat.st_mode)
        or opened_parent_stat.st_dev != parent_stat.st_dev
        or opened_parent_stat.st_ino != parent_stat.st_ino
    ):
        os.close(directory_fd)
        _fail("deployment-record parent changed while it was opened")

    temporary_name = f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(12)}"
    temporary_fd: int | None = None
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        _write_all(temporary_fd, payload)
        os.fchown(temporary_fd, owner_uid, owner_gid)
        os.fchmod(temporary_fd, mode)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        os.replace(
            temporary_name,
            destination.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)

        installed_fd = os.open(
            destination.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        with os.fdopen(installed_fd, "rb") as installed:
            installed_stat = os.fstat(installed.fileno())
            if (
                not stat.S_ISREG(installed_stat.st_mode)
                or installed_stat.st_nlink != 1
                or installed_stat.st_uid != owner_uid
                or installed_stat.st_gid != owner_gid
                or stat.S_IMODE(installed_stat.st_mode) != mode
                or installed.read() != payload
            ):
                _fail("installed deployment record failed verification")
        final_parent_stat = destination.parent.lstat()
        if (
            final_parent_stat.st_dev != opened_parent_stat.st_dev
            or final_parent_stat.st_ino != opened_parent_stat.st_ino
        ):
            _fail("deployment-record parent changed during installation")
    except OSError as error:
        _fail(f"could not atomically install deployment record: {error}")
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory_fd)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if os.geteuid() != 0:
            _fail("deployment record installation must run as root")
        payload = sys.stdin.buffer.read(MAX_DEPLOYMENT_RECORD_BYTES + 1)
        if len(payload) > MAX_DEPLOYMENT_RECORD_BYTES:
            _fail("deployment record exceeds the size limit")
        atomic_write_deployment_record(args.destination, payload)
        return 0
    except DeploymentRecordError as error:
        print(f"deployment record error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
