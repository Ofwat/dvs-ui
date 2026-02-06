import json

import dash
from dash import Input, MATCH, Output, dcc, html
from dash.exceptions import PreventUpdate

from components.service_upload import build_service_upload_block
from online_auth import check_token_access, get_cached_access_status


SERVICE_DETAIL_PAGES = [
    {
        "label": "Data Validation Service",
        "path": "/services/data-validation",
        "summary": "Validate datasets against GOV.UK Ocean schemas before publishing.",
        "description": (
            "Data Validation Service ensures every data file matches expected column structures "
            "and formatting rules so dashboards stay consistent."
        ),
        "highlights": [
            "Checks required columns, date formats, and numeric ranges.",
            "Provides inline corrections or warnings for schema breaches.",
            "Signals success only when an XLSX file passes all validation steps.",
        ],
        "modes": [
            {
                "slug": "offline",
                "label": "Offline mode",
                "summary": "Run validation locally before exposing data externally.",
                "description": (
                    "Upload a workbook once, share only the report, and keep "
                    "sensitive data inside your secure network."
                ),
                "steps": [
                    "Download the GOV.UK Ocean template.",
                    "Run the validation script inside your network.",
                    "Publish the clean report to the dashboard.",
                ],
            },
            {
                "slug": "online",
                "label": "Online mode",
                "summary": "Stream uploads through the hosted validation API.",
                "description": (
                    "Send workbooks to our secure cloud service for real-time checks "
                    "and get instant feedback."
                ),
                "steps": [
                    "Connect your data source via the secure API.",
                    "Upload workbooks through the dashboard.",
                    "Review validation results in seconds.",
                ],
            },
        ],
    }
]


def _mode_card(service: dict, mode: dict):
    return html.Div(
        className="govuk-grid-column-one-half-from-desktop govuk-!-margin-bottom-4",
        children=[
            html.H3(mode["label"], className="govuk-heading-m"),
            html.P(mode["summary"], className="govuk-body"),
            html.P(mode["description"], className="govuk-body"),
            html.Ul(
                className="govuk-list govuk-list--bullet",
                children=[html.Li(step, className="govuk-body") for step in mode.get("steps", [])],
            ),
            dcc.Link(
                "Explore this mode",
                href=f"{service['path']}/{mode['slug']}",
                className="govuk-button govuk-button--secondary govuk-!-margin-top-3",
            ),
        ],
    )


def build_service_detail_content(service: dict):
    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full",
                children=[
                    html.H2(service["label"], className="govuk-heading-l"),
                    html.P(service["summary"], className="govuk-body"),
                    html.P(service["description"], className="govuk-body"),
                    html.Ul(
                        className="govuk-list govuk-list--bullet govuk-!-margin-bottom-0",
                        children=[
                            html.Li(highlight, className="govuk-body")
                            for highlight in service.get("highlights", [])
                        ],
                    ),
                    html.Div(
                        className="govuk-grid-row govuk-!-margin-top-6",
                        children=[_mode_card(service, mode) for mode in service.get("modes", [])],
                    ),
                ],
            )
        ],
    )


def build_offline_mode_app(service: dict):
    template_upload = {"type": "service-detail-upload", "index": service["path"], "role": "template"}
    final_upload = {"type": "service-detail-upload", "index": service["path"], "role": "final"}
    template_filename = {"type": "service-upload-filename", "index": service["path"], "role": "template"}
    final_filename = {"type": "service-upload-filename", "index": service["path"], "role": "final"}
    template_store = {"type": "service-upload-store", "index": service["path"], "role": "template"}
    final_store = {"type": "service-upload-store", "index": service["path"], "role": "final"}
    table_output = {"type": "union-table", "index": service["path"]}

    return html.Div(
        className="app-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full",
                children=[
                    html.H2(service["label"], className="govuk-heading-l"),
                    html.Div(
                        className="govuk-grid-row",
                        children=[
                            html.Div(
                                className="govuk-grid-column-one-half",
                                children=[
                                    build_service_upload_block("Upload template", template_upload, template_filename),
                                    dcc.Store(id=template_store),
                                ],
                            ),
                            html.Div(
                                className="govuk-grid-column-one-half",
                                children=[
                                    build_service_upload_block("Upload final workbook", final_upload, final_filename),
                                    dcc.Store(id=final_store),
                                ],
                            ),
                        ],
                    ),
                    dcc.Loading(
                        id={"type": "service-upload-loading", "index": service["path"]},
                        children=html.Div(id=table_output, className="govuk-!-margin-top-4"),
                        type="dot",
                    ),
                ],
            )
        ],
    )


def _build_online_mode_panel_children(service: dict, mode: dict, force_auth: bool = False):
    detail_children = []
    if force_auth:
        success, payload = check_token_access(force=True)
        detail_message = (
            "Authentication succeeded."
            if success
            else f"Authentication failed: {payload}"
        )
        detail_children.append(
            html.P(detail_message, className="govuk-body govuk-!-margin-bottom-0")
        )
        if success:
            detail_children.append(
                html.P(
                    "Tokens cached for Fabric and SharePoint requests.",
                    className="govuk-body govuk-!-margin-bottom-0",
                )
            )

    is_authenticated, status_message = get_cached_access_status()
    button_text = "Authenticate" if not is_authenticated else "Refresh authentication"
    button_id = {"type": "online-auth-button", "service": service["path"], "mode": mode["slug"]}

    return [
        html.Div(
            className="govuk-grid-row govuk-!-margin-bottom-3",
            children=[
                html.Div(
                    className="govuk-grid-column-one-half",
                    children=[
                        html.Button(
                            button_text,
                            id=button_id,
                            n_clicks=0,
                            className="govuk-button govuk-button--secondary",
                            type="button",
                        )
                    ],
                ),
                html.Div(
                    status_message,
                    className="govuk-body govuk-!-margin-top-0",
                ),
            ],
        ),
        *(
            [
                html.Div(
                    className="govuk-!-margin-top-2",
                    children=detail_children,
                )
            ]
            if detail_children
            else []
        ),
    ]


def build_online_mode_panel(service: dict, mode: dict):
    return html.Div(
        id={"type": "online-mode-panel", "service": service["path"], "mode": mode["slug"]},
        className="govuk-!-margin-top-4",
        children=_build_online_mode_panel_children(service, mode),
    )


def build_mode_detail(service: dict, mode: dict):
    sections = [
        html.H2(
            f"{service['label']} · {mode['label']}",
            className="govuk-heading-l",
        ),
        html.P(mode["description"], className="govuk-body"),
        html.Ul(
            className="govuk-list govuk-list--bullet govuk-!-margin-bottom-0",
            children=[html.Li(step, className="govuk-body") for step in mode.get("steps", [])],
        ),
        dcc.Link(
            "Back to service overview",
            href=service["path"],
            className="govuk-link govuk-!-margin-top-3 govuk-!-display-inline-block",
        ),
    ]
    if mode["slug"] == "offline":
        sections.append(html.Hr(className="govuk-section-break govuk-section-break--visible"))
        sections.append(build_offline_mode_app(service))
    elif mode["slug"] == "online":
        sections.append(html.Hr(className="govuk-section-break govuk-section-break--visible"))
        sections.append(build_online_mode_panel(service, mode))

    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[html.Div(className="govuk-grid-column-full", children=sections)],
    )


def find_service_by_path(path: str):
    for service in SERVICE_DETAIL_PAGES:
        if service["path"] == path:
            return service
    return None


def find_mode_by_slug(service: dict | None, slug: str):
    if not service:
        return None
    for mode in service.get("modes", []):
        if mode["slug"] == slug:
            return mode
    return None


def register_online_mode_callbacks(app):
    @app.callback(
        Output({"type": "online-mode-panel", "service": MATCH, "mode": MATCH}, "children"),
        Input({"type": "online-auth-button", "service": MATCH, "mode": MATCH}, "n_clicks"),
        prevent_initial_call=True,
    )
    def refresh_online_mode_panel(n_clicks):
        if not dash.callback_context.triggered:
            raise PreventUpdate
        triggered = dash.callback_context.triggered[0]
        component_id = json.loads(triggered["prop_id"].split(".")[0])
        service_path = component_id.get("service")
        mode_slug = component_id.get("mode")
        service = find_service_by_path(service_path)
        mode = find_mode_by_slug(service, mode_slug)
        if not service or not mode:
            raise PreventUpdate
        return _build_online_mode_panel_children(service, mode, force_auth=True)
