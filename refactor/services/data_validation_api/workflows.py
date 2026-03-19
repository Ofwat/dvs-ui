from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable
from uuid import UUID, uuid4

from refactor.services.data_validation_api.submission_service import (
    CreateSubmissionRequest,
    EditSubmissionRequest,
    OrganisationSubmissionRef,
    RefreshSubmissionRequest,
    SharePointSourceRef,
    TemplateSubmissionRef,
    TrackedFileRef,
    UNSET_NOTE,
    ValidationServiceApi,
)
from refactor.services.data_validation_api.validation_run_api import (
    CreateRunRequest,
    FinalizeRunRequest,
    GetRunRequest,
    ListRunningRunsRequest,
    PlanRunRequest,
    PollRunRequest,
    RefreshRunningRunsRequest,
    StageRunInputsRequest,
    StagedAsset,
    TriggerRunRequest,
    ValidationRunApi,
)


def require_ok(action: str, success: bool, payload: Any):
    if not success:
        raise RuntimeError(f"{action} failed: {payload}")


def normalize_relative_path(relative_path: str) -> str:
    return "/".join(segment.strip() for segment in relative_path.replace("\\", "/").split("/") if segment.strip())


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


def _list_sharepoint_descendants(
    drive_id: str,
    parent_item_id: str,
    *,
    parent_relative_path: str = "",
) -> list[dict[str, Any]]:
    from refactor.online_auth import list_sharepoint_drive_items

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
    from refactor.online_auth import get_drive, resolve_sharepoint_share_url

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
        raise RuntimeError("SharePoint link must point to a folder for scanning.")

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
    from refactor.online_auth import download_sharepoint_item

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
    validation_run_id_by_key = {
        (item.path or "", item.watch_search_term or ""): item.validation_run_id
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
                        validation_run_id=validation_run_id_by_key.get((tracked.path or "", tracked.watch_search_term or "")),
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
                        validation_run_id=validation_run_id_by_key.get((tracked.path or "", tracked.watch_search_term or "")),
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
                        validation_run_id=validation_run_id_by_key.get(("", search_term)),
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
                        validation_run_id=validation_run_id_by_key.get(("", search_term)),
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


def resolve_sharepoint_file_bytes(source_payload: dict[str, Any], relative_path: str) -> tuple[bytes, dict[str, Any]]:
    from refactor.online_auth import download_sharepoint_item

    source = SharePointSourceRef.from_dict(source_payload)
    if not source.folder_url:
        raise RuntimeError("SharePoint source is missing folder_url.")
    context, items = resolve_sharepoint_folder_listing(source.folder_url)
    normalized_path = normalize_relative_path(relative_path)
    item_payload = next(
        (
            item
            for item in items
            if normalize_relative_path(str(item.get("relativePath") or item.get("name") or "")) == normalized_path
        ),
        None,
    )
    if item_payload is None or item_payload.get("isFolder"):
        raise RuntimeError(f"Tracked SharePoint path '{normalized_path}' must resolve to a file.")
    file_ok, file_payload = download_sharepoint_item(str(context["drive_id"]), str(item_payload["id"]))
    require_ok("download_sharepoint_item", file_ok, file_payload)
    return file_payload, item_payload


def stage_file_to_fabric(
    workspace_id: str,
    lakehouse_id: str,
    source_payload: dict[str, Any],
    tracked_file: dict[str, Any],
    run_id: str,
    unit_key: str,
    write_file: Callable[[str, str, str, bytes], tuple[bool, Any]],
) -> dict[str, Any]:
    relative_path = normalize_relative_path(str(tracked_file["path"]))
    file_bytes, _item_payload = resolve_sharepoint_file_bytes(source_payload, relative_path)
    staging_relative_path = f"Files/validation-service/runs/{run_id}/{unit_key}/{relative_path}"
    write_ok, write_payload = write_file(workspace_id, lakehouse_id, staging_relative_path, file_bytes)
    require_ok("write_lakehouse_file", write_ok, write_payload)
    return {
        "path": relative_path,
        "staging_path": staging_relative_path,
        "byte_count": len(file_bytes),
    }


def infer_validation_mode(run_summary: dict[str, Any], asset_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    organisation_assets = [item for item in asset_snapshots if item.get("asset_type") == "organisation_input"]
    template_assets = [item for item in asset_snapshots if item.get("asset_type") == "template_input"]
    organisation_count = len({org for item in organisation_assets for org in item.get("organisation_cds", [])})
    template_keys = {str(item.get("asset_key", "")).split("::", 1)[0].strip().upper() for item in template_assets}
    return {
        "run_kind": "submission" if organisation_assets else "template",
        "template_scope": {
            "one_file_one_template": bool(organisation_assets and len(template_keys) == organisation_count),
            "template_count": len(template_keys),
        },
        "selected_organisations": run_summary.get("selected_organisations", []),
        "selected_templates": run_summary.get("selected_templates", []),
    }


def build_validation_units(run_summary: dict[str, Any], asset_snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    organisation_assets = [item for item in asset_snapshots if item.get("asset_type") == "organisation_input"]
    template_assets = [item for item in asset_snapshots if item.get("asset_type") == "template_input"]
    if organisation_assets:
        units: list[dict[str, Any]] = []
        organisation_cds = sorted({org for item in organisation_assets for org in item.get("organisation_cds", [])})
        for organisation_cd in organisation_cds:
            org_asset_subset = [
                item for item in organisation_assets if organisation_cd in item.get("organisation_cds", [])
            ]
            template_subset = [
                item
                for item in template_assets
                if organisation_cd in item.get("organisation_cds", [])
                or not item.get("organisation_cds")
            ]
            units.append(
                {
                    "unit_key": f"submission_{organisation_cd.lower()}",
                    "validation_target": {
                        "type": "submission",
                        "organisation_cd": organisation_cd,
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


def build_submission_refs(payload: dict[str, Any]) -> tuple[dict[str, OrganisationSubmissionRef], dict[str, TemplateSubmissionRef]]:
    organisations: dict[str, OrganisationSubmissionRef] = {}
    for org_cd, org_payload in payload.get("organisations", {}).items():
        key = str(org_cd).strip().upper()
        if isinstance(org_payload, OrganisationSubmissionRef):
            organisations[key] = org_payload
            continue
        if isinstance(org_payload, dict) and "sharepoint_source" in org_payload:
            organisations[key] = OrganisationSubmissionRef.from_dict(org_payload)
            continue
        organisations[key] = OrganisationSubmissionRef(
            sharepoint_source=build_sharepoint_source(org_payload),
            template_keys=[str(item).strip().upper() for item in org_payload.get("template_keys", []) if str(item).strip()],
        )

    templates: dict[str, TemplateSubmissionRef] = {}
    for template_key, template_payload in payload.get("templates", {}).items():
        key = str(template_key).strip().upper()
        if isinstance(template_payload, TemplateSubmissionRef):
            templates[key] = template_payload
            continue
        if isinstance(template_payload, dict) and "sharepoint_source" in template_payload:
            templates[key] = TemplateSubmissionRef.from_dict(template_payload)
            continue
        templates[key] = TemplateSubmissionRef(
            assignment_mode=str(template_payload.get("assignment_mode", "shared")).strip().lower(),
            applies_to=[str(item).strip().upper() for item in template_payload.get("applies_to", []) if str(item).strip()],
            sharepoint_source=build_sharepoint_source(template_payload),
        )
    return organisations, templates


def build_sharepoint_source(payload: dict[str, Any]) -> SharePointSourceRef:
    source_type = str(payload.get("source_type", "drive")).strip().lower()
    target_name = str(payload.get("target_name", "")).strip()
    root_path = str(payload.get("root_path", "")).strip()
    raw_folder_url = payload.get("folder_url")
    raw_drive_id = payload.get("drive_id")
    raw_linked_item_id = payload.get("linked_item_id")
    raw_linked_item_name = payload.get("linked_item_name")
    folder_url = str(raw_folder_url).strip() if raw_folder_url not in (None, "") else None
    drive_id = str(raw_drive_id).strip() if raw_drive_id not in (None, "") else None
    linked_item_id = str(raw_linked_item_id).strip() if raw_linked_item_id not in (None, "") else None
    linked_item_name = str(raw_linked_item_name).strip() if raw_linked_item_name not in (None, "") else None
    linked_item_is_folder = payload.get("linked_item_is_folder")
    tracked_payloads = payload.get("tracked_files")
    if not isinstance(tracked_payloads, list):
        tracked_payloads = [
            {
                "path": str(item).strip(),
                "watch": False,
                "watch_search_term": None,
            }
            for item in payload.get("files", [])
            if str(item).strip()
        ]
    tracked_files = [TrackedFileRef.from_dict(item) for item in tracked_payloads]

    if folder_url and linked_item_id and drive_id:
        sharepoint_context, scanned_items = resolve_sharepoint_folder_listing(folder_url)
        enriched_tracked_files: list[TrackedFileRef] = []
        for tracked in tracked_files:
            if tracked.path:
                matched_item = next(
                    (
                        item
                        for item in scanned_items
                        if normalize_relative_path(str(item.get("relativePath") or item.get("name") or "")) == normalize_relative_path(tracked.path)
                    ),
                    None,
                )
                if matched_item and not matched_item.get("isFolder"):
                    resolved = _build_tracked_file_from_item(
                        str(sharepoint_context["drive_id"]),
                        str(matched_item["id"]),
                        str(matched_item.get("relativePath") or matched_item.get("name") or tracked.path),
                        matched_item,
                    )
                    enriched_tracked_files.append(
                        TrackedFileRef(
                            path=resolved.path,
                            watch=tracked.watch,
                            watch_search_term=tracked.watch_search_term,
                            current_hash=resolved.current_hash,
                            validated_hash=tracked.validated_hash,
                            validation_run_id=tracked.validation_run_id,
                            size_bytes=resolved.size_bytes,
                            created=resolved.created,
                            modified=resolved.modified,
                            modified_by=resolved.modified_by,
                        )
                    )
                    continue
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


@dataclass(frozen=True)
class SubmissionWorkflowResult:
    response: Any


def create_submission_from_discovery(
    submission_service: ValidationServiceApi,
    *,
    process_cd: str,
    submission_period_cd: str,
    organisations_payload: dict[str, Any],
    templates_payload: dict[str, Any] | None,
    created_by: str,
    note: str | None = None,
    allow_duplicate_active: bool = False,
    idempotency_key: str | None = None,
) -> SubmissionWorkflowResult:
    organisations, templates = build_submission_refs(
        {"organisations": organisations_payload, "templates": templates_payload or {}}
    )
    response = submission_service.create_submission(
        CreateSubmissionRequest(
            process_cd=process_cd,
            submission_period_cd=submission_period_cd,
            organisations=organisations,
            templates=templates or {},
            created_by=created_by,
            idempotency_key=idempotency_key or f"create-submission-{uuid4().hex}",
            note=note,
            allow_duplicate_active=allow_duplicate_active,
        )
    )
    return SubmissionWorkflowResult(response=response)


def edit_submission_workflow(
    submission_service: ValidationServiceApi,
    *,
    submission_id: str,
    modified_by: str,
    organisation_payloads: dict[str, Any] | None = None,
    organisation_removals: list[str] | None = None,
    template_payloads: dict[str, Any] | None = None,
    template_removals: list[str] | None = None,
    organisation_validation_changes: dict[str, str] | None = None,
    template_validation_changes: dict[str, str] | None = None,
    note: str | None | object = UNSET_NOTE,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> SubmissionWorkflowResult:
    organisations, templates = build_submission_refs(
        {"organisations": organisation_payloads or {}, "templates": template_payloads or {}}
    )
    response = submission_service.edit_submission(
        EditSubmissionRequest(
            submission_id=submission_id,
            modified_by=modified_by,
            idempotency_key=idempotency_key or f"edit-submission-{uuid4().hex}",
            organisation_upserts=organisations or None,
            organisation_removals=organisation_removals,
            template_upserts=templates or None,
            template_removals=template_removals,
            organisation_validation_changes=organisation_validation_changes,
            template_validation_changes=template_validation_changes,
            note=note,
            reason=reason,
        )
    )
    return SubmissionWorkflowResult(response=response)


def run_submission_workflow(
    submission_service: ValidationServiceApi,
    run_api: ValidationRunApi,
    *,
    submission_id: str,
    actor: str,
    requested_organisations: list[str] | None = None,
    requested_template_keys: list[str] | None = None,
    refresh_submission: bool = False,
    poll_interval_seconds: float | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if refresh_submission:
        result["refresh_submission_hashes"] = submission_service.refresh_submission_hashes(
            RefreshSubmissionRequest(
                submission_id=submission_id,
                refreshed_by=actor,
                idempotency_key=f"refresh-submission-before-run-{uuid4().hex}",
            )
        ).to_dict()

    plan = run_api.plan_run(
        PlanRunRequest(
            submission_id=submission_id,
            requested_organisations=requested_organisations or None,
            requested_template_keys=requested_template_keys or None,
        )
    )
    result["plan_run"] = plan.to_dict()
    created = run_api.create_run(
        CreateRunRequest(
            submission_id=submission_id,
            requested_by=actor,
            idempotency_key=f"create-run-{uuid4().hex}",
            requested_organisations=requested_organisations or None,
            requested_template_keys=requested_template_keys or None,
        )
    )
    result["create_run"] = created.to_dict()
    staged = run_api.stage_run_inputs(
        StageRunInputsRequest(
            run_id=created.run.run_id,
            staged_by=actor,
        )
    )
    result["stage_run_inputs"] = staged.to_dict()
    if staged.state not in {"staged", "partially_succeeded"}:
        result["get_run"] = run_api.get_run(GetRunRequest(run_id=created.run.run_id)).to_dict()
        return result

    triggered = run_api.trigger_run(
        TriggerRunRequest(
            run_id=created.run.run_id,
            triggered_by=actor,
        )
    )
    result["trigger_run"] = triggered.to_dict()
    if poll_interval_seconds is not None and timeout_seconds is not None:
        result["poll_run"] = run_api.poll_run(
            PollRunRequest(
                run_id=created.run.run_id,
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
            )
        ).to_dict()
    result["get_run"] = run_api.get_run(GetRunRequest(run_id=created.run.run_id)).to_dict()
    return result


def refresh_active_runs_workflow(
    run_api: ValidationRunApi,
    *,
    submission_id: str | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    preview = run_api.list_running_runs(
        ListRunningRunsRequest(
            submission_id=submission_id,
            requested_by=requested_by,
            page=1,
            page_size=10_000,
        )
    )
    refreshed = run_api.refresh_running_runs(
        RefreshRunningRunsRequest(
            submission_id=submission_id,
            requested_by=requested_by,
        )
    )
    return {
        "list_running_runs": preview.to_dict(),
        "refresh_running_runs": refreshed.to_dict(),
    }
