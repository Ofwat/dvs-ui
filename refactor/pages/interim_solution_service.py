from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from dash import Input, Output, State, callback_context, dcc, html, no_update
from dash.exceptions import PreventUpdate

from .interim_solution_state import (
    get_interim_jobs,
    get_interim_jobs_by_environment,
    get_interim_pipeline_config,
    get_missing_env_vars,
    load_service_config,
)


_SYNC_RUNS: dict[str, dict[str, object]] = {}
_SYNC_RUNS_LOCK = threading.Lock()


INTERIM_SOLUTION_SERVICE = {
    "label": "Interim Solution",
    "path": "/services/interim-solution",
    "summary": "Scan a SharePoint folder and upload every file into Fabric.",
    "description": (
        "Interim Solution scans one SharePoint folder per job at runtime. "
        "The config only stores the folder URL and the Fabric destination, not per-file mappings."
    ),
}


def _debug(message: str) -> None:
    print(f"[InterimSolution] {message}", flush=True)


def _find_by_display_name(items: list[dict[str, object]], display_name: str, label: str) -> dict[str, object]:
    matches = [item for item in items if str(item.get("displayName", "")).strip() == display_name]
    if not matches:
        raise RuntimeError(f"{label} with displayName '{display_name}' was not found.")
    if len(matches) > 1:
        raise RuntimeError(f"{label} with displayName '{display_name}' is not unique.")
    return matches[0]


def _build_run_folder_name() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


def _parse_fabric_timestamp(timestamp_text: str | None) -> datetime | None:
    if not timestamp_text:
        return None
    normalized = str(timestamp_text).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_fabric_timestamp(timestamp_text: str | None) -> str:
    parsed = _parse_fabric_timestamp(timestamp_text)
    if parsed is None:
        return str(timestamp_text or "").strip()
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_duration_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _create_sync_state(environment: str | None, total_files: int = 0) -> dict[str, object]:
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    return {
        "run_id": uuid.uuid4().hex,
        "environment": selected_env,
        "running": False,
        "done": False,
        "status": "",
        "current_job": "",
        "current_file": "",
        "current_transfer": "",
        "pipeline_workspace_id": "",
        "pipeline_id": "",
        "pipeline_run_id": "",
        "pipeline_status": "",
        "pipeline_event": "",
        "pipeline_start_time": "",
        "pipeline_end_time": "",
        "pipeline_failure_reason": "",
        "processed_files": 0,
        "total_files": total_files,
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
    processed = int(state.get("processed_files", 0) or 0)
    total = int(state.get("total_files", 0) or 0)
    if total <= 0:
        return "0/0"
    return f"{processed}/{total}"


def _build_progress_detail(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    environment = str(state.get("environment", "dev")).upper()
    current_job = str(state.get("current_job", "")).strip()
    current_file = str(state.get("current_file", "")).strip()
    current_job_folder = str(state.get("current_job_folder", "")).strip()
    status = str(state.get("status", "")).strip()
    if current_job and current_file and current_job_folder:
        return f"{environment} | {current_job} | {current_file} | {current_job_folder}"
    if current_job and current_file:
        return f"{environment} | {current_job} | {current_file}"
    if current_file and current_job_folder:
        return f"{environment} | {current_file} | {current_job_folder}"
    if current_file:
        return f"{environment} | {current_file}"
    if current_job:
        return f"{environment} | {current_job}"
    if current_job_folder:
        return f"{environment} | {current_job_folder}"
    if status:
        return f"{environment} | {status}"
    return f"{environment} | Waiting to start"


def _build_transfer_text(state: dict[str, object] | None) -> str:
    if not state:
        return ""
    current_transfer = str(state.get("current_transfer", "")).strip()
    if current_transfer:
        return current_transfer
    return "Waiting for transfer progress..."


def _build_sharepoint_folder_link(folder_url: str) -> html.A | str:
    folder_url = str(folder_url or "").strip()
    if not folder_url:
        return ""
    return html.A("Open SharePoint folder", href=folder_url, target="_blank", rel="noopener noreferrer")


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
    if pipeline_event:
        return pipeline_event
    return ""


def _build_pipeline_details_text(state: dict[str, object] | None) -> str:
    if not state:
        return ""

    pipeline_run_id = str(state.get("pipeline_run_id", "")).strip()
    pipeline_status = _build_pipeline_status_text(state)
    pipeline_start_time = str(state.get("pipeline_start_time", "")).strip()
    pipeline_end_time = str(state.get("pipeline_end_time", "")).strip()
    pipeline_failure_reason = str(state.get("pipeline_failure_reason", "")).strip()

    details: list[str] = []
    start_dt = _parse_fabric_timestamp(pipeline_start_time)
    end_dt = _parse_fabric_timestamp(pipeline_end_time)

    if pipeline_start_time:
        details.append(f"Started: {_format_fabric_timestamp(pipeline_start_time)}")
    if start_dt is not None and end_dt is not None:
        duration_seconds = max((end_dt - start_dt).total_seconds(), 0.0)
        details.append(f"Duration: {_format_duration_seconds(duration_seconds)}")
    elif start_dt is not None and pipeline_run_id and _pipeline_is_active(pipeline_status.removeprefix("Pipeline: ")):
        elapsed_seconds = max((datetime.now(timezone.utc) - start_dt).total_seconds(), 0.0)
        details.append(f"Elapsed: {_format_duration_seconds(elapsed_seconds)}")
    if pipeline_end_time:
        details.append(f"Ended: {_format_fabric_timestamp(pipeline_end_time)}")
    if pipeline_failure_reason:
        details.append(f"Failure reason: {pipeline_failure_reason}")

    if details:
        return "Pipeline details: " + " | ".join(details)
    if pipeline_run_id:
        return "Pipeline details: waiting for Fabric timestamps"
    return ""


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


def _pipeline_is_active(status_text: str) -> bool:
    return status_text.strip().lower() in {"notstarted", "queued", "inprogress", "running"}


def _pipeline_is_terminal(state: dict[str, object] | None) -> bool:
    if not state:
        return False
    status_text = _build_pipeline_status_text(state).removeprefix("Pipeline: ").strip().lower()
    return status_text in {"succeeded", "completed", "failed", "error", "cancelled", "canceled", "partially_succeeded"}


def _pipeline_needs_polling(state: dict[str, object] | None) -> bool:
    if not state:
        return False
    pipeline_run_id = str(state.get("pipeline_run_id", "")).strip()
    if not pipeline_run_id:
        return False
    return not _pipeline_is_terminal(state)


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


def _resolve_latest_pipeline_run_id(auth, workspace_id: str, pipeline_id: str) -> str:
    success, payload = auth.list_fabric_pipeline_runs(workspace_id, pipeline_id)
    if not success or not isinstance(payload, list) or not payload:
        return ""
    latest = payload[0]
    candidate = (
        latest.get("id")
        or latest.get("jobInstanceId")
        or latest.get("jobId")
        or latest.get("runId")
        or ""
    )
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


def build_interim_solution_content():
    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full",
                children=[
                    html.H2(INTERIM_SOLUTION_SERVICE["label"], className="govuk-heading-l"),
                    html.P(INTERIM_SOLUTION_SERVICE["summary"], className="govuk-body"),
                    html.P(
                        INTERIM_SOLUTION_SERVICE["description"],
                        className="govuk-body govuk-!-margin-bottom-4",
                    ),
                    html.H3("Choose environment", className="govuk-heading-m"),
                    html.Div(
                        className="govuk-radios govuk-radios--inline govuk-!-margin-bottom-6",
                        children=[
                            html.Div(
                                className="govuk-radios__item",
                                children=[
                                    dcc.RadioItems(
                                        id="interim-solution-environment",
                                        options=[
                                            {"label": "dev", "value": "dev"},
                                            {"label": "prod", "value": "prod"},
                                        ],
                                        value="dev",
                                        inline=True,
                                    )
                                ],
                            )
                        ],
                    ),
                    html.Div(id="interim-solution-action-panel"),
                    html.Div(
                        id="interim-solution-progress",
                        className="govuk-!-margin-top-4 govuk-body govuk-!-font-weight-bold",
                    ),
                    html.Div(
                        id="interim-solution-progress-detail",
                        className="govuk-!-margin-top-1 govuk-body",
                    ),
                    html.Div(
                        id="interim-solution-transfer",
                        className="govuk-!-margin-top-1 govuk-body govuk-!-font-family-monospace",
                    ),
                    html.Div(
                        id="interim-solution-pipeline-status",
                        className="govuk-!-margin-top-1 govuk-body govuk-!-font-weight-bold",
                    ),
                    html.Div(
                        id="interim-solution-pipeline-event",
                        className="govuk-!-margin-top-1 govuk-body govuk-!-font-family-monospace",
                    ),
                    html.Div(
                        id="interim-solution-pipeline-details",
                        className="govuk-!-margin-top-1 govuk-body",
                    ),
                    html.Div(
                        id="interim-solution-pipeline-link",
                        className="govuk-!-margin-top-1 govuk-body",
                    ),
                    html.Div(
                        id="interim-solution-results",
                        className="govuk-inset-text govuk-!-margin-top-4 govuk-body",
                    ),
                    html.Div(id="interim-solution-status", className="govuk-!-margin-top-2 govuk-body"),
                    dcc.Store(id="interim-solution-sync-state"),
                    dcc.Interval(
                        id="interim-solution-sync-poll",
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
    grouped_jobs = get_interim_jobs_by_environment(config)
    jobs = grouped_jobs.get(selected_env, [])

    if config_error:
        return html.Div(
            className="govuk-inset-text govuk-!-margin-bottom-4",
            children=[html.P(config_error, className="govuk-body govuk-!-margin-bottom-0")],
        )
    if missing_env_vars:
        return html.Div(
            className="govuk-inset-text govuk-!-margin-bottom-4",
            children=[
                html.P(
                    "Interim Solution is not configured yet. Missing environment values: "
                    + ", ".join(missing_env_vars)
                    + ".",
                    className="govuk-body govuk-!-margin-bottom-0",
                )
            ],
        )

    return html.Div(
        className="govuk-inset-text govuk-!-margin-bottom-4",
        children=[
            html.H3(f"{selected_env.upper()} environment", className="govuk-heading-s"),
            html.P(
                f"{len(jobs)} SharePoint folder job(s) ready for this environment.",
                className="govuk-body govuk-!-margin-bottom-2",
            ),
            html.Div(
                className="govuk-button-group",
                children=[
                    html.Button(
                        "Refresh files",
                        type="button",
                        id="interim-solution-refresh",
                        className="govuk-button govuk-button--secondary",
                    ),
                    html.Button(
                        "List files",
                        type="button",
                        id="interim-solution-list",
                        className="govuk-button govuk-button--secondary",
                    ),
                    html.Button(
                        "Sync dimensions Fabric with SharePoint",
                        type="button",
                        id="interim-solution-sync",
                        className="govuk-button",
                    ),
                ],
            ),
            html.Ul(
                className="govuk-list govuk-list--spaced govuk-!-margin-bottom-2",
                children=[
                    html.Li(
                        children=[
                            html.Span(f"{str(job.get('name', '')).strip() or 'Unnamed job'}", className="govuk-!-font-weight-bold"),
                            html.Span(" | ", className="govuk-hint"),
                            _build_sharepoint_folder_link(job.get("source_folder_url", "")),
                        ]
                    )
                    for job in jobs
                ],
            ),
            html.P(
                "Refresh updates the job summary, List shows the configured folders, and Sync uploads every file in the SharePoint folder before triggering any configured pipeline.",
                className="govuk-hint govuk-!-margin-bottom-0",
            ),
        ],
    )


def _build_action_result(action_id: str, environment: str | None) -> str:
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_interim_jobs_by_environment(config)
    jobs = grouped_jobs.get(selected_env, [])

    if config_error:
        return config_error
    if missing_env_vars:
        return (
            "Interim Solution is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )

    if action_id == "interim-solution-refresh":
        return f"Refreshed {len(jobs)} job(s) for {selected_env.upper()} from the Interim Solution config."
    if action_id == "interim-solution-list":
        if not jobs:
            return f"No SharePoint folder jobs found for {selected_env.upper()}."
        return f"{selected_env.upper()} folder scan ready."
    return "No action selected."


def _build_list_results(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_interim_jobs_by_environment(config)
    jobs = grouped_jobs.get(selected_env, [])

    if config_error:
        return html.Pre(config_error, className="govuk-body", style={"whiteSpace": "pre-wrap", "marginBottom": 0})
    if missing_env_vars:
        return html.Pre(
            "Interim Solution is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + ".",
            className="govuk-body",
            style={"whiteSpace": "pre-wrap", "marginBottom": 0},
        )
    if not jobs:
        return html.Pre(
            f"No SharePoint folder jobs found for {selected_env.upper()}.",
            className="govuk-body",
            style={"whiteSpace": "pre-wrap", "marginBottom": 0},
        )

    lines: list[str] = []
    for job in jobs:
        job_name = str(job.get("name", "")).strip() or "Unnamed job"
        folder_url = str(job.get("source_folder_url", "")).strip()
        lines.append(f"{job_name}")
        try:
            files = _scan_sharepoint_folder(folder_url)
        except Exception as exc:  # pylint:disable=broad-except
            lines.append(f"  Failed to scan SharePoint folder: {exc}")
            continue
        if not files:
            lines.append("  No files found.")
            continue
        target_root = str(job.get("target_root", "")).strip().rstrip("/")
        lines.append(f"  {len(files)} file(s) found:")
        for item in files:
            source_relative_path = str(item.get("relativePath") or item.get("name") or "").strip()
            if not source_relative_path:
                continue
            target_relative_path = "/".join(part for part in [target_root, source_relative_path] if part)
            lines.append(f"  - {source_relative_path} -> {target_relative_path}")

    return html.Pre(
        "\n".join(lines),
        className="govuk-body",
        style={"whiteSpace": "pre-wrap", "marginBottom": 0},
    )


def _scan_sharepoint_folder(folder_url: str) -> list[dict[str, object]]:
    from refactor.services.data_validation_api.workflows import resolve_sharepoint_folder_listing

    _context, items = resolve_sharepoint_folder_listing(folder_url)
    return [item for item in items if not item.get("isFolder")]


def _trigger_configured_pipeline(uploader, job: dict[str, object]) -> str | None:
    pipeline = get_interim_pipeline_config(job)
    pipeline_workspace_name = str(pipeline.get("workspace_display_name", "")).strip()
    pipeline_display_name = str(pipeline.get("pipeline_display_name", "")).strip()
    if not pipeline_workspace_name or not pipeline_display_name:
        return None

    common = uploader._common()
    auth = uploader._online_auth()
    workspaces_ok, workspaces_payload = auth.list_fabric_workspaces()
    common.require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    pipeline_workspace = _find_by_display_name(list(workspaces_payload), pipeline_workspace_name, "Pipeline workspace")
    pipeline_workspace_id = str(pipeline_workspace["id"])

    pipelines_ok, pipelines_payload = auth.list_fabric_pipelines(pipeline_workspace_id)
    common.require_ok("list_fabric_pipelines", pipelines_ok, pipelines_payload)
    pipeline_item = _find_by_display_name(list(pipelines_payload), pipeline_display_name, "Pipeline")
    pipeline_id = str(pipeline_item["id"])

    previous_run_id = _resolve_latest_pipeline_run_id(auth, pipeline_workspace_id, pipeline_id)
    raw_parameters = pipeline.get("parameters", {})
    parameters = dict(raw_parameters) if isinstance(raw_parameters, dict) else {}
    input_folder_path = str(job.get("_run_folder_path", "")).strip()
    if input_folder_path:
        parameters["input_folder_path"] = input_folder_path
    parameters = parameters or None
    trigger_ok, trigger_payload = auth.trigger_fabric_pipeline(pipeline_workspace_id, pipeline_id, parameters=parameters)
    common.require_ok("trigger_fabric_pipeline", trigger_ok, trigger_payload)
    raw_run_id = (
        trigger_payload.get("id")
        or trigger_payload.get("jobInstanceId")
        or trigger_payload.get("jobId")
        or trigger_payload.get("runId")
    )
    pipeline_run_id = str(raw_run_id).strip() if raw_run_id is not None else ""
    if pipeline_run_id.lower() == "none":
        pipeline_run_id = ""
    if not pipeline_run_id:
        pipeline_run_id = _resolve_new_pipeline_run_id(
            auth,
            pipeline_workspace_id,
            pipeline_id,
            previous_run_id=previous_run_id,
        )
    job["_pipeline_workspace_id"] = pipeline_workspace_id
    job["_pipeline_id"] = pipeline_id
    job["_pipeline_run_id"] = pipeline_run_id
    return pipeline_run_id or None


def _interim_sync_worker(
    run_id: str,
    uploader,
    config: dict[str, object],
    selected_env: str,
    prepared_jobs: list[dict[str, object]],
    total_files: int,
) -> None:
    try:
        run_folder_name = str(config.get("_run_folder_name", "")).strip() or _build_run_folder_name()
        _debug(
            f"Starting worker run_id={run_id} env={selected_env} "
            f"jobs={len(prepared_jobs)} total_files={total_files} run_folder={run_folder_name}"
        )
        auth = uploader._online_auth()
        auth._ensure_authenticated(auth.SHAREPOINT_SCOPE)
        auth._ensure_authenticated(auth.FABRIC_SCOPE)
        _set_sync_state(
            run_id,
            running=True,
            status=f"Syncing {selected_env.upper()}...",
            current_job="",
            current_file="",
            current_transfer="",
            current_job_folder=run_folder_name,
            processed_files=0,
            total_files=total_files,
        )

        processed_files = 0
        successes = 0
        failures = 0
        pipeline_failures = 0
        last_job_name = ""
        last_file_name = ""
        for job_entry in prepared_jobs:
            job = dict(job_entry["job"])
            files = list(job_entry["files"])
            job_name = str(job.get("name", "")).strip() or "Unnamed job"
            folder_url = str(job.get("source_folder_url", "")).strip()
            job_failures = 0
            last_job_name = job_name
            _debug(f"Scanning folder for job={job_name}")
            _set_sync_state(
                run_id,
                current_job=job_name,
                status=f"Scanning {job_name}",
            )
            try:
                workspace_id, lakehouse_id = uploader._resolve_fabric_destination(job)
            except Exception as exc:  # pylint:disable=broad-except
                _debug(f"Failed to resolve destination for {job_name}: {exc}")
                failures += 1
                continue

            if not files:
                _debug(f"No files found for {job_name}")
                continue

            target_root = str(job.get("target_root", "")).strip().rstrip("/")
            for item in files:
                source_relative_path = str(item.get("relativePath") or item.get("name") or "").strip()
                if not source_relative_path:
                    continue
                last_file_name = source_relative_path
                target_relative_path = "/".join(
                    part for part in [target_root, run_folder_name, source_relative_path] if part
                )
                _debug(f"Uploading {source_relative_path} for job={job_name}")
                try:
                    file_bytes = uploader._download_sharepoint_bytes_with_progress(  # noqa: SLF001
                        folder_url,
                        source_relative_path,
                        progress_callback=lambda line, run_id=run_id: _set_sync_state(
                            run_id,
                            current_transfer=str(line),
                        ),
                    )
                    uploader._upload_bytes_with_progress(  # noqa: SLF001
                        workspace_id,
                        lakehouse_id,
                        target_relative_path,
                        file_bytes,
                        progress_callback=lambda line, run_id=run_id: _set_sync_state(
                            run_id,
                            current_transfer=str(line),
                        ),
                    )
                    successes += 1
                except Exception as exc:  # pylint:disable=broad-except
                    _debug(f"Upload failed for {source_relative_path}: {exc}")
                    failures += 1
                    job_failures += 1
                finally:
                    processed_files += 1
                    _set_sync_state(
                        run_id,
                        processed_files=processed_files,
                        total_files=total_files,
                        current_file=source_relative_path,
                        current_transfer=f"Uploading {target_relative_path}",
                        status=f"Processing {processed_files}/{total_files}",
                    )

            if not files:
                continue

            pipeline = get_interim_pipeline_config(job)
            if not str(pipeline.get("workspace_display_name", "")).strip() or not str(
                pipeline.get("pipeline_display_name", "")
            ).strip():
                continue
            if job_failures:
                _debug(f"Skipping pipeline trigger for {job_name} because {job_failures} file(s) failed")
                _set_sync_state(
                    run_id,
                    current_job=job_name,
                    current_transfer="",
                    status=f"Skipping pipeline trigger for {job_name} because {job_failures} file(s) failed.",
                )
                continue

            _debug(f"Triggering pipeline for job={job_name}")
            _set_sync_state(
                run_id,
                current_job=job_name,
                current_transfer="",
                pipeline_event=f"Triggering pipeline for job={job_name}",
                status=f"Triggering pipeline for {job_name}",
            )
            try:
                pipeline_run_id = _trigger_configured_pipeline(uploader, job)
                pipeline_workspace_id = str(job.get("_pipeline_workspace_id", "")).strip()
                pipeline_id = str(job.get("_pipeline_id", "")).strip()
                _debug(
                    f"Triggered pipeline for job={job_name}"
                    + (f" run_id={pipeline_run_id}" if pipeline_run_id else "")
                )
                _set_sync_state(
                    run_id,
                    current_job=job_name,
                    current_transfer="",
                    pipeline_workspace_id=pipeline_workspace_id,
                    pipeline_id=pipeline_id,
                    pipeline_run_id=pipeline_run_id or "",
                    pipeline_status="Pipeline: queued" if pipeline_run_id else "",
                    pipeline_event=f"Triggered pipeline for job={job_name}"
                    + (f" run_id={pipeline_run_id}" if pipeline_run_id else ""),
                    status=(
                        f"Triggered pipeline for {job_name}"
                        + (f" run_id={pipeline_run_id}" if pipeline_run_id else "")
                    ),
                )
                refreshed_state = _refresh_pipeline_status(_get_sync_state(run_id) or {})
                _set_sync_state(
                    run_id,
                    **{key: value for key, value in refreshed_state.items() if key != "run_id"},
                )
            except Exception as exc:  # pylint:disable=broad-except
                pipeline_failures += 1
                _debug(f"Pipeline trigger failed for {job_name}: {exc}")
                _set_sync_state(
                    run_id,
                    current_job=job_name,
                    current_transfer="",
                    pipeline_status="",
                    pipeline_event=f"Pipeline trigger failed for job={job_name}: {exc}",
                    pipeline_run_id="",
                    pipeline_workspace_id="",
                    pipeline_id="",
                    status=f"Pipeline trigger failed for {job_name}: {exc}",
                )

        _set_sync_state(
            run_id,
            running=False,
            done=True,
            current_job=last_job_name,
            current_file=last_file_name,
            current_transfer="",
            pipeline_status=str(_get_sync_state(run_id).get("pipeline_status", "")) if _get_sync_state(run_id) else "",
            pipeline_event=str(_get_sync_state(run_id).get("pipeline_event", "")) if _get_sync_state(run_id) else "",
            current_job_folder=run_folder_name,
            processed_files=processed_files,
            total_files=total_files,
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
    prepared_jobs: list[dict[str, object]] = []
    total_files = 0
    for job in jobs:
        folder_url = str(job.get("source_folder_url", "")).strip()
        try:
            files = _scan_sharepoint_folder(folder_url)
        except Exception:
            files = []
        prepared_jobs.append({"job": job, "files": files})
        total_files += len(files)
    run_folder_name = _build_run_folder_name()
    state = _create_sync_state(selected_env, total_files)
    run_id = str(state["run_id"])
    state["current_job_folder"] = run_folder_name
    _SYNC_RUNS[run_id] = dict(state)
    prepared_jobs = [
        {
            **entry,
            "job": {
                **entry["job"],
                "_run_folder_path": "/".join(
                    part
                    for part in [
                        str(entry["job"].get("target_root", "")).strip().rstrip("/"),
                        run_folder_name,
                    ]
                    if part
                ),
            },
        }
        for entry in prepared_jobs
    ]
    thread = threading.Thread(
        target=_interim_sync_worker,
        args=(run_id, uploader, {**config, "_run_folder_name": run_folder_name}, selected_env, prepared_jobs, total_files),
        daemon=True,
    )
    thread.start()
    return (
        f"Sync started for {selected_env.upper()}.",
        "0/{}".format(total_files) if total_files else "0/0",
        _build_progress_detail(state),
        _build_transfer_text(state),
        _build_pipeline_status_text(state),
        _build_pipeline_event_text(state),
        _build_pipeline_details_text(state),
        "",
        state,
        False,
    )


def _sync_dimension_jobs(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    _debug(f"Sync requested env={selected_env}")
    if config_error:
        return config_error, "", "", "", "", "", "", "", "", no_update, {"environment": selected_env, "running": False, "done": True}, True

    missing_env_vars = get_missing_env_vars()
    if missing_env_vars:
        message = (
            "Interim Solution is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )
        return message, "", "", "", "", "", "", "", "", no_update, {"environment": selected_env, "running": False, "done": True}, True

    jobs = [
        job
        for job in get_interim_jobs(config, selected_env)
        if bool(job.get("enabled", True))
    ]
    if not jobs:
        return (
            f"No enabled SharePoint folder jobs found for {selected_env.upper()}.",
            "0/0",
            f"{selected_env.upper()} | Waiting to start",
            "",
            "",
            "",
            "",
            "",
            no_update,
            {"environment": selected_env, "running": False, "done": True, "processed_files": 0, "total_files": 0},
            True,
        )

    from services.fabric_uploader_cli import app as uploader

    return _start_sync_run(uploader, config, selected_env, jobs)


def register_interim_solution_callbacks(app):
    @app.callback(
        Output("interim-solution-action-panel", "children"),
        Input("interim-solution-environment", "value"),
    )
    def update_interim_solution_action_panel(environment):
        return _build_action_panel(environment)

    @app.callback(
        Output("interim-solution-status", "children"),
        Output("interim-solution-progress", "children"),
        Output("interim-solution-progress-detail", "children"),
        Output("interim-solution-transfer", "children"),
        Output("interim-solution-pipeline-status", "children"),
        Output("interim-solution-pipeline-event", "children"),
        Output("interim-solution-pipeline-details", "children"),
        Output("interim-solution-pipeline-link", "children"),
        Output("interim-solution-results", "children"),
        Output("interim-solution-sync-state", "data"),
        Output("interim-solution-sync-poll", "disabled"),
        Input("interim-solution-refresh", "n_clicks"),
        Input("interim-solution-list", "n_clicks"),
        Input("interim-solution-sync", "n_clicks"),
        Input("interim-solution-environment", "value"),
        prevent_initial_call=True,
    )
    def handle_interim_solution_actions(refresh_clicks, list_clicks, sync_clicks, environment):
        trigger = callback_context.triggered[0] if callback_context.triggered else None
        if not trigger:
            raise PreventUpdate

        component_id = str(trigger.get("prop_id", "")).split(".", 1)[0]
        _debug(f"Callback action trigger={component_id} environment={environment}")
        if component_id == "interim-solution-refresh":
            status = _build_action_result(component_id, environment)
            return status, "", "", "", "", "", "", "", no_update, None, True
        if component_id == "interim-solution-list":
            status = _build_action_result(component_id, environment)
            return status, "", "", "", "", "", "", "", _build_list_results(environment), None, True
        if component_id == "interim-solution-sync":
            status, progress, detail, transfer, pipeline_status, pipeline_event, pipeline_details, pipeline_link, state, disabled = _sync_dimension_jobs(environment)
            return status, progress, detail, transfer, pipeline_status, pipeline_event, pipeline_details, pipeline_link, no_update, state, disabled
        if component_id == "interim-solution-environment":
            raise PreventUpdate
        raise PreventUpdate

    @app.callback(
        Output("interim-solution-progress", "children", allow_duplicate=True),
        Output("interim-solution-progress-detail", "children", allow_duplicate=True),
        Output("interim-solution-transfer", "children", allow_duplicate=True),
        Output("interim-solution-pipeline-status", "children", allow_duplicate=True),
        Output("interim-solution-pipeline-event", "children", allow_duplicate=True),
        Output("interim-solution-pipeline-details", "children", allow_duplicate=True),
        Output("interim-solution-pipeline-link", "children", allow_duplicate=True),
        Output("interim-solution-status", "children", allow_duplicate=True),
        Output("interim-solution-sync-state", "data", allow_duplicate=True),
        Output("interim-solution-sync-poll", "disabled", allow_duplicate=True),
        Input("interim-solution-sync-poll", "n_intervals"),
        State("interim-solution-sync-state", "data"),
        prevent_initial_call=True,
    )
    def poll_interim_solution_sync(_intervals, sync_state):
        state = _get_sync_state(str((sync_state or {}).get("run_id", "")))
        if not state:
            raise PreventUpdate
        if str(state.get("pipeline_run_id", "")).strip():
            state = _refresh_pipeline_status(state)
            run_id = str(state.get("run_id", ""))
            _set_sync_state(run_id, **{key: value for key, value in state.items() if key != "run_id"})
        progress = _build_progress_text(state)
        detail = _build_progress_detail(state)
        transfer = _build_transfer_text(state)
        pipeline_status = _build_pipeline_status_text(state)
        pipeline_event = _build_pipeline_event_text(state)
        pipeline_details = _build_pipeline_details_text(state)
        pipeline_link_url = _build_pipeline_link(state)
        pipeline_link = _build_pipeline_link_component(state)
        status = str(state.get("status", "")).strip()
        disabled = not bool(state.get("running", False)) and not _pipeline_needs_polling(state)
        return progress, detail, transfer, pipeline_status, pipeline_event, pipeline_details, pipeline_link, status, state, disabled
