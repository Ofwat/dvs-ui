import unittest
from pathlib import Path
import tempfile

from refactor.services.data_validation_api.cli.app import (
    _default_config_payload,
    _entity_check_label,
    _entity_run_block_reason,
    _submission_check_label,
    _load_json_file,
    _save_json_file,
    current_org_refs,
    current_template_refs,
    editable_org_payloads,
    editable_template_payloads,
    friendly_submission_display,
    has_single_shared_template,
    parse_code_csv,
    tracked_file_status,
    summarize_edit_changes,
)


class DataValidationCliHelpersTests(unittest.TestCase):
    def test_default_config_has_service_and_pipeline_sections(self):
        payload = _default_config_payload()
        self.assertIn("service", payload)
        self.assertIn("pipeline", payload)
        self.assertEqual(payload["service"]["storage_mode"], "fabric")

    def test_save_and_load_json_file_round_trip(self):
        payload = _default_config_payload()
        payload["service"]["workspace_display_name"] = "Workspace A"
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "local_config.json"
            _save_json_file(path, payload)
            loaded = _load_json_file(path)
        self.assertEqual(loaded["service"]["workspace_display_name"], "Workspace A")

    def test_parse_code_csv_normalizes_and_deduplicates(self):
        self.assertEqual(parse_code_csv(" abc,DEF,abc , , ghi "), ["ABC", "DEF", "GHI"])

    def test_tracked_file_status(self):
        self.assertEqual(tracked_file_status({"current_hash": "a", "validated_hash": "a"}), "validated")
        self.assertEqual(tracked_file_status({"current_hash": "a", "validated_hash": "b"}), "changed_since_validation")
        self.assertEqual(tracked_file_status({"watch": True, "path": None}), "watching")
        self.assertEqual(tracked_file_status({}), "unresolved")

    def test_entity_check_label(self):
        self.assertEqual(
            _entity_check_label({"file": {"tracked_files": [{"current_hash": "a", "validated_hash": "a"}]}}),
            "hashes match validated run",
        )
        self.assertEqual(
            _entity_check_label({"file": {"tracked_files": [{"current_hash": "a", "validated_hash": "b"}]}}),
            "changed since validated run",
        )

    def test_entity_run_block_reason(self):
        self.assertEqual(
            _entity_run_block_reason({"file": {"tracked_files": [{"path": None, "watch": True, "watch_search_term": "SRN"}]}}),
            "unavailable: watching (SRN)",
        )
        self.assertEqual(
            _entity_run_block_reason({"file": {"tracked_files": [{"path": None, "watch": False}]}}),
            "unavailable: unresolved (<unknown>)",
        )

    def test_submission_check_label(self):
        submission = {
            "organisations": {
                "ORG1": {"file": {"tracked_files": [{"current_hash": "a", "validated_hash": "a"}]}},
            },
            "templates": {
                "TPL1": {"file": {"tracked_files": [{"current_hash": "a", "validated_hash": "a"}]}},
            },
        }
        self.assertEqual(_submission_check_label(submission), "all hashes match validated run")

    def test_friendly_submission_display(self):
        self.assertEqual(
            friendly_submission_display(
                {
                    "process_cd": "PROC_A",
                    "submission_period_cd": "2026M03",
                    "created_by": "alice@example.com",
                    "created_ts_utc": "2026-03-18T10:00:00+00:00",
                }
            ),
            "PROC_A | 2026M03 | alice@example.com | 2026-03-18T10:00:00+00:00",
        )

    def test_editable_payload_extractors_and_current_refs(self):
        projection = {
            "organisations": {
                "ORG1": {
                    "file": {
                        "source_type": "drive",
                        "target_name": "Drive A",
                        "root_path": "Shared Documents/Validation",
                        "tracked_files": [{"path": "ORG1/file.xlsx", "watch": False}],
                    },
                    "template_keys": ["TPL_MAIN"],
                    "validation_flag": "not_validated",
                }
            },
            "templates": {
                "TPL_MAIN": {
                    "file": {
                        "source_type": "drive",
                        "target_name": "Templates",
                        "root_path": "Shared Documents/Templates",
                        "tracked_files": [{"path": "templates/main.xlsx", "watch": False}],
                    },
                    "assignment_mode": "shared",
                    "applies_to": ["ORG1"],
                    "validation_flag": "validated",
                }
            },
        }

        org_payloads = editable_org_payloads(projection)
        template_payloads = editable_template_payloads(projection)
        org_refs = current_org_refs(projection)
        template_refs = current_template_refs(projection)

        self.assertEqual(org_payloads["ORG1"]["template_keys"], ["TPL_MAIN"])
        self.assertEqual(template_payloads["TPL_MAIN"]["assignment_mode"], "shared")
        self.assertEqual(org_refs["ORG1"]["sharepoint_source"]["target_name"], "Drive A")
        self.assertEqual(template_refs["TPL_MAIN"]["sharepoint_source"]["target_name"], "Templates")
        self.assertTrue(has_single_shared_template(template_payloads))

    def test_summarize_edit_changes(self):
        summary = summarize_edit_changes(
            note_for_api="Updated note",
            organisation_upserts={"ORG2": object()},
            organisation_removals=["ORG1"],
            organisation_validation_changes={"ORG2": "validated"},
            template_upserts={"TPL_TWO": object()},
            template_removals=["TPL_MAIN"],
            template_validation_changes={"TPL_TWO": "not_validated"},
        )
        self.assertEqual(
            summary,
            [
                "Update note to: Updated note",
                "Upsert company: ORG2",
                "Remove company: ORG1",
                "Set company validation flag: ORG2 -> validated",
                "Upsert template: TPL_TWO",
                "Remove template: TPL_MAIN",
                "Set template validation flag: TPL_TWO -> not_validated",
            ],
        )


if __name__ == "__main__":
    unittest.main()
