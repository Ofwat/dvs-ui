from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

REFACTOR_DIR = Path(__file__).resolve().parents[1]
if str(REFACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(REFACTOR_DIR))

from pages.dimension_loader_state import (
    build_env_missing_message,
    get_dimension_jobs_by_environment,
    get_missing_env_vars,
    load_service_config,
)


class DimensionLoaderStateTests(unittest.TestCase):
    def test_get_missing_env_vars_reports_required_keys(self):
        missing = get_missing_env_vars({"SHAREPOINT_HOST": "", "SHAREPOINT_SITE_PATH": "sites/ofw-ii"})
        self.assertEqual(missing, ["SHAREPOINT_HOST"])

    def test_build_env_missing_message(self):
        lines = build_env_missing_message(["SHAREPOINT_HOST", "SHAREPOINT_SITE_PATH"])
        self.assertEqual(
            lines,
            [
                "Dimensions Loader is not configured yet.",
                "Missing environment values: SHAREPOINT_HOST, SHAREPOINT_SITE_PATH.",
                "Add them to .env and reload the app.",
            ],
        )

    def test_load_service_config_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "missing.json"
            config, error = load_service_config(config_path)
        self.assertIsNone(config)
        self.assertIn("No dimensions loader config found", error or "")

    def test_group_dimension_jobs_by_environment(self):
        payload = {
            "jobs": [
                {
                    "name": "[DEV] Core Dimensions",
                    "enabled": True,
                    "source_kind": "sharepoint",
                    "target_root": "Files/dimensions/core",
                    "fabric": {
                        "workspace_display_name": "dev-ocean",
                        "lakehouse_display_name": "Source_Data",
                    },
                    "source_links": ["https://example.test"],
                    "mappings": [],
                },
                {
                    "name": "[PROD] Core Dimensions",
                    "enabled": False,
                    "source_kind": "sharepoint",
                    "target_root": "Files/dimensions/core",
                    "fabric": {
                        "workspace_display_name": "prod-ocean",
                        "lakehouse_display_name": "Source_Data",
                    },
                    "source_links": ["https://example.test"],
                    "mappings": [],
                },
                {
                    "name": "Ignored local job",
                    "enabled": True,
                    "source_kind": "local",
                    "target_root": "Files/dimensions/core",
                    "fabric": {},
                },
            ]
        }
        grouped = get_dimension_jobs_by_environment(payload)
        self.assertEqual(len(grouped["dev"]), 1)
        self.assertEqual(len(grouped["prod"]), 1)
        self.assertEqual(grouped["dev"][0]["workspace_display_name"], "dev-ocean")
        self.assertFalse(grouped["prod"][0]["enabled"])


if __name__ == "__main__":
    unittest.main()
