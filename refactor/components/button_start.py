from dash import Dash, dcc, html
import dash_svg as svg


def build_start_button(text="Get started", address="#"):
    return dcc.Link(
        href=address,
        className="govuk-button govuk-button--inverse govuk-!-margin-top-6 govuk-!-margin-bottom-0 govuk-button--start",
        children=[
            html.Span(text),
            svg.Svg(
                className="govuk-button__start-icon",
                xmlns="http://www.w3.org/2000/svg",
                width="17.5",
                height="19",
                viewBox="0 0 33 40",
                **{
                    "aria-hidden": "true",
                    # "focusable": "false",
                },
                children=[
                    svg.Path(
                        fill="currentColor",
                        d="M0 0h13l20 20-20 20H0l20-20z",
                    )
                ],
            ),
        ],
    )
