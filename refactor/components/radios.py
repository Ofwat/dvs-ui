from dash import dcc, html


def build_radio_group(
    legend: str,
    radio_id: str,
    options: list[dict[str, str]],
    value: str | None = None,
) -> html.Div:
    return html.Div(
        className="govuk-form-group",
        children=[
            html.Fieldset(
                className="govuk-fieldset",
                children=[
                    html.Legend(
                        className="govuk-fieldset__legend govuk-fieldset__legend--m",
                        children=legend,
                    ),
                    dcc.RadioItems(
                        id=radio_id,
                        options=options,
                        value=value,
                        className="govuk-radios",
                        inputClassName="govuk-radios__input",
                        labelClassName="govuk-label govuk-radios__label",
                        inline=False,
                    ),
                ],
            )
        ],
    )
