from dash import html


def build_get_started_content():
    return html.Div(
        className="govuk-grid-row govuk-!-margin-bottom-6",
        children=[
            html.Div(
                className="govuk-grid-column-full govuk-!-margin-top-6",
                children=[
                    html.H2("Get started", className="govuk-heading-l"),
                    html.P(
                        "Follow these steps to build your Ocean dashboard.",
                        className="govuk-body",
                    ),
                    html.Ol(
                        className="govuk-list govuk-list--number",
                        children=[
                            html.Li(
                                "Review the GOV.UK Ocean guidance.",
                                className="govuk-body",
                            ),
                            html.Li(
                                "Draft your layout on paper or in code.",
                                className="govuk-body",
                            ),
                            html.Li(
                                "Add the Iris visualisation once the data is ready.",
                                className="govuk-body",
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
