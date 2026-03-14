from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from refactor.online_auth import (  # noqa: E402
    check_token_access,
    download_lakehouse_file,
    get_drive,
    get_current_user,
    list_fabric_lakehouses,
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
        value = item.get(display_key) or item
        print(f"  {index}. {value}")
    raw = prompt_text(f"Select {label} number", str(default_index + 1), allow_empty=False)
    choice_index = int(raw) - 1
    if choice_index < 0 or choice_index >= len(items):
        raise RuntimeError(f"Invalid {label} selection '{raw}'.")
    return items[choice_index]


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
    files = [str(item).strip() for item in payload.get("files", []) if str(item).strip()]
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

    return SharePointSourceRef(
        source_type=source_type,
        target_name=target_name,
        root_path=root_path,
        tracked_files=[TrackedFileRef(path=path) for path in files],
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
    )


def build_run_api(config: dict[str, Any]) -> ValidationRunApi:
    _, read_text, write_text = build_storage_context(config)
    runs_events_path = str(config.get("runs_events_path", "Files/validation-service/events/run_events.jsonl"))
    runs_projection_path = str(config.get("runs_projection_path", "Files/validation-service/projections/runs_current.json"))
    runs_idempotency_path = str(config.get("runs_idempotency_path", "Files/validation-service/system/run_idempotency.json"))

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
    )
