from dash import html


def build_phase(
    phase_state: str = "Alpha",
    feedback_href: str = "mailto:team@example.com",
    feedback_text: str = "give your feedback by email",
):
    return html.Div(
        className="govuk-phase-banner app-width-container",
        children=[
            html.P(
                className="govuk-phase-banner__content",
                children=[
                    html.Strong(
                        className="govuk-tag govuk-phase-banner__content__tag",
                        children=phase_state,
                    ),
                    html.Span(
                        className="govuk-phase-banner__text",
                        children=[
                            "This is a new service. Help us improve it and ",
                            html.A(className="govuk-link", href=feedback_href, children=feedback_text),
                            ".",
                        ],
                    ),
                ],
            )
        ],
    )
