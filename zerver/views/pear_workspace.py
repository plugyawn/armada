from __future__ import annotations

import logging
import re
from typing import Annotated
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from pydantic import Json

from zerver.lib.exceptions import JsonableError
from zerver.lib.pear_workspace_index import (
    WorkspaceCursorError,
    WorkspaceEntryIdError,
    WorkspaceIndexEntry,
)
from zerver.lib.pear_workspace_io import (
    InvalidWorkspacePathError,
    UnsafeWorkspaceEntryError,
    WorkspaceEntryChangedError,
    WorkspaceEntryNotFoundError,
    WorkspaceFile,
    WorkspaceFileTooLargeError,
    WorkspaceInvalidUTF8Error,
    WorkspaceIOError,
    WorkspaceVersionConflictError,
    decode_workspace_text,
    validate_workspace_relative_path,
)
from zerver.lib.pear_workspace_resources import (
    PEAR_WORKSPACE_RAW_MAX_BYTES,
    PEAR_WORKSPACE_TEXT_MAX_BYTES,
    WorkspaceSnapshot,
    WorkspaceSnapshotError,
    finish_workspace_write,
    get_workspace_snapshot,
    read_workspace_node_file,
    refresh_workspace_snapshot,
    resolve_stale_retry_entry,
    workspace_language,
    workspace_mime_type,
    workspace_rich_mode_eligible,
    write_workspace_node_file,
)
from zerver.lib.response import json_response, json_success
from zerver.lib.streams import access_stream_by_name
from zerver.lib.typed_endpoint import INTENTIONALLY_UNDOCUMENTED, ApiParamConfig, typed_endpoint
from zerver.models.bots import PearWorkspaceNode
from zerver.models.users import UserProfile

logger = logging.getLogger(__name__)


def _workspace_error(
    *,
    code: str,
    message: str,
    status: int,
    revision: int | None = None,
    data: dict[str, object] | None = None,
) -> HttpResponse:
    payload: dict[str, object] = {"error_code": code}
    if revision is not None:
        payload["workspace_revision"] = revision
    if data is not None:
        payload.update(data)
    return json_response("error", msg=message, data=payload, status=status)


def _access_workspace_node(
    user_profile: UserProfile,
    workspace_uuid: UUID,
) -> PearWorkspaceNode:
    node = (
        PearWorkspaceNode.objects.select_related("project")
        .filter(realm=user_profile.realm, uuid=workspace_uuid)
        .first()
    )
    if node is None:
        raise JsonableError(_("Pear workspace not found."))
    if not node.stream_name:
        raise JsonableError(_("Pear workspace has no channel."))
    access_stream_by_name(user_profile, node.stream_name)
    if not node.workdir.strip():
        raise JsonableError(_("Pear workspace storage is unavailable."))
    return node


def _entry_payload(entry: WorkspaceIndexEntry) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "path": entry.path,
        "name": entry.name,
        "kind": entry.kind,
        "status": entry.status,
        "has_children": entry.has_children,
        "size": None,
    }


def _workspace_descriptor_payload(
    node: PearWorkspaceNode,
    snapshot: WorkspaceSnapshot,
) -> dict[str, object]:
    default_entry: WorkspaceIndexEntry | None
    try:
        candidate = snapshot.index.entry_for_path("README.md")
        default_entry = (
            candidate if candidate.kind == "file" and candidate.status != "deleted" else None
        )
    except WorkspaceEntryIdError:
        default_entry = None
    can_write = node.lifecycle_status == PearWorkspaceNode.ACTIVE
    topic_label = node.topic_name or _("general chat")
    return {
        "uuid": snapshot.workspace_uuid,
        "display_root": f"{node.stream_name} > {topic_label}",
        "stream": node.stream_name,
        "topic": node.topic_name,
        "branch": snapshot.branch,
        "head": snapshot.head,
        "dirty": snapshot.dirty_count > 0,
        "dirty_count": snapshot.dirty_count,
        "workspace_revision": snapshot.revision,
        "root": _entry_payload(snapshot.index.root),
        "default_path": default_entry.path if default_entry is not None else None,
        "default_entry": _entry_payload(default_entry) if default_entry is not None else None,
        "capabilities": {
            "read_file": True,
            "write_file": can_write,
            "refresh": True,
            "create_entry": False,
            "rename_entry": False,
            "delete_entry": False,
            "history": False,
        },
        "limits": {
            "text_file_max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES,
            "raw_file_max_bytes": PEAR_WORKSPACE_RAW_MAX_BYTES,
        },
    }


def _snapshot_or_error(node: PearWorkspaceNode) -> WorkspaceSnapshot | HttpResponse:
    try:
        return get_workspace_snapshot(node)
    except (WorkspaceSnapshotError, WorkspaceIOError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not inspect this workspace.",
            status=503,
            revision=int(node.content_revision),
        )


def _resolve_file_entry(
    snapshot: WorkspaceSnapshot,
    entry_id: str,
) -> WorkspaceIndexEntry | HttpResponse:
    try:
        entry = snapshot.index.entry_for_id(entry_id)
    except WorkspaceEntryIdError:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    if entry.kind != "file":
        return _workspace_error(
            code="unsupported_file",
            message="Workspace entry is not a file.",
            status=415,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry)},
        )
    return entry


def _text_file_payload(
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
    file: WorkspaceFile,
    *,
    edit_revision: int | None = None,
    include_content: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_uuid": snapshot.workspace_uuid,
        "workspace_revision": snapshot.revision,
        "entry_id": entry.entry_id,
        "path": entry.path,
        "version": file.version,
        "size": file.size,
        "mime_type": workspace_mime_type(entry.path),
        "language": workspace_language(entry.path),
        "editable": False,
        "rich_mode_eligible": workspace_rich_mode_eligible(entry.path),
        "max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES,
    }
    if edit_revision is not None:
        payload["edit_revision"] = edit_revision
    try:
        text_file = decode_workspace_text(file)
        if "\0" in text_file.text:
            raise WorkspaceInvalidUTF8Error("Workspace file contains NUL bytes.")
    except WorkspaceInvalidUTF8Error:
        return payload
    payload["editable"] = True
    if include_content:
        payload["content"] = text_file.text
    return payload


def _unsupported_file_response(
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
    *,
    file: WorkspaceFile | None = None,
) -> HttpResponse:
    metadata: dict[str, object] = {
        "workspace_uuid": snapshot.workspace_uuid,
        "workspace_revision": snapshot.revision,
        "entry_id": entry.entry_id,
        "path": entry.path,
        "mime_type": workspace_mime_type(entry.path),
        "language": workspace_language(entry.path),
        "editable": False,
        "rich_mode_eligible": False,
        "max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES,
    }
    if file is not None:
        metadata.update({"version": file.version, "size": file.size})
    return _workspace_error(
        code="unsupported_file",
        message="Workspace file is not editable UTF-8 text.",
        status=415,
        revision=snapshot.revision,
        data={"file": metadata},
    )


def _read_text_file(
    node: PearWorkspaceNode,
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
) -> tuple[WorkspaceFile, dict[str, object]] | HttpResponse:
    try:
        file = read_workspace_node_file(
            node,
            entry.path,
            max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
        )
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry), "max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=snapshot.revision,
        )
    except (UnsafeWorkspaceEntryError, InvalidWorkspacePathError):
        return _unsupported_file_response(snapshot, entry)
    except WorkspaceIOError:
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not read this workspace file.",
            status=503,
            revision=snapshot.revision,
        )
    payload = _text_file_payload(snapshot, entry, file)
    if not payload["editable"]:
        return _unsupported_file_response(snapshot, entry, file=file)
    return file, payload


@typed_endpoint
def get_pear_workspace_descriptor_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    return json_success(
        request,
        data={"pear_workspace": _workspace_descriptor_payload(node, snapshot)},
    )


@typed_endpoint
def get_pear_workspace_tree_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    directory_id: Annotated[
        str | None,
        ApiParamConfig("directory_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    cursor: Annotated[
        str | None,
        ApiParamConfig("cursor", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    page_size: Annotated[
        int,
        ApiParamConfig("page_size", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = 200,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    try:
        page = snapshot.index.tree_page(
            directory_id=directory_id,
            cursor=cursor,
            page_size=page_size,
        )
        directory = (
            snapshot.index.root
            if directory_id is None
            else snapshot.index.entry_for_id(directory_id)
        )
    except WorkspaceCursorError:
        return _workspace_error(
            code="stale_cursor",
            message="Workspace tree cursor is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    except WorkspaceEntryIdError:
        return _workspace_error(
            code="stale_entry",
            message="Workspace directory is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    except ValueError as error:
        raise JsonableError(str(error))
    return json_success(
        request,
        data={
            "pear_workspace_tree": {
                "workspace_uuid": snapshot.workspace_uuid,
                "workspace_revision": snapshot.revision,
                "directory": _entry_payload(directory),
                "entries": [_entry_payload(entry) for entry in page.entries],
                "next_cursor": page.next_cursor,
            }
        },
    )


def _ancestor_payloads(
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
) -> list[dict[str, object]]:
    components = entry.path.split("/")[:-1]
    ancestors = [snapshot.index.root]
    ancestors.extend(
        snapshot.index.entry_for_path("/".join(components[: offset + 1]))
        for offset in range(len(components))
    )
    return [_entry_payload(ancestor) for ancestor in ancestors]


@typed_endpoint
def get_pear_workspace_search_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    query: Annotated[
        str,
        ApiParamConfig("q", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    limit: Annotated[
        int,
        ApiParamConfig("limit", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = 100,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    try:
        matches = snapshot.index.search(query, limit=limit)
    except ValueError as error:
        raise JsonableError(str(error))
    entries: list[dict[str, object]] = []
    for entry in matches:
        payload = _entry_payload(entry)
        payload["ancestors"] = _ancestor_payloads(snapshot, entry)
        entries.append(payload)
    return json_success(
        request,
        data={
            "pear_workspace_search": {
                "workspace_uuid": snapshot.workspace_uuid,
                "workspace_revision": snapshot.revision,
                "query": query,
                "entries": entries,
            }
        },
    )


@typed_endpoint
def get_pear_workspace_resource_file_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    result = _read_text_file(node, snapshot, entry)
    if isinstance(result, HttpResponse):
        return result
    _file, payload = result
    return json_success(request, data={"pear_workspace_file": payload})


def _stale_workspace_response(snapshot: WorkspaceSnapshot) -> HttpResponse:
    return _workspace_error(
        code="stale_workspace",
        message="Workspace changed; refresh before saving.",
        status=409,
        revision=snapshot.revision,
    )


@typed_endpoint
def put_pear_workspace_resource_file_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    expected_path: Annotated[
        str,
        ApiParamConfig("expected_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    content: Annotated[
        str,
        ApiParamConfig("content", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    base_version: Annotated[
        str,
        ApiParamConfig("base_version", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    workspace_revision: Annotated[
        int,
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    edit_revision: Annotated[
        int,
        ApiParamConfig("edit_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if node.lifecycle_status != PearWorkspaceNode.ACTIVE:
        return _workspace_error(
            code="workspace_read_only",
            message="Workspace is read-only.",
            status=403,
            revision=snapshot.revision,
        )
    if edit_revision < 0:
        raise JsonableError(_("Invalid edit revision."))
    try:
        content_bytes = content.encode("utf-8", errors="strict")
        normalized_path = "/".join(validate_workspace_relative_path(expected_path))
    except (UnicodeEncodeError, InvalidWorkspacePathError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry path is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    if len(content_bytes) > PEAR_WORKSPACE_TEXT_MAX_BYTES:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    if re.fullmatch(r"[0-9a-f]{64}", base_version) is None:
        raise JsonableError(_("Invalid base version."))

    if workspace_revision != snapshot.revision:
        if workspace_revision < 0 or workspace_revision > snapshot.revision:
            return _stale_workspace_response(snapshot)
        try:
            current_entry = resolve_stale_retry_entry(
                snapshot,
                requested_revision=workspace_revision,
                entry_id=entry_id,
                expected_path=normalized_path,
            )
            current_file = read_workspace_node_file(
                node,
                current_entry.path,
                max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
            )
        except (
            WorkspaceEntryIdError,
            WorkspaceIOError,
            WorkspaceFileTooLargeError,
        ):
            return _stale_workspace_response(snapshot)
        if current_file.data != content_bytes:
            return _stale_workspace_response(snapshot)
        payload = _text_file_payload(
            snapshot,
            current_entry,
            current_file,
            edit_revision=edit_revision,
        )
        if not payload["editable"]:
            return _unsupported_file_response(snapshot, current_entry, file=current_file)
        return json_success(request, data={"pear_workspace_file": payload})

    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    if entry.path != normalized_path:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry path is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    authority_is_current = PearWorkspaceNode.objects.filter(
        id=node.id,
        realm=user_profile.realm,
        lifecycle_status=PearWorkspaceNode.ACTIVE,
        content_revision=snapshot.revision,
    ).exists()
    if not authority_is_current:
        node.refresh_from_db()
        if node.lifecycle_status != PearWorkspaceNode.ACTIVE:
            return _workspace_error(
                code="workspace_read_only",
                message="Workspace is read-only.",
                status=403,
                revision=int(node.content_revision),
            )
        latest_snapshot = _snapshot_or_error(node)
        if isinstance(latest_snapshot, HttpResponse):
            return latest_snapshot
        return _stale_workspace_response(latest_snapshot)
    try:
        result = write_workspace_node_file(
            node,
            entry.path,
            content_bytes,
            expected_version=base_version,
        )
    except WorkspaceVersionConflictError as error:
        remote_file = error.current
        data: dict[str, object] = {}
        if remote_file is not None:
            remote_payload = _text_file_payload(snapshot, entry, remote_file)
            if remote_payload["editable"]:
                data["remote_file"] = remote_payload
        return _workspace_error(
            code="version_conflict",
            message="Workspace file changed; reload before saving.",
            status=409,
            revision=snapshot.revision,
            data=data,
        )
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=snapshot.revision,
        )
    except UnsafeWorkspaceEntryError:
        return _unsupported_file_response(snapshot, entry)
    except (WorkspaceIOError, ValueError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not save this workspace file.",
            status=503,
            revision=snapshot.revision,
        )

    try:
        saved_snapshot = finish_workspace_write(
            node,
            requested_revision=workspace_revision,
            base_version=base_version,
            result=result,
        )
        saved_entry = saved_snapshot.index.entry_for_path(entry.path)
    except (WorkspaceSnapshotError, WorkspaceEntryIdError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Workspace file was saved but its index could not be refreshed.",
            status=503,
            revision=int(node.content_revision),
        )
    payload = _text_file_payload(
        saved_snapshot,
        saved_entry,
        result.file,
        edit_revision=edit_revision,
    )
    logger.info(
        "Pear workspace file saved workspace=%s revision=%s entry=%s actor=%s changed=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        saved_entry.entry_id,
        user_profile.id,
        result.changed,
    )
    return json_success(request, data={"pear_workspace_file": payload})


@typed_endpoint
def get_pear_workspace_raw_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    download: Annotated[
        Json[bool] | None,
        ApiParamConfig("download", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    try:
        file = read_workspace_node_file(
            node,
            entry.path,
            max_bytes=PEAR_WORKSPACE_RAW_MAX_BYTES,
        )
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the raw download size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_RAW_MAX_BYTES},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=snapshot.revision,
        )
    except (UnsafeWorkspaceEntryError, InvalidWorkspacePathError):
        return _unsupported_file_response(snapshot, entry)
    except WorkspaceIOError:
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not read this workspace file.",
            status=503,
            revision=snapshot.revision,
        )
    response = HttpResponse(file.data, content_type=workspace_mime_type(entry.path))
    response["Content-Disposition"] = content_disposition_header(bool(download), entry.name) or (
        "attachment" if download else "inline"
    )
    response["Content-Length"] = str(file.size)
    response["ETag"] = f'"{file.version}"'
    response["Cache-Control"] = "private, max-age=0, must-revalidate"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Pear-Workspace-Revision"] = str(snapshot.revision)
    return response


@typed_endpoint
def post_pear_workspace_refresh_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    try:
        snapshot, changed = refresh_workspace_snapshot(node)
    except (WorkspaceSnapshotError, WorkspaceIOError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not refresh this workspace.",
            status=503,
            revision=int(node.content_revision),
        )
    return json_success(
        request,
        data={
            "pear_workspace_refresh": {
                "changed": changed,
                "workspace": _workspace_descriptor_payload(node, snapshot),
            }
        },
    )
