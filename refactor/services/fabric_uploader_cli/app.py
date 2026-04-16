from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any
from urllib.parse import quote

ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_CONFIG_PATH = Path(__file__).with_name("local_config.json")
MAX_TRANSFER_WORKERS = 4
_PROGRESS_LOCK = threading.Lock()


def _questionary():
    try:
        import questionary
    except ImportError as exc:
        raise RuntimeError(
            "The interactive CLI requires 'questionary'. Install dependencies from requirements.txt."
        ) from exc
    return questionary


def _common():
    from refactor.services.data_validation_api.examples import common

    return common


def _online_auth():
    from refactor import online_auth

    return online_auth


def _workflows():
    from refactor.services.data_validation_api import workflows

    return workflows


def _select(message: str, choices: list[Any], default: Any | None = None) -> Any:
    q = _questionary()
    normalized_choices: list[Any] = []
    for item in choices:
        if isinstance(item, dict) and "value" in item and "name" in item:
            normalized_choices.append(q.Choice(title=str(item["name"]), value=item["value"]))
            continue
        normalized_choices.append(item)
    return q.select(message, choices=normalized_choices, default=default, use_indicator=True).ask()


def _checkbox(message: str, choices: list[Any]) -> list[Any]:
    q = _questionary()
    normalized_choices: list[Any] = []
    for item in choices:
        if isinstance(item, dict) and "value" in item and "name" in item:
            normalized_choices.append(q.Choice(title=str(item["name"]), value=item["value"]))
            continue
        normalized_choices.append(item)
    return q.checkbox(message, choices=normalized_choices).ask() or []


def _text(message: str, default: str | None = None) -> str:
    q = _questionary()
    value = q.text(message, default=default or "").ask()
    return str(value or "").strip()


def _confirm(message: str, default: bool = False) -> bool:
    q = _questionary()
    return bool(q.confirm(message, default=default).ask())


def _print_json(payload: Any):
    print(json.dumps(payload, indent=2), flush=True)


def _print_error(message: str):
    print(f"\nError: {message}", flush=True)


def _prompt_required_text(message: str, default: str | None = None, error_message: str | None = None) -> str:
    while True:
        value = _text(message, default)
        if value:
            return value
        _print_error(error_message or f"{message} is required.")


def _format_bytes(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class _TransferProgress:
    def __init__(self, label: str, total_bytes: int | None):
        self.label = label
        self.total_bytes = total_bytes
        self.start = time.time()
        self.last_render = 0.0

    def update(self, completed_bytes: int, *, force: bool = False):
        now = time.time()
        if not force and completed_bytes != self.total_bytes and now - self.last_render < 0.5:
            return
        self.last_render = now
        elapsed = max(now - self.start, 0.001)
        speed = completed_bytes / elapsed
        if self.total_bytes:
            remaining_bytes = max(self.total_bytes - completed_bytes, 0)
            eta_seconds = remaining_bytes / speed if speed > 0 else None
            percent = min(max(completed_bytes / self.total_bytes, 0.0), 1.0)
            width = 24
            filled = min(int(percent * width), width)
            bar = "#" * filled + "-" * (width - filled)
            line = (
                f"{self.label} [{bar}] {percent * 100:6.2f}% "
                f"{_format_bytes(completed_bytes)}/{_format_bytes(self.total_bytes)} "
                f"remaining {_format_bytes(remaining_bytes)} "
                f"ETA {_format_duration(eta_seconds)} "
                f"{_format_bytes(speed)}/s"
            )
        else:
            line = (
                f"{self.label} {_format_bytes(completed_bytes)} "
                f"ETA {_format_duration(None)} "
                f"{_format_bytes(speed)}/s"
            )
        with _PROGRESS_LOCK:
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()

    def finish(self, completed_bytes: int):
        self.update(completed_bytes, force=True)


def _load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json_file(path: Path, payload: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _serializable_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "jobs": [
            {
                key: value
                for key, value in job.items()
                if not str(key).startswith("_")
            }
            for job in list(config.get("jobs", []) or [])
            if isinstance(job, dict)
        ],
    }


def _normalize_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    job_fabric = dict(job.get("fabric", {})) if isinstance(job.get("fabric"), dict) else {}
    return {
        **_default_job_payload(str(job.get("source_kind", "")).strip() or "local"),
        **job,
        "fabric": {
            "workspace_display_name": str(
                job_fabric.get("workspace_display_name", "")
            ),
            "lakehouse_display_name": str(
                job_fabric.get("lakehouse_display_name", "")
            ),
        },
        "mappings": [
            {
                "source_relative_path": _normalize_relative_path(mapping.get("source_relative_path", "")),
                "target_relative_path": _normalize_relative_path(mapping.get("target_relative_path", "")),
                **(
                    {"source_link": str(mapping.get("source_link", "")).strip()}
                    if str(mapping.get("source_link", "")).strip()
                    else {}
                ),
            }
            for mapping in list(job.get("mappings", []) or [])
        ],
        "source_links": [
            str(link).strip()
            for link in list(job.get("source_links", []) or [])
            if str(link).strip()
        ],
    }


def _normalize_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "jobs": [
            _normalize_job_payload(job)
            for job in list(config.get("jobs", []) or [])
            if isinstance(job, dict)
        ],
    }


def _default_config_payload() -> dict[str, Any]:
    return {
        "jobs": [],
    }


def _normalize_relative_path(value: str) -> str:
    return "/".join(segment.strip() for segment in str(value).replace("\\", "/").split("/") if segment.strip())


def _default_target_path(source_relative_path: str, target_root: str) -> str:
    return _normalize_relative_path(source_relative_path)


def _effective_target_path(target_relative_path: str, target_root: str) -> str:
    normalized_target = _normalize_relative_path(target_relative_path)
    normalized_root = _normalize_relative_path(target_root)
    if not normalized_root:
        return normalized_target
    return "/".join(part for part in [normalized_root, normalized_target] if part)


def _target_path_is_relative(target_relative_path: str, target_root: str) -> bool:
    normalized_target = _normalize_relative_path(target_relative_path)
    normalized_root = _normalize_relative_path(target_root)
    if not normalized_target:
        return False
    if not normalized_root:
        return True
    return not (
        normalized_target == normalized_root
        or normalized_target.startswith(f"{normalized_root}/")
    )


def _require_relative_target_path(target_relative_path: str, target_root: str):
    if _target_path_is_relative(target_relative_path, target_root):
        return
    raise RuntimeError(
        "Target Fabric path must be relative to the job target root, not include it: "
        f"{_normalize_relative_path(target_root)}"
    )


def _config_base_dir(config: dict[str, Any]) -> Path:
    runtime_dir = str(config.get("_config_dir", "")).strip()
    if runtime_dir:
        return Path(runtime_dir)
    return Path.cwd()


def _resolve_local_root(path_value: str, base_dir: Path | None = None) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        root = base_dir or Path.cwd()
        path = (root / path).resolve()
    return path


def _require_existing_local_root(local_root: str, base_dir: Path | None = None) -> Path:
    root = _resolve_local_root(local_root, base_dir)
    if not root.exists():
        raise RuntimeError(
            f"Local source path was not found: {root}. Use an absolute path or a path relative to the config file."
        )
    return root


def _list_local_files(local_root: str, base_dir: Path | None = None) -> list[str]:
    root = _require_existing_local_root(local_root, base_dir)
    if root.is_file():
        return [root.name]
    files = [str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*") if path.is_file()]
    return sorted(files)


def _read_local_file(local_root: str, source_relative_path: str, base_dir: Path | None = None) -> bytes:
    root = _require_existing_local_root(local_root, base_dir)
    if root.is_file():
        return root.read_bytes()
    file_path = root / Path(source_relative_path)
    return file_path.read_bytes()


def _normalize_manual_source_path(job: dict[str, Any], raw_value: str, config: dict[str, Any]) -> str:
    source_kind = str(job.get("source_kind", "")).strip()
    normalized_value = str(raw_value).strip()
    if source_kind != "local":
        return _normalize_relative_path(normalized_value)
    source_root = str(job.get("source_root", "")).strip()
    if not source_root:
        return _normalize_relative_path(normalized_value)
    root = _resolve_local_root(source_root, _config_base_dir(config))
    candidate = Path(normalized_value).expanduser()
    if not candidate.is_absolute():
        return _normalize_relative_path(normalized_value)
    candidate = candidate.resolve()
    if root.is_file():
        if candidate != root.resolve():
            raise RuntimeError(f"Source path must match configured file root: {root}")
        return root.name
    try:
        return _normalize_relative_path(str(candidate.relative_to(root.resolve())))
    except ValueError as exc:
        raise RuntimeError(f"Source path must be inside configured job root: {root}") from exc


def _manual_source_default(job: dict[str, Any], config: dict[str, Any]) -> str:
    mappings = list(job.get("mappings", []) or [])
    if not mappings:
        return ""
    return str(mappings[-1].get("source_relative_path", ""))


def _resolve_fabric_destination(job: dict[str, Any]) -> tuple[str, str]:
    common = _common()
    auth = _online_auth()
    fabric = job.get("fabric", {}) or {}
    workspace_name = str(fabric.get("workspace_display_name", "")).strip()
    lakehouse_name = str(fabric.get("lakehouse_display_name", "")).strip()
    if not workspace_name or not lakehouse_name:
        raise RuntimeError("Job Fabric workspace and lakehouse display names must be set.")

    workspaces_ok, workspaces_payload = auth.list_fabric_workspaces()
    common.require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    workspace = common.find_by_display_name(workspaces_payload, workspace_name, "Workspace")
    workspace_id = str(workspace["id"])

    lakehouses_ok, lakehouses_payload = auth.list_fabric_lakehouses(workspace_id)
    common.require_ok("list_fabric_lakehouses", lakehouses_ok, lakehouses_payload)
    lakehouse = common.find_by_display_name(lakehouses_payload, lakehouse_name, "Lakehouse")
    return workspace_id, str(lakehouse["id"])


def _default_job_payload(source_kind: str) -> dict[str, Any]:
    return {
        "name": "",
        "enabled": True,
        "fabric": {
            "workspace_display_name": "",
            "lakehouse_display_name": "",
        },
        "source_kind": source_kind,
        "source_root": "",
        "source_links": [],
        "target_root": "Files/Uploads",
        "mappings": [],
    }


def _job_summary(job: dict[str, Any]) -> str:
    status = "enabled" if bool(job.get("enabled", True)) else "disabled"
    source_kind = str(job.get("source_kind", "")).strip() or "unknown"
    if source_kind == "sharepoint":
        source_links = list(job.get("source_links", []) or [])
        source_root = f"{len(source_links)} link(s)"
    else:
        source_root = str(job.get("source_root", "")).strip() or "-"
    fabric = job.get("fabric", {}) or {}
    fabric_target = (
        f"{str(fabric.get('workspace_display_name', '')).strip() or '?'}"
        f" / {str(fabric.get('lakehouse_display_name', '')).strip() or '?'}"
    )
    mappings = list(job.get("mappings", []) or [])
    return (
        f"{job.get('name', '<unnamed>')} [{status}] "
        f"({source_kind}, {len(mappings)} files) -> {source_root} -> {fabric_target}"
    )


def _mapping_summary(mapping: dict[str, Any]) -> str:
    source = str(mapping.get("source_relative_path", "")).strip() or "-"
    target = str(mapping.get("target_relative_path", "")).strip() or "-"
    source_link = str(mapping.get("source_link", "")).strip()
    if source_link:
        return f"{source} ({source_link}) -> {target}"
    return f"{source} -> {target}"


def _job_source_links(job: dict[str, Any]) -> list[str]:
    return [
        str(link).strip()
        for link in list(job.get("source_links", []) or [])
        if str(link).strip()
    ]


def _upsert_mapping(job: dict[str, Any], source_relative_path: str, target_relative_path: str) -> dict[str, Any]:
    normalized_source = _normalize_relative_path(source_relative_path)
    normalized_target = _normalize_relative_path(target_relative_path)
    mappings = []
    replaced = False
    for mapping in list(job.get("mappings", []) or []):
        if _normalize_relative_path(mapping.get("source_relative_path", "")) == normalized_source:
            mappings.append(
                {
                    "source_relative_path": normalized_source,
                    "target_relative_path": normalized_target,
                }
            )
            replaced = True
        else:
            mappings.append(
                {
                    "source_relative_path": _normalize_relative_path(mapping.get("source_relative_path", "")),
                    "target_relative_path": _normalize_relative_path(mapping.get("target_relative_path", "")),
                }
            )
    if not replaced:
        mappings.append(
            {
                "source_relative_path": normalized_source,
                "target_relative_path": normalized_target,
            }
        )
    return {
        **job,
        "mappings": sorted(mappings, key=lambda item: str(item.get("source_relative_path", "")).upper()),
    }


def _remove_mappings(job: dict[str, Any], source_relative_paths: list[str]) -> dict[str, Any]:
    normalized = {_normalize_relative_path(item) for item in source_relative_paths}
    return {
        **job,
        "mappings": [
            mapping
            for mapping in list(job.get("mappings", []) or [])
            if _normalize_relative_path(mapping.get("source_relative_path", "")) not in normalized
        ],
    }


def _prompt_job_fabric_config(job: dict[str, Any]) -> dict[str, Any]:
    current = dict(job.get("fabric", {})) if isinstance(job.get("fabric"), dict) else {}
    workspace_name = _prompt_required_text(
        "Fabric workspace display name",
        str(current.get("workspace_display_name", "")),
        "Fabric workspace display name is required.",
    )
    lakehouse_name = _prompt_required_text(
        "Fabric lakehouse display name",
        str(current.get("lakehouse_display_name", "")),
        "Fabric lakehouse display name is required.",
    )
    return {
        **job,
        "fabric": {
            "workspace_display_name": workspace_name,
            "lakehouse_display_name": lakehouse_name,
        },
    }


def _load_runtime_config(config_path: str | None) -> tuple[dict[str, Any], Path | None]:
    if config_path:
        resolved = Path(config_path).resolve()
        config = _normalize_config_payload(_load_json_file(resolved))
        config["_config_dir"] = str(resolved.parent)
        return config, resolved
    if DEFAULT_CONFIG_PATH.exists():
        resolved = DEFAULT_CONFIG_PATH.resolve()
        config = _normalize_config_payload(_load_json_file(resolved))
        config["_config_dir"] = str(resolved.parent)
        return config, resolved
    print(f"No local uploader config found. Creating {DEFAULT_CONFIG_PATH.name}.", flush=True)
    payload = _default_config_payload()
    _save_json_file(DEFAULT_CONFIG_PATH, payload)
    payload["_config_dir"] = str(DEFAULT_CONFIG_PATH.resolve().parent)
    return payload, DEFAULT_CONFIG_PATH.resolve()


def _save_runtime_config(config: dict[str, Any], config_path: Path | None) -> Path:
    target_path = config_path or DEFAULT_CONFIG_PATH
    _save_json_file(target_path, _serializable_config(_normalize_config_payload(config)))
    return target_path


def _validate_config(config: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    jobs = list(config.get("jobs", []) or [])
    base_dir = _config_base_dir(config)
    for index, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            issues.append(f"Job {index}: invalid job payload.")
            continue
        job_name = str(job.get("name", "")).strip() or f"Job {index}"
        source_kind = str(job.get("source_kind", "")).strip()
        source_root = str(job.get("source_root", "")).strip()
        source_links = _job_source_links(job)
        target_root = str(job.get("target_root", "")).strip()
        fabric = job.get("fabric", {}) or {}
        workspace_name = str(fabric.get("workspace_display_name", "")).strip()
        lakehouse_name = str(fabric.get("lakehouse_display_name", "")).strip()

        if not source_kind:
            issues.append(f"{job_name}: source kind is required.")
        elif source_kind not in {"local", "sharepoint"}:
            issues.append(f"{job_name}: unsupported source kind '{source_kind}'.")

        if source_kind == "local":
            if not source_root:
                issues.append(f"{job_name}: source path is required.")
            else:
                try:
                    _require_existing_local_root(source_root, base_dir)
                except Exception as exc:
                    issues.append(f"{job_name}: {exc}")
        elif source_kind == "sharepoint":
            if not source_links:
                issues.append(f"{job_name}: at least one SharePoint file/folder link is required.")
        elif not source_root:
            issues.append(f"{job_name}: source path/link is required.")

        if not target_root:
            issues.append(f"{job_name}: Fabric target root is required.")
        if not workspace_name:
            issues.append(f"{job_name}: Fabric workspace display name is required.")
        if not lakehouse_name:
            issues.append(f"{job_name}: Fabric lakehouse display name is required.")

        mappings = list(job.get("mappings", []) or [])
        for mapping_index, mapping in enumerate(mappings, start=1):
            if not isinstance(mapping, dict):
                issues.append(f"{job_name}: mapping {mapping_index} is invalid.")
                continue
            source_relative_path = str(mapping.get("source_relative_path", "")).strip()
            target_relative_path = str(mapping.get("target_relative_path", "")).strip()
            source_link = str(mapping.get("source_link", "")).strip()
            if not source_relative_path:
                issues.append(f"{job_name}: mapping {mapping_index} is missing source path.")
            if source_kind == "sharepoint":
                if not source_link:
                    issues.append(f"{job_name}: mapping {mapping_index} is missing SharePoint source link.")
                elif source_link not in source_links:
                    issues.append(f"{job_name}: mapping {mapping_index} uses a SharePoint link not configured on the job.")
            if not target_relative_path:
                issues.append(f"{job_name}: mapping {mapping_index} is missing target path.")
            elif not _target_path_is_relative(target_relative_path, target_root):
                issues.append(
                    f"{job_name}: mapping {mapping_index} target path must be relative to "
                    f"'{_normalize_relative_path(target_root)}'."
                )
    return issues


def _prompt_sharepoint_links(default_links: list[str] | None = None) -> list[str]:
    current = list(default_links or [])
    while True:
        print("\nConfigured SharePoint links:", flush=True)
        if current:
            for index, link in enumerate(current, start=1):
                print(f"  {index}. {link}", flush=True)
        else:
            print("  No SharePoint links configured.", flush=True)
        action = _select(
            "Choose a SharePoint link action",
            [
                {"name": "Add link", "value": "add"},
                {"name": "Remove link", "value": "remove"},
                {"name": "Done", "value": "done"},
            ],
        )
        if action == "done":
            if current:
                return current
            _print_error("At least one SharePoint file/folder link is required.")
            continue
        if action == "add":
            current.append(
                _prompt_required_text(
                    "SharePoint file/folder link",
                    None,
                    "SharePoint file/folder link is required.",
                )
            )
        elif action == "remove":
            if not current:
                _print_error("There are no SharePoint links to remove.")
                continue
            selected_index = _select(
                "Choose a SharePoint link to remove",
                [
                    {"name": link, "value": index}
                    for index, link in enumerate(current)
                ],
            )
            current = [link for index, link in enumerate(current) if index != selected_index]


def _prompt_source_config(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    source_kind = str(job.get("source_kind", "")).strip()
    current = dict(job)
    if source_kind == "sharepoint":
        current["source_root"] = ""
        current["source_links"] = _prompt_sharepoint_links(_job_source_links(job))
        return current
    base_dir = _config_base_dir(config)
    while True:
        source_root = _prompt_required_text(
            "Local file/folder path",
            str(job.get("source_root", "")),
            "Local file/folder path is required.",
        )
        try:
            _require_existing_local_root(source_root, base_dir)
            current["source_root"] = source_root
            current["source_links"] = []
            return current
        except Exception as exc:
            _print_error(str(exc))


def _report_config_issues(config: dict[str, Any], *, heading: str = "Config validation") -> list[str]:
    issues = _validate_config(config)
    if not issues:
        return issues
    print(f"\n{heading}:", flush=True)
    print("CONFIG INVALID. UPLOADS ARE BLOCKED UNTIL EVERY ISSUE BELOW IS FIXED.", flush=True)
    for issue in issues:
        print(f"  - {issue}", flush=True)
    return issues


def _show_config_path(config_path: Path | None):
    resolved = (config_path or DEFAULT_CONFIG_PATH).resolve()
    print(f"\nConfig path: {resolved}", flush=True)


def _select_source_kind() -> str:
    return _select(
        "Choose an upload source",
        [
            {"name": "Local files or folder", "value": "local"},
            {"name": "SharePoint folder", "value": "sharepoint"},
        ],
    )


def _prompt_job_basics(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    source_kind = str(job.get("source_kind", "")).strip() or _select_source_kind()

    name = _prompt_required_text("Job name", str(job.get("name", "")), "Job name is required.")
    enabled = _confirm("Enable this job", default=bool(job.get("enabled", True)))

    updated = {
        **job,
        "name": name,
        "enabled": enabled,
        "source_kind": source_kind,
        "target_root": _normalize_relative_path(
            _prompt_required_text(
                "Fabric target root",
                str(job.get("target_root", "Files/Uploads")),
                "Fabric target root is required.",
            )
        ),
        "mappings": list(job.get("mappings", []) or []),
    }
    updated = _prompt_source_config(updated, config)
    return _prompt_job_fabric_config(updated)


def _edit_source_root(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return _prompt_source_config(job, config)


def _edit_target_root(job: dict[str, Any]) -> dict[str, Any]:
    current = dict(job)
    current["target_root"] = _normalize_relative_path(
        _prompt_required_text(
            "Fabric target root",
            str(current.get("target_root", "Files/Uploads")),
            "Fabric target root is required.",
        )
    )
    return current


def _resolve_sharepoint_link(link: str) -> dict[str, Any]:
    auth = _online_auth()
    success, payload = auth.resolve_sharepoint_share_url(link)
    _common().require_ok("resolve_sharepoint_share_url", success, payload)
    return payload


def _sharepoint_link_label(link: str) -> str:
    payload = _resolve_sharepoint_link(link)
    item_name = str(payload.get("name") or "").strip()
    parent_reference = payload.get("parentReference", {}) or {}
    raw_path = str(parent_reference.get("path") or "")
    parent_path = raw_path.split("root:/", 1)[-1].strip("/")
    drive_id = str(parent_reference.get("driveId") or "").strip()
    drive_label = drive_id
    if drive_id:
        drive_ok, drive_payload = _online_auth().get_drive(drive_id)
        if drive_ok and isinstance(drive_payload, dict):
            drive_label = str(drive_payload.get("name") or drive_id).strip()
    if parent_path and item_name:
        return f"{drive_label} / {parent_path} / {item_name}"
    if item_name:
        return f"{drive_label} / {item_name}"
    return drive_label or link


def _upsert_sharepoint_mapping(job: dict[str, Any], source_link: str, source_relative_path: str, target_relative_path: str) -> dict[str, Any]:
    normalized_source = _normalize_relative_path(source_relative_path)
    normalized_target = _normalize_relative_path(target_relative_path)
    mappings = []
    replaced = False
    for mapping in list(job.get("mappings", []) or []):
        existing_source = _normalize_relative_path(mapping.get("source_relative_path", ""))
        existing_link = str(mapping.get("source_link", "")).strip()
        normalized_mapping = {
            "source_relative_path": existing_source,
            "target_relative_path": _normalize_relative_path(mapping.get("target_relative_path", "")),
        }
        if existing_link:
            normalized_mapping["source_link"] = existing_link
        if existing_source == normalized_source and existing_link == source_link:
            normalized_mapping["target_relative_path"] = normalized_target
            normalized_mapping["source_link"] = source_link
            replaced = True
        mappings.append(normalized_mapping)
    if not replaced:
        mappings.append(
            {
                "source_relative_path": normalized_source,
                "target_relative_path": normalized_target,
                "source_link": source_link,
            }
        )
    return {
        **job,
        "mappings": sorted(
            mappings,
            key=lambda item: (
                str(item.get("source_link", "")).upper(),
                str(item.get("source_relative_path", "")).upper(),
            ),
        ),
    }


def _available_source_files(job: dict[str, Any], config: dict[str, Any]) -> list[str]:
    source_kind = str(job.get("source_kind", "")).strip()
    source_root = str(job.get("source_root", "")).strip()
    if not source_root:
        raise RuntimeError("Set the job source before selecting files.")
    if source_kind == "local":
        return _list_local_files(source_root, _config_base_dir(config))
    raise RuntimeError(f"Unsupported source kind: {source_kind}")


def _scan_sharepoint_folder(job: dict[str, Any], source_link: str) -> dict[str, Any]:
    folder_label = _sharepoint_link_label(source_link)
    _context, items = _workflows().resolve_sharepoint_folder_listing(source_link)
    available_files = sorted(
        str(item.get("relativePath") or item.get("name") or "").strip("/")
        for item in items
        if not item.get("isFolder")
    )
    existing = {
        _normalize_relative_path(str(mapping.get("source_relative_path", "")))
        for mapping in list(job.get("mappings", []) or [])
        if str(mapping.get("source_link", "")).strip() == source_link
    }
    if not available_files:
        raise RuntimeError(f"No files were found for SharePoint folder: {folder_label}")
    selection_mode = _select(
        f"How do you want to add files from {folder_label}",
        [
            {"name": "Add all scanned files", "value": "all"},
            {"name": "Select specific files", "value": "selected"},
        ],
    )
    selected = available_files
    if selection_mode == "selected":
        q = _questionary()
        choices = [
            q.Choice(
                title=(f"{file_path} (already selected)" if _normalize_relative_path(file_path) in existing else file_path),
                value=file_path,
                checked=_normalize_relative_path(file_path) in existing,
            )
            for file_path in available_files
        ]
        while True:
            selected = _checkbox(
                f"Choose files from {folder_label} (use Space to select multiple, then Enter to confirm)",
                choices,
            )
            if selected:
                break
            _print_error("No files selected. Use Space to select one or more files, then Enter to confirm.")
            if _confirm("Skip this SharePoint folder", default=False):
                return job
    updated = dict(job)
    for source_path in selected:
        updated = _upsert_sharepoint_mapping(
            updated,
            source_link,
            source_path,
            _default_target_path(source_path, str(job.get("target_root", ""))),
        )
    return updated


def _add_mappings_from_scan(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if str(job.get("source_kind", "")).strip() == "sharepoint":
        source_links = _job_source_links(job)
        if not source_links:
            raise RuntimeError("Set at least one SharePoint file/folder link before scanning.")
        updated = dict(job)
        for source_link in source_links:
            payload = _resolve_sharepoint_link(source_link)
            if payload.get("folder"):
                updated = _scan_sharepoint_folder(updated, source_link)
                continue
            file_name = str(payload.get("name", "")).strip()
            if not file_name:
                raise RuntimeError(f"Resolved SharePoint file link did not include a file name: {source_link}")
            updated = _upsert_sharepoint_mapping(
                updated,
                source_link,
                file_name,
                _default_target_path(file_name, str(job.get("target_root", ""))),
            )
        return updated

    available_files = _available_source_files(job, config)
    if not available_files:
        raise RuntimeError("No files were found for this source.")
    selection_mode = _select(
        "How do you want to add scanned files",
        [
            {"name": "Add all scanned files", "value": "all"},
            {"name": "Select specific files", "value": "selected"},
        ],
    )
    if selection_mode == "all":
        updated = dict(job)
        for source_path in available_files:
            updated = _upsert_mapping(
                updated,
                source_path,
                _default_target_path(source_path, str(job.get("target_root", ""))),
            )
        return updated

    q = _questionary()
    existing = {
        _normalize_relative_path(mapping.get("source_relative_path", ""))
        for mapping in list(job.get("mappings", []) or [])
    }
    choices = [
        q.Choice(
            title=(
                f"{source_path}"
                if _normalize_relative_path(source_path) not in existing
                else f"{source_path} (already mapped)"
            ),
            value=source_path,
            checked=_normalize_relative_path(source_path) in existing,
        )
        for source_path in available_files
    ]
    while True:
        selected = _checkbox(
            "Choose files to upload (use Space to select multiple, then Enter to confirm)",
            choices,
        )
        if selected:
            updated = dict(job)
            for source_path in selected:
                updated = _upsert_mapping(
                    updated,
                    source_path,
                    _default_target_path(source_path, str(job.get("target_root", ""))),
                )
            return updated
        _print_error("No files selected. Use Space to select one or more files, then Enter to confirm.")
        if _confirm("Keep this job with no file mappings", default=False):
            return job


def _add_mapping_manually(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    mappings = list(job.get("mappings", []) or [])
    source_default = _manual_source_default(job, config)
    target_default = str(mappings[-1].get("target_relative_path", "")) if mappings else ""
    source_prompt = "Source file path relative to the job root"
    source_root = _normalize_relative_path(str(job.get("source_root", "")))
    selected_source_link = ""
    if str(job.get("source_kind", "")).strip() == "local":
        source_root = str(job.get("source_root", "")).strip()
    elif str(job.get("source_kind", "")).strip() == "sharepoint":
        source_links = _job_source_links(job)
        if not source_links:
            raise RuntimeError("At least one SharePoint file/folder link is required.")
        selected_source_link = _select(
            "Choose a SharePoint source link",
            [{"name": link, "value": link} for link in source_links],
        )
        source_root = selected_source_link
    if source_root:
        source_prompt = f"Source file path [{source_root}]"
    source_relative_path = _normalize_manual_source_path(
        job,
        _prompt_required_text(
            source_prompt,
            source_default,
            "Source file path relative to the job root is required.",
        ),
        config,
    )
    target_relative_path = _normalize_relative_path(
        _prompt_required_text(
            f"Target Fabric path [{_normalize_relative_path(str(job.get('target_root', '')))}]",
            target_default or _default_target_path(source_relative_path, str(job.get("target_root", ""))),
            "Target Fabric path is required.",
        )
    )
    _require_relative_target_path(target_relative_path, str(job.get("target_root", "")))
    if selected_source_link:
        return _upsert_sharepoint_mapping(job, selected_source_link, source_relative_path, target_relative_path)
    return _upsert_mapping(job, source_relative_path, target_relative_path)


def _edit_mapping_target(job: dict[str, Any]) -> dict[str, Any]:
    mappings = list(job.get("mappings", []) or [])
    if not mappings:
        print("This job has no mappings.", flush=True)
        return job
    selected = _select(
        "Choose a mapping to edit",
        [{"name": _mapping_summary(mapping), "value": mapping} for mapping in mappings],
    )
    updated_target = _normalize_relative_path(
        _text(
            f"Target Fabric path [{_normalize_relative_path(str(job.get('target_root', '')))}]",
            str(selected.get("target_relative_path", "")),
        )
    )
    _require_relative_target_path(updated_target, str(job.get("target_root", "")))
    source_link = str(selected.get("source_link", "")).strip()
    if source_link:
        return _upsert_sharepoint_mapping(job, source_link, str(selected.get("source_relative_path", "")), updated_target)
    return _upsert_mapping(job, str(selected.get("source_relative_path", "")), updated_target)


def _remove_mapping_flow(job: dict[str, Any]) -> dict[str, Any]:
    mappings = list(job.get("mappings", []) or [])
    if not mappings:
        print("This job has no mappings.", flush=True)
        return job
    selected = _checkbox(
        "Choose mappings to remove",
        [
            {
                "name": _mapping_summary(mapping),
                "value": str(mapping.get("source_relative_path", "")),
            }
            for mapping in mappings
        ],
    )
    if not selected:
        return job
    return _remove_mappings(job, selected)


def _manage_job_mappings(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    current = dict(job)
    while True:
        print(f"\nMappings for {current.get('name') or '<unnamed>'}:", flush=True)
        mappings = list(current.get("mappings", []) or [])
        if mappings:
            for mapping in mappings:
                print(f"  - {_mapping_summary(mapping)}", flush=True)
        else:
            print("  No mappings configured.", flush=True)
        action = _select(
            "Choose a mapping action",
            [
                {"name": "Scan source and select files", "value": "scan"},
                {"name": "Add mapping manually", "value": "manual"},
                {"name": "Change source root / SharePoint links", "value": "source_root"},
                {"name": "Change target root", "value": "target_root"},
                {"name": "Edit a target path", "value": "edit_target"},
                {"name": "Remove mappings", "value": "remove"},
                {"name": "Back", "value": "back"},
            ],
        )
        if action == "back":
            return current
        try:
            if action == "scan":
                current = _add_mappings_from_scan(current, config)
            elif action == "manual":
                current = _add_mapping_manually(current, config)
            elif action == "source_root":
                current = _edit_source_root(current, config)
            elif action == "target_root":
                current = _edit_target_root(current)
            elif action == "edit_target":
                current = _edit_mapping_target(current)
            elif action == "remove":
                current = _remove_mapping_flow(current)
        except Exception as exc:
            _print_error(str(exc))


def _maybe_scan_files_now(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if not _confirm("Scan source and select files now", default=True):
        return job
    try:
        return _add_mappings_from_scan(job, config)
    except Exception as exc:
        _print_error(str(exc))
        return job


def _create_job(config: dict[str, Any]) -> dict[str, Any]:
    job = _prompt_job_basics(_default_job_payload(_select_source_kind()), config)
    job = _maybe_scan_files_now(job, config)
    return _manage_job_mappings(job, config)


def _edit_job(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    current = dict(job)
    while True:
        print(f"\nEditing job: {_job_summary(current)}", flush=True)
        action = _select(
            "Choose an edit action",
            [
                {"name": "Edit job details", "value": "details"},
                {"name": "Edit Fabric destination", "value": "fabric"},
                {"name": "Manage file mappings", "value": "mappings"},
                {"name": "Back", "value": "back"},
            ],
        )
        if action == "back":
            return current
        if action == "details":
            current = _prompt_job_basics(current, config)
            current = _maybe_scan_files_now(current, config)
        elif action == "fabric":
            current = _prompt_job_fabric_config(current)
        elif action == "mappings":
            current = _manage_job_mappings(current, config)


def _remove_job_flow(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not jobs:
        print("There are no jobs to remove.", flush=True)
        return jobs
    selected_index = _select(
        "Choose a job to remove",
        [
            {"name": _job_summary(job), "value": index}
            for index, job in enumerate(jobs)
        ],
    )
    selected_job = jobs[selected_index]
    if not _confirm(f"Remove job '{selected_job.get('name', '<unnamed>')}'", default=False):
        return jobs
    return [job for index, job in enumerate(jobs) if index != selected_index]


def _maybe_save_config(current: dict[str, Any], config_path: Path | None) -> tuple[dict[str, Any], Path | None, bool]:
    if not _confirm("Save changes now", default=True):
        return current, config_path, False
    target_path = _save_runtime_config(current, config_path)
    print(f"Saved config to {target_path}", flush=True)
    return current, target_path, True


def _edit_config(config: dict[str, Any], config_path: Path | None) -> tuple[dict[str, Any], Path]:
    current = {
        "jobs": list(config.get("jobs", []) or []),
        "_config_dir": str(config.get("_config_dir", "")).strip(),
    }
    while True:
        print("\nCurrent uploader config:", flush=True)
        if current["jobs"]:
            for index, job in enumerate(current["jobs"], start=1):
                print(f"  {index}. {_job_summary(job)}", flush=True)
        else:
            print("  No jobs configured.", flush=True)

        action = _select(
            "Choose a config action",
            [
                {"name": "Add job", "value": "add"},
                {"name": "Edit existing job", "value": "edit"},
                {"name": "Remove job", "value": "remove"},
                {"name": "Show config path", "value": "show_path"},
                {"name": "Back and save", "value": "save"},
            ],
        )
        if action == "save":
            target_path = _save_runtime_config(current, config_path)
            print(f"Saved config to {target_path}", flush=True)
            return current, target_path
        try:
            if action == "add":
                current["jobs"].append(_create_job(current))
            elif action == "edit":
                if not current["jobs"]:
                    print("There are no jobs to edit.", flush=True)
                    continue
                selected_index = _select(
                    "Choose a job",
                    [
                        {"name": _job_summary(job), "value": index}
                        for index, job in enumerate(current["jobs"])
                    ],
                )
                current["jobs"][selected_index] = _edit_job(current["jobs"][selected_index], current)
            elif action == "remove":
                current["jobs"] = _remove_job_flow(current["jobs"])
                current, current_path, saved = _maybe_save_config(current, config_path)
                config_path = current_path
                if saved:
                    return current, current_path or DEFAULT_CONFIG_PATH
            elif action == "show_path":
                _show_config_path(config_path)
        except Exception as exc:
            _print_error(str(exc))


def _show_config(config: dict[str, Any]):
    print("", flush=True)
    _print_json(config)


def _resolve_sharepoint_bytes(source_link: str, source_relative_path: str) -> bytes:
    source_payload = {
        "folder_url": str(source_link).strip(),
        "tracked_files": [
            {
                "path": _normalize_relative_path(source_relative_path),
                "watch": False,
            }
        ],
    }
    payload, _item = _workflows().resolve_sharepoint_file_bytes(source_payload, source_relative_path)
    return payload


def _resolve_sharepoint_item(source_link: str, source_relative_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _resolve_sharepoint_link(source_link)
    if payload.get("folder"):
        context, items = _workflows().resolve_sharepoint_folder_listing(source_link)
        normalized_path = _workflows().normalize_relative_path(source_relative_path)
        item_payload = next(
            (
                item
                for item in items
                if _workflows().normalize_relative_path(str(item.get("relativePath") or item.get("name") or "")) == normalized_path
            ),
            None,
        )
        if item_payload is None or item_payload.get("isFolder"):
            raise RuntimeError(f"Tracked SharePoint path '{normalized_path}' must resolve to a file.")
        return context, item_payload

    parent_reference = payload.get("parentReference", {}) or {}
    drive_id = str(parent_reference.get("driveId") or "")
    item_id = str(payload.get("id") or "")
    item_name = str(payload.get("name") or "").strip()
    if not drive_id or not item_id or not item_name:
        raise RuntimeError("Resolved SharePoint file link did not include drive/item details.")
    normalized_path = _workflows().normalize_relative_path(source_relative_path)
    if _workflows().normalize_relative_path(item_name) != normalized_path:
        raise RuntimeError(f"Tracked SharePoint path '{normalized_path}' does not match linked file '{item_name}'.")
    return {"drive_id": drive_id}, {"id": item_id, "name": item_name}


def _download_sharepoint_bytes_with_progress(source_link: str, source_relative_path: str) -> bytes:
    auth = _online_auth()
    context, item_payload = _resolve_sharepoint_item(source_link, source_relative_path)
    drive_id = str(context["drive_id"])
    item_id = str(item_payload["id"])
    auth._ensure_authenticated(auth.SHAREPOINT_SCOPE)
    url = auth.SHAREPOINT_DRIVE_ITEM_CONTENT_URL.format(drive=drive_id, item=item_id)
    response = auth._request_with_auto_refresh("GET", url, auth.SHAREPOINT_SCOPE, stream=True)
    if not response.ok:
        raise RuntimeError(f"download_sharepoint_item failed: {response.text}")
    total_bytes = None
    content_length = response.headers.get("Content-Length")
    if content_length and content_length.isdigit():
        total_bytes = int(content_length)
    progress = _TransferProgress(f"Downloading {source_relative_path}", total_bytes)
    content = bytearray()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        content.extend(chunk)
        progress.update(len(content))
    progress.finish(len(content))
    return bytes(content)


def _upload_bytes_with_progress(
    workspace_id: str,
    lakehouse_id: str,
    relative_path: str,
    content: bytes,
) -> None:
    auth = _online_auth()
    auth._ensure_authenticated(auth.ONELAKE_SCOPE)
    lakehouse_path = f"{lakehouse_id}/{relative_path.strip('/')}"
    url = auth.ONELAKE_FILE_URL.format(filesystem=workspace_id, path=quote(lakehouse_path))
    headers = {"x-ms-version": "2023-08-03"}
    create = auth._request_with_auto_refresh(
        "PUT",
        url,
        auth.ONELAKE_SCOPE,
        headers=headers,
        params={"resource": "file"},
    )
    if not create.ok:
        raise RuntimeError(f"write_lakehouse_file failed during create: {create.text}")
    progress = _TransferProgress(f"Uploading {relative_path}", len(content))
    position = 0
    chunk_size = 4 * 1024 * 1024
    while position < len(content):
        chunk = content[position : position + chunk_size]
        append = auth._request_with_auto_refresh(
            "PATCH",
            url,
            auth.ONELAKE_SCOPE,
            headers=headers,
            params={"action": "append", "position": position},
            data=chunk,
        )
        if not append.ok:
            raise RuntimeError(f"write_lakehouse_file failed during append: {append.text}")
        position += len(chunk)
        progress.update(position)
    flush = auth._request_with_auto_refresh(
        "PATCH",
        url,
        auth.ONELAKE_SCOPE,
        headers=headers,
        params={"action": "flush", "position": len(content)},
    )
    if not flush.ok:
        raise RuntimeError(f"write_lakehouse_file failed during flush: {flush.text}")
    progress.finish(len(content))


def _upload_mapping(
    job: dict[str, Any],
    mapping: dict[str, Any],
    workspace_id: str,
    lakehouse_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    source_relative_path = _normalize_relative_path(mapping.get("source_relative_path", ""))
    target_relative_path = _normalize_relative_path(mapping.get("target_relative_path", ""))
    effective_target_path = _effective_target_path(target_relative_path, str(job.get("target_root", "")))
    if str(job.get("source_kind", "")) == "local":
        content = _read_local_file(str(job.get("source_root", "")), source_relative_path, _config_base_dir(config))
    elif str(job.get("source_kind", "")) == "sharepoint":
        source_link = str(mapping.get("source_link", "")).strip()
        if not source_link:
            raise RuntimeError("SharePoint mapping is missing source_link.")
        content = _download_sharepoint_bytes_with_progress(source_link, source_relative_path)
    else:
        raise RuntimeError(f"Unsupported source kind: {job.get('source_kind')}")
    _upload_bytes_with_progress(workspace_id, lakehouse_id, effective_target_path, content)
    return {
        "source_relative_path": source_relative_path,
        "target_relative_path": target_relative_path,
        "effective_target_path": effective_target_path,
        "byte_count": len(content),
    }


def _choose_jobs(config: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = [job for job in list(config.get("jobs", []) or []) if bool(job.get("enabled", True))]
    if not jobs:
        return []
    mode = _select(
        "Choose what to upload",
        [
            {"name": "Run all enabled jobs", "value": "all"},
            {"name": "Select specific jobs", "value": "selected"},
        ],
    )
    if mode == "all":
        return jobs
    selected = _checkbox(
        "Choose jobs to run",
        [{"name": _job_summary(job), "value": index} for index, job in enumerate(jobs)],
    )
    return [job for index, job in enumerate(jobs) if index in set(selected)]


def _run_uploads(config: dict[str, Any]):
    jobs = _choose_jobs(config)
    if not jobs:
        print("No enabled jobs selected.", flush=True)
        return
    _common().ensure_authenticated()

    upload_tasks: list[dict[str, Any]] = []
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for job in jobs:
        print(f"\nRunning job: {job.get('name')}", flush=True)
        workspace_id, lakehouse_id = _resolve_fabric_destination(job)
        mappings = list(job.get("mappings", []) or [])
        if not mappings:
            print("  Skipped: no mappings configured.", flush=True)
            continue
        for mapping in mappings:
            upload_tasks.append(
                {
                    "job_name": str(job.get("name", "")),
                    "job": job,
                    "mapping": mapping,
                    "workspace_id": workspace_id,
                    "lakehouse_id": lakehouse_id,
                }
            )

    if not upload_tasks:
        print("\nNo uploads to run.", flush=True)
        return

    print(
        f"\nStarting {len(upload_tasks)} upload task(s) with up to {MAX_TRANSFER_WORKERS} parallel workers.",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=MAX_TRANSFER_WORKERS) as executor:
        future_to_task = {
            executor.submit(
                _upload_mapping,
                task["job"],
                task["mapping"],
                task["workspace_id"],
                task["lakehouse_id"],
                config,
            ): task
            for task in upload_tasks
        }
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            mapping = task["mapping"]
            try:
                result = future.result()
                successes.append({"job_name": task["job_name"], **result})
                print(
                    f"  Uploaded {result['source_relative_path']} -> {result['effective_target_path']}"
                    f" ({result['byte_count']} bytes)",
                    flush=True,
                )
            except Exception as exc:
                failures.append(
                    {
                        "job_name": task["job_name"],
                        "source_relative_path": str(mapping.get("source_relative_path", "")),
                        "target_relative_path": str(mapping.get("target_relative_path", "")),
                        "error": str(exc),
                    }
                )
                print(
                    f"  Failed {mapping.get('source_relative_path')} -> {mapping.get('target_relative_path')}: {exc}",
                    flush=True,
                )

    print("\nUpload summary:", flush=True)
    print(f"  Successful uploads: {len(successes)}", flush=True)
    print(f"  Failed uploads: {len(failures)}", flush=True)
    if failures:
        for failure in failures:
            print(
                f"  - {failure['job_name']}: {failure['source_relative_path']}"
                f" -> {failure['target_relative_path']} | {failure['error']}",
                flush=True,
            )


def _interactive_loop(config: dict[str, Any], config_path: Path | None) -> int:
    current_config = config
    current_path = config_path
    while True:
        issues = _validate_config(current_config)
        if issues:
            _report_config_issues(current_config, heading="Startup config validation")
            action = _select(
                "Config is invalid. Choose a repair action",
                [
                    {"name": "Edit saved config", "value": "edit_config"},
                    {"name": "Show config path", "value": "show_config_path"},
                    {"name": "Show current config", "value": "show_config"},
                    {"name": "Exit", "value": "exit"},
                ],
            )
            if action == "exit":
                return 1
            try:
                if action == "edit_config":
                    current_config, current_path = _edit_config(current_config, current_path)
                elif action == "show_config_path":
                    _show_config_path(current_path)
                elif action == "show_config":
                    _show_config(current_config)
            except Exception as exc:
                _print_error(str(exc))
            continue

        action = _select(
            "Fabric uploader",
            [
                {"name": "Upload files now", "value": "upload"},
                {"name": "Edit saved config", "value": "edit_config"},
                {"name": "Show config path", "value": "show_config_path"},
                {"name": "Show current config", "value": "show_config"},
                {"name": "Authenticate", "value": "authenticate"},
                {"name": "Exit", "value": "exit"},
            ],
        )
        if action == "exit":
            return 0
        try:
            if action == "upload":
                _run_uploads(current_config)
            elif action == "edit_config":
                current_config, current_path = _edit_config(current_config, current_path)
            elif action == "show_config_path":
                _show_config_path(current_path)
            elif action == "show_config":
                _show_config(current_config)
            elif action == "authenticate":
                _common().ensure_authenticated()
                print("Authentication complete.", flush=True)
        except Exception as exc:
            _print_error(str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive uploader for local or SharePoint files into Fabric.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument(
        "--action",
        choices=["upload", "edit_config", "show_config_path", "show_config", "authenticate"],
        help="Run a single action directly.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config, config_path = _load_runtime_config(args.config)
        issues = _report_config_issues(config, heading="Startup config validation")

        if issues and args.action in {"upload", "authenticate"}:
            _print_error("Config is invalid. Fix the config before running this action.")
            return 1

        if args.action == "upload":
            _run_uploads(config)
            return 0
        if args.action == "edit_config":
            _edit_config(config, config_path)
            return 0
        if args.action == "show_config_path":
            _show_config_path(config_path)
            return 0
        if args.action == "show_config":
            _show_config(config)
            return 0
        if args.action == "authenticate":
            _common().ensure_authenticated()
            print("Authentication complete.", flush=True)
            return 0

        return _interactive_loop(config, config_path)
    except Exception as exc:
        _print_error(str(exc))
        return 1
