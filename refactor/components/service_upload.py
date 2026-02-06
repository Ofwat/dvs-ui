from dash import dcc, html


def build_service_upload_block(label: str, upload_id: dict, filename_id: dict) -> html.Div:
    """Return the upload form group with GOV.UK styling."""

    return html.Div(
        className="govuk-form-group",
        children=[
            html.Label(label, className="govuk-label"),
            dcc.Upload(
                id=upload_id,
                className="govuk-file-upload",
                children=[
                    html.Span("Choose File", className="govuk-button govuk-button--secondary"),
                    html.Span(
                        "No file chosen",
                        className="govuk-body govuk-!-margin-left-3",
                        id=filename_id,
                    ),
                ],
                multiple=False,
                accept=".xlsx",
            ),
        ],
    )
