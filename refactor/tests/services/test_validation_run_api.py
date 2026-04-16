import unittest
from pathlib import Path
import tempfile

from refactor.services.data_validation_api.submission_service import (
    CreateSubmissionRequest,
    JsonFileIdempotencyStore,
    JsonProjectionStore,
    ListSubmissionsRequest,
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
    ListRunningRunsRequest,
    ListRunsRequest,
    PollRunRequest,
    PlanRunRequest,
    RefreshRunningRunsRequest,
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

    def _create_submission(self, service: ValidationServiceApi, request: CreateSubmissionRequest | None = None, **kwargs):
        return service.create_submission(request or CreateSubmissionRequest(**kwargs))

    def _list_submissions(self, service: ValidationServiceApi, **kwargs):
        return service.list_submissions(ListSubmissionsRequest(**kwargs))

    def test_plan_run_resolves_company_and_template_assets(self):
        service = self._submission_service()
        created = self._create_submission(service,
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

    def test_get_run_missing_returns_structured_error(self):
        api = ValidationRunApi(submission_resolver=lambda _submission_id: None)
        result = api.get_run(GetRunRequest(run_id="missing-run"))
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, "RUN_NOT_FOUND")

    def test_trigger_run_not_ready_returns_structured_error(self):
        service = self._submission_service()
        created = self._create_submission(service,
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
            idempotency_key="create-trigger-not-ready",
        )
        assert created.data is not None

        api = ValidationRunApi(
            submission_resolver=lambda submission_id: service._get_submission_projection(submission_id)  # noqa: SLF001
        )
        run = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="create-run-trigger-not-ready",
            )
        )
        triggered = api.trigger_run(TriggerRunRequest(run_id=run.run.run_id, triggered_by="alice@example.com"))
        self.assertFalse(triggered.ok)
        assert triggered.error is not None
        self.assertEqual(triggered.error.code, "RUN_NOT_READY")

    def test_persisted_run_lifecycle(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="validation-run-api-"))
        runs_events_path = temp_dir / "run_events.jsonl"
        runs_projection_path = temp_dir / "runs_current.json"
        runs_idempotency_path = temp_dir / "run_idempotency.json"

        service = self._submission_service()
        created = self._create_submission(service,
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
            submission_hash_persister=service.persist_validation_hashes,
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
        refreshed_submission = self._list_submissions(service, process_cd="PROC_A")
        assert refreshed_submission.data is not None
        org_tracked = refreshed_submission.data[0]["organisations"]["ORG001"]["file"]["tracked_files"][0]
        tpl_tracked = refreshed_submission.data[0]["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]
        self.assertEqual(org_tracked["validated_hash"], "company-hash")
        self.assertEqual(org_tracked["validation_run_id"], created_run.run.run_id)
        self.assertEqual(tpl_tracked["validated_hash"], "template-hash")
        self.assertEqual(tpl_tracked["validation_run_id"], created_run.run.run_id)
        self.assertEqual(listed.total, 1)
        self.assertEqual(history.total, 1)
        self.assertEqual(history.items[0].run.run_id, created_run.run.run_id)
        self.assertEqual(history.items[0].run.state, "succeeded")
        self.assertGreaterEqual(len(history.items[0].status_history), 4)
        self.assertIsNotNone(history.items[0].pipeline)
        self.assertTrue(runs_events_path.exists())
        self.assertTrue(runs_projection_path.exists())
        self.assertTrue(runs_idempotency_path.exists())

    def test_finalize_run_persists_hashes_without_marking_submission_validated(self):
        service = self._submission_service()
        created = self._create_submission(service,
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
            idempotency_key="create-hash-persist-1",
        )
        assert created.data is not None

        api = ValidationRunApi(
            submission_resolver=lambda submission_id: service._get_submission_projection(submission_id),  # noqa: SLF001
            submission_hash_persister=service.persist_validation_hashes,
        )
        created_run = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-hash-persist-1",
                requested_organisations=["ORG001"],
            )
        )
        api.stage_run_inputs(StageRunInputsRequest(run_id=created_run.run.run_id, staged_by="alice@example.com"))
        api.trigger_run(TriggerRunRequest(run_id=created_run.run.run_id, triggered_by="alice@example.com"))

        finalized = api.finalize_run(
            FinalizeRunRequest(
                run_id=created_run.run.run_id,
                finalized_by="alice@example.com",
                result_payload={
                    "asset_results": [
                        {"asset_key": "ORG001_FILE::company.xlsx", "status": "passed"},
                        {"asset_key": "TPL_MAIN::template.xlsx", "status": "passed"},
                    ]
                },
            )
        )

        self.assertEqual(finalized.state, "succeeded")
        self.assertIsNone(finalized.submission_update)
        refreshed_submission = self._list_submissions(service, process_cd="PROC_A")
        assert refreshed_submission.data is not None
        org_entry = refreshed_submission.data[0]["organisations"]["ORG001"]
        tpl_entry = refreshed_submission.data[0]["templates"]["TPL_MAIN"]
        org_tracked = org_entry["file"]["tracked_files"][0]
        tpl_tracked = tpl_entry["file"]["tracked_files"][0]
        self.assertEqual(org_entry["validation_flag"], "not_validated")
        self.assertEqual(tpl_entry["validation_flag"], "not_validated")
        self.assertEqual(org_tracked["validated_hash"], "company-hash")
        self.assertEqual(org_tracked["validation_run_id"], created_run.run.run_id)
        self.assertEqual(tpl_tracked["validated_hash"], "template-hash")
        self.assertEqual(tpl_tracked["validation_run_id"], created_run.run.run_id)

    def test_poll_run_waits_until_terminal_status(self):
        service = self._submission_service()
        created = self._create_submission(service,
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

        statuses = iter(["running", "completed"])

        api = ValidationRunApi(
            submission_resolver=lambda submission_id: service._get_submission_projection(submission_id),  # noqa: SLF001
            submission_flag_setter=service.set_validation_flags,
            submission_hash_persister=service.persist_validation_hashes,
            pipeline_status_resolver=lambda _pipeline, _projection: {"status": next(statuses)},
        )
        created_run = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-1",
                requested_organisations=["ORG001"],
            )
        )
        api.stage_run_inputs(StageRunInputsRequest(run_id=created_run.run.run_id, staged_by="alice@example.com"))
        api.trigger_run(TriggerRunRequest(run_id=created_run.run.run_id, triggered_by="alice@example.com"))

        polled = api.poll_run(
            PollRunRequest(
                run_id=created_run.run.run_id,
                poll_interval_seconds=0.0,
                timeout_seconds=1.0,
            )
        )

        self.assertEqual(polled.state, "succeeded")
        self.assertEqual(polled.pipeline_status, "completed")
        self.assertEqual(polled.polls, 2)
        self.assertFalse(polled.timed_out)
        refreshed_submission = self._list_submissions(service, process_cd="PROC_A")
        assert refreshed_submission.data is not None
        org_tracked = refreshed_submission.data[0]["organisations"]["ORG001"]["file"]["tracked_files"][0]
        tpl_tracked = refreshed_submission.data[0]["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]
        self.assertEqual(org_tracked["validated_hash"], "company-hash")
        self.assertEqual(org_tracked["validation_run_id"], created_run.run.run_id)
        self.assertEqual(tpl_tracked["validated_hash"], "template-hash")
        self.assertEqual(tpl_tracked["validation_run_id"], created_run.run.run_id)

    def test_list_running_runs_returns_only_active_runs_in_descending_order(self):
        service = self._submission_service()
        created = self._create_submission(service,
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
        )
        queued = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-queued",
                requested_organisations=["ORG001"],
            )
        )
        running = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="bob@example.com",
                idempotency_key="run-running",
                requested_organisations=["ORG001"],
            )
        )
        api.stage_run_inputs(StageRunInputsRequest(run_id=running.run.run_id, staged_by="bob@example.com"))
        api.trigger_run(TriggerRunRequest(run_id=running.run.run_id, triggered_by="bob@example.com"))

        succeeded = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-succeeded",
                requested_organisations=["ORG001"],
            )
        )
        api.stage_run_inputs(StageRunInputsRequest(run_id=succeeded.run.run_id, staged_by="alice@example.com"))
        api.trigger_run(TriggerRunRequest(run_id=succeeded.run.run_id, triggered_by="alice@example.com"))
        api.finalize_run(
            FinalizeRunRequest(
                run_id=succeeded.run.run_id,
                finalized_by="alice@example.com",
                result_payload={
                    "asset_results": [
                        {"asset_key": "ORG001_FILE::company.xlsx", "status": "passed"},
                        {"asset_key": "TPL_MAIN::template.xlsx", "status": "passed"},
                    ]
                },
            )
        )

        listed = api.list_running_runs(ListRunningRunsRequest(submission_id=created.data["submission_id"]))
        listed_for_bob = api.list_running_runs(
            ListRunningRunsRequest(
                submission_id=created.data["submission_id"],
                requested_by="bob@example.com",
            )
        )

        self.assertEqual([item.run_id for item in listed.items], [running.run.run_id, queued.run.run_id])
        self.assertEqual([item.state for item in listed.items], ["running", "queued"])
        self.assertEqual(listed.total, 2)
        self.assertEqual([item.run_id for item in listed_for_bob.items], [running.run.run_id])

    def test_refresh_running_runs_refreshes_each_active_run(self):
        service = self._submission_service()
        created = self._create_submission(service,
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

        statuses = iter(["completed"])
        api = ValidationRunApi(
            submission_resolver=lambda submission_id: service._get_submission_projection(submission_id),  # noqa: SLF001
            submission_flag_setter=service.set_validation_flags,
            submission_hash_persister=service.persist_validation_hashes,
            pipeline_status_resolver=lambda _pipeline, _projection: {"status": next(statuses)},
        )
        queued = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-queued",
                requested_organisations=["ORG001"],
            )
        )
        running = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-running",
                requested_organisations=["ORG001"],
            )
        )
        api.stage_run_inputs(StageRunInputsRequest(run_id=running.run.run_id, staged_by="alice@example.com"))
        api.trigger_run(TriggerRunRequest(run_id=running.run.run_id, triggered_by="alice@example.com"))

        refreshed = api.refresh_running_runs(
            RefreshRunningRunsRequest(submission_id=created.data["submission_id"])
        )

        self.assertEqual(refreshed.total, 2)
        self.assertEqual([item.run_id for item in refreshed.items], [running.run.run_id, queued.run.run_id])
        self.assertEqual(refreshed.items[0].state, "succeeded")
        self.assertEqual(refreshed.items[0].pipeline_status, "completed")
        self.assertEqual(refreshed.items[1].state, "queued")
        refreshed_submission = self._list_submissions(service, process_cd="PROC_A")
        assert refreshed_submission.data is not None
        org_tracked = refreshed_submission.data[0]["organisations"]["ORG001"]["file"]["tracked_files"][0]
        tpl_tracked = refreshed_submission.data[0]["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]
        self.assertEqual(org_tracked["validated_hash"], "company-hash")
        self.assertEqual(org_tracked["validation_run_id"], running.run.run_id)
        self.assertEqual(tpl_tracked["validated_hash"], "template-hash")
        self.assertEqual(tpl_tracked["validation_run_id"], running.run.run_id)

    def test_get_run_persists_external_pipeline_metadata(self):
        service = self._submission_service()
        created = self._create_submission(service,
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
            pipeline_trigger=lambda _payload: {
                "pipeline_id": "pipeline-123",
                "pipeline_run_id": "",
                "triggered_ts_utc": "2026-03-09T10:00:00Z",
                "external_metadata": {
                    "pipeline_config_filepaths": [
                        "Files/validation-service/runs/run-1/submission_org001.pipeline_config.json"
                    ]
                },
            },
            pipeline_status_resolver=lambda _pipeline, _projection: {
                "status": "completed",
                "pipeline_run_id": "11111111-1111-1111-1111-111111111111",
            },
        )
        created_run = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-1",
                requested_organisations=["ORG001"],
            )
        )
        api.stage_run_inputs(StageRunInputsRequest(run_id=created_run.run.run_id, staged_by="alice@example.com"))
        triggered = api.trigger_run(TriggerRunRequest(run_id=created_run.run.run_id, triggered_by="alice@example.com"))
        refreshed = api.refresh_run_status(RefreshRunStatusRequest(run_id=created_run.run.run_id))
        fetched = api.get_run(GetRunRequest(run_id=created_run.run.run_id))

        self.assertEqual(triggered.external_metadata.pipeline_config_filepaths, [
            "Files/validation-service/runs/run-1/submission_org001.pipeline_config.json"
        ])
        self.assertEqual(refreshed.external_metadata.latest_external_status, "completed")
        self.assertIsNotNone(refreshed.external_metadata.last_status_sync_ts_utc)
        self.assertIsNone(refreshed.external_metadata.latest_error)
        self.assertIsNotNone(fetched.pipeline)
        self.assertEqual(fetched.pipeline.pipeline_run_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(fetched.external_metadata.pipeline_config_filepaths, [
            "Files/validation-service/runs/run-1/submission_org001.pipeline_config.json"
        ])
        self.assertEqual(fetched.external_metadata.latest_external_status, "completed")

    def test_refresh_run_status_normalizes_pipeline_status_failure(self):
        service = self._submission_service()
        created = self._create_submission(service,
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
            pipeline_status_resolver=lambda _pipeline, _projection: (_ for _ in ()).throw(RuntimeError("bad status payload")),
        )
        created_run = api.create_run(
            CreateRunRequest(
                submission_id=created.data["submission_id"],
                requested_by="alice@example.com",
                idempotency_key="run-1",
                requested_organisations=["ORG001"],
            )
        )
        api.stage_run_inputs(StageRunInputsRequest(run_id=created_run.run.run_id, staged_by="alice@example.com"))
        api.trigger_run(TriggerRunRequest(run_id=created_run.run.run_id, triggered_by="alice@example.com"))

        refreshed = api.refresh_run_status(RefreshRunStatusRequest(run_id=created_run.run.run_id))
        fetched = api.get_run(GetRunRequest(run_id=created_run.run.run_id))

        self.assertEqual(refreshed.state, "running")
        self.assertIsNotNone(refreshed.latest_error)
        self.assertEqual(refreshed.latest_error.code, "PIPELINE_STATUS_FAILED")
        self.assertIsNotNone(refreshed.external_metadata)
        self.assertIsNotNone(refreshed.external_metadata.latest_error)
        self.assertEqual(refreshed.external_metadata.latest_error.code, "PIPELINE_STATUS_FAILED")
        self.assertIsNotNone(fetched.external_metadata)
        self.assertEqual(fetched.external_metadata.latest_error.code, "PIPELINE_STATUS_FAILED")


if __name__ == "__main__":
    unittest.main()
