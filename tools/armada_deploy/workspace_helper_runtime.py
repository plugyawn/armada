#!/usr/bin/env python3
"""Install and verify the isolated Pear workspace I/O helper."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

HELPER_SOURCE_RELATIVE = Path("zerver/lib/armada_workspace_io.py")
DEFAULT_HELPER_PATH = Path("/usr/local/lib/armada-workspace-io")
HELPER_MODE = 0o555
ISOLATION_USERNAME_RE = re.compile(r"armada-[0-9a-f]{24}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SMOKE_CONTENT = b"armada-workspace-helper-smoke\n"


class WorkspaceHelperDeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class HelperSource:
    payload: bytes
    sha256: str


@dataclass(frozen=True)
class HelperInstallation:
    state: Literal["absent", "current", "outdated"]
    sha256: str | None
    mode: int | None
    uid: int | None
    gid: int | None


def _fail(message: str) -> NoReturn:
    raise WorkspaceHelperDeploymentError(message)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_regular_file_nofollow(path: Path) -> bytes:
    try:
        path_stat = path.lstat()
    except OSError as error:
        _fail(f"could not stat {path}: {error}")
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        _fail(f"{path} must be a regular non-symlink file")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _fail(f"could not open {path} safely: {error}")
    with os.fdopen(descriptor, "rb") as input_file:
        opened_stat = os.fstat(input_file.fileno())
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_dev != path_stat.st_dev
            or opened_stat.st_ino != path_stat.st_ino
        ):
            _fail(f"{path} changed while it was opened")
        return input_file.read()


def load_expected_helper(repo_root: Path, expected_sha256: str) -> HelperSource:
    if SHA256_RE.fullmatch(expected_sha256) is None:
        _fail("expected workspace helper SHA-256 is invalid")
    source_path = repo_root / HELPER_SOURCE_RELATIVE
    payload = _read_regular_file_nofollow(source_path)
    source_sha256 = _sha256(payload)
    if source_sha256 != expected_sha256:
        _fail("staged workspace helper source does not match the expected SHA-256")
    return HelperSource(payload=payload, sha256=source_sha256)


def _validate_destination_parent(path: Path, *, expected_uid: int) -> None:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        _fail("workspace helper destination must be an absolute file path")
    parent = path.parent
    try:
        parent_stat = parent.lstat()
    except OSError as error:
        _fail(f"could not stat workspace helper parent {parent}: {error}")
    parent_mode = stat.S_IMODE(parent_stat.st_mode)
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        _fail("workspace helper parent must be a real directory")
    if parent_stat.st_uid != expected_uid:
        _fail("workspace helper parent has an unsafe owner")
    if parent_mode & 0o022:
        _fail("workspace helper parent must not be group- or world-writable")


def inspect_installed_helper(
    path: Path,
    *,
    expected_sha256: str | None,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> HelperInstallation:
    if expected_sha256 is not None and SHA256_RE.fullmatch(expected_sha256) is None:
        _fail("expected workspace helper SHA-256 is invalid")
    _validate_destination_parent(path, expected_uid=expected_uid)
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return HelperInstallation("absent", None, None, None, None)
    except OSError as error:
        _fail(f"could not stat installed workspace helper: {error}")
    mode = stat.S_IMODE(path_stat.st_mode)
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        _fail("installed workspace helper must be a regular non-symlink file")
    if path_stat.st_uid != expected_uid or path_stat.st_gid != expected_gid:
        _fail("installed workspace helper must be owned by root:root")
    if mode != HELPER_MODE or mode & 0o222:
        _fail(f"installed workspace helper must have mode {HELPER_MODE:04o}")
    if path_stat.st_nlink != 1:
        _fail("installed workspace helper must not have hard-link aliases")
    installed_sha256 = _sha256(_read_regular_file_nofollow(path))
    state: Literal["current", "outdated"] = (
        "current" if expected_sha256 is None or installed_sha256 == expected_sha256 else "outdated"
    )
    return HelperInstallation(
        state,
        installed_sha256,
        mode,
        path_stat.st_uid,
        path_stat.st_gid,
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError as error:
            _fail(f"could not write the workspace helper: {error}")
        if written <= 0:
            _fail("could not complete the workspace helper write")
        offset += written


def atomic_install_helper(
    source: HelperSource,
    destination: Path,
    *,
    owner_uid: int = 0,
    owner_gid: int = 0,
) -> HelperInstallation:
    if (
        not source.payload
        or SHA256_RE.fullmatch(source.sha256) is None
        or _sha256(source.payload) != source.sha256
    ):
        _fail("workspace helper source payload does not match its SHA-256")
    _validate_destination_parent(destination, expected_uid=owner_uid)
    existing = inspect_installed_helper(
        destination,
        expected_sha256=None,
        expected_uid=owner_uid,
        expected_gid=owner_gid,
    )
    if existing.state == "current" and existing.sha256 == source.sha256:
        return existing

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        directory_fd = os.open(destination.parent, directory_flags)
    except OSError as error:
        _fail(f"could not open workspace helper parent safely: {error}")
    temporary_name = f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    temporary_fd: int | None = None
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o500,
            dir_fd=directory_fd,
        )
        _write_all(temporary_fd, source.payload)
        os.fchown(temporary_fd, owner_uid, owner_gid)
        os.fchmod(temporary_fd, HELPER_MODE)
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
    except OSError as error:
        _fail(f"could not atomically install the workspace helper: {error}")
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory_fd)

    installed = inspect_installed_helper(
        destination,
        expected_sha256=source.sha256,
        expected_uid=owner_uid,
        expected_gid=owner_gid,
    )
    if installed.state != "current":
        _fail("atomically installed workspace helper has the wrong content")
    return installed


def _decode_success_response(completed: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    if completed.returncode != 0:
        _fail("workspace helper smoke process failed")
    try:
        response = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("workspace helper smoke returned invalid JSON")
    if not isinstance(response, dict) or set(response) != {"ok", "result"}:
        _fail("workspace helper smoke returned an invalid response")
    if response["ok"] is not True or not isinstance(response["result"], dict):
        _fail("workspace helper smoke returned an operation error")
    return response["result"]


def _run_smoke_request(
    username: str, helper_path: Path, request: dict[str, object]
) -> dict[str, object]:
    encoded_request = json.dumps(request, separators=(",", ":")).encode("utf-8")
    try:
        completed = subprocess.run(
            [
                "sudo",
                "-n",
                "-u",
                username,
                "/usr/bin/env",
                "python3",
                os.fspath(helper_path),
                "--stdio",
            ],
            input=encoded_request,
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("workspace helper smoke could not execute as the isolation user")
    return _decode_success_response(completed)


def _existing_isolation_user() -> pwd.struct_passwd | None:
    users = sorted(
        (
            entry
            for entry in pwd.getpwall()
            if entry.pw_uid != 0 and ISOLATION_USERNAME_RE.fullmatch(entry.pw_name)
        ),
        key=lambda entry: entry.pw_name,
    )
    return users[0] if users else None


def smoke_helper_as_existing_isolation_user(helper_path: Path) -> str | None:
    user = _existing_isolation_user()
    if user is None:
        return None
    if os.geteuid() != 0:
        _fail("workspace helper isolation-user smoke must run as root")

    smoke_root = Path(tempfile.mkdtemp(prefix="armada-workspace-helper-smoke-", dir="/tmp"))
    smoke_file = smoke_root / "probe.txt"
    try:
        file_descriptor = os.open(
            smoke_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            _write_all(file_descriptor, SMOKE_CONTENT)
            os.fchown(file_descriptor, user.pw_uid, user.pw_gid)
            os.fchmod(file_descriptor, 0o600)
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
        os.chown(smoke_root, user.pw_uid, user.pw_gid)
        os.chmod(smoke_root, 0o700)

        inspect_result = _run_smoke_request(
            user.pw_name,
            helper_path,
            {
                "operation": "inspect",
                "root": os.fspath(smoke_root),
                "entries": [{"path": "probe.txt", "kind": "file"}],
                "skip_unsafe": False,
            },
        )
        inspected_entries = inspect_result.get("entries")
        if (
            not isinstance(inspected_entries, list)
            or len(inspected_entries) != 1
            or not isinstance(inspected_entries[0], dict)
            or inspected_entries[0].get("path") != "probe.txt"
            or inspected_entries[0].get("kind") != "file"
            or not isinstance(inspected_entries[0].get("identity"), dict)
        ):
            _fail("workspace helper inspect smoke returned the wrong entry")

        read_result = _run_smoke_request(
            user.pw_name,
            helper_path,
            {
                "operation": "read",
                "root": os.fspath(smoke_root),
                "path": "probe.txt",
                "max_bytes": len(SMOKE_CONTENT),
                "expected_identity": None,
            },
        )
        file_payload = read_result.get("file")
        if not isinstance(file_payload, dict):
            _fail("workspace helper read smoke omitted its file payload")
        try:
            returned_content = base64.b64decode(
                file_payload.get("content_base64", ""), validate=True
            )
        except (TypeError, ValueError):
            _fail("workspace helper read smoke returned invalid content")
        if (
            returned_content != SMOKE_CONTENT
            or file_payload.get("size") != len(SMOKE_CONTENT)
            or file_payload.get("version") != _sha256(SMOKE_CONTENT)
            or file_payload.get("mode") != 0o600
            or not isinstance(file_payload.get("identity"), dict)
        ):
            _fail("workspace helper read smoke returned the wrong file")
    finally:
        shutil.rmtree(smoke_root)
    return user.pw_name


def _print_installation(installation: HelperInstallation, *, expected_sha256: str) -> None:
    print(f"workspace_helper_state={installation.state}")
    print(f"workspace_helper_expected_sha256={expected_sha256}")
    if installation.sha256 is not None:
        print(f"workspace_helper_installed_sha256={installation.sha256}")
        assert installation.mode is not None
        assert installation.uid is not None
        assert installation.gid is not None
        print(f"workspace_helper_mode={installation.mode:04o}")
        print(f"workspace_helper_owner={installation.uid}:{installation.gid}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="inspect without changing the helper")
    preflight.add_argument("--destination", type=Path, default=DEFAULT_HELPER_PATH)
    preflight.add_argument("--expected-sha256", required=True)

    for command in ("install", "verify"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--repo-root", type=Path, required=True)
        command_parser.add_argument("--destination", type=Path, default=DEFAULT_HELPER_PATH)
        command_parser.add_argument("--expected-sha256", required=True)
        if command == "verify":
            command_parser.add_argument("--smoke-existing-isolation-user", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "preflight":
            installation = inspect_installed_helper(
                args.destination,
                expected_sha256=args.expected_sha256,
            )
            _print_installation(installation, expected_sha256=args.expected_sha256)
            return 0

        source = load_expected_helper(args.repo_root, args.expected_sha256)
        if args.command == "install":
            if os.geteuid() != 0:
                _fail("workspace helper installation must run as root")
            installation = atomic_install_helper(source, args.destination)
        else:
            installation = inspect_installed_helper(
                args.destination,
                expected_sha256=source.sha256,
            )
            if installation.state != "current":
                _fail("installed workspace helper is absent or does not match deployed source")
        _print_installation(installation, expected_sha256=source.sha256)

        if args.command == "verify" and args.smoke_existing_isolation_user:
            username = smoke_helper_as_existing_isolation_user(args.destination)
            if username is None:
                print("workspace_helper_isolation_smoke=skipped-no-existing-user")
            else:
                print(f"workspace_helper_isolation_smoke=passed:{username}")
        return 0
    except WorkspaceHelperDeploymentError as error:
        print(f"workspace helper error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
