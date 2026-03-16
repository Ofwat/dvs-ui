from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import (
    build_run_api,
    build_submission_service,
    ensure_authenticated,
    load_config,
    prompt_bool,
    prompt_choice_index,
    prompt_service_config,
    prompt_text,
    resolve_actor,
)
from create_submission import prompt_code_list
from refactor.services.data_validation_api import ListSubmissionsRequest, PlanRunRequest
from refactor.services.data_validation_api.workflows import run_submission_workflow


def _friendly_submission_display(item: dict[str, object]) -> str:
    return (
        f"{item.get('process_cd', '')} | "
        f"{item.get('submission_period_cd', '')} | "
        f"{item.get('created_by', '')} | "
        f"{item.get('created_ts_utc', '')}"
    )


def main():
    parser = argparse.ArgumentParser(description="Authenticate and run the validation lifecycle for a selected submission.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    args = parser.parse_args()

    config = load_config(Path(args.config)) if args.config else prompt_service_config(include_pipeline=True)
    ensure_authenticated()

    submission_service = build_submission_service(config["service"])
    run_api = build_run_api(config, submission_service=submission_service)
    result: dict[str, object] = {}

    submissions_response = submission_service.list_submissions(ListSubmissionsRequest())
    result["list_submissions"] = submissions_response.to_dict()
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
    selected_organisations = prompt_code_list(
        "Company acronyms",
        [str(item) for item in selected_submission.get("organisations", {}).keys()],
    )
    selected_templates = prompt_code_list(
        "Template keys",
        [str(item) for item in selected_submission.get("templates", {}).keys()],
    )

    actor = resolve_actor(None)
    refresh_before_run = prompt_bool("Refresh SharePoint metadata before planning the run", default=True)
    plan = run_api.plan_run(
        PlanRunRequest(
            submission_id=submission_id,
            requested_organisations=selected_organisations or None,
            requested_template_keys=selected_templates or None,
        )
    )
    result["plan_run"] = plan.to_dict()
    print("\nPlanned run:")
    print(f"  Submission: {submission_id}")
    print(f"  Organisations: {', '.join(plan.selected_organisations) if plan.selected_organisations else '-'}")
    print(f"  Templates: {', '.join(plan.selected_templates) if plan.selected_templates else '-'}")
    print(f"  Assets: {len(plan.selected_asset_keys)}")
    if not prompt_bool("Create, stage, and trigger this run", default=True):
        result["run_submission"] = {"skipped": True, "reason": "User cancelled before create_run."}
        print(json.dumps(result, indent=2))
        return

    poll_interval_seconds = None
    poll_timeout_seconds = None
    if prompt_bool("Poll run status until terminal state", default=True):
        poll_interval_seconds = float(prompt_text("Poll interval seconds", "10", allow_empty=False))
        poll_timeout_seconds = float(prompt_text("Poll timeout seconds", "600", allow_empty=False))
    result.update(
        run_submission_workflow(
            submission_service,
            run_api,
            submission_id=submission_id,
            actor=actor,
            requested_organisations=selected_organisations or None,
            requested_template_keys=selected_templates or None,
            refresh_submission=refresh_before_run,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=poll_timeout_seconds,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
