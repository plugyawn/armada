#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path

RUNTIME_FILES = {
    Path("tools/armada_deploy/runtime/Caddyfile"): (Path("/etc/caddy/Caddyfile"), 0o644),
    Path("tools/armada_deploy/runtime/armada-run-zulip-dev"): (
        Path("/usr/local/bin/armada-run-zulip-dev"),
        0o755,
    ),
    Path("tools/armada_deploy/runtime/armada-zulip-dev.service"): (
        Path("/etc/systemd/system/armada-zulip-dev.service"),
        0o644,
    ),
    Path("tools/armada_deploy/runtime/armada-addressing.service"): (
        Path("/etc/systemd/system/armada-addressing.service"),
        0o644,
    ),
    Path("tools/armada_deploy/runtime/armada-presentation.service"): (
        Path("/etc/systemd/system/armada-presentation.service"),
        0o644,
    ),
    Path("tools/armada_deploy/runtime/armada-worker.service"): (
        Path("/etc/systemd/system/armada-worker.service"),
        0o644,
    ),
    Path("tools/armada_deploy/runtime/armada-worker-1.service"): (
        Path("/etc/systemd/system/armada-worker-1.service"),
        0o644,
    ),
}
CADDY_SOURCE_PATH = Path("tools/armada_deploy/runtime/Caddyfile")
CADDY_FQDN_TOKEN = b"{$ARMADA_CADDY_FQDN}"
LEGACY_RUNTIME_FILES = (
    Path("/etc/armada/caddy-basic-auth.env"),
    Path("/etc/systemd/system/caddy.service.d/armada-basic-auth.conf"),
)
ARMADA_ENV_PATH = Path("/home/zulipdev/armada.env")
PRODUCTION_ASSET_ENV = "ZULIP_DEV_PRODUCTION_ASSETS"
MAX_RUNTIME_FILE_BYTES = 1024 * 1024

FQDN_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


class RuntimeConfigError(RuntimeError):
    pass


def rooted(root_prefix: Path, absolute_path: Path) -> Path:
    if not absolute_path.is_absolute():
        raise RuntimeConfigError(f"runtime destination must be absolute: {absolute_path}")
    return root_prefix / absolute_path.relative_to("/")


def validate_fqdn(value: str) -> str:
    value = value.lower()
    if FQDN_RE.fullmatch(value) is None:
        raise RuntimeConfigError("invalid Caddy FQDN")
    return value


def _read_regular_file_nofollow(
    path: Path,
    *,
    required_mode: int | None = None,
    required_uid: int | None = None,
    required_gid: int | None = None,
) -> tuple[bytes, os.stat_result]:
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise RuntimeConfigError(f"could not stat required runtime file {path}: {exc}") from exc
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeConfigError(f"runtime file must be a regular non-symlink: {path}")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise RuntimeConfigError(f"could not safely open runtime file {path}: {exc}") from exc
    with os.fdopen(descriptor, "rb") as input_file:
        opened_stat = os.fstat(input_file.fileno())
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_nlink != 1
            or opened_stat.st_dev != path_stat.st_dev
            or opened_stat.st_ino != path_stat.st_ino
        ):
            raise RuntimeConfigError(f"runtime file changed while opening: {path}")
        mode = stat.S_IMODE(opened_stat.st_mode)
        if required_mode is not None and mode != required_mode:
            raise RuntimeConfigError(
                f"runtime file {path} must have mode {required_mode:04o}, not {mode:04o}"
            )
        if required_uid is not None and opened_stat.st_uid != required_uid:
            raise RuntimeConfigError(f"runtime file has the wrong owner: {path}")
        if required_gid is not None and opened_stat.st_gid != required_gid:
            raise RuntimeConfigError(f"runtime file has the wrong group: {path}")
        if opened_stat.st_size > MAX_RUNTIME_FILE_BYTES:
            raise RuntimeConfigError(f"runtime file exceeds the size limit: {path}")
        payload = input_file.read(MAX_RUNTIME_FILE_BYTES + 1)
        final_stat = os.fstat(input_file.fileno())
        if len(payload) > MAX_RUNTIME_FILE_BYTES:
            raise RuntimeConfigError(f"runtime file exceeds the size limit: {path}")
        if (
            final_stat.st_dev != opened_stat.st_dev
            or final_stat.st_ino != opened_stat.st_ino
            or final_stat.st_size != opened_stat.st_size
            or final_stat.st_ctime_ns != opened_stat.st_ctime_ns
            or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
            or len(payload) != opened_stat.st_size
        ):
            raise RuntimeConfigError(f"runtime file changed while reading: {path}")
        return payload, final_stat


def atomic_write(
    path: Path,
    content: bytes,
    mode: int,
    *,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        parent_stat = path.parent.lstat()
    except OSError as exc:
        raise RuntimeConfigError(f"could not stat runtime parent {path.parent}: {exc}") from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        raise RuntimeConfigError(f"runtime parent must be a real directory: {path.parent}")
    try:
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise RuntimeConfigError(
            f"could not safely open runtime parent {path.parent}: {exc}"
        ) from exc
    opened_parent_stat = os.fstat(directory_fd)
    if (
        opened_parent_stat.st_dev != parent_stat.st_dev
        or opened_parent_stat.st_ino != parent_stat.st_ino
    ):
        os.close(directory_fd)
        raise RuntimeConfigError(f"runtime parent changed while opening: {path.parent}")

    try:
        existing = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
        os.close(directory_fd)
        raise RuntimeConfigError(f"runtime destination must be a regular single-link file: {path}")

    temporary_name = f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(12)}"
    temporary_fd: int | None = None
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(content):
            written = os.write(temporary_fd, content[offset:])
            if written <= 0:
                raise RuntimeConfigError(f"could not complete runtime write: {path}")
            offset += written
        if owner_uid is not None or owner_gid is not None:
            os.fchown(
                temporary_fd,
                owner_uid if owner_uid is not None else -1,
                owner_gid if owner_gid is not None else -1,
            )
        os.fchmod(temporary_fd, mode)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)

        installed_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        with os.fdopen(installed_fd, "rb") as installed:
            installed_stat = os.fstat(installed.fileno())
            if (
                not stat.S_ISREG(installed_stat.st_mode)
                or installed_stat.st_nlink != 1
                or stat.S_IMODE(installed_stat.st_mode) != mode
                or (owner_uid is not None and installed_stat.st_uid != owner_uid)
                or (owner_gid is not None and installed_stat.st_gid != owner_gid)
                or installed.read() != content
            ):
                raise RuntimeConfigError(f"installed runtime file failed verification: {path}")
        final_parent_stat = path.parent.lstat()
        if (
            final_parent_stat.st_dev != opened_parent_stat.st_dev
            or final_parent_stat.st_ino != opened_parent_stat.st_ino
        ):
            raise RuntimeConfigError(f"runtime parent changed during installation: {path.parent}")
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory_fd)


def upsert_armada_env(path: Path) -> None:
    try:
        payload, old_stat = _read_regular_file_nofollow(path, required_mode=0o600)
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeConfigError(f"invalid Pear environment encoding: {path}") from exc
    if os.geteuid() == 0:
        try:
            zulipdev = pwd.getpwnam("zulipdev")
        except KeyError as exc:
            raise RuntimeConfigError("required zulipdev account is missing") from exc
        if old_stat.st_uid != zulipdev.pw_uid or old_stat.st_gid != zulipdev.pw_gid:
            raise RuntimeConfigError("Pear environment must be owned by zulipdev:zulipdev")

    prefix = f"{PRODUCTION_ASSET_ENV}="
    lines = [line for line in lines if not line.startswith(prefix)]
    lines.append(f"{PRODUCTION_ASSET_ENV}=1")
    atomic_write(
        path,
        ("\n".join(lines) + "\n").encode(),
        0o600,
        owner_uid=old_stat.st_uid,
        owner_gid=old_stat.st_gid,
    )


def render_runtime_payload(source_relative: Path, payload: bytes, fqdn: str) -> bytes:
    if source_relative != CADDY_SOURCE_PATH:
        return payload
    if payload.count(CADDY_FQDN_TOKEN) != 1:
        raise RuntimeConfigError("Caddy source must contain exactly one FQDN token")
    return payload.replace(CADDY_FQDN_TOKEN, validate_fqdn(fqdn).encode("ascii"))


def copy_runtime_files(repo_root: Path, root_prefix: Path, fqdn: str) -> None:
    for source_relative, (destination_absolute, mode) in RUNTIME_FILES.items():
        source = repo_root / source_relative
        payload, _ = _read_regular_file_nofollow(source)
        payload = render_runtime_payload(source_relative, payload, fqdn)
        destination = rooted(root_prefix, destination_absolute)
        atomic_write(destination, payload, mode)


def validate_legacy_runtime_files(root_prefix: Path) -> None:
    for absolute_path in LEGACY_RUNTIME_FILES:
        path = rooted(root_prefix, absolute_path)
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise RuntimeConfigError(f"legacy runtime path must be a regular non-symlink: {path}")


def remove_legacy_runtime_files(root_prefix: Path) -> None:
    for absolute_path in LEGACY_RUNTIME_FILES:
        with contextlib.suppress(FileNotFoundError):
            rooted(root_prefix, absolute_path).unlink()


def verify_legacy_runtime_files_absent(root_prefix: Path) -> None:
    for absolute_path in LEGACY_RUNTIME_FILES:
        path = rooted(root_prefix, absolute_path)
        if path.exists() or path.is_symlink():
            raise RuntimeConfigError(f"retired Caddy authentication file still exists: {path}")


def verify_runtime_files(repo_root: Path, root_prefix: Path, fqdn: str) -> None:
    for source_relative, (destination_absolute, mode) in RUNTIME_FILES.items():
        source = repo_root / source_relative
        destination = rooted(root_prefix, destination_absolute)
        source_payload, _ = _read_regular_file_nofollow(source)
        source_payload = render_runtime_payload(source_relative, source_payload, fqdn)
        required_owner = 0 if os.geteuid() == 0 else None
        destination_payload, _ = _read_regular_file_nofollow(
            destination,
            required_mode=mode,
            required_uid=required_owner,
            required_gid=required_owner,
        )
        if destination_payload != source_payload:
            raise RuntimeConfigError(f"runtime file does not match source: {destination}")


def verify_armada_env(path: Path) -> None:
    payload, path_stat = _read_regular_file_nofollow(path, required_mode=0o600)
    if os.geteuid() == 0:
        try:
            zulipdev = pwd.getpwnam("zulipdev")
        except KeyError as exc:
            raise RuntimeConfigError("required zulipdev account is missing") from exc
        if path_stat.st_uid != zulipdev.pw_uid or path_stat.st_gid != zulipdev.pw_gid:
            raise RuntimeConfigError("Pear environment must be owned by zulipdev:zulipdev")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeConfigError(f"invalid Pear environment encoding: {path}") from exc
    matches = [line for line in lines if line.startswith(f"{PRODUCTION_ASSET_ENV}=")]
    if matches != [f"{PRODUCTION_ASSET_ENV}=1"]:
        raise RuntimeConfigError(
            f"{path} must contain exactly one {PRODUCTION_ASSET_ENV}=1 setting"
        )


def validate_caddy(caddyfile: Path, fqdn: str) -> None:
    validate_fqdn(fqdn)
    try:
        result = subprocess.run(
            ["caddy", "validate", "--config", str(caddyfile), "--adapter", "caddyfile"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeConfigError("caddy executable is missing") from exc
    if result.returncode != 0:
        output = result.stdout + result.stderr
        raise RuntimeConfigError(f"Caddy config validation failed: {output.strip()}")


def install(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    root_prefix = args.root_prefix.resolve()
    validate_legacy_runtime_files(root_prefix)
    copy_runtime_files(repo_root, root_prefix, args.fqdn)
    remove_legacy_runtime_files(root_prefix)
    armada_env_path = rooted(root_prefix, ARMADA_ENV_PATH)
    upsert_armada_env(armada_env_path)
    verify(args)


def verify(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    root_prefix = args.root_prefix.resolve()
    verify_runtime_files(repo_root, root_prefix, args.fqdn)
    verify_legacy_runtime_files_absent(root_prefix)
    verify_armada_env(rooted(root_prefix, ARMADA_ENV_PATH))
    if not args.skip_caddy_validate:
        validate_caddy(rooted(root_prefix, Path("/etc/caddy/Caddyfile")), args.fqdn)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "verify"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--repo-root", type=Path, required=True)
        command_parser.add_argument("--root-prefix", type=Path, default=Path("/"))
        command_parser.add_argument("--fqdn", required=True)
        command_parser.add_argument("--skip-caddy-validate", action="store_true")
        if command == "install":
            command_parser.set_defaults(handler=install)
        else:
            command_parser.set_defaults(handler=verify)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (OSError, RuntimeConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
