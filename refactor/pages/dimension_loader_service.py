from __future__ import annotations

import threading
import uuid

from dash import Input, Output, State, callback_context, dcc, html
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


def _create_sync_state(environment: str | None, total_mappings: int = 0) -> dict[str, object]:
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    return {
        "run_id": uuid.uuid4().hex,
        "environment": selected_env,
        "running": False,
        "done": False,
        "status": "",
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
                "Refresh updates the job summary, List shows the configured files, and Sync uploads them to Fabric.",
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
        job_names = ", ".join(
            str(job.get("name", "")).strip()
            for job in jobs
            if str(job.get("name", "")).strip()
        )
        return f"{selected_env.upper()} jobs: {job_names}"
    return "No action selected."


def _dimension_loader_sync_worker(
    run_id: str,
    uploader,
    config: dict[str, object],
    selected_env: str,
    jobs: list[dict[str, object]],
    total_mappings: int,
) -> None:
    try:
        _debug(
            f"Starting worker run_id={run_id} env={selected_env} "
            f"jobs={len(jobs)} total_mappings={total_mappings}"
        )
        auth = uploader._online_auth()
        auth._ensure_authenticated(auth.SHAREPOINT_SCOPE)
        auth._ensure_authenticated(auth.FABRIC_SCOPE)
        _set_sync_state(
            run_id,
            running=True,
            status=f"Syncing {selected_env.upper()}...",
            processed_mappings=0,
            total_mappings=total_mappings,
        )

        processed_mappings = 0
        successes = 0
        failures = 0
        for job in jobs:
            job_name = str(job.get("name", "")).strip() or "Unnamed job"
            _debug(f"Resolving destination for job={job_name}")
            try:
                workspace_id, lakehouse_id = uploader._resolve_fabric_destination(job)
            except Exception as exc:  # pylint:disable=broad-except
                _debug(f"Failed to resolve destination for {job_name}: {exc}")
                failures += len(list(job.get("mappings", []) or [])) or 1
                continue

            mappings = list(job.get("mappings", []) or [])
            for mapping in mappings:
                source_path = str(mapping.get("source_relative_path", "")).strip()
                _debug(f"Uploading {source_path} for job={job_name}")
                try:
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
                        status=f"Processing {processed_mappings}/{total_mappings}",
                    )

        _set_sync_state(
            run_id,
            running=False,
            done=True,
            processed_mappings=processed_mappings,
            total_mappings=total_mappings,
            status=(
                f"Sync complete for {selected_env.upper()}: "
                f"{successes} successful mapping(s), {failures} failed mapping(s)."
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
    total_mappings = sum(len(list(job.get("mappings", []) or [])) for job in jobs)
    state = _create_sync_state(selected_env, total_mappings)
    run_id = str(state["run_id"])
    _SYNC_RUNS[run_id] = state
    thread = threading.Thread(
        target=_dimension_loader_sync_worker,
        args=(run_id, uploader, config, selected_env, jobs, total_mappings),
        daemon=True,
    )
    thread.start()
    return (
        f"Sync started for {selected_env.upper()}.",
        "0/{}".format(total_mappings) if total_mappings else "0/0",
        state,
        False,
    )


def _sync_dimension_jobs(environment: str | None):
    config, config_error = load_service_config()
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    _debug(f"Sync requested env={selected_env}")
    if config_error:
        return config_error, "", {"environment": selected_env, "running": False, "done": True}, True

    missing_env_vars = get_missing_env_vars()
    if missing_env_vars:
        message = (
            "Dimensions Loader is not configured yet. Missing environment values: "
            + ", ".join(missing_env_vars)
            + "."
        )
        return message, "", {"environment": selected_env, "running": False, "done": True}, True

    jobs = [
        job
        for job in get_dimension_jobs(config, selected_env)
        if bool(job.get("enabled", True))
    ]
    if not jobs:
        return (
            f"No enabled SharePoint dimension jobs found for {selected_env.upper()}.",
            "0/0",
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
            return status, "", None, True
        if component_id == "dimension-loader-list":
            status = _build_dimension_loader_action_result(component_id, environment)
            return status, "", None, True
        if component_id == "dimension-loader-sync":
            status, progress, state, disabled = _sync_dimension_jobs(environment)
            return status, progress, state, disabled
        if component_id == "dimension-loader-environment":
            raise PreventUpdate
        raise PreventUpdate

    @app.callback(
        Output("dimension-loader-progress", "children", allow_duplicate=True),
        Output("dimension-loader-status", "children", allow_duplicate=True),
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
        status = str(state.get("status", "")).strip()
        disabled = not bool(state.get("running", False))
        return progress, status, state, disabled
