from __future__ import annotations

from .get_started import build_get_started_content
from .home import build_home_content, build_home_masthead
from .services import build_services_content
from .dimension_loader_service import (
    DIMENSION_LOADER_SERVICE,
    build_dimension_loader_content,
)
from .interim_solution_service import (
    INTERIM_SOLUTION_SERVICE,
    build_interim_solution_content,
)
from .data_validation_service import (
    SERVICE_DETAIL_PAGES,
    build_mode_detail,
    build_service_detail_content,
)


_BASE_DEFINITIONS = [
    {
        "label": "Home",
        "path": "/",
        "content": build_home_content,
        "masthead": build_home_masthead,
        "nav": False,
        "nav_class": "govuk-service-navigation govuk-service-navigation--inverse app-service-navigation",
    },
    {
        "label": "Get started",
        "path": "/get-started",
        "content": build_get_started_content,
        "nav": True,
        "nav_class": "govuk-service-navigation app-service-navigation",
    },
    {
        "label": "Services",
        "path": "/services",
        "content": build_services_content,
        "nav": True,
        "nav_class": "govuk-service-navigation app-service-navigation",
    },
]

_DETAIL_PAGE_DEFINITIONS = []
_DETAIL_PAGE_DEFINITIONS.append(
    {
        "label": DIMENSION_LOADER_SERVICE["label"],
        "path": DIMENSION_LOADER_SERVICE["path"],
        "content": build_dimension_loader_content,
        "nav": False,
        "nav_class": "govuk-service-navigation app-service-navigation",
    }
)
_DETAIL_PAGE_DEFINITIONS.append(
    {
        "label": INTERIM_SOLUTION_SERVICE["label"],
        "path": INTERIM_SOLUTION_SERVICE["path"],
        "content": build_interim_solution_content,
        "nav": False,
        "nav_class": "govuk-service-navigation app-service-navigation",
    }
)
for service in SERVICE_DETAIL_PAGES:
    _DETAIL_PAGE_DEFINITIONS.append(
        {
            "label": service["label"],
            "path": service["path"],
            "content": lambda service=service: build_service_detail_content(service),
            "nav": False,
            "nav_class": "govuk-service-navigation app-service-navigation",
        }
    )
    for mode in service.get("modes", []):
        _DETAIL_PAGE_DEFINITIONS.append(
            {
                "label": f"{service['label']} - {mode['label']}",
                "path": f"{service['path']}/{mode['slug']}",
                "content": lambda service=service, mode=mode: build_mode_detail(service, mode),
                "nav": False,
                "nav_class": "govuk-service-navigation app-service-navigation",
            }
        )

_DEFINITIONS = _BASE_DEFINITIONS + _DETAIL_PAGE_DEFINITIONS


def _normalize_path(pathname: str | None) -> str:
    if not pathname:
        return "/"
    trimmed = pathname.rstrip("/")
    return trimmed if trimmed else "/"


def _build_page_payload(definition: dict) -> dict:
    masthead_builder = definition.get("masthead")
    masthead_children = [masthead_builder()] if masthead_builder else []
    return {
        "label": definition["label"],
        "path": definition["path"],
        "content": definition["content"](),
        "masthead": masthead_children,
        "nav_class": definition.get("nav_class", "govuk-service-navigation app-service-navigation"),
    }


def get_page_by_path(pathname: str | None) -> dict:
    normalized = _normalize_path(pathname)
    for definition in _DEFINITIONS:
        if definition["path"] == normalized:
            return _build_page_payload(definition)
    return _build_page_payload(_DEFINITIONS[0])


def get_navigation_pages() -> list[dict]:
    return [
        {"label": definition["label"], "path": definition["path"]}
        for definition in _DEFINITIONS
        if definition.get("nav", False)
    ]


DEFAULT_PAGE = get_page_by_path(None)
