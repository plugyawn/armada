"""Contained filesystem I/O for Pear workspaces.

This module is deliberately independent of Django so the same implementation
can run inside the web process for ordinary workspaces and as the owning
per-topic OS user for isolated workspaces.  The latter uses the JSON/stdio CLI
at the bottom of this file; deployment installs this file at a path readable by
the isolation user.

Path authorization (for example, requiring an opaque entry ID or excluding a
Git-ignored secret) belongs to the caller.  Once a relative path has been
authorized, this layer guarantees that every component is opened relative to
an already-open directory, without following symlinks, and that only regular,
single-link files on the workspace filesystem are read or replaced.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PEAR_ISOLATION_USERNAME_RE = re.compile(r"pear-[0-9a-f]{24}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

MAX_WORKSPACE_PATH_BYTES = 4096
MAX_WORKSPACE_PATH_COMPONENTS = 256
MAX_CLI_REQUEST_BYTES = 16 * 1024 * 1024
MAX_CLI_RESPONSE_BYTES = 16 * 1024 * 1024

DEFAULT_ISOLATED_HELPER_PATH = Path("/usr/local/lib/pear-workspace-io")

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True)
class WorkspaceFile:
    data: bytes
    version: str
    size: int
    mode: int


@dataclass(frozen=True)
class WorkspaceTextFile:
    text: str
    version: str
    size: int
    mode: int


@dataclass(frozen=True)
class WorkspaceWriteResult:
    file: WorkspaceFile
    changed: bool
    created: bool


class WorkspaceIOError(Exception):
    code = "io_error"

    def __init__(
        self,
        message: str,
        *,
        current: WorkspaceFile | None = None,
    ) -> None:
        super().__init__(message)
        self.current = current


class InvalidWorkspacePathError(WorkspaceIOError):
    code = "invalid_path"


class UnsafeWorkspaceEntryError(WorkspaceIOError):
    code = "unsafe_entry"


class WorkspaceEntryNotFoundError(WorkspaceIOError):
    code = "not_found"


class WorkspaceEntryExistsError(WorkspaceIOError):
    code = "already_exists"


class WorkspaceFileTooLargeError(WorkspaceIOError):
    code = "too_large"


class WorkspaceInvalidUTF8Error(WorkspaceIOError):
    code = "invalid_utf8"


class WorkspaceVersionConflictError(WorkspaceIOError):
    code = "version_conflict"


class WorkspaceEntryChangedError(WorkspaceIOError):
    code = "stale_entry"


class WorkspaceHelperError(WorkspaceIOError):
    code = "helper_error"


def _validate_nonnegative_limit(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def validate_workspace_relative_path(relative_path: str) -> tuple[str, ...]:
    """Validate without normalizing, so aliases never change file identity."""

    if not isinstance(relative_path, str):
        raise InvalidWorkspacePathError("Workspace path must be text.")
    try:
        encoded_path = relative_path.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise InvalidWorkspacePathError("Workspace path is not valid UTF-8.") from error
    if not encoded_path or len(encoded_path) > MAX_WORKSPACE_PATH_BYTES:
        raise InvalidWorkspacePathError("Workspace path has an invalid length.")
    if relative_path.startswith("/") or "\\" in relative_path:
        raise InvalidWorkspacePathError("Workspace path must be relative.")
    if _contains_control_character(relative_path):
        raise InvalidWorkspacePathError("Workspace path contains a control character.")

    components = tuple(relative_path.split("/"))
    if len(components) > MAX_WORKSPACE_PATH_COMPONENTS:
        raise InvalidWorkspacePathError("Workspace path has too many components.")
    for component in components:
        if component in {"", ".", ".."}:
            raise InvalidWorkspacePathError("Workspace path contains an invalid component.")
        if component.casefold() == ".git":
            raise InvalidWorkspacePathError("Git metadata is not a workspace file.")
    return components


def _validate_workspace_root(root: str | Path) -> tuple[str, ...]:
    raw_root = os.fspath(root)
    if not isinstance(raw_root, str):
        raise InvalidWorkspacePathError("Workspace root must be text.")
    try:
        raw_root.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise InvalidWorkspacePathError("Workspace root is not valid UTF-8.") from error
    if not raw_root.startswith("/") or raw_root == "/":
        raise InvalidWorkspacePathError("Workspace root must be an absolute directory.")
    if _contains_control_character(raw_root):
        raise InvalidWorkspacePathError("Workspace root contains a control character.")

    components = tuple(raw_root.split("/")[1:])
    if (
        not components
        or len(components) > MAX_WORKSPACE_PATH_COMPONENTS
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise InvalidWorkspacePathError("Workspace root is not canonical.")
    return components


def _path_open_error(error: OSError, *, final: bool) -> WorkspaceIOError:
    if error.errno in {errno.ENOENT, errno.ENOTDIR}:
        return WorkspaceEntryNotFoundError("Workspace entry does not exist.")
    if error.errno in {
        errno.ELOOP,
        errno.ENXIO,
        errno.ENODEV,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
    }:
        return UnsafeWorkspaceEntryError("Workspace entry is not a safe regular file.")
    target = "file" if final else "directory"
    return WorkspaceIOError(f"Could not open workspace {target}.")


def _open_directory_at(parent_fd: int, component: str) -> int:
    try:
        child_fd = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise _path_open_error(error, final=False) from error
    child_stat = os.fstat(child_fd)
    if not stat.S_ISDIR(child_stat.st_mode):  # nocoverage: O_DIRECTORY enforces this.
        os.close(child_fd)
        raise UnsafeWorkspaceEntryError("Workspace path contains a non-directory component.")
    return child_fd


@contextmanager
def _open_workspace_root(root: str | Path) -> Iterator[tuple[int, os.stat_result]]:
    components = _validate_workspace_root(root)
    current_fd = os.open("/", _DIRECTORY_OPEN_FLAGS)
    try:
        for component in components:
            next_fd = _open_directory_at(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        root_stat = os.fstat(current_fd)
        yield current_fd, root_stat
    finally:
        os.close(current_fd)


@contextmanager
def _workspace_lock(root_fd: int, *, exclusive: bool) -> Iterator[None]:
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(root_fd, operation)
    except OSError as error:
        raise WorkspaceIOError("Could not lock the workspace.") from error
    try:
        yield
    finally:
        fcntl.flock(root_fd, fcntl.LOCK_UN)


@contextmanager
def _open_parent_directory(
    root_fd: int,
    root_device: int,
    components: tuple[str, ...],
) -> Iterator[tuple[int, str]]:
    parent_fd = os.dup(root_fd)
    try:
        for component in components[:-1]:
            next_fd = _open_directory_at(parent_fd, component)
            os.close(parent_fd)
            parent_fd = next_fd
            if os.fstat(parent_fd).st_dev != root_device:
                raise UnsafeWorkspaceEntryError("Workspace path crosses a filesystem boundary.")
        yield parent_fd, components[-1]
    finally:
        os.close(parent_fd)


def _validate_regular_file(file_stat: os.stat_result, *, root_device: int) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise UnsafeWorkspaceEntryError("Workspace entry is not a regular file.")
    if file_stat.st_dev != root_device:
        raise UnsafeWorkspaceEntryError("Workspace file crosses a filesystem boundary.")
    if file_stat.st_nlink != 1:
        raise UnsafeWorkspaceEntryError("Workspace file has an untrusted hard link.")


def _stat_identity(file_stat: os.stat_result) -> tuple[int, int]:
    return (file_stat.st_dev, file_stat.st_ino)


def _stat_revision(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _read_bounded(fd: int, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    bytes_read = 0
    while bytes_read <= max_bytes:
        try:
            chunk = os.read(fd, min(64 * 1024, max_bytes + 1 - bytes_read))
        except OSError as error:
            raise WorkspaceIOError("Could not read the workspace file.") from error
        if not chunk:
            break
        chunks.append(chunk)
        bytes_read += len(chunk)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise WorkspaceFileTooLargeError("Workspace file exceeds the read limit.")
    return data


def _read_file_from_parent(
    parent_fd: int,
    name: str,
    *,
    root_device: int,
    max_bytes: int,
) -> tuple[WorkspaceFile, os.stat_result]:
    try:
        file_fd = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise _path_open_error(error, final=True) from error
    try:
        before = os.fstat(file_fd)
        _validate_regular_file(before, root_device=root_device)
        if before.st_size > max_bytes:
            raise WorkspaceFileTooLargeError("Workspace file exceeds the read limit.")
        data = _read_bounded(file_fd, max_bytes=max_bytes)
        after = os.fstat(file_fd)
        if _stat_revision(after) != _stat_revision(before):
            raise WorkspaceEntryChangedError("Workspace file changed while it was read.")
        try:
            path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            if error.errno in {errno.ENOENT, errno.ENOTDIR}:
                raise WorkspaceEntryChangedError(
                    "Workspace file moved while it was read."
                ) from error
            raise _path_open_error(error, final=True) from error
        _validate_regular_file(path_stat, root_device=root_device)
        if _stat_identity(path_stat) != _stat_identity(after):
            raise WorkspaceEntryChangedError("Workspace file was replaced while it was read.")
        return (
            WorkspaceFile(
                data=data,
                version=hashlib.sha256(data).hexdigest(),
                size=len(data),
                mode=stat.S_IMODE(after.st_mode) & 0o777,
            ),
            after,
        )
    finally:
        os.close(file_fd)


def read_workspace_file(
    root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> WorkspaceFile:
    _validate_nonnegative_limit(max_bytes)
    components = validate_workspace_relative_path(relative_path)
    with (
        _open_workspace_root(root) as (root_fd, root_stat),
        _workspace_lock(root_fd, exclusive=False),
        _open_parent_directory(root_fd, root_stat.st_dev, components) as (parent_fd, name),
    ):
        file, _file_stat = _read_file_from_parent(
            parent_fd,
            name,
            root_device=root_stat.st_dev,
            max_bytes=max_bytes,
        )
        return file


def decode_workspace_text(file: WorkspaceFile) -> WorkspaceTextFile:
    try:
        text = file.data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise WorkspaceInvalidUTF8Error("Workspace file is not valid UTF-8.") from error
    return WorkspaceTextFile(
        text=text,
        version=file.version,
        size=file.size,
        mode=file.mode,
    )


def read_workspace_text_file(
    root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> WorkspaceTextFile:
    return decode_workspace_text(read_workspace_file(root, relative_path, max_bytes=max_bytes))


def _validate_expected_version(expected_version: str | None, *, create: bool) -> None:
    if create:
        if expected_version is not None:
            raise ValueError("A create operation cannot have an expected version")
        return
    if not isinstance(expected_version, str) or SHA256_RE.fullmatch(expected_version) is None:
        raise ValueError("expected_version must be a lowercase SHA-256 digest")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(data):
        try:
            count = os.write(fd, view[written:])
        except OSError as error:
            raise WorkspaceIOError("Could not write the workspace file.") from error
        if count <= 0:  # nocoverage: os.write raises instead of returning zero.
            raise WorkspaceIOError("Could not write the workspace file.")
        written += count


def _unlink_if_present(parent_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError as error:
        raise WorkspaceIOError("Could not remove a workspace temporary file.") from error


def _sync_fd(fd: int, *, target: str) -> None:
    try:
        os.fsync(fd)
    except OSError as error:
        raise WorkspaceIOError(f"Could not sync the workspace {target}.") from error


def _set_file_mode(fd: int, mode: int) -> None:
    try:
        os.fchmod(fd, mode)
    except OSError as error:
        raise WorkspaceIOError("Could not set workspace file permissions.") from error


def atomic_write_workspace_file(
    root: str | Path,
    relative_path: str,
    data: bytes,
    *,
    max_bytes: int,
    expected_version: str | None,
    create: bool = False,
    create_mode: int = 0o600,
) -> WorkspaceWriteResult:
    """Replace or create a file atomically after a raw-byte version check.

    `flock` on the workspace root serializes writers that use this module,
    including separate isolation-helper processes.  An arbitrary external
    process does not honor that lock; the last identity/version check narrows
    but cannot mathematically eliminate its final check-to-rename race.
    """

    _validate_nonnegative_limit(max_bytes)
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > max_bytes:
        raise WorkspaceFileTooLargeError("Workspace file exceeds the write limit.")
    if isinstance(create_mode, bool) or not isinstance(create_mode, int) or create_mode & ~0o777:
        raise ValueError("create_mode must contain ordinary Unix permission bits")
    _validate_expected_version(expected_version, create=create)
    components = validate_workspace_relative_path(relative_path)

    with _open_workspace_root(root) as (root_fd, root_stat):  # noqa: SIM117
        with _workspace_lock(root_fd, exclusive=True):
            with _open_parent_directory(root_fd, root_stat.st_dev, components) as (
                parent_fd,
                name,
            ):
                current: WorkspaceFile | None
                current_stat: os.stat_result | None
                try:
                    current, current_stat = _read_file_from_parent(
                        parent_fd,
                        name,
                        root_device=root_stat.st_dev,
                        max_bytes=max_bytes,
                    )
                except WorkspaceEntryNotFoundError:
                    current = None
                    current_stat = None

                if create:
                    if current is not None:
                        if current.data == data:
                            return WorkspaceWriteResult(file=current, changed=False, created=False)
                        raise WorkspaceEntryExistsError("Workspace entry already exists.")
                    target_mode = create_mode
                else:
                    if current is None:
                        raise WorkspaceEntryNotFoundError("Workspace entry does not exist.")
                    if current.data == data:
                        # A lost response can be retried with an old base version.
                        return WorkspaceWriteResult(file=current, changed=False, created=False)
                    if current.version != expected_version:
                        raise WorkspaceVersionConflictError(
                            "Workspace file changed before it could be saved.",
                            current=current,
                        )
                    target_mode = current.mode

                temporary_name = f".pear-write-{os.getpid()}-{secrets.token_hex(12)}"
                temporary_fd: int | None = None
                temporary_exists = False
                try:
                    try:
                        temporary_fd = os.open(
                            temporary_name,
                            _FILE_WRITE_FLAGS,
                            target_mode,
                            dir_fd=parent_fd,
                        )
                    except OSError as error:
                        raise WorkspaceIOError(
                            "Could not create a workspace temporary file."
                        ) from error
                    temporary_exists = True
                    _set_file_mode(temporary_fd, target_mode)
                    _write_all(temporary_fd, data)
                    _sync_fd(temporary_fd, target="file")
                    temporary_stat = os.fstat(temporary_fd)
                    _validate_regular_file(temporary_stat, root_device=root_stat.st_dev)
                    os.close(temporary_fd)
                    temporary_fd = None

                    if create:
                        try:
                            os.link(
                                temporary_name,
                                name,
                                src_dir_fd=parent_fd,
                                dst_dir_fd=parent_fd,
                                follow_symlinks=False,
                            )
                        except FileExistsError:
                            raced, _raced_stat = _read_file_from_parent(
                                parent_fd,
                                name,
                                root_device=root_stat.st_dev,
                                max_bytes=max_bytes,
                            )
                            if raced.data == data:
                                return WorkspaceWriteResult(
                                    file=raced,
                                    changed=False,
                                    created=False,
                                )
                            raise WorkspaceEntryExistsError("Workspace entry already exists.")
                        except OSError as error:
                            raise WorkspaceIOError(
                                "Could not create the workspace file."
                            ) from error
                        _unlink_if_present(parent_fd, temporary_name)
                        temporary_exists = False
                    else:
                        assert current is not None and current_stat is not None
                        latest, latest_stat = _read_file_from_parent(
                            parent_fd,
                            name,
                            root_device=root_stat.st_dev,
                            max_bytes=max_bytes,
                        )
                        if latest.data == data:
                            return WorkspaceWriteResult(
                                file=latest,
                                changed=False,
                                created=False,
                            )
                        if latest.version != expected_version or _stat_identity(
                            latest_stat
                        ) != _stat_identity(current_stat):
                            raise WorkspaceVersionConflictError(
                                "Workspace file changed before it could be saved.",
                                current=latest,
                            )
                        try:
                            os.replace(
                                temporary_name,
                                name,
                                src_dir_fd=parent_fd,
                                dst_dir_fd=parent_fd,
                            )
                        except OSError as error:
                            raise WorkspaceIOError(
                                "Could not replace the workspace file."
                            ) from error
                        temporary_exists = False

                    _sync_fd(parent_fd, target="directory")
                    saved, saved_stat = _read_file_from_parent(
                        parent_fd,
                        name,
                        root_device=root_stat.st_dev,
                        max_bytes=max_bytes,
                    )
                    if saved.data != data or _stat_identity(saved_stat) != _stat_identity(
                        temporary_stat
                    ):
                        raise WorkspaceEntryChangedError(
                            "Workspace file changed while the save was acknowledged."
                        )
                    return WorkspaceWriteResult(file=saved, changed=True, created=create)
                finally:
                    if temporary_fd is not None:
                        os.close(temporary_fd)
                    if temporary_exists:
                        _unlink_if_present(parent_fd, temporary_name)


def _workspace_file_payload(file: WorkspaceFile) -> dict[str, object]:
    return {
        "content_base64": base64.b64encode(file.data).decode("ascii"),
        "version": file.version,
        "size": file.size,
        "mode": file.mode,
    }


def _workspace_file_from_payload(payload: Mapping[str, object]) -> WorkspaceFile:
    content_base64 = payload.get("content_base64")
    version = payload.get("version")
    size = payload.get("size")
    mode = payload.get("mode")
    if (
        not isinstance(content_base64, str)
        or not isinstance(version, str)
        or SHA256_RE.fullmatch(version) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or isinstance(mode, bool)
        or not isinstance(mode, int)
        or mode < 0
        or mode & ~0o777
    ):
        raise WorkspaceHelperError("Workspace helper returned an invalid file payload.")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise WorkspaceHelperError("Workspace helper returned invalid file bytes.") from error
    if len(data) != size or hashlib.sha256(data).hexdigest() != version:
        raise WorkspaceHelperError("Workspace helper returned inconsistent file metadata.")
    return WorkspaceFile(data=data, version=version, size=size, mode=mode)


def _error_payload(error: WorkspaceIOError) -> dict[str, object]:
    payload: dict[str, object] = {"code": error.code, "message": str(error)}
    if error.current is not None:
        payload["current"] = _workspace_file_payload(error.current)
    return payload


_ERROR_TYPES: dict[str, type[WorkspaceIOError]] = {
    error_type.code: error_type
    for error_type in (
        WorkspaceIOError,
        InvalidWorkspacePathError,
        UnsafeWorkspaceEntryError,
        WorkspaceEntryNotFoundError,
        WorkspaceEntryExistsError,
        WorkspaceFileTooLargeError,
        WorkspaceInvalidUTF8Error,
        WorkspaceVersionConflictError,
        WorkspaceEntryChangedError,
        WorkspaceHelperError,
    )
}


def _raise_helper_error(payload: Mapping[str, object]) -> None:
    code = payload.get("code")
    message = payload.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        raise WorkspaceHelperError("Workspace helper returned an invalid error payload.")
    error_type = _ERROR_TYPES.get(code, WorkspaceHelperError)
    current_payload = payload.get("current")
    current = (
        _workspace_file_from_payload(current_payload)
        if isinstance(current_payload, Mapping)
        else None
    )
    raise error_type(message, current=current)


def _require_request_keys(request: Mapping[str, object], allowed_keys: set[str]) -> None:
    unknown_keys = set(request) - allowed_keys
    if unknown_keys:
        raise ValueError("Workspace helper request has unknown fields")


def execute_workspace_io_request(request: Mapping[str, object]) -> dict[str, object]:
    operation = request.get("operation")
    if operation == "read":
        _require_request_keys(request, {"operation", "root", "path", "max_bytes"})
        root = request.get("root")
        relative_path = request.get("path")
        max_bytes = request.get("max_bytes")
        if not isinstance(root, str) or not isinstance(relative_path, str):
            raise ValueError("Workspace helper read request has invalid paths")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise ValueError("Workspace helper read request has an invalid limit")
        return {
            "file": _workspace_file_payload(
                read_workspace_file(root, relative_path, max_bytes=max_bytes)
            )
        }
    if operation == "write":
        _require_request_keys(
            request,
            {
                "operation",
                "root",
                "path",
                "content_base64",
                "max_bytes",
                "expected_version",
                "create",
                "create_mode",
            },
        )
        root = request.get("root")
        relative_path = request.get("path")
        content_base64 = request.get("content_base64")
        max_bytes = request.get("max_bytes")
        expected_version = request.get("expected_version")
        create = request.get("create", False)
        create_mode = request.get("create_mode", 0o600)
        if (
            not isinstance(root, str)
            or not isinstance(relative_path, str)
            or not isinstance(content_base64, str)
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or (expected_version is not None and not isinstance(expected_version, str))
            or not isinstance(create, bool)
            or isinstance(create_mode, bool)
            or not isinstance(create_mode, int)
        ):
            raise ValueError("Workspace helper write request is invalid")
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("Workspace helper write request has invalid content") from error
        result = atomic_write_workspace_file(
            root,
            relative_path,
            data,
            max_bytes=max_bytes,
            expected_version=expected_version,
            create=create,
            create_mode=create_mode,
        )
        return {
            "file": _workspace_file_payload(result.file),
            "changed": result.changed,
            "created": result.created,
        }
    raise ValueError("Workspace helper request has an unknown operation")


def _helper_command(
    *,
    isolation_username: str | None,
    helper_path: str | Path | None,
    python_executable: str | Path | None,
) -> list[str]:
    if isolation_username is not None:
        if PEAR_ISOLATION_USERNAME_RE.fullmatch(isolation_username) is None:
            raise ValueError("Invalid Pear isolation username")
        executable = os.fspath(python_executable or "python3")
        path = os.fspath(helper_path or DEFAULT_ISOLATED_HELPER_PATH)
        return [
            "sudo",
            "-n",
            "-u",
            isolation_username,
            "/usr/bin/env",
            executable,
            path,
            "--stdio",
        ]
    executable = os.fspath(python_executable or sys.executable)
    path = os.fspath(helper_path or Path(__file__))
    return [executable, path, "--stdio"]


def invoke_workspace_io_helper(
    request: Mapping[str, object],
    *,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
    timeout: float = 20,
) -> dict[str, object]:
    encoded_request = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(encoded_request) > MAX_CLI_REQUEST_BYTES:
        raise WorkspaceHelperError("Workspace helper request exceeds the protocol limit.")
    command = _helper_command(
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    try:
        completed = subprocess.run(
            command,
            input=encoded_request,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceHelperError("Workspace helper could not be executed.") from error
    if completed.returncode != 0 or len(completed.stdout) > MAX_CLI_RESPONSE_BYTES:
        raise WorkspaceHelperError("Workspace helper failed.")
    try:
        response = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkspaceHelperError("Workspace helper returned invalid JSON.") from error
    if not isinstance(response, dict) or set(response) != {"ok", "result"}:
        raise WorkspaceHelperError("Workspace helper returned an invalid response.")
    ok = response["ok"]
    result = response["result"]
    if not isinstance(ok, bool) or not isinstance(result, dict):
        raise WorkspaceHelperError("Workspace helper returned an invalid response.")
    if not ok:
        _raise_helper_error(result)
    return result


def read_workspace_file_via_helper(
    root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> WorkspaceFile:
    result = invoke_workspace_io_helper(
        {
            "operation": "read",
            "root": os.fspath(root),
            "path": relative_path,
            "max_bytes": max_bytes,
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    file_payload = result.get("file")
    if not isinstance(file_payload, Mapping):
        raise WorkspaceHelperError("Workspace helper omitted its file payload.")
    return _workspace_file_from_payload(file_payload)


def atomic_write_workspace_file_via_helper(
    root: str | Path,
    relative_path: str,
    data: bytes,
    *,
    max_bytes: int,
    expected_version: str | None,
    create: bool = False,
    create_mode: int = 0o600,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> WorkspaceWriteResult:
    result = invoke_workspace_io_helper(
        {
            "operation": "write",
            "root": os.fspath(root),
            "path": relative_path,
            "content_base64": base64.b64encode(data).decode("ascii"),
            "max_bytes": max_bytes,
            "expected_version": expected_version,
            "create": create,
            "create_mode": create_mode,
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    file_payload = result.get("file")
    changed = result.get("changed")
    created = result.get("created")
    if (
        not isinstance(file_payload, Mapping)
        or not isinstance(changed, bool)
        or not isinstance(created, bool)
    ):
        raise WorkspaceHelperError("Workspace helper returned an invalid write result.")
    return WorkspaceWriteResult(
        file=_workspace_file_from_payload(file_payload),
        changed=changed,
        created=created,
    )


def _read_cli_request() -> Mapping[str, object]:
    request_bytes = sys.stdin.buffer.read(MAX_CLI_REQUEST_BYTES + 1)
    if len(request_bytes) > MAX_CLI_REQUEST_BYTES:
        raise ValueError("Workspace helper request exceeds the protocol limit")
    request = json.loads(request_bytes)
    if not isinstance(request, dict):
        raise ValueError("Workspace helper request must be an object")
    return request


def _write_cli_response(response: Mapping[str, object]) -> None:
    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CLI_RESPONSE_BYTES:
        encoded = json.dumps(
            {
                "ok": False,
                "result": _error_payload(
                    WorkspaceHelperError("Workspace helper response exceeds the protocol limit.")
                ),
            },
            separators=(",", ":"),
        ).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Perform contained Pear workspace I/O.")
    parser.add_argument("--stdio", action="store_true", help="use the internal JSON/stdio protocol")
    args = parser.parse_args(argv)
    if not args.stdio:
        parser.error("--stdio is required")
    try:
        result = execute_workspace_io_request(_read_cli_request())
    except WorkspaceIOError as error:
        _write_cli_response({"ok": False, "result": _error_payload(error)})
        return 0
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        _write_cli_response(
            {
                "ok": False,
                "result": _error_payload(
                    WorkspaceHelperError("Workspace helper request is invalid.")
                ),
            }
        )
        return 0
    _write_cli_response({"ok": True, "result": result})
    return 0


if __name__ == "__main__":  # nocoverage
    raise SystemExit(main())
