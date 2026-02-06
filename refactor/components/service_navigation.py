from __future__ import annotations

from dash import dcc, html


def _normalize_path(path: str | None) -> str:
    if not path:
        return "/"
    trimmed = path.rstrip("/")
    return trimmed if trimmed else "/"


def _build_nav_item(page: dict, active_path: str):
    is_active = _normalize_path(page["path"]) == active_path
    classes = "govuk-service-navigation__item"
    if is_active:
        classes += " govuk-service-navigation__item--active"

    link_children = (
        html.Strong(
            className="govuk-service-navigation__active-fallback",
            children=page["label"],
        )
        if is_active
        else page["label"]
    )

    return html.Li(
        className=classes,
        children=[
            dcc.Link(
                href=page["path"],
                className="govuk-service-navigation__link",
                children=link_children,
            )
        ],
    )


def build_service_navigation(
    nav_items: list[dict],
    active_path: str = "/",
    class_name: str = "govuk-service-navigation",
):
    normalized = _normalize_path(active_path)
    return html.Div(
        className=class_name,
        children=[
            html.Div(
                className="govuk-width-container",
                children=[
                    html.Div(
                        className="govuk-service-navigation__container",
                        children=[
                            html.Nav(
                                className="govuk-service-navigation__wrapper",
                                children=[
                                    html.Button(
                                        type="button",
                                        className="govuk-service-navigation__toggle govuk-js-service-navigation-toggle",
                                        hidden=True,
                                        children="Menu",
                                    ),
                                    html.Ul(
                                        className="govuk-service-navigation__list",
                                        id="navigation",
                                        children=[
                                            _build_nav_item(page, normalized) for page in nav_items
                                        ],
                                    ),
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )
