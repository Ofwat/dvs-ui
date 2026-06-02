from __future__ import annotations

from dash import Input, Output, dcc, html

from .dimension_loader_state import (
    build_config_missing_message,
    build_env_missing_message,
    get_dimension_jobs_by_environment,
    get_missing_env_vars,
    load_service_config,
)


DIMENSION_LOADER_SERVICE = {
    "label": "Dimension Loader",
    "path": "/services/dimension-loader",
    "summary": "Refresh, inspect, and sync SharePoint-backed dimension files into Fabric.",
    "description": (
        "Dimension Loader keeps the Fabric dimension lakehouse aligned with SharePoint sources. "
        "Use the dev or prod environment selector to choose the target job set."
    ),
    "highlights": [
        "SharePoint-only source workflow.",
        "Environment selection based on [DEV] and [PROD] job names.",
        "Designed for a future editable service config without blocking the first release.",
    ],
}


def build_dimension_loader_content():
    config, config_error = load_service_config()
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_dimension_jobs_by_environment(config)
    config_missing_lines = build_config_missing_message(config_error)
    env_missing_lines = build_env_missing_message(missing_env_vars)

    status_blocks = []
    if config_missing_lines:
        status_blocks.append(
            html.Div(
                className="govuk-inset-text govuk-!-margin-bottom-4",
                children=[
                    html.Strong("Config not found", className="govuk-body"),
                    html.Ul(
                        className="govuk-list govuk-list--bullet govuk-!-margin-top-2 govuk-!-margin-bottom-0",
                        children=[html.Li(line, className="govuk-body") for line in config_missing_lines],
                    ),
                ],
            )
        )
    if env_missing_lines:
        status_blocks.append(
            html.Div(
                className="govuk-inset-text govuk-!-margin-bottom-4",
                children=[
                    html.Strong("Environment not ready", className="govuk-body"),
                    html.Ul(
                        className="govuk-list govuk-list--bullet govuk-!-margin-top-2 govuk-!-margin-bottom-0",
                        children=[html.Li(line, className="govuk-body") for line in env_missing_lines],
                    ),
                ],
            )
        )

    env_cards = []
    for environment in ("dev", "prod"):
        jobs = grouped_jobs.get(environment, [])
        env_cards.append(
            html.Div(
                className="govuk-grid-column-one-half govuk-!-margin-bottom-4",
                children=[
                    html.Div(
                        className="govuk-card govuk-card--clickable",
                        children=[
                            html.Div(
                                className="govuk-card__content",
                                children=[
                                    html.H3(environment.upper(), className="govuk-heading-m"),
                                    html.P(
                                        f"{len(jobs)} configured dimension job(s).",
                                        className="govuk-body",
                                    ),
                                    html.Ul(
                                        className="govuk-list govuk-list--bullet govuk-!-margin-bottom-0",
                                        children=[
                                            html.Li(
                                                f"{job['name']} -> {job['target_root']}",
                                                className="govuk-body",
                                            )
                                            for job in jobs[:5]
                                        ]
                                        or [html.Li("No jobs found for this environment.", className="govuk-body")],
                                    ),
                                ],
                            )
                        ],
                    )
                ],
            )
        )

    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full",
                children=[
                    html.H2(DIMENSION_LOADER_SERVICE["label"], className="govuk-heading-l"),
                    html.P(DIMENSION_LOADER_SERVICE["summary"], className="govuk-body"),
                    html.P(DIMENSION_LOADER_SERVICE["description"], className="govuk-body"),
                    *status_blocks,
                    html.Ul(
                        className="govuk-list govuk-list--bullet govuk-!-margin-bottom-4",
                        children=[
                            html.Li(item, className="govuk-body")
                            for item in DIMENSION_LOADER_SERVICE["highlights"]
                        ],
                    ),
                    html.H3("Configured environments", className="govuk-heading-m"),
                    html.Div(
                        className="govuk-grid-row govuk-!-margin-bottom-4",
                        children=env_cards,
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
                        className="govuk-summary-list",
                        children=[
                            html.Div(
                                className="govuk-summary-list__row",
                                children=[
                                    html.Div("Refresh files", className="govuk-summary-list__key"),
                                    html.Div(
                                        "Rescan SharePoint and refresh the file list for the selected environment.",
                                        className="govuk-summary-list__value",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="govuk-summary-list__row",
                                children=[
                                    html.Div("List files", className="govuk-summary-list__key"),
                                    html.Div(
                                        "Show the current dimension files configured for the selected environment.",
                                        className="govuk-summary-list__value",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="govuk-summary-list__row",
                                children=[
                                    html.Div("Sync to Fabric", className="govuk-summary-list__key"),
                                    html.Div(
                                        "Upload the selected SharePoint dimension files into Fabric.",
                                        className="govuk-summary-list__value",
                                    ),
                                ],
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


def _build_action_panel(environment: str | None):
    config, config_error = load_service_config()
    missing_env_vars = get_missing_env_vars()
    grouped_jobs = get_dimension_jobs_by_environment(config)
    selected_env = environment if environment in {"dev", "prod"} else "dev"
    jobs = grouped_jobs.get(selected_env, [])
    ready = not config_error and not missing_env_vars and bool(jobs)

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
        className="govuk-!-margin-bottom-4",
        children=[
            html.H3(f"{selected_env.upper()} environment", className="govuk-heading-m"),
            html.P(
                f"{len(jobs)} dimension job(s) ready for the selected environment.",
                className="govuk-body",
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
                "Action wiring will be added after the service shell is stable.",
                className="govuk-hint",
            ),
        ],
    )


def register_dimension_loader_callbacks(app):
    @app.callback(
        Output("dimension-loader-action-panel", "children"),
        Input("dimension-loader-environment", "value"),
    )
    def update_dimension_loader_action_panel(environment):
        return _build_action_panel(environment)
