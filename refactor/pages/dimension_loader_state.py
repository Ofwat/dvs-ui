from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


CONFIG_PATH = Path.home() / ".dvs-ui" / "services" / "dimensions-loader" / "config.json"
REQUIRED_ENV_VARS = ("SHAREPOINT_HOST", "SHAREPOINT_SITE_PATH")
DIMENSIONS_TARGET_PREFIX = "Files/dimensions/"
ENV_PREFIXES = {"[DEV]": "dev", "[PROD]": "prod"}


def get_missing_env_vars(env: Mapping[str, str] | None = None) -> list[str]:
    current_env = env or os.environ
    return [name for name in REQUIRED_ENV_VARS if not str(current_env.get(name, "")).strip()]


def load_service_config(config_path: Path = CONFIG_PATH) -> tuple[dict[str, Any] | None, str | None]:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        return None, (
            f"No dimensions loader config found at {config_path}. "
            "Create the config file or point the service at a different config."
        )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pylint:disable=broad-except
        return None, f"Unable to read dimensions loader config at {config_path}: {exc}"
    if not isinstance(payload, dict):
        return None, f"Dimensions loader config at {config_path} must contain a JSON object."
    return payload, None


def _job_name(job: dict[str, Any], index: int) -> str:
    return str(job.get("name", "")).strip() or f"Job {index}"


def _job_environment(job: dict[str, Any]) -> str | None:
    name = str(job.get("name", "")).strip()
    for prefix, env_name in ENV_PREFIXES.items():
        if name.upper().startswith(prefix):
            return env_name
    return None


def _is_dimension_job(job: dict[str, Any]) -> bool:
    if not isinstance(job, dict):
        return False
    source_kind = str(job.get("source_kind", "")).strip().lower()
    if source_kind != "sharepoint":
        return False
    target_root = str(job.get("target_root", "")).strip()
    return target_root.startswith(DIMENSIONS_TARGET_PREFIX)


def get_dimension_jobs_by_environment(config: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"dev": [], "prod": []}
    for index, job in enumerate(get_dimension_jobs(config), start=1):
        environment = _job_environment(job)
        if not environment:
            continue
        fabric = job.get("fabric", {})
        if not isinstance(fabric, dict):
            fabric = {}
        grouped[environment].append(
            {
                "name": _job_name(job, index),
                "enabled": bool(job.get("enabled", True)),
                "workspace_display_name": str(fabric.get("workspace_display_name", "")).strip(),
                "lakehouse_display_name": str(fabric.get("lakehouse_display_name", "")).strip(),
                "source_links": list(job.get("source_links", []) or []),
                "target_root": str(job.get("target_root", "")).strip(),
                "mappings": list(job.get("mappings", []) or []),
            }
        )
    return grouped


def get_dimension_jobs(config: dict[str, Any] | None, environment: str | None = None) -> list[dict[str, Any]]:
    jobs = list((config or {}).get("jobs", []) or [])
    selected_env = environment if environment in {"dev", "prod"} else None
    selected_jobs: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict) or not _is_dimension_job(job):
            continue
        if selected_env and _job_environment(job) != selected_env:
            continue
        selected_jobs.append(job)
    return selected_jobs


def get_dimension_pipeline_config(config: dict[str, Any] | None, environment: str | None = None) -> dict[str, Any]:
    pipelines = (config or {}).get("pipelines", {})
    if not isinstance(pipelines, dict):
        return {}
    selected_env = environment if environment in {"dev", "prod"} else None
    if not selected_env:
        return {}
    pipeline = pipelines.get(selected_env, {})
    if not isinstance(pipeline, dict):
        return {}
    return {
        "workspace_display_name": str(pipeline.get("workspace_display_name", "")).strip(),
        "pipeline_display_name": str(pipeline.get("pipeline_display_name", "")).strip(),
        "parameters": dict(pipeline.get("parameters", {}) or {}),
    }


def build_config_missing_message(config_error: str | None) -> list[str]:
    if not config_error:
        return []
    return [config_error]


def build_env_missing_message(missing_vars: list[str]) -> list[str]:
    if not missing_vars:
        return []
    vars_text = ", ".join(missing_vars)
    return [
        "Dimensions Loader is not configured yet.",
        f"Missing environment values: {vars_text}.",
        "Add them to .env and reload the app.",
    ]
