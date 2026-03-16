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
    prompt_text,
)
from refactor.services.data_validation_api import ListRunsRequest, ListSubmissionsRequest


def _friendly_submission_display(item: dict[str, object]) -> str:
    return (
        f"{item.get('process_cd', '')} | "
        f"{item.get('submission_period_cd', '')} | "
        f"{item.get('created_by', '')} | "
        f"{item.get('created_ts_utc', '')}"
    )


def _prompt_run_state_filter(default: str = "") -> str:
    options = [
        {"value": "", "_display": "All states"},
        {"value": "queued", "_display": "queued"},
        {"value": "running", "_display": "running"},
        {"value": "staged", "_display": "staged"},
        {"value": "partially_succeeded", "_display": "partially_succeeded"},
        {"value": "succeeded", "_display": "succeeded"},
        {"value": "failed", "_display": "failed"},
        {"value": "cancelled", "_display": "cancelled"},
    ]
    default_index = next(
        (index for index, item in enumerate(options) if item["value"] == (default or "")),
        0,
    )
    selected = prompt_choice_index(
        "run state filter",
        options,
        display_key="value",
        default_index=default_index,
    )
    return str(selected["value"])


def _prompt_requested_by_filter(candidates: list[str], default: str = "") -> str:
    options = [{"value": "", "_display": "All requestors"}]
    options.extend(
        {"value": candidate, "_display": candidate}
        for candidate in candidates
        if candidate
    )
    default_index = next(
        (index for index, item in enumerate(options) if item["value"] == (default or "")),
        0,
    )
    selected = prompt_choice_index(
        "requested by filter",
        options,
        display_key="value",
        default_index=default_index,
    )
    return str(selected["value"])


def main():
    parser = argparse.ArgumentParser(description="Authenticate and list validation runs for a selected submission.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument("--state", help="Optional run state filter.")
    parser.add_argument("--requested-by", help="Optional requested_by filter.")
    args = parser.parse_args()

    config = load_config(Path(args.config)) if args.config else prompt_service_config(include_pipeline=False)
    ensure_authenticated()

    submission_service = build_submission_service(config["service"])
    run_api = build_run_api(config, submission_service=submission_service)
    submissions_response = submission_service.list_submissions(ListSubmissionsRequest())
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
    result["selected_submission_id"] = submission_id
    state_filter = _prompt_run_state_filter(args.state or "")
    preview_runs = run_api.list_runs(ListRunsRequest(submission_id=submission_id))
    candidate_requestors = sorted(
        {
            str(item.requested_by)
            for item in preview_runs.items
            if str(item.requested_by).strip()
        }
    )
    requested_by_filter = _prompt_requested_by_filter(candidate_requestors, args.requested_by or "")
    result["list_runs"] = run_api.list_runs(
        ListRunsRequest(
            submission_id=submission_id,
            state=state_filter or None,
            requested_by=requested_by_filter or None,
        )
    ).to_dict()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
