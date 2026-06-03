from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REFACTOR_DIR = Path(__file__).resolve().parents[1]
if str(REFACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(REFACTOR_DIR))

from pages import interim_solution_service as iss
from pages.interim_solution_state import (
    get_interim_jobs,
    get_interim_jobs_by_environment,
    get_missing_env_vars,
)


class InterimSolutionStateTests(unittest.TestCase):
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

    def test_group_interim_jobs_by_environment(self):
        payload = {
            "jobs": [
                {
                    "name": "[DEV] Dummy Models",
                    "enabled": True,
                    "source_kind": "sharepoint",
                    "source_folder_url": "https://example.test/folder",
                    "target_root": "Files/test",
                    "fabric": {
                        "workspace_display_name": "dev-ocean",
                        "lakehouse_display_name": "Source_Data",
                    },
                },
                {
                    "name": "[PROD] Dummy Models",
                    "enabled": False,
                    "source_kind": "sharepoint",
                    "source_folder_url": "https://example.test/folder",
                    "target_root": "Files/test",
                    "fabric": {
                        "workspace_display_name": "prod-ocean",
                        "lakehouse_display_name": "Source_Data",
                    },
                },
            ]
        }
        grouped = get_interim_jobs_by_environment(payload)
        self.assertEqual(len(grouped["dev"]), 1)
        self.assertEqual(len(grouped["prod"]), 1)
        self.assertEqual(grouped["dev"][0]["source_folder_url"], "https://example.test/folder")

    def test_get_interim_jobs_filters_by_environment(self):
        payload = {
            "jobs": [
                {"name": "[DEV] A", "source_kind": "sharepoint", "source_folder_url": "https://example.test", "target_root": "Files/a"},
                {"name": "[PROD] B", "source_kind": "sharepoint", "source_folder_url": "https://example.test", "target_root": "Files/b"},
                {"name": "[DEV] C", "source_kind": "local", "source_folder_url": "https://example.test", "target_root": "Files/c"},
            ]
        }
        self.assertEqual(len(get_interim_jobs(payload, "dev")), 1)
        self.assertEqual(len(get_interim_jobs(payload, "prod")), 1)
        self.assertEqual(len(get_interim_jobs(payload)), 2)

    @patch("pages.interim_solution_service.get_missing_env_vars", return_value=[])
    @patch(
        "pages.interim_solution_service.get_interim_jobs_by_environment",
        return_value={
            "dev": [
                {
                    "name": "[DEV] Dummy Models",
                    "enabled": True,
                    "source_folder_url": "https://example.test/folder",
                    "target_root": "Files/test",
                    "workspace_display_name": "dev-ocean",
                    "lakehouse_display_name": "Source_Data",
                }
            ],
            "prod": [],
        },
    )
    @patch("pages.interim_solution_service.load_service_config", return_value=({"jobs": []}, None))
    def test_action_panel_renders_buttons(self, *_):
        panel = iss._build_action_panel("dev")  # noqa: SLF001
        self.assertEqual(self._count_text(panel, "Refresh files"), 1)
        self.assertEqual(self._count_text(panel, "List files"), 1)
        self.assertEqual(self._count_text(panel, "Sync dimensions Fabric with SharePoint"), 1)
        self.assertEqual(
            iss._build_action_result("interim-solution-refresh", "dev"),  # noqa: SLF001
            "Refreshed 1 job(s) for DEV from the Interim Solution config.",
        )
        self.assertEqual(
            iss._build_action_result("interim-solution-list", "dev"),  # noqa: SLF001
            "DEV folder scan ready.",
        )

    @patch(
        "pages.interim_solution_service._scan_sharepoint_folder",
        return_value=[
            {"relativePath": "folder/a.xlsx", "name": "a.xlsx", "isFolder": False},
            {"relativePath": "folder/b.xlsx", "name": "b.xlsx", "isFolder": False},
        ],
    )
    @patch("pages.interim_solution_service.get_missing_env_vars", return_value=[])
    @patch(
        "pages.interim_solution_service.get_interim_jobs_by_environment",
        return_value={
            "dev": [
                {
                    "name": "[DEV] Dummy Models",
                    "enabled": True,
                    "source_folder_url": "https://example.test/folder",
                    "target_root": "Files/test",
                    "workspace_display_name": "dev-ocean",
                    "lakehouse_display_name": "Source_Data",
                }
            ],
            "prod": [],
        },
    )
    @patch("pages.interim_solution_service.load_service_config", return_value=({"jobs": []}, None))
    def test_list_results_show_scanned_files(self, *_):
        result = iss._build_list_results("dev")  # noqa: SLF001
        self.assertEqual(self._count_text(result, "a.xlsx -> Files/test/folder/a.xlsx"), 1)
        self.assertEqual(self._count_text(result, "b.xlsx -> Files/test/folder/b.xlsx"), 1)

    @patch("services.fabric_uploader_cli.app._upload_bytes_with_progress")
    @patch("services.fabric_uploader_cli.app._download_sharepoint_bytes_with_progress", return_value=b"content")
    @patch("services.fabric_uploader_cli.app._resolve_fabric_destination", return_value=("ws-1", "lh-1"))
    @patch("services.fabric_uploader_cli.app._online_auth")
    @patch("pages.interim_solution_service.threading.Thread")
    @patch(
        "pages.interim_solution_service._scan_sharepoint_folder",
        return_value=[{"relativePath": "dev/file1.xlsx", "name": "file1.xlsx", "isFolder": False}],
    )
    @patch("pages.interim_solution_service.get_missing_env_vars", return_value=[])
    @patch(
        "pages.interim_solution_service.get_interim_jobs",
        return_value=[
            {
                "name": "[DEV] Dummy Models",
                "enabled": True,
                "source_kind": "sharepoint",
                "source_folder_url": "https://example.test/folder",
                "target_root": "Files/test",
                "fabric": {
                    "workspace_display_name": "dev-ocean",
                    "lakehouse_display_name": "Source_Data",
                },
            }
        ],
    )
    @patch("pages.interim_solution_service.load_service_config", return_value=({"jobs": []}, None))
    def test_sync_action_updates_counter(self, *mocks):
        upload_bytes_with_progress = mocks[-1]
        auth = type(
            "Auth",
            (),
            {
                "SHAREPOINT_SCOPE": "sp-scope",
                "FABRIC_SCOPE": "fabric-scope",
                "_ensure_authenticated": lambda self, scope: None,
            },
        )()
        iss._SYNC_RUNS.clear()  # noqa: SLF001
        with patch("pages.interim_solution_service.threading.Thread") as mock_thread:
            class _FakeThread:
                def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                    self.target = target
                    self.args = args
                    self.kwargs = kwargs or {}

                def start(self):
                    if self.target:
                        self.target(*self.args, **self.kwargs)

            mock_thread.side_effect = _FakeThread
            with patch("pages.interim_solution_service._build_run_folder_name", return_value="20260603T120000Z_abcd1234"):
                with patch("services.fabric_uploader_cli.app._online_auth", return_value=auth):
                    status, progress, detail, state, disabled = iss._sync_dimension_jobs("dev")  # noqa: SLF001

        self.assertIn("Sync started for DEV", status)
        self.assertEqual(progress, "0/1")
        self.assertIn("DEV", detail)
        self.assertIn("20260603T120000Z_abcd1234", detail)
        self.assertFalse(disabled)
        live_state = iss._get_sync_state(str(state["run_id"]))  # noqa: SLF001
        self.assertEqual(iss._build_progress_text(live_state), "1/1")  # noqa: SLF001
        self.assertIn("file1.xlsx", iss._build_progress_detail(live_state))  # noqa: SLF001
        self.assertIn("20260603T120000Z_abcd1234", iss._build_progress_detail(live_state))  # noqa: SLF001
        upload_bytes_with_progress.assert_called_once()
        self.assertEqual(
            upload_bytes_with_progress.call_args.args[2],
            "Files/test/20260603T120000Z_abcd1234/dev/file1.xlsx",
        )


if __name__ == "__main__":
    unittest.main()
