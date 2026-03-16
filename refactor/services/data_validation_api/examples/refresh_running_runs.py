from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import (
    build_run_api,
    build_submission_service,
    ensure_authenticated,
    load_config,
    prompt_choice_index,
    prompt_service_config,
)
from refactor.services.data_validation_api import ListRunningRunsRequest, ListSubmissionsRequest
from refactor.services.data_validation_api.workflows import refresh_active_runs_workflow


def _friendly_submission_display(item: dict[str, object]) -> str:
    return (
        f"{item.get('process_cd', '')} | "
        f"{item.get('submission_period_cd', '')} | "
        f"{item.get('created_by', '')} | "
        f"{item.get('created_ts_utc', '')}"
    )


def _prompt_requested_by_filter(candidates: list[str]) -> str:
    options = [{"value": "", "_display": "All requestors"}]
    options.extend({"value": candidate, "_display": candidate} for candidate in candidates if candidate)
    selected = prompt_choice_index(
        "requested by filter",
        options,
        display_key="value",
        default_index=0,
    )
    return str(selected["value"])


def main():
    parser = argparse.ArgumentParser(description="Authenticate and refresh all currently running validation runs.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    args = parser.parse_args()

    config = load_config(Path(args.config)) if args.config else prompt_service_config(include_pipeline=True)
    ensure_authenticated()

    submission_service = build_submission_service(config["service"])
    run_api = build_run_api(config, submission_service=submission_service)
    submissions_response = submission_service.list_submissions(ListSubmissionsRequest())
    result: dict[str, object] = {"list_submissions": submissions_response.to_dict()}

    submissions = submissions_response.data if submissions_response.ok else None
    if not isinstance(submissions, list) or not submissions:
        print(json.dumps(result, indent=2))
        return

    submission_options = [{"value": "", "_display": "All submissions"}]
    submission_options.extend(
        {
            "value": str(item.get("submission_id", "")),
            "_display": _friendly_submission_display(item),
        }
        for item in submissions
        if isinstance(item, dict)
    )
    selected_submission = prompt_choice_index(
        "submission filter",
        submission_options,
        display_key="value",
        default_index=0,
    )
    submission_id = str(selected_submission["value"])
    if submission_id:
        result["selected_submission_id"] = submission_id

    preview_runs = run_api.list_running_runs(ListRunningRunsRequest(submission_id=submission_id or None))
    result["list_running_runs"] = preview_runs.to_dict()
    candidate_requestors = sorted({str(item.requested_by) for item in preview_runs.items if str(item.requested_by).strip()})
    requested_by_filter = _prompt_requested_by_filter(candidate_requestors)
    result.update(
        refresh_active_runs_workflow(
            run_api,
            submission_id=submission_id or None,
            requested_by=requested_by_filter or None,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
