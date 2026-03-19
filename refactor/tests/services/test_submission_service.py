import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from refactor.services.data_validation_api.submission_service import (
    CreateSubmissionRequest,
    EditSubmissionRequest,
    ListSubmissionsRequest,
    ListEditHistoryRequest,
    RefreshSubmissionRequest,
    SetValidationFlagsRequest,
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
                TrackedFileRef(path=item, current_hash=f"hash-{index}", size_bytes=100 + index)
                for index, item in enumerate(tracked_files, start=1)
            ],
        )

    def _watch_source_ref(self, source_type: str, target_name: str, search_term: str) -> SharePointSourceRef:
        return SharePointSourceRef(
            source_type=source_type,
            target_name=target_name,
            root_path="Shared Documents/Validation",
            tracked_files=[
                TrackedFileRef(
                    path=None,
                    watch=True,
                    watch_search_term=search_term,
                )
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

    def _create_submission(self, service: ValidationServiceApi, request: CreateSubmissionRequest | None = None, **kwargs):
        return service.create_submission(request or CreateSubmissionRequest(**kwargs))

    def _edit_submission(self, service: ValidationServiceApi, request: EditSubmissionRequest | None = None, **kwargs):
        return service.edit_submission(request or EditSubmissionRequest(**kwargs))

    def _list_submissions(self, service: ValidationServiceApi, **kwargs):
        return service.list_submissions(ListSubmissionsRequest(**kwargs))

    def _refresh_submission_hashes(self, service: ValidationServiceApi, request: RefreshSubmissionRequest | None = None, **kwargs):
        return service.refresh_submission_hashes(request or RefreshSubmissionRequest(**kwargs))

    def _set_validation_flags(self, service: ValidationServiceApi, request: SetValidationFlagsRequest | None = None, **kwargs):
        return service.set_validation_flags(request or SetValidationFlagsRequest(**kwargs))

    def test_create_submission_and_list_submissions(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
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
        self.assertEqual(created.data["organisations"]["ORG1"]["file"]["tracked_files"][0]["size_bytes"], 101)

        listed = service.list_submissions(ListSubmissionsRequest(process_cd="PROC_A"))
        self.assertTrue(listed.ok)
        assert listed.data is not None
        self.assertEqual(len(listed.data), 1)

    def test_submission_api_accepts_typed_request_objects(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"ORG1": self._org_ref("drive", "Drive A", "org1.csv", "TPL_MAIN")},
                templates={"TPL_MAIN": self._template_ref("drive", "Drive A", "template.xlsx", "ORG1")},
                created_by="alice@example.com",
                idempotency_key="typed-create-1",
            )
        )
        self.assertTrue(created.ok)
        listed = service.list_submissions(ListSubmissionsRequest(process_cd="PROC_A"))
        self.assertTrue(listed.ok)
        self.assertEqual(len(listed.items), 1)

    def test_create_submission_rejects_duplicate_active(self):
        service = self._service()
        first = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )
        self.assertTrue(first.ok)

        second = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org2": self._org_ref("group", "Group B", "org2/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-2",
            )
        )
        self.assertFalse(second.ok)
        assert second.error is not None
        self.assertEqual(second.error.code, "DUPLICATE_ACTIVE_SUBMISSION")

    def test_create_submission_accepts_watched_tracked_files(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": OrganisationSubmissionRef(self._watch_source_ref("drive", "Drive A", "ORG1"), ["TPL_MAIN"])},
                templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
                created_by="alice@example.com",
                idempotency_key="create-watch-1",
            )
        )

        self.assertTrue(created.ok)
        assert created.data is not None
        tracked = created.data["organisations"]["ORG1"]["file"]["tracked_files"][0]
        self.assertIsNone(tracked["path"])
        self.assertTrue(tracked["watch"])
        self.assertEqual(tracked["watch_search_term"], "ORG1")
        self.assertIsNone(tracked["validation_run_id"])

    def test_set_validation_flags_updates_org_and_template_state(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN")},
                templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )
        assert created.data is not None
        submission_id = created.data["submission_id"]

        partial = self._set_validation_flags(service,
            SetValidationFlagsRequest(
                submission_id=submission_id,
                organisation_changes={"org1": VALIDATION_VALIDATED},
                template_changes=None,
                modified_by="bob@example.com",
                idempotency_key="flag-1",
            )
        )
        self.assertTrue(partial.ok)
        assert partial.data is not None
        self.assertEqual(partial.data["organisations"]["ORG1"]["validation_flag"], VALIDATION_VALIDATED)

        complete = self._set_validation_flags(service,
            SetValidationFlagsRequest(
                submission_id=submission_id,
                organisation_changes=None,
                template_changes={"tpl_main": VALIDATION_VALIDATED},
                modified_by="bob@example.com",
                idempotency_key="flag-2",
            )
        )
        self.assertTrue(complete.ok)
        assert complete.data is not None
        self.assertEqual(complete.data["organisations"]["ORG1"]["validation_flag"], VALIDATION_VALIDATED)
        self.assertEqual(complete.data["state"], VALIDATION_VALIDATED)

    def test_edit_submission_can_add_company_and_template(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )
        assert created.data is not None

        edited = self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=created.data["submission_id"],
                modified_by="bob@example.com",
                idempotency_key="edit-1",
                organisation_upserts={"org2": self._org_ref("group", "Group B", "org2/file.xlsx", "TPL_TWO")},
                template_upserts={"tpl_two": self._template_ref("drive", "Templates", "templates/two.xlsx", "ORG2")},
                reason="Add second company and template",
            )
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertIn("ORG2", edited.data["organisations"])
        self.assertIn("TPL_TWO", edited.data["templates"])
        self.assertEqual(edited.data["organisations"]["ORG2"]["template_keys"], ["TPL_TWO"])
        self.assertEqual(edited.data["templates"]["TPL_TWO"]["validation_flag"], VALIDATION_NOT_VALIDATED)

    def test_edit_submission_can_update_note(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
                note="First note",
            )
        )
        assert created.data is not None

        edited = self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=created.data["submission_id"],
                modified_by="bob@example.com",
                idempotency_key="edit-1",
                note="Updated note",
                reason="Clarify submission",
            )
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertEqual(edited.data["note"], "Updated note")
        self.assertIsNone(edited.data["organisations"]["ORG1"]["file"]["tracked_files"][0]["validation_run_id"])

    def test_edit_submission_can_clear_note_with_none(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
                note="First note",
            )
        )
        assert created.data is not None

        edited = self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=created.data["submission_id"],
                modified_by="bob@example.com",
                idempotency_key="edit-1",
                note=None,
                reason="Clear submission note",
            )
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertIsNone(edited.data["note"])

    def test_edit_submission_without_note_keeps_existing_note(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
                note="First note",
            )
        )
        assert created.data is not None

        edited = self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=created.data["submission_id"],
                modified_by="bob@example.com",
                idempotency_key="edit-1",
                reason="No note change",
            )
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertEqual(edited.data["note"], "First note")

    def test_edit_submission_can_update_validation_flags(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN")},
                templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )
        assert created.data is not None

        edited = self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=created.data["submission_id"],
                modified_by="bob@example.com",
                idempotency_key="edit-flags-1",
                organisation_validation_changes={"org1": VALIDATION_VALIDATED},
                template_validation_changes={"tpl_main": VALIDATION_VALIDATED},
                reason="Manual validation review",
            )
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertEqual(edited.data["organisations"]["ORG1"]["validation_flag"], VALIDATION_VALIDATED)
        self.assertEqual(edited.data["templates"]["TPL_MAIN"]["validation_flag"], VALIDATION_VALIDATED)
        self.assertEqual(
            edited.data["organisations"]["ORG1"]["file"]["tracked_files"][0]["validated_hash"],
            "hash-1",
        )
        self.assertEqual(
            edited.data["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]["validated_hash"],
            "hash-1",
        )

    def test_edit_submission_resets_tracking_metadata_for_upserted_files(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN")},
                templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )
        assert created.data is not None

        edited = self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=created.data["submission_id"],
                modified_by="bob@example.com",
                idempotency_key="edit-reset-1",
                organisation_upserts={"org1": self._org_ref("drive", "Drive A", "org1/new_file.xlsx", "TPL_MAIN")},
                template_upserts={"tpl_main": self._template_ref("drive", "Templates", "templates/new_main.xlsx", "ORG1")},
            )
        )

        self.assertTrue(edited.ok)
        assert edited.data is not None
        org_tracked = edited.data["organisations"]["ORG1"]["file"]["tracked_files"][0]
        self.assertEqual(org_tracked["path"], "org1/new_file.xlsx")
        self.assertIsNone(org_tracked["current_hash"])
        self.assertIsNone(org_tracked["validated_hash"])
        self.assertIsNone(org_tracked["validation_run_id"])
        self.assertIsNone(org_tracked["size_bytes"])
        self.assertIsNone(org_tracked["created"])
        self.assertIsNone(org_tracked["modified"])
        self.assertIsNone(org_tracked["modified_by"])
        tpl_tracked = edited.data["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]
        self.assertEqual(tpl_tracked["path"], "templates/new_main.xlsx")
        self.assertIsNone(tpl_tracked["current_hash"])
        self.assertIsNone(tpl_tracked["validated_hash"])
        self.assertIsNone(tpl_tracked["validation_run_id"])
        self.assertIsNone(tpl_tracked["size_bytes"])

    def test_list_edit_history_returns_submission_changes_only(self):
        service = self._service()
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
                note="Initial note",
            )
        )
        assert created.data is not None
        submission_id = created.data["submission_id"]

        self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=submission_id,
                modified_by="bob@example.com",
                idempotency_key="edit-1",
                note="Updated note",
                reason="Clarify submission",
            )
        )

        history = service.list_edit_history(ListEditHistoryRequest(submission_id=submission_id))

        self.assertTrue(history.ok)
        assert history.data is not None
        self.assertEqual([item["event_type"] for item in history.data], ["submission_note_updated", "submission_created"])
        self.assertEqual(history.data[0]["actor"], "bob@example.com")
        self.assertEqual(history.data[0]["summary"], "Submission note updated.")

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
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )
        assert created.data is not None
        self.assertEqual(created.data["created_by"], "alice@example.com")
        self.assertEqual(created.data["last_modified_by"], "alice@example.com")

        edited = self._edit_submission(service,
            EditSubmissionRequest(
                submission_id=created.data["submission_id"],
                modified_by="bob@example.com",
                idempotency_key="edit-1",
                note="Updated note",
            )
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
                        size_bytes=500 + call_count["value"],
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
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx", "TPL_MAIN")},
                templates={"tpl_main": self._template_ref("drive", "Templates", "templates/main.xlsx", "ORG1")},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )
        assert created.data is not None
        submission_id = created.data["submission_id"]

        refreshed = self._refresh_submission_hashes(service,
            RefreshSubmissionRequest(
                submission_id=submission_id,
                refreshed_by="carol@example.com",
                idempotency_key="refresh-1",
            )
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
        self.assertEqual(
            refreshed.data["organisations"]["ORG1"]["file"]["tracked_files"][0]["size_bytes"],
            501,
        )
        self.assertEqual(
            refreshed.data["templates"]["TPL_MAIN"]["file"]["tracked_files"][0]["size_bytes"],
            502,
        )

    def test_refresh_submission_hashes_can_resolve_watched_file(self):
        def refresh_resolver(source_payload: dict[str, object]) -> dict[str, object]:
            source = SharePointSourceRef.from_dict(source_payload)
            tracked_files = []
            for item in source.tracked_files:
                if item.watch:
                    tracked_files.append(
                        TrackedFileRef(
                            path="resolved/ORG1.xlsx",
                            watch=False,
                            watch_search_term=item.watch_search_term,
                            current_hash="resolved-hash",
                            validated_hash=item.validated_hash,
                            size_bytes=777,
                        )
                    )
                else:
                    tracked_files.append(item)
            return SharePointSourceRef(
                source_type=source.source_type,
                target_name=source.target_name,
                root_path=source.root_path,
                tracked_files=tracked_files,
                folder_url=source.folder_url,
            ).to_dict()

        service = ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path="Files/validation-service/events/submission_events.jsonl",
            sharepoint_hash_resolver=refresh_resolver,
        )
        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": OrganisationSubmissionRef(self._watch_source_ref("drive", "Drive A", "ORG1"), [])},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-watch-refresh-1",
            )
        )
        assert created.data is not None

        refreshed = self._refresh_submission_hashes(service,
            RefreshSubmissionRequest(
                submission_id=created.data["submission_id"],
                refreshed_by="carol@example.com",
                idempotency_key="refresh-watch-1",
            )
        )

        self.assertTrue(refreshed.ok)
        assert refreshed.data is not None
        tracked = refreshed.data["organisations"]["ORG1"]["file"]["tracked_files"][0]
        self.assertEqual(tracked["path"], "resolved/ORG1.xlsx")
        self.assertFalse(tracked["watch"])
        self.assertEqual(tracked["watch_search_term"], "ORG1")
        self.assertEqual(tracked["current_hash"], "resolved-hash")
        self.assertEqual(tracked["size_bytes"], 777)

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

        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
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
        listed = reloaded.list_submissions(ListSubmissionsRequest(process_cd="PROC_A"))
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

        created = self._create_submission(service,
            CreateSubmissionRequest(
                process_cd="PROC_A",
                submission_period_cd="2026M01",
                organisations={"org1": self._org_ref("drive", "Drive A", "org1/file.xlsx")},
                templates={},
                created_by="alice@example.com",
                idempotency_key="create-1",
            )
        )

        self.assertTrue(created.ok)
        projection_payload = json.loads(projection_path.read_text(encoding="utf-8"))
        self.assertIn("storage_metadata", projection_payload)

if __name__ == "__main__":
    unittest.main()
