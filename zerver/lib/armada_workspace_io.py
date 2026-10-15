"""Contained filesystem I/O for agent workspaces.

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
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from typing_extensions import override

ARMADA_ISOLATION_USERNAME_RE = re.compile(r"armada-[0-9a-f]{24}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

MAX_WORKSPACE_PATH_BYTES = 4096
MAX_WORKSPACE_PATH_COMPONENTS = 256
MAX_CLI_REQUEST_BYTES = 16 * 1024 * 1024
MAX_CLI_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_STREAM_HEADER_BYTES = 64 * 1024

DEFAULT_ISOLATED_HELPER_PATH = Path("/usr/local/lib/armada-workspace-io")

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_ANCESTOR_DIRECTORY_OPEN_FLAGS = (
    getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True)
class WorkspaceEntryIdentity:
    device: int
    inode: int
    ctime_ns: int


@dataclass(frozen=True)
class WorkspaceFile:
    data: bytes
    version: str
    size: int
    mode: int
    identity: WorkspaceEntryIdentity | None = None


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


@dataclass(frozen=True)
class WorkspaceEntryMutationResult:
    path: str
    kind: Literal["file", "directory"]
    identity: WorkspaceEntryIdentity | None = None


@dataclass(frozen=True)
class WorkspaceDeleteResult:
    path: str
    kind: Literal["file", "directory"]
    removed_entries: int


@dataclass(frozen=True)
class WorkspaceInspectedEntry:
    path: str
    kind: Literal["file", "directory"]
    identity: WorkspaceEntryIdentity


@dataclass(frozen=True)
class WorkspaceRawFileMetadata:
    size: int
    mode: int
    cache_token: str
    identity: WorkspaceEntryIdentity


@dataclass
class _InspectionTrieNode:
    expected_kind: Literal["file", "directory"] | None
    children: dict[str, _InspectionTrieNode]


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


class WorkspaceDirectoryNotEmptyError(WorkspaceIOError):
    code = "directory_not_empty"


class WorkspaceOperationUnsupportedError(WorkspaceIOError):
    code = "unsupported"


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
    if isinstance(max_bytes, bool) or max_bytes < 0:
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


def _open_ancestor_directory_at(parent_fd: int, component: str) -> int:
    """Open a trusted path component without requiring directory-list access.

    Isolated workspace users intentionally only have execute permission on the
    shared workspace parent, so they can reach their own root without listing
    sibling workspace names.  Linux's O_PATH provides exactly that traversal
    capability while O_DIRECTORY and O_NOFOLLOW preserve the component checks.
    Platforms without O_PATH retain the ordinary directory-open behavior.
    """

    try:
        child_fd = os.open(component, _ANCESTOR_DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
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
        for offset, component in enumerate(components):
            if offset == len(components) - 1:
                next_fd = _open_directory_at(current_fd, component)
            else:
                next_fd = _open_ancestor_directory_at(current_fd, component)
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


def _entry_kind(
    entry_stat: os.stat_result,
    *,
    root_device: int,
) -> Literal["file", "directory"]:
    if stat.S_ISREG(entry_stat.st_mode):
        _validate_regular_file(entry_stat, root_device=root_device)
        return "file"
    if stat.S_ISDIR(entry_stat.st_mode):
        if entry_stat.st_dev != root_device:
            raise UnsafeWorkspaceEntryError("Workspace directory crosses a filesystem boundary.")
        return "directory"
    raise UnsafeWorkspaceEntryError("Workspace entry is not a regular file or directory.")


def _stat_entry_from_parent(
    parent_fd: int,
    name: str,
    *,
    root_device: int,
) -> tuple[os.stat_result, Literal["file", "directory"]]:
    try:
        entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise _path_open_error(error, final=True) from error
    return entry_stat, _entry_kind(entry_stat, root_device=root_device)


def _validate_expected_kind(
    actual: Literal["file", "directory"],
    expected: Literal["file", "directory"],
) -> None:
    if actual != expected:
        raise WorkspaceEntryChangedError("Workspace entry kind changed before the operation.")


def _stat_identity(file_stat: os.stat_result) -> tuple[int, int]:
    return (file_stat.st_dev, file_stat.st_ino)


def _entry_identity(entry_stat: os.stat_result) -> WorkspaceEntryIdentity:
    return WorkspaceEntryIdentity(
        device=entry_stat.st_dev,
        inode=entry_stat.st_ino,
        ctime_ns=entry_stat.st_ctime_ns,
    )


def _validate_expected_identity(
    entry_stat: os.stat_result,
    expected_identity: WorkspaceEntryIdentity | None,
) -> None:
    if expected_identity is not None and _entry_identity(entry_stat) != expected_identity:
        raise WorkspaceEntryChangedError("Workspace entry identity is stale.")


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
    expected_identity: WorkspaceEntryIdentity | None = None,
) -> tuple[WorkspaceFile, os.stat_result]:
    try:
        file_fd = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise _path_open_error(error, final=True) from error
    try:
        before = os.fstat(file_fd)
        _validate_regular_file(before, root_device=root_device)
        _validate_expected_identity(before, expected_identity)
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
        _validate_expected_identity(path_stat, expected_identity)
        if _stat_identity(path_stat) != _stat_identity(after):
            raise WorkspaceEntryChangedError("Workspace file was replaced while it was read.")
        return (
            WorkspaceFile(
                data=data,
                version=hashlib.sha256(data).hexdigest(),
                size=len(data),
                mode=stat.S_IMODE(after.st_mode) & 0o777,
                identity=_entry_identity(after),
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
    expected_identity: WorkspaceEntryIdentity | None = None,
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
            expected_identity=expected_identity,
        )
        return file


def inspect_workspace_entries(
    root: str | Path,
    expectations: Mapping[str, Literal["file", "directory"]],
    *,
    skip_unsafe: bool = False,
) -> tuple[WorkspaceInspectedEntry, ...]:
    if not isinstance(skip_unsafe, bool):
        raise TypeError("skip_unsafe must be a boolean")
    trie = _InspectionTrieNode(expected_kind=None, children={})
    for relative_path, expected_kind in expectations.items():
        if expected_kind not in {"file", "directory"}:
            raise ValueError("Workspace inspection kind is invalid")
        components = validate_workspace_relative_path(relative_path)
        node = trie
        for component in components:
            node = node.children.setdefault(
                component,
                _InspectionTrieNode(expected_kind=None, children={}),
            )
        if node.expected_kind is not None and node.expected_kind != expected_kind:
            raise ValueError("Workspace inspection path has conflicting kinds")
        node.expected_kind = expected_kind

    inspected: list[WorkspaceInspectedEntry] = []

    def inspect_children(
        parent_fd: int,
        node: _InspectionTrieNode,
        prefix: tuple[str, ...],
        *,
        root_device: int,
    ) -> None:
        for name in sorted(node.children):
            child_node = node.children[name]
            try:
                entry_stat, actual_kind = _stat_entry_from_parent(
                    parent_fd,
                    name,
                    root_device=root_device,
                )
            except (
                UnsafeWorkspaceEntryError,
                WorkspaceEntryNotFoundError,
                WorkspaceEntryChangedError,
            ):
                if skip_unsafe:
                    continue
                raise
            child_path = (*prefix, name)
            if child_node.expected_kind is not None:
                try:
                    _validate_expected_kind(actual_kind, child_node.expected_kind)
                except WorkspaceEntryChangedError:
                    if skip_unsafe:
                        continue
                    raise
                inspected.append(
                    WorkspaceInspectedEntry(
                        path="/".join(child_path),
                        kind=child_node.expected_kind,
                        identity=_entry_identity(entry_stat),
                    )
                )
            if child_node.children:
                if actual_kind != "directory":
                    raise UnsafeWorkspaceEntryError(
                        "Workspace inspection path contains a non-directory component."
                    )
                try:
                    child_fd = _open_checked_directory(
                        parent_fd,
                        name,
                        root_device=root_device,
                    )
                except (UnsafeWorkspaceEntryError, WorkspaceEntryNotFoundError):
                    if skip_unsafe:
                        continue
                    raise
                try:
                    if _stat_identity(os.fstat(child_fd)) != _stat_identity(entry_stat):
                        raise WorkspaceEntryChangedError(
                            "Workspace directory changed during inspection."
                        )
                    inspect_children(
                        child_fd,
                        child_node,
                        child_path,
                        root_device=root_device,
                    )
                finally:
                    os.close(child_fd)

    with (
        _open_workspace_root(root) as (root_fd, root_stat),
        _workspace_lock(root_fd, exclusive=False),
    ):
        inspect_children(root_fd, trie, (), root_device=root_stat.st_dev)
    return tuple(inspected)


def inspect_empty_workspace_directory(
    root: str | Path,
    relative_path: str,
    *,
    expected_identity: WorkspaceEntryIdentity | None = None,
) -> WorkspaceEntryIdentity:
    """Return an empty directory's identity without following any path alias."""

    components = validate_workspace_relative_path(relative_path)
    with (
        _open_workspace_root(root) as (root_fd, root_stat),
        _workspace_lock(root_fd, exclusive=False),
        _open_parent_directory(root_fd, root_stat.st_dev, components) as (parent_fd, name),
    ):
        entry_stat, kind = _stat_entry_from_parent(
            parent_fd,
            name,
            root_device=root_stat.st_dev,
        )
        _validate_expected_kind(kind, "directory")
        _validate_expected_identity(entry_stat, expected_identity)
        directory_fd = _open_checked_directory(
            parent_fd,
            name,
            root_device=root_stat.st_dev,
        )
        try:
            opened_stat = os.fstat(directory_fd)
            if _entry_identity(opened_stat) != _entry_identity(entry_stat):
                raise WorkspaceEntryChangedError(
                    "Workspace directory changed while it was inspected."
                )
            try:
                names = os.listdir(directory_fd)
            except OSError as error:
                raise WorkspaceIOError("Could not inspect the workspace directory.") from error
            if names:
                raise WorkspaceDirectoryNotEmptyError("Workspace directory is not empty.")
            final_stat = os.fstat(directory_fd)
            if _entry_identity(final_stat) != _entry_identity(opened_stat):
                raise WorkspaceEntryChangedError(
                    "Workspace directory changed while it was inspected."
                )
            return _entry_identity(final_stat)
        finally:
            os.close(directory_fd)


def _raw_cache_token(file_stat: os.stat_result) -> str:
    revision = ":".join(str(value) for value in _stat_revision(file_stat)).encode("ascii")
    return hashlib.sha256(b"armada-workspace-raw-v1\0" + revision).hexdigest()


class WorkspaceRawFileStream(Iterator[bytes]):
    def __init__(
        self,
        *,
        stack: ExitStack,
        file_fd: int,
        parent_fd: int,
        name: str,
        root_device: int,
        initial_stat: os.stat_result,
        chunk_bytes: int,
    ) -> None:
        self._stack = stack
        self._file_fd = file_fd
        self._parent_fd = parent_fd
        self._name = name
        self._root_device = root_device
        self._initial_stat = initial_stat
        self._remaining = initial_stat.st_size
        self._chunk_bytes = chunk_bytes
        self._closed = False
        self.metadata = WorkspaceRawFileMetadata(
            size=initial_stat.st_size,
            mode=stat.S_IMODE(initial_stat.st_mode) & 0o777,
            cache_token=_raw_cache_token(initial_stat),
            identity=_entry_identity(initial_stat),
        )

    @override
    def __iter__(self) -> WorkspaceRawFileStream:
        return self

    @override
    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        if self._remaining:
            try:
                chunk = os.read(self._file_fd, min(self._chunk_bytes, self._remaining))
            except OSError as error:
                self.close()
                raise WorkspaceIOError("Could not stream the workspace file.") from error
            if not chunk:
                self.close()
                raise WorkspaceEntryChangedError("Workspace file changed while it was streamed.")
            self._remaining -= len(chunk)
            return chunk
        try:
            if os.read(self._file_fd, 1):
                raise WorkspaceEntryChangedError("Workspace file changed while it was streamed.")
            final_stat = os.fstat(self._file_fd)
            if _stat_revision(final_stat) != _stat_revision(self._initial_stat):
                raise WorkspaceEntryChangedError("Workspace file changed while it was streamed.")
            path_stat = os.stat(self._name, dir_fd=self._parent_fd, follow_symlinks=False)
            _validate_regular_file(path_stat, root_device=self._root_device)
            if _entry_identity(path_stat) != _entry_identity(final_stat):
                raise WorkspaceEntryChangedError(
                    "Workspace file was replaced while it was streamed."
                )
        except OSError as error:
            self.close()
            raise WorkspaceEntryChangedError(
                "Workspace file changed while it was streamed."
            ) from error
        except WorkspaceIOError:
            self.close()
            raise
        self.close()
        raise StopIteration

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stack.close()

    def __del__(self) -> None:  # nocoverage: defensive cleanup for abandoned iterators.
        self.close()


def open_workspace_file_stream(
    root: str | Path,
    relative_path: str,
    *,
    expected_identity: WorkspaceEntryIdentity | None = None,
    chunk_bytes: int = 64 * 1024,
) -> WorkspaceRawFileStream:
    if isinstance(chunk_bytes, bool) or chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be a positive integer")
    components = validate_workspace_relative_path(relative_path)
    stack = ExitStack()
    try:
        root_fd, root_stat = stack.enter_context(_open_workspace_root(root))
        stack.enter_context(_workspace_lock(root_fd, exclusive=False))
        parent_fd, name = stack.enter_context(
            _open_parent_directory(root_fd, root_stat.st_dev, components)
        )
        try:
            file_fd = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            raise _path_open_error(error, final=True) from error
        stack.callback(os.close, file_fd)
        initial_stat = os.fstat(file_fd)
        _validate_regular_file(initial_stat, root_device=root_stat.st_dev)
        _validate_expected_identity(initial_stat, expected_identity)
        try:
            path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise _path_open_error(error, final=True) from error
        _validate_regular_file(path_stat, root_device=root_stat.st_dev)
        _validate_expected_identity(path_stat, expected_identity)
        if _stat_identity(path_stat) != _stat_identity(initial_stat):
            raise WorkspaceEntryChangedError(
                "Workspace file was replaced before it could be streamed."
            )
        return WorkspaceRawFileStream(
            stack=stack,
            file_fd=file_fd,
            parent_fd=parent_fd,
            name=name,
            root_device=root_stat.st_dev,
            initial_stat=initial_stat,
            chunk_bytes=chunk_bytes,
        )
    except Exception:
        stack.close()
        raise


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
    expected_identity: WorkspaceEntryIdentity | None = None,
) -> WorkspaceTextFile:
    return decode_workspace_text(
        read_workspace_file(
            root,
            relative_path,
            max_bytes=max_bytes,
            expected_identity=expected_identity,
        )
    )


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


def _unlink_if_exact_identity(
    parent_fd: int,
    name: str,
    *,
    root_device: int,
    expected_identity: WorkspaceEntryIdentity,
) -> Literal["removed", "missing", "mismatch", "error"]:
    """Remove only the exact regular file that was previously authorized."""

    try:
        entry_stat, kind = _stat_entry_from_parent(
            parent_fd,
            name,
            root_device=root_device,
        )
    except WorkspaceEntryNotFoundError:
        return "missing"
    except WorkspaceIOError:
        return "mismatch"
    if kind != "file" or _entry_identity(entry_stat) != expected_identity:
        return "mismatch"
    try:
        os.unlink(name, dir_fd=parent_fd)
    except OSError:
        return "error"
    return "removed"


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


def create_workspace_directory(
    root: str | Path,
    relative_path: str,
    *,
    create_mode: int = 0o700,
) -> WorkspaceEntryMutationResult:
    if isinstance(create_mode, bool) or create_mode & ~0o777:
        raise ValueError("create_mode must contain ordinary Unix permission bits")
    components = validate_workspace_relative_path(relative_path)
    with (
        _open_workspace_root(root) as (root_fd, root_stat),
        _workspace_lock(root_fd, exclusive=True),
        _open_parent_directory(root_fd, root_stat.st_dev, components) as (parent_fd, name),
    ):
        try:
            os.mkdir(name, mode=create_mode, dir_fd=parent_fd)
        except FileExistsError as error:
            raise WorkspaceEntryExistsError("Workspace entry already exists.") from error
        except OSError as error:
            if error.errno in {errno.ENOENT, errno.ENOTDIR}:
                raise WorkspaceEntryNotFoundError(
                    "Workspace destination parent does not exist."
                ) from error
            raise WorkspaceIOError("Could not create the workspace directory.") from error
        try:
            created_stat, kind = _stat_entry_from_parent(
                parent_fd,
                name,
                root_device=root_stat.st_dev,
            )
            if kind != "directory" or stat.S_IMODE(created_stat.st_mode) & 0o777 != create_mode:
                raise WorkspaceEntryChangedError(
                    "Workspace directory changed while creation was acknowledged."
                )
            _sync_fd(parent_fd, target="directory")
        except Exception:
            with suppress(OSError):
                os.rmdir(name, dir_fd=parent_fd)
            raise
    return WorkspaceEntryMutationResult(path=relative_path, kind="directory")


def workspace_rename_noreplace_supported() -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    return hasattr(libc, "renameat2") or (
        sys.platform == "darwin" and hasattr(libc, "renameatx_np")
    )


def _rename_noreplace(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source_name)
    destination_bytes = os.fsencode(destination_name)
    result: int
    if hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            source_parent_fd,
            source_bytes,
            destination_parent_fd,
            destination_bytes,
            1,  # RENAME_NOREPLACE
        )
    elif sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        renameatx_np = libc.renameatx_np
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_parent_fd,
            source_bytes,
            destination_parent_fd,
            destination_bytes,
            0x00000004,  # RENAME_EXCL
        )
    else:
        raise WorkspaceOperationUnsupportedError(
            "No-overwrite workspace rename is unavailable on this host."
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise WorkspaceEntryExistsError("Workspace destination already exists.")
    if error_number in {errno.ENOENT, errno.ENOTDIR}:
        raise WorkspaceEntryChangedError("Workspace source or destination parent changed.")
    if error_number in {errno.EXDEV, errno.ELOOP}:
        raise UnsafeWorkspaceEntryError("Workspace rename crossed a safe filesystem boundary.")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
        raise WorkspaceOperationUnsupportedError(
            "No-overwrite workspace rename is unavailable on this filesystem."
        )
    raise WorkspaceIOError("Could not rename the workspace entry.")


def _rename_exchange(
    left_parent_fd: int,
    left_name: str,
    right_parent_fd: int,
    right_name: str,
) -> None:
    """Atomically exchange two existing directory entries.

    Updates use this instead of a check followed by ``replace``: the displaced
    target remains available under the temporary name until its identity and
    bytes have been verified. A stale target can therefore be exchanged back
    without deleting or overwriting it.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    left_bytes = os.fsencode(left_name)
    right_bytes = os.fsencode(right_name)
    result: int
    if hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            left_parent_fd,
            left_bytes,
            right_parent_fd,
            right_bytes,
            2,  # RENAME_EXCHANGE
        )
    elif sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        renameatx_np = libc.renameatx_np
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            left_parent_fd,
            left_bytes,
            right_parent_fd,
            right_bytes,
            0x00000002,  # RENAME_SWAP
        )
    else:
        raise WorkspaceOperationUnsupportedError(
            "Atomic workspace exchange is unavailable on this host."
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.ENOENT, errno.ENOTDIR}:
        raise WorkspaceEntryChangedError("Workspace target changed before exchange.")
    if error_number in {errno.EXDEV, errno.ELOOP}:
        raise UnsafeWorkspaceEntryError("Workspace exchange crossed a safe filesystem boundary.")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
        raise WorkspaceOperationUnsupportedError(
            "Atomic workspace exchange is unavailable on this filesystem."
        )
    raise WorkspaceIOError("Could not exchange the workspace entries.")


def _quarantine_name(operation: str) -> str:
    return f".armada-{operation}-{os.getpid()}-{secrets.token_hex(12)}"


def _restore_quarantined_entry(
    parent_fd: int,
    quarantine_name: str,
    original_name: str,
) -> None:
    try:
        _rename_noreplace(parent_fd, quarantine_name, parent_fd, original_name)
    except WorkspaceIOError as error:
        raise WorkspaceEntryChangedError(
            "Workspace entry changed while the operation was rolled back."
        ) from error
    _sync_fd(parent_fd, target="directory")


def _capture_workspace_entry(
    parent_fd: int,
    name: str,
    *,
    root_device: int,
    expected_kind: Literal["file", "directory"],
    expected_identity: WorkspaceEntryIdentity | None,
    operation: str,
) -> tuple[str, os.stat_result, Literal["file", "directory"]]:
    """Atomically move the current named object aside, then authorize it.

    The unguessable quarantine name means a replacement raced in before the
    rename is captured rather than acted on. A stale capture is restored to its
    original name before the error escapes.
    """

    original_stat, original_kind = _stat_entry_from_parent(
        parent_fd,
        name,
        root_device=root_device,
    )
    _validate_expected_kind(original_kind, expected_kind)
    _validate_expected_identity(original_stat, expected_identity)
    quarantine_name = _quarantine_name(operation)
    _rename_noreplace(parent_fd, name, parent_fd, quarantine_name)
    try:
        captured_stat, actual_kind = _stat_entry_from_parent(
            parent_fd,
            quarantine_name,
            root_device=root_device,
        )
        _validate_expected_kind(actual_kind, expected_kind)
        if _stat_identity(captured_stat) != _stat_identity(original_stat):
            raise WorkspaceEntryChangedError("Workspace entry identity changed during capture.")
    except Exception:
        _restore_quarantined_entry(parent_fd, quarantine_name, name)
        raise
    return quarantine_name, captured_stat, actual_kind


def rename_workspace_entry(
    root: str | Path,
    source_path: str,
    destination_path: str,
    *,
    expected_kind: Literal["file", "directory"],
    expected_identity: WorkspaceEntryIdentity | None = None,
) -> WorkspaceEntryMutationResult:
    source_components = validate_workspace_relative_path(source_path)
    destination_components = validate_workspace_relative_path(destination_path)
    if source_components == destination_components:
        raise WorkspaceEntryExistsError("Workspace destination already exists.")

    with _open_workspace_root(root) as (root_fd, root_stat):  # noqa: SIM117
        with _workspace_lock(root_fd, exclusive=True):
            with _open_parent_directory(root_fd, root_stat.st_dev, source_components) as (
                source_parent_fd,
                source_name,
            ):
                with _open_parent_directory(root_fd, root_stat.st_dev, destination_components) as (
                    destination_parent_fd,
                    destination_name,
                ):
                    if (
                        expected_kind == "directory"
                        and destination_components[: len(source_components)] == source_components
                    ):
                        raise InvalidWorkspacePathError(
                            "A workspace directory cannot be moved inside itself."
                        )
                    quarantine_name, source_stat, actual_kind = _capture_workspace_entry(
                        source_parent_fd,
                        source_name,
                        root_device=root_stat.st_dev,
                        expected_kind=expected_kind,
                        expected_identity=expected_identity,
                        operation="rename",
                    )
                    try:
                        _rename_noreplace(
                            source_parent_fd,
                            quarantine_name,
                            destination_parent_fd,
                            destination_name,
                        )
                    except Exception:
                        _restore_quarantined_entry(
                            source_parent_fd,
                            quarantine_name,
                            source_name,
                        )
                        raise
                    destination_stat, destination_kind = _stat_entry_from_parent(
                        destination_parent_fd,
                        destination_name,
                        root_device=root_stat.st_dev,
                    )
                    if destination_kind != actual_kind or _stat_identity(
                        destination_stat
                    ) != _stat_identity(source_stat):
                        try:
                            _rename_noreplace(
                                destination_parent_fd,
                                destination_name,
                                source_parent_fd,
                                source_name,
                            )
                        except WorkspaceIOError as error:
                            raise WorkspaceEntryChangedError(
                                "Workspace rename could not be rolled back safely."
                            ) from error
                        raise WorkspaceEntryChangedError(
                            "Workspace entry changed while rename was acknowledged."
                        )
                    try:
                        os.stat(source_name, dir_fd=source_parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise WorkspaceEntryChangedError("Workspace source remained after rename.")
                    _sync_fd(source_parent_fd, target="directory")
                    if _stat_identity(os.fstat(source_parent_fd)) != _stat_identity(
                        os.fstat(destination_parent_fd)
                    ):
                        _sync_fd(destination_parent_fd, target="directory")
    return WorkspaceEntryMutationResult(
        path=destination_path,
        kind=expected_kind,
        identity=_entry_identity(destination_stat),
    )


def _open_checked_directory(
    parent_fd: int,
    name: str,
    *,
    root_device: int,
) -> int:
    directory_fd = _open_directory_at(parent_fd, name)
    if os.fstat(directory_fd).st_dev != root_device:
        os.close(directory_fd)
        raise UnsafeWorkspaceEntryError("Workspace directory crosses a filesystem boundary.")
    return directory_fd


def _validate_directory_tree(directory_fd: int, *, root_device: int) -> None:
    try:
        names = os.listdir(directory_fd)
    except OSError as error:
        raise WorkspaceIOError("Could not inspect the workspace directory.") from error
    for name in names:
        if name.casefold() == ".git":
            raise UnsafeWorkspaceEntryError("Git metadata cannot be recursively deleted.")
        entry_stat, kind = _stat_entry_from_parent(
            directory_fd,
            name,
            root_device=root_device,
        )
        if kind == "directory":
            child_fd = _open_checked_directory(
                directory_fd,
                name,
                root_device=root_device,
            )
            try:
                _validate_directory_tree(child_fd, root_device=root_device)
            finally:
                os.close(child_fd)


def _remove_directory_tree(directory_fd: int, *, root_device: int) -> int:
    removed_entries = 0
    try:
        names = os.listdir(directory_fd)
    except OSError as error:
        raise WorkspaceIOError("Could not inspect the workspace directory.") from error
    for name in names:
        _entry_stat, kind = _stat_entry_from_parent(
            directory_fd,
            name,
            root_device=root_device,
        )
        if kind == "directory":
            child_fd = _open_checked_directory(
                directory_fd,
                name,
                root_device=root_device,
            )
            try:
                removed_entries += _remove_directory_tree(
                    child_fd,
                    root_device=root_device,
                )
            finally:
                os.close(child_fd)
            try:
                os.rmdir(name, dir_fd=directory_fd)
            except OSError as error:
                raise WorkspaceEntryChangedError(
                    "Workspace directory changed during recursive deletion."
                ) from error
        else:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError as error:
                raise WorkspaceEntryChangedError(
                    "Workspace file changed during recursive deletion."
                ) from error
        removed_entries += 1
    _sync_fd(directory_fd, target="directory")
    return removed_entries


def delete_workspace_entry(
    root: str | Path,
    relative_path: str,
    *,
    expected_kind: Literal["file", "directory"],
    expected_identity: WorkspaceEntryIdentity | None = None,
    recursive: bool = False,
) -> WorkspaceDeleteResult:
    if not isinstance(recursive, bool):
        raise TypeError("recursive must be a boolean")
    components = validate_workspace_relative_path(relative_path)
    with (
        _open_workspace_root(root) as (root_fd, root_stat),
        _workspace_lock(root_fd, exclusive=True),
        _open_parent_directory(root_fd, root_stat.st_dev, components) as (parent_fd, name),
    ):
        quarantine_name, entry_stat, actual_kind = _capture_workspace_entry(
            parent_fd,
            name,
            root_device=root_stat.st_dev,
            expected_kind=expected_kind,
            expected_identity=expected_identity,
            operation="delete",
        )
        removed_entries = 1
        quarantine_exists = True
        try:
            if actual_kind == "file":
                if recursive:
                    raise ValueError("recursive deletion applies only to directories")
                try:
                    os.unlink(quarantine_name, dir_fd=parent_fd)
                except OSError as error:
                    raise WorkspaceEntryChangedError(
                        "Workspace file changed during deletion."
                    ) from error
                quarantine_exists = False
            elif recursive:
                directory_fd = _open_checked_directory(
                    parent_fd,
                    quarantine_name,
                    root_device=root_stat.st_dev,
                )
                try:
                    if _entry_identity(os.fstat(directory_fd)) != _entry_identity(entry_stat):
                        raise WorkspaceEntryChangedError(
                            "Workspace directory changed before recursive deletion."
                        )
                    _validate_directory_tree(directory_fd, root_device=root_stat.st_dev)
                    removed_entries += _remove_directory_tree(
                        directory_fd,
                        root_device=root_stat.st_dev,
                    )
                finally:
                    os.close(directory_fd)
                try:
                    os.rmdir(quarantine_name, dir_fd=parent_fd)
                except OSError as error:
                    raise WorkspaceEntryChangedError(
                        "Workspace directory changed during recursive deletion."
                    ) from error
                quarantine_exists = False
            else:
                try:
                    os.rmdir(quarantine_name, dir_fd=parent_fd)
                except OSError as error:
                    if error.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                        raise WorkspaceDirectoryNotEmptyError(
                            "Workspace directory is not empty."
                        ) from error
                    raise WorkspaceEntryChangedError(
                        "Workspace directory changed during deletion."
                    ) from error
                quarantine_exists = False
            _sync_fd(parent_fd, target="directory")
        except Exception:
            if quarantine_exists:
                _restore_quarantined_entry(parent_fd, quarantine_name, name)
            raise
    return WorkspaceDeleteResult(
        path=relative_path,
        kind=expected_kind,
        removed_entries=removed_entries,
    )


def atomic_write_workspace_file(
    root: str | Path,
    relative_path: str,
    data: bytes,
    *,
    max_bytes: int,
    expected_version: str | None,
    expected_identity: WorkspaceEntryIdentity | None = None,
    create: bool = False,
    create_mode: int = 0o600,
) -> WorkspaceWriteResult:
    """Replace or create a file atomically after a raw-byte version check.

    `flock` on the workspace root serializes writers that use this module,
    including separate isolation-helper processes. The final publication is an
    atomic exchange: the displaced target is authorized after capture and is
    exchanged back before an error escapes if its identity or bytes are stale.
    """

    _validate_nonnegative_limit(max_bytes)
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > max_bytes:
        raise WorkspaceFileTooLargeError("Workspace file exceeds the write limit.")
    if isinstance(create_mode, bool) or create_mode & ~0o777:
        raise ValueError("create_mode must contain ordinary Unix permission bits")
    _validate_expected_version(expected_version, create=create)
    if create and expected_identity is not None:
        raise ValueError("A create operation cannot have an expected identity")
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
                        expected_identity=expected_identity,
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

                temporary_name = f".armada-write-{os.getpid()}-{secrets.token_hex(12)}"
                temporary_fd: int | None = None
                temporary_exists = False
                published_file: WorkspaceFile | None = None
                authorized_displaced_identity: WorkspaceEntryIdentity | None = None
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
                            expected_identity=expected_identity,
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
                        _rename_exchange(
                            parent_fd,
                            temporary_name,
                            parent_fd,
                            name,
                        )
                        try:
                            displaced, displaced_stat = _read_file_from_parent(
                                parent_fd,
                                temporary_name,
                                root_device=root_stat.st_dev,
                                max_bytes=max_bytes,
                            )
                            if _stat_identity(displaced_stat) != _stat_identity(current_stat):
                                raise WorkspaceEntryChangedError(
                                    "Workspace target identity changed during save publication."
                                )
                            if displaced.version != expected_version:
                                raise WorkspaceVersionConflictError(
                                    "Workspace file changed before it could be saved.",
                                    current=displaced,
                                )
                            authorized_displaced_identity = _entry_identity(displaced_stat)
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
                                    "Workspace file changed while the save was published."
                                )
                            published_file = saved
                        except Exception:
                            target_is_published_file = False
                            try:
                                target_stat, target_kind = _stat_entry_from_parent(
                                    parent_fd,
                                    name,
                                    root_device=root_stat.st_dev,
                                )
                                target_is_published_file = target_kind == "file" and _stat_identity(
                                    target_stat
                                ) == _stat_identity(temporary_stat)
                            except WorkspaceIOError:
                                pass
                            if not target_is_published_file:
                                # An uncooperative writer replaced the published
                                # name after the exchange. Never exchange it into
                                # the cleanup name. The displaced old target may
                                # be retired only if it was already authorized and
                                # still has that exact post-exchange identity.
                                if authorized_displaced_identity is not None:
                                    # From here on cleanup is identity-bound. The
                                    # generic finally block must not unlink this
                                    # name if it was replaced after validation.
                                    temporary_exists = False
                                    cleanup_outcome = _unlink_if_exact_identity(
                                        parent_fd,
                                        temporary_name,
                                        root_device=root_stat.st_dev,
                                        expected_identity=authorized_displaced_identity,
                                    )
                                    if cleanup_outcome == "error":
                                        raise WorkspaceIOError(
                                            "Could not retire the authorized displaced file; "
                                            "it was preserved under the recovery name."
                                        )
                                temporary_exists = False
                                raise WorkspaceEntryChangedError(
                                    "Workspace target changed after save publication."
                                )
                            try:
                                _rename_exchange(
                                    parent_fd,
                                    temporary_name,
                                    parent_fd,
                                    name,
                                )
                            except WorkspaceIOError as rollback_error:
                                temporary_exists = False
                                cleanup_outcome = "missing"
                                if authorized_displaced_identity is not None:
                                    cleanup_outcome = _unlink_if_exact_identity(
                                        parent_fd,
                                        temporary_name,
                                        root_device=root_stat.st_dev,
                                        expected_identity=authorized_displaced_identity,
                                    )
                                raise WorkspaceEntryChangedError(
                                    "Workspace save could not be rolled back safely; "
                                    + (
                                        "the authorized displaced entry was preserved under "
                                        "the recovery name."
                                        if cleanup_outcome == "error"
                                        else (
                                            "an unverified displaced entry was preserved."
                                            if cleanup_outcome == "mismatch"
                                            else "the published file remains in place."
                                        )
                                    )
                                ) from rollback_error
                            raise
                        _unlink_if_present(parent_fd, temporary_name)
                        temporary_exists = False

                    _sync_fd(parent_fd, target="directory")
                    if published_file is not None:
                        return WorkspaceWriteResult(
                            file=published_file,
                            changed=True,
                            created=False,
                        )
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


def _workspace_identity_payload(identity: WorkspaceEntryIdentity) -> dict[str, int]:
    return {
        "device": identity.device,
        "inode": identity.inode,
        "ctime_ns": identity.ctime_ns,
    }


def _workspace_identity_from_payload(payload: object) -> WorkspaceEntryIdentity:
    if not isinstance(payload, Mapping) or set(payload) != {"device", "inode", "ctime_ns"}:
        raise WorkspaceHelperError("Workspace helper returned an invalid identity payload.")
    device = payload["device"]
    inode = payload["inode"]
    ctime_ns = payload["ctime_ns"]
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device < 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode < 0
        or isinstance(ctime_ns, bool)
        or not isinstance(ctime_ns, int)
        or ctime_ns < 0
    ):
        raise WorkspaceHelperError("Workspace helper returned an invalid identity payload.")
    return WorkspaceEntryIdentity(
        device=device,
        inode=inode,
        ctime_ns=ctime_ns,
    )


def _optional_workspace_identity_from_payload(
    payload: object,
) -> WorkspaceEntryIdentity | None:
    if payload is None:
        return None
    return _workspace_identity_from_payload(payload)


def _workspace_raw_metadata_payload(
    metadata: WorkspaceRawFileMetadata,
) -> dict[str, object]:
    return {
        "size": metadata.size,
        "mode": metadata.mode,
        "cache_token": metadata.cache_token,
        "identity": _workspace_identity_payload(metadata.identity),
    }


def _workspace_raw_metadata_from_payload(payload: object) -> WorkspaceRawFileMetadata:
    if not isinstance(payload, Mapping) or set(payload) != {
        "size",
        "mode",
        "cache_token",
        "identity",
    }:
        raise WorkspaceHelperError("Workspace helper returned invalid stream metadata.")
    size = payload["size"]
    mode = payload["mode"]
    cache_token = payload["cache_token"]
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or isinstance(mode, bool)
        or not isinstance(mode, int)
        or mode < 0
        or mode & ~0o777
        or not isinstance(cache_token, str)
        or SHA256_RE.fullmatch(cache_token) is None
    ):
        raise WorkspaceHelperError("Workspace helper returned invalid stream metadata.")
    return WorkspaceRawFileMetadata(
        size=size,
        mode=mode,
        cache_token=cache_token,
        identity=_workspace_identity_from_payload(payload["identity"]),
    )


def _workspace_file_payload(file: WorkspaceFile) -> dict[str, object]:
    if file.identity is None:
        raise WorkspaceHelperError("Workspace file identity is unavailable.")
    return {
        "content_base64": base64.b64encode(file.data).decode("ascii"),
        "version": file.version,
        "size": file.size,
        "mode": file.mode,
        "identity": _workspace_identity_payload(file.identity),
    }


def _workspace_file_from_payload(payload: Mapping[str, object]) -> WorkspaceFile:
    content_base64 = payload.get("content_base64")
    version = payload.get("version")
    size = payload.get("size")
    mode = payload.get("mode")
    identity_payload = payload.get("identity")
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
    return WorkspaceFile(
        data=data,
        version=version,
        size=size,
        mode=mode,
        identity=_workspace_identity_from_payload(identity_payload),
    )


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
        WorkspaceDirectoryNotEmptyError,
        WorkspaceOperationUnsupportedError,
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
        _require_request_keys(
            request,
            {"operation", "root", "path", "max_bytes", "expected_identity"},
        )
        root = request.get("root")
        relative_path = request.get("path")
        max_bytes = request.get("max_bytes")
        expected_identity = _optional_workspace_identity_from_payload(
            request.get("expected_identity")
        )
        if not isinstance(root, str) or not isinstance(relative_path, str):
            raise ValueError("Workspace helper read request has invalid paths")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise ValueError("Workspace helper read request has an invalid limit")
        return {
            "file": _workspace_file_payload(
                read_workspace_file(
                    root,
                    relative_path,
                    max_bytes=max_bytes,
                    expected_identity=expected_identity,
                )
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
                "expected_identity",
                "create",
                "create_mode",
            },
        )
        root = request.get("root")
        relative_path = request.get("path")
        content_base64 = request.get("content_base64")
        max_bytes = request.get("max_bytes")
        expected_version = request.get("expected_version")
        expected_identity = _optional_workspace_identity_from_payload(
            request.get("expected_identity")
        )
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
        write_result = atomic_write_workspace_file(
            root,
            relative_path,
            data,
            max_bytes=max_bytes,
            expected_version=expected_version,
            expected_identity=expected_identity,
            create=create,
            create_mode=create_mode,
        )
        return {
            "file": _workspace_file_payload(write_result.file),
            "changed": write_result.changed,
            "created": write_result.created,
        }
    if operation == "mkdir":
        _require_request_keys(request, {"operation", "root", "path", "create_mode"})
        root = request.get("root")
        relative_path = request.get("path")
        create_mode = request.get("create_mode", 0o700)
        if (
            not isinstance(root, str)
            or not isinstance(relative_path, str)
            or isinstance(create_mode, bool)
            or not isinstance(create_mode, int)
        ):
            raise ValueError("Workspace helper mkdir request is invalid")
        mkdir_result = create_workspace_directory(root, relative_path, create_mode=create_mode)
        return {"path": mkdir_result.path, "kind": mkdir_result.kind}
    if operation == "rename":
        _require_request_keys(
            request,
            {
                "operation",
                "root",
                "source_path",
                "destination_path",
                "expected_kind",
                "expected_identity",
            },
        )
        root = request.get("root")
        source_path = request.get("source_path")
        destination_path = request.get("destination_path")
        raw_expected_kind = request.get("expected_kind")
        expected_identity = _optional_workspace_identity_from_payload(
            request.get("expected_identity")
        )
        if (
            not isinstance(root, str)
            or not isinstance(source_path, str)
            or not isinstance(destination_path, str)
            or raw_expected_kind not in {"file", "directory"}
        ):
            raise ValueError("Workspace helper rename request is invalid")
        rename_expected_kind: Literal["file", "directory"] = (
            "file" if raw_expected_kind == "file" else "directory"
        )
        rename_result = rename_workspace_entry(
            root,
            source_path,
            destination_path,
            expected_kind=rename_expected_kind,
            expected_identity=expected_identity,
        )
        return {
            "path": rename_result.path,
            "kind": rename_result.kind,
            "identity": (
                _workspace_identity_payload(rename_result.identity)
                if rename_result.identity is not None
                else None
            ),
        }
    if operation == "delete":
        _require_request_keys(
            request,
            {
                "operation",
                "root",
                "path",
                "expected_kind",
                "expected_identity",
                "recursive",
            },
        )
        root = request.get("root")
        relative_path = request.get("path")
        raw_expected_kind = request.get("expected_kind")
        expected_identity = _optional_workspace_identity_from_payload(
            request.get("expected_identity")
        )
        recursive = request.get("recursive", False)
        if (
            not isinstance(root, str)
            or not isinstance(relative_path, str)
            or raw_expected_kind not in {"file", "directory"}
            or not isinstance(recursive, bool)
        ):
            raise ValueError("Workspace helper delete request is invalid")
        delete_expected_kind: Literal["file", "directory"] = (
            "file" if raw_expected_kind == "file" else "directory"
        )
        delete_result = delete_workspace_entry(
            root,
            relative_path,
            expected_kind=delete_expected_kind,
            expected_identity=expected_identity,
            recursive=recursive,
        )
        return {
            "path": delete_result.path,
            "kind": delete_result.kind,
            "removed_entries": delete_result.removed_entries,
        }
    if operation == "inspect":
        _require_request_keys(request, {"operation", "root", "entries", "skip_unsafe"})
        root = request.get("root")
        entries_payload = request.get("entries")
        skip_unsafe = request.get("skip_unsafe", False)
        if (
            not isinstance(root, str)
            or not isinstance(entries_payload, list)
            or not isinstance(skip_unsafe, bool)
        ):
            raise ValueError("Workspace helper inspect request is invalid")
        expectations: dict[str, Literal["file", "directory"]] = {}
        for entry_payload in entries_payload:
            if not isinstance(entry_payload, Mapping) or set(entry_payload) != {"path", "kind"}:
                raise ValueError("Workspace helper inspect request is invalid")
            path = entry_payload["path"]
            kind = entry_payload["kind"]
            if not isinstance(path, str) or kind not in {"file", "directory"}:
                raise ValueError("Workspace helper inspect request is invalid")
            if path in expectations:
                raise ValueError("Workspace helper inspect request contains duplicate paths")
            expectations[path] = "file" if kind == "file" else "directory"
        inspected = inspect_workspace_entries(root, expectations, skip_unsafe=skip_unsafe)
        return {
            "entries": [
                {
                    "path": entry.path,
                    "kind": entry.kind,
                    "identity": _workspace_identity_payload(entry.identity),
                }
                for entry in inspected
            ]
        }
    if operation == "inspect_empty_directory":
        _require_request_keys(
            request,
            {"operation", "root", "path", "expected_identity"},
        )
        root = request.get("root")
        relative_path = request.get("path")
        expected_identity = _optional_workspace_identity_from_payload(
            request.get("expected_identity")
        )
        if not isinstance(root, str) or not isinstance(relative_path, str):
            raise ValueError("Workspace helper directory inspection request is invalid")
        identity = inspect_empty_workspace_directory(
            root,
            relative_path,
            expected_identity=expected_identity,
        )
        return {"identity": _workspace_identity_payload(identity)}
    raise ValueError("Workspace helper request has an unknown operation")


def _helper_command(
    *,
    isolation_username: str | None,
    helper_path: str | Path | None,
    python_executable: str | Path | None,
) -> list[str]:
    if isolation_username is not None:
        if ARMADA_ISOLATION_USERNAME_RE.fullmatch(isolation_username) is None:
            raise ValueError("Invalid isolation username")
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


def _read_exact_with_deadline(
    stream: object,
    byte_count: int,
    *,
    deadline: float,
) -> bytes:
    fileno = getattr(stream, "fileno", None)
    if not callable(fileno):
        raise WorkspaceHelperError("Workspace stream helper pipe is unavailable.")
    fd = fileno()
    chunks: list[bytes] = []
    remaining = byte_count
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(fd, False)
        selector.register(fd, selectors.EVENT_READ)
        while remaining:
            wait_seconds = deadline - time.monotonic()
            if wait_seconds <= 0 or not selector.select(wait_seconds):
                raise WorkspaceHelperError("Workspace stream helper timed out.")
            try:
                chunk = os.read(fd, remaining)
            except BlockingIOError:  # nocoverage: selector reported readiness.
                continue
            if not chunk:
                raise WorkspaceHelperError("Workspace stream helper ended before its header.")
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        selector.close()
        with suppress(OSError):
            os.set_blocking(fd, True)
    return b"".join(chunks)


def _terminate_helper_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            process.kill()
        process.wait()


class WorkspaceHelperRawFileStream(Iterator[bytes]):
    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        metadata: WorkspaceRawFileMetadata,
        chunk_bytes: int,
        deadline: float,
    ) -> None:
        assert process.stdout is not None
        self._process = process
        self._stdout = process.stdout
        self._remaining = metadata.size
        self._chunk_bytes = chunk_bytes
        self._deadline = deadline
        self._closed = False
        self.metadata = metadata

    @override
    def __iter__(self) -> WorkspaceHelperRawFileStream:
        return self

    @override
    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        if self._remaining:
            try:
                chunk = _read_exact_with_deadline(
                    self._stdout,
                    min(self._chunk_bytes, self._remaining),
                    deadline=self._deadline,
                )
            except (OSError, WorkspaceHelperError) as error:
                self.close()
                raise WorkspaceHelperError("Workspace stream helper failed.") from error
            self._remaining -= len(chunk)
            return chunk
        remaining_timeout = self._deadline - time.monotonic()
        if remaining_timeout <= 0:
            self.close()
            raise WorkspaceHelperError("Workspace stream helper timed out.")
        try:
            returncode = self._process.wait(timeout=remaining_timeout)
        except subprocess.TimeoutExpired as error:
            self.close()
            raise WorkspaceHelperError("Workspace stream helper did not finish.") from error
        if self._stdout.read(1) or returncode != 0:
            self.close()
            raise WorkspaceHelperError("Workspace stream helper failed.")
        self._finish_close()
        raise StopIteration

    def _finish_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stdout.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _terminate_helper_process(self._process)
        self._stdout.close()

    def __del__(self) -> None:  # nocoverage: defensive cleanup for abandoned iterators.
        self.close()


def open_workspace_file_stream_via_helper(
    root: str | Path,
    relative_path: str,
    *,
    expected_identity: WorkspaceEntryIdentity | None = None,
    chunk_bytes: int = 64 * 1024,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
    timeout: float = 20,
) -> WorkspaceHelperRawFileStream:
    if isinstance(chunk_bytes, bool) or chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be a positive integer")
    if isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout must be positive")
    deadline = time.monotonic() + timeout
    request = {
        "operation": "stream",
        "root": os.fspath(root),
        "path": relative_path,
        "expected_identity": (
            _workspace_identity_payload(expected_identity)
            if expected_identity is not None
            else None
        ),
    }
    encoded_request = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(encoded_request) > MAX_CLI_REQUEST_BYTES:
        raise WorkspaceHelperError("Workspace stream helper request exceeds the protocol limit.")
    command = _helper_command(
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    command[-1] = "--stream"
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as error:
        raise WorkspaceHelperError("Workspace stream helper could not be executed.") from error
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(encoded_request)
        process.stdin.close()
        header_size_bytes = _read_exact_with_deadline(process.stdout, 8, deadline=deadline)
        header_size = int.from_bytes(header_size_bytes, "big")
        if not 1 <= header_size <= MAX_STREAM_HEADER_BYTES:
            raise WorkspaceHelperError("Workspace stream helper returned an invalid header.")
        encoded_header = _read_exact_with_deadline(
            process.stdout,
            header_size,
            deadline=deadline,
        )
        try:
            header = json.loads(encoded_header)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkspaceHelperError(
                "Workspace stream helper returned invalid metadata."
            ) from error
        if not isinstance(header, dict) or set(header) != {"ok", "result"}:
            raise WorkspaceHelperError("Workspace stream helper returned invalid metadata.")
        ok = header["ok"]
        result = header["result"]
        if not isinstance(ok, bool) or not isinstance(result, dict):
            raise WorkspaceHelperError("Workspace stream helper returned invalid metadata.")
        if not ok:
            _raise_helper_error(result)
        metadata = _workspace_raw_metadata_from_payload(result)
        return WorkspaceHelperRawFileStream(
            process=process,
            metadata=metadata,
            chunk_bytes=chunk_bytes,
            deadline=deadline,
        )
    except Exception:
        _terminate_helper_process(process)
        if process.stdout is not None:
            process.stdout.close()
        raise


class WorkspaceStagedRawFileStream(Iterator[bytes]):
    """An immutable, unlinked disk snapshot safe to expose over HTTP."""

    def __init__(
        self,
        *,
        file: BinaryIO,
        metadata: WorkspaceRawFileMetadata,
        chunk_bytes: int,
    ) -> None:
        self._file = file
        self._remaining = metadata.size
        self._chunk_bytes = chunk_bytes
        self._closed = False
        self.metadata = metadata

    @override
    def __iter__(self) -> WorkspaceStagedRawFileStream:
        return self

    @override
    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        if not self._remaining:
            self.close()
            raise StopIteration
        try:
            chunk = self._file.read(min(self._chunk_bytes, self._remaining))
        except OSError as error:
            self.close()
            raise WorkspaceIOError("Could not read the staged workspace file.") from error
        if not chunk:
            self.close()
            raise WorkspaceIOError("The staged workspace file was truncated.")
        self._remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._file.close()

    def __del__(self) -> None:  # nocoverage: defensive cleanup for abandoned responses.
        self.close()


def stage_workspace_raw_stream(
    source: WorkspaceRawFileStream | WorkspaceHelperRawFileStream,
    *,
    max_bytes: int,
    chunk_bytes: int = 64 * 1024,
) -> WorkspaceStagedRawFileStream:
    """Fully verify a bounded source into an unlinked disk file before exposure."""

    _validate_nonnegative_limit(max_bytes)
    if isinstance(chunk_bytes, bool) or chunk_bytes <= 0:
        source.close()
        raise ValueError("chunk_bytes must be a positive integer")
    if source.metadata.size > max_bytes:
        source.close()
        raise WorkspaceFileTooLargeError("Workspace file exceeds the raw stream limit.")

    try:
        staged_file = tempfile.TemporaryFile(prefix="armada-workspace-raw-")  # noqa: SIM115
    except OSError as error:
        source.close()
        raise WorkspaceIOError("Could not stage the workspace raw file.") from error
    digest = hashlib.sha256()
    total = 0
    try:
        for chunk in source:
            total += len(chunk)
            if total > max_bytes:
                raise WorkspaceFileTooLargeError("Workspace file exceeds the raw stream limit.")
            _write_all(staged_file.fileno(), chunk)
            digest.update(chunk)
        if total != source.metadata.size:
            raise WorkspaceEntryChangedError("Workspace raw stream size changed during staging.")
        staged_file.flush()
        staged_file.seek(0)
    except Exception:
        staged_file.close()
        raise
    finally:
        source.close()

    return WorkspaceStagedRawFileStream(
        file=staged_file,
        metadata=WorkspaceRawFileMetadata(
            size=total,
            mode=source.metadata.mode,
            cache_token=digest.hexdigest(),
            identity=source.metadata.identity,
        ),
        chunk_bytes=chunk_bytes,
    )


def read_workspace_file_via_helper(
    root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
    expected_identity: WorkspaceEntryIdentity | None = None,
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
            "expected_identity": (
                _workspace_identity_payload(expected_identity)
                if expected_identity is not None
                else None
            ),
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    file_payload = result.get("file")
    if not isinstance(file_payload, Mapping):
        raise WorkspaceHelperError("Workspace helper omitted its file payload.")
    return _workspace_file_from_payload(file_payload)


def inspect_workspace_entries_via_helper(
    root: str | Path,
    expectations: Mapping[str, Literal["file", "directory"]],
    *,
    skip_unsafe: bool = False,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> tuple[WorkspaceInspectedEntry, ...]:
    result = invoke_workspace_io_helper(
        {
            "operation": "inspect",
            "root": os.fspath(root),
            "entries": [
                {"path": path, "kind": kind} for path, kind in sorted(expectations.items())
            ],
            "skip_unsafe": skip_unsafe,
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    entries_payload = result.get("entries")
    if not isinstance(entries_payload, list):
        raise WorkspaceHelperError("Workspace helper omitted inspection entries.")
    inspected: list[WorkspaceInspectedEntry] = []
    seen: set[str] = set()
    for entry_payload in entries_payload:
        if not isinstance(entry_payload, Mapping) or set(entry_payload) != {
            "path",
            "kind",
            "identity",
        }:
            raise WorkspaceHelperError("Workspace helper returned invalid inspection data.")
        path = entry_payload["path"]
        kind = entry_payload["kind"]
        if (
            not isinstance(path, str)
            or path in seen
            or path not in expectations
            or kind != expectations[path]
        ):
            raise WorkspaceHelperError("Workspace helper returned invalid inspection data.")
        expected_kind = expectations[path]
        inspected.append(
            WorkspaceInspectedEntry(
                path=path,
                kind=expected_kind,
                identity=_workspace_identity_from_payload(entry_payload["identity"]),
            )
        )
        seen.add(path)
    if not skip_unsafe and seen != set(expectations):
        raise WorkspaceHelperError("Workspace helper omitted inspection entries.")
    return tuple(inspected)


def atomic_write_workspace_file_via_helper(
    root: str | Path,
    relative_path: str,
    data: bytes,
    *,
    max_bytes: int,
    expected_version: str | None,
    expected_identity: WorkspaceEntryIdentity | None = None,
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
            "expected_identity": (
                _workspace_identity_payload(expected_identity)
                if expected_identity is not None
                else None
            ),
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


def _entry_mutation_result_from_helper(
    result: Mapping[str, object],
    *,
    expected_path: str,
    expected_kind: Literal["file", "directory"],
) -> WorkspaceEntryMutationResult:
    path = result.get("path")
    kind = result.get("kind")
    if path != expected_path or kind != expected_kind:
        raise WorkspaceHelperError("Workspace helper returned an invalid mutation result.")
    identity_payload = result.get("identity")
    identity = (
        _workspace_identity_from_payload(identity_payload) if identity_payload is not None else None
    )
    return WorkspaceEntryMutationResult(
        path=expected_path,
        kind=expected_kind,
        identity=identity,
    )


def create_workspace_directory_via_helper(
    root: str | Path,
    relative_path: str,
    *,
    create_mode: int = 0o700,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> WorkspaceEntryMutationResult:
    result = invoke_workspace_io_helper(
        {
            "operation": "mkdir",
            "root": os.fspath(root),
            "path": relative_path,
            "create_mode": create_mode,
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    return _entry_mutation_result_from_helper(
        result,
        expected_path=relative_path,
        expected_kind="directory",
    )


def inspect_empty_workspace_directory_via_helper(
    root: str | Path,
    relative_path: str,
    *,
    expected_identity: WorkspaceEntryIdentity | None = None,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> WorkspaceEntryIdentity:
    result = invoke_workspace_io_helper(
        {
            "operation": "inspect_empty_directory",
            "root": os.fspath(root),
            "path": relative_path,
            "expected_identity": (
                _workspace_identity_payload(expected_identity)
                if expected_identity is not None
                else None
            ),
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    if set(result) != {"identity"}:
        raise WorkspaceHelperError(
            "Workspace helper returned an invalid directory inspection result."
        )
    return _workspace_identity_from_payload(result["identity"])


def rename_workspace_entry_via_helper(
    root: str | Path,
    source_path: str,
    destination_path: str,
    *,
    expected_kind: Literal["file", "directory"],
    expected_identity: WorkspaceEntryIdentity | None = None,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> WorkspaceEntryMutationResult:
    result = invoke_workspace_io_helper(
        {
            "operation": "rename",
            "root": os.fspath(root),
            "source_path": source_path,
            "destination_path": destination_path,
            "expected_kind": expected_kind,
            "expected_identity": (
                _workspace_identity_payload(expected_identity)
                if expected_identity is not None
                else None
            ),
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    return _entry_mutation_result_from_helper(
        result,
        expected_path=destination_path,
        expected_kind=expected_kind,
    )


def delete_workspace_entry_via_helper(
    root: str | Path,
    relative_path: str,
    *,
    expected_kind: Literal["file", "directory"],
    expected_identity: WorkspaceEntryIdentity | None = None,
    recursive: bool = False,
    isolation_username: str | None = None,
    helper_path: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> WorkspaceDeleteResult:
    result = invoke_workspace_io_helper(
        {
            "operation": "delete",
            "root": os.fspath(root),
            "path": relative_path,
            "expected_kind": expected_kind,
            "expected_identity": (
                _workspace_identity_payload(expected_identity)
                if expected_identity is not None
                else None
            ),
            "recursive": recursive,
        },
        isolation_username=isolation_username,
        helper_path=helper_path,
        python_executable=python_executable,
    )
    mutation = _entry_mutation_result_from_helper(
        result,
        expected_path=relative_path,
        expected_kind=expected_kind,
    )
    removed_entries = result.get("removed_entries")
    if (
        isinstance(removed_entries, bool)
        or not isinstance(removed_entries, int)
        or removed_entries < 1
    ):
        raise WorkspaceHelperError("Workspace helper returned an invalid delete result.")
    return WorkspaceDeleteResult(
        path=mutation.path,
        kind=mutation.kind,
        removed_entries=removed_entries,
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


def _write_stream_header(response: Mapping[str, object]) -> None:
    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
    if not 1 <= len(encoded) <= MAX_STREAM_HEADER_BYTES:
        raise WorkspaceHelperError("Workspace stream helper header is invalid.")
    sys.stdout.buffer.write(len(encoded).to_bytes(8, "big"))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _stream_cli_request() -> int:
    stream: WorkspaceRawFileStream | None = None
    try:
        request = _read_cli_request()
        _require_request_keys(
            request,
            {"operation", "root", "path", "expected_identity"},
        )
        root = request.get("root")
        relative_path = request.get("path")
        if (
            request.get("operation") != "stream"
            or not isinstance(root, str)
            or not isinstance(relative_path, str)
        ):
            raise ValueError("Workspace stream helper request is invalid")
        expected_identity = _optional_workspace_identity_from_payload(
            request.get("expected_identity")
        )
        stream = open_workspace_file_stream(
            root,
            relative_path,
            expected_identity=expected_identity,
        )
    except WorkspaceIOError as error:
        _write_stream_header({"ok": False, "result": _error_payload(error)})
        return 0
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        _write_stream_header(
            {
                "ok": False,
                "result": _error_payload(
                    WorkspaceHelperError("Workspace stream helper request is invalid.")
                ),
            }
        )
        return 0

    assert stream is not None
    try:
        _write_stream_header(
            {"ok": True, "result": _workspace_raw_metadata_payload(stream.metadata)}
        )
        for chunk in stream:
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        return 0
    except WorkspaceIOError:
        return 1
    finally:
        stream.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Perform contained workspace I/O.")
    parser.add_argument("--stdio", action="store_true", help="use the internal JSON/stdio protocol")
    parser.add_argument("--stream", action="store_true", help="stream one contained file")
    args = parser.parse_args(argv)
    if args.stdio == args.stream:
        parser.error("exactly one helper protocol is required")
    if args.stream:
        return _stream_cli_request()
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
