from __future__ import annotations

from dash import dcc, html


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
    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full",
                children=[
                    html.H2(DIMENSION_LOADER_SERVICE["label"], className="govuk-heading-l"),
                    html.P(DIMENSION_LOADER_SERVICE["summary"], className="govuk-body"),
                    html.P(DIMENSION_LOADER_SERVICE["description"], className="govuk-body"),
                    html.Ul(
                        className="govuk-list govuk-list--bullet govuk-!-margin-bottom-4",
                        children=[
                            html.Li(item, className="govuk-body")
                            for item in DIMENSION_LOADER_SERVICE["highlights"]
                        ],
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

