from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
DEFAULT_CONFIG_PATH = Path(__file__).with_name("local_config.json")

from refactor.services.data_validation_api import (
    CreateRunRequest,
    GetRunRequest,
    ListRunsRequest,
    ListSubmissionsRequest,
    OrganisationSubmissionRef,
    PlanRunRequest,
    RefreshRunStatusRequest,
    RefreshSubmissionRequest,
    StageRunInputsRequest,
    TemplateSubmissionRef,
    TriggerRunRequest,
)
from refactor.services.data_validation_api.workflows import (
    build_submission_refs,
    create_submission_from_discovery,
    edit_submission_workflow,
    find_prefix_matches,
    resolve_sharepoint_folder_listing,
)


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


def parse_code_csv(raw: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in raw.split(","):
        normalized = item.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def friendly_submission_display(item: dict[str, Any]) -> str:
    return (
        f"{item.get('process_cd', '')} | "
        f"{item.get('submission_period_cd', '')} | "
        f"{item.get('created_by', '')} | "
        f"{item.get('created_ts_utc', '')}"
    )


def _truncate_text(value: str | None, max_len: int = 48) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def editable_org_payloads(projection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    organisations = projection.get("organisations", {})
    payloads: dict[str, dict[str, Any]] = {}
    if not isinstance(organisations, dict):
        return payloads
    for org_cd, entry in organisations.items():
        if not isinstance(entry, dict):
            continue
        file_payload = dict(entry.get("file", {})) if isinstance(entry.get("file"), dict) else {}
        payloads[str(org_cd)] = {
            **file_payload,
            "template_keys": list(entry.get("template_keys", [])) if isinstance(entry.get("template_keys"), list) else [],
        }
    return payloads


def editable_template_payloads(projection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    templates = projection.get("templates", {})
    payloads: dict[str, dict[str, Any]] = {}
    if not isinstance(templates, dict):
        return payloads
    for template_key, entry in templates.items():
        if not isinstance(entry, dict):
            continue
        file_payload = dict(entry.get("file", {})) if isinstance(entry.get("file"), dict) else {}
        payloads[str(template_key)] = {
            **file_payload,
            "assignment_mode": entry.get("assignment_mode", "shared"),
            "applies_to": list(entry.get("applies_to", [])) if isinstance(entry.get("applies_to"), list) else [],
        }
    return payloads


def current_org_refs(projection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    organisations = projection.get("organisations", {})
    refs: dict[str, dict[str, Any]] = {}
    if not isinstance(organisations, dict):
        return refs
    for org_cd, entry in organisations.items():
        if not isinstance(entry, dict):
            continue
        refs[str(org_cd)] = OrganisationSubmissionRef.from_dict(
            {
                "sharepoint_source": entry.get("file", {}),
                "template_keys": entry.get("template_keys", []),
            }
        ).to_dict()
    return refs


def current_template_refs(projection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    templates = projection.get("templates", {})
    refs: dict[str, dict[str, Any]] = {}
    if not isinstance(templates, dict):
        return refs
    for template_key, entry in templates.items():
        if not isinstance(entry, dict):
            continue
        refs[str(template_key)] = TemplateSubmissionRef.from_dict(
            {
                "assignment_mode": entry.get("assignment_mode", "shared"),
                "applies_to": entry.get("applies_to", []),
                "sharepoint_source": entry.get("file", {}),
            }
        ).to_dict()
    return refs


def summarize_edit_changes(
    *,
    note_for_api: str | None,
    organisation_upserts: dict[str, object],
    organisation_removals: list[str],
    organisation_validation_changes: dict[str, str],
    template_upserts: dict[str, object],
    template_removals: list[str],
    template_validation_changes: dict[str, str],
) -> list[str]:
    changes: list[str] = []
    if note_for_api is not None:
        if note_for_api == "":
            changes.append("Clear note")
        else:
            changes.append(f"Update note to: {note_for_api}")
    for org_cd in sorted(organisation_upserts):
        changes.append(f"Upsert company: {org_cd}")
    for org_cd in sorted(organisation_removals):
        changes.append(f"Remove company: {org_cd}")
    for org_cd in sorted(organisation_validation_changes):
        changes.append(f"Set company validation flag: {org_cd} -> {organisation_validation_changes[org_cd]}")
    for template_key in sorted(template_upserts):
        changes.append(f"Upsert template: {template_key}")
    for template_key in sorted(template_removals):
        changes.append(f"Remove template: {template_key}")
    for template_key in sorted(template_validation_changes):
        changes.append(f"Set template validation flag: {template_key} -> {template_validation_changes[template_key]}")
    return changes


def has_single_shared_template(template_payloads: dict[str, dict[str, Any]]) -> bool:
    shared_template_keys = [
        template_key
        for template_key, payload in template_payloads.items()
        if str(payload.get("assignment_mode", "shared")).strip().lower() == "shared"
    ]
    return len(template_payloads) == 1 and len(shared_template_keys) == 1


def tracked_file_status(file_payload: dict[str, Any]) -> str:
    current_hash = str(file_payload.get("current_hash") or "").strip()
    validated_hash = str(file_payload.get("validated_hash") or "").strip()
    watch = bool(file_payload.get("watch"))
    path = str(file_payload.get("path") or "").strip()
    if validated_hash and current_hash and validated_hash == current_hash:
        return "validated"
    if validated_hash and current_hash and validated_hash != current_hash:
        return "changed_since_validation"
    if validated_hash:
        return "validated"
    if watch and not path:
        return "watching"
    if current_hash:
        return "not_validated"
    return "unresolved"


def _summarize_tracked_files(tracked_files: list[dict[str, Any]]) -> str:
    if not tracked_files:
        return "no_files"
    statuses = [tracked_file_status(item) for item in tracked_files]
    if statuses and all(status == "validated" for status in statuses):
        return "already_checked"
    if any(status == "changed_since_validation" for status in statuses):
        return "changed_since_check"
    if any(status == "watching" for status in statuses):
        return "watching"
    if any(status == "unresolved" for status in statuses):
        return "unresolved"
    if any(status == "not_validated" for status in statuses):
        return "not_checked"
    return statuses[0]


def _entity_check_label(entity_payload: dict[str, Any]) -> str:
    file_payload = entity_payload.get("file", {}) or {}
    tracked_files = list(file_payload.get("tracked_files", []) or [])
    summary = _summarize_tracked_files(tracked_files)
    labels = {
        "already_checked": "hashes match validated run",
        "changed_since_check": "changed since validated run",
        "watching": "watching for file",
        "unresolved": "file unresolved",
        "not_checked": "not yet checked",
        "no_files": "no tracked files",
        "validated": "hashes match validated run",
    }
    return labels.get(summary, summary.replace("_", " "))


def _entity_run_block_reason(entity_payload: dict[str, Any]) -> str | None:
    file_payload = entity_payload.get("file", {}) or {}
    tracked_files = list(file_payload.get("tracked_files", []) or [])
    if not tracked_files:
        return "no tracked files"
    for tracked_file in tracked_files:
        status = tracked_file_status(tracked_file)
        if status in {"unresolved", "watching"}:
            path = str(tracked_file.get("path") or "").strip() or str(tracked_file.get("watch_search_term") or "").strip() or "<unknown>"
            return f"unavailable: {status} ({path})"
    return None


def _submission_check_label(submission: dict[str, Any]) -> str:
    labels: list[str] = []
    for org_payload in (submission.get("organisations", {}) or {}).values():
        if isinstance(org_payload, dict):
            labels.append(_entity_check_label(org_payload))
    for template_payload in (submission.get("templates", {}) or {}).values():
        if isinstance(template_payload, dict):
            labels.append(_entity_check_label(template_payload))
    if not labels:
        return "no tracked files"
    if all(label == "hashes match validated run" for label in labels):
        return "all hashes match validated run"
    if any(label == "changed since validated run" for label in labels):
        return "some files changed since validated run"
    if any(label == "file unresolved" for label in labels):
        return "some files unresolved"
    if any(label == "watching for file" for label in labels):
        return "watching for files"
    return "some files not yet checked"


def _resolve_fabric_portal_context(config: dict[str, Any]) -> dict[str, str] | None:
    pipeline_config = config.get("pipeline", {}) or {}
    workspace_name = str(pipeline_config.get("workspace_display_name", "")).strip()
    pipeline_name = str(pipeline_config.get("pipeline_display_name", "")).strip()
    if not workspace_name or not pipeline_name:
        return None
    common = _common()
    auth = _online_auth()
    workspaces_ok, workspaces_payload = auth.list_fabric_workspaces()
    common.require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    workspace = common.find_by_display_name(workspaces_payload, workspace_name, "Pipeline workspace")
    workspace_id = str(workspace["id"])
    pipelines_ok, pipelines_payload = auth.list_fabric_pipelines(workspace_id)
    common.require_ok("list_fabric_pipelines", pipelines_ok, pipelines_payload)
    pipeline = common.find_by_display_name(pipelines_payload, pipeline_name, "Pipeline")
    return {
        "workspace_id": workspace_id,
        "pipeline_id": str(pipeline["id"]),
    }


def _build_fabric_links(config: dict[str, Any], run_payload: dict[str, Any] | None) -> dict[str, str]:
    if not run_payload:
        return {}
    try:
        portal_context = _resolve_fabric_portal_context(config)
    except Exception:
        return {}
    if not portal_context:
        return {}
    pipeline = run_payload.get("pipeline", {}) or {}
    pipeline_id = str(pipeline.get("pipeline_id") or portal_context.get("pipeline_id") or "").strip()
    pipeline_run_id = str(pipeline.get("pipeline_run_id") or "").strip()
    workspace_id = str(portal_context.get("workspace_id") or "").strip()
    if not workspace_id or not pipeline_id:
        return {}
    links = {
        "pipeline_page": f"https://app.fabric.microsoft.com/groups/{workspace_id}/pipelines/{pipeline_id}",
    }
    if pipeline_run_id:
        links["monitor_page"] = (
            "https://app.powerbi.com/workloads/data-pipeline/monitoring/"
            f"workspaces/{workspace_id}/pipelines/{pipeline_id}/{pipeline_run_id}"
            "?experience=power-bi"
        )
    return links


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
    return q.checkbox(message, choices=choices).ask() or []


def _text(message: str, default: str | None = None) -> str:
    q = _questionary()
    value = q.text(message, default=default or "").ask()
    return str(value or "").strip()


def _confirm(message: str, default: bool = False) -> bool:
    q = _questionary()
    return bool(q.confirm(message, default=default).ask())


def _print_json(payload: Any):
    if hasattr(payload, "to_dict"):
        print(json.dumps(payload.to_dict(), indent=2), flush=True)
        return
    print(json.dumps(payload, indent=2), flush=True)


def _default_config_payload() -> dict[str, Any]:
    return {
        "service": {
            "storage_mode": "fabric",
            "workspace_display_name": "",
            "lakehouse_display_name": "",
            "submissions_events_path": "Files/validation-service/events/submission_events.jsonl",
            "submissions_projection_path": "Files/validation-service/projections/submissions_current.json",
            "idempotency_path": "Files/validation-service/system/idempotency.json",
            "runs_events_path": "Files/validation-service/events/run_events.jsonl",
            "runs_projection_path": "Files/validation-service/projections/runs_current.json",
            "runs_idempotency_path": "Files/validation-service/system/run_idempotency.json",
        },
        "pipeline": {
            "workspace_display_name": "",
            "pipeline_display_name": "",
        },
        "scenario": {
            "submission": {},
        },
    }


def _load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json_file(path: Path, payload: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _prompt_service_config(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _default_config_payload()
    payload = {
        "service": {
            **base["service"],
            **((existing or {}).get("service", {})),
        },
        "pipeline": {
            **base["pipeline"],
            **((existing or {}).get("pipeline", {})),
        },
        "scenario": dict((existing or {}).get("scenario", base["scenario"])),
    }
    service = {
        "storage_mode": "fabric",
        "workspace_display_name": _text("Service workspace display name", str(payload["service"].get("workspace_display_name", ""))),
        "lakehouse_display_name": _text("Service lakehouse display name", str(payload["service"].get("lakehouse_display_name", ""))),
        "submissions_events_path": _text(
            "Submissions events path",
            str(payload["service"].get("submissions_events_path", "")),
        ),
        "submissions_projection_path": _text(
            "Submissions projection path",
            str(payload["service"].get("submissions_projection_path", "")),
        ),
        "idempotency_path": _text(
            "Submission idempotency path",
            str(payload["service"].get("idempotency_path", "")),
        ),
        "runs_events_path": _text(
            "Runs events path",
            str(payload["service"].get("runs_events_path", "")),
        ),
        "runs_projection_path": _text(
            "Runs projection path",
            str(payload["service"].get("runs_projection_path", "")),
        ),
        "runs_idempotency_path": _text(
            "Run idempotency path",
            str(payload["service"].get("runs_idempotency_path", "")),
        ),
    }
    pipeline = {
        "workspace_display_name": _text(
            "Pipeline workspace display name",
            str(payload["pipeline"].get("workspace_display_name", "")),
        ),
        "pipeline_display_name": _text(
            "Pipeline display name",
            str(payload["pipeline"].get("pipeline_display_name", "")),
        ),
    }
    return {
        "service": service,
        "pipeline": pipeline,
        "scenario": payload.get("scenario", {"submission": {}}),
    }


def _load_runtime_config(config_path: str | None) -> tuple[dict[str, Any], Path | None]:
    if config_path:
        return _common().load_config(Path(config_path)), Path(config_path)
    if DEFAULT_CONFIG_PATH.exists():
        return _load_json_file(DEFAULT_CONFIG_PATH), DEFAULT_CONFIG_PATH
    print(f"No local CLI config found. Creating {DEFAULT_CONFIG_PATH.name}.")
    payload = _prompt_service_config()
    _save_json_file(DEFAULT_CONFIG_PATH, payload)
    return payload, DEFAULT_CONFIG_PATH


def _edit_runtime_config(config_path: Path | None, current_config: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    target_path = config_path or DEFAULT_CONFIG_PATH
    updated_config = _prompt_service_config(current_config)
    _save_json_file(target_path, updated_config)
    print(f"Saved config to {target_path}")
    return updated_config, target_path


def _relative_item_path(sharepoint_context: dict[str, Any], item: dict[str, Any]) -> str:
    root_path = str(sharepoint_context.get("root_path", "")).strip("/")
    item_path = str(item.get("relativePath") or item.get("name") or "").strip("/")
    return "/".join(part for part in [root_path, item_path] if part)


def _default_payload(payloads: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    normalized_key = key.strip().upper()
    for candidate_key, payload in payloads.items():
        if str(candidate_key).strip().upper() == normalized_key:
            return dict(payload)
    if payloads:
        first_key = next(iter(payloads))
        return dict(payloads[first_key])
    return {}


def _prompt_sharepoint_payload(label: str, defaults: dict[str, Any]) -> dict[str, Any]:
    folder_url = _text(f"{label} SharePoint link", str(defaults.get("folder_url", "")))
    files_default = ",".join(str(item).strip() for item in defaults.get("files", []) if str(item).strip())
    files_value = _text(f"{label} files (comma-separated)", files_default)
    return {
        "folder_url": folder_url,
        "files": [item.strip() for item in files_value.split(",") if item.strip()],
    }


def _build_payload_from_sharepoint_match(
    sharepoint_context: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    relative_path = str(item.get("relativePath") or item.get("name") or "").strip().strip("/")
    return {
        **sharepoint_context,
        "files": [relative_path],
        "linked_item_id": str(sharepoint_context["linked_item_id"]),
        "linked_item_name": str(sharepoint_context["linked_item_name"]),
        "linked_item_is_folder": True,
    }


def _find_similar_name_matches(items: list[dict[str, Any]], target_name: str) -> list[dict[str, Any]]:
    normalized_target = target_name.strip().upper()
    stem = normalized_target.rsplit(".", 1)[0]
    exact = [item for item in items if str(item.get("name", "")).strip().upper() == normalized_target]
    if exact:
        return exact
    return [
        item
        for item in items
        if stem and stem in str(item.get("name", "")).strip().upper()
    ]


def _choose_match(label: str, sharepoint_context: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
    q = _questionary()
    choices = [
        q.Choice(
            title=f"[{'folder' if bool(item.get('isFolder')) else 'file'}] {_relative_item_path(sharepoint_context, item)}",
            value=item,
        )
        for item in matches
    ]
    return _select(f"{label}: choose a match", choices)


def _maybe_override_detected_files(label: str, payload: dict[str, Any]) -> dict[str, Any]:
    current_files = [str(item).strip() for item in payload.get("files", []) if str(item).strip()]
    if not current_files:
        return payload
    detected_text = ", ".join(current_files)
    if not _confirm(f"Override detected {label} filename(s): {detected_text}", default=False):
        return payload
    override_value = _text(f"{label} filename(s)", ",".join(current_files))
    return {
        **payload,
        "files": [item.strip() for item in override_value.split(",") if item.strip()],
    }


def _prompt_watch_payload(sharepoint_context: dict[str, Any], default_name: str) -> dict[str, Any]:
    return {
        **sharepoint_context,
        "tracked_files": [
            {
                "path": None,
                "watch": True,
                "watch_search_term": default_name.strip().upper(),
            }
        ],
        "linked_item_id": str(sharepoint_context["linked_item_id"]),
        "linked_item_name": str(sharepoint_context["linked_item_name"]),
        "linked_item_is_folder": True,
    }


def _resolve_payload_from_scan(
    sharepoint_context: dict[str, Any],
    scanned_items: list[dict[str, Any]],
    search_term: str,
    *,
    entity_label: str,
    override_label: str,
    watch_label: str,
    manual_label: str,
    exclude_item: dict[str, Any] | None = None,
    allow_watch_fallback: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    prefix_matches = list(find_prefix_matches(scanned_items, search_term)["matches"])
    similar_matches = _find_similar_name_matches(scanned_items, search_term)
    matches = prefix_matches or similar_matches
    if exclude_item is not None:
        excluded_id = str(exclude_item.get("id") or "")
        matches = [item for item in matches if str(item.get("id") or "") != excluded_id]
    if len(matches) > 1:
        selected = _choose_match(entity_label, sharepoint_context, matches)
        payload = _maybe_override_detected_files(
            override_label,
            _build_payload_from_sharepoint_match(sharepoint_context, selected),
        )
        return payload, selected
    if len(matches) == 1:
        payload = _maybe_override_detected_files(
            override_label,
            _build_payload_from_sharepoint_match(sharepoint_context, matches[0]),
        )
        return payload, matches[0]
    if allow_watch_fallback and _confirm(watch_label, default=True):
        return _prompt_watch_payload(sharepoint_context, search_term), None
    return _prompt_sharepoint_payload(manual_label, {}), None


def _choose_company_payload_from_scan(
    sharepoint_context: dict[str, Any],
    scanned_items: list[dict[str, Any]],
    company_acronym: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    return _resolve_payload_from_scan(
        sharepoint_context,
        scanned_items,
        company_acronym,
        entity_label=f"Company {company_acronym}",
        override_label=f"company {company_acronym}",
        watch_label=f"Put a watch for company {company_acronym}",
        manual_label=f"Company {company_acronym} file",
    )


def _choose_template_payload_for_company(
    sharepoint_context: dict[str, Any],
    scanned_items: list[dict[str, Any]],
    company_acronym: str,
    company_item: dict[str, Any] | None,
) -> dict[str, Any]:
    payload, matched_item = _resolve_payload_from_scan(
        sharepoint_context,
        scanned_items,
        company_acronym,
        entity_label=f"Template {company_acronym}",
        override_label=f"template {company_acronym}",
        watch_label=f"Put a watch for template {company_acronym}",
        manual_label=f"Template {company_acronym} file",
        exclude_item=company_item,
        allow_watch_fallback=False,
    )
    current_files = [str(item).strip() for item in payload.get("files", []) if str(item).strip()]
    if current_files:
        return _maybe_override_detected_files(f"template {company_acronym}", payload) if matched_item is not None else payload
    if company_item is not None and _confirm(
        f"No second '{company_acronym}' file found. Reuse the same file for the template",
        default=True,
    ):
        return _maybe_override_detected_files(
            f"template {company_acronym}",
            _build_payload_from_sharepoint_match(sharepoint_context, company_item),
        )
    watch_or_manual_payload, _ = _resolve_payload_from_scan(
        sharepoint_context,
        scanned_items,
        company_acronym,
        entity_label=f"Template {company_acronym}",
        override_label=f"template {company_acronym}",
        watch_label=f"Put a watch for template {company_acronym}",
        manual_label=f"Template {company_acronym} file",
        exclude_item=company_item,
        allow_watch_fallback=True,
    )
    return watch_or_manual_payload


def _prompt_company_payloads_from_sharepoint(
    sharepoint_context: dict[str, Any],
    scanned_items: list[dict[str, Any]],
    company_acronyms: list[str],
    *,
    template_keys_by_company: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    organisations: dict[str, dict[str, Any]] = {}
    for acronym in company_acronyms:
        payload, _ = _choose_company_payload_from_scan(sharepoint_context, scanned_items, acronym)
        organisations[acronym] = {
            **payload,
            "template_keys": list((template_keys_by_company or {}).get(acronym, [acronym])),
        }
    return organisations


def _prompt_org_payloads(
    selected_orgs: list[str],
    organisation_payloads: dict[str, dict[str, Any]],
    selected_templates: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for org_cd in selected_orgs:
        defaults = _default_payload(organisation_payloads, org_cd)
        source_defaults = _prompt_sharepoint_payload(f"Organisation {org_cd}", defaults)
        template_keys_default = ",".join(
            str(item).strip().upper() for item in defaults.get("template_keys", selected_templates) if str(item).strip()
        )
        template_keys_value = _text(
            f"Organisation {org_cd} template keys",
            template_keys_default,
        )
        result[org_cd] = {
            **source_defaults,
            "template_keys": parse_code_csv(template_keys_value),
        }
    return result


def _prompt_template_payloads(
    selected_templates: list[str],
    template_payloads: dict[str, dict[str, Any]],
    selected_orgs: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    assignment_choices = [
        {"name": "shared", "value": "shared"},
        {"name": "per_organisation", "value": "per_organisation"},
    ]
    for template_key in selected_templates:
        defaults = _default_payload(template_payloads, template_key)
        source_defaults = _prompt_sharepoint_payload(f"Template {template_key}", defaults)
        applies_default = ",".join(
            str(item).strip().upper() for item in defaults.get("applies_to", selected_orgs) if str(item).strip()
        )
        applies_value = _text(f"Template {template_key} applies to", applies_default)
        default_assignment = str(defaults.get("assignment_mode", "shared")).strip().lower() or "shared"
        result[template_key] = {
            **source_defaults,
            "assignment_mode": _select(
                f"Template {template_key} assignment mode",
                assignment_choices,
                default=default_assignment,
            ),
            "applies_to": parse_code_csv(applies_value),
        }
    return result


def _prompt_validation_flag(label: str, default_flag: str) -> str:
    normalized_default = str(default_flag or "not_validated").strip().lower() or "not_validated"
    return _select(
        f"{label} validation flag",
        [
            {"name": "not_validated", "value": "not_validated"},
            {"name": "validated", "value": "validated"},
        ],
        default=normalized_default,
    )


def _prompt_note_change(current_note: str) -> str | None:
    action = _select(
        "Submission note",
        [
            {"name": "Keep current note", "value": "keep"},
            {"name": "Edit note", "value": "edit"},
            {"name": "Clear note", "value": "clear"},
        ],
        default="keep",
    )
    if action == "keep":
        return None
    if action == "clear":
        return ""
    updated = _text("Note", current_note)
    return updated if updated != current_note else None


def _build_one_template_one_company_submission(scenario_submission: dict[str, Any]) -> dict[str, Any]:
    sharepoint_link = _text("SharePoint folder link")
    sharepoint_context, scanned_items = resolve_sharepoint_folder_listing(sharepoint_link)
    selected_orgs = parse_code_csv(_text("Company acronyms (comma-separated)"))
    organisations: dict[str, dict[str, Any]] = {}
    templates: dict[str, dict[str, Any]] = {}
    for org_cd in selected_orgs:
        template_key = org_cd
        organisation_source, organisation_match = _choose_company_payload_from_scan(
            sharepoint_context,
            scanned_items,
            org_cd,
        )
        template_source = _choose_template_payload_for_company(
            sharepoint_context,
            scanned_items,
            org_cd,
            organisation_match,
        )
        organisations[org_cd] = {
            **organisation_source,
            "template_keys": [template_key],
        }
        templates[template_key] = {
            **template_source,
            "assignment_mode": "per_organisation",
            "applies_to": [org_cd],
        }
    return {
        **scenario_submission,
        "organisations": organisations,
        "templates": templates,
    }


def _build_shared_template_submission(scenario_submission: dict[str, Any]) -> dict[str, Any]:
    sharepoint_link = _text("SharePoint folder link")
    sharepoint_context, scanned_items = resolve_sharepoint_folder_listing(sharepoint_link)
    template_file_name = _text("Shared template file name")
    template_key = Path(template_file_name).stem.strip().upper() or "TPL_MAIN"
    template_matches = _find_similar_name_matches(scanned_items, template_file_name)
    if len(template_matches) > 1:
        template_matches = [_choose_match("Shared template search", sharepoint_context, template_matches)]
    if len(template_matches) == 1:
        template_source = _maybe_override_detected_files(
            f"shared template {template_key}",
            _build_payload_from_sharepoint_match(sharepoint_context, template_matches[0]),
        )
    else:
        template_source = _prompt_sharepoint_payload(
            f"Shared template {template_key}",
            {"folder_url": sharepoint_link, "files": [template_file_name]},
        )
    selected_orgs = parse_code_csv(_text("Company acronyms (comma-separated)"))
    organisations = _prompt_company_payloads_from_sharepoint(
        sharepoint_context,
        scanned_items,
        selected_orgs,
        template_keys_by_company={org_cd: [template_key] for org_cd in selected_orgs},
    )
    return {
        **scenario_submission,
        "organisations": organisations,
        "templates": {
            template_key: {
                **template_source,
                "assignment_mode": "shared",
                "applies_to": selected_orgs,
            }
        },
    }


def _prompt_create_submission(service: Any, config: dict[str, Any]) -> dict[str, Any]:
    scenario_submission = dict(config.get("scenario", {}).get("submission", {}))
    created_by = _common().resolve_actor(None)
    process_cd = _text("Process code", str(scenario_submission.get("process_cd", "")))
    submission_period_cd = _text(
        "Submission period code",
        str(scenario_submission.get("submission_period_cd", "")),
    )
    note = _text("Note", str(scenario_submission.get("note", "")))
    allow_duplicate_active = bool(scenario_submission.get("allow_duplicate_active", False))
    mode = _select(
        "Validation mode",
        [
            {"name": "One template per company file", "value": "one_template_one_company"},
            {"name": "Shared template across companies", "value": "shared_template"},
        ],
        default="one_template_one_company",
    )
    prompted_submission = (
        _build_one_template_one_company_submission(scenario_submission)
        if mode == "one_template_one_company"
        else _build_shared_template_submission(scenario_submission)
    )
    response = create_submission_from_discovery(
        service,
        process_cd=process_cd,
        submission_period_cd=submission_period_cd,
        organisations_payload=prompted_submission.get("organisations", {}),
        templates_payload=prompted_submission.get("templates", {}),
        created_by=created_by,
        idempotency_key=f"create-submission-cli-{uuid4().hex}",
        note=note or None,
        allow_duplicate_active=allow_duplicate_active,
    ).response
    return {
        "created_by": created_by,
        "create_submission": response.to_dict(),
    }


def _load_run_details(run_api: Any, run_id: str | None) -> dict[str, Any] | None:
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        return None
    result = run_api.get_run(GetRunRequest(run_id=normalized_run_id))
    return result.to_dict() if getattr(result, "ok", False) else None


def _print_run_details(
    run_payload: dict[str, Any] | None,
    *,
    validation_run_id: str | None = None,
    config: dict[str, Any] | None = None,
):
    normalized_run_id = str(validation_run_id or "").strip()
    if not normalized_run_id:
        print("Validation run details: no validation run recorded")
        return
    if not run_payload:
        print(f"Validation run details: unavailable for run ID {normalized_run_id}")
        return
    run = run_payload.get("run", {}) or {}
    pipeline = run_payload.get("pipeline", {}) or {}
    external_metadata = run_payload.get("external_metadata", {}) or {}
    result_summary = run_payload.get("result_summary", {}) or {}
    print("Validation run details:")
    print(f"  Run ID: {run.get('run_id', '-')}")
    print(f"  State: {run.get('state', '-')}")
    print(f"  Requested by: {run.get('requested_by', '-')}")
    print(f"  Requested at: {run.get('requested_ts_utc', '-')}")
    print(f"  Pipeline run ID: {pipeline.get('pipeline_run_id', '-')}")
    print(f"  External status: {external_metadata.get('latest_external_status', '-')}")
    print(f"  Passed assets: {', '.join(result_summary.get('passed_asset_keys', [])) or '-'}")
    print(f"  Failed assets: {', '.join(result_summary.get('failed_asset_keys', [])) or '-'}")
    if config is not None:
        fabric_links = _build_fabric_links(config, run_payload)
        if fabric_links:
            print(f"  Fabric pipeline page: {fabric_links.get('pipeline_page', '-')}")
            print(f"  Fabric monitor page: {fabric_links.get('monitor_page', '-')}")


def _view_tracked_file(file_payload: dict[str, Any], *, heading: str, run_api: Any, config: dict[str, Any]):
    print(f"\n{heading}")
    print(f"  Validation status: {tracked_file_status(file_payload)}")
    print(f"  Path: {file_payload.get('path') or '-'}")
    print(f"  Watch: {'yes' if file_payload.get('watch') else 'no'}")
    print(f"  Watch search term: {file_payload.get('watch_search_term') or '-'}")
    print(f"  Current hash: {file_payload.get('current_hash') or '-'}")
    print(f"  Validated hash: {file_payload.get('validated_hash') or '-'}")
    print(f"  Size bytes: {file_payload.get('size_bytes') if file_payload.get('size_bytes') is not None else '-'}")
    print(f"  Created: {file_payload.get('created') or '-'}")
    print(f"  Modified: {file_payload.get('modified') or '-'}")
    print(f"  Modified by: {file_payload.get('modified_by') or '-'}")
    print(f"  Validation run ID: {file_payload.get('validation_run_id') or '-'}")
    _print_run_details(
        _load_run_details(run_api, file_payload.get("validation_run_id")),
        validation_run_id=file_payload.get("validation_run_id"),
        config=config,
    )
    _select("File view", [{"name": "Back", "value": "back"}], default="back")


def _browse_files(files: list[dict[str, Any]], *, entity_label: str, run_api: Any, config: dict[str, Any]):
    while True:
        if not files:
            print(f"\n{entity_label} has no tracked files.")
            _select("Files", [{"name": "Back", "value": "back"}], default="back")
            return
        choices = []
        for index, file_payload in enumerate(files, start=1):
            path_or_watch = str(file_payload.get("path") or file_payload.get("watch_search_term") or f"file_{index}")
            title = f"{path_or_watch} | {tracked_file_status(file_payload)}"
            choices.append({"name": title, "value": index - 1})
        choices.append({"name": "Back", "value": "back"})
        selected = _select(f"{entity_label} files", choices, default="back")
        if selected == "back":
            return
        _view_tracked_file(files[int(selected)], heading=f"{entity_label} file", run_api=run_api, config=config)


def _refresh_submission_projection(submission_service: Any, submission_id: str) -> dict[str, Any] | None:
    refreshed = submission_service.list_submissions(ListSubmissionsRequest()).data
    if not isinstance(refreshed, list):
        return None
    return next(
        (
            item for item in refreshed
            if isinstance(item, dict) and str(item.get("submission_id")) == str(submission_id)
        ),
        None,
    )


def _browse_submission_entities(submission_service: Any, submission: dict[str, Any], *, config: dict[str, Any], run_api: Any):
    while True:
        orgs = submission.get("organisations", {}) or {}
        templates = submission.get("templates", {}) or {}
        note = str(submission.get("note") or "").strip()
        print("\nSubmission summary:")
        print(f"  Submission ID: {submission.get('submission_id', '-')}")
        print(f"  Process: {submission.get('process_cd', '-')}")
        print(f"  Period: {submission.get('submission_period_cd', '-')}")
        print(f"  State: {submission.get('state', '-')}")
        print(f"  Check status: {_submission_check_label(submission)}")
        print(f"  Note: {note or '-'}")
        print(f"  Companies: {len(orgs)}")
        print(f"  Templates: {len(templates)}")
        choices: list[dict[str, Any]] = []
        choices.append({"name": "Refresh this submission", "value": ("action", "refresh")})
        choices.append({"name": "Edit this submission", "value": ("action", "edit")})
        choices.append({"name": "Run this submission", "value": ("action", "run")})
        choices.append({"name": "View runs for this submission", "value": ("action", "runs")})
        for org_cd, org_payload in sorted(orgs.items()):
            title = (
                f"Company {org_cd} | flag={org_payload.get('validation_flag', '-')} | "
                f"{_entity_check_label(org_payload)} | templates: {', '.join(org_payload.get('template_keys', [])) or '-'}"
            )
            choices.append({"name": title, "value": ("org", org_cd)})
        for template_key, template_payload in sorted(templates.items()):
            title = (
                f"Template {template_key} | flag={template_payload.get('validation_flag', '-')} | "
                f"{_entity_check_label(template_payload)} | applies to: {', '.join(template_payload.get('applies_to', [])) or '-'}"
            )
            choices.append({"name": title, "value": ("template", template_key)})
        choices.append({"name": "Back", "value": "back"})
        selected = _select("Submission contents", choices, default="back")
        if selected == "back":
            return
        entity_type, entity_key = selected
        if entity_type == "action":
            if entity_key == "refresh":
                actor = _common().resolve_actor(None)
                response = submission_service.refresh_submission_hashes(
                    RefreshSubmissionRequest(
                        submission_id=str(submission.get("submission_id")),
                        refreshed_by=actor,
                        idempotency_key=f"refresh-submission-view-{uuid4().hex}",
                    )
                )
                response_payload = response.to_dict()
                _print_json(response_payload)
                updated_submission = response_payload.get("submission")
                if isinstance(updated_submission, dict):
                    submission = updated_submission
            elif entity_key == "edit":
                edit_result = _prompt_edit_submission(submission_service, selected_submission=submission)
                _print_json(edit_result)
                updated_submission = (edit_result.get("edit_submission") or {}).get("submission")
                if isinstance(updated_submission, dict):
                    submission = updated_submission
            elif entity_key == "run":
                _print_json(_prompt_run_submission(submission_service, config, selected_submission=submission))
            elif entity_key == "runs":
                _browse_validation_runs(
                    submission_service,
                    config,
                    submission_id=str(submission.get("submission_id")),
                )
            replacement = _refresh_submission_projection(submission_service, str(submission.get("submission_id")))
            if replacement is not None:
                submission = replacement
            continue
        if entity_type == "org":
            org_payload = orgs[entity_key]
            print(f"\nCompany {entity_key}")
            print(f"  Validation flag: {org_payload.get('validation_flag', '-')}")
            print(f"  Check status: {_entity_check_label(org_payload)}")
            print(f"  Template keys: {', '.join(org_payload.get('template_keys', [])) or '-'}")
            file_payload = org_payload.get("file", {}) or {}
            _browse_files(
                list(file_payload.get("tracked_files", []) or []),
                entity_label=f"Company {entity_key}",
                run_api=run_api,
                config=config,
            )
            continue
        template_payload = templates[entity_key]
        print(f"\nTemplate {entity_key}")
        print(f"  Validation flag: {template_payload.get('validation_flag', '-')}")
        print(f"  Check status: {_entity_check_label(template_payload)}")
        print(f"  Assignment mode: {template_payload.get('assignment_mode', '-')}")
        print(f"  Applies to: {', '.join(template_payload.get('applies_to', [])) or '-'}")
        file_payload = template_payload.get("file", {}) or {}
        _browse_files(
            list(file_payload.get("tracked_files", []) or []),
            entity_label=f"Template {entity_key}",
            run_api=run_api,
            config=config,
        )


def _browse_submissions(submission_service: Any, config: dict[str, Any]):
    run_api = _common().build_run_api(config, submission_service=submission_service)
    while True:
        submissions_response = submission_service.list_submissions(ListSubmissionsRequest())
        submissions = submissions_response.data if submissions_response.ok else None
        if not isinstance(submissions, list) or not submissions:
            print("\nNo submissions are available.")
            _select("Submissions", [{"name": "Back", "value": "back"}], default="back")
            return
        q = _questionary()
        choices = [
            q.Choice(
                title=(
                    f"{item.get('process_cd', '')} | {item.get('submission_period_cd', '')} | "
                    f"state={item.get('state', '-')} | {_submission_check_label(item)} | "
                    f"orgs={len((item.get('organisations', {}) or {}))} | "
                    f"tpls={len((item.get('templates', {}) or {}))} | "
                    f"note={_truncate_text(item.get('note'))}"
                ),
                value=item,
            )
            for item in submissions
            if isinstance(item, dict)
        ]
        choices.append(q.Choice(title="Back", value="back"))
        selected = _select("View submissions", choices, default="back")
        if selected == "back":
            return
        _browse_submission_entities(submission_service, selected, config=config, run_api=run_api)


def _browse_validation_runs(submission_service: Any, config: dict[str, Any], submission_id: str | None = None):
    run_api = _common().build_run_api(config, submission_service=submission_service)
    page = 1
    page_size = 20
    while True:
        runs_response = run_api.list_runs(
            ListRunsRequest(
                submission_id=submission_id,
                page=page,
                page_size=page_size,
            )
        )
        runs = getattr(runs_response, "items", None)
        if not getattr(runs_response, "ok", False) or not isinstance(runs, list) or not runs:
            print("\nNo validation runs are available.")
            _select("Validation runs", [{"name": "Back", "value": "back"}], default="back")
            return
        total = int(getattr(runs_response, "total", len(runs)))
        max_page = max(1, (total + page_size - 1) // page_size)
        q = _questionary()
        run_details_by_id = {
            run.run_id: _load_run_details(run_api, run.run_id) or {}
            for run in runs
        }
        choices = []
        for run in runs:
            run_details = run_details_by_id.get(run.run_id, {})
            pipeline = run_details.get("pipeline", {}) or {}
            fabric_run_id = pipeline.get("pipeline_run_id") or "-"
            choices.append(
                q.Choice(
                    title=(
                        f"fabric_run={fabric_run_id} | requested={run.requested_ts_utc} | "
                        f"{run.process_cd} | {run.submission_period_cd} | "
                        f"state={run.state} | requested_by={run.requested_by}"
                    ),
                    value=run.run_id,
                )
            )
        if page > 1:
            choices.append(q.Choice(title="Previous page", value="__prev__"))
        if page < max_page:
            choices.append(q.Choice(title="Next page", value="__next__"))
        choices.append(q.Choice(title="Back", value="back"))
        selected_run_id = _select(
            (
                f"View validation runs"
                f"{f' for submission {submission_id}' if submission_id else ''}"
                f" (page {page}/{max_page}, total {total})"
            ),
            choices,
            default="back",
        )
        if selected_run_id == "back":
            return
        if selected_run_id == "__prev__":
            page = max(1, page - 1)
            continue
        if selected_run_id == "__next__":
            page = min(max_page, page + 1)
            continue
        run_payload = _load_run_details(run_api, selected_run_id)
        print()
        _print_run_details(run_payload, validation_run_id=selected_run_id, config=config)
        _select("Run view", [{"name": "Back", "value": "back"}], default="back")


def _select_submission(service: Any) -> dict[str, Any]:
    submissions_response = service.list_submissions(ListSubmissionsRequest())
    submissions = submissions_response.data if submissions_response.ok else None
    if not isinstance(submissions, list) or not submissions:
        raise RuntimeError("No submissions are available.")
    q = _questionary()
    choices = [
        q.Choice(
            title=friendly_submission_display(item),
            value=item,
        )
        for item in submissions
        if isinstance(item, dict)
    ]
    return _select("Select submission", choices)


def _manage_company_payloads(
    org_payloads: dict[str, dict[str, Any]],
    template_payloads: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    shared_template_keys = [
        template_key
        for template_key, payload in template_payloads.items()
        if str(payload.get("assignment_mode", "shared")).strip().lower() == "shared"
    ]
    fixed_template_keys = shared_template_keys if len(template_payloads) == 1 and len(shared_template_keys) == 1 else None
    default_folder_url = ""
    for payload in org_payloads.values():
        candidate = str(payload.get("folder_url") or "").strip()
        if candidate:
            default_folder_url = candidate
            break

    while True:
        company_choices = [{"name": org_cd, "value": org_cd} for org_cd in sorted(org_payloads)]
        action = _select(
            "Company action",
            [
                {"name": "Edit existing company", "value": "edit"},
                {"name": "Remove existing company", "value": "remove"},
                {"name": "Add new company", "value": "add"},
                {"name": "Done", "value": "done"},
            ],
            default="done",
        )
        if action == "done":
            return org_payloads
        if action in {"edit", "remove"} and not org_payloads:
            print("No companies available.")
            continue
        if action == "remove":
            org_cd = _select("Remove company", company_choices)
            if _confirm(f"Remove company {org_cd}", default=False):
                org_payloads.pop(org_cd, None)
            continue
        if action == "add":
            org_cd = _text("New company acronym").upper()
        else:
            org_cd = _select("Edit company", company_choices)
        defaults = dict(org_payloads.get(org_cd, {}))
        if fixed_template_keys is not None:
            sharepoint_link = _text(
                f"Organisation {org_cd} SharePoint link",
                str(defaults.get("folder_url") or default_folder_url),
            )
            sharepoint_context, scanned_items = resolve_sharepoint_folder_listing(sharepoint_link)
            source_defaults, _ = _choose_company_payload_from_scan(
                sharepoint_context,
                scanned_items,
                org_cd,
            )
            org_payloads[org_cd] = {
                **source_defaults,
                "template_keys": list(fixed_template_keys),
            }
            continue
        org_payloads[org_cd] = _prompt_org_payloads([org_cd], org_payloads, list(template_payloads.keys()))[org_cd]


def _prompt_edit_submission(service: Any, selected_submission: dict[str, Any] | None = None) -> dict[str, Any]:
    selected_submission = selected_submission or _select_submission(service)
    submission_id = str(selected_submission["submission_id"])
    current_note = str(selected_submission.get("note") or "")
    note_for_api = _prompt_note_change(current_note)
    current_org_payloads = editable_org_payloads(selected_submission)
    current_template_payloads = editable_template_payloads(selected_submission)
    prompted_org_payloads = _manage_company_payloads(
        dict(current_org_payloads),
        dict(current_template_payloads),
    )
    selected_orgs = list(prompted_org_payloads.keys())
    if has_single_shared_template(current_template_payloads):
        prompted_template_payloads = dict(current_template_payloads)
    else:
        selected_templates = parse_code_csv(
            _text("Template keys (comma-separated)", ",".join(current_template_payloads.keys()))
        )
        prompted_template_payloads = _prompt_template_payloads(
            selected_templates,
            current_template_payloads,
            selected_orgs,
        )
    desired_orgs, desired_templates = build_submission_refs(
        {
            "organisations": prompted_org_payloads,
            "templates": prompted_template_payloads,
        }
    )
    existing_org_refs = current_org_refs(selected_submission)
    existing_template_refs = current_template_refs(selected_submission)
    organisation_upserts = {
        org_cd: ref
        for org_cd, ref in desired_orgs.items()
        if org_cd not in existing_org_refs or ref.to_dict() != existing_org_refs[org_cd]
    }
    organisation_removals = [org_cd for org_cd in existing_org_refs if org_cd not in desired_orgs]
    template_upserts = {
        template_key: ref
        for template_key, ref in desired_templates.items()
        if template_key not in existing_template_refs or ref.to_dict() != existing_template_refs[template_key]
    }
    template_removals = [template_key for template_key in existing_template_refs if template_key not in desired_templates]
    organisation_validation_changes: dict[str, str] = {}
    for org_cd in sorted(desired_orgs):
        current_flag = str(
            ((selected_submission.get("organisations", {}) or {}).get(org_cd, {}) or {}).get("validation_flag")
            or "not_validated"
        ).strip().lower()
        updated_flag = _prompt_validation_flag(f"Company {org_cd}", current_flag)
        if updated_flag != current_flag:
            organisation_validation_changes[org_cd] = updated_flag
    template_validation_changes: dict[str, str] = {}
    for template_key in sorted(desired_templates):
        current_flag = str(
            ((selected_submission.get("templates", {}) or {}).get(template_key, {}) or {}).get("validation_flag")
            or "not_validated"
        ).strip().lower()
        updated_flag = _prompt_validation_flag(f"Template {template_key}", current_flag)
        if updated_flag != current_flag:
            template_validation_changes[template_key] = updated_flag
    change_summary = summarize_edit_changes(
        note_for_api=note_for_api,
        organisation_upserts=organisation_upserts,
        organisation_removals=organisation_removals,
        organisation_validation_changes=organisation_validation_changes,
        template_upserts=template_upserts,
        template_removals=template_removals,
        template_validation_changes=template_validation_changes,
    )
    result: dict[str, Any] = {"submission_id": submission_id, "planned_changes": change_summary}
    if not change_summary:
        return result
    print("\nPlanned changes:")
    for item in change_summary:
        print(f"  - {item}")
    if not _confirm("Apply these changes", default=False):
        result["edit_submission"] = {"skipped": True, "reason": "User cancelled before apply."}
        return result
    response = edit_submission_workflow(
        service,
        submission_id=submission_id,
        modified_by=_common().resolve_actor(None),
        idempotency_key=f"edit-submission-cli-{uuid4().hex}",
        organisation_payloads={key: value.to_dict() for key, value in organisation_upserts.items()} if organisation_upserts else None,
        organisation_removals=organisation_removals or None,
        organisation_validation_changes=organisation_validation_changes or None,
        template_payloads={key: value.to_dict() for key, value in template_upserts.items()} if template_upserts else None,
        template_removals=template_removals or None,
        template_validation_changes=template_validation_changes or None,
        note=note_for_api,
    ).response
    result["edit_submission"] = response.to_dict()
    return result


def _checked_choices(
    values: list[str],
    labels_by_value: dict[str, str] | None = None,
    disabled_by_value: dict[str, str] | None = None,
) -> list[Any]:
    q = _questionary()
    return [
        q.Choice(
            title=(labels_by_value or {}).get(value, value),
            value=value,
            checked=value not in (disabled_by_value or {}),
            disabled=(disabled_by_value or {}).get(value),
        )
        for value in values
    ]


def _run_submission_with_progress(
    submission_service: Any,
    run_api: Any,
    *,
    config: dict[str, Any],
    submission_id: str,
    actor: str,
    requested_organisations: list[str] | None,
    requested_template_keys: list[str] | None,
    refresh_submission: bool,
    poll_interval_seconds: float | None,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    if refresh_submission:
        print("\nRefreshing SharePoint metadata...", flush=True)
        refreshed = submission_service.refresh_submission_hashes(
            RefreshSubmissionRequest(
                submission_id=submission_id,
                refreshed_by=actor,
                idempotency_key=f"refresh-submission-before-run-{uuid4().hex}",
            )
        )
        result["refresh_submission_hashes"] = refreshed.to_dict()
        print(f"  Refresh result: {'ok' if refreshed.ok else 'failed'}", flush=True)

    print("\nPlanning run...", flush=True)
    plan = run_api.plan_run(
        PlanRunRequest(
            submission_id=submission_id,
            requested_organisations=requested_organisations or None,
            requested_template_keys=requested_template_keys or None,
        )
    )
    result["plan_run"] = plan.to_dict()
    print(f"  Plan state: {'ok' if plan.ok else 'failed'}", flush=True)
    print(f"  Assets selected: {len(plan.selected_asset_keys)}", flush=True)
    if not plan.ok:
        return result

    print("\nCreating run...", flush=True)
    created = run_api.create_run(
        CreateRunRequest(
            submission_id=submission_id,
            requested_by=actor,
            idempotency_key=f"create-run-{uuid4().hex}",
            requested_organisations=requested_organisations or None,
            requested_template_keys=requested_template_keys or None,
        )
    )
    result["create_run"] = created.to_dict()
    print(f"  Run ID: {created.run.run_id}", flush=True)
    print(f"  Create result: {'ok' if created.ok else 'failed'}", flush=True)
    if not created.ok:
        return result

    print("\nStaging inputs...", flush=True)
    staged = run_api.stage_run_inputs(
        StageRunInputsRequest(
            run_id=created.run.run_id,
            staged_by=actor,
        )
    )
    result["stage_run_inputs"] = staged.to_dict()
    print(f"  Stage state: {staged.state}", flush=True)
    if staged.errors:
        for error in staged.errors:
            print(f"  Stage error: {error.code} | {error.message}", flush=True)
    if staged.state not in {"staged", "partially_succeeded"}:
        result["get_run"] = run_api.get_run(GetRunRequest(run_id=created.run.run_id)).to_dict()
        return result

    print("\nTriggering run...", flush=True)
    triggered = run_api.trigger_run(
        TriggerRunRequest(
            run_id=created.run.run_id,
            triggered_by=actor,
        )
    )
    result["trigger_run"] = triggered.to_dict()
    print(f"  Trigger state: {triggered.state}", flush=True)
    print(f"  Pipeline run ID: {triggered.pipeline.pipeline_run_id or '-'}", flush=True)
    fabric_links = _build_fabric_links(config, result["trigger_run"])
    if fabric_links.get("pipeline_page"):
        print(f"  Fabric pipeline page: {fabric_links['pipeline_page']}", flush=True)
    if fabric_links.get("monitor_page"):
        print(f"  Fabric monitor page: {fabric_links['monitor_page']}", flush=True)
    if not triggered.ok:
        result["get_run"] = run_api.get_run(GetRunRequest(run_id=created.run.run_id)).to_dict()
        return result

    if poll_interval_seconds is not None and timeout_seconds is not None:
        print("\nPolling run status...", flush=True)
        start = time.monotonic()
        polls = 0
        while True:
            poll = run_api.refresh_run_status(RefreshRunStatusRequest(run_id=created.run.run_id))
            polls += 1
            poll_payload = poll.to_dict()
            result["poll_run"] = poll_payload
            state = str(poll.state)
            external_status = str(poll.pipeline_status or "").strip() or "-"
            elapsed_seconds = int(time.monotonic() - start)
            print(
                f"  Poll {polls}: elapsed={elapsed_seconds}s state={state} external_status={external_status}",
                flush=True,
            )
            if poll.latest_error is not None:
                print(f"  Latest error: {poll.latest_error.code} | {poll.latest_error.message}", flush=True)
            if state in {"succeeded", "failed", "cancelled", "canceled", "partially_succeeded"}:
                result["poll_run"]["polls"] = polls
                result["poll_run"]["timed_out"] = False
                break
            if time.monotonic() - start >= max(timeout_seconds, 0.0):
                result["poll_run"]["polls"] = polls
                result["poll_run"]["timed_out"] = True
                result["poll_run"]["ok"] = False
                print("  Polling timed out.", flush=True)
                break
            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)

    result["get_run"] = run_api.get_run(GetRunRequest(run_id=created.run.run_id)).to_dict()
    return result


def _prompt_run_submission(
    submission_service: Any,
    config: dict[str, Any],
    selected_submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_submission = selected_submission or _select_submission(submission_service)
    run_api = _common().build_run_api(config, submission_service=submission_service)
    submission_id = str(selected_submission["submission_id"])
    actor = _common().resolve_actor(None)
    refresh_before_run = _confirm(
        "Refresh SharePoint info before selecting scope. This recomputes stored hashes.",
        default=True,
    )
    if refresh_before_run:
        print("\nRefreshing SharePoint metadata before selection...", flush=True)
        refreshed = submission_service.refresh_submission_hashes(
            RefreshSubmissionRequest(
                submission_id=submission_id,
                refreshed_by=actor,
                idempotency_key=f"refresh-submission-before-scope-{uuid4().hex}",
            )
        )
        print(f"  Refresh result: {'ok' if refreshed.ok else 'failed'}", flush=True)
        refreshed_submission = getattr(refreshed, "data", None)
        if isinstance(refreshed_submission, dict):
            selected_submission = refreshed_submission
        else:
            refreshed_items = submission_service.list_submissions(ListSubmissionsRequest()).data
            if isinstance(refreshed_items, list):
                replacement = next(
                    (
                        item for item in refreshed_items
                        if isinstance(item, dict) and str(item.get("submission_id")) == submission_id
                    ),
                    None,
                )
                if replacement is not None:
                    selected_submission = replacement
        refresh_before_run = False

    organisations = selected_submission.get("organisations", {}) or {}
    organisation_labels = {
        str(org_cd): (
            f"{org_cd} | {_entity_check_label(org_payload)} | "
            f"validation_flag={org_payload.get('validation_flag', '-')}"
        )
        for org_cd, org_payload in organisations.items()
        if isinstance(org_payload, dict)
    }
    organisation_disabled = {
        str(org_cd): reason
        for org_cd, org_payload in organisations.items()
        if isinstance(org_payload, dict)
        for reason in [_entity_run_block_reason(org_payload)]
        if reason is not None
    }
    if organisation_disabled:
        print("\nRun scope warnings:", flush=True)
        for org_cd, reason in organisation_disabled.items():
            print(f"  Company {org_cd} disabled: {reason}", flush=True)
    selected_organisations = _checkbox(
        "Select companies to include",
        _checked_choices(
            [str(item) for item in organisations.keys()],
            organisation_labels,
            organisation_disabled,
        ),
    )

    templates = selected_submission.get("templates", {}) or {}
    template_labels = {
        str(template_key): (
            f"{template_key} | {_entity_check_label(template_payload)} | "
            f"validation_flag={template_payload.get('validation_flag', '-')}"
        )
        for template_key, template_payload in templates.items()
        if isinstance(template_payload, dict)
    }
    template_disabled = {
        str(template_key): reason
        for template_key, template_payload in templates.items()
        if isinstance(template_payload, dict)
        for reason in [_entity_run_block_reason(template_payload)]
        if reason is not None
    }
    if template_disabled:
        if not organisation_disabled:
            print("\nRun scope warnings:", flush=True)
        for template_key, reason in template_disabled.items():
            print(f"  Template {template_key} disabled: {reason}", flush=True)
    selected_templates = _checkbox(
        "Select templates to include",
        _checked_choices(
            [str(item) for item in templates.keys()],
            template_labels,
            template_disabled,
        ),
    )
    if not selected_organisations and not selected_templates:
        return {
            "run_submission": {
                "skipped": True,
                "reason": "No runnable companies or templates were selected.",
            }
        }
    plan = run_api.plan_run(
        PlanRunRequest(
            submission_id=submission_id,
            requested_organisations=selected_organisations or None,
            requested_template_keys=selected_templates or None,
        )
    )
    result: dict[str, Any] = {"plan_run": plan.to_dict()}
    print("\nPlanned run:")
    print(f"  Submission: {submission_id}")
    print(f"  Organisations: {', '.join(plan.selected_organisations) if plan.selected_organisations else '-'}")
    print(f"  Templates: {', '.join(plan.selected_templates) if plan.selected_templates else '-'}")
    print(f"  Assets: {len(plan.selected_asset_keys)}")
    if not _confirm("Create, stage, and trigger this run", default=True):
        result["run_submission"] = {"skipped": True, "reason": "User cancelled before create_run."}
        return result
    poll_interval_seconds = None
    poll_timeout_seconds = None
    if _confirm("Poll run status until terminal state", default=True):
        poll_interval_seconds = float(_text("Poll interval seconds", "10"))
        poll_timeout_seconds = float(_text("Poll timeout seconds", "600"))
    result.update(
        _run_submission_with_progress(
            submission_service,
            run_api,
            config=config,
            submission_id=submission_id,
            actor=actor,
            requested_organisations=selected_organisations or None,
            requested_template_keys=selected_templates or None,
            refresh_submission=refresh_before_run,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=poll_timeout_seconds,
        )
    )
    return result


def _prompt_action(cli_action: str | None) -> str:
    if cli_action:
        return cli_action
    return _select(
        "Choose an action",
        [
            {"name": "View submissions", "value": "view"},
            {"name": "View validation runs", "value": "view_runs"},
            {"name": "Create submission", "value": "create"},
            {"name": "Edit saved config", "value": "edit_config"},
            {"name": "Exit", "value": "exit"},
        ],
        default="view",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interactive terminal UI for the data validation API.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument("--action", choices=["view", "view_runs", "create", "edit", "run", "edit_config"], help="Run a single action directly.")
    args = parser.parse_args(argv)

    config, config_path = _load_runtime_config(args.config)
    if args.action:
        actions = [args.action]
    else:
        actions = None

    authenticated = False

    while True:
        action = actions.pop(0) if actions else _prompt_action(None)

        if action == "exit":
            return 0

        if action == "edit_config":
            config, config_path = _edit_runtime_config(config_path, config)
            if actions is not None and not actions:
                return 0
            continue

        if not authenticated:
            _common().ensure_authenticated()
            authenticated = True

        submission_service = _common().build_submission_service(config["service"])

        if action == "view":
            _browse_submissions(submission_service, config)
        elif action == "view_runs":
            _browse_validation_runs(submission_service, config)
        elif action == "create":
            _print_json(_prompt_create_submission(submission_service, config))
        elif action == "edit":
            _print_json(_prompt_edit_submission(submission_service))
        elif action == "run":
            _print_json(_prompt_run_submission(submission_service, config))
        else:
            raise RuntimeError(f"Unsupported action '{action}'.")

        if actions is not None and not actions:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
