from __future__ import annotations

import threading
import uuid

from dash import Input, Output, State, callback_context, dcc, html, no_update
from dash.exceptions import PreventUpdate

from .dimension_loader_state import (
    get_dimension_jobs,
    get_dimension_jobs_by_environment,
    get_missing_env_vars,
    load_service_config,
)


_SYNC_RUNS: dict[str, dict[str, object]] = {}
_SYNC_RUNS_LOCK = threading.Lock()


def _debug(message: str) -> None:
    print(f"[DimensionLoader] {message}", flush=True)


DIMENSION_LOADER_SERVICE = {
    "label": "Dimension Loader",
    "path": "/services/dimension-loader",
    "summary": "Refresh, inspect, and sync SharePoint-backed dimension files into Fabric.",
    "description": (
        "Dimension Loader keeps the Fabric dimension lakehouse aligned with SharePoint sources. "
        "Use the dev or prod environment selector to choose the target job set."
    ),
}


def _create_sync_state(environment: str | None) -> dict[str, object]:
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    run_id = uuid.uuid4().hex
    return {
        "run_id": run_id,
        "environment": selected_env,
        "running": False,
        "done": False,
        "status": "",
        "current_line": "",
        "current_job": "",
        "current_file": "",
        "processed_mappings": 0,
        "total_mappings": 0,
        "successes": [],
        "failures": [],
        "error": None,
    }


def _set_sync_state(run_id: str, **updates: object) -> dict[str, object]:
    with _SYNC_RUNS_LOCK:
        state = dict(_SYNC_RUNS.get(run_id, {}))
        state.update(updates)
        state["run_id"] = run_id
        _SYNC_RUNS[run_id] = state
        return state


def _append_sync_line(run_id: str, line: str) -> None:
    with _SYNC_RUNS_LOCK:
        state = dict(_SYNC_RUNS.get(run_id, {}))
        state["current_line"] = str(line)
        state["status"] = str(line)
        _SYNC_RUNS[run_id] = state
    _debug(str(line))


def _get_sync_state(run_id: str | None) -> dict[str, object] | None:
    if not run_id:
        return None
    with _SYNC_RUNS_LOCK:
        state = _SYNC_RUNS.get(run_id)
        return dict(state) if state else None


def _build_current_transfer_text(sync_state: dict[str, object] | None) -> str:
    if not sync_state:
        return "No sync progress available."
    environment = str(sync_state.get("environment", "dev")).upper()
    total = int(sync_state.get("total_mappings", 0) or 0)
    processed = int(sync_state.get("processed_mappings", 0) or 0)
    remaining = max(total - processed, 0)
    current_file = str(sync_state.get("current_file", "")).strip()
    current_line = str(sync_state.get("current_line", "")).strip() or str(sync_state.get("status", "")).strip()
    return "\n".join(
        [
            f"{environment} sync",
            f"Processed {processed}/{total} mapping(s). {remaining} remaining.",
            f"Current file: {current_file or 'Waiting for next file...'}",
            current_line or "Waiting for progress...",
        ]
    )


def _build_progress_board(sync_state: dict[str, object] | None = None):
    config, _ = load_service_config()
    grouped_jobs = get_dimension_jobs_by_environment(config)
    selected_env = str((sync_state or {}).get("environment", "dev") or "dev")
    job_progress = sync_state.get("job_progress", {}) if isinstance(sync_state, dict) else {}
    current_line = str((sync_state or {}).get("current_line", "")).strip()
    current_job = str((sync_state or {}).get("current_job", "")).strip()
    total = int((sync_state or {}).get("total_mappings", 0) or 0)
    processed = int((sync_state or {}).get("processed_mappings", 0) or 0)
    remaining = max(total - processed, 0)

    children = [
        html.Div(
            className="govuk-inset-text govuk-!-margin-bottom-4",
            children=[
                html.H3("Progress", className="govuk-heading-s"),
                html.P(
                    f"Processed {processed}/{total} mapping(s). {remaining} remaining.",
                    className="govuk-body govuk-!-margin-bottom-1",
                ),
                html.P(
                    f"Current job: {current_job or 'Waiting to start'}",
                    className="govuk-body govuk-!-margin-bottom-1",
                ),
                html.P(
                    current_line or "Waiting for progress...",
                    className="govuk-body govuk-!-margin-bottom-0",
                ),
            ],
        )
    ]

    jobs = grouped_jobs.get(selected_env, [])
    if sync_state and bool(sync_state.get("done", False)):
        successes = list(sync_state.get("successes", []) or [])
        failures = list(sync_state.get("failures", []) or [])
        current_file = str(sync_state.get("current_file", "")).strip() or "Waiting for next file..."
        summary_lines = [str(sync_state.get("status", "")).strip() or "Sync complete."]
        summary_lines.append(f"Successful mappings: {len(successes)}")
        summary_lines.append(f"Failed mappings: {len(failures)}")
        summary_lines.append(f"Current file: {current_file}")
        if successes:
            summary_lines.append("Successful mappings:")
            summary_lines.extend(
                f"- {item['job']}: {item['source_relative_path']} -> {item['target_relative_path']}"
                for item in successes[:5]
            )
        if failures:
            summary_lines.append("Failed mappings:")
            summary_lines.extend(
                f"- {item['job']}: {item['source_relative_path']} -> {item['target_relative_path']} | {item['error']}"
                for item in failures[:5]
            )
        children.append(
            html.Div(
                className="govuk-inset-text govuk-!-margin-bottom-4",
                children=[
                    html.H3("Latest run", className="govuk-heading-s"),
                    html.Pre(
                        "\n".join(summary_lines),
                        className="govuk-body govuk-!-margin-bottom-0",
                        style={"whiteSpace": "pre-wrap", "marginBottom": 0},
                    ),
                ],
            )
        )
    if not jobs:
        children.append(
            html.Div(
                className="govuk-inset-text",
                children=[
                    html.P(
                        "No dimension jobs found for the selected environment.",
                        className="govuk-body govuk-!-margin-bottom-0",
                    )
                ],
            )
        )
        return html.Div(children=children)

    job_cards = []
    for job in jobs:
        progress = job_progress.get(str(job.get("name", "")), {}) if isinstance(job_progress, dict) else {}
        job_processed = int(progress.get("processed_mappings", 0) or 0)
        job_total = int(progress.get("total_mappings", len(list(job.get("mappings", []) or []))) or len(list(job.get("mappings", []) or [])))
        current_file = str(progress.get("current_file", "")).strip() or "Waiting to start"
        progress_value = 0 if job_total <= 0 else int((job_processed / job_total) * 100)
        is_active = current_file != "Waiting to start" and job_processed < job_total
        job_cards.append(
            html.Div(
                className="govuk-inset-text govuk-!-margin-bottom-4",
                children=[
                    html.Div(
                        className="govuk-!-display-flex govuk-!-justify-content-space-between govuk-!-align-items-center",
                        children=[
                            html.H4(job["name"], className="govuk-heading-s govuk-!-margin-bottom-0"),
                            html.Span(
                                f"{job_processed}/{job_total}",
                                className="govuk-tag" if is_active else "govuk-body govuk-!-margin-bottom-0",
                            ),
                        ],
                    ),
                    html.P(
                        f"Current file: {current_file}",
                        className="govuk-body govuk-!-margin-top-2 govuk-!-margin-bottom-1",
                    ),
                    html.Progress(
                        value=progress_value,
                        max=100,
                        className="govuk-!-width-full",
                        style={"width": "100%"},
                    ),
                    html.P(
                        f"Progress: {job_processed}/{job_total}",
                        className="govuk-body govuk-!-margin-top-2 govuk-!-margin-bottom-1",
                    ),
                    html.P(
                        f"Target: {job['target_root']}",
                        className="govuk-body govuk-!-margin-top-2 govuk-!-margin-bottom-1",
                    ),
                    html.P(
                        f"Workspace: {job['workspace_display_name']} | Lakehouse: {job['lakehouse_display_name']}",
                        className="govuk-body govuk-!-margin-bottom-1",
                    ),
                    html.P(
                        f"SharePoint links: {len(job['source_links'])} | Mappings: {len(job['mappings'])}",
                        className="govuk-body govuk-!-margin-bottom-0",
                    ),
                ],
            )
        )

    children.extend(job_cards)
    return html.Div(children=children)


def build_dimension_loader_content():
    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full",
                children=[
                    html.H2(DIMENSION_LOADER_SERVICE["label"], className="govuk-heading-l"),
                    html.P(DIMENSION_LOADER_SERVICE["summary"], className="govuk-body"),
                    html.P(DIMENSION_LOADER_SERVICE["description"], className="govuk-body govuk-!-margin-bottom-4"),
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
                    dcc.Store(
                        id="dimension-loader-sync-state",
                        data={"run_id": None, "running": False, "done": True, "lines": []},
                    ),
                    dcc.Interval(
                        id="dimension-loader-sync-poll",
                        interval=250,
                        disabled=True,
                    ),
                    html.Div(
                        className="govuk-!-margin-top-4",
                        children=[
                            html.Div(id="dimension-loader-status", className="govuk-body"),
                            html.Pre(
                                id="dimension-loader-current-transfer",
                                className="govuk-inset-text govuk-!-margin-bottom-4 govuk-body",
                                style={"whiteSpace": "pre-wrap", "marginBottom": "1rem"},
                            ),
                            html.Div(
                                id="dimension-loader-results",
                                className="govuk-inset-text govuk-!-margin-bottom-4 govuk-body",
                                style={"marginBottom": "1rem"},
                                children=_build_progress_board(),
                            ),
                        ],
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


def _build_action_panel(environment: str | None, sync_state: dict[str, object] | None = None):
    config, config_error = load_service_config()
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_dimension_jobs_by_environment(config)
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    jobs = grouped_jobs.get(selected_env, [])
    running = bool(sync_state and sync_state.get("running", False))
    ready = not config_error and not missing_env_vars and bool(jobs) and not running

    if config_error:
        return html.Div(
            className="govuk-inset-text govuk-!-margin-bottom-4",
            children=[
                html.P(config_error, className="govuk-body govuk-!-margin-bottom-0"),
            ],
        )
    if missing_env_vars:
        return html.Div(
            className="govuk-inset-text govuk-!-margin-bottom-4",
            children=[
                html.P(
                    "Dimensions Loader is not configured yet. Missing environment values: "
                    + ", ".join(missing_env_vars)
                    + ".",
                    className="govuk-body govuk-!-margin-bottom-0",
                ),
            ],
        )

    return html.Div(
        className="govuk-inset-text govuk-!-margin-bottom-4",
        children=[
            html.H3(f"{selected_env.upper()} environment", className="govuk-heading-s"),
            html.P(
                f"{len(jobs)} SharePoint-backed job(s) ready for this environment.",
                className="govuk-body govuk-!-margin-bottom-2",
            ),
            *(
                [
                    html.P(
                        f"Sync is running for {selected_env.upper()}. The buttons are disabled until it completes.",
                        className="govuk-body govuk-!-margin-bottom-4",
                    )
                ]
                if running
                else []
            ),
            html.Div(
                className="govuk-button-group",
                children=[
                    html.Button(
                        "Refresh files",
                        type="button",
                        id="dimension-loader-refresh",
                        className="govuk-button govuk-button--secondary",
                        disabled=not ready,
                    ),
                    html.Button(
                        "List files",
                        type="button",
                        id="dimension-loader-list",
                        className="govuk-button govuk-button--secondary",
                        disabled=not ready,
                    ),
                    html.Button(
                        "Sync dimensions Fabric with SharePoint",
                        type="button",
                        id="dimension-loader-sync",
                        className="govuk-button",
                        disabled=not ready,
                    ),
                ],
            ),
            html.P(
                "Refresh updates the summary below, List shows the configured files, and Sync uploads them to Fabric.",
                className="govuk-hint govuk-!-margin-bottom-0",
            ),
        ],
    )


def _build_dimension_loader_action_result(action_id: str, environment: str | None):
    config, config_error = load_service_config()
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_dimension_jobs_by_environment(config)
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    jobs = grouped_jobs.get(selected_env, [])

    if config_error:
        return config_error, _build_progress_board({"environment": selected_env, "job_progress": {}})
    if missing_env_vars:
        message = "Dimensions Loader is not configured yet. Missing environment values: " + ", ".join(missing_env_vars) + "."
        return message, _build_progress_board({"environment": selected_env, "job_progress": {}})

    if action_id == "dimension-loader-refresh":
        status = (
            f"Refreshed {len(jobs)} job(s) for {selected_env.upper()} from "
            "the Dimensions Loader config."
        )
    elif action_id == "dimension-loader-list":
        status = f"Listing {len(jobs)} job(s) for {selected_env.upper()}."
    else:
        status = "No action selected."

    state = {"environment": selected_env, "job_progress": {}}
    return status, _build_progress_board(state)


def _build_sync_results(sync_state: dict[str, object] | None):
    if not sync_state:
        return "No sync progress available.", "No sync progress available.", _build_progress_board()

    status = str(sync_state.get("status", "")).strip() or "Sync running..."
    running = bool(sync_state.get("running", False))
    done = bool(sync_state.get("done", False))
    environment = str(sync_state.get("environment", "dev")).upper()
    successes = list(sync_state.get("successes", []) or [])
    failures = list(sync_state.get("failures", []) or [])

    current_transfer = _build_current_transfer_text(sync_state)
    if not done:
        return status, current_transfer, _build_progress_board(sync_state)

    summary_lines = [
        status,
        "",
    ]
    if successes:
        summary_lines.append("Successful mappings:")
        summary_lines.extend(
            f"- {item['job']}: {item['source_relative_path']} -> {item['target_relative_path']}"
            for item in successes
        )
    if failures:
        summary_lines.append("")
        summary_lines.append("Failed mappings:")
        summary_lines.extend(
            f"- {item['job']}: {item['source_relative_path']} -> {item['target_relative_path']} | {item['error']}"
            for item in failures
        )
    if done and not running and not failures:
        summary_lines.append("")
        summary_lines.append(f"Sync completed for {environment}. {len(successes)} mapping(s) uploaded successfully.")
    return status, current_transfer, _build_progress_board(sync_state)


def _dimension_loader_sync_worker(
    run_id: str,
    uploader,
    config: dict[str, object],
    selected_env: str,
    jobs: list[dict[str, object]],
    total_mappings: int,
) -> None:
    def log(line: str) -> None:
        _debug(line)
        _append_sync_line(run_id, line)

    try:
        _debug(f"Starting worker run_id={run_id} env={selected_env} jobs={len(jobs)} total_mappings={total_mappings}")
        log(f"Using {selected_env.upper()} environment.")
        log(f"Loaded {len(jobs)} enabled SharePoint-backed job(s).")
        log("Authenticating SharePoint and Fabric scopes.")
        auth = uploader._online_auth()
        auth._ensure_authenticated(auth.SHAREPOINT_SCOPE)
        auth._ensure_authenticated(auth.FABRIC_SCOPE)
        log("Authentication completed.")

        successes: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        processed_mappings = 0
        last_file = ""
        job_progress: dict[str, dict[str, object]] = {}
        for job in jobs:
            job_name = str(job.get("name", ""))
            _debug(f"Resolving destination for job={job_name}")
            log(f"Resolving Fabric destination for {job_name}.")
            _set_sync_state(
                run_id,
                current_job=job_name,
                current_line=f"Preparing {job_name}.",
                status=f"Preparing {job_name}.",
            )
            try:
                workspace_id, lakehouse_id = uploader._resolve_fabric_destination(job)
            except Exception as exc:  # pylint:disable=broad-except
                failures.append({"job": job_name, "error": str(exc)})
                log(f"Failed to resolve Fabric destination for {job_name}: {exc}")
                continue

            mappings = list(job.get("mappings", []) or [])
            if not mappings:
                failures.append({"job": job_name, "error": "No mappings configured."})
                log(f"Skipped {job_name}: no mappings configured.")
                job_progress[job_name] = {
                    "processed_mappings": 0,
                    "total_mappings": 0,
                    "current_file": "",
                }
                continue

            log(f"Uploading {len(mappings)} mapping(s) for {job_name}.")
            job_processed = 0
            for mapping in mappings:
                source_path = str(mapping.get("source_relative_path", ""))
                target_path = str(mapping.get("target_relative_path", ""))
                last_file = source_path
                _debug(f"Job={job_name} mapping={source_path} -> {target_path} processed={processed_mappings}/{total_mappings}")
                job_progress[job_name] = {
                    "processed_mappings": job_processed,
                    "total_mappings": len(mappings),
                    "current_file": source_path,
                }
                _set_sync_state(
                    run_id,
                    current_file=source_path,
                    processed_mappings=processed_mappings,
                    total_mappings=total_mappings,
                    status=f"Processing {processed_mappings + 1}/{total_mappings} mapping(s).",
                )
                log(f"Uploading {source_path} -> {target_path}.")
                try:
                    result = uploader._upload_mapping(  # noqa: SLF001
                        job,
                        mapping,
                        workspace_id,
                        lakehouse_id,
                        config,
                        progress_callback=log,
                    )
                    successes.append(
                        {
                            "job": job_name,
                            "source_relative_path": str(result.get("source_relative_path", "")),
                            "target_relative_path": str(result.get("effective_target_path", "")),
                        }
                    )
                    processed_mappings += 1
                    job_processed += 1
                    job_progress[job_name] = {
                        "processed_mappings": job_processed,
                        "total_mappings": len(mappings),
                        "current_file": source_path,
                    }
                    _set_sync_state(
                        run_id,
                        processed_mappings=processed_mappings,
                        total_mappings=total_mappings,
                        current_file=source_path,
                        status=f"Completed {processed_mappings}/{total_mappings} mapping(s).",
                    )
                    log(
                        f"Uploaded {result.get('source_relative_path', '')} -> {result.get('effective_target_path', '')} "
                        f"({result.get('byte_count', 0)} bytes)."
                    )
                except Exception as exc:  # pylint:disable=broad-except
                    failures.append(
                        {
                            "job": job_name,
                            "source_relative_path": source_path,
                            "target_relative_path": target_path,
                            "error": str(exc),
                        }
                    )
                    processed_mappings += 1
                    job_processed += 1
                    job_progress[job_name] = {
                        "processed_mappings": job_processed,
                        "total_mappings": len(mappings),
                        "current_file": source_path,
                    }
                    _set_sync_state(
                        run_id,
                        processed_mappings=processed_mappings,
                        total_mappings=total_mappings,
                        current_file=source_path,
                        status=f"Failed {processed_mappings}/{total_mappings} mapping(s).",
                    )
                    log(f"Failed {source_path} -> {target_path}: {exc}")

        status = (
            f"Sync complete for {selected_env.upper()}: "
            f"{len(successes)} successful mapping(s), {len(failures)} failed mapping(s)."
        )
        _set_sync_state(
            run_id,
            running=False,
            done=True,
            status=status,
            current_file=last_file,
            processed_mappings=processed_mappings,
            total_mappings=total_mappings,
            job_progress=job_progress,
            successes=successes,
            failures=failures,
        )
    except Exception as exc:  # pylint:disable=broad-except
        _debug(f"Worker failed run_id={run_id}: {exc}")
        log(f"Sync failed: {exc}")
        _set_sync_state(
            run_id,
            running=False,
            done=True,
            status=f"Sync failed for {selected_env.upper()}: {exc}",
            current_file=last_file,
            error=str(exc),
        )


def _start_sync_run(uploader, config: dict[str, object], selected_env: str, jobs: list[dict[str, object]]):
    state = _create_sync_state(selected_env)
    run_id = str(state["run_id"])
    total_mappings = sum(len(list(job.get("mappings", []) or [])) for job in jobs)
    _debug(f"Starting sync run_id={run_id} env={selected_env} jobs={len(jobs)} total_mappings={total_mappings}")
    _set_sync_state(
        run_id,
        environment=selected_env,
        running=True,
        done=False,
        status=f"Starting sync for {selected_env.upper()}...",
        current_line="Starting sync...",
        current_file="",
        processed_mappings=0,
        total_mappings=total_mappings,
        job_progress={str(job.get("name", "")): {"processed_mappings": 0, "total_mappings": len(list(job.get("mappings", []) or [])), "current_file": ""} for job in jobs},
        successes=[],
        failures=[],
        error=None,
    )
    thread = threading.Thread(
        target=_dimension_loader_sync_worker,
        args=(run_id, uploader, config, selected_env, jobs, total_mappings),
        daemon=True,
    )
    thread.start()
    return (
        f"Sync started for {selected_env.upper()}. Progress will update below.",
        "\n".join(
            [
                f"{selected_env.upper()} progress",
                f"Processed 0/{total_mappings} mapping(s). {total_mappings} remaining.",
                "Current file: Waiting for next file...",
                "Starting sync...",
            ]
        ),
        _build_progress_board(_get_sync_state(run_id) or state),
        _get_sync_state(run_id) or state,
        False,
    )


def _sync_dimension_jobs(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    _debug(f"Sync requested env={selected_env}")
    if config_error:
        state = {"environment": selected_env, "job_progress": {}}
        return config_error, "", _build_progress_board(state), state, True

    missing_env_vars = get_missing_env_vars()
    if missing_env_vars:
        message = (
            "Dimensions Loader is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )
        state = {"environment": selected_env, "job_progress": {}}
        return message, "", _build_progress_board(state), state, True

    jobs = [
        job
        for job in get_dimension_jobs(config, selected_env)
        if bool(job.get("enabled", True))
    ]
    if not jobs:
        return (
            f"No enabled SharePoint dimension jobs found for {selected_env.upper()}.",
            "",
            _build_progress_board({"environment": selected_env, "job_progress": {}, "processed_mappings": 0, "total_mappings": 0, "current_line": ""}),
            {"environment": selected_env, "running": False, "done": True, "job_progress": {}},
            True,
        )

    from services.fabric_uploader_cli import app as uploader

    return _start_sync_run(uploader, config, selected_env, jobs)


def register_dimension_loader_callbacks(app):
    @app.callback(
        Output("dimension-loader-action-panel", "children"),
        Input("dimension-loader-environment", "value"),
        Input("dimension-loader-sync-state", "data"),
    )
    def update_dimension_loader_action_panel(environment, sync_state):
        return _build_action_panel(environment, sync_state)

    @app.callback(
        Output("dimension-loader-status", "children"),
        Output("dimension-loader-current-transfer", "children"),
        Output("dimension-loader-results", "children"),
        Output("dimension-loader-sync-state", "data"),
        Output("dimension-loader-sync-poll", "disabled"),
        Input("dimension-loader-refresh", "n_clicks"),
        Input("dimension-loader-list", "n_clicks"),
        Input("dimension-loader-sync", "n_clicks"),
        Input("dimension-loader-environment", "value"),
        State("dimension-loader-sync-state", "data"),
        prevent_initial_call=True,
    )
    def handle_dimension_loader_actions(refresh_clicks, list_clicks, sync_clicks, environment, sync_state):
        trigger = callback_context.triggered[0] if callback_context.triggered else None
        if not trigger:
            raise PreventUpdate

        component_id = str(trigger.get("prop_id", "")).split(".", 1)[0]
        _debug(f"Callback action trigger={component_id} environment={environment}")
        current_state = sync_state if isinstance(sync_state, dict) else {"run_id": None, "running": False, "done": True, "lines": []}
        if component_id == "dimension-loader-refresh":
            status, results = _build_dimension_loader_action_result(component_id, environment)
            return status, "", results, current_state, True
        if component_id == "dimension-loader-list":
            status, results = _build_dimension_loader_action_result(component_id, environment)
            return status, "", results, current_state, True
        if component_id == "dimension-loader-sync":
            status, current_transfer, results, new_state, disabled = _sync_dimension_jobs(environment)
            return status, current_transfer, results, new_state, disabled
        raise PreventUpdate

    @app.callback(
        Output("dimension-loader-status", "children", allow_duplicate=True),
        Output("dimension-loader-current-transfer", "children", allow_duplicate=True),
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
        _debug(
            f"Poll run_id={state.get('run_id')} running={state.get('running')} "
            f"processed={state.get('processed_mappings')}/{state.get('total_mappings')} "
            f"current_file={state.get('current_file', '')}"
        )
        disabled = not bool(state.get("running", False))
        if state.get("running", False):
            return no_update, no_update, _build_progress_board(state), state, False
        status, current_transfer, results = _build_sync_results(state)
        return status, current_transfer, results, state, disabled
