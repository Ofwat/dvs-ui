import unittest

from refactor.services.data_validation_api.submission_service import (
    SharePointSourceRef,
    TrackedFileRef,
    ValidationServiceApi,
)
from refactor.services.data_validation_api.validation_run_api import ValidationRunApi
from refactor.services.data_validation_api.workflows import (
    create_submission_from_discovery,
    edit_submission_workflow,
    refresh_active_runs_workflow,
    run_submission_workflow,
)


class WorkflowTests(unittest.TestCase):
    def _source_payload(self, path: str) -> dict:
        return SharePointSourceRef(
            source_type="drive",
            target_name="Drive A",
            root_path="Shared Documents/Validation",
            tracked_files=[TrackedFileRef(path=path, current_hash=f"hash-{path}")],
        ).to_dict()

    def _service(self) -> ValidationServiceApi:
        return ValidationServiceApi(
            workspace_id="ws-1",
            submissions_registry_path="Files/validation-service/events/submission_events.jsonl",
        )

    def test_create_and_edit_submission_workflows(self):
        service = self._service()
        created = create_submission_from_discovery(
            service,
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations_payload={
                "ORG1": {
                    **self._source_payload("org1/file.xlsx"),
                    "template_keys": ["TPL_MAIN"],
                }
            },
            templates_payload={
                "TPL_MAIN": {
                    **self._source_payload("templates/main.xlsx"),
                    "assignment_mode": "shared",
                    "applies_to": ["ORG1"],
                }
            },
            created_by="alice@example.com",
            note="Created from workflow",
        ).response

        self.assertTrue(created.ok)
        assert created.data is not None

        edited = edit_submission_workflow(
            service,
            submission_id=created.data["submission_id"],
            modified_by="bob@example.com",
            organisation_payloads={
                "ORG2": {
                    **self._source_payload("org2/file.xlsx"),
                    "template_keys": ["TPL_MAIN"],
                }
            },
            note="Edited from workflow",
        ).response

        self.assertTrue(edited.ok)
        assert edited.data is not None
        self.assertIn("ORG2", edited.data["organisations"])
        self.assertEqual(edited.data["note"], "Edited from workflow")

    def test_run_and_refresh_active_runs_workflows(self):
        service = self._service()
        created = create_submission_from_discovery(
            service,
            process_cd="PROC_A",
            submission_period_cd="2026M01",
            organisations_payload={
                "ORG1": {
                    **self._source_payload("org1/file.xlsx"),
                    "template_keys": ["TPL_MAIN"],
                }
            },
            templates_payload={
                "TPL_MAIN": {
                    **self._source_payload("templates/main.xlsx"),
                    "assignment_mode": "shared",
                    "applies_to": ["ORG1"],
                }
            },
            created_by="alice@example.com",
        ).response
        assert created.data is not None

        run_api = ValidationRunApi(
            submission_resolver=lambda submission_id: service._get_submission_projection(submission_id),  # noqa: SLF001
        )
        workflow_result = run_submission_workflow(
            service,
            run_api,
            submission_id=created.data["submission_id"],
            actor="alice@example.com",
            refresh_submission=False,
        )
        self.assertIn("create_run", workflow_result)
        self.assertIn("trigger_run", workflow_result)

        refreshed = refresh_active_runs_workflow(
            run_api,
            submission_id=created.data["submission_id"],
        )
        self.assertIn("list_running_runs", refreshed)
        self.assertIn("refresh_running_runs", refreshed)


if __name__ == "__main__":
    unittest.main()
