from __future__ import annotations

from collections.abc import Sequence

from dash import html


def _as_children(content):
    if content is None:
        return []
    if isinstance(content, (str, int, float, bool)):
        return [str(content)]
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        return list(content)
    return [content]


def _build_action_link(action: dict) -> html.A:
    children = [action.get("label", "")]
    visually_hidden_text = str(action.get("visually_hidden_text") or "").strip()
    if visually_hidden_text:
        children.append(html.Span(f" {visually_hidden_text}", className="govuk-visually-hidden"))
    return html.A(
        children,
        href=action.get("href", "#"),
        className=action.get("className", "govuk-link"),
    )


def build_summary_list_row(
    key,
    value,
    actions: list[dict] | None = None,
    classes: str | None = None,
) -> html.Div:
    row_children = [
        html.Dt(_as_children(key), className="govuk-summary-list__key"),
        html.Dd(_as_children(value), className="govuk-summary-list__value"),
    ]
    if actions:
        row_children.append(
            html.Dd(
                children=[
                    _build_action_link(action)
                    for action in actions
                ],
                className="govuk-summary-list__actions",
            )
        )
    return html.Div(
        className="govuk-summary-list__row" + (f" {classes}" if classes else ""),
        children=row_children,
    )


def build_summary_list(
    rows: list[dict],
    classes: str | None = None,
) -> html.Dl:
    return html.Dl(
        className="govuk-summary-list" + (f" {classes}" if classes else ""),
        children=[
            build_summary_list_row(
                row["key"],
                row["value"],
                actions=row.get("actions"),
                classes=row.get("classes"),
            )
            for row in rows
        ],
    )


def build_summary_card(
    title,
    rows: list[dict],
    actions: list[dict] | None = None,
    classes: str | None = None,
) -> html.Div:
    title_wrapper_children = [
        html.H2(title, className="govuk-summary-card__title")
    ]
    if actions:
        title_wrapper_children.append(
            html.Ul(
                className="govuk-summary-card__actions",
                children=[
                    html.Li(
                        className="govuk-summary-card__action",
                        children=[_build_action_link(action)],
                    )
                    for action in actions
                ],
            )
        )

    return html.Div(
        className="govuk-summary-card" + (f" {classes}" if classes else ""),
        children=[
            html.Div(
                className="govuk-summary-card__title-wrapper",
                children=title_wrapper_children,
            ),
            html.Div(
                className="govuk-summary-card__content",
                children=[build_summary_list(rows)],
            ),
        ],
    )
