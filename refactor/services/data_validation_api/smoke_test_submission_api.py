from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from refactor.services.data_validation_api.submission_service import (
        ValidationServiceApi,
    )
except ModuleNotFoundError:
    from refactor.services.data_validation_api.submission_service import ValidationServiceApi


def pretty(label: str, payload: dict[str, Any]):
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2))


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a smoke test for the submission API using a JSON config file."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("smoke_test_submission_api.config.json"),
        help="Path to the smoke test JSON config file.",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)

    service_config = config["service"]
    scenario = config["scenario"]
    submission = scenario["submission"]

    api = ValidationServiceApi(
        workspace_id=service_config["workspace_id"],
        submissions_registry_path=service_config["submissions_registry_path"],
    )

    create_response = api.create_submission(
        process_cd=submission["process_cd"],
        submission_period_cd=submission["submission_period_cd"],
        organisation_paths=submission["organisation_paths"],
        created_by=submission["created_by"],
        idempotency_key=submission["idempotency_key"],
        allow_duplicate_active=submission.get("allow_duplicate_active", False),
    )
    pretty("create_submission", create_response.to_dict())

    list_filters = scenario.get("list_filters", {})
    list_response = api.list_submissions(
        process_cd=list_filters.get("process_cd"),
        submission_period_cd=list_filters.get("submission_period_cd"),
        state=list_filters.get("state"),
        created_by=list_filters.get("created_by"),
    )
    pretty("list_submissions", list_response.to_dict())

    if not create_response.ok or not create_response.data:
        print("\nCannot continue smoke test: create_submission failed.")
        return

    submission_id = create_response.data["submission_id"]

    for index, update in enumerate(scenario.get("flag_updates", []), start=1):
        response = api.set_organisation_validation_flags(
            submission_id=submission_id,
            changes=update["changes"],
            modified_by=update["modified_by"],
            idempotency_key=update["idempotency_key"],
            reason=update.get("reason"),
        )
        pretty(f"set_organisation_validation_flags ({index})", response.to_dict())

    replay = scenario.get("replay_create")
    if replay:
        replay_response = api.create_submission(
            process_cd=replay["process_cd"],
            submission_period_cd=replay["submission_period_cd"],
            organisation_paths=replay["organisation_paths"],
            created_by=replay["created_by"],
            idempotency_key=replay["idempotency_key"],
            allow_duplicate_active=replay.get("allow_duplicate_active", False),
        )
        pretty("create_submission replay", replay_response.to_dict())

    conflict = scenario.get("conflict_create")
    if conflict:
        conflict_response = api.create_submission(
            process_cd=conflict["process_cd"],
            submission_period_cd=conflict["submission_period_cd"],
            organisation_paths=conflict["organisation_paths"],
            created_by=conflict["created_by"],
            idempotency_key=conflict["idempotency_key"],
            allow_duplicate_active=conflict.get("allow_duplicate_active", False),
        )
        pretty("create_submission conflict", conflict_response.to_dict())


if __name__ == "__main__":
    main()
