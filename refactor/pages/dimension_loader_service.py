from __future__ import annotations

import threading
import time
import uuid
from urllib.parse import parse_qs, unquote, urlparse

from dash import Input, Output, State, callback_context, dcc, html, no_update
from dash.exceptions import PreventUpdate

from components.radios import build_radio_group
from components.summary_list import build_summary_list

from .dimension_loader_state import (
    build_env_missing_message,
    get_dimension_jobs,
    get_dimension_jobs_by_environment,
    get_dimension_pipeline_config,
    get_missing_env_vars,
    load_service_config,
)


_SYNC_RUNS: dict[str, dict[str, object]] = {}
_SYNC_RUNS_LOCK = threading.Lock()


DIMENSION_LOADER_SERVICE = {
    "label": "Dimension Loader",
    "path": "/services/dimension-loader",
    "summary": "Refresh, inspect, and sync SharePoint-backed dimension files into Fabric.",
    "description": (
        "Dimension Loader keeps the Fabric dimension lakehouse aligned with SharePoint sources. "
        "Use the dev or prod environment selector to choose the target job set."
    ),
}


def _debug(message: str) -> None:
    print(f"[DimensionLoader] {message}", flush=True)


def _scan_sharepoint_folder(folder_url: str) -> list[dict[str, object]]:
    from refactor.services.data_validation_api.workflows import resolve_sharepoint_folder_listing

    _context, items = resolve_sharepoint_folder_listing(folder_url)
    return [item for item in items if not item.get("isFolder")]


def _job_source_links(job: dict[str, object]) -> list[str]:
    return [
        str(source_link).strip()
        for source_link in list(job.get("source_links", []) or [])
        if str(source_link).strip()
    ]


def _sharepoint_folder_name(folder_url: str) -> str:
    parsed = urlparse(str(folder_url or "").strip())
    query = parse_qs(parsed.query)
    for key in ("id", "RootFolder", "folder"):
        values = query.get(key) or []
        for value in values:
            path = unquote(str(value).strip().rstrip("/"))
            if path:
                folder_name = path.rsplit("/", 1)[-1].strip()
                if folder_name and folder_name.lower() != "allitems.aspx":
                    return folder_name
    path = unquote(parsed.path.rstrip("/"))
    if not path:
        return "SharePoint folder"
    parts = [segment for segment in path.split("/") if segment]
    if not parts:
        return "SharePoint folder"
    last_segment = parts[-1].strip()
    if last_segment.lower() == "allitems.aspx" and len(parts) >= 2:
        return unquote(parts[-2]).strip() or "SharePoint folder"
    return last_segment or "SharePoint folder"


def _find_by_display_name(items: list[dict[str, object]], display_name: str, label: str) -> dict[str, object]:
    matches = [item for item in items if str(item.get("displayName", "")).strip() == display_name]
    if not matches:
        raise RuntimeError(f"{label} with displayName '{display_name}' was not found.")
    if len(matches) > 1:
        raise RuntimeError(f"{label} with displayName '{display_name}' is not unique.")
    return matches[0]


def _scan_job_files(job: dict[str, object]) -> list[dict[str, object]]:
    scanned_files: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for source_link in _job_source_links(job):
        for item in _scan_sharepoint_folder(source_link):
            source_relative_path = str(item.get("relativePath") or item.get("name") or "").strip().strip("/")
            if not source_relative_path:
                continue
            dedupe_key = (source_link, source_relative_path)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            scanned_files.append(
                {
                    "source_link": source_link,
                    "source_relative_path": source_relative_path,
                    "target_relative_path": source_relative_path,
                }
            )
    return scanned_files


def _build_sharepoint_folder_rows(jobs: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for job in jobs:
        source_links = _job_source_links(job)
        folder_url = source_links[0] if source_links else ""
        rows.append(
            {
                "key": str(job.get("name", "")).strip() or "Unnamed job",
                "value": _sharepoint_folder_name(folder_url),
                "actions": [
                    {
                        "label": "Open folder",
                        "href": folder_url,
                        "visually_hidden_text": f"({str(job.get('name', '')).strip() or 'Unnamed job'})",
                    }
                ]
                if folder_url
                else [],
            }
        )
    return rows


def _prepare_scanned_jobs(jobs: list[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
    prepared_jobs: list[dict[str, object]] = []
    total_files = 0
    for job in jobs:
        scanned_files = _scan_job_files(job)
        prepared_jobs.append({**job, "_scanned_files": scanned_files})
        total_files += len(scanned_files)
    return prepared_jobs, total_files


def _create_sync_state(environment: str | None, total_mappings: int = 0) -> dict[str, object]:
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    return {
        "run_id": uuid.uuid4().hex,
        "environment": selected_env,
        "running": False,
        "done": False,
        "status": "",
        "current_job": "",
        "current_file": "",
        "processed_mappings": 0,
        "total_mappings": total_mappings,
        "pipeline_workspace_id": "",
        "pipeline_id": "",
        "pipeline_run_id": "",
        "pipeline_status": "",
        "pipeline_event": "",
        "pipeline_start_time": "",
        "pipeline_end_time": "",
        "pipeline_failure_reason": "",
        "error": None,
    }


def _set_sync_state(run_id: str, **updates: object) -> dict[str, object]:
    with _SYNC_RUNS_LOCK:
        state = dict(_SYNC_RUNS.get(run_id, {}))
        state.update(updates)
        state["run_id"] = run_id
        _SYNC_RUNS[run_id] = state
        return state


def _get_sync_state(run_id: str | None) -> dict[str, object] | None:
    if not run_id:
        return None
    with _SYNC_RUNS_LOCK:
        state = _SYNC_RUNS.get(run_id)
        return dict(state) if state else None


def _build_progress_text(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    processed = int(state.get("processed_mappings", 0) or 0)
    total = int(state.get("total_mappings", 0) or 0)
    if total <= 0:
        return "0/0"
    return f"{processed}/{total}"


def _build_progress_detail(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    environment = str(state.get("environment", "dev")).upper()
    current_job = str(state.get("current_job", "")).strip()
    current_file = str(state.get("current_file", "")).strip()
    status = str(state.get("status", "")).strip()
    if current_job and current_file:
        return f"{environment} | {current_job} | {current_file}"
    if current_file:
        return f"{environment} | {current_file}"
    if current_job:
        return f"{environment} | {current_job}"
    if status:
        return f"{environment} | {status}"
    return f"{environment} | Waiting to start"


def _parse_fabric_timestamp(timestamp_text: str | None) -> str:
    text = str(timestamp_text or "").strip()
    return text


def _format_pipeline_timestamp(timestamp_text: str | None) -> str:
    text = _parse_fabric_timestamp(timestamp_text)
    if not text:
        return ""
    normalized = text.replace("Z", "+00:00")
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return text


def _format_pipeline_duration(start_time: str | None, end_time: str | None) -> str:
    from datetime import datetime, timezone

    start_text = str(start_time or "").strip()
    end_text = str(end_time or "").strip()
    if not start_text:
        return ""
    try:
        start_dt = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        else:
            start_dt = start_dt.astimezone(timezone.utc)
    except ValueError:
        return ""
    if not end_text:
        elapsed = max((datetime.now(timezone.utc) - start_dt).total_seconds(), 0.0)
        total_seconds = int(elapsed)
    else:
        try:
            end_dt = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            else:
                end_dt = end_dt.astimezone(timezone.utc)
            total_seconds = int(max((end_dt - start_dt).total_seconds(), 0.0))
        except ValueError:
            return ""
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _build_pipeline_status_text(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    pipeline_status = str(state.get("pipeline_status", "")).strip()
    if pipeline_status:
        return pipeline_status
    pipeline_run_id = str(state.get("pipeline_run_id", "")).strip()
    if pipeline_run_id:
        return "Pipeline: queued"
    return ""


def _build_pipeline_event_text(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    pipeline_event = str(state.get("pipeline_event", "")).strip()
    return pipeline_event


def _build_pipeline_details_text(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    start_time = str(state.get("pipeline_start_time", "")).strip()
    end_time = str(state.get("pipeline_end_time", "")).strip()
    failure_reason_raw = state.get("pipeline_failure_reason", "")
    failure_reason = str(failure_reason_raw).strip()
    pipeline_run_id = str(state.get("pipeline_run_id", "")).strip()
    details: list[str] = []
    if start_time:
        details.append(f"Started: {_format_pipeline_timestamp(start_time)}")
        if end_time:
            duration = _format_pipeline_duration(start_time, end_time)
            if duration:
                details.append(f"Duration: {duration}")
        elif pipeline_run_id:
            details.append(f"Elapsed: {_format_pipeline_duration(start_time, '')}")
    if end_time:
        details.append(f"Ended: {_format_pipeline_timestamp(end_time)}")
    if failure_reason and failure_reason.lower() != "none":
        details.append(f"Failure reason: {failure_reason}")
    if details:
        return "Pipeline details: " + " | ".join(details)
    if pipeline_run_id:
        return "Pipeline details: waiting for Fabric timestamps"
    return ""


def _build_pipeline_link(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    workspace_id = str(state.get("pipeline_workspace_id", "")).strip()
    pipeline_id = str(state.get("pipeline_id", "")).strip()
    pipeline_run_id = str(state.get("pipeline_run_id", "")).strip()
    if not workspace_id or not pipeline_id or not pipeline_run_id:
        return ""
    return (
        "https://app.powerbi.com/workloads/data-pipeline/monitoring/"
        f"workspaces/{workspace_id}/pipelines/{pipeline_id}/{pipeline_run_id}?experience=power-bi"
    )


def _build_pipeline_link_component(state: dict[str, object] | None):
    workspace_id = str(state.get("pipeline_workspace_id", "")).strip() if state else ""
    pipeline_id = str(state.get("pipeline_id", "")).strip() if state else ""
    pipeline_run_id = str(state.get("pipeline_run_id", "")).strip() if state else ""
    if not workspace_id or not pipeline_id:
        return ""
    pipeline_page = f"https://app.fabric.microsoft.com/groups/{workspace_id}/pipelines/{pipeline_id}"
    monitor_page = _build_pipeline_link(state)
    children: list[object] = [html.A("Open Fabric pipeline", href=pipeline_page, target="_blank", rel="noopener noreferrer")]
    if monitor_page:
        children.append(html.Span(" ", className="govuk-!-margin-left-1"))
        children.append(html.A("Open Fabric monitor", href=monitor_page, target="_blank", rel="noopener noreferrer"))
    if not pipeline_run_id:
        children.append(html.Span("Pipeline run id is not available yet.", className="govuk-hint"))
    return html.Div(children=children)


def _pipeline_is_terminal(state: dict[str, object] | None) -> bool:
    if not state:
        return False
    status_text = _build_pipeline_status_text(state).removeprefix("Pipeline: ").strip().lower()
    return status_text in {"succeeded", "completed", "failed", "error", "cancelled", "canceled", "partially_succeeded"}


def _pipeline_needs_polling(state: dict[str, object] | None) -> bool:
    if not state:
        return False
    return bool(str(state.get("pipeline_run_id", "")).strip()) and not _pipeline_is_terminal(state)


def _extract_pipeline_status(payload: dict[str, object] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    raw_status = (
        payload.get("status")
        or payload.get("state")
        or payload.get("runStatus")
        or payload.get("jobStatus")
        or payload.get("externalStatus")
        or payload.get("pipelineStatus")
        or payload.get("currentStatus")
        or payload.get("statusText")
        or payload.get("operationStatus")
        or ""
    )
    status = str(raw_status).strip()
    if status:
        return status
    if payload.get("error"):
        return f"Failed: {payload.get('error')}"
    return ""


def _resolve_latest_pipeline_run_id(auth, workspace_id: str, pipeline_id: str) -> str:
    success, payload = auth.list_fabric_pipeline_runs(workspace_id, pipeline_id)
    if not success or not isinstance(payload, list) or not payload:
        return ""
    latest = payload[0]
    candidate = latest.get("id") or latest.get("jobInstanceId") or latest.get("jobId") or latest.get("runId") or ""
    run_id = str(candidate).strip()
    return "" if run_id.lower() == "none" else run_id


def _resolve_new_pipeline_run_id(
    auth,
    workspace_id: str,
    pipeline_id: str,
    previous_run_id: str = "",
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.5,
) -> str:
    previous_run_id = str(previous_run_id).strip()
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        run_id = _resolve_latest_pipeline_run_id(auth, workspace_id, pipeline_id)
        if run_id and run_id != previous_run_id:
            return run_id
        if time.monotonic() >= deadline:
            return ""
        time.sleep(max(poll_interval_seconds, 0.1))


def _refresh_pipeline_status(state: dict[str, object]) -> dict[str, object]:
    pipeline_run_id = str(state.get("pipeline_run_id", "")).strip()
    pipeline_workspace_id = str(state.get("pipeline_workspace_id", "")).strip()
    pipeline_id = str(state.get("pipeline_id", "")).strip()
    if not pipeline_run_id or not pipeline_workspace_id or not pipeline_id:
        return state

    from services.fabric_uploader_cli import app as uploader

    auth = uploader._online_auth()
    run_payload = None
    success, payload = auth.get_fabric_pipeline_run(pipeline_workspace_id, pipeline_id, pipeline_run_id)
    if success and isinstance(payload, dict):
        run_payload = payload
    if run_payload is None:
        success, payload = auth.list_fabric_pipeline_runs(pipeline_workspace_id, pipeline_id)
        if success and isinstance(payload, list) and payload:
            run_payload = next(
                (
                    item
                    for item in payload
                    if str(
                        item.get("id")
                        or item.get("jobInstanceId")
                        or item.get("jobId")
                        or item.get("runId")
                        or ""
                    ).strip()
                    == pipeline_run_id
                ),
                payload[0],
            )
    if not isinstance(run_payload, dict):
        state["pipeline_status"] = "Pipeline: status unavailable"
        return state

    state.update(
        {
            "pipeline_start_time": str(run_payload.get("startTimeUtc", "")).strip(),
            "pipeline_end_time": str(run_payload.get("endTimeUtc", "")).strip(),
            "pipeline_failure_reason": str(run_payload.get("failureReason", "")).strip(),
        }
    )
    status_text = _extract_pipeline_status(run_payload)
    normalized_status = status_text.lower()
    if normalized_status == "notstarted" and state.get("pipeline_start_time") and not state.get("pipeline_end_time"):
        status_text = "InProgress"
        normalized_status = status_text.lower()
    if status_text:
        state["pipeline_status"] = f"Pipeline: {status_text}"
        if normalized_status in {"succeeded", "completed", "failed", "error", "cancelled", "canceled", "partially_succeeded"}:
            state["pipeline_event"] = f"Pipeline finished with status: {status_text}"
    return state


def build_dimension_loader_content():
    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full",
                children=[
                    html.H2(DIMENSION_LOADER_SERVICE["label"], className="govuk-heading-l"),
                    html.P(DIMENSION_LOADER_SERVICE["summary"], className="govuk-body"),
                    html.P(
                        DIMENSION_LOADER_SERVICE["description"],
                        className="govuk-body govuk-!-margin-bottom-4",
                    ),
                    build_radio_group(
                        legend="Choose environment",
                        radio_id="dimension-loader-environment",
                        options=[
                            {"label": "dev", "value": "dev"},
                            {"label": "prod", "value": "prod"},
                        ],
                        value="dev",
                    ),
                    html.Div(id="dimension-loader-action-panel"),
                    html.Div(
                        id="dimension-loader-progress",
                        className="govuk-!-margin-top-4 govuk-body govuk-!-font-weight-bold",
                    ),
                    html.Div(
                        id="dimension-loader-progress-detail",
                        className="govuk-!-margin-top-1 govuk-body",
                    ),
                    html.Div(
                        id="dimension-loader-results",
                        className="govuk-inset-text govuk-!-margin-top-4 govuk-body",
                    ),
                    html.Div(
                        id="dimension-loader-pipeline-status",
                        className="govuk-!-margin-top-1 govuk-body govuk-!-font-weight-bold",
                    ),
                    html.Div(
                        id="dimension-loader-pipeline-event",
                        className="govuk-!-margin-top-1 govuk-body govuk-!-font-family-monospace",
                    ),
                    html.Div(
                        id="dimension-loader-pipeline-details",
                        className="govuk-!-margin-top-1 govuk-body",
                    ),
                    html.Div(
                        id="dimension-loader-pipeline-link",
                        className="govuk-!-margin-top-1 govuk-body",
                    ),
                    html.Div(id="dimension-loader-status", className="govuk-!-margin-top-2 govuk-body"),
                    dcc.Store(id="dimension-loader-sync-state"),
                    dcc.Interval(
                        id="dimension-loader-sync-poll",
                        interval=500,
                        n_intervals=0,
                        disabled=True,
                    ),
                    html.Div(
                        className="govuk-!-margin-top-4",
                        children=[
                            dcc.Link(
                                "Back to services",
                                href="/services",
                                className="govuk-link",
                            )
                        ],
                    ),
                ],
            )
        ],
    )


def _build_action_panel(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_dimension_jobs_by_environment(config)
    jobs = grouped_jobs.get(selected_env, [])

    if config_error:
        return html.Div(
            className="govuk-inset-text govuk-!-margin-bottom-4",
            children=[html.P(config_error, className="govuk-body govuk-!-margin-bottom-0")],
        )
    if missing_env_vars:
        return html.Div(
            className="govuk-inset-text govuk-!-margin-bottom-4",
            children=[html.P(build_env_missing_message(missing_env_vars)[0], className="govuk-body govuk-!-margin-bottom-0")],
        )

    return html.Div(
        className="govuk-inset-text govuk-!-margin-bottom-4",
        children=[
            html.H3(f"{selected_env.upper()} environment", className="govuk-heading-s"),
            html.P(
                f"{len(jobs)} SharePoint-backed job(s) ready for this environment.",
                className="govuk-body govuk-!-margin-bottom-2",
            ),
            build_summary_list(
                _build_sharepoint_folder_rows(jobs),
                classes="govuk-!-margin-bottom-2",
            ),
            html.Div(
                className="govuk-button-group",
                children=[
                    html.Button(
                        "Refresh files",
                        type="button",
                        id="dimension-loader-refresh",
                        className="govuk-button govuk-button--secondary",
                    ),
                    html.Button(
                        "List files",
                        type="button",
                        id="dimension-loader-list",
                        className="govuk-button govuk-button--secondary",
                    ),
                    html.Button(
                        "Sync dimensions Fabric with SharePoint",
                        type="button",
                        id="dimension-loader-sync",
                        className="govuk-button",
                    ),
                ],
            ),
            html.P(
                "Refresh updates the job summary, List scans the SharePoint folders, and Sync uploads every file it finds into Fabric.",
                className="govuk-hint govuk-!-margin-bottom-0",
            ),
        ],
    )


def _build_dimension_loader_action_result(action_id: str, environment: str | None) -> str:
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_dimension_jobs_by_environment(config)
    jobs = grouped_jobs.get(selected_env, [])

    if config_error:
        return config_error
    if missing_env_vars:
        return (
            "Dimensions Loader is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )

    if action_id == "dimension-loader-refresh":
        return f"Refreshed {len(jobs)} job(s) for {selected_env.upper()} from the Dimensions Loader config."
    if action_id == "dimension-loader-list":
        if not jobs:
            return f"No SharePoint-backed jobs found for {selected_env.upper()}."
        return f"{selected_env.upper()} folder scan ready."
    return "No action selected."


def _build_list_results(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    missing_env_vars = get_missing_env_vars()
    jobs = get_dimension_jobs_by_environment(config).get(selected_env, [])

    if config_error:
        return html.Pre(config_error, className="govuk-body", style={"whiteSpace": "pre-wrap", "marginBottom": 0})
    if missing_env_vars:
        return html.Pre(
            "Dimensions Loader is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + ".",
            className="govuk-body",
            style={"whiteSpace": "pre-wrap", "marginBottom": 0},
        )
    if not jobs:
        return html.Pre(
            f"No SharePoint-backed jobs found for {selected_env.upper()}.",
            className="govuk-body",
            style={"whiteSpace": "pre-wrap", "marginBottom": 0},
        )

    lines: list[str] = []
    for job in jobs:
        job_name = str(job.get("name", "")).strip() or "Unnamed job"
        target_root = str(job.get("target_root", "")).strip().rstrip("/")
        lines.append(job_name)
        try:
            scanned_files = _scan_job_files(job)
        except Exception as exc:  # pylint:disable=broad-except
            lines.append(f"  Failed to scan SharePoint folders: {exc}")
            continue
        if not scanned_files:
            lines.append("  No files found.")
            continue
        lines.append(f"  {len(scanned_files)} file(s) found:")
        for item in scanned_files:
            source_relative_path = str(item.get("source_relative_path", "")).strip()
            target_relative_path = "/".join(part for part in [target_root, source_relative_path] if part)
            lines.append(f"  - {source_relative_path} -> {target_relative_path}")

    return html.Pre(
        "\n".join(lines),
        className="govuk-body",
        style={"whiteSpace": "pre-wrap", "marginBottom": 0},
    )


def _trigger_dimension_pipeline(uploader, config: dict[str, object], selected_env: str) -> tuple[str, str, str, str, str]:
    pipeline = get_dimension_pipeline_config(config, selected_env)
    pipeline_workspace_name = str(pipeline.get("workspace_display_name", "")).strip()
    pipeline_display_name = str(pipeline.get("pipeline_display_name", "")).strip()
    parameters = dict(pipeline.get("parameters", {}) or {})
    if not pipeline_workspace_name or not pipeline_display_name:
        return "", "", "", "", ""

    common = uploader._common()
    auth = uploader._online_auth()
    workspaces_ok, workspaces_payload = auth.list_fabric_workspaces()
    common.require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    workspace = _find_by_display_name(list(workspaces_payload or []), pipeline_workspace_name, "Workspace")
    workspace_id = str(workspace.get("id", "")).strip()
    if not workspace_id:
        raise RuntimeError(f"Workspace '{pipeline_workspace_name}' does not have an id.")

    pipelines_ok, pipelines_payload = auth.list_fabric_pipelines(workspace_id)
    common.require_ok("list_fabric_pipelines", pipelines_ok, pipelines_payload)
    pipeline_item = _find_by_display_name(list(pipelines_payload or []), pipeline_display_name, "Pipeline")
    pipeline_id = str(pipeline_item.get("id", "")).strip()
    if not pipeline_id:
        raise RuntimeError(f"Pipeline '{pipeline_display_name}' does not have an id.")

    previous_run_id = _resolve_latest_pipeline_run_id(auth, workspace_id, pipeline_id)
    trigger_payload = dict(parameters)
    trigger_payload.setdefault("environment", selected_env)
    trigger_ok, trigger_response = auth.trigger_fabric_pipeline(
        workspace_id,
        pipeline_id,
        parameters=trigger_payload,
    )
    common.require_ok("trigger_fabric_pipeline", trigger_ok, trigger_response)
    pipeline_run_id = str(
        trigger_response.get("id")
        or trigger_response.get("jobInstanceId")
        or trigger_response.get("jobId")
        or trigger_response.get("runId")
        or ""
    ).strip()
    if not pipeline_run_id:
        pipeline_run_id = _resolve_new_pipeline_run_id(
            auth,
            workspace_id,
            pipeline_id,
            previous_run_id=previous_run_id,
        )

    return workspace_id, pipeline_id, pipeline_run_id, pipeline_workspace_name, pipeline_display_name


def _dimension_loader_sync_worker(
    run_id: str,
    uploader,
    config: dict[str, object],
    selected_env: str,
    jobs: list[dict[str, object]],
    total_mappings: int,
) -> None:
    try:
        _debug(f"Starting worker run_id={run_id} env={selected_env} jobs={len(jobs)} total_files={total_mappings}")
        auth = uploader._online_auth()
        auth._ensure_authenticated(auth.SHAREPOINT_SCOPE)
        auth._ensure_authenticated(auth.FABRIC_SCOPE)
        _set_sync_state(
            run_id,
            running=True,
            status=f"Syncing {selected_env.upper()}...",
            current_job="",
            current_file="",
            processed_mappings=0,
            total_mappings=total_mappings,
        )

        processed_mappings = 0
        successes = 0
        failures = 0
        pipeline_failures = 0
        last_job_name = ""
        last_file_name = ""
        for job in jobs:
            job_name = str(job.get("name", "")).strip() or "Unnamed job"
            last_job_name = job_name
            _debug(f"Resolving destination for job={job_name}")
            _set_sync_state(
                run_id,
                current_job=job_name,
                status=f"Working on {job_name}",
            )
            scanned_files = list(job.get("_scanned_files", []) or [])
            try:
                workspace_id, lakehouse_id = uploader._resolve_fabric_destination(job)
            except Exception as exc:  # pylint:disable=broad-except
                _debug(f"Failed to resolve destination for {job_name}: {exc}")
                failures += len(scanned_files) or 1
                continue

            for file_item in scanned_files:
                source_path = str(file_item.get("source_relative_path", "")).strip()
                last_file_name = source_path
                _debug(f"Uploading {source_path} for job={job_name}")
                try:
                    mapping = {
                        "source_relative_path": source_path,
                        "target_relative_path": str(file_item.get("target_relative_path", "")).strip() or source_path,
                        "source_link": str(file_item.get("source_link", "")).strip(),
                    }
                    uploader._upload_mapping(  # noqa: SLF001
                        job,
                        mapping,
                        workspace_id,
                        lakehouse_id,
                        config,
                    )
                    successes += 1
                except Exception as exc:  # pylint:disable=broad-except
                    _debug(f"Upload failed for {source_path}: {exc}")
                    failures += 1
                finally:
                    processed_mappings += 1
                    _set_sync_state(
                        run_id,
                        processed_mappings=processed_mappings,
                        total_mappings=total_mappings,
                        current_file=source_path,
                        status=f"Processing {processed_mappings}/{total_mappings}",
                    )

        pipeline_workspace_id = ""
        pipeline_id = ""
        pipeline_run_id = ""
        pipeline_config = get_dimension_pipeline_config(config, selected_env)
        if failures == 0 and (
            str(pipeline_config.get("workspace_display_name", "")).strip()
            and str(pipeline_config.get("pipeline_display_name", "")).strip()
        ):
            try:
                _debug(f"Triggering pipeline for env={selected_env}")
                _set_sync_state(
                    run_id,
                    current_job=last_job_name,
                    current_file=last_file_name,
                    pipeline_event=f"Triggering pipeline for {selected_env.upper()}",
                    status=f"Triggering pipeline for {selected_env.upper()}",
                )
                pipeline_workspace_id, pipeline_id, pipeline_run_id, _, _ = _trigger_dimension_pipeline(
                    uploader,
                    config,
                    selected_env,
                )
                _debug(
                    f"Triggered pipeline for env={selected_env}"
                    + (f" run_id={pipeline_run_id}" if pipeline_run_id else "")
                )
                _set_sync_state(
                    run_id,
                    pipeline_workspace_id=pipeline_workspace_id,
                    pipeline_id=pipeline_id,
                    pipeline_run_id=pipeline_run_id,
                    pipeline_status="Pipeline: queued" if pipeline_run_id else "",
                    pipeline_event=(
                        f"Triggered pipeline for {selected_env.upper()}"
                        + (f" run_id={pipeline_run_id}" if pipeline_run_id else "")
                    ),
                    status=(
                        f"Triggered pipeline for {selected_env.upper()}"
                        + (f" run_id={pipeline_run_id}" if pipeline_run_id else "")
                    ),
                )
                refreshed_state = _refresh_pipeline_status(_get_sync_state(run_id) or {})
                _set_sync_state(run_id, **{key: value for key, value in refreshed_state.items() if key != "run_id"})
            except Exception as exc:  # pylint:disable=broad-except
                pipeline_failures += 1
                _debug(f"Pipeline trigger failed for env={selected_env}: {exc}")
                _set_sync_state(
                    run_id,
                    pipeline_status="",
                    pipeline_event=f"Pipeline trigger failed for {selected_env.upper()}: {exc}",
                    pipeline_run_id="",
                    pipeline_workspace_id="",
                    pipeline_id="",
                    status=f"Pipeline trigger failed for {selected_env.upper()}: {exc}",
                )
        elif failures > 0:
            _set_sync_state(
                run_id,
                pipeline_status="",
                pipeline_event=f"Pipeline skipped for {selected_env.upper()} because {failures} file(s) failed.",
            )

        _set_sync_state(
            run_id,
            running=False,
            done=True,
            current_job=last_job_name,
            current_file=last_file_name,
            processed_mappings=processed_mappings,
            total_mappings=total_mappings,
            status=(
                f"Sync complete for {selected_env.upper()}: "
                f"{successes} successful file(s), {failures} failed file(s), "
                f"{pipeline_failures} pipeline failure(s)."
            ),
        )
    except Exception as exc:  # pylint:disable=broad-except
        _debug(f"Worker failed run_id={run_id}: {exc}")
        _set_sync_state(
            run_id,
            running=False,
            done=True,
            error=str(exc),
            status=f"Sync failed for {selected_env.upper()}: {exc}",
        )


def _start_sync_run(uploader, config: dict[str, object], selected_env: str, jobs: list[dict[str, object]]):
    prepared_jobs, total_files = _prepare_scanned_jobs(jobs)
    state = _create_sync_state(selected_env, total_files)
    run_id = str(state["run_id"])
    _SYNC_RUNS[run_id] = state
    thread = threading.Thread(
        target=_dimension_loader_sync_worker,
        args=(run_id, uploader, config, selected_env, prepared_jobs, total_files),
        daemon=True,
    )
    thread.start()
    return (
        f"Sync started for {selected_env.upper()}.",
        "0/{}".format(total_files) if total_files else "0/0",
        _build_progress_detail(state),
        "",
        "",
        "",
        "",
        state,
        False,
    )


def _sync_dimension_jobs(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    _debug(f"Sync requested env={selected_env}")
    if config_error:
        return config_error, "", "", "", "", "", "", {"environment": selected_env, "running": False, "done": True}, True

    missing_env_vars = get_missing_env_vars()
    if missing_env_vars:
        message = (
            "Dimensions Loader is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )
        return message, "", "", "", "", "", "", {"environment": selected_env, "running": False, "done": True}, True

    jobs = [
        job
        for job in get_dimension_jobs(config, selected_env)
        if bool(job.get("enabled", True))
    ]
    if not jobs:
        return (
            f"No enabled SharePoint dimension jobs found for {selected_env.upper()}.",
            "0/0",
            f"{selected_env.upper()} | Waiting to start",
            "",
            "",
            "",
            "",
            {"environment": selected_env, "running": False, "done": True, "processed_mappings": 0, "total_mappings": 0},
            True,
        )

    from services.fabric_uploader_cli import app as uploader

    return _start_sync_run(uploader, config, selected_env, jobs)


def register_dimension_loader_callbacks(app):
    @app.callback(
        Output("dimension-loader-action-panel", "children"),
        Input("dimension-loader-environment", "value"),
    )
    def update_dimension_loader_action_panel(environment):
        return _build_action_panel(environment)

    @app.callback(
        Output("dimension-loader-status", "children"),
        Output("dimension-loader-progress", "children"),
        Output("dimension-loader-progress-detail", "children"),
        Output("dimension-loader-results", "children"),
        Output("dimension-loader-pipeline-status", "children"),
        Output("dimension-loader-pipeline-event", "children"),
        Output("dimension-loader-pipeline-details", "children"),
        Output("dimension-loader-pipeline-link", "children"),
        Output("dimension-loader-sync-state", "data"),
        Output("dimension-loader-sync-poll", "disabled"),
        Input("dimension-loader-refresh", "n_clicks"),
        Input("dimension-loader-list", "n_clicks"),
        Input("dimension-loader-sync", "n_clicks"),
        Input("dimension-loader-environment", "value"),
        prevent_initial_call=True,
    )
    def handle_dimension_loader_actions(refresh_clicks, list_clicks, sync_clicks, environment):
        trigger = callback_context.triggered[0] if callback_context.triggered else None
        if not trigger:
            raise PreventUpdate

        component_id = str(trigger.get("prop_id", "")).split(".", 1)[0]
        _debug(f"Callback action trigger={component_id} environment={environment}")
        if component_id == "dimension-loader-refresh":
            status = _build_dimension_loader_action_result(component_id, environment)
            return status, "", "", no_update, "", "", "", "", None, True
        if component_id == "dimension-loader-list":
            status = _build_dimension_loader_action_result(component_id, environment)
            return status, "", "", _build_list_results(environment), "", "", "", "", None, True
        if component_id == "dimension-loader-sync":
            status, progress, detail, pipeline_status, pipeline_event, pipeline_details, pipeline_link, state, disabled = _sync_dimension_jobs(environment)
            detail = _build_progress_detail(state)
            return status, progress, detail, no_update, pipeline_status, pipeline_event, pipeline_details, pipeline_link, state, disabled
        if component_id == "dimension-loader-environment":
            raise PreventUpdate
        raise PreventUpdate

    @app.callback(
        Output("dimension-loader-progress", "children", allow_duplicate=True),
        Output("dimension-loader-progress-detail", "children", allow_duplicate=True),
        Output("dimension-loader-status", "children", allow_duplicate=True),
        Output("dimension-loader-results", "children", allow_duplicate=True),
        Output("dimension-loader-pipeline-status", "children", allow_duplicate=True),
        Output("dimension-loader-pipeline-event", "children", allow_duplicate=True),
        Output("dimension-loader-pipeline-details", "children", allow_duplicate=True),
        Output("dimension-loader-pipeline-link", "children", allow_duplicate=True),
        Output("dimension-loader-sync-state", "data", allow_duplicate=True),
        Output("dimension-loader-sync-poll", "disabled", allow_duplicate=True),
        Input("dimension-loader-sync-poll", "n_intervals"),
        State("dimension-loader-sync-state", "data"),
        prevent_initial_call=True,
    )
    def poll_dimension_loader_sync(_intervals, sync_state):
        state = _get_sync_state(str((sync_state or {}).get("run_id", "")))
        if not state:
            raise PreventUpdate
        if str(state.get("pipeline_run_id", "")).strip():
            state = _refresh_pipeline_status(state)
            run_id = str(state.get("run_id", ""))
            _set_sync_state(run_id, **{key: value for key, value in state.items() if key != "run_id"})
        progress = _build_progress_text(state)
        detail = _build_progress_detail(state)
        status = str(state.get("status", "")).strip()
        pipeline_status = _build_pipeline_status_text(state)
        pipeline_event = _build_pipeline_event_text(state)
        pipeline_details = _build_pipeline_details_text(state)
        pipeline_link = _build_pipeline_link_component(state)
        disabled = not bool(state.get("running", False)) and not _pipeline_needs_polling(state)
        return progress, detail, status, no_update, pipeline_status, pipeline_event, pipeline_details, pipeline_link, state, disabled
