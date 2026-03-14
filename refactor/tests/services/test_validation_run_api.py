import unittest
from pathlib import Path
import tempfile

from refactor.services.data_validation_api.submission_service import (
    JsonFileIdempotencyStore,
    JsonProjectionStore,
    OrganisationSubmissionRef,
    SharePointSourceRef,
    TemplateSubmissionRef,
    TrackedFileRef,
    ValidationServiceApi,
)
from refactor.services.data_validation_api.validation_run_api import (
    CreateRunRequest,
    FinalizeRunRequest,
    GetRunRequest,
    JsonlRunEventStore,
    ListRunHistoryRequest,
    ListRunsRequest,
    PlanRunRequest,
    RefreshRunStatusRequest,
    StageRunInputsRequest,
    ValidationRunApi,
    TriggerRunRequest,
)


class ValidationRunApiTests(unittest.TestCase):
    def _source(self, path: str, *, current_hash: str | None, validated_hash: str | None = None) -> SharePointSourceRef:
        return SharePointSourceRef(
            source_type="drive",
            target_name="Drive A",
            root_path="Shared Documents/Validation",
            tracked_files=[
                TrackedFileRef(
                    path=path,
                    current_hash=current_hash,
                    validated_hash=validated_hash,
                )
            ],
        )

    def _submission_service(self) -> ValidationServiceApi:
        return ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path="Files/validation-service/events/submission_events.jsonl",
        )

    def test_plan_run_resolves_company_and_template_assets(self):
        service = self._submission_service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={
                "ORG001": OrganisationSubmissionRef(
                    sharepoint_source=self._source("company.xlsx", current_hash="company-hash"),
                    template_keys=["TPL_MAIN"],
                )
            },
            templates={
                "TPL_MAIN": TemplateSubmissionRef(
                    assignment_mode="shared",
                    applies_to=["ORG001"],
                    sharepoint_source=self._source("template.xlsx", current_hash="template-hash", validated_hash="template-hash"),
                )
            },
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None

        api = ValidationRunApi(
            submission_resolver=lambda submission_id: service._get_submission_projection(submission_id)  # noqa: SLF001
        )
        planned = api.plan_run(
            PlanRunRequest(
                submission_id=created.data["submission_id"],
                requested_organisations=["ORG001"],
            )
        )

        self.assertEqual(planned.selected_organisations, ["ORG001"])
        self.assertEqual(planned.selected_templates, ["TPL_MAIN"])
        self.assertEqual(len(planned.asset_plan), 2)
        statuses = {item["asset_key"]: item["status"] for item in planned.asset_plan}
        self.assertEqual(statuses["TPL_MAIN::template.xlsx"], "already_validated")
        self.assertEqual(statuses["ORG001_FILE::company.xlsx"], "not_yet_validated")

    def test_persisted_run_lifecycle(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="validation-run-api-"))
        runs_events_path = temp_dir / "run_events.jsonl"
        runs_projection_path = temp_dir / "runs_current.json"
        runs_idempotency_path = temp_dir / "run_idempotency.json"

        service = self._submission_service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={
                "ORG001": OrganisationSubmissionRef(
                    sharepoint_source=self._source("company.xlsx", current_hash="company-hash"),
                    template_keys=["TPL_MAIN"],
                )
            },
            templates={
                "TPL_MAIN": TemplateSubmissionRef(
                    assignment_mode="shared",
                    applies_to=["ORG001"],
                    sharepoint_source=self._source("template.xlsx", current_hash="template-hash"),
                )
            },
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None

        api = ValidationRunApi(
            submission_resolver=lambda submission_id: service._get_submission_projection(submission_id),  # noqa: SLF001
            submission_flag_setter=service.set_validation_flags,
            event_store=JsonlRunEventStore(runs_events_path),
            idempotency_store=JsonFileIdempotencyStore(runs_idempotency_path),
            runs_projection_store=JsonProjectionStore(runs_projection_path),
        )
        created_run = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-1",
                requested_organisations=["ORG001"],
            )
        )
        staged = api.stage_run_inputs(StageRunInputsRequest(run_id=created_run.run.run_id, staged_by="alice@example.com"))
        triggered = api.trigger_run(TriggerRunRequest(run_id=created_run.run.run_id, triggered_by="alice@example.com"))
        status = api.refresh_run_status(RefreshRunStatusRequest(run_id=created_run.run.run_id))
        finalized = api.finalize_run(
            FinalizeRunRequest(
                run_id=created_run.run.run_id,
                finalized_by="alice@example.com",
                apply_validation_flags=True,
                result_payload={
                    "asset_results": [
                        {"asset_key": "ORG001_FILE::company.xlsx", "status": "passed"},
                        {"asset_key": "TPL_MAIN::template.xlsx", "status": "passed"},
                    ]
                },
            )
        )
        fetched = api.get_run(GetRunRequest(run_id=created_run.run.run_id))
        listed = api.list_runs(ListRunsRequest(submission_id=created.data["submission_id"]))
        history = api.list_run_history(ListRunHistoryRequest(submission_id=created.data["submission_id"]))

        self.assertEqual(staged.state, "staged")
        self.assertEqual(len(staged.staged_assets), 2)
        self.assertEqual(triggered.state, "running")
        self.assertEqual(status.state, "running")
        self.assertEqual(finalized.state, "succeeded")
        self.assertEqual(finalized.submission_update, {"submission_id": created.data["submission_id"], "state": "validated"})
        self.assertEqual(fetched.run.state, "succeeded")
        self.assertEqual(listed.total, 1)
        self.assertEqual(history.total, 1)
        self.assertEqual(history.items[0].run.run_id, created_run.run.run_id)
        self.assertEqual(history.items[0].run.state, "succeeded")
        self.assertGreaterEqual(len(history.items[0].status_history), 4)
        self.assertIsNotNone(history.items[0].pipeline)
        self.assertTrue(runs_events_path.exists())
        self.assertTrue(runs_projection_path.exists())
        self.assertTrue(runs_idempotency_path.exists())


if __name__ == "__main__":
    unittest.main()
