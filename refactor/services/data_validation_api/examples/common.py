from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID

ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from refactor.online_auth import (  # noqa: E402
    check_token_access,
    download_lakehouse_file,
    get_fabric_pipeline_run,
    get_current_user,
    list_fabric_lakehouses,
    list_fabric_pipeline_runs,
    list_fabric_pipelines,
    list_fabric_workspaces,
    trigger_fabric_pipeline,
    write_lakehouse_file,
)
from refactor.services.data_validation_api import (  # noqa: E402
    ListSubmissionsRequest,
    StagedAsset,
    TextBackedIdempotencyStore,
    TextBackedProjectionStore,
    TextBackedRunEventStore,
    TextBackedSubmissionEventStore,
    ValidationRunApi,
    ValidationServiceApi,
)
from refactor.services.data_validation_api import workflows as wf  # noqa: E402


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
    return wf.is_guid(value)


def hash_bytes(content: bytes) -> str:
    return wf.hash_bytes(content)


def normalize_relative_path(relative_path: str) -> str:
    return wf.normalize_relative_path(relative_path)


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

resolve_sharepoint_folder_listing = wf.resolve_sharepoint_folder_listing
find_prefix_matches = wf.find_prefix_matches


def build_submission_refs(payload: dict[str, Any]) -> tuple[dict[str, OrganisationSubmissionRef], dict[str, TemplateSubmissionRef]]:
    return wf.build_submission_refs(payload)


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
        sharepoint_hash_resolver=wf.refresh_source_hashes,
    )


def build_run_api(config: dict[str, Any], submission_service: ValidationServiceApi | None = None) -> ValidationRunApi:
    service_config = config.get("service", config)
    workspace_id, read_text, write_text = build_storage_context(service_config)
    runs_events_path = str(service_config.get("runs_events_path", "Files/validation-service/events/run_events.jsonl"))
    runs_projection_path = str(service_config.get("runs_projection_path", "Files/validation-service/projections/runs_current.json"))
    runs_idempotency_path = str(service_config.get("runs_idempotency_path", "Files/validation-service/system/run_idempotency.json"))
    lakehouse_name = str(service_config.get("lakehouse_display_name", "")).strip()
    workspace_name = str(service_config.get("workspace_display_name", "")).strip()

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

    def submission_resolver(submission_id: str) -> dict[str, Any] | None:
        if submission_service is None:
            return None
        response = submission_service.list_submissions(ListSubmissionsRequest())
        items = response.data if response.ok else None
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and str(item.get("submission_id")) == submission_id:
                return item
        return None
    submission_flag_setter = submission_service.set_validation_flags if submission_service is not None else None
    submission_hash_persister = submission_service.persist_validation_hashes if submission_service is not None else None

    workspaces_ok, workspaces_payload = list_fabric_workspaces()
    require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    workspace = find_by_display_name(workspaces_payload, workspace_name, "Workspace")
    lakehouses_ok, lakehouses_payload = list_fabric_lakehouses(str(workspace["id"]))
    require_ok("list_fabric_lakehouses", lakehouses_ok, lakehouses_payload)
    lakehouse = find_by_display_name(lakehouses_payload, lakehouse_name, "Lakehouse")
    lakehouse_id = str(lakehouse["id"])

    def asset_stager(snapshot: Any, run_id: str):
        source_payload = {
            "source_type": snapshot.source.source_type,
            "target_name": snapshot.source.target_name,
            "root_path": snapshot.source.root_path,
            "folder_url": snapshot.source.folder_url,
        }
        tracked_file = {"path": snapshot.source.path}
        unit_key = (
            f"companies/{snapshot.organisation_cds[0]}"
            if snapshot.asset_type == "organisation_input" and snapshot.organisation_cds
            else f"templates/{snapshot.asset_key}"
        )
        staged = wf.stage_file_to_fabric(
            workspace_id,
            lakehouse_id,
            source_payload,
            tracked_file,
            run_id,
            unit_key,
            write_lakehouse_file,
        )
        staged["asset_key"] = snapshot.asset_key
        return StagedAsset.from_dict(staged)

    def write_pipeline_config(payload: dict[str, Any]) -> list[str]:
        run_id = str(payload["run"]["run_id"])
        config_paths: list[str] = []
        for unit in wf.build_validation_units(payload["run"], list(payload.get("asset_snapshots", []))):
            config_relative_path = f"Files/validation-service/runs/{run_id}/{unit['unit_key']}.pipeline_config.json"
            config_payload = wf.build_pipeline_config_payload(
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

    def pipeline_trigger(payload: dict[str, Any]):
        if not pipeline_workspace_id or not pipeline_id:
            return {
                "pipeline_id": "example-fallback-pipeline",
                "pipeline_run_id": f"fallback-{payload['run']['run_id']}",
                "triggered_ts_utc": payload["run"]["requested_ts_utc"],
                "external_metadata": {
                    "pipeline_config_filepaths": [],
                },
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
            pipeline_run_id = resolve_pipeline_run_id(str(payload["run"]["run_id"]))
        return {
            "pipeline_id": pipeline_id,
            "pipeline_run_id": pipeline_run_id,
            "triggered_ts_utc": payload["status_history"][-1]["ts_utc"] if payload["status_history"] else payload["run"]["requested_ts_utc"],
            "external_metadata": {
                "pipeline_config_filepaths": config_filepaths,
            },
        }

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
                return {"status": "running", "pipeline_run_id": ""}
        success, response_payload = get_fabric_pipeline_run(
            pipeline_workspace_id,
            str(pipeline.pipeline_id or pipeline_id),
            pipeline_run_id,
        )
        if not success and "JobInstanceNotFound" in str(response_payload):
            resolved_run_id = resolve_pipeline_run_id(str(projection["run"]["run_id"]))
            if not is_guid(resolved_run_id):
                return {"status": "running", "pipeline_run_id": ""}
            success, response_payload = get_fabric_pipeline_run(
                pipeline_workspace_id,
                str(pipeline.pipeline_id or pipeline_id),
                resolved_run_id,
            )
            pipeline_run_id = resolved_run_id
        if not success:
            return {
                "status": projection["run"]["state"],
                "pipeline_run_id": pipeline_run_id,
                "error": {
                    "code": "PIPELINE_STATUS_FAILED",
                    "message": str(response_payload),
                },
            }
        return {
            "status": str(response_payload.get("status") or response_payload.get("state") or "running").lower(),
            "pipeline_run_id": pipeline_run_id,
            "error": response_payload.get("failureReason"),
        }

    return ValidationRunApi(
        submission_resolver=submission_resolver,
        submission_flag_setter=submission_flag_setter,
        submission_hash_persister=submission_hash_persister,
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
        asset_stager=asset_stager,
        pipeline_trigger=pipeline_trigger,
        pipeline_status_resolver=pipeline_status_resolver,
    )
