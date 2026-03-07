import unittest
from pathlib import Path
import tempfile

from refactor.services.data_validation_api.submission_service import (
    VALIDATION_NOT_VALIDATED,
    VALIDATION_VALIDATED,
    JsonFileIdempotencyStore,
    JsonlSubmissionEventStore,
    SharePointSourceRef,
    ValidationServiceApi,
)


class SubmissionServiceTests(unittest.TestCase):
    def _source_ref(self, source_type: str, target_name: str, file_path: str) -> SharePointSourceRef:
        return SharePointSourceRef(
            source_type=source_type,
            target_name=target_name,
            root_path="Shared Documents/Validation",
            file_path=file_path,
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
            organisation_sources={
                "org1": self._source_ref("drive", "Drive A", "org1/file.xlsx"),
                "org2": self._source_ref("group", "Group B", "org2/file.xlsx"),
            },
            created_by="alice@example.com",
            idempotency_key="create-1",
        )

        self.assertTrue(created.ok)
        self.assertIsNotNone(created.data)
        assert created.data is not None
        self.assertEqual(created.data["state"], "ready")
        self.assertEqual(len(created.data["organisations"]), 2)
        self.assertTrue(
            all(org["validation_flag"] == VALIDATION_NOT_VALIDATED for org in created.data["organisations"].values())
        )
        self.assertEqual(created.data["organisations"]["ORG1"]["sharepoint_source"]["source_type"], "drive")
        self.assertEqual(created.data["organisations"]["ORG2"]["sharepoint_source"]["source_type"], "group")

        listed = service.list_submissions(process_cd="PROC_A")
        self.assertTrue(listed.ok)
        assert listed.data is not None
        self.assertEqual(len(listed.data), 1)

    def test_create_submission_rejects_duplicate_active(self):
        service = self._service()
        first = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisation_sources={"org1": self._source_ref("drive", "Drive A", "org1/file.xlsx")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        self.assertTrue(first.ok)

        second = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisation_sources={"org2": self._source_ref("group", "Group B", "org2/file.xlsx")},
            created_by="alice@example.com",
            idempotency_key="create-2",
        )
        self.assertFalse(second.ok)
        assert second.error is not None
        self.assertEqual(second.error.code, "DUPLICATE_ACTIVE_SUBMISSION")

    def test_create_submission_idempotent_replay_returns_same_submission(self):
        service = self._service()
        first = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisation_sources={"org1": self._source_ref("drive", "Drive A", "org1/file.xlsx")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        second = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisation_sources={"org1": self._source_ref("drive", "Drive A", "org1/file.xlsx")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        assert first.data is not None and second.data is not None
        self.assertEqual(first.data["submission_id"], second.data["submission_id"])

    def test_idempotency_conflict_for_different_payload(self):
        service = self._service()
        service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisation_sources={"org1": self._source_ref("drive", "Drive A", "org1/file.xlsx")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        conflict = service.create_submission(
            process_cd="PROC_B",
            submission_period_cd="2026M01",
            organisation_sources={"org1": self._source_ref("drive", "Drive A", "org1/file.xlsx")},
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        self.assertFalse(conflict.ok)
        assert conflict.error is not None
        self.assertEqual(conflict.error.code, "IDEMPOTENCY_CONFLICT")

    def test_set_organisation_validation_flags_updates_state(self):
        service = self._service()
        created = service.create_submission(
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisation_sources={
                "org1": self._source_ref("drive", "Drive A", "org1/file.xlsx"),
                "org2": self._source_ref("group", "Group B", "org2/file.xlsx"),
            },
            created_by="alice@example.com",
            idempotency_key="create-1",
        )
        assert created.data is not None
        submission_id = created.data["submission_id"]

        partial = service.set_organisation_validation_flags(
            submission_id=submission_id,
            changes={"org1": VALIDATION_VALIDATED},
            modified_by="bob@example.com",
            idempotency_key="flag-1",
            reason="manual check passed",
        )
        self.assertTrue(partial.ok)
        assert partial.data is not None
        self.assertEqual(partial.data["state"], "partially_validated")

        complete = service.set_organisation_validation_flags(
            submission_id=submission_id,
            changes={"org2": VALIDATION_VALIDATED},
            modified_by="bob@example.com",
            idempotency_key="flag-2",
        )
        self.assertTrue(complete.ok)
        assert complete.data is not None
        self.assertEqual(complete.data["state"], "validated")

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
            organisation_sources={"org1": self._source_ref("drive", "Drive A", "org1/file.xlsx")},
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
        self.assertEqual(listed.data[0]["process_cd"], "PROC_A")


if __name__ == "__main__":
    unittest.main()
