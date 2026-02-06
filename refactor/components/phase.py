# <div class="govuk-phase-banner">
#   <p class="govuk-phase-banner__content">
#     <strong class="govuk-tag govuk-phase-banner__content__tag">
#       Alpha
#     </strong>
#     <span class="govuk-phase-banner__text">
#       This is a new service. Help us improve it and <a class="govuk-link" href="#">give your feedback by email</a>.
#     </span>
#   </p>
# </div>

from dash import html

def build_phase(phase_state:str = "Alpha", email="#"):
  return html.Div(
    className="govuk-phase-banner app-width-container",
    children=[
        html.P(
            className="govuk-phase-banner__content",
            children=[
                html.Strong(
                    className="govuk-tag govuk-phase-banner__content__tag",
                    children=phase_state
                ),
                html.Span(
                    className="govuk-phase-banner__text",
                    children=[
                        "This is a new service. Help us improve it and ",
                        html.A(
                            className="govuk-link",
                            href=email,
                            children="give your feedback by email"
                        )
                    ]
                )
            ]
        )
    ]
)
