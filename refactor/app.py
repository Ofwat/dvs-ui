import base64
import tempfile
from pathlib import Path
import sys

import os

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import dash
from dash import Input, Output, State, MATCH, dcc, dash_table, html
from dash.exceptions import PreventUpdate

from components.header import build_header
from components.service_navigation import build_service_navigation
from pages import DEFAULT_PAGE, get_navigation_pages, get_page_by_path
from pages.data_validation_service import register_online_mode_callbacks
from pages.dimension_loader_service import register_dimension_loader_callbacks
from pages.interim_solution_service import register_interim_solution_callbacks
from template_utils import build_index_string
from env_utils import load_env


load_env()
app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.server.secret_key = os.getenv("DASH_SECRET_KEY", "replace-me-with-strong-secret")
app.server.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=False,
)
app.index_string = build_index_string()

initial_nav_items = get_navigation_pages()
initial_page = DEFAULT_PAGE

app.layout = html.Div(
    className="refactor-app",
    children=[
        dcc.Location(id="url", refresh=False),
        build_header(),
        html.Div(
            id="service-navigation-wrapper",
            className=initial_page["nav_class"],
            children=[
                html.Div(
                    id="service-navigation",
                    children=build_service_navigation(
                        initial_nav_items,
                        active_path=initial_page["path"],
                        class_name=initial_page["nav_class"],
                    ),
                ),
            ],
        ),
        html.Div(id="masthead-slot", children=initial_page["masthead"]),
        html.Main(
            id="main-content",
            className="govuk-width-container app-width-container",
            children=initial_page["content"],
        ),
    ],
)

register_online_mode_callbacks(app)
register_dimension_loader_callbacks(app)
register_interim_solution_callbacks(app)


@app.callback(
    Output("service-navigation-wrapper", "className"),
    Output("service-navigation", "children"),
    Output("masthead-slot", "children"),
    Output("main-content", "children"),
    Input("url", "pathname"),
    Input("url", "search"),
)
def render_page(pathname: str | None, search: str | None):
    page = get_page_by_path(pathname)
    nav_children = build_service_navigation(
        get_navigation_pages(),
        active_path=page["path"],
        class_name=page["nav_class"],
    )
    return (
        page["nav_class"],
        nav_children,
        page["masthead"],
        page["content"],
    )


def save_upload_content(upload_data: dict | None) -> Path | None:
    if not upload_data or "contents" not in upload_data:
        return None
    contents = upload_data["contents"]
    if not contents:
        return None

    _, b64 = contents.split(",", 1)
    decoded = base64.b64decode(b64)
    temp_dir = Path(tempfile.gettempdir()) / "dqchecks-uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(upload_data.get("filename", "upload.xlsx")).name
    role = upload_data.get("role", "uploaded")
    service_index = upload_data.get("service_index", "service").strip("/").replace("/", "_")
    dest = temp_dir / f"{service_index}_{role}_{filename}"
    dest.write_bytes(decoded)
    return dest


def df_to_table(df):
    if df.empty:
        return html.P("No issues found", className="govuk-body")

    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": col, "id": col} for col in df.columns],
        sort_action="native",
        filter_action="native",
        page_action="none",
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "left", "minWidth": "120px", "whiteSpace": "normal"},
        style_header={"fontWeight": "bold"},
    )


@app.callback(
    Output({"type": "service-upload-filename", "index": MATCH, "role": MATCH}, "children"),
    Output({"type": "service-upload-store", "index": MATCH, "role": MATCH}, "data"),
    Input({"type": "service-detail-upload", "index": MATCH, "role": MATCH}, "contents"),
    State({"type": "service-detail-upload", "index": MATCH, "role": MATCH}, "filename"),
    State({"type": "service-detail-upload", "index": MATCH, "role": MATCH}, "id"),
    prevent_initial_call=True,
)
def update_upload_status(contents: str | None, filename: str | None, upload_id: dict | None):
    if not contents or not upload_id:
        raise PreventUpdate

    filename_text = filename or "Unnamed file"
    return (
        filename_text,
        {
            "contents": contents,
            "filename": filename_text,
            "service_index": upload_id.get("index"),
            "role": upload_id.get("role"),
        },
    )


@app.callback(
    Output({"type": "union-table", "index": MATCH}, "children"),
    Input({"type": "service-upload-store", "index": MATCH, "role": "template"}, "data"),
    Input({"type": "service-upload-store", "index": MATCH, "role": "final"}, "data"),
)
def update_union_table(template_data, final_data):
    if not template_data or not final_data:
        raise PreventUpdate

    # Defer the workbook-validation stack until the offline upload flow actually needs it.
    from example_dqchecks import perform_checks

    template_path = save_upload_content(template_data)
    final_path = save_upload_content(final_data)
    if not template_path or not final_path:
        raise PreventUpdate

    union_df = perform_checks(template_path, final_path)
    return df_to_table(union_df)




if __name__ == "__main__":
    app.run(debug=True)
