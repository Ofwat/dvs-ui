from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from common import (
    build_run_api,
    build_submission_service,
    ensure_authenticated,
    load_config,
    prompt_choice_index,
    prompt_service_config,
    resolve_actor,
)
from refactor.services.data_validation_api import (
    GetRunRequest,
    ListRunHistoryRequest,
    ListSubmissionsRequest,
    RefreshRunStatusRequest,
    RefreshSubmissionRequest,
)


def main():
    parser = argparse.ArgumentParser(description="Authenticate and refresh validation run status for a selected run.")
    parser.add_argument("--config", help="Path to a JSON config file.")
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
        selected_submission_id = str(selected_submission["submission_id"])
        result["selected_submission_id"] = selected_submission_id
        actor = resolve_actor(None)
        result["refresh_submission_hashes"] = submission_service.refresh_submission_hashes(
            RefreshSubmissionRequest(
                submission_id=selected_submission_id,
                refreshed_by=actor,
                idempotency_key=f"refresh-submission-hashes-{uuid4().hex}",
            )
        ).to_dict()

        run_history = run_api.list_run_history(ListRunHistoryRequest(submission_id=selected_submission_id))
        result["list_run_history"] = run_history.to_dict()
        if run_history.items:
            run_choices = [
                {
                    "run_id": item.run.run_id,
                    "state": item.run.state,
                    "requested_ts_utc": item.run.requested_ts_utc,
                }
                for item in run_history.items
            ]
            selected_run = prompt_choice_index(
                "run",
                run_choices,
                display_key="run_id",
                default_index=0,
            )
            selected_run_id = str(selected_run["run_id"])
            result["selected_run_id"] = selected_run_id
            result["refresh_run_status"] = run_api.refresh_run_status(
                RefreshRunStatusRequest(run_id=selected_run_id)
            ).to_dict()
            result["get_run"] = run_api.get_run(GetRunRequest(run_id=selected_run_id)).to_dict()

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
