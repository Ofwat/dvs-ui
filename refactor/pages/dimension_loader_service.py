from __future__ import annotations

import threading
import uuid

from dash import Input, Output, State, callback_context, dcc, html, no_update
from dash.exceptions import PreventUpdate

from .dimension_loader_state import (
    build_env_missing_message,
    get_dimension_jobs,
    get_dimension_jobs_by_environment,
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


def _build_sharepoint_folder_link(folder_url: str) -> html.A | str:
    folder_url = str(folder_url or "").strip()
    if not folder_url:
        return ""
    return html.A("Open SharePoint folder", href=folder_url, target="_blank", rel="noopener noreferrer")


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
                    html.H3("Choose environment", className="govuk-heading-m"),
                    html.Div(
                        className="govuk-radios govuk-radios--inline govuk-!-margin-bottom-6",
                        children=[
                            html.Div(
                                className="govuk-radios__item",
                                children=[
                                    dcc.RadioItems(
                                        id="dimension-loader-environment",
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
            html.Ul(
                className="govuk-list govuk-list--spaced govuk-!-margin-bottom-2",
                children=[
                    html.Li(
                        children=[
                            html.Span(f"{str(job.get('name', '')).strip() or 'Unnamed job'}", className="govuk-!-font-weight-bold"),
                            html.Span(" | ", className="govuk-hint"),
                            _build_sharepoint_folder_link((_job_source_links(job) or [""])[0]),
                        ]
                    )
                    for job in jobs
                ],
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
                f"{successes} successful file(s), {failures} failed file(s)."
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
        state,
        False,
    )


def _sync_dimension_jobs(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    _debug(f"Sync requested env={selected_env}")
    if config_error:
        return config_error, "", "", {"environment": selected_env, "running": False, "done": True}, True

    missing_env_vars = get_missing_env_vars()
    if missing_env_vars:
        message = (
            "Dimensions Loader is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )
        return message, "", "", {"environment": selected_env, "running": False, "done": True}, True

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
            return status, "", "", no_update, None, True
        if component_id == "dimension-loader-list":
            status = _build_dimension_loader_action_result(component_id, environment)
            return status, "", "", _build_list_results(environment), None, True
        if component_id == "dimension-loader-sync":
            status, progress, detail, state, disabled = _sync_dimension_jobs(environment)
            detail = _build_progress_detail(state)
            return status, progress, detail, no_update, state, disabled
        if component_id == "dimension-loader-environment":
            raise PreventUpdate
        raise PreventUpdate

    @app.callback(
        Output("dimension-loader-progress", "children", allow_duplicate=True),
        Output("dimension-loader-progress-detail", "children", allow_duplicate=True),
        Output("dimension-loader-status", "children", allow_duplicate=True),
        Output("dimension-loader-results", "children", allow_duplicate=True),
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
        progress = _build_progress_text(state)
        detail = _build_progress_detail(state)
        status = str(state.get("status", "")).strip()
        disabled = not bool(state.get("running", False))
        return progress, detail, status, no_update, state, disabled
