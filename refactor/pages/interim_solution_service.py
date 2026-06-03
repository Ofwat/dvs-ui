from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from dash import Input, Output, State, callback_context, dcc, html, no_update
from dash.exceptions import PreventUpdate

from .interim_solution_state import (
    get_interim_jobs,
    get_interim_jobs_by_environment,
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


def _build_run_folder_name() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


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
            html.P(
                "Refresh updates the job summary, List shows the configured folders, and Sync uploads every file in the SharePoint folder.",
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
            current_job_folder=run_folder_name,
            processed_files=0,
            total_files=total_files,
        )

        processed_files = 0
        successes = 0
        failures = 0
        last_job_name = ""
        last_file_name = ""
        for job_entry in prepared_jobs:
            job = dict(job_entry["job"])
            files = list(job_entry["files"])
            job_name = str(job.get("name", "")).strip() or "Unnamed job"
            folder_url = str(job.get("source_folder_url", "")).strip()
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
                    )
                    uploader._upload_bytes_with_progress(  # noqa: SLF001
                        workspace_id,
                        lakehouse_id,
                        target_relative_path,
                        file_bytes,
                    )
                    successes += 1
                except Exception as exc:  # pylint:disable=broad-except
                    _debug(f"Upload failed for {source_relative_path}: {exc}")
                    failures += 1
                finally:
                    processed_files += 1
                    _set_sync_state(
                        run_id,
                        processed_files=processed_files,
                        total_files=total_files,
                        current_file=source_relative_path,
                        status=f"Processing {processed_files}/{total_files}",
                    )

        _set_sync_state(
            run_id,
            running=False,
            done=True,
            current_job=last_job_name,
            current_file=last_file_name,
            current_job_folder=run_folder_name,
            processed_files=processed_files,
            total_files=total_files,
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
            "Interim Solution is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )
        return message, "", "", {"environment": selected_env, "running": False, "done": True}, True

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
            return status, "", "", no_update, None, True
        if component_id == "interim-solution-list":
            status = _build_action_result(component_id, environment)
            return status, "", "", _build_list_results(environment), None, True
        if component_id == "interim-solution-sync":
            status, progress, detail, state, disabled = _sync_dimension_jobs(environment)
            return status, progress, detail, no_update, state, disabled
        if component_id == "interim-solution-environment":
            raise PreventUpdate
        raise PreventUpdate

    @app.callback(
        Output("interim-solution-progress", "children", allow_duplicate=True),
        Output("interim-solution-progress-detail", "children", allow_duplicate=True),
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
        progress = _build_progress_text(state)
        detail = _build_progress_detail(state)
        status = str(state.get("status", "")).strip()
        disabled = not bool(state.get("running", False))
        return progress, detail, status, state, disabled
