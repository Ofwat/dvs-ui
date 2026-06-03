from dash import dcc, html

from .dimension_loader_service import DIMENSION_LOADER_SERVICE
from .interim_solution_service import INTERIM_SOLUTION_SERVICE
from .data_validation_service import SERVICE_DETAIL_PAGES


def build_services_content():
    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full govuk-!-margin-top-6",
                children=[
                    html.H3("Services catalog", className="govuk-heading-m"),
                    html.P(
                        "The Data Validation Service accepts an XLSX file and runs Ocean schema checks "
                        "before your dashboards go live.",
                        className="govuk-body",
                    ),
                ],
            ),
            *[
                html.Div(
                    className="govuk-grid-column-full govuk-!-margin-bottom-5",
                    children=[
                        html.H3(service["label"], className="govuk-heading-m"),
                        html.P(service["summary"], className="govuk-body"),
                        dcc.Link(
                            "View service details",
                            href=service["path"],
                            className="govuk-link",
                        ),
                    ],
                )
                for service in SERVICE_DETAIL_PAGES
            ],
            html.Div(
                className="govuk-grid-column-full govuk-!-margin-bottom-5",
                children=[
                    html.H3(DIMENSION_LOADER_SERVICE["label"], className="govuk-heading-m"),
                    html.P(DIMENSION_LOADER_SERVICE["summary"], className="govuk-body"),
                    dcc.Link(
                        "View service details",
                        href=DIMENSION_LOADER_SERVICE["path"],
                        className="govuk-link",
                    ),
                ],
            ),
            html.Div(
                className="govuk-grid-column-full govuk-!-margin-bottom-5",
                children=[
                    html.H3(INTERIM_SOLUTION_SERVICE["label"], className="govuk-heading-m"),
                    html.P(INTERIM_SOLUTION_SERVICE["summary"], className="govuk-body"),
                    dcc.Link(
                        "View service details",
                        href=INTERIM_SOLUTION_SERVICE["path"],
                        className="govuk-link",
                    ),
                ],
            ),
        ],
    )
