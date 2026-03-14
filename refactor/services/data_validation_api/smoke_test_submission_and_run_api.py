from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any
from uuid import UUID, uuid4

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from refactor.online_auth import (  # noqa: E402
    download_lakehouse_file,
    download_sharepoint_item,
    get_current_user,
    get_drive,
    get_fabric_pipeline_run,
    get_group_drive,
    list_fabric_lakehouses,
    list_fabric_pipeline_runs,
    list_fabric_pipelines,
    list_running_fabric_pipeline_runs,
    list_fabric_workspaces,
    list_groups,
    list_sharepoint_drive_items,
    list_sharepoint_resources,
    resolve_sharepoint_share_url,
    trigger_fabric_pipeline,
    write_lakehouse_file,
)
from refactor.services.data_validation_api.submission_service import (  # noqa: E402
    JsonFileIdempotencyStore,
    JsonProjectionStore,
    JsonlSubmissionEventStore,
    OrganisationSubmissionRef,
    SharePointSourceRef,
    TemplateSubmissionRef,
    TextBackedIdempotencyStore,
    TextBackedProjectionStore,
    TextBackedSubmissionEventStore,
    TrackedFileRef,
    ValidationServiceApi,
)
from refactor.services.data_validation_api.validation_run_api import (  # noqa: E402
    CreateRunRequest,
    GetRunRequest,
    JsonlRunEventStore,
    ListRunsRequest,
    PlanRunRequest,
    RefreshRunStatusRequest,
    StageRunInputsRequest,
    StagedAsset,
    TextBackedRunEventStore,
    TriggerRunRequest,
    ValidationRunApi,
)


TERMINAL_PIPELINE_STATUSES = {
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
}


def pretty(label: str, payload: dict[str, Any]):
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2))


def stage(message: str):
    print(f"\n--- {message} ---")


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_config(config_path: Path, payload: dict[str, Any]):
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_optional_json_text(payload: bytes | str) -> str | None:
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    if "PathNotFound" in payload or "BlobNotFound" in payload:
        return None
    if '"code":"PathNotFound"' in payload or '"code":"BlobNotFound"' in payload:
        return None
    raise RuntimeError(payload)


def require_ok(action: str, success: bool, payload: Any):
    if not success:
        raise RuntimeError(f"{action} failed: {payload}")


def find_item_by_display_name(items: list[dict[str, Any]], display_name: str, label: str) -> dict[str, Any]:
    matches = [item for item in items if item.get("displayName") == display_name]
    if not matches:
        raise RuntimeError(f"{label} with displayName '{display_name}' was not found.")
    if len(matches) > 1:
        raise RuntimeError(f"{label} with displayName '{display_name}' is not unique.")
    return matches[0]


def ensure_idempotency_key(payload: dict[str, Any], default_prefix: str, fallback: str | None = None) -> str:
    existing = str(payload.get("idempotency_key", "")).strip()
    if existing:
        return existing
    if fallback:
        payload["idempotency_key"] = fallback
        return fallback
    generated = f"{default_prefix}-{uuid4().hex}"
    payload["idempotency_key"] = generated
    return generated


def build_random_smoke_note(prefix: str = "Smoke test update") -> str:
    return f"{prefix} {uuid4().hex[:8]}"


def resolve_actor_identity() -> str:
    user_ok, user_payload = get_current_user()
    require_ok("get_current_user", user_ok, user_payload)
    return str(
        user_payload.get("mail")
        or user_payload.get("userPrincipalName")
        or user_payload.get("displayName")
        or user_payload.get("id")
    )


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def is_guid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def normalize_pipeline_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith('"') and normalized.endswith('"') and len(normalized) >= 2:
        normalized = normalized[1:-1].strip()
    return normalized


def infer_validation_mode(
    run_summary: dict[str, Any],
    asset_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    organisation_assets = [item for item in asset_snapshots if str(item.get("asset_type")) == "organisation_input"]
    template_assets = [item for item in asset_snapshots if str(item.get("asset_type")) == "template"]

    if organisation_assets and template_assets:
        run_kind = "submission"
    elif template_assets:
        run_kind = "template"
    else:
        run_kind = "submission"

    template_scope = "none"
    if template_assets:
        template_org_sets = {
            tuple(sorted(str(org).strip().upper() for org in item.get("organisation_cds", [])))
            for item in template_assets
        }
        if any(len(orgs) > 1 for orgs in template_org_sets):
            template_scope = "shared_template"
        elif organisation_assets and len(template_assets) == len(organisation_assets):
            template_scope = "one_file_one_template"
        else:
            template_scope = "template_only"

    return {
        "run_kind": run_kind,
        "template_scope": template_scope,
        "selected_organisations": list(run_summary.get("selected_organisations", [])),
        "selected_templates": list(run_summary.get("selected_templates", [])),
        "selected_asset_keys": list(run_summary.get("selected_asset_keys", [])),
    }


def build_validation_units(
    run_summary: dict[str, Any],
    asset_snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    organisation_assets = [item for item in asset_snapshots if str(item.get("asset_type")) == "organisation_input"]
    template_assets = [item for item in asset_snapshots if str(item.get("asset_type")) == "template"]

    if organisation_assets:
        selected_orgs = [
            str(item).strip().upper()
            for item in run_summary.get("selected_organisations", [])
            if str(item).strip()
        ]
        orgs = selected_orgs or sorted(
            {
                str(org_cd).strip().upper()
                for asset in organisation_assets
                for org_cd in asset.get("organisation_cds", [])
                if str(org_cd).strip()
            }
        )
        units: list[dict[str, Any]] = []
        for org_cd in orgs:
            org_asset_subset = [
                item
                for item in organisation_assets
                if org_cd in [str(candidate).strip().upper() for candidate in item.get("organisation_cds", [])]
            ]
            template_subset = [
                item
                for item in template_assets
                if not item.get("organisation_cds")
                or org_cd in [str(candidate).strip().upper() for candidate in item.get("organisation_cds", [])]
            ]
            units.append(
                {
                    "unit_key": f"submission_{org_cd.lower()}",
                    "validation_target": {
                        "type": "submission",
                        "organisation_cd": org_cd,
                        "template_keys": sorted(
                            {
                                str(item["asset_key"]).split("::", 1)[0].strip().upper()
                                for item in template_subset
                            }
                        ),
                    },
                    "asset_snapshots": [*org_asset_subset, *template_subset],
                }
            )
        return units

    template_keys = sorted(
        {
            str(item["asset_key"]).split("::", 1)[0].strip().upper()
            for item in template_assets
        }
    )
    return [
        {
            "unit_key": f"template_{template_key.lower()}",
            "validation_target": {
                "type": "template",
                "template_key": template_key,
            },
            "asset_snapshots": [
                item
                for item in template_assets
                if str(item["asset_key"]).split("::", 1)[0].strip().upper() == template_key
            ],
        }
        for template_key in template_keys
    ]


def build_pipeline_config_payload(
    payload: dict[str, Any],
    asset_snapshots: list[dict[str, Any]],
    workspace_name: str,
    lakehouse_name: str,
    config_filepath: str,
    validation_target: dict[str, Any],
) -> dict[str, Any]:
    run_summary = dict(payload["run"])
    staged_assets = list(payload.get("staged_assets", []))
    staged_by_asset_key = {str(item.get("asset_key")): item for item in staged_assets}

    resolved_assets: list[dict[str, Any]] = []
    for asset in asset_snapshots:
        asset_key = str(asset["asset_key"])
        staged_asset = staged_by_asset_key.get(asset_key, {})
        resolved_assets.append(
            {
                "asset_key": asset_key,
                "asset_type": asset.get("asset_type"),
                "organisation_cds": asset.get("organisation_cds", []),
                "sharepoint": {
                    "source_type": asset["source"].get("source_type"),
                    "target_name": asset["source"].get("target_name"),
                    "root_path": asset["source"].get("root_path"),
                    "path": asset["source"].get("path"),
                    "folder_url": asset["source"].get("folder_url"),
                    "created_ts_utc": asset["metadata_snapshot"].get("created"),
                    "modified_ts_utc": asset["metadata_snapshot"].get("modified"),
                    "modified_by": asset["metadata_snapshot"].get("modified_by"),
                },
                "hashes": {
                    "current_hash": asset["hash_snapshot"].get("current_hash"),
                    "validated_hash": asset["hash_snapshot"].get("validated_hash"),
                },
                "lakehouse": {
                    "workspace_name": workspace_name,
                    "lakehouse_name": lakehouse_name,
                    "staged_path": staged_asset.get("staged_path") or staged_asset.get("staging_path"),
                    "byte_count": staged_asset.get("byte_count"),
                },
            }
        )

    return {
        "run": {
            "run_id": run_summary.get("run_id"),
            "submission_id": run_summary.get("submission_id"),
            "process_cd": run_summary.get("process_cd"),
            "submission_period_cd": run_summary.get("submission_period_cd"),
            "requested_by": run_summary.get("requested_by"),
            "requested_ts_utc": run_summary.get("requested_ts_utc"),
            "state": run_summary.get("state"),
        },
        "pipeline_config": {
            "workspace_name": workspace_name,
            "lakehouse_name": lakehouse_name,
            "config_filepath": config_filepath,
        },
        "validation_target": validation_target,
        "validation": infer_validation_mode(run_summary, asset_snapshots),
        "assets": resolved_assets,
        "status_history": payload.get("status_history", []),
    }


def normalize_relative_path(relative_path: str) -> str:
    return "/".join(segment.strip() for segment in relative_path.replace("\\", "/").split("/") if segment.strip())


def extract_item_modified_by(item_payload: dict[str, Any]) -> str | None:
    payload = item_payload.get("lastModifiedBy")
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        user = payload.get("user", {})
        if isinstance(user, dict) and user.get("displayName"):
            return str(user["displayName"])
        application = payload.get("application", {})
        if isinstance(application, dict) and application.get("displayName"):
            return str(application["displayName"])
    return None


def infer_source_from_drive_id(drive_id: str) -> tuple[str, str]:
    direct_drive_ok, direct_drive_payload = get_drive(drive_id)
    require_ok("get_drive", direct_drive_ok, direct_drive_payload)

    drives_ok, drives_payload = list_sharepoint_resources()
    require_ok("list_sharepoint_resources", drives_ok, drives_payload)
    for drive in drives_payload:
        if drive.get("id") == drive_id:
            return "drive", str(drive.get("name"))

    groups_ok, groups_payload = list_groups()
    require_ok("list_groups", groups_ok, groups_payload)
    for group in groups_payload:
        group_drive_ok, group_drive_payload = get_group_drive(group["id"])
        if not group_drive_ok:
            continue
        if group_drive_payload.get("id") == drive_id:
            return "group", str(group.get("name"))

    drive_name = str(direct_drive_payload.get("name") or direct_drive_payload.get("id") or drive_id)
    return "drive", drive_name


def build_tracked_file_from_item(
    drive_id: str,
    item_id: str,
    relative_path: str,
    item_payload: dict[str, Any] | None = None,
) -> TrackedFileRef:
    file_ok, file_payload = download_sharepoint_item(drive_id, item_id)
    require_ok("download_sharepoint_item", file_ok, file_payload)
    metadata = item_payload or {}
    return TrackedFileRef(
        path=relative_path,
        current_hash=hash_bytes(file_payload),
        validated_hash=None,
        created=str(metadata.get("createdDateTime")).strip() if metadata.get("createdDateTime") else None,
        modified=str(metadata.get("lastModifiedDateTime")).strip() if metadata.get("lastModifiedDateTime") else None,
        modified_by=extract_item_modified_by(metadata),
    )


def build_missing_tracked_file(relative_path: str) -> TrackedFileRef:
    return TrackedFileRef(
        path=relative_path,
        watch=False,
        watch_search_term=None,
        current_hash=None,
        validated_hash=None,
        created=None,
        modified=None,
        modified_by=None,
    )


def build_watched_tracked_file(search_term: str) -> TrackedFileRef:
    return TrackedFileRef(
        path=None,
        watch=True,
        watch_search_term=search_term.strip().upper(),
        current_hash=None,
        validated_hash=None,
        created=None,
        modified=None,
        modified_by=None,
    )


def list_sharepoint_descendants(drive_id: str, parent_item_id: str, parent_relative_path: str = "") -> list[dict[str, Any]]:
    items_ok, items_payload = list_sharepoint_drive_items(drive_id, parent_item_id)
    require_ok("list_sharepoint_drive_items", items_ok, items_payload)
    descendants: list[dict[str, Any]] = []
    for item in items_payload:
        item_name = str(item.get("name", "")).strip("/")
        relative_path = "/".join(part for part in [parent_relative_path, item_name] if part)
        enriched = {**item, "relativePath": relative_path}
        descendants.append(enriched)
        if item.get("isFolder") and item.get("id"):
            descendants.extend(list_sharepoint_descendants(drive_id, str(item["id"]), relative_path))
    return descendants


def find_sharepoint_match_by_search_term(items: list[dict[str, Any]], search_term: str) -> dict[str, Any] | None:
    normalized = search_term.strip().upper()
    prefix_matches = [
        item for item in items if str(item.get("name", "")).strip().upper().startswith(normalized)
    ]
    if prefix_matches:
        file_matches = [item for item in prefix_matches if not item.get("isFolder")]
        return file_matches[0] if file_matches else prefix_matches[0]
    similar_matches = [
        item for item in items if normalized and normalized in str(item.get("name", "")).strip().upper()
    ]
    if not similar_matches:
        return None
    file_matches = [item for item in similar_matches if not item.get("isFolder")]
    return file_matches[0] if file_matches else similar_matches[0]


def resolve_tracked_file(
    drive_id: str,
    linked_item_id: str,
    linked_item_name: str,
    linked_item_is_folder: bool,
    linked_item_payload: dict[str, Any],
    relative_path: str,
) -> TrackedFileRef:
    normalized_path = normalize_relative_path(relative_path)
    if not normalized_path:
        if linked_item_is_folder:
            raise RuntimeError("Tracked SharePoint file path must not be empty when folder_url points to a folder.")
        normalized_path = linked_item_name

    if not linked_item_is_folder:
        if normalized_path != linked_item_name:
            return build_missing_tracked_file(normalized_path)
        return build_tracked_file_from_item(drive_id, linked_item_id, normalized_path, linked_item_payload)

    parent_item_id = linked_item_id
    item_payload: dict[str, Any] | None = None
    for part in normalized_path.split("/"):
        items_ok, items_payload = list_sharepoint_drive_items(drive_id, parent_item_id)
        require_ok("list_sharepoint_drive_items", items_ok, items_payload)
        matches = [item for item in items_payload if item.get("name") == part]
        if not matches:
            return build_missing_tracked_file(normalized_path)
        if len(matches) > 1:
            raise RuntimeError(f"SharePoint path '{normalized_path}' is ambiguous under the linked folder.")
        item_payload = matches[0]
        parent_item_id = str(item_payload["id"])

    if item_payload is None or item_payload.get("isFolder"):
        return build_missing_tracked_file(normalized_path)
    return build_tracked_file_from_item(drive_id, str(item_payload["id"]), normalized_path, item_payload)


def resolve_sharepoint_source(payload: dict[str, Any], label: str) -> SharePointSourceRef:
    stage(f"Resolving SharePoint source for {label}")
    folder_url = payload.get("folder_url")
    tracked_file_requests = [
        TrackedFileRef.from_dict(item)
        for item in payload.get("tracked_files", [])
        if isinstance(item, (dict, str))
    ]
    files = [str(item).strip() for item in payload.get("files", []) if str(item).strip()]
    if not tracked_file_requests:
        tracked_file_requests = [TrackedFileRef(path=item, watch=False, watch_search_term=None) for item in files]
    if not tracked_file_requests:
        raise RuntimeError(f"{label} must include at least one tracked file or watch entry.")
    drive_id = str(payload.get("drive_id") or "").strip() or None
    linked_item_id = str(payload.get("linked_item_id") or "").strip() or None
    linked_item_name = str(payload.get("linked_item_name") or "").strip() or None
    linked_item_is_folder = payload.get("linked_item_is_folder")
    source_type = str(payload.get("source_type") or "").strip().lower() or None
    target_name = str(payload.get("target_name") or "").strip() or None
    root_path = str(payload.get("root_path") or "").strip() or None

    if not all([drive_id, linked_item_id, linked_item_name, source_type, target_name, root_path]) or linked_item_is_folder is None:
        if not folder_url:
            raise RuntimeError(f"{label} is missing both resolved SharePoint metadata and folder_url.")
        share_ok, share_payload = resolve_sharepoint_share_url(folder_url)
        require_ok("resolve_sharepoint_share_url", share_ok, share_payload)
        parent_reference = share_payload.get("parentReference", {})
        drive_id = parent_reference.get("driveId")
        if not drive_id:
            raise RuntimeError(f"Resolved SharePoint folder for '{label}' did not include driveId.")
        linked_item_id = share_payload.get("id")
        if not linked_item_id:
            raise RuntimeError(f"Resolved SharePoint folder for '{label}' did not include item id.")
        linked_item_name = str(share_payload.get("name", "")).strip("/")
        linked_item_is_folder = bool(share_payload.get("folder"))
        source_type, target_name = infer_source_from_drive_id(drive_id)
        root_path = str(parent_reference.get("path", "")).split("root:/", 1)[-1].strip("/")
        payload["drive_id"] = drive_id
        payload["linked_item_id"] = linked_item_id
        payload["linked_item_name"] = linked_item_name
        payload["linked_item_is_folder"] = linked_item_is_folder
        payload["source_type"] = source_type
        payload["target_name"] = target_name
        payload["root_path"] = root_path
        share_payload_for_resolution = share_payload
    else:
        share_payload_for_resolution = {
            "id": linked_item_id,
            "name": linked_item_name,
            "folder": {} if linked_item_is_folder else None,
        }

    combined_root = "/".join(part for part in [root_path, linked_item_name] if part) if linked_item_is_folder else root_path
    descendants = (
        list_sharepoint_descendants(str(drive_id), str(linked_item_id))
        if bool(linked_item_is_folder)
        else []
    )
    tracked_files: list[TrackedFileRef] = []
    for tracked_request in tracked_file_requests:
        if tracked_request.path:
            tracked_files.append(
                resolve_tracked_file(
                    drive_id,
                    str(linked_item_id),
                    linked_item_name,
                    bool(linked_item_is_folder),
                    share_payload_for_resolution,
                    tracked_request.path,
                )
            )
            continue
        if tracked_request.watch:
            search_term = tracked_request.watch_search_term or label
            matched_item = find_sharepoint_match_by_search_term(descendants, search_term)
            if matched_item and not matched_item.get("isFolder"):
                resolved = build_tracked_file_from_item(
                    str(drive_id),
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
                        validated_hash=tracked_request.validated_hash,
                        created=resolved.created,
                        modified=resolved.modified,
                        modified_by=resolved.modified_by,
                    )
                )
            else:
                tracked_files.append(build_watched_tracked_file(search_term))
    return SharePointSourceRef(
        source_type=str(source_type),
        target_name=str(target_name),
        root_path=combined_root,
        tracked_files=tracked_files,
        folder_url=folder_url,
        drive_id=str(drive_id),
        linked_item_id=str(linked_item_id),
        linked_item_name=linked_item_name,
        linked_item_is_folder=bool(linked_item_is_folder),
    )


def build_submission_organisations(raw_organisations: dict[str, dict[str, Any]]) -> dict[str, OrganisationSubmissionRef]:
    resolved: dict[str, OrganisationSubmissionRef] = {}
    for organisation_cd, payload in raw_organisations.items():
        source = resolve_sharepoint_source(payload, organisation_cd)
        resolved[organisation_cd] = OrganisationSubmissionRef(
            sharepoint_source=source,
            template_keys=[str(item).strip().upper() for item in payload.get("template_keys", []) if str(item).strip()],
        )
    return resolved


def build_submission_templates(raw_templates: dict[str, dict[str, Any]]) -> dict[str, TemplateSubmissionRef]:
    resolved: dict[str, TemplateSubmissionRef] = {}
    for template_key, payload in raw_templates.items():
        source = resolve_sharepoint_source(payload, template_key)
        resolved[template_key] = TemplateSubmissionRef(
            assignment_mode=str(payload.get("assignment_mode", "shared")).strip().lower(),
            applies_to=[str(item).strip().upper() for item in payload.get("applies_to", []) if str(item).strip()],
            sharepoint_source=source,
        )
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke test that creates/edits a submission and then triggers a validation run."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("smoke_test_submission_api.config.json"),
        help="Path to the smoke test JSON config file.",
    )
    return parser


def refresh_source_hashes(source_payload: dict[str, Any]) -> dict[str, Any]:
    source = SharePointSourceRef.from_dict(source_payload)
    if not source.folder_url:
        raise RuntimeError("Cannot refresh SharePoint hashes without folder_url in the stored source payload.")

    refreshed_source = resolve_sharepoint_source(
        {
            "folder_url": source.folder_url,
            "tracked_files": [item.to_dict() for item in source.tracked_files],
        },
        "REFRESH",
    )
    validated_by_key = {
        (item.path or "", item.watch_search_term or ""): item.validated_hash
        for item in source.tracked_files
    }
    tracked_files = [
        TrackedFileRef(
            path=item.path,
            watch=bool(item.watch),
            watch_search_term=item.watch_search_term,
            current_hash=item.current_hash,
            validated_hash=validated_by_key.get((item.path or "", item.watch_search_term or "")),
            created=item.created,
            modified=item.modified,
            modified_by=item.modified_by,
        )
        for item in refreshed_source.tracked_files
    ]
    return SharePointSourceRef(
        source_type=refreshed_source.source_type,
        target_name=refreshed_source.target_name,
        root_path=refreshed_source.root_path,
        tracked_files=tracked_files,
        folder_url=refreshed_source.folder_url,
    ).to_dict()


def resolve_sharepoint_file_bytes(source_payload: dict[str, Any], relative_path: str) -> tuple[bytes, dict[str, Any]]:
    source = SharePointSourceRef.from_dict(source_payload)
    drive_id = source.drive_id
    linked_item_id = source.linked_item_id or ""
    linked_item_name = source.linked_item_name or ""
    linked_item_is_folder = bool(source.linked_item_is_folder)
    share_payload: dict[str, Any] = {}
    if not drive_id or not linked_item_id or not linked_item_name:
        if not source.folder_url:
            raise RuntimeError("Stored SharePoint source is missing both resolved identifiers and folder_url.")
        share_ok, share_payload = resolve_sharepoint_share_url(source.folder_url)
        require_ok("resolve_sharepoint_share_url", share_ok, share_payload)
        parent_reference = share_payload.get("parentReference", {})
        drive_id = parent_reference.get("driveId")
        if not drive_id:
            raise RuntimeError("Resolved SharePoint source did not include driveId.")
        linked_item_id = str(share_payload.get("id") or "")
        linked_item_name = str(share_payload.get("name", "")).strip("/")
        linked_item_is_folder = bool(share_payload.get("folder"))

    normalized_path = normalize_relative_path(relative_path)
    if not normalized_path:
        raise RuntimeError("Tracked SharePoint file path must not be empty.")

    if not linked_item_is_folder:
        if normalized_path != linked_item_name:
            raise RuntimeError(
                f"Configured tracked file '{normalized_path}' does not match linked SharePoint file '{linked_item_name}'."
            )
        file_ok, file_payload = download_sharepoint_item(str(drive_id), linked_item_id)
        require_ok("download_sharepoint_item", file_ok, file_payload)
        return file_payload, share_payload

    parent_item_id = linked_item_id
    item_payload: dict[str, Any] | None = None
    for part in normalized_path.split("/"):
        items_ok, items_payload = list_sharepoint_drive_items(str(drive_id), parent_item_id)
        require_ok("list_sharepoint_drive_items", items_ok, items_payload)
        matches = [item for item in items_payload if item.get("name") == part]
        if not matches:
            raise RuntimeError(f"Tracked SharePoint file '{normalized_path}' was not found.")
        if len(matches) > 1:
            raise RuntimeError(f"Tracked SharePoint file '{normalized_path}' is ambiguous.")
        item_payload = matches[0]
        parent_item_id = str(item_payload["id"])

    if item_payload is None or item_payload.get("isFolder"):
        raise RuntimeError(f"Tracked SharePoint path '{normalized_path}' must resolve to a file.")
    file_ok, file_payload = download_sharepoint_item(str(drive_id), str(item_payload["id"]))
    require_ok("download_sharepoint_item", file_ok, file_payload)
    return file_payload, item_payload


def stage_file_locally(
    base_dir: Path,
    source_payload: dict[str, Any],
    tracked_file: dict[str, Any],
    run_id: str,
    org_cd: str,
) -> dict[str, Any]:
    relative_path = normalize_relative_path(str(tracked_file["path"]))
    file_bytes, _ = resolve_sharepoint_file_bytes(source_payload, relative_path)
    staging_path = base_dir / "runs" / run_id / org_cd / Path(relative_path)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.write_bytes(file_bytes)
    return {
        "path": relative_path,
        "staging_path": str(staging_path),
        "byte_count": len(file_bytes),
    }


def stage_file_to_fabric(
    workspace_id: str,
    lakehouse_id: str,
    source_payload: dict[str, Any],
    tracked_file: dict[str, Any],
    run_id: str,
    org_cd: str,
) -> dict[str, Any]:
    relative_path = normalize_relative_path(str(tracked_file["path"]))
    file_bytes, _ = resolve_sharepoint_file_bytes(source_payload, relative_path)
    staging_relative_path = f"Files/validation-service/runs/{run_id}/{org_cd}/{relative_path}"
    write_ok, write_payload = write_lakehouse_file(
        workspace_id,
        lakehouse_id,
        staging_relative_path,
        file_bytes,
    )
    require_ok("write_lakehouse_file", write_ok, write_payload)
    return {
        "path": relative_path,
        "staging_path": staging_relative_path,
        "byte_count": len(file_bytes),
    }


def build_api(service_config: dict[str, Any]) -> ValidationServiceApi:
    storage_mode = service_config.get("storage_mode", "local")
    workspace_id = service_config.get("workspace_id", "local-test-workspace")

    if storage_mode == "local":
        base_dir = Path(service_config.get("local_base_dir", Path.cwd() / ".tmp" / "submission-api"))
        events_path = base_dir / "submission_events.jsonl"
        projection_path = base_dir / "submissions_current.json"
        idempotency_path = base_dir / "idempotency.json"
        return ValidationServiceApi(
            workspace_id=workspace_id,
            submissions_registry_path=str(events_path),
            event_store=JsonlSubmissionEventStore(events_path),
            idempotency_store=JsonFileIdempotencyStore(idempotency_path),
            submissions_projection_path=projection_path,
            sharepoint_hash_resolver=refresh_source_hashes,
            run_file_stager=lambda source_payload, tracked_file, run_id, org_cd: stage_file_locally(
                base_dir,
                source_payload,
                tracked_file,
                run_id,
                org_cd,
            ),
        )

    if storage_mode == "fabric":
        workspace_name = service_config["workspace_display_name"]
        lakehouse_name = service_config["lakehouse_display_name"]
        workspaces_ok, workspaces_payload = list_fabric_workspaces()
        require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
        workspace = find_item_by_display_name(workspaces_payload, workspace_name, "Workspace")
        workspace_id = str(workspace["id"])

        lakehouses_ok, lakehouses_payload = list_fabric_lakehouses(workspace_id)
        require_ok("list_fabric_lakehouses", lakehouses_ok, lakehouses_payload)
        lakehouse = find_item_by_display_name(lakehouses_payload, lakehouse_name, "Lakehouse")
        lakehouse_id = str(lakehouse["id"])

        events_path = service_config["submissions_events_path"]
        projection_path = service_config["submissions_projection_path"]
        idempotency_path = service_config["idempotency_path"]

        def read_text(relative_path: str):
            success, payload = download_lakehouse_file(workspace_id, lakehouse_id, relative_path)
            if success:
                return payload.decode("utf-8")
            return parse_optional_json_text(payload)

        def write_text(relative_path: str, content: str):
            success, payload = write_lakehouse_file(
                workspace_id,
                lakehouse_id,
                relative_path,
                content.encode("utf-8"),
            )
            if not success:
                raise RuntimeError(payload)

        return ValidationServiceApi(
            workspace_id=workspace_id,
            submissions_registry_path=events_path,
            event_store=TextBackedSubmissionEventStore(
                read_text=lambda: read_text(events_path),
                write_text=lambda content: write_text(events_path, content),
            ),
            idempotency_store=TextBackedIdempotencyStore(
                read_text=lambda: read_text(idempotency_path),
                write_text=lambda content: write_text(idempotency_path, content),
            ),
            submissions_projection_store=TextBackedProjectionStore(
                write_text=lambda content: write_text(projection_path, content),
            ),
            sharepoint_hash_resolver=refresh_source_hashes,
            run_file_stager=lambda source_payload, tracked_file, run_id, org_cd: stage_file_to_fabric(
                workspace_id,
                lakehouse_id,
                source_payload,
                tracked_file,
                run_id,
                org_cd,
            ),
        )

    raise RuntimeError(f"Unsupported storage_mode '{storage_mode}'.")


def build_asset_stager(service_config: dict[str, Any]):
    storage_mode = service_config.get("storage_mode", "local")
    if storage_mode == "fabric":
        workspace_name = service_config["workspace_display_name"]
        lakehouse_name = service_config["lakehouse_display_name"]
        workspaces_ok, workspaces_payload = list_fabric_workspaces()
        require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
        workspace = find_item_by_display_name(workspaces_payload, workspace_name, "Workspace")
        workspace_id = str(workspace["id"])

        lakehouses_ok, lakehouses_payload = list_fabric_lakehouses(workspace_id)
        require_ok("list_fabric_lakehouses", lakehouses_ok, lakehouses_payload)
        lakehouse = find_item_by_display_name(lakehouses_payload, lakehouse_name, "Lakehouse")
        lakehouse_id = str(lakehouse["id"])

        def stage_asset(snapshot: Any, run_id: str):
            stage(f"Staging asset to Fabric: {snapshot.asset_key} ({snapshot.asset_type}) -> {snapshot.source.path}")
            source_payload = {
                "source_type": snapshot.source.source_type,
                "target_name": snapshot.source.target_name,
                "root_path": snapshot.source.root_path,
                "folder_url": snapshot.source.folder_url,
            }
            tracked_file = {"path": snapshot.source.path}
            org_cd = (
                f"companies/{snapshot.organisation_cds[0]}"
                if snapshot.asset_type == "organisation_input" and snapshot.organisation_cds
                else f"templates/{snapshot.asset_key}"
            )
            staged = stage_file_to_fabric(workspace_id, lakehouse_id, source_payload, tracked_file, run_id, org_cd)
            staged["asset_key"] = snapshot.asset_key
            print(f"Staged to: {staged['staging_path']}")
            return StagedAsset.from_dict(staged)

        return workspace_id, lakehouse_id, stage_asset

    base_dir = Path(service_config.get("local_base_dir", Path.cwd() / ".tmp" / "validation-run-api"))

    def stage_asset(snapshot: Any, run_id: str):
        stage(f"Staging asset locally: {snapshot.asset_key} ({snapshot.asset_type}) -> {snapshot.source.path}")
        source_payload = {
            "source_type": snapshot.source.source_type,
            "target_name": snapshot.source.target_name,
            "root_path": snapshot.source.root_path,
            "folder_url": snapshot.source.folder_url,
        }
        tracked_file = {"path": snapshot.source.path}
        org_cd = (
            f"companies/{snapshot.organisation_cds[0]}"
            if snapshot.asset_type == "organisation_input" and snapshot.organisation_cds
            else f"templates/{snapshot.asset_key}"
        )
        staged = stage_file_locally(base_dir, source_payload, tracked_file, run_id, org_cd)
        staged["asset_key"] = snapshot.asset_key
        print(f"Staged to: {staged['staging_path']}")
        return StagedAsset.from_dict(staged)

    return None, None, stage_asset


def build_run_api(
    service_config: dict[str, Any],
    pipeline_config: dict[str, Any],
    submission_api: Any,
    asset_stager: Any,
):
    submission_resolver = lambda submission_id: submission_api._get_submission_projection(submission_id)  # noqa: SLF001
    submission_flag_setter = submission_api.set_validation_flags
    storage_mode = service_config.get("storage_mode", "local")

    if storage_mode == "local":
        base_dir = Path(service_config.get("local_base_dir", Path.cwd() / ".tmp" / "submission-api"))
        workspace_name = str(service_config.get("workspace_display_name", "local-workspace"))
        lakehouse_name = str(service_config.get("lakehouse_display_name", "local-lakehouse"))
        runs_events_path = base_dir / "run_events.jsonl"
        runs_projection_path = base_dir / "runs_current.json"
        runs_idempotency_path = base_dir / "run_idempotency.json"

        def write_pipeline_config(payload: dict[str, Any]) -> list[str]:
            run_id = str(payload["run"]["run_id"])
            config_dir = base_dir / "runs" / run_id
            config_dir.mkdir(parents=True, exist_ok=True)
            config_paths: list[str] = []
            for unit in build_validation_units(payload["run"], list(payload.get("asset_snapshots", []))):
                config_path = config_dir / f"{unit['unit_key']}.pipeline_config.json"
                config_payload = build_pipeline_config_payload(
                    payload=payload,
                    asset_snapshots=unit["asset_snapshots"],
                    workspace_name=workspace_name,
                    lakehouse_name=lakehouse_name,
                    config_filepath=str(config_path),
                    validation_target=unit["validation_target"],
                )
                config_path.write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
                config_paths.append(str(config_path))
            return config_paths

        return ValidationRunApi(
            submission_resolver=submission_resolver,
            submission_flag_setter=submission_flag_setter,
            asset_stager=asset_stager,
            event_store=JsonlRunEventStore(runs_events_path),
            idempotency_store=JsonFileIdempotencyStore(runs_idempotency_path),
            runs_projection_store=JsonProjectionStore(runs_projection_path),
            pipeline_trigger=lambda payload: {
                "pipeline_id": "local-pipeline",
                "pipeline_run_id": f"local-{payload['run']['run_id']}",
                "triggered_ts_utc": payload["status_history"][-1]["ts_utc"] if payload["status_history"] else payload["run"]["requested_ts_utc"],
            },
        )

    if storage_mode == "fabric":
        workspace_name = service_config["workspace_display_name"]
        lakehouse_name = service_config["lakehouse_display_name"]
        workspaces_ok, workspaces_payload = list_fabric_workspaces()
        require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
        workspace = find_item_by_display_name(workspaces_payload, workspace_name, "Workspace")
        workspace_id = str(workspace["id"])

        lakehouses_ok, lakehouses_payload = list_fabric_lakehouses(workspace_id)
        require_ok("list_fabric_lakehouses", lakehouses_ok, lakehouses_payload)
        lakehouse = find_item_by_display_name(lakehouses_payload, lakehouse_name, "Lakehouse")
        lakehouse_id = str(lakehouse["id"])

        runs_events_path = str(service_config.get("runs_events_path", "Files/validation-service/events/run_events.jsonl"))
        runs_projection_path = str(service_config.get("runs_projection_path", "Files/validation-service/projections/runs_current.json"))
        runs_idempotency_path = str(service_config.get("runs_idempotency_path", "Files/validation-service/system/run_idempotency.json"))

        def read_text(relative_path: str):
            success, payload = download_lakehouse_file(workspace_id, lakehouse_id, relative_path)
            if success:
                return payload.decode("utf-8")
            return parse_optional_json_text(payload)

        def write_text(relative_path: str, content: str):
            success, payload = write_lakehouse_file(workspace_id, lakehouse_id, relative_path, content.encode("utf-8"))
            if not success:
                raise RuntimeError(payload)

        def write_pipeline_config(payload: dict[str, Any]) -> list[str]:
            run_id = str(payload["run"]["run_id"])
            config_paths: list[str] = []
            for unit in build_validation_units(payload["run"], list(payload.get("asset_snapshots", []))):
                config_relative_path = f"Files/validation-service/runs/{run_id}/{unit['unit_key']}.pipeline_config.json"
                config_payload = build_pipeline_config_payload(
                    payload=payload,
                    asset_snapshots=unit["asset_snapshots"],
                    workspace_name=workspace_name,
                    lakehouse_name=lakehouse_name,
                    config_filepath=config_relative_path,
                    validation_target=unit["validation_target"],
                )
                write_text(config_relative_path, json.dumps(config_payload, indent=2))
                config_paths.append(config_relative_path)
            return config_paths

        pipeline_workspace_name = str(pipeline_config.get("workspace_display_name", "")).strip()
        pipeline_display_name = str(pipeline_config.get("pipeline_display_name", "")).strip()
        pipeline_workspace_id = workspace_id
        pipeline_id = ""

        if pipeline_workspace_name:
            workspaces_ok, workspaces_payload = list_fabric_workspaces()
            require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
            pipeline_workspace = find_item_by_display_name(
                workspaces_payload,
                pipeline_workspace_name,
                "Pipeline workspace",
            )
            pipeline_workspace_id = str(pipeline_workspace["id"])

        if pipeline_display_name:
            pipelines_ok, pipelines_payload = list_fabric_pipelines(pipeline_workspace_id)
            require_ok("list_fabric_pipelines", pipelines_ok, pipelines_payload)
            pipeline = find_item_by_display_name(
                pipelines_payload,
                pipeline_display_name,
                "Pipeline",
            )
            pipeline_id = str(pipeline["id"])

        def resolve_pipeline_run_id(fallback_run_id: str) -> str:
            runs_ok, runs_payload = list_fabric_pipeline_runs(pipeline_workspace_id, pipeline_id)
            require_ok("list_fabric_pipeline_runs", runs_ok, runs_payload)
            if not runs_payload:
                return ""
            latest = runs_payload[0]
            candidate = (
                latest.get("id")
                or latest.get("jobInstanceId")
                or latest.get("jobId")
                or latest.get("runId")
            )
            candidate_value = str(candidate).strip() if candidate is not None else ""
            return candidate_value if is_guid(candidate_value) else ""

        def pipeline_trigger(payload: dict[str, Any]):
            if not pipeline_id:
                return {
                    "pipeline_id": "smoke-fallback-pipeline",
                    "pipeline_run_id": f"fallback-{payload['run']['run_id']}",
                    "triggered_ts_utc": payload["run"]["requested_ts_utc"],
                }
            config_filepaths = write_pipeline_config(payload)
            success, response_payload = trigger_fabric_pipeline(
                pipeline_workspace_id,
                pipeline_id,
                parameters={
                    "workspace_name": workspace_name,
                    "lakehouse_name": lakehouse_name,
                    "config_filepath": config_filepaths,
                },
            )
            require_ok("trigger_fabric_pipeline", success, response_payload)
            raw_pipeline_run_id = (
                response_payload.get("id")
                or response_payload.get("jobInstanceId")
                or response_payload.get("jobId")
                or response_payload.get("runId")
            )
            pipeline_run_id = str(raw_pipeline_run_id).strip() if raw_pipeline_run_id is not None else ""
            if pipeline_run_id.lower() == "none":
                pipeline_run_id = ""
            if not is_guid(pipeline_run_id):
                pipeline_run_id = resolve_pipeline_run_id(payload["run"]["run_id"])
            return {
                "pipeline_id": pipeline_id,
                "pipeline_run_id": pipeline_run_id,
                "triggered_ts_utc": payload["status_history"][-1]["ts_utc"] if payload["status_history"] else payload["run"]["requested_ts_utc"],
            }

        def pipeline_status_resolver(pipeline: Any, _projection: dict[str, Any]):
            if pipeline.pipeline_id == "smoke-fallback-pipeline":
                return {"status": "running"}
            if not is_guid(pipeline.pipeline_run_id):
                resolved_run_id = resolve_pipeline_run_id(_projection["run"]["run_id"])
                if not is_guid(resolved_run_id):
                    return {"status": "running"}
                pipeline_run_id = resolved_run_id
            else:
                pipeline_run_id = pipeline.pipeline_run_id
            success, response_payload = get_fabric_pipeline_run(
                pipeline_workspace_id,
                pipeline.pipeline_id,
                pipeline_run_id,
            )
            if not success and "JobInstanceNotFound" in str(response_payload):
                resolved_run_id = resolve_pipeline_run_id(_projection["run"]["run_id"])
                if not is_guid(resolved_run_id):
                    return {"status": "running"}
                success, response_payload = get_fabric_pipeline_run(
                    pipeline_workspace_id,
                    pipeline.pipeline_id,
                    resolved_run_id,
                )
            require_ok("get_fabric_pipeline_run", success, response_payload)
            return {
                "status": str(response_payload.get("status") or response_payload.get("state") or "running").lower(),
                "error": response_payload.get("failureReason"),
            }

        return ValidationRunApi(
            submission_resolver=submission_resolver,
            submission_flag_setter=submission_flag_setter,
            asset_stager=asset_stager,
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
            pipeline_trigger=pipeline_trigger,
            pipeline_status_resolver=pipeline_status_resolver,
        )

    raise RuntimeError(f"Unsupported storage_mode '{storage_mode}'.")


def resolve_pipeline_target(
    service_config: dict[str, Any],
    pipeline_config: dict[str, Any],
) -> tuple[str, str] | None:
    if service_config.get("storage_mode", "local") != "fabric":
        return None

    pipeline_workspace_name = str(pipeline_config.get("workspace_display_name", "")).strip()
    pipeline_display_name = str(pipeline_config.get("pipeline_display_name", "")).strip()
    if not pipeline_workspace_name or not pipeline_display_name:
        return None

    workspaces_ok, workspaces_payload = list_fabric_workspaces()
    require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    pipeline_workspace = find_item_by_display_name(
        workspaces_payload,
        pipeline_workspace_name,
        "Pipeline workspace",
    )
    pipeline_workspace_id = str(pipeline_workspace["id"])

    pipelines_ok, pipelines_payload = list_fabric_pipelines(pipeline_workspace_id)
    require_ok("list_fabric_pipelines", pipelines_ok, pipelines_payload)
    pipeline = find_item_by_display_name(
        pipelines_payload,
        pipeline_display_name,
        "Pipeline",
    )
    return pipeline_workspace_id, str(pipeline["id"])


def ensure_submission(
    submission_api: Any,
    submission_config: dict[str, Any],
    actor_identity: str,
) -> dict[str, Any]:
    organisations = build_submission_organisations(submission_config["organisations"])
    templates = build_submission_templates(submission_config.get("templates", {}))
    create_idempotency_key = ensure_idempotency_key(submission_config, "create-submission")

    stage("Creating or reusing submission")
    create_response = submission_api.create_submission(
        process_cd=submission_config["process_cd"],
        submission_period_cd=submission_config["submission_period_cd"],
        organisations=organisations,
        templates=templates,
        created_by=actor_identity,
        idempotency_key=create_idempotency_key,
        allow_duplicate_active=submission_config.get("allow_duplicate_active", False),
        note=submission_config.get("note"),
    )
    pretty("create_submission", create_response.to_dict())

    if (
        not create_response.ok
        and create_response.error is not None
        and create_response.error.code == "IDEMPOTENCY_CONFLICT"
    ):
        stage("Create submission hit idempotency conflict, retrying with a fresh key")
        submission_config["idempotency_key"] = f"create-submission-{uuid4().hex}"
        create_response = submission_api.create_submission(
            process_cd=submission_config["process_cd"],
            submission_period_cd=submission_config["submission_period_cd"],
            organisations=organisations,
            templates=templates,
            created_by=actor_identity,
            idempotency_key=submission_config["idempotency_key"],
            allow_duplicate_active=submission_config.get("allow_duplicate_active", False),
            note=submission_config.get("note"),
        )
        pretty("create_submission retry", create_response.to_dict())

    if create_response.ok and create_response.data:
        return create_response.data

    if (
        not create_response.ok
        and create_response.error is not None
        and create_response.error.code == "DUPLICATE_ACTIVE_SUBMISSION"
        and create_response.error.details
        and create_response.error.details.get("submission_id")
    ):
        submission_id = str(create_response.error.details["submission_id"])
        stage("Using existing active submission")
        existing = submission_api.list_submissions(
            process_cd=submission_config["process_cd"],
            submission_period_cd=submission_config["submission_period_cd"],
        )
        pretty("list_submissions", existing.to_dict())
        if existing.ok and existing.data:
            matches = [item for item in existing.data if item["submission_id"] == submission_id]
            if matches:
                return matches[0]

    raise RuntimeError("Unable to create or locate an active submission for the smoke test.")


def edit_submission(
    submission_api: Any,
    submission_id: str,
    actor_identity: str,
    scenario: dict[str, Any],
) -> dict[str, Any]:
    edit_config = scenario.get("edit_submission", {})
    edit_idempotency_key = ensure_idempotency_key(edit_config, "edit-submission")
    note = edit_config.get("note")
    if note is None:
        note = build_random_smoke_note("Submission smoke edit")

    stage("Editing submission")
    response = submission_api.edit_submission(
        submission_id=submission_id,
        modified_by=str(edit_config.get("modified_by") or actor_identity),
        idempotency_key=edit_idempotency_key,
        note=note,
        reason=str(edit_config.get("reason") or "Smoke test submission edit before validation run"),
    )
    pretty("edit_submission", response.to_dict())
    if not response.ok or not response.data:
        raise RuntimeError("Submission edit failed during smoke test.")
    return response.data


def trigger_validation_run(
    run_api: Any,
    submission_id: str,
    actor_identity: str,
    scenario: dict[str, Any],
) -> str:
    run_config = dict(scenario.get("validation_run") or {})
    requested_organisations = run_config.get("requested_organisations")
    requested_template_keys = run_config.get("requested_template_keys")
    requested_asset_keys = run_config.get("requested_asset_keys")

    stage("Planning validation run")
    planned = run_api.plan_run(
        PlanRunRequest(
            submission_id=submission_id,
            requested_organisations=requested_organisations,
            requested_template_keys=requested_template_keys,
            requested_asset_keys=requested_asset_keys,
            force_revalidate=bool(run_config.get("force_revalidate", False)),
        )
    )
    pretty("plan_run", planned.to_dict())

    stage("Creating validation run")
    create_run_idempotency_key = ensure_idempotency_key(run_config, "create-run")
    created = run_api.create_run(
        CreateRunRequest(
            submission_id=submission_id,
            requested_by=actor_identity,
            idempotency_key=create_run_idempotency_key,
            requested_organisations=requested_organisations,
            requested_template_keys=requested_template_keys,
            requested_asset_keys=requested_asset_keys,
        )
    )
    pretty("create_run", created.to_dict())

    run_id = created.run.run_id

    stage("Staging validation run inputs")
    staged = run_api.stage_run_inputs(
        StageRunInputsRequest(
            run_id=run_id,
            staged_by=str(run_config.get("staged_by") or actor_identity),
        )
    )
    pretty("stage_run_inputs", staged.to_dict())

    stage("Triggering validation run")
    triggered = run_api.trigger_run(
        TriggerRunRequest(
            run_id=run_id,
            triggered_by=str(run_config.get("triggered_by") or actor_identity),
        )
    )
    pretty("trigger_run", triggered.to_dict())

    stage("Refreshing validation run status")
    refreshed = run_api.refresh_run_status(RefreshRunStatusRequest(run_id=run_id))
    pretty("refresh_run_status", refreshed.to_dict())

    stage("Getting validation run")
    fetched = run_api.get_run(GetRunRequest(run_id=run_id))
    pretty("get_run", fetched.to_dict())
    return run_id


def poll_validation_run_until_stopped(
    run_api: Any,
    run_id: str,
    service_config: dict[str, Any],
    pipeline_config: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    run_config = scenario.get("validation_run") or {}
    poll_seconds = max(int(run_config.get("poll_interval_seconds", 10)), 1)
    timeout_seconds = max(int(run_config.get("poll_timeout_seconds", 600)), poll_seconds)
    deadline = time.time() + timeout_seconds
    pipeline_target = resolve_pipeline_target(service_config, pipeline_config)

    last_payload: dict[str, Any] | None = None
    iteration = 0
    while True:
        iteration += 1
        stage(f"Polling validation run status ({iteration})")
        refreshed = run_api.refresh_run_status(RefreshRunStatusRequest(run_id=run_id))
        last_payload = refreshed.to_dict()
        pretty("refresh_run_status", last_payload)

        current_status = normalize_pipeline_status(refreshed.pipeline_status)
        payload_status = normalize_pipeline_status(last_payload.get("pipeline_status"))
        if current_status in TERMINAL_PIPELINE_STATUSES:
            break
        if payload_status in TERMINAL_PIPELINE_STATUSES:
            break

        if pipeline_target is not None:
            pipeline_workspace_id, pipeline_id = pipeline_target
            running_ok, running_payload = list_running_fabric_pipeline_runs(
                pipeline_workspace_id,
                pipeline_id,
            )
            require_ok("list_running_fabric_pipeline_runs", running_ok, running_payload)
            pretty(
                "running_pipeline_runs",
                {
                    "workspace_id": pipeline_workspace_id,
                    "pipeline_id": pipeline_id,
                    "count": len(running_payload),
                    "items": running_payload,
                },
            )
            if not running_payload and (
                current_status in TERMINAL_PIPELINE_STATUSES
                or payload_status in TERMINAL_PIPELINE_STATUSES
                or payload_status == "completed"
            ):
                break

        if time.time() >= deadline:
            raise RuntimeError(
                f"Pipeline polling timed out after {timeout_seconds} seconds for run '{run_id}'."
            )
        time.sleep(poll_seconds)

    stage("Getting validation run after polling")
    fetched = run_api.get_run(GetRunRequest(run_id=run_id))
    pretty("get_run after polling", fetched.to_dict())
    return last_payload or {}


def main():
    parser = build_parser()
    args = parser.parse_args()

    stage(f"Loading config from {args.config}")
    config = load_config(args.config)
    config_before = json.dumps(config, sort_keys=True)
    service_config = config["service"]
    pipeline_config = config.get("pipeline", {})
    scenario = config["scenario"]

    stage("Building submission API")
    submission_api = build_api(service_config)

    stage("Resolving current user")
    actor_identity = resolve_actor_identity()
    print(f"Actor: {actor_identity}")

    submission_projection = ensure_submission(
        submission_api=submission_api,
        submission_config=scenario["submission"],
        actor_identity=actor_identity,
    )
    config_after = json.dumps(config, sort_keys=True)
    if config_after != config_before:
        stage("Persisting resolved SharePoint metadata into config")
        save_config(args.config, config)
    submission_id = str(submission_projection["submission_id"])

    edited_submission = edit_submission(
        submission_api=submission_api,
        submission_id=submission_id,
        actor_identity=actor_identity,
        scenario=scenario,
    )

    stage("Building validation run API")
    _workspace_id, _lakehouse_id, asset_stager = build_asset_stager(service_config)
    run_api = build_run_api(
        service_config=service_config,
        pipeline_config=pipeline_config,
        submission_api=submission_api,
        asset_stager=asset_stager,
    )

    run_id = trigger_validation_run(
        run_api=run_api,
        submission_id=submission_id,
        actor_identity=actor_identity,
        scenario=scenario,
    )

    poll_validation_run_until_stopped(
        run_api=run_api,
        run_id=run_id,
        service_config=service_config,
        pipeline_config=pipeline_config,
        scenario=scenario,
    )

    stage("Listing validation runs")
    runs = run_api.list_runs(ListRunsRequest(submission_id=submission_id))
    pretty("list_runs", runs.to_dict())

    stage("Smoke test summary")
    pretty(
        "summary",
        {
            "submission_id": submission_id,
            "edited_note": edited_submission.get("note"),
            "run_id": run_id,
            "storage_mode": service_config.get("storage_mode", "local"),
        },
    )


if __name__ == "__main__":
    main()
