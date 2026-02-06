import json

import dash
from dash import ALL, Input, MATCH, Output, State, dcc, html
from dash.exceptions import PreventUpdate

from components.service_upload import build_service_upload_block
from online_auth import (
    check_token_access,
    download_sharepoint_item,
    get_cached_access_status,
    list_sharepoint_drive_items,
    list_sharepoint_resources,
)


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
    sharepoint_drives = None
    sharepoint_status = "Tokens must be refreshed before browsing."
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
            drives_success, drives_payload = list_sharepoint_resources()
            if drives_success:
                sharepoint_drives = drives_payload
                sharepoint_status = "Select a drive to begin browsing."
            else:
                sharepoint_status = f"SharePoint drives unavailable: {drives_payload}"

    is_authenticated, status_message = get_cached_access_status()
    button_text = "Authenticate" if not is_authenticated else "Refresh authentication"
    button_id = {"type": "online-auth-button", "service": service["path"], "mode": mode["slug"]}

    children = [
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
    children.append(build_sharepoint_explorer(service, mode, sharepoint_drives, sharepoint_status))
    return children


def build_online_mode_panel(service: dict, mode: dict):
    return html.Div(
        id={"type": "online-mode-panel", "service": service["path"], "mode": mode["slug"]},
        className="govuk-!-margin-top-4",
        children=_build_online_mode_panel_children(service, mode),
    )


def build_mode_detail(service: dict, mode: dict):
    sections = [
        html.H2(
            f"{service['label']} - {mode['label']}",
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


def _explorer_id(service_path: str, mode_slug: str, kind: str, **extra):
    base = {"type": f"sharepoint-{kind}", "service": service_path, "mode": mode_slug}
    base.update(extra)
    return base


def _find_item_name(items: list[dict], item_id: str) -> str | None:
    for item in items:
        if item.get("id") == item_id:
            return item.get("name")
    return None


def _render_sharepoint_item(item: dict, service_path: str, mode_slug: str):
    label = item.get("name", "Untitled")
    button_id = _explorer_id(
        service_path,
        mode_slug,
        "folder-open" if item.get("isFolder") else "file-download",
        item=item.get("id"),
    )
    button_text = "Open folder" if item.get("isFolder") else "Download file"
    return html.Li(
        className="govuk-!-margin-bottom-1",
        children=[
            html.Div(label, className="govuk-body"),
            html.Div(
                html.Button(
                    button_text,
                    id=button_id,
                    n_clicks=0,
                    className="govuk-button govuk-button--secondary govuk-!-margin-top-1",
                    type="button",
                ),
                className="govuk-!-margin-top-1",
            ),
        ],
    )


def _render_sharepoint_items_list(
    items: list[dict],
    state: dict,
    service_path: str,
    mode_slug: str,
):
    children = []
    drive_id = state.get("drive_id")
    if not drive_id:
        children.append(html.P("Select a drive to begin browsing.", className="govuk-body"))
        return children
    if not items:
        children.append(html.P("This folder is empty.", className="govuk-body"))
    else:
        children.append(
            html.Ul(
                className="govuk-list govuk-list--bullet",
                children=[
                    _render_sharepoint_item(item, service_path, mode_slug)
                    for item in items
                ],
            )
        )
    return children


def _format_breadcrumbs(state: dict):
    drive_name = state.get("drive_name") or "Selected drive"
    breadcrumbs = [drive_name] + [crumb.get("name", "Folder") for crumb in state.get("breadcrumbs", [])]
    return " / ".join(breadcrumbs)


def build_sharepoint_explorer(
    service: dict,
    mode: dict,
    drives: list[dict] | None = None,
    status_message: str | None = None,
):
    service_path = service["path"]
    mode_slug = mode["slug"]
    status_id = _explorer_id(service_path, mode_slug, "status")
    dropdown_id = _explorer_id(service_path, mode_slug, "drive-dropdown")
    drives_store_id = _explorer_id(service_path, mode_slug, "drive-store")
    state_store_id = _explorer_id(service_path, mode_slug, "state-store")
    items_store_id = _explorer_id(service_path, mode_slug, "items-store")
    items_container_id = _explorer_id(service_path, mode_slug, "items-container")
    breadcrumb_id = _explorer_id(service_path, mode_slug, "breadcrumb")
    download_id = _explorer_id(service_path, mode_slug, "download")

    drive_options = []
    drive_value = None
    if drives:
        drive_options = [
            {"label": drive.get("name", "Drive"), "value": drive.get("id")}
            for drive in drives
        ]
        if drive_options:
            drive_value = drive_options[0]["value"]

    return html.Div(
        className="govuk-!-margin-top-5",
        children=[
            html.H3("SharePoint explorer", className="govuk-heading-s"),
            html.Div(
                className="govuk-body govuk-!-margin-bottom-3",
                id=status_id,
                children=status_message or "Tokens must be refreshed before browsing.",
            ),
            html.Button(
                "Up one level",
                id=_explorer_id(service_path, mode_slug, "folder-back"),
                n_clicks=0,
                className="govuk-button govuk-button--secondary govuk-!-margin-bottom-3",
                type="button",
                disabled=True,
            ),
            dcc.Store(
                id=state_store_id,
                data={"drive_id": None, "drive_name": "", "breadcrumbs": []},
            ),
            dcc.Store(id=drives_store_id, data={"drives": drives or []}),
            dcc.Store(id=items_store_id, data={"items": []}),
            html.Div(
                className="govuk-grid-row govuk-!-margin-bottom-4",
                children=[
                    html.Div(
                        className="govuk-grid-column-one-half",
                        children=[
                            html.Label("Select a drive", className="govuk-label"),
                            dcc.Dropdown(
                                id=dropdown_id,
                                options=drive_options,
                                clearable=False,
                                placeholder="Choose a drive",
                                value=drive_value,
                            ),
                        ],
                    ),
                    html.Div(
                        className="govuk-grid-column-one-half",
                        children=[
                            html.Label("Current path", className="govuk-label"),
                            html.Div(id=breadcrumb_id, className="govuk-body govuk-!-margin-top-1"),
                        ],
                    ),
                ],
            ),
            html.Div(
                id=items_container_id,
                className="govuk-panel govuk-panel--confirmation",
                children=html.P("Select a drive to begin browsing.", className="govuk-body"),
            ),
            dcc.Download(id=download_id),
        ],
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

    _register_sharepoint_callbacks(app)


def _register_sharepoint_callbacks(app):
    @app.callback(
        Output({"type": "sharepoint-items-container", "service": MATCH, "mode": MATCH}, "children"),
        Output({"type": "sharepoint-items-store", "service": MATCH, "mode": MATCH}, "data"),
        Output({"type": "sharepoint-state-store", "service": MATCH, "mode": MATCH}, "data"),
        Output({"type": "sharepoint-status", "service": MATCH, "mode": MATCH}, "children"),
        Input({"type": "sharepoint-drive-dropdown", "service": MATCH, "mode": MATCH}, "value"),
        Input(
            {"type": "sharepoint-folder-open", "service": MATCH, "mode": MATCH, "item": ALL},
            "n_clicks",
        ),
        Input(
            {"type": "sharepoint-folder-back", "service": MATCH, "mode": MATCH},
            "n_clicks",
        ),
        State({"type": "sharepoint-state-store", "service": MATCH, "mode": MATCH}, "data"),
        State({"type": "sharepoint-items-store", "service": MATCH, "mode": MATCH}, "data"),
        State({"type": "sharepoint-drive-store", "service": MATCH, "mode": MATCH}, "data"),
        prevent_initial_call=True,
    )
    def update_sharepoint_items(
        drive_value,
        folder_open_clicks,
        folder_back_clicks,
        state_data,
        items_cache,
        drives_data,
    ):
        if not dash.callback_context.triggered:
            raise PreventUpdate
        trigger = dash.callback_context.triggered[0]
        component_id = json.loads(trigger["prop_id"].split(".")[0])
        action_type = component_id.get("type")
        service_path = component_id.get("service")
        mode_slug = component_id.get("mode")

        state = state_data or {"drive_id": None, "drive_name": "", "breadcrumbs": []}
        items_cache = items_cache or {"items": []}
        drives = (drives_data or {}).get("drives", [])

        if action_type == "sharepoint-drive-dropdown":
            drive_id = drive_value
            drive_name = next((drive.get("name") for drive in drives if drive.get("id") == drive_id), "")
            state = {"drive_id": drive_id, "drive_name": drive_name, "breadcrumbs": []}
            folder_id = None
        elif action_type == "sharepoint-folder-open":
            # Pattern-matching inputs can trigger when the list of buttons is created.
            # Ignore those "all zeros" cases; only react to an actual click.
            if not folder_open_clicks or max((count or 0) for count in folder_open_clicks) == 0:
                raise PreventUpdate
            drive_id = component_id.get("drive") or state.get("drive_id")
            folder_id = component_id.get("item")
            if not folder_id:
                raise PreventUpdate
            folder_name = _find_item_name(items_cache.get("items", []), folder_id) or "Folder"
            breadcrumbs = state.get("breadcrumbs", [])[:]
            breadcrumbs.append({"id": folder_id, "name": folder_name})
            state = {
                "drive_id": drive_id,
                "drive_name": state.get("drive_name", ""),
                "breadcrumbs": breadcrumbs,
            }
        elif action_type == "sharepoint-folder-back":
            if not folder_back_clicks:
                raise PreventUpdate
            drive_id = state.get("drive_id")
            breadcrumbs = state.get("breadcrumbs", [])[:]
            if breadcrumbs:
                breadcrumbs.pop()
            folder_id = breadcrumbs[-1]["id"] if breadcrumbs else None
            state = {
                "drive_id": drive_id,
                "drive_name": state.get("drive_name", ""),
                "breadcrumbs": breadcrumbs,
            }
        else:
            raise PreventUpdate

        if not drive_id:
            children = [html.P("Select a drive to begin browsing.", className="govuk-body")]
            return children, items_cache, state, "Select a drive to continue."

        success, payload = list_sharepoint_drive_items(drive_id, folder_id)
        if not success:
            message = f"Unable to list SharePoint contents: {payload}"
            return [html.P(message, className="govuk-body")], items_cache, state, message

        items = payload
        children = _render_sharepoint_items_list(items, state, service_path, mode_slug)
        message = f"Showing {len(items)} items | {_format_breadcrumbs(state)}"
        return children, {"items": items}, state, message

    @app.callback(
        Output({"type": "sharepoint-breadcrumb", "service": MATCH, "mode": MATCH}, "children"),
        Input({"type": "sharepoint-state-store", "service": MATCH, "mode": MATCH}, "data"),
    )
    def render_sharepoint_breadcrumbs(state_data):
        if not state_data or not state_data.get("drive_id"):
            return html.P("Not browsing yet.", className="govuk-body")
        return html.P(_format_breadcrumbs(state_data), className="govuk-body govuk-!-margin-top-1")

    @app.callback(
        Output({"type": "sharepoint-folder-back", "service": MATCH, "mode": MATCH}, "disabled"),
        Input({"type": "sharepoint-state-store", "service": MATCH, "mode": MATCH}, "data"),
    )
    def set_back_disabled(state_data):
        breadcrumbs = (state_data or {}).get("breadcrumbs", [])
        return not bool(breadcrumbs)

    @app.callback(
        Output({"type": "sharepoint-download", "service": MATCH, "mode": MATCH}, "data"),
        Input(
            {"type": "sharepoint-file-download", "service": MATCH, "mode": MATCH, "item": ALL},
            "n_clicks",
        ),
        State({"type": "sharepoint-items-store", "service": MATCH, "mode": MATCH}, "data"),
        State({"type": "sharepoint-state-store", "service": MATCH, "mode": MATCH}, "data"),
        prevent_initial_call=True,
    )
    def download_sharepoint_file(n_clicks, items_store, state_data):
        if not n_clicks or max((count or 0) for count in n_clicks) == 0:
            # The callback can trigger when the list of buttons is created; don't download anything then.
            raise PreventUpdate
        if not dash.callback_context.triggered:
            raise PreventUpdate
        triggered = dash.callback_context.triggered[0]
        component_id = json.loads(triggered["prop_id"].split(".")[0])
        drive_id = component_id.get("drive")
        item_id = component_id.get("item")
        if not item_id:
            raise PreventUpdate
        items = items_store.get("items", []) if items_store else []
        file_name = next((item.get("name") for item in items if item.get("id") == item_id), "download.xlsx")
        drive_id = drive_id or (state_data or {}).get("drive_id")
        if not drive_id:
            raise PreventUpdate
        success, payload = download_sharepoint_item(drive_id, item_id)
        if not success:
            raise PreventUpdate
        return dcc.send_bytes(lambda buffer: buffer.write(payload), file_name)
