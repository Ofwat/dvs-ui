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
from refactor.services.data_validation_api import ListRunHistoryRequest, ListSubmissionsRequest


def main():
    parser = argparse.ArgumentParser(description="Authenticate and show run history for the first available submission.")
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument("--actor", help="Optional actor filter for run history.")
    args = parser.parse_args()

    config = load_config(Path(args.config)) if args.config else prompt_service_config(include_pipeline=False)
    ensure_authenticated()

    submission_service = build_submission_service(config["service"])
    run_api = build_run_api(config)
    submissions_response = submission_service.list_submissions(ListSubmissionsRequest())
    result: dict[str, object] = {"list_submissions": submissions_response.to_dict()}

    submissions = submissions_response.data if submissions_response.ok else None
    if isinstance(submissions, list) and submissions:
        submission_choices = [
            {
                **item,
                "_display": (
                    f"{item.get('process_cd', '')} | "
                    f"{item.get('submission_period_cd', '')} | "
                    f"{item.get('created_by', '')} | "
                    f"{item.get('created_ts_utc', '')}"
                ),
            }
            for item in submissions
        ]
        selected_submission = prompt_choice_index(
            "submission",
            submission_choices,
            display_key="submission_id",
            default_index=0,
        )
        selected_actor = prompt_text("Requested by filter", args.actor or "", allow_empty=True)
        selected_submission_id = str(selected_submission["submission_id"])
        result["selected_submission_id"] = selected_submission_id
        result["list_run_history"] = run_api.list_run_history(
            ListRunHistoryRequest(
                submission_id=selected_submission_id,
                requested_by=selected_actor or None,
            )
        ).to_dict()

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
