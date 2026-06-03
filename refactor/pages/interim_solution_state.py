from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


CONFIG_PATH = Path.home() / ".dvs-ui" / "services" / "interim-solution" / "config.json"
REQUIRED_ENV_VARS = ("SHAREPOINT_HOST", "SHAREPOINT_SITE_PATH")
ENV_PREFIXES = {"[DEV]": "dev", "[PROD]": "prod"}


def get_missing_env_vars(env: Mapping[str, str] | None = None) -> list[str]:
    current_env = env or os.environ
    return [name for name in REQUIRED_ENV_VARS if not str(current_env.get(name, "")).strip()]


def load_service_config(config_path: Path = CONFIG_PATH) -> tuple[dict[str, Any] | None, str | None]:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        return None, (
            f"No interim solution config found at {config_path}. "
            "Create the config file or point the service at a different config."
        )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pylint:disable=broad-except
        return None, f"Unable to read interim solution config at {config_path}: {exc}"
    if not isinstance(payload, dict):
        return None, f"Interim solution config at {config_path} must contain a JSON object."
    return payload, None


def _job_environment(job: dict[str, Any]) -> str | None:
    name = str(job.get("name", "")).strip()
    for prefix, env_name in ENV_PREFIXES.items():
        if name.upper().startswith(prefix):
            return env_name
    return None


def _job_source_folder_url(job: dict[str, Any]) -> str:
    folder_url = str(job.get("source_folder_url", "")).strip()
    if folder_url:
        return folder_url
    source_links = list(job.get("source_links", []) or [])
    return str(source_links[0]).strip() if source_links else ""


def _is_interim_job(job: dict[str, Any]) -> bool:
    if not isinstance(job, dict):
        return False
    source_kind = str(job.get("source_kind", "")).strip().lower()
    if source_kind != "sharepoint":
        return False
    if not _job_source_folder_url(job):
        return False
    target_root = str(job.get("target_root", "")).strip()
    return bool(target_root)


def get_interim_jobs_by_environment(config: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"dev": [], "prod": []}
    for job in get_interim_jobs(config):
        environment = _job_environment(job)
        if not environment:
            continue
        fabric = job.get("fabric", {}) if isinstance(job.get("fabric"), dict) else {}
        grouped[environment].append(
            {
                "name": str(job.get("name", "")).strip(),
                "enabled": bool(job.get("enabled", True)),
                "workspace_display_name": str(fabric.get("workspace_display_name", "")).strip(),
                "lakehouse_display_name": str(fabric.get("lakehouse_display_name", "")).strip(),
                "source_folder_url": _job_source_folder_url(job),
                "target_root": str(job.get("target_root", "")).strip(),
            }
        )
    return grouped


def get_interim_jobs(config: dict[str, Any] | None, environment: str | None = None) -> list[dict[str, Any]]:
    jobs = list((config or {}).get("jobs", []) or [])
    selected_env = environment if environment in {"dev", "prod"} else None
    selected_jobs: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict) or not _is_interim_job(job):
            continue
        if selected_env and _job_environment(job) != selected_env:
            continue
        selected_jobs.append(job)
    return selected_jobs
