from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

REFACTOR_DIR = Path(__file__).resolve().parents[1]
if str(REFACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(REFACTOR_DIR))

from pages import dimension_loader_service as dls
from pages.dimension_loader_state import (
    build_env_missing_message,
    get_dimension_jobs,
    get_dimension_jobs_by_environment,
    get_missing_env_vars,
    load_service_config,
)


class DimensionLoaderStateTests(unittest.TestCase):
    def _count_text(self, node, text: str) -> int:
        if isinstance(node, (list, tuple)):
            return sum(self._count_text(child, text) for child in node)
        if isinstance(node, str):
            return 1 if text in node else 0
        children = getattr(node, "children", None)
        if children is None:
            return 0
        if isinstance(children, (list, tuple)):
            return sum(self._count_text(child, text) for child in children)
        return self._count_text(children, text)

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

    def test_sharepoint_folder_name_prefers_query_path(self):
        self.assertEqual(
            dls._sharepoint_folder_name(  # noqa: SLF001
                "https://tenant.sharepoint.com/sites/site/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2Fsite%2FShared%20Documents%2FFolderA"
            ),
            "FolderA",
        )
        self.assertEqual(
            dls._sharepoint_folder_name(  # noqa: SLF001
                "https://tenant.sharepoint.com/sites/site/Shared%20Documents/Forms/AllItems.aspx?RootFolder=%2Fsites%2Fsite%2FShared%20Documents%2FFolderB"
            ),
            "FolderB",
        )

    def test_load_service_config_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "missing.json"
            config, error = load_service_config(config_path)
        self.assertIsNone(config)
        self.assertIn("No dimensions loader config found", error or "")

    def test_load_service_config_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "services" / "dimensions-loader" / "config.json"
            self.assertFalse(config_path.parent.exists())
            config, error = load_service_config(config_path)
            self.assertTrue(config_path.parent.exists())
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
                    "source_links": ["https://example.test/folder"],
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
                    "source_links": ["https://example.test/folder"],
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

    def test_get_dimension_jobs_filters_by_environment(self):
        payload = {
            "jobs": [
                {"name": "[DEV] A", "source_kind": "sharepoint", "target_root": "Files/dimensions/core"},
                {"name": "[PROD] B", "source_kind": "sharepoint", "target_root": "Files/dimensions/core"},
                {"name": "[DEV] C", "source_kind": "local", "target_root": "Files/dimensions/core"},
            ]
        }
        dev_jobs = get_dimension_jobs(payload, "dev")
        prod_jobs = get_dimension_jobs(payload, "prod")
        all_jobs = get_dimension_jobs(payload)
        self.assertEqual(len(dev_jobs), 1)
        self.assertEqual(len(prod_jobs), 1)
        self.assertEqual(len(all_jobs), 2)

    @patch("pages.dimension_loader_service.get_missing_env_vars", return_value=[])
    @patch(
        "pages.dimension_loader_service._scan_job_files",
        return_value=[{"source_relative_path": "a.xlsx", "target_relative_path": "Files/dimensions/core/a.xlsx"}],
    )
    @patch(
        "pages.dimension_loader_service.get_dimension_jobs_by_environment",
        return_value={
            "dev": [
                {
                    "name": "[DEV] Core Dimensions",
                    "target_root": "Files/dimensions/core",
                    "workspace_display_name": "dev-ocean",
                    "lakehouse_display_name": "Source_Data",
                    "source_links": ["https://example.test"],
                }
            ],
            "prod": [],
        },
    )
    @patch("pages.dimension_loader_service.load_service_config", return_value=({"jobs": []}, None))
    def test_action_helpers_render_status_text(self, *_):
        panel = dls._build_action_panel("dev")  # noqa: SLF001
        self.assertEqual(self._count_text(panel, "Refresh files"), 1)
        self.assertEqual(self._count_text(panel, "List files"), 1)
        self.assertEqual(self._count_text(panel, "Sync dimensions Fabric with SharePoint"), 1)
        self.assertIn("govuk-summary-list", str(panel))
        self.assertEqual(self._count_text(panel, "Open folder"), 1)

        refresh_status = dls._build_dimension_loader_action_result("dimension-loader-refresh", "dev")  # noqa: SLF001
        list_status = dls._build_dimension_loader_action_result("dimension-loader-list", "dev")  # noqa: SLF001
        self.assertEqual(refresh_status, "Refreshed 1 job(s) for DEV from the Dimensions Loader config.")
        self.assertEqual(list_status, "DEV folder scan ready.")

        list_results = dls._build_list_results("dev")  # noqa: SLF001
        self.assertEqual(self._count_text(list_results, "a.xlsx -> Files/dimensions/core/a.xlsx"), 1)

    def test_build_progress_text(self):
        self.assertEqual(dls._build_progress_text({"processed_mappings": 3, "total_mappings": 8}), "3/8")  # noqa: SLF001
        self.assertEqual(dls._build_progress_text(None), "")  # noqa: SLF001
        self.assertEqual(
            dls._build_progress_detail(  # noqa: SLF001
                {
                    "environment": "dev",
                    "current_job": "[DEV] Core Dimensions",
                    "current_file": "assurance_dim.xlsx",
                }
            ),
            "DEV | [DEV] Core Dimensions | assurance_dim.xlsx",
        )

    @patch("services.fabric_uploader_cli.app._upload_mapping")
    @patch("services.fabric_uploader_cli.app._resolve_fabric_destination", return_value=("ws-1", "lh-1"))
    @patch("services.fabric_uploader_cli.app._online_auth")
    @patch("pages.dimension_loader_service.threading.Thread")
    @patch(
        "pages.dimension_loader_service._prepare_scanned_jobs",
        return_value=(
            [
                {
                    "name": "[DEV] Core Dimensions",
                    "enabled": True,
                    "source_kind": "sharepoint",
                    "target_root": "Files/dimensions/core",
                    "source_links": ["https://example.test/folder"],
                    "fabric": {
                        "workspace_display_name": "dev-ocean",
                        "lakehouse_display_name": "Source_Data",
                    },
                    "_scanned_files": [
                        {
                            "source_link": "https://example.test/folder",
                            "source_relative_path": "a.xlsx",
                            "target_relative_path": "a.xlsx",
                        }
                    ],
                }
            ],
            1,
        ),
    )
    @patch(
        "pages.dimension_loader_service.get_dimension_jobs",
        return_value=[
            {
                "name": "[DEV] Core Dimensions",
                "enabled": True,
                "source_kind": "sharepoint",
                "target_root": "Files/dimensions/core",
                "fabric": {
                    "workspace_display_name": "dev-ocean",
                    "lakehouse_display_name": "Source_Data",
                },
                    "source_links": ["https://example.test/folder"],
            }
        ],
    )
    @patch("pages.dimension_loader_service.get_missing_env_vars", return_value=[])
    @patch("pages.dimension_loader_service.load_service_config")
    def test_sync_action_runs_uploader_helpers(
        self,
        load_service_config,
        get_missing_env_vars,
        get_dimension_jobs,
        prepare_scanned_jobs,
        mock_thread,
        get_online_auth,
        resolve_destination,
        upload_mapping,
    ):
        load_service_config.return_value = (
            {
                "jobs": [],
                "pipelines": {
                    "dev": {
                        "workspace_display_name": "dev-ocean",
                        "pipeline_display_name": "Refresh Dimensions",
                        "parameters": {"mode": "dev"},
                    }
                },
            },
            None,
        )
        auth = Mock()
        auth.SHAREPOINT_SCOPE = "sp-scope"
        auth.FABRIC_SCOPE = "fabric-scope"
        auth._ensure_authenticated = Mock(return_value=None)
        auth.list_fabric_workspaces = Mock(return_value=(True, [{"id": "workspace-pipeline", "displayName": "dev-ocean"}]))
        auth.list_fabric_pipelines = Mock(return_value=(True, [{"id": "pipeline-1", "displayName": "Refresh Dimensions"}]))
        auth.list_fabric_pipeline_runs = Mock(
            side_effect=[
                (True, [{"id": "pipeline-run-old", "status": "NotStarted"}]),
                (True, [{"id": "pipeline-run-old", "status": "NotStarted"}]),
                (True, [{"id": "pipeline-run-1", "status": "NotStarted"}]),
            ]
        )
        auth.trigger_fabric_pipeline = Mock(return_value=(True, {}))
        auth.get_fabric_pipeline_run = Mock(
            return_value=(
                True,
                {
                    "id": "pipeline-run-1",
                    "status": "NotStarted",
                    "startTimeUtc": "2026-06-03T12:00:00Z",
                    "endTimeUtc": "",
                    "failureReason": None,
                },
            )
        )
        get_online_auth.return_value = auth
        upload_mapping.return_value = {
            "source_relative_path": "a.xlsx",
            "effective_target_path": "a.xlsx",
            "byte_count": 1024,
        }

        class _FakeThread:
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}

            def start(self):
                if self.target:
                    self.target(*self.args, **self.kwargs)

        mock_thread.side_effect = _FakeThread

        status, progress, detail, pipeline_status, pipeline_event, pipeline_details, pipeline_link, state, disabled = dls._sync_dimension_jobs("dev")  # noqa: SLF001
        self.assertIn("Sync started for DEV", status)
        self.assertEqual(progress, "0/1")
        self.assertIn("DEV", detail)
        self.assertEqual(pipeline_status, "")
        self.assertEqual(pipeline_event, "")
        self.assertEqual(pipeline_details, "")
        self.assertEqual(pipeline_link, "")
        self.assertFalse(disabled)
        self.assertTrue(str(state["run_id"]))
        live_state = dls._get_sync_state(str(state["run_id"]))  # noqa: SLF001
        self.assertEqual(dls._build_progress_text(live_state), "1/1")  # noqa: SLF001
        self.assertEqual(
            dls._build_progress_detail(live_state),  # noqa: SLF001
            "DEV | [DEV] Core Dimensions | a.xlsx",
        )
        self.assertIn("Pipeline: InProgress", dls._build_pipeline_status_text(live_state))  # noqa: SLF001
        self.assertIn("Triggered pipeline for DEV", dls._build_pipeline_event_text(live_state))  # noqa: SLF001
        self.assertIn("Pipeline details: Started: 2026-06-03 12:00:00 UTC", dls._build_pipeline_details_text(live_state))  # noqa: SLF001
        self.assertIn("Elapsed:", dls._build_pipeline_details_text(live_state))  # noqa: SLF001
        self.assertIn("app.powerbi.com/workloads/data-pipeline/monitoring", dls._build_pipeline_link(live_state))  # noqa: SLF001
        auth.list_fabric_workspaces.assert_called_once()
        auth.list_fabric_pipelines.assert_called_once_with("workspace-pipeline")
        auth.trigger_fabric_pipeline.assert_called_once_with(
            "workspace-pipeline",
            "pipeline-1",
            parameters={"mode": "dev", "environment": "dev"},
        )
        self.assertGreaterEqual(auth.list_fabric_pipeline_runs.call_count, 2)
        upload_mapping.assert_called_once()
        self.assertEqual(upload_mapping.call_args.args[1]["source_relative_path"], "a.xlsx")
        self.assertEqual(upload_mapping.call_args.args[1]["source_link"], "https://example.test/folder")


if __name__ == "__main__":
    unittest.main()
