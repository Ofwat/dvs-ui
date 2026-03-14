from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID
import hashlib

ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from refactor.online_auth import (  # noqa: E402
    check_token_access,
    download_lakehouse_file,
    download_sharepoint_item,
    get_fabric_pipeline_run,
    get_drive,
    get_current_user,
    list_fabric_lakehouses,
    list_fabric_pipeline_runs,
    list_fabric_pipelines,
    list_sharepoint_drive_items,
    list_fabric_workspaces,
    resolve_sharepoint_share_url,
    write_lakehouse_file,
)
from refactor.services.data_validation_api import (  # noqa: E402
    OrganisationSubmissionRef,
    SharePointSourceRef,
    TemplateSubmissionRef,
    TextBackedIdempotencyStore,
    TextBackedProjectionStore,
    TextBackedRunEventStore,
    TextBackedSubmissionEventStore,
    TrackedFileRef,
    ValidationRunApi,
    ValidationServiceApi,
)


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require_ok(action: str, success: bool, payload: Any):
    if not success:
        raise RuntimeError(f"{action} failed: {payload}")


def ensure_authenticated():
    auth_ok, auth_payload = check_token_access(force=True)
    require_ok("check_token_access", auth_ok, auth_payload)


def find_by_display_name(items: list[dict[str, Any]], display_name: str, label: str) -> dict[str, Any]:
    matches = [item for item in items if item.get("displayName") == display_name]
    if not matches:
        raise RuntimeError(f"{label} with displayName '{display_name}' was not found.")
    if len(matches) > 1:
        raise RuntimeError(f"{label} with displayName '{display_name}' is not unique.")
    return matches[0]


def parse_optional_text(payload: bytes | str) -> str | None:
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    if "PathNotFound" in payload or "BlobNotFound" in payload:
        return None
    if '"code":"PathNotFound"' in payload or '"code":"BlobNotFound"' in payload:
        return None
    raise RuntimeError(payload)


def is_guid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_actor(explicit_actor: str | None) -> str:
    actor = str(explicit_actor or "").strip()
    if actor:
        return actor
    success, payload = get_current_user()
    require_ok("get_current_user", success, payload)
    return str(
        payload.get("mail")
        or payload.get("userPrincipalName")
        or payload.get("displayName")
        or payload.get("id")
    )


def prompt_text(label: str, default: str | None = None, *, allow_empty: bool = True) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return str(default)
        if allow_empty:
            return ""
        print("A value is required.")


def prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter 'y' or 'n'.")


def prompt_csv_selection(label: str, available: list[str], default: list[str] | None = None) -> list[str]:
    if not available:
        print(f"{label} available: none")
        return []
    default_values = default if default is not None else available
    default_text = ",".join(default_values)
    print(f"{label} available: {', '.join(available)}")
    raw = prompt_text(
        f"{label} to use (comma-separated, blank for default)",
        default_text,
        allow_empty=True,
    )
    selected = [item.strip().upper() for item in raw.split(",") if item.strip()]
    if not selected:
        return list(default_values)
    unknown = [item for item in selected if item not in available]
    if unknown:
        raise RuntimeError(f"Unknown {label.lower()} value(s): {', '.join(unknown)}")
    return selected


def prompt_choice_index(label: str, items: list[dict[str, Any]], *, display_key: str, default_index: int = 0) -> dict[str, Any]:
    if not items:
        raise RuntimeError(f"No items available for {label}.")
    print(f"\nAvailable {label}:")
    for index, item in enumerate(items, start=1):
        if "_display" in item:
            value = item["_display"]
        else:
            value = item.get(display_key) or item
        print(f"  {index}. {value}")
    raw = prompt_text(f"Select {label} number", str(default_index + 1), allow_empty=False)
    choice_index = int(raw) - 1
    if choice_index < 0 or choice_index >= len(items):
        raise RuntimeError(f"Invalid {label} selection '{raw}'.")
    return items[choice_index]


def prompt_service_config(*, include_pipeline: bool = False) -> dict[str, Any]:
    service = {
        "storage_mode": "fabric",
        "workspace_display_name": prompt_text("Service workspace display name", allow_empty=False),
        "lakehouse_display_name": prompt_text("Service lakehouse display name", allow_empty=False),
        "submissions_events_path": prompt_text(
            "Submissions events path",
            "Files/validation-service/events/submission_events.jsonl",
            allow_empty=False,
        ),
        "submissions_projection_path": prompt_text(
            "Submissions projection path",
            "Files/validation-service/projections/submissions_current.json",
            allow_empty=False,
        ),
        "idempotency_path": prompt_text(
            "Submission idempotency path",
            "Files/validation-service/system/idempotency.json",
            allow_empty=False,
        ),
        "runs_events_path": prompt_text(
            "Runs events path",
            "Files/validation-service/events/run_events.jsonl",
            allow_empty=False,
        ),
        "runs_projection_path": prompt_text(
            "Runs projection path",
            "Files/validation-service/projections/runs_current.json",
            allow_empty=False,
        ),
        "runs_idempotency_path": prompt_text(
            "Run idempotency path",
            "Files/validation-service/system/run_idempotency.json",
            allow_empty=False,
        ),
    }
    pipeline: dict[str, Any] = {}
    if include_pipeline:
        pipeline = {
            "workspace_display_name": prompt_text("Pipeline workspace display name", allow_empty=False),
            "pipeline_display_name": prompt_text("Pipeline display name", allow_empty=False),
        }
    return {"service": service, "pipeline": pipeline, "scenario": {"submission": {}}}


def _list_sharepoint_descendants(
    drive_id: str,
    parent_item_id: str,
    *,
    parent_relative_path: str = "",
) -> list[dict[str, Any]]:
    items_ok, items_payload = list_sharepoint_drive_items(drive_id, parent_item_id)
    require_ok("list_sharepoint_drive_items", items_ok, items_payload)

    descendants: list[dict[str, Any]] = []
    for item in items_payload:
        item_name = str(item.get("name", "")).strip("/")
        relative_path = "/".join(part for part in [parent_relative_path, item_name] if part)
        enriched = {**item, "relativePath": relative_path}
        descendants.append(enriched)
        if item.get("isFolder") and item.get("id"):
            descendants.extend(
                _list_sharepoint_descendants(
                    drive_id,
                    str(item["id"]),
                    parent_relative_path=relative_path,
                )
            )
    return descendants


def resolve_sharepoint_folder_listing(share_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    success, payload = resolve_sharepoint_share_url(share_url)
    require_ok("resolve_sharepoint_share_url", success, payload)

    parent_reference = payload.get("parentReference", {})
    drive_id = str(parent_reference.get("driveId") or "")
    if not drive_id:
        raise RuntimeError("Resolved SharePoint link did not include driveId.")
    linked_item_id = str(payload.get("id") or "")
    linked_item_name = str(payload.get("name") or "").strip("/")
    linked_item_is_folder = bool(payload.get("folder"))
    if not linked_item_is_folder:
        raise RuntimeError("SharePoint link must point to a folder for scanning by company acronym.")

    drive_ok, drive_payload = get_drive(drive_id)
    require_ok("get_drive", drive_ok, drive_payload)
    items_payload = _list_sharepoint_descendants(drive_id, linked_item_id)

    root_path = str(parent_reference.get("path", "")).split("root:/", 1)[-1].strip("/")
    combined_root = "/".join(part for part in [root_path, linked_item_name] if part)
    context = {
        "folder_url": share_url,
        "drive_id": drive_id,
        "linked_item_id": linked_item_id,
        "linked_item_name": linked_item_name,
        "linked_item_is_folder": True,
        "source_type": "drive",
        "target_name": str(drive_payload.get("name") or drive_id),
        "root_path": combined_root,
    }
    return context, list(items_payload)


def extract_item_modified_by(item_payload: dict[str, Any]) -> str | None:
    last_modified_by = item_payload.get("lastModifiedBy", {})
    if isinstance(last_modified_by, dict):
        user = last_modified_by.get("user", {})
        if isinstance(user, dict) and user.get("displayName"):
            return str(user["displayName"])
        application = last_modified_by.get("application", {})
        if isinstance(application, dict) and application.get("displayName"):
            return str(application["displayName"])
    if item_payload.get("lastModifiedBy"):
        return str(item_payload["lastModifiedBy"])
    return None


def _build_tracked_file_from_item(drive_id: str, item_id: str, relative_path: str, item_payload: dict[str, Any] | None = None) -> TrackedFileRef:
    file_ok, file_payload = download_sharepoint_item(drive_id, item_id)
    require_ok("download_sharepoint_item", file_ok, file_payload)
    metadata = item_payload or {}
    return TrackedFileRef(
        path=relative_path,
        watch=False,
        watch_search_term=None,
        current_hash=hash_bytes(file_payload),
        validated_hash=None,
        size_bytes=len(file_payload),
        created=str(metadata.get("createdDateTime")).strip() if metadata.get("createdDateTime") else None,
        modified=str(metadata.get("lastModifiedDateTime")).strip() if metadata.get("lastModifiedDateTime") else None,
        modified_by=extract_item_modified_by(metadata),
    )


def refresh_source_hashes(source_payload: dict[str, Any]) -> dict[str, Any]:
    source = SharePointSourceRef.from_dict(source_payload)
    if not source.folder_url:
        raise RuntimeError("Cannot refresh SharePoint hashes without folder_url in the stored source payload.")

    context, items = resolve_sharepoint_folder_listing(source.folder_url)
    item_by_path = {
        str(item.get("relativePath") or item.get("name") or "").strip("/"): item
        for item in items
    }
    validated_by_key = {
        (item.path or "", item.watch_search_term or ""): item.validated_hash
        for item in source.tracked_files
    }

    tracked_files: list[TrackedFileRef] = []
    for tracked in source.tracked_files:
        if tracked.path:
            matched_item = item_by_path.get(tracked.path.strip("/"))
            if matched_item and not matched_item.get("isFolder"):
                resolved = _build_tracked_file_from_item(
                    str(context["drive_id"]),
                    str(matched_item["id"]),
                    str(matched_item.get("relativePath") or matched_item.get("name") or tracked.path),
                    matched_item,
                )
                tracked_files.append(
                    TrackedFileRef(
                        path=resolved.path,
                        watch=False,
                        watch_search_term=tracked.watch_search_term,
                        current_hash=resolved.current_hash,
                        validated_hash=validated_by_key.get((tracked.path or "", tracked.watch_search_term or "")),
                        size_bytes=resolved.size_bytes,
                        created=resolved.created,
                        modified=resolved.modified,
                        modified_by=resolved.modified_by,
                    )
                )
            else:
                tracked_files.append(
                    TrackedFileRef(
                        path=tracked.path,
                        watch=tracked.watch,
                        watch_search_term=tracked.watch_search_term,
                        current_hash=None,
                        validated_hash=validated_by_key.get((tracked.path or "", tracked.watch_search_term or "")),
                        size_bytes=None,
                        created=None,
                        modified=None,
                        modified_by=None,
                    )
                )
            continue

        if tracked.watch:
            search_term = tracked.watch_search_term or ""
            matched_item = find_sharepoint_match_by_search_term(items, search_term)
            if matched_item and not matched_item.get("isFolder"):
                resolved = _build_tracked_file_from_item(
                    str(context["drive_id"]),
                    str(matched_item["id"]),
                    str(matched_item.get("relativePath") or matched_item.get("name") or search_term),
                    matched_item,
                )
                tracked_files.append(
                    TrackedFileRef(
                        path=resolved.path,
                        watch=False,
                        watch_search_term=search_term,
                        current_hash=resolved.current_hash,
                        validated_hash=validated_by_key.get(("", search_term)),
                        size_bytes=resolved.size_bytes,
                        created=resolved.created,
                        modified=resolved.modified,
                        modified_by=resolved.modified_by,
                    )
                )
            else:
                tracked_files.append(
                    TrackedFileRef(
                        path=None,
                        watch=True,
                        watch_search_term=search_term,
                        current_hash=None,
                        validated_hash=validated_by_key.get(("", search_term)),
                        size_bytes=None,
                        created=None,
                        modified=None,
                        modified_by=None,
                    )
                )

    return SharePointSourceRef(
        source_type=source.source_type,
        target_name=source.target_name,
        root_path=source.root_path,
        tracked_files=tracked_files,
        folder_url=source.folder_url,
        drive_id=source.drive_id,
        linked_item_id=source.linked_item_id,
        linked_item_name=source.linked_item_name,
        linked_item_is_folder=source.linked_item_is_folder,
    ).to_dict()


def find_prefix_matches(items: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    normalized_prefix = prefix.strip().upper()
    matches = [
        item
        for item in items
        if str(item.get("name", "")).strip().upper().startswith(normalized_prefix)
    ]
    file_matches = [item for item in matches if not item.get("isFolder")]
    folder_matches = [item for item in matches if item.get("isFolder")]
    chosen = file_matches[0] if file_matches else (folder_matches[0] if folder_matches else None)
    return {
        "matches": matches,
        "file_matches": file_matches,
        "folder_matches": folder_matches,
        "chosen": chosen,
    }


def build_sharepoint_source(payload: dict[str, Any]) -> SharePointSourceRef:
    tracked_file_payloads = payload.get("tracked_files")
    files = [str(item).strip() for item in payload.get("files", []) if str(item).strip()]
    tracked_files = (
        [TrackedFileRef.from_dict(item) for item in tracked_file_payloads if isinstance(item, (dict, str))]
        if tracked_file_payloads is not None
        else [TrackedFileRef(path=path, watch=False, watch_search_term=None) for path in files]
    )
    folder_url = str(payload.get("folder_url")).strip() if payload.get("folder_url") else None
    source_type = str(payload.get("source_type", "")).strip()
    target_name = str(payload.get("target_name", "")).strip()
    root_path = str(payload.get("root_path", "")).strip()
    drive_id = str(payload.get("drive_id")).strip() if payload.get("drive_id") else None
    linked_item_id = str(payload.get("linked_item_id")).strip() if payload.get("linked_item_id") else None
    linked_item_name = str(payload.get("linked_item_name")).strip() if payload.get("linked_item_name") else None
    linked_item_is_folder = bool(payload["linked_item_is_folder"]) if payload.get("linked_item_is_folder") is not None else None

    if (not source_type or not target_name or not root_path or not drive_id or not linked_item_id or not linked_item_name or linked_item_is_folder is None) and folder_url:
        success, resolved = resolve_sharepoint_share_url(folder_url)
        require_ok("resolve_sharepoint_share_url", success, resolved)
        parent_reference = resolved.get("parentReference", {})
        resolved_drive_id = str(parent_reference.get("driveId") or "")
        if not resolved_drive_id:
            raise RuntimeError("Resolved SharePoint link did not include driveId.")
        drive_ok, drive_payload = get_drive(resolved_drive_id)
        require_ok("get_drive", drive_ok, drive_payload)
        source_type = source_type or "drive"
        target_name = target_name or str(drive_payload.get("name") or resolved_drive_id)
        root_path = root_path or str(parent_reference.get("path", "")).split("root:/", 1)[-1].strip("/")
        drive_id = drive_id or resolved_drive_id
        linked_item_id = linked_item_id or str(resolved.get("id") or "")
        linked_item_name = linked_item_name or str(resolved.get("name", "")).strip("/")
        if linked_item_is_folder is None:
            linked_item_is_folder = bool(resolved.get("folder"))

    if tracked_file_payloads is None and folder_url and drive_id and linked_item_id and linked_item_is_folder:
        descendants = _list_sharepoint_descendants(str(drive_id), str(linked_item_id))
        items_by_path = {
            str(item.get("relativePath") or item.get("name") or "").strip("/"): item
            for item in descendants
        }
        enriched_tracked_files: list[TrackedFileRef] = []
        for tracked in tracked_files:
            tracked_path = str(tracked.path or "").strip("/")
            matched_item = items_by_path.get(tracked_path)
            if matched_item and not matched_item.get("isFolder") and matched_item.get("id"):
                enriched_tracked_files.append(
                    _build_tracked_file_from_item(
                        str(drive_id),
                        str(matched_item["id"]),
                        str(matched_item.get("relativePath") or matched_item.get("name") or tracked_path),
                        matched_item,
                    )
                )
            else:
                enriched_tracked_files.append(tracked)
        tracked_files = enriched_tracked_files

    return SharePointSourceRef(
        source_type=source_type,
        target_name=target_name,
        root_path=root_path,
        tracked_files=tracked_files,
        folder_url=folder_url,
        drive_id=drive_id,
        linked_item_id=linked_item_id,
        linked_item_name=linked_item_name,
        linked_item_is_folder=linked_item_is_folder,
    )


def build_submission_refs(payload: dict[str, Any]) -> tuple[dict[str, OrganisationSubmissionRef], dict[str, TemplateSubmissionRef]]:
    organisations = {
        str(org_cd).strip().upper(): OrganisationSubmissionRef(
            sharepoint_source=build_sharepoint_source(org_payload),
            template_keys=[str(item).strip().upper() for item in org_payload.get("template_keys", []) if str(item).strip()],
        )
        for org_cd, org_payload in payload.get("organisations", {}).items()
    }
    templates = {
        str(template_key).strip().upper(): TemplateSubmissionRef(
            assignment_mode=str(template_payload.get("assignment_mode", "shared")).strip().lower(),
            applies_to=[str(item).strip().upper() for item in template_payload.get("applies_to", []) if str(item).strip()],
            sharepoint_source=build_sharepoint_source(template_payload),
        )
        for template_key, template_payload in payload.get("templates", {}).items()
    }
    return organisations, templates


def build_storage_context(config: dict[str, Any]) -> tuple[str, Any, Any]:
    workspace_name = str(config["workspace_display_name"]).strip()
    lakehouse_name = str(config["lakehouse_display_name"]).strip()

    workspaces_ok, workspaces_payload = list_fabric_workspaces()
    require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    workspace = find_by_display_name(workspaces_payload, workspace_name, "Workspace")
    workspace_id = str(workspace["id"])

    lakehouses_ok, lakehouses_payload = list_fabric_lakehouses(workspace_id)
    require_ok("list_fabric_lakehouses", lakehouses_ok, lakehouses_payload)
    lakehouse = find_by_display_name(lakehouses_payload, lakehouse_name, "Lakehouse")
    lakehouse_id = str(lakehouse["id"])

    def read_text(relative_path: str) -> str | None:
        success, payload = download_lakehouse_file(workspace_id, lakehouse_id, relative_path)
        if success:
            return payload.decode("utf-8")
        return parse_optional_text(payload)

    def write_text(relative_path: str, content: str):
        success, payload = write_lakehouse_file(
            workspace_id,
            lakehouse_id,
            relative_path,
            content.encode("utf-8"),
        )
        require_ok(f"write_lakehouse_file({relative_path})", success, payload)

    return workspace_id, read_text, write_text


def build_submission_service(config: dict[str, Any]) -> ValidationServiceApi:
    workspace_id, read_text, write_text = build_storage_context(config)

    return ValidationServiceApi(
        workspace_id=workspace_id,
        submissions_registry_path=str(config["submissions_events_path"]),
        event_store=TextBackedSubmissionEventStore(
            read_text=lambda: read_text(str(config["submissions_events_path"])),
            write_text=lambda content: write_text(str(config["submissions_events_path"]), content),
        ),
        idempotency_store=TextBackedIdempotencyStore(
            read_text=lambda: read_text(str(config["idempotency_path"])),
            write_text=lambda content: write_text(str(config["idempotency_path"]), content),
        ),
        submissions_projection_store=TextBackedProjectionStore(
            write_text=lambda content: write_text(str(config["submissions_projection_path"]), content),
        ),
        sharepoint_hash_resolver=refresh_source_hashes,
    )


def build_run_api(config: dict[str, Any]) -> ValidationRunApi:
    service_config = config.get("service", config)
    _, read_text, write_text = build_storage_context(service_config)
    runs_events_path = str(service_config.get("runs_events_path", "Files/validation-service/events/run_events.jsonl"))
    runs_projection_path = str(service_config.get("runs_projection_path", "Files/validation-service/projections/runs_current.json"))
    runs_idempotency_path = str(service_config.get("runs_idempotency_path", "Files/validation-service/system/run_idempotency.json"))

    pipeline_config = config.get("pipeline", {})
    pipeline_workspace_id = None
    pipeline_id = None
    pipeline_workspace_name = str(pipeline_config.get("workspace_display_name", "")).strip()
    pipeline_display_name = str(pipeline_config.get("pipeline_display_name", "")).strip()
    if pipeline_workspace_name and pipeline_display_name:
        workspaces_ok, workspaces_payload = list_fabric_workspaces()
        require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
        pipeline_workspace = find_by_display_name(workspaces_payload, pipeline_workspace_name, "Pipeline workspace")
        pipeline_workspace_id = str(pipeline_workspace["id"])
        pipelines_ok, pipelines_payload = list_fabric_pipelines(pipeline_workspace_id)
        require_ok("list_fabric_pipelines", pipelines_ok, pipelines_payload)
        pipeline = find_by_display_name(pipelines_payload, pipeline_display_name, "Pipeline")
        pipeline_id = str(pipeline["id"])

    def resolve_pipeline_run_id(fallback_run_id: str) -> str:
        if not pipeline_workspace_id or not pipeline_id:
            return ""
        runs_ok, runs_payload = list_fabric_pipeline_runs(pipeline_workspace_id, pipeline_id)
        require_ok("list_fabric_pipeline_runs", runs_ok, runs_payload)
        if not runs_payload:
            return ""
        latest = runs_payload[0]
        candidate = latest.get("id") or latest.get("jobInstanceId") or latest.get("jobId") or latest.get("runId")
        candidate_value = str(candidate).strip() if candidate is not None else ""
        return candidate_value if is_guid(candidate_value) else ""

    def pipeline_status_resolver(pipeline: Any, projection: dict[str, Any]):
        if not pipeline_workspace_id or not pipeline_id:
            return {"status": projection["run"]["state"]}
        pipeline_run_id = str(pipeline.pipeline_run_id or "").strip()
        if pipeline_run_id.lower() == "none":
            pipeline_run_id = ""
        if not is_guid(pipeline_run_id):
            pipeline_run_id = resolve_pipeline_run_id(str(projection["run"]["run_id"]))
            if not is_guid(pipeline_run_id):
                return {"status": "running"}
        success, response_payload = get_fabric_pipeline_run(
            pipeline_workspace_id,
            str(pipeline.pipeline_id or pipeline_id),
            pipeline_run_id,
        )
        if not success and "JobInstanceNotFound" in str(response_payload):
            resolved_run_id = resolve_pipeline_run_id(str(projection["run"]["run_id"]))
            if not is_guid(resolved_run_id):
                return {"status": "running"}
            success, response_payload = get_fabric_pipeline_run(
                pipeline_workspace_id,
                str(pipeline.pipeline_id or pipeline_id),
                resolved_run_id,
            )
        require_ok("get_fabric_pipeline_run", success, response_payload)
        return {
            "status": str(response_payload.get("status") or response_payload.get("state") or "running").lower(),
            "error": response_payload.get("failureReason"),
        }

    return ValidationRunApi(
        submission_resolver=lambda _submission_id: None,
        event_store=TextBackedRunEventStore(
            read_text=lambda: read_text(runs_events_path),
            write_text=lambda content: write_text(runs_events_path, content),
        ),
        idempotency_store=TextBackedIdempotencyStore(
            read_text=lambda: read_text(runs_idempotency_path),
            write_text=lambda content: write_text(runs_idempotency_path, content),
        ),
        runs_projection_store=TextBackedProjectionStore(
            write_text=lambda content: write_text(runs_projection_path, content),
        ),
        pipeline_status_resolver=pipeline_status_resolver,
    )
