from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4
from typing import Any

from common import (
    build_submission_refs,
    build_submission_service,
    ensure_authenticated,
    find_prefix_matches,
    load_config,
    prompt_bool,
    prompt_service_config,
    prompt_text,
    resolve_sharepoint_folder_listing,
    resolve_actor,
)


def prompt_code_list(label: str, defaults: list[str]) -> list[str]:
    default_text = ",".join(defaults)
    raw = prompt_text(f"{label} (comma-separated)", default_text, allow_empty=True)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _default_payload(payloads: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    normalized_key = key.strip().upper()
    for candidate_key, payload in payloads.items():
        if str(candidate_key).strip().upper() == normalized_key:
            return dict(payload)
    if payloads:
        first_key = next(iter(payloads))
        return dict(payloads[first_key])
    return {}


def prompt_sharepoint_payload(label: str, defaults: dict[str, Any]) -> dict[str, Any]:
    files_default = ",".join(str(item).strip() for item in defaults.get("files", []) if str(item).strip())
    files_value = prompt_text(f"{label} files", files_default, allow_empty=True)
    payload = {
        "folder_url": prompt_text(f"{label} SharePoint link", str(defaults.get("folder_url", "")), allow_empty=False),
        "files": [item.strip() for item in files_value.split(",") if item.strip()],
    }
    return payload


def build_payload_from_sharepoint_match(
    sharepoint_context: dict[str, Any],
    item: dict[str, Any],
    *,
    folder_notice_label: str,
) -> dict[str, Any]:
    name = str(item.get("name", "")).strip()
    relative_path = str(item.get("relativePath") or name).strip().strip("/")
    is_folder = bool(item.get("isFolder"))
    if is_folder:
        print(f"{folder_notice_label}: found folder '{relative_path}' but no matching file. A placeholder will be stored for now.")
    else:
        print(f"{folder_notice_label}: using file '{relative_path}'.")
    return {
        **sharepoint_context,
        "files": [relative_path],
        "linked_item_id": str(sharepoint_context["linked_item_id"]),
        "linked_item_name": str(sharepoint_context["linked_item_name"]),
        "linked_item_is_folder": True,
    }


def maybe_override_detected_file(label: str, payload: dict[str, Any]) -> dict[str, Any]:
    current_files = [str(item).strip() for item in payload.get("files", []) if str(item).strip()]
    current_value = ",".join(current_files)
    if not current_value:
        return payload
    if not prompt_bool(f"Override detected {label} filename(s)", default=False):
        return payload
    override_value = prompt_text(f"{label} filename(s)", current_value, allow_empty=False)
    return {
        **payload,
        "files": [item.strip() for item in override_value.split(",") if item.strip()],
    }


def relative_item_path(sharepoint_context: dict[str, Any], item: dict[str, Any]) -> str:
    root_path = str(sharepoint_context.get("root_path", "")).strip("/")
    item_path = str(item.get("relativePath") or item.get("name") or "").strip("/")
    return "/".join(part for part in [root_path, item_path] if part)


def find_similar_name_matches(items: list[dict[str, Any]], target_name: str) -> list[dict[str, Any]]:
    normalized_target = target_name.strip().upper()
    stem = normalized_target.rsplit(".", 1)[0]
    exact = [item for item in items if str(item.get("name", "")).strip().upper() == normalized_target]
    if exact:
        return exact
    contains = [
        item
        for item in items
        if stem and stem in str(item.get("name", "")).strip().upper()
    ]
    return contains


def print_template_match_summary(label: str, sharepoint_context: dict[str, Any], matches: list[dict[str, Any]]):
    if not matches:
        print(f"{label}: no matching file or folder found.")
        return
    if len(matches) == 1:
        item = matches[0]
        item_type = "folder" if bool(item.get("isFolder")) else "file"
        print(f"{label}: found {item_type} at {relative_item_path(sharepoint_context, item)}")
        return
    print(
        f"{label}: found multiple matches: "
        + ", ".join(relative_item_path(sharepoint_context, item) for item in matches)
    )


def choose_match_interactively(label: str, sharepoint_context: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
    print(f"{label}: multiple matches found:")
    for index, item in enumerate(matches, start=1):
        item_type = "folder" if bool(item.get("isFolder")) else "file"
        print(f"  {index}. [{item_type}] {relative_item_path(sharepoint_context, item)}")
    choice = prompt_text("Select match number", "1", allow_empty=False)
    selected_index = int(choice) - 1
    if selected_index < 0 or selected_index >= len(matches):
        print("Invalid selection. Using the first match.")
        return matches[0]
    return matches[selected_index]


def prompt_watch_payload(sharepoint_context: dict[str, Any], *, entity_label: str, default_name: str) -> dict[str, Any]:
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


def resolve_payload_from_scan(
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
    print_not_found_message: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    prefix_matches = list(find_prefix_matches(scanned_items, search_term)["matches"])
    similar_matches = find_similar_name_matches(scanned_items, search_term)
    matches = prefix_matches or similar_matches
    if exclude_item is not None:
        excluded_id = str(exclude_item.get("id") or "")
        matches = [item for item in matches if str(item.get("id") or "") != excluded_id]

    if len(matches) > 1:
        print_template_match_summary(f"{entity_label} search", sharepoint_context, matches)
        selected = choose_match_interactively(
            f"{entity_label} search",
            sharepoint_context,
            matches,
        )
        payload = maybe_override_detected_file(
            override_label,
            build_payload_from_sharepoint_match(
                sharepoint_context,
                selected,
                folder_notice_label=entity_label,
            ),
        )
        return payload, selected

    if len(matches) == 1:
        print_template_match_summary(f"{entity_label} search", sharepoint_context, matches)
        payload = maybe_override_detected_file(
            override_label,
            build_payload_from_sharepoint_match(
                sharepoint_context,
                matches[0],
                folder_notice_label=entity_label,
            ),
        )
        return payload, matches[0]

    if print_not_found_message:
        print(f"{entity_label}: no matching file or folder found under the scanned SharePoint folder.")
    if not allow_watch_fallback:
        return {}, None
    if prompt_bool(watch_label, default=True):
        payload = prompt_watch_payload(
            sharepoint_context,
            entity_label=entity_label,
            default_name=search_term,
        )
        return payload, None

    return prompt_sharepoint_payload(manual_label, {}), None


def choose_company_payload_from_scan(
    sharepoint_context: dict[str, Any],
    scanned_items: list[dict[str, Any]],
    company_acronym: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    return resolve_payload_from_scan(
        sharepoint_context,
        scanned_items,
        company_acronym,
        entity_label=f"Company {company_acronym}",
        override_label=f"company {company_acronym}",
        watch_label=f"Put a watch for company {company_acronym}",
        manual_label=f"Company {company_acronym} file",
    )


def prompt_company_payloads_from_sharepoint(
    sharepoint_context: dict[str, Any],
    scanned_items: list[dict[str, Any]],
    company_acronyms: list[str],
    *,
    template_keys_by_company: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    organisations: dict[str, dict[str, Any]] = {}
    for acronym in company_acronyms:
        payload, _ = choose_company_payload_from_scan(
            sharepoint_context,
            scanned_items,
            acronym,
        )
        organisations[acronym] = {
            **payload,
            "template_keys": list((template_keys_by_company or {}).get(acronym, [acronym])),
        }
    return organisations


def choose_template_payload_for_company(
    sharepoint_context: dict[str, Any],
    scanned_items: list[dict[str, Any]],
    company_acronym: str,
    company_item: dict[str, Any] | None,
) -> dict[str, Any]:
    payload, matched_item = resolve_payload_from_scan(
        sharepoint_context,
        scanned_items,
        company_acronym,
        entity_label=f"Template {company_acronym}",
        override_label=f"template {company_acronym}",
        watch_label=f"Put a watch for template {company_acronym}",
        manual_label=f"Template {company_acronym} file",
        exclude_item=company_item,
        allow_watch_fallback=False,
        print_not_found_message=False,
    )
    current_files = [str(item).strip() for item in payload.get("files", []) if str(item).strip()]
    if current_files:
        return maybe_override_detected_file(f"template {company_acronym}", payload) if matched_item is not None else payload

    if company_item is not None and prompt_bool(
        f"No second '{company_acronym}' file found. Reuse the same file for the template",
        default=True,
    ):
        return maybe_override_detected_file(
            f"template {company_acronym}",
            build_payload_from_sharepoint_match(
                sharepoint_context,
                company_item,
                folder_notice_label=f"Template {company_acronym}",
            ),
        )
    watch_or_manual_payload, _ = resolve_payload_from_scan(
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


def prompt_organisation_payloads(
    selected_orgs: list[str],
    organisation_payloads: dict[str, dict[str, Any]],
    selected_templates: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for org_cd in selected_orgs:
        defaults = _default_payload(organisation_payloads, org_cd)
        source_defaults = prompt_sharepoint_payload(f"Organisation {org_cd}", defaults)
        template_keys_default = ",".join(
            str(item).strip().upper() for item in defaults.get("template_keys", selected_templates) if str(item).strip()
        )
        template_keys_value = prompt_text(
            f"Organisation {org_cd} template keys",
            template_keys_default,
            allow_empty=True,
        )
        result[org_cd] = {
            **source_defaults,
            "template_keys": [item.strip().upper() for item in template_keys_value.split(",") if item.strip()],
        }
    return result


def prompt_template_payloads(
    selected_templates: list[str],
    template_payloads: dict[str, dict[str, Any]],
    selected_orgs: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for template_key in selected_templates:
        defaults = _default_payload(template_payloads, template_key)
        source_defaults = prompt_sharepoint_payload(f"Template {template_key}", defaults)
        applies_default = ",".join(
            str(item).strip().upper() for item in defaults.get("applies_to", selected_orgs) if str(item).strip()
        )
        applies_value = prompt_text(f"Template {template_key} applies to", applies_default, allow_empty=True)
        result[template_key] = {
            **source_defaults,
            "assignment_mode": prompt_text(
                f"Template {template_key} assignment mode",
                str(defaults.get("assignment_mode", "shared")),
                allow_empty=False,
            ),
            "applies_to": [item.strip().upper() for item in applies_value.split(",") if item.strip()],
        }
    return result


def prompt_mode() -> str:
    print("\nValidation mode:")
    print("  1. One template per company file")
    print("  2. Shared template across companies")
    value = prompt_text("Select mode", "1", allow_empty=False)
    if value == "1":
        return "one_template_one_company"
    if value == "2":
        return "shared_template"
    raise RuntimeError(f"Unsupported mode selection '{value}'.")


def build_one_template_one_company_submission(
    scenario_submission: dict[str, Any],
) -> dict[str, Any]:
    sharepoint_link = prompt_text("SharePoint folder link", "", allow_empty=False)
    sharepoint_context, scanned_items = resolve_sharepoint_folder_listing(sharepoint_link)
    selected_orgs = prompt_code_list("Company acronyms", [])
    organisations: dict[str, dict[str, Any]] = {}
    templates: dict[str, dict[str, Any]] = {}

    for org_cd in selected_orgs:
        template_key = org_cd
        organisation_source, organisation_match = choose_company_payload_from_scan(
            sharepoint_context,
            scanned_items,
            org_cd,
        )
        template_source = choose_template_payload_for_company(
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


def build_shared_template_submission(
    scenario_submission: dict[str, Any],
) -> dict[str, Any]:
    sharepoint_link = prompt_text("SharePoint folder link", "", allow_empty=False)
    sharepoint_context, scanned_items = resolve_sharepoint_folder_listing(sharepoint_link)
    template_file_name = prompt_text("Shared template file name", "", allow_empty=False)
    template_key = Path(template_file_name).stem.strip().upper() or "TPL_MAIN"

    template_matches = find_similar_name_matches(scanned_items, template_file_name)
    print_template_match_summary("Shared template search", sharepoint_context, template_matches)
    if len(template_matches) > 1:
        selected_template_match = choose_match_interactively(
            "Shared template search",
            sharepoint_context,
            template_matches,
        )
        template_matches = [selected_template_match]
    if len(template_matches) == 1:
        template_source = maybe_override_detected_file(
            f"shared template {template_key}",
            build_payload_from_sharepoint_match(
                sharepoint_context,
                template_matches[0],
                folder_notice_label=f"Shared template {template_key}",
            ),
        )
    else:
        print(f"Shared template {template_key}: no matching file found under the provided SharePoint folder.")
        template_folder_link = prompt_text("Template folder SharePoint link", "", allow_empty=False)
        template_context, template_items = resolve_sharepoint_folder_listing(template_folder_link)
        template_folder_matches = find_similar_name_matches(template_items, template_file_name)
        print_template_match_summary("Template folder search", template_context, template_folder_matches)
        if len(template_folder_matches) > 1:
            selected_template_match = choose_match_interactively(
                "Template folder search",
                template_context,
                template_folder_matches,
            )
            template_folder_matches = [selected_template_match]
        if len(template_folder_matches) == 0:
            print(f"Template file '{template_file_name}' was not found in the provided template folder.")
            template_source = prompt_sharepoint_payload(f"Shared template {template_key}", {"files": [template_file_name]})
            if not template_source.get("files"):
                template_source["files"] = [template_file_name]
        else:
            template_source = maybe_override_detected_file(
                f"shared template {template_key}",
                build_payload_from_sharepoint_match(
                    template_context,
                    template_folder_matches[0],
                    folder_notice_label=f"Shared template {template_key}",
                ),
            )

    selected_orgs = prompt_code_list("Company acronyms", [])
    organisations = prompt_company_payloads_from_sharepoint(
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


def main():
    parser = argparse.ArgumentParser(description="Authenticate and create a submission using scenario.submission from the config.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    args = parser.parse_args()

    config = load_config(Path(args.config)) if args.config else prompt_service_config(include_pipeline=False)
    ensure_authenticated()

    service = build_submission_service(config["service"])
    scenario_submission = dict(config.get("scenario", {}).get("submission", {}))
    created_by = resolve_actor(None)
    print(f"Created by: {created_by}")
    process_cd = prompt_text("Process code", str(scenario_submission.get("process_cd", "")), allow_empty=False)
    submission_period_cd = prompt_text(
        "Submission period code",
        str(scenario_submission.get("submission_period_cd", "")),
        allow_empty=False,
    )
    note = prompt_text("Note", str(scenario_submission.get("note", "")), allow_empty=True)
    allow_duplicate_active = bool(scenario_submission.get("allow_duplicate_active", False))
    mode = prompt_mode()
    if mode == "one_template_one_company":
        prompted_submission = build_one_template_one_company_submission(scenario_submission)
    else:
        prompted_submission = build_shared_template_submission(scenario_submission)
    filtered_submission = {
        **prompted_submission,
        "process_cd": process_cd,
        "submission_period_cd": submission_period_cd,
        "note": note or None,
        "allow_duplicate_active": allow_duplicate_active,
    }
    organisations, templates = build_submission_refs(filtered_submission)

    response = service.create_submission(
        process_cd=process_cd,
        submission_period_cd=submission_period_cd,
        organisations=organisations,
        templates=templates,
        created_by=created_by,
        idempotency_key=f"create-submission-example-{uuid4().hex}",
        note=note or None,
        allow_duplicate_active=allow_duplicate_active,
    )

    print(json.dumps(response.to_dict(), indent=2))


if __name__ == "__main__":
    main()
