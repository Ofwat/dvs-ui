from dash import html

try:
    from components.button_start import build_start_button
except ImportError:  # pragma: no cover - lets tests import the package form.
    from refactor.components.button_start import build_start_button


APP_HERO_DESCRIPTION = (
    "Use this design system to make government services consistent with GOV.UK. "
    "Learn from the research and experience of other service teams and avoid repeating work that has already been done."
)


def _section_container(children):
    return html.Div(
        className="govuk-main-wrapper govuk-main-wrapper--l",
        children=[html.Div(className="govuk-grid-row", children=children)],
    )


def _highlight_card(title, body, href, link_text):
    return html.Div(
        className="govuk-grid-column-one-third-from-desktop",
        children=[
            html.H2(title, className="govuk-heading-l"),
            html.P(body, className="govuk-body"),
            html.P(
                className="govuk-body govuk-!-margin-bottom-0",
                children=[
                    html.A(link_text, href=href, className="govuk-link govuk-!-font-weight-bold")
                ],
            ),
        ],
    )


def build_home_masthead():
    return html.Div(
        className="app-masthead",
        children=[
            html.Div(
                className="app-width-container",
                children=[
                    html.Div(
                        className="govuk-grid-row",
                        children=[
                            html.Div(
                                className="govuk-grid-column-two-thirds-from-desktop",
                                children=[
                                    html.H1(
                                        "A unified platform for submitting data, validating its quality, and supporting ongoing analysis",
                                        className="govuk-heading-xl app-masthead__title",
                                    ),
                                    html.P(APP_HERO_DESCRIPTION, className="app-masthead__description"),
                                    build_start_button(address="/get-started"),
                                ],
                            ),
                            html.Div(
                                className="govuk-grid-column-one-third-from-desktop",
                                children=[
                                    html.Div(
                                        className="app-hero-illustration",
                                        children=[
                                            html.Div(className="app-hero-illustration__circle"),
                                            html.Div(className="app-hero-illustration__grid"),
                                        ],
                                    )
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )


def build_whats_new_section():
    return html.Div(
        className="app-whats-new",
        children=[
            html.Div(
                className="app-width-container",
                children=_section_container(
                    [
                        html.Div(
                            className="govuk-grid-column-two-thirds-from-desktop",
                            children=[
                                html.H2("What's new", className="govuk-heading-l"),
                                html.H3(
                                    "14 January 2026: GOV.UK Frontend 5.14.0 is out",
                                    className="govuk-heading-s",
                                ),
                                html.P(
                                    "This release lets you drop content licence references if you don't publish under the OGL.",
                                    className="govuk-body",
                                ),
                                html.P(
                                    "It also fixes the VoiceOver bug in the service navigation toggle.",
                                    className="govuk-body",
                                ),
                                html.P(
                                    className="govuk-body",
                                    children=[
                                        html.A(
                                            "Read the release notes for v5.14.0",
                                            href="https://github.com/alphagov/govuk-frontend/releases/tag/v5.14.0",
                                            className="govuk-link",
                                        ),
                                        " or ",
                                        html.A(
                                            "see the latest updates",
                                            href="/community/whats-new/",
                                            className="govuk-link",
                                        ),
                                        ".",
                                    ],
                                ),
                                html.H2("Stay in the loop", className="govuk-heading-l"),
                                html.P(
                                    "Want change alerts or event news from the Design System team?",
                                    className="govuk-body",
                                ),
                                html.P(
                                    className="govuk-body",
                                    children=[
                                        html.A(
                                            "Sign up with your email",
                                            href="http://mailchi.mp/707ce8dec373/get-updated-by-email-govuk-design-system",
                                            className="govuk-link",
                                        )
                                    ],
                                ),
                            ],
                        )
                    ]
                ),
            ),
        ],
    )


def build_feature_grid():
    return html.Div(
        className="app-width-container",
        children=_section_container(
            [
                _highlight_card(
                    "Styles",
                    "Make government services look like GOV.UK with layout, typography, colour and imagery guidance.",
                    "/styles/",
                    "Browse our styles",
                ),
                _highlight_card(
                    "Components",
                    "Save time with reusable, accessible navigation, forms and dashboards.",
                    "/components/",
                    "Browse our components",
                ),
                _highlight_card(
                    "Patterns",
                    "Guide people through common journeys such as form-filling and account creation.",
                    "/patterns/",
                    "Browse our patterns",
                ),
            ]
        ),
    )


def build_brand_and_principles():
    return html.Div(
        className="app-width-container",
        children=_section_container(
            [
                html.Div(
                    className="govuk-grid-column-two-thirds-from-desktop",
                    children=[
                        html.H2("Use the refreshed GOV.UK brand", className="govuk-heading-l"),
                        html.P(
                            "In 2025 GOV.UK refreshed its brand. Several GOV.UK Frontend releases support teams modernising services.",
                            className="govuk-body",
                        ),
                        html.P(
                            className="govuk-body",
                            children=[
                                html.A(
                                    "Find out how to update your service",
                                    href="https://frontend.design-system.service.gov.uk/brand-refresh-changes/#updating-your-service-to-use-the-new-brand",
                                    className="govuk-link",
                                )
                            ],
                        ),
                        html.Hr(className="govuk-section-break govuk-section-break--visible govuk-section-break--xl"),
                        html.H2("Principles we follow", className="govuk-heading-l"),
                        html.P(
                            className="govuk-body",
                            children=[
                                "The GOV.UK Design System follows the ",
                                html.A(
                                    "Government Design Principles",
                                    href="https://www.gov.uk/guidance/government-design-principles",
                                    className="govuk-link",
                                ),
                                " and the ",
                                html.A(
                                    "GOV.UK Service Manual",
                                    href="https://www.gov.uk/service-manual",
                                    className="govuk-link",
                                ),
                                ".",
                            ],
                        ),
                        html.P(
                            className="govuk-body",
                            children=[
                                html.A(
                                    "Explore our accessibility strategy",
                                    href="/accessibility/accessibility-strategy/",
                                    className="govuk-link",
                                )
                            ],
                        ),
                    ],
                )
            ]
        ),
    )


def build_home_content():
    return html.Div(
        children=[
            build_whats_new_section(),
            build_feature_grid(),
            build_brand_and_principles(),
        ],
    )
