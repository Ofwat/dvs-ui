from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from common import (
    build_submission_service,
    ensure_authenticated,
    load_config,
    prompt_choice_index,
    prompt_bool,
    prompt_service_config,
    prompt_text,
    resolve_actor,
)
from create_submission import (
    choose_company_payload_from_scan,
    prompt_code_list,
    prompt_organisation_payloads,
    prompt_template_payloads,
)
from refactor.services.data_validation_api.workflows import (
    build_submission_refs,
    edit_submission_workflow,
    resolve_sharepoint_folder_listing,
)
from refactor.services.data_validation_api import ListSubmissionsRequest, OrganisationSubmissionRef, TemplateSubmissionRef


def _friendly_submission_display(item: dict[str, object]) -> str:
    return (
        f"{item.get('process_cd', '')} | "
        f"{item.get('submission_period_cd', '')} | "
        f"{item.get('created_by', '')} | "
        f"{item.get('created_ts_utc', '')}"
    )


def _prompt_validation_flag(label: str, default_flag: str) -> str:
    normalized_default = str(default_flag or "not_validated").strip().lower() or "not_validated"
    print(f"\n{label} validation flag:")
    print("  1. not_validated")
    print("  2. validated")
    default_choice = "2" if normalized_default == "validated" else "1"
    value = prompt_text("Select validation flag", default_choice, allow_empty=False)
    if value == "2":
        return "validated"
    return "not_validated"


def _editable_org_payloads(projection: dict[str, object]) -> dict[str, dict[str, object]]:
    organisations = projection.get("organisations", {})
    payloads: dict[str, dict[str, object]] = {}
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


def _editable_template_payloads(projection: dict[str, object]) -> dict[str, dict[str, object]]:
    templates = projection.get("templates", {})
    payloads: dict[str, dict[str, object]] = {}
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


def _current_org_refs(projection: dict[str, object]) -> dict[str, dict[str, object]]:
    organisations = projection.get("organisations", {})
    refs: dict[str, dict[str, object]] = {}
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


def _current_template_refs(projection: dict[str, object]) -> dict[str, dict[str, object]]:
    templates = projection.get("templates", {})
    refs: dict[str, dict[str, object]] = {}
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


def _print_company_summary(org_payloads: dict[str, dict[str, object]]):
    print("\nCurrent companies:")
    if not org_payloads:
        print("  none")
        return
    for org_cd in sorted(org_payloads):
        template_keys = org_payloads[org_cd].get("template_keys", [])
        template_text = ", ".join(str(item) for item in template_keys) if isinstance(template_keys, list) and template_keys else "-"
        print(f"  {org_cd} -> templates: {template_text}")


def _choose_existing_company(label: str, org_payloads: dict[str, dict[str, object]]) -> str:
    choices = [{"org_cd": org_cd, "_display": org_cd} for org_cd in sorted(org_payloads)]
    selected = prompt_choice_index(label, choices, display_key="org_cd", default_index=0)
    return str(selected["org_cd"])


def _manage_company_payloads(
    org_payloads: dict[str, dict[str, object]],
    template_payloads: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
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

    def prompt_company_update(org_cd: str) -> dict[str, object]:
        if fixed_template_keys is None:
            return prompt_organisation_payloads(
                [org_cd],
                org_payloads,
                list(template_payloads.keys()),
            )[org_cd]
        defaults = dict(org_payloads.get(org_cd, {}))
        folder_url_default = str(defaults.get("folder_url") or default_folder_url).strip()
        sharepoint_link = prompt_text(
            f"Organisation {org_cd} SharePoint link",
            folder_url_default,
            allow_empty=False,
        )
        sharepoint_context, scanned_items = resolve_sharepoint_folder_listing(sharepoint_link)
        source_defaults, _ = choose_company_payload_from_scan(
            sharepoint_context,
            scanned_items,
            org_cd,
        )
        return {
            **source_defaults,
            "template_keys": list(fixed_template_keys),
        }

    while True:
        _print_company_summary(org_payloads)
        print("\nCompany actions:")
        print("  1. Edit existing company")
        print("  2. Remove existing company")
        print("  3. Add new company")
        print("  4. Done")
        choice = prompt_text("Select company action", "4", allow_empty=False)

        if choice == "1":
            if not org_payloads:
                print("No companies available to edit.")
                continue
            org_cd = _choose_existing_company("company", org_payloads)
            org_payloads[org_cd] = prompt_company_update(org_cd)
            continue

        if choice == "2":
            if not org_payloads:
                print("No companies available to remove.")
                continue
            org_cd = _choose_existing_company("company", org_payloads)
            if prompt_bool(f"Remove company {org_cd}", default=False):
                org_payloads.pop(org_cd, None)
            continue

        if choice == "3":
            org_cd = prompt_text("New company acronym", "", allow_empty=False).strip().upper()
            if not org_cd:
                print("A company acronym is required.")
                continue
            org_payloads[org_cd] = prompt_company_update(org_cd)
            continue

        if choice == "4":
            return org_payloads

        print("Unsupported option.")


def _has_single_shared_template(template_payloads: dict[str, dict[str, object]]) -> bool:
    shared_template_keys = [
        template_key
        for template_key, payload in template_payloads.items()
        if str(payload.get("assignment_mode", "shared")).strip().lower() == "shared"
    ]
    return len(template_payloads) == 1 and len(shared_template_keys) == 1


def _summarize_edit_changes(
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


def main():
    parser = argparse.ArgumentParser(description="Authenticate and edit an existing submission.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    args = parser.parse_args()

    config = load_config(Path(args.config)) if args.config else prompt_service_config(include_pipeline=False)
    ensure_authenticated()

    service = build_submission_service(config["service"])
    submissions_response = service.list_submissions(ListSubmissionsRequest())
    result: dict[str, object] = {"list_submissions": submissions_response.to_dict()}
    submissions = submissions_response.data if submissions_response.ok else None
    if not isinstance(submissions, list) or not submissions:
        print(json.dumps(result, indent=2))
        return

    selected_submission = prompt_choice_index(
        "submission",
        [{**item, "_display": _friendly_submission_display(item)} for item in submissions if isinstance(item, dict)],
        display_key="submission_id",
        default_index=0,
    )
    submission_id = str(selected_submission["submission_id"])
    current_note = str(selected_submission.get("note") or "")

    note_input = prompt_text("Note (enter '-' to clear)", current_note, allow_empty=True)
    note_for_api: str | None
    if note_input == "-":
        note_for_api = ""
    elif note_input == current_note:
        note_for_api = None
    else:
        note_for_api = note_input

    current_org_payloads = _editable_org_payloads(selected_submission)
    current_template_payloads = _editable_template_payloads(selected_submission)
    prompted_org_payloads = _manage_company_payloads(
        dict(current_org_payloads),
        dict(current_template_payloads),
    )
    selected_orgs = list(prompted_org_payloads.keys())
    if _has_single_shared_template(current_template_payloads):
        prompted_template_payloads = dict(current_template_payloads)
    else:
        selected_templates = prompt_code_list("Template keys", list(current_template_payloads.keys()))
        prompted_template_payloads = prompt_template_payloads(
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
    current_org_refs = _current_org_refs(selected_submission)
    current_template_refs = _current_template_refs(selected_submission)

    organisation_upserts = {
        org_cd: ref
        for org_cd, ref in desired_orgs.items()
        if org_cd not in current_org_refs or ref.to_dict() != current_org_refs[org_cd]
    }
    organisation_removals = [org_cd for org_cd in current_org_refs if org_cd not in desired_orgs]
    template_upserts = {
        template_key: ref
        for template_key, ref in desired_templates.items()
        if template_key not in current_template_refs or ref.to_dict() != current_template_refs[template_key]
    }
    template_removals = [template_key for template_key in current_template_refs if template_key not in desired_templates]
    organisation_validation_changes = {}
    for org_cd in sorted(desired_orgs):
        current_flag = str(
            ((selected_submission.get("organisations", {}) or {}).get(org_cd, {}) or {}).get("validation_flag")
            or "not_validated"
        ).strip().lower()
        updated_flag = _prompt_validation_flag(f"Company {org_cd}", current_flag)
        if updated_flag != current_flag:
            organisation_validation_changes[org_cd] = updated_flag
    template_validation_changes = {}
    for template_key in sorted(desired_templates):
        current_flag = str(
            ((selected_submission.get("templates", {}) or {}).get(template_key, {}) or {}).get("validation_flag")
            or "not_validated"
        ).strip().lower()
        updated_flag = _prompt_validation_flag(f"Template {template_key}", current_flag)
        if updated_flag != current_flag:
            template_validation_changes[template_key] = updated_flag

    change_summary = _summarize_edit_changes(
        note_for_api=note_for_api,
        organisation_upserts=organisation_upserts,
        organisation_removals=organisation_removals,
        organisation_validation_changes=organisation_validation_changes,
        template_upserts=template_upserts,
        template_removals=template_removals,
        template_validation_changes=template_validation_changes,
    )
    result["planned_changes"] = change_summary
    print("\nPlanned changes:")
    if change_summary:
        for item in change_summary:
            print(f"  - {item}")
    else:
        print("  No changes detected.")
    if not change_summary:
        print(json.dumps(result, indent=2))
        return
    if not prompt_bool("Apply these changes", default=False):
        result["edit_submission"] = {"skipped": True, "reason": "User cancelled before apply."}
        print(json.dumps(result, indent=2))
        return

    response = edit_submission_workflow(
        service,
        submission_id=submission_id,
        modified_by=resolve_actor(None),
        idempotency_key=f"edit-submission-example-{uuid4().hex}",
        organisation_payloads={key: value.to_dict() for key, value in organisation_upserts.items()} if organisation_upserts else None,
        organisation_removals=organisation_removals or None,
        organisation_validation_changes=organisation_validation_changes or None,
        template_payloads={key: value.to_dict() for key, value in template_upserts.items()} if template_upserts else None,
        template_removals=template_removals or None,
        template_validation_changes=template_validation_changes or None,
        note=note_for_api,
    ).response

    result["edit_submission"] = response.to_dict()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
