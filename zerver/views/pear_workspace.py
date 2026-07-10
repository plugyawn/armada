from __future__ import annotations

import logging
import re
from typing import Annotated, Literal
from uuid import UUID

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
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
    WorkspaceDirectoryNotEmptyError,
    WorkspaceEntryChangedError,
    WorkspaceEntryExistsError,
    WorkspaceEntryNotFoundError,
    WorkspaceFile,
    WorkspaceFileTooLargeError,
    WorkspaceInvalidUTF8Error,
    WorkspaceIOError,
    WorkspaceOperationUnsupportedError,
    WorkspaceVersionConflictError,
    WorkspaceWriteResult,
    decode_workspace_text,
    validate_workspace_relative_path,
)
from zerver.lib.pear_workspace_resources import (
    PEAR_WORKSPACE_TEXT_MAX_BYTES,
    WorkspaceDestinationPolicyError,
    WorkspaceHistoryCursorError,
    WorkspaceMutationReadOnlyError,
    WorkspaceMutationStaleError,
    WorkspaceSnapshot,
    WorkspaceSnapshotError,
    create_workspace_node_directory,
    create_workspace_node_file,
    delete_workspace_node_entry,
    finish_workspace_entry_mutation,
    get_workspace_file_observation,
    get_workspace_history,
    get_workspace_mutation_receipt,
    get_workspace_snapshot,
    lock_workspace_mutation,
    open_workspace_node_file_stream,
    read_workspace_node_file,
    record_completed_workspace_mutation,
    record_pending_workspace_mutation,
    record_workspace_file_observation,
    refresh_workspace_snapshot,
    refresh_workspace_snapshot_after_conflict,
    rename_workspace_node_entry,
    validate_workspace_destination,
    workspace_entry_mutation_capabilities,
    workspace_language,
    workspace_mime_type,
    workspace_rich_mode_eligible,
    write_workspace_node_file_at_revision,
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
    mutation_capabilities = workspace_entry_mutation_capabilities(node)
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
            **mutation_capabilities,
        },
        "limits": {
            "text_file_max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES,
            "raw_file_max_bytes": None,
        },
    }


def _snapshot_or_error(
    node: PearWorkspaceNode,
    *,
    force: bool = False,
) -> WorkspaceSnapshot | HttpResponse:
    try:
        return get_workspace_snapshot(node, force=force)
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
    if entry.identity is None:
        return _workspace_error(
            code="unsafe_entry",
            message="Workspace entry has no safe filesystem identity.",
            status=415,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry)},
        )
    return entry


def _resolve_mutable_entry(
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
    if not entry.path or entry.kind not in {"file", "directory"}:
        return _workspace_error(
            code="invalid_entry",
            message="The workspace root cannot be mutated.",
            status=400,
            revision=snapshot.revision,
        )
    if entry.identity is None:
        return _workspace_error(
            code="unsafe_entry",
            message="Workspace entry has no safe filesystem identity.",
            status=415,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry)},
        )
    return entry


def _validate_destination_against_snapshot(
    node: PearWorkspaceNode,
    snapshot: WorkspaceSnapshot,
    destination_path: str,
) -> str | HttpResponse:
    try:
        normalized = validate_workspace_destination(node, destination_path)
    except (InvalidWorkspacePathError, WorkspaceDestinationPolicyError) as error:
        code = error.code if isinstance(error, WorkspaceDestinationPolicyError) else "invalid_path"
        return _workspace_error(
            code=code,
            message=str(error),
            status=400,
            revision=snapshot.revision,
        )
    try:
        existing_entry = snapshot.index.entry_for_path(normalized)
    except WorkspaceEntryIdError:
        pass
    else:
        if existing_entry.status != "deleted":
            return _workspace_error(
                code="already_exists",
                message="Workspace destination already exists.",
                status=409,
                revision=snapshot.revision,
            )
    parent_path = normalized.rpartition("/")[0]
    if parent_path:
        try:
            parent = snapshot.index.entry_for_path(parent_path)
        except WorkspaceEntryIdError:
            return _workspace_error(
                code="destination_parent_missing",
                message="Workspace destination parent does not exist.",
                status=409,
                revision=snapshot.revision,
            )
        if parent.kind != "directory":
            return _workspace_error(
                code="destination_parent_missing",
                message="Workspace destination parent is not a directory.",
                status=409,
                revision=snapshot.revision,
            )
        if parent.identity is None:
            return _workspace_error(
                code="unsafe_entry",
                message="Workspace destination parent has no safe filesystem identity.",
                status=415,
                revision=snapshot.revision,
            )
    return normalized


def _locked_mutation_error_response(
    node: PearWorkspaceNode,
    error: WorkspaceMutationReadOnlyError | WorkspaceMutationStaleError,
) -> HttpResponse:
    if isinstance(error, WorkspaceMutationReadOnlyError):
        return _workspace_error(
            code="workspace_read_only",
            message="Workspace is read-only.",
            status=403,
            revision=error.revision,
        )
    node.refresh_from_db()
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    return _stale_workspace_response(snapshot)


def _entry_mutation_payload(
    snapshot: WorkspaceSnapshot,
    *,
    operation: Literal["create", "rename", "delete"],
    previous_entry_id: str | None,
    entry: WorkspaceIndexEntry | None,
    path: str,
    removed_entries: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation": operation,
        "workspace_uuid": snapshot.workspace_uuid,
        "workspace_revision": snapshot.revision,
        "previous_entry_id": previous_entry_id,
        "entry": _entry_payload(entry) if entry is not None else None,
        "path": path,
    }
    if removed_entries is not None:
        payload["removed_entries"] = removed_entries
    return payload


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
            expected_identity=entry.identity,
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


def _check_and_record_file_observation(
    node: PearWorkspaceNode,
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
    file: WorkspaceFile,
) -> HttpResponse | None:
    observed_version = get_workspace_file_observation(
        snapshot.workspace_uuid,
        snapshot.revision,
        entry.entry_id,
    )
    if observed_version is not None and observed_version != file.version:
        try:
            current_snapshot = refresh_workspace_snapshot_after_conflict(
                node,
                observed_revision=snapshot.revision,
            )
        except (WorkspaceSnapshotError, WorkspaceIOError):
            return _workspace_error(
                code="workspace_unavailable",
                message="Could not refresh the replaced workspace file.",
                status=503,
                revision=int(node.content_revision),
            )
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=current_snapshot.revision,
        )
    record_workspace_file_observation(
        snapshot.workspace_uuid,
        snapshot.revision,
        entry.entry_id,
        file.version,
    )
    return None


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
    file, payload = result
    observation_error = _check_and_record_file_observation(node, snapshot, entry, file)
    if observation_error is not None:
        return observation_error
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
        receipt = get_workspace_mutation_receipt(
            actor_id=user_profile.id,
            workspace_uuid=snapshot.workspace_uuid,
            requested_revision=workspace_revision,
            entry_id=entry_id,
            expected_path=normalized_path,
            base_version=base_version,
            content=content_bytes,
            edit_revision=edit_revision,
        )
        if receipt is None:
            return _stale_workspace_response(snapshot)
        try:
            if receipt.state == "complete":
                if receipt.result_revision != snapshot.revision or receipt.result_entry_id is None:
                    return _stale_workspace_response(snapshot)
                current_entry = snapshot.index.entry_for_id(receipt.result_entry_id)
            elif receipt.state == "pending" and snapshot.revision == workspace_revision + 1:
                current_entry = snapshot.index.entry_for_path(normalized_path)
            else:
                return _stale_workspace_response(snapshot)
            if current_entry.kind != "file" or current_entry.path != normalized_path:
                return _stale_workspace_response(snapshot)
            if current_entry.identity != receipt.result_identity:
                return _stale_workspace_response(snapshot)
            current_file = read_workspace_node_file(
                node,
                current_entry.path,
                max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
                expected_identity=receipt.result_identity,
            )
        except (
            WorkspaceEntryIdError,
            WorkspaceIOError,
            WorkspaceFileTooLargeError,
        ):
            return _stale_workspace_response(snapshot)
        if current_file.data != content_bytes or current_file.version != receipt.result_version:
            return _stale_workspace_response(snapshot)
        payload = _text_file_payload(
            snapshot,
            current_entry,
            current_file,
            edit_revision=edit_revision,
        )
        if not payload["editable"]:
            return _unsupported_file_response(snapshot, current_entry, file=current_file)
        observation_error = _check_and_record_file_observation(
            node,
            snapshot,
            current_entry,
            current_file,
        )
        if observation_error is not None:
            return observation_error
        if receipt.state == "pending":
            record_completed_workspace_mutation(
                actor_id=user_profile.id,
                workspace_uuid=snapshot.workspace_uuid,
                requested_revision=workspace_revision,
                entry_id=entry_id,
                expected_path=normalized_path,
                base_version=base_version,
                content=content_bytes,
                edit_revision=edit_revision,
                result_version=current_file.version,
                result_identity=receipt.result_identity,
                result_revision=snapshot.revision,
                result_entry_id=current_entry.entry_id,
            )
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

    pending_repair_receipt = get_workspace_mutation_receipt(
        actor_id=user_profile.id,
        workspace_uuid=snapshot.workspace_uuid,
        requested_revision=workspace_revision,
        entry_id=entry_id,
        expected_path=normalized_path,
        base_version=base_version,
        content=content_bytes,
        edit_revision=edit_revision,
    )
    write_identity = entry.identity
    if pending_repair_receipt is not None and pending_repair_receipt.state == "pending":
        write_identity = pending_repair_receipt.result_identity

    def before_finish(result: WorkspaceWriteResult) -> None:
        if result.file.identity is None:
            raise WorkspaceEntryChangedError("Saved workspace file identity is unavailable.")
        if result.changed:
            record_pending_workspace_mutation(
                actor_id=user_profile.id,
                workspace_uuid=snapshot.workspace_uuid,
                requested_revision=workspace_revision,
                entry_id=entry_id,
                expected_path=normalized_path,
                base_version=base_version,
                content=content_bytes,
                edit_revision=edit_revision,
                result_version=result.file.version,
                result_identity=result.file.identity,
            )
            return
        if result.file.version == base_version:
            return
        pending_receipt = get_workspace_mutation_receipt(
            actor_id=user_profile.id,
            workspace_uuid=snapshot.workspace_uuid,
            requested_revision=workspace_revision,
            entry_id=entry_id,
            expected_path=normalized_path,
            base_version=base_version,
            content=content_bytes,
            edit_revision=edit_revision,
        )
        if (
            pending_receipt is None
            or pending_receipt.state != "pending"
            or pending_receipt.result_version != result.file.version
            or pending_receipt.result_identity != result.file.identity
        ):
            raise WorkspaceEntryChangedError(
                "Workspace entry changed before the save revision was recorded."
            )

    try:
        result, saved_snapshot = write_workspace_node_file_at_revision(
            node,
            entry.path,
            content_bytes,
            expected_version=base_version,
            expected_identity=write_identity,
            requested_revision=workspace_revision,
            before_finish=before_finish,
        )
        saved_entry = saved_snapshot.index.entry_for_path(entry.path)
    except WorkspaceMutationReadOnlyError as error:
        return _workspace_error(
            code="workspace_read_only",
            message="Workspace is read-only.",
            status=403,
            revision=error.revision,
        )
    except WorkspaceMutationStaleError:
        node.refresh_from_db()
        latest_snapshot = _snapshot_or_error(node)
        if isinstance(latest_snapshot, HttpResponse):
            return latest_snapshot
        return _stale_workspace_response(latest_snapshot)
    except WorkspaceVersionConflictError:
        try:
            conflict_snapshot = refresh_workspace_snapshot_after_conflict(
                node,
                observed_revision=snapshot.revision,
            )
            conflict_entry = conflict_snapshot.index.entry_for_path(entry.path)
            if conflict_entry.kind != "file":
                raise WorkspaceEntryIdError("Workspace entry is no longer a file.")
            remote_file = read_workspace_node_file(
                node,
                conflict_entry.path,
                max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
                expected_identity=conflict_entry.identity,
            )
        except (WorkspaceEntryIdError, WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
            return _workspace_error(
                code="stale_entry",
                message="Workspace entry changed; refresh before retrying.",
                status=409,
                revision=int(node.content_revision),
            )
        except WorkspaceFileTooLargeError:
            return _workspace_error(
                code="file_too_large",
                message="Workspace file exceeds the editable size limit.",
                status=413,
                revision=int(node.content_revision),
                data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
            )
        except (WorkspaceSnapshotError, WorkspaceIOError):
            return _workspace_error(
                code="workspace_unavailable",
                message="Could not refresh the conflicting workspace file.",
                status=503,
                revision=int(node.content_revision),
            )
        data: dict[str, object] = {}
        remote_payload = _text_file_payload(conflict_snapshot, conflict_entry, remote_file)
        record_workspace_file_observation(
            conflict_snapshot.workspace_uuid,
            conflict_snapshot.revision,
            conflict_entry.entry_id,
            remote_file.version,
        )
        if remote_payload["editable"]:
            data["remote_file"] = remote_payload
        return _workspace_error(
            code="version_conflict",
            message="Workspace file changed; reload before saving.",
            status=409,
            revision=conflict_snapshot.revision,
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
    except (WorkspaceIOError, WorkspaceSnapshotError, WorkspaceEntryIdError, ValueError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not save and refresh this workspace file.",
            status=503,
            revision=int(node.content_revision),
        )
    assert result.file.identity is not None
    payload = _text_file_payload(
        saved_snapshot,
        saved_entry,
        result.file,
        edit_revision=edit_revision,
    )
    record_workspace_file_observation(
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        saved_entry.entry_id,
        result.file.version,
    )
    record_completed_workspace_mutation(
        actor_id=user_profile.id,
        workspace_uuid=snapshot.workspace_uuid,
        requested_revision=workspace_revision,
        entry_id=entry_id,
        expected_path=normalized_path,
        base_version=base_version,
        content=content_bytes,
        edit_revision=edit_revision,
        result_version=result.file.version,
        result_identity=result.file.identity,
        result_revision=saved_snapshot.revision,
        result_entry_id=saved_entry.entry_id,
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
def post_pear_workspace_entry_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    destination_path: Annotated[
        str,
        ApiParamConfig("destination_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    kind: Annotated[
        Literal["file", "directory"],
        ApiParamConfig("kind", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    workspace_revision: Annotated[
        int,
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    content: Annotated[
        str,
        ApiParamConfig("content", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = "",
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if workspace_revision != snapshot.revision:
        return _stale_workspace_response(snapshot)
    destination = _validate_destination_against_snapshot(node, snapshot, destination_path)
    if isinstance(destination, HttpResponse):
        return destination
    if kind == "directory" and content:
        raise JsonableError(_("A directory cannot have file content."))
    try:
        content_bytes = content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise JsonableError(_("Workspace file content must be valid UTF-8."))
    if len(content_bytes) > PEAR_WORKSPACE_TEXT_MAX_BYTES:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )

    try:
        with lock_workspace_mutation(node, requested_revision=workspace_revision) as locked:
            locked_destination = _validate_destination_against_snapshot(
                locked,
                snapshot,
                destination,
            )
            if isinstance(locked_destination, HttpResponse):
                return locked_destination
            if kind == "file":
                result = create_workspace_node_file(locked, locked_destination, content_bytes)
                if not result.created:
                    raise WorkspaceEntryExistsError("Workspace destination already exists.")
            else:
                create_workspace_node_directory(locked, locked_destination)
            saved_snapshot = finish_workspace_entry_mutation(locked)
            saved_entry = saved_snapshot.index.entry_for_path(locked_destination)
    except (WorkspaceMutationReadOnlyError, WorkspaceMutationStaleError) as error:
        return _locked_mutation_error_response(node, error)
    except WorkspaceEntryExistsError:
        return _workspace_error(
            code="already_exists",
            message="Workspace destination already exists.",
            status=409,
            revision=int(node.content_revision),
        )
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=int(node.content_revision),
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace destination changed; refresh before retrying.",
            status=409,
            revision=int(node.content_revision),
        )
    except (WorkspaceSnapshotError, WorkspaceIOError, WorkspaceEntryIdError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not create and index the workspace entry.",
            status=503,
            revision=int(node.content_revision),
        )
    logger.info(
        "Pear workspace entry created workspace=%s revision=%s entry=%s actor=%s kind=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        saved_entry.entry_id,
        user_profile.id,
        kind,
    )
    return json_success(
        request,
        data={
            "pear_workspace_mutation": _entry_mutation_payload(
                saved_snapshot,
                operation="create",
                previous_entry_id=None,
                entry=saved_entry,
                path=saved_entry.path,
            )
        },
    )


@typed_endpoint
def patch_pear_workspace_entry_backend(
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
    destination_path: Annotated[
        str,
        ApiParamConfig("destination_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    workspace_revision: Annotated[
        int,
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if workspace_revision != snapshot.revision:
        return _stale_workspace_response(snapshot)
    entry = _resolve_mutable_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    try:
        normalized_expected_path = "/".join(validate_workspace_relative_path(expected_path))
    except InvalidWorkspacePathError:
        normalized_expected_path = ""
    if entry.path != normalized_expected_path:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry path is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    destination = _validate_destination_against_snapshot(node, snapshot, destination_path)
    if isinstance(destination, HttpResponse):
        return destination
    entry_kind: Literal["file", "directory"] = "file" if entry.kind == "file" else "directory"
    try:
        with lock_workspace_mutation(node, requested_revision=workspace_revision) as locked:
            locked_destination = _validate_destination_against_snapshot(
                locked,
                snapshot,
                destination,
            )
            if isinstance(locked_destination, HttpResponse):
                return locked_destination
            rename_workspace_node_entry(
                locked,
                entry.path,
                locked_destination,
                expected_kind=entry_kind,
                expected_identity=entry.identity,
            )
            saved_snapshot = finish_workspace_entry_mutation(locked)
            saved_entry = saved_snapshot.index.entry_for_path(locked_destination)
    except (WorkspaceMutationReadOnlyError, WorkspaceMutationStaleError) as error:
        return _locked_mutation_error_response(node, error)
    except WorkspaceEntryExistsError:
        return _workspace_error(
            code="already_exists",
            message="Workspace destination already exists.",
            status=409,
            revision=int(node.content_revision),
        )
    except (InvalidWorkspacePathError, WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=int(node.content_revision),
        )
    except WorkspaceOperationUnsupportedError:
        return _workspace_error(
            code="unsupported_operation",
            message="No-overwrite rename is unavailable for this workspace.",
            status=501,
            revision=int(node.content_revision),
        )
    except (WorkspaceSnapshotError, WorkspaceIOError, WorkspaceEntryIdError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not rename and index the workspace entry.",
            status=503,
            revision=int(node.content_revision),
        )
    logger.info(
        "Pear workspace entry renamed workspace=%s revision=%s old_entry=%s new_entry=%s actor=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        entry.entry_id,
        saved_entry.entry_id,
        user_profile.id,
    )
    return json_success(
        request,
        data={
            "pear_workspace_mutation": _entry_mutation_payload(
                saved_snapshot,
                operation="rename",
                previous_entry_id=entry.entry_id,
                entry=saved_entry,
                path=saved_entry.path,
            )
        },
    )


@typed_endpoint
def delete_pear_workspace_entry_backend(
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
    workspace_revision: Annotated[
        int,
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    recursive: Annotated[
        Json[bool],
        ApiParamConfig("recursive", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
    confirm_irreversible: Annotated[
        Json[bool],
        ApiParamConfig("confirm_irreversible", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if workspace_revision != snapshot.revision:
        return _stale_workspace_response(snapshot)
    entry = _resolve_mutable_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    try:
        normalized_expected_path = "/".join(validate_workspace_relative_path(expected_path))
    except InvalidWorkspacePathError:
        normalized_expected_path = ""
    if entry.path != normalized_expected_path:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry path is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    if entry.kind == "file" and entry.status == "untracked" and not confirm_irreversible:
        return _workspace_error(
            code="confirmation_required",
            message="Deleting an untracked file is irreversible and requires confirmation.",
            status=409,
            revision=snapshot.revision,
            data={"confirmation": "untracked_file"},
        )
    if recursive and not confirm_irreversible:
        return _workspace_error(
            code="confirmation_required",
            message="Recursive deletion is irreversible and requires confirmation.",
            status=409,
            revision=snapshot.revision,
            data={"confirmation": "recursive_delete"},
        )
    entry_kind: Literal["file", "directory"] = "file" if entry.kind == "file" else "directory"
    try:
        with lock_workspace_mutation(node, requested_revision=workspace_revision) as locked:
            result = delete_workspace_node_entry(
                locked,
                entry.path,
                expected_kind=entry_kind,
                expected_identity=entry.identity,
                recursive=bool(recursive),
            )
            saved_snapshot = finish_workspace_entry_mutation(locked)
            try:
                remaining_entry = saved_snapshot.index.entry_for_path(entry.path)
            except WorkspaceEntryIdError:
                remaining_entry = None
            else:
                if remaining_entry.status != "deleted":
                    raise WorkspaceEntryChangedError(
                        "Deleted workspace path remained live in the refreshed index."
                    )
    except (WorkspaceMutationReadOnlyError, WorkspaceMutationStaleError) as error:
        return _locked_mutation_error_response(node, error)
    except WorkspaceDirectoryNotEmptyError:
        return _workspace_error(
            code="directory_not_empty",
            message="Workspace directory is not empty; recursive deletion must be explicit.",
            status=409,
            revision=int(node.content_revision),
            data={"requires_recursive": True, "requires_confirmation": True},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=int(node.content_revision),
        )
    except (WorkspaceSnapshotError, WorkspaceIOError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not delete and refresh the workspace entry.",
            status=503,
            revision=int(node.content_revision),
        )
    logger.info(
        "Pear workspace entry deleted workspace=%s revision=%s old_entry=%s actor=%s kind=%s recursive=%s removed=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        entry.entry_id,
        user_profile.id,
        entry_kind,
        bool(recursive),
        result.removed_entries,
    )
    return json_success(
        request,
        data={
            "pear_workspace_mutation": _entry_mutation_payload(
                saved_snapshot,
                operation="delete",
                previous_entry_id=entry.entry_id,
                entry=remaining_entry,
                path=entry.path,
                removed_entries=result.removed_entries,
            )
        },
    )


@typed_endpoint
def get_pear_workspace_history_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    cursor: Annotated[
        str | None,
        ApiParamConfig("cursor", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    limit: Annotated[
        int,
        ApiParamConfig("limit", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = 50,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    try:
        page = get_workspace_history(
            node,
            snapshot,
            entry_id=entry.entry_id,
            path=entry.path,
            cursor=cursor,
            limit=limit,
        )
    except WorkspaceHistoryCursorError:
        return _workspace_error(
            code="stale_cursor",
            message="Workspace history cursor is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    except ValueError as error:
        raise JsonableError(str(error))
    except WorkspaceSnapshotError:
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not load workspace history.",
            status=503,
            revision=snapshot.revision,
        )
    return json_success(
        request,
        data={
            "pear_workspace_history": {
                "workspace_uuid": snapshot.workspace_uuid,
                "workspace_revision": snapshot.revision,
                "entry": _entry_payload(entry),
                "commits": [
                    {
                        "commit": item.commit,
                        "authored_at": item.authored_at,
                        "author_name": item.author_name,
                        "subject": item.subject,
                    }
                    for item in page.entries
                ],
                "next_cursor": page.next_cursor,
            }
        },
    )


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
        stream = open_workspace_node_file_stream(
            node,
            entry.path,
            expected_identity=entry.identity,
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
    response = StreamingHttpResponse(
        streaming_content=stream,
        content_type=workspace_mime_type(entry.path),
    )
    response["Content-Disposition"] = content_disposition_header(bool(download), entry.name) or (
        "attachment" if download else "inline"
    )
    response["Content-Length"] = str(stream.metadata.size)
    response["ETag"] = f'"{stream.metadata.cache_token}"'
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
