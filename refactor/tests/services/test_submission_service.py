import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from refactor.services.data_validation_api.submission_service import (
    VALIDATION_NOT_VALIDATED,
    VALIDATION_VALIDATED,
    JsonFileIdempotencyStore,
    JsonlSubmissionEventStore,
    OrganisationSubmissionRef,
    SharePointSourceRef,
    TemplateSubmissionRef,
    TrackedFileRef,
    ValidationServiceApi,
)


class SubmissionServiceTests(unittest.TestCase):
    def _source_ref(self, source_type: str, target_name: str, *tracked_files: str) -> SharePointSourceRef:
        return SharePointSourceRef(
            source_type=source_type,
            target_name=target_name,
            root_path="Shared Documents/Validation",
            tracked_files=[
                TrackedFileRef(path=item, current_hash=f"hash-{index}")
                for index, item in enumerate(tracked_files, start=1)
            ],
        )

    def _org_ref(self, source_type: str, target_name: str, tracked_file: str, *template_keys: str) -> OrganisationSubmissionRef:
        return OrganisationSubmissionRef(
            sharepoint_source=self._source_ref(source_type, target_name, tracked_file),
            template_keys=[item.upper() for item in template_keys],
        )

    def _template_ref(self, source_type: str, target_name: str, tracked_file: str, *applies_to: str) -> TemplateSubmissionRef:
        return TemplateSubmissionRef(
            assignment_mode="shared",
            applies_to=[item.upper() for item in applies_to],
            sharepoint_source=self._source_ref(source_type, target_name, tracked_file),
        )

    def _service(self) -> ValidationServiceApi:
        return ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path="Files/validation-service/events/submission_events.jsonl",
        )

    def test_create_submission_and_list_submissions(self):
        service = self._service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={
                "org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN"),
                "org2": self._org_ref("group", "Group B", "nested/org2/file.xlsx", "TPL_MAIN"),
            },
            templates={
                "tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1", "ORG2"),
            },
            created_by="alice@example.com",
            idempotency_key="create-1",
            note="Initial submission note",
        )

        self.assertTrue(created.ok)
        assert created.data is not None
        self.assertEqual(created.data["state"], "ready")
        self.assertEqual(created.data["note"], "Initial submission note")
        self.assertEqual(created.data["last_modified_by"], "alice@example.com")
        self.assertEqual(created.data["last_modified_ts_utc"], created.data["created_ts_utc"])
        self.assertEqual(created.data["organisations"]["ORG1"]["template_keys"], ["TPL_MAIN"])
        self.assertEqual(created.data["templates"]["TPL_MAIN"]["validation_flag"], VALIDATION_NOT_VALIDATED)
        self.assertEqual(created.data["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]["validated_hash"], None)

        listed = service.list_submissions(process_cd="PROC_A")
        self.assertTrue(listed.ok)
        assert listed.data is not None
        self.assertEqual(len(listed.data), 1)

    def test_create_submission_rejects_duplicate_active(self):
        service = self._service()
        first = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        self.assertTrue(first.ok)

        second = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org2": self._org_ref("group", "Group B", "org2/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-2",
        )
        self.assertFalse(second.ok)
        assert second.error is not None
        self.assertEqual(second.error.code, "DUPLICATE_ACTIVE_SUBMISSION")

    def test_set_validation_flags_updates_org_and_template_state(self):
        service = self._service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN")},
            templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None
        submission_id = created.data["submission_id"]

        partial = service.set_validation_flags(
            submission_id=submission_id,
            organisation_changes={"org1": VALIDATION_VALIDATED},
            template_changes=None,
            modified_by="bob@example.com",
            idempotency_key="flag-1",
        )
        self.assertTrue(partial.ok)
        assert partial.data is not None
        self.assertEqual(partial.data["organisations"]["ORG1"]["company_validation_flag"], VALIDATION_VALIDATED)
        self.assertEqual(partial.data["organisations"]["ORG1"]["validation_flag"], "partially_validated")

        complete = service.set_validation_flags(
            submission_id=submission_id,
            organisation_changes=None,
            template_changes={"tpl_main": VALIDATION_VALIDATED},
            modified_by="bob@example.com",
            idempotency_key="flag-2",
        )
        self.assertTrue(complete.ok)
        assert complete.data is not None
        self.assertEqual(complete.data["organisations"]["ORG1"]["validation_flag"], VALIDATION_VALIDATED)
        self.assertEqual(complete.data["state"], VALIDATION_VALIDATED)

    def test_edit_submission_can_add_company_and_template(self):
        service = self._service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None

        edited = service.edit_submission(
            submission_id=created.data["submission_id"],
            modified_by="bob@example.com",
            idempotency_key="edit-1",
            organisation_upserts={"org2": self._org_ref("group", "Group B", "org2/file.xlsx", "TPL_TWO")},
            template_upserts={"tpl_two": self._template_ref("drive", "Templates", "templates/two.xlsx", "ORG2")},
            reason="Add second company and template",
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertIn("ORG2", edited.data["organisations"])
        self.assertIn("TPL_TWO", edited.data["templates"])
        self.assertEqual(edited.data["organisations"]["ORG2"]["template_keys"], ["TPL_TWO"])
        self.assertEqual(edited.data["templates"]["TPL_TWO"]["validation_flag"], VALIDATION_NOT_VALIDATED)

    def test_edit_submission_can_update_note(self):
        service = self._service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-1",
            note="First note",
        )
        assert created.data is not None

        edited = service.edit_submission(
            submission_id=created.data["submission_id"],
            modified_by="bob@example.com",
            idempotency_key="edit-1",
            note="Updated note",
            reason="Clarify submission",
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertEqual(edited.data["note"], "Updated note")

    def test_list_edit_history_returns_submission_changes_only(self):
        service = self._service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-1",
            note="Initial note",
        )
        assert created.data is not None
        submission_id = created.data["submission_id"]

        service.edit_submission(
            submission_id=submission_id,
            modified_by="bob@example.com",
            idempotency_key="edit-1",
            note="Updated note",
            reason="Clarify submission",
        )

        history = service.list_edit_history(submission_id=submission_id)

        self.assertTrue(history.ok)
        assert history.data is not None
        self.assertEqual([item["event_type"] for item in history.data], ["submission_created", "submission_note_updated"])
        self.assertEqual(history.data[-1]["actor"], "bob@example.com")
        self.assertEqual(history.data[-1]["summary"], "Submission note updated.")

    def test_submission_tracks_last_modified_user_and_timestamp(self):
        timestamps = iter(
            [
                datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 3, 9, 10, 5, tzinfo=timezone.utc),
            ]
        )
        service = ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path="Files/validation-service/events/submission_events.jsonl",
            now_fn=lambda: next(timestamps),
        )
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None
        self.assertEqual(created.data["created_by"], "alice@example.com")
        self.assertEqual(created.data["last_modified_by"], "alice@example.com")

        edited = service.edit_submission(
            submission_id=created.data["submission_id"],
            modified_by="bob@example.com",
            idempotency_key="edit-1",
            note="Updated note",
        )
        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertEqual(edited.data["last_modified_by"], "bob@example.com")
        self.assertEqual(edited.data["last_modified_ts_utc"], "2026-03-09T10:05:00+00:00")

    def test_refresh_submission_hashes_updates_company_and_template_hashes(self):
        call_count = {"value": 0}

        def refresh_resolver(source_payload: dict[str, object]) -> dict[str, object]:
            call_count["value"] += 1
            source = SharePointSourceRef.from_dict(source_payload)
            return SharePointSourceRef(
                source_type=source.source_type,
                target_name=source.target_name,
                root_path=source.root_path,
                tracked_files=[
                    TrackedFileRef(
                        path=item.path,
                        current_hash=f"refreshed-{call_count['value']}",
                        validated_hash=item.validated_hash,
                    )
                    for item in source.tracked_files
                ],
                folder_url=source.folder_url,
            ).to_dict()

        service = ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path="Files/validation-service/events/submission_events.jsonl",
            sharepoint_hash_resolver=refresh_resolver,
        )
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN")},
            templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None
        submission_id = created.data["submission_id"]

        refreshed = service.refresh_submission_hashes(
            submission_id=submission_id,
            refreshed_by="carol@example.com",
            idempotency_key="refresh-1",
        )
        self.assertTrue(refreshed.ok)
        assert refreshed.data is not None
        self.assertEqual(
            refreshed.data["organisations"]["ORG1"]["file"]["tracked_files"][0]["current_hash"],
            "refreshed-1",
        )
        self.assertEqual(
            refreshed.data["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]["current_hash"],
            "refreshed-2",
        )

    def test_file_backed_store_persists_events_and_projection(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="submission-service-"))
        events_path = temp_dir / "submission_events.jsonl"
        projection_path = temp_dir / "submissions_current.json"
        idempotency_path = temp_dir / "idempotency.json"
        service = ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path=str(events_path),
            event_store=JsonlSubmissionEventStore(events_path),
            idempotency_store=JsonFileIdempotencyStore(idempotency_path),
            submissions_projection_path=projection_path,
        )

        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )

        self.assertTrue(created.ok)
        self.assertTrue(events_path.exists())
        self.assertTrue(projection_path.exists())
        self.assertTrue(idempotency_path.exists())

        reloaded = ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path=str(events_path),
            event_store=JsonlSubmissionEventStore(events_path),
            idempotency_store=JsonFileIdempotencyStore(idempotency_path),
            submissions_projection_path=projection_path,
        )
        listed = reloaded.list_submissions(process_cd="PROC_A")
        self.assertTrue(listed.ok)
        assert listed.data is not None
        self.assertEqual(len(listed.data), 1)

    def test_projection_metadata_is_persisted_in_projection_file(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="submission-service-metadata-"))
        projection_path = temp_dir / "submissions_current.json"
        service = ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path=str(temp_dir / "submission_events.jsonl"),
            submissions_projection_path=projection_path,
            projection_metadata_provider=lambda: {"files": [{"path": "Files/validation-service/projections/submissions_current.json"}]},
        )

        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
            templates={},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )

        self.assertTrue(created.ok)
        projection_payload = json.loads(projection_path.read_text(encoding="utf-8"))
        self.assertIn("storage_metadata", projection_payload)

    def test_start_validation_run_stages_company_and_template_files(self):
        staged_calls: list[tuple[str, str]] = []

        def run_stager(source_payload: dict[str, object], tracked_file: dict[str, object], run_id: str, org_cd: str):
            staged_calls.append((org_cd, str(tracked_file["path"])))
            return {
                "path": tracked_file["path"],
                "staging_path": f"Files/validation-service/runs/{run_id}/{org_cd}/{tracked_file['path']}",
                "byte_count": 123,
                "asset_kind": tracked_file.get("asset_kind"),
                "asset_key": tracked_file.get("asset_key"),
            }

        service = ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path="Files/validation-service/events/submission_events.jsonl",
            run_file_stager=run_stager,
        )
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN")},
            templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None

        started = service.start_validation_run(
            submission_id=created.data["submission_id"],
            requested_org_files={"org1": None},
            started_by="alice@example.com",
            idempotency_key="run-1",
            refresh_hashes=False,
        )

        self.assertTrue(started.ok)
        assert started.data is not None
        self.assertEqual(started.data["state"], "staged")
        self.assertEqual(
            staged_calls,
            [("ORG1", "org1/file.xlsx"), ("ORG1", "templates/main.xlsx")],
        )


if __name__ == "__main__":
    unittest.main()
