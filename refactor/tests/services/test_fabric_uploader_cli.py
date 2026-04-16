import tempfile
import unittest
from pathlib import Path

from refactor.services.fabric_uploader_cli.app import (
    MAX_TRANSFER_WORKERS,
    _config_base_dir,
    _default_config_payload,
    _default_target_path,
    _format_bytes,
    _format_duration,
    _job_summary,
    _list_local_files,
    _load_json_file,
    _mapping_summary,
    _normalize_manual_source_path,
    _normalize_relative_path,
    _remove_mappings,
    _require_existing_local_root,
    _require_relative_target_path,
    _save_json_file,
    _serializable_config,
    _upsert_mapping,
    _validate_config,
)


class FabricUploaderCliTests(unittest.TestCase):
    def test_parallel_worker_count(self):
        self.assertEqual(MAX_TRANSFER_WORKERS, 4)

    def test_default_config_shape(self):
        payload = _default_config_payload()
        self.assertEqual(payload["jobs"], [])

    def test_save_and_load_config_round_trip(self):
        payload = _default_config_payload()
        payload["jobs"] = [
            {
                "name": "Job A",
                "enabled": True,
                "fabric": {
                    "workspace_display_name": "Workspace A",
                    "lakehouse_display_name": "Lakehouse A",
                },
                "source_kind": "local",
                "source_root": "C:/tmp",
                "target_root": "Files/Uploads",
                "mappings": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "uploader.json"
            _save_json_file(path, payload)
            loaded = _load_json_file(path)
        self.assertEqual(loaded["jobs"][0]["fabric"]["workspace_display_name"], "Workspace A")

    def test_serializable_config_excludes_runtime_fields(self):
        payload = {
            "_config_dir": "C:/shared",
            "jobs": [
                {
                    "name": "Job A",
                    "_runtime": "ignore-me",
                    "fabric": {
                        "workspace_display_name": "Workspace A",
                        "lakehouse_display_name": "Lakehouse A",
                    },
                    "source_kind": "local",
                    "source_root": "incoming",
                    "target_root": "Files/Uploads",
                    "mappings": [],
                }
            ],
        }
        serialized = _serializable_config(payload)
        self.assertNotIn("_config_dir", serialized)
        self.assertNotIn("_runtime", serialized["jobs"][0])

    def test_validate_config_reports_missing_fields(self):
        payload = {
            "_config_dir": tempfile.gettempdir(),
            "jobs": [
                {
                    "name": "Job A",
                    "source_kind": "local",
                    "source_root": r"C:\definitely-missing-path-for-uploader-tests",
                    "target_root": "",
                    "fabric": {
                        "workspace_display_name": "",
                        "lakehouse_display_name": "",
                    },
                    "mappings": [{"source_relative_path": "", "target_relative_path": ""}],
                }
            ],
        }
        issues = _validate_config(payload)
        self.assertTrue(any("Local source path was not found:" in issue for issue in issues))
        self.assertTrue(any("Fabric target root is required" in issue for issue in issues))
        self.assertTrue(any("Fabric workspace display name is required" in issue for issue in issues))
        self.assertTrue(any("mapping 1 is missing source path" in issue for issue in issues))

    def test_require_relative_target_path(self):
        _require_relative_target_path("company.csv", "Files/dimensions")
        with self.assertRaises(RuntimeError):
            _require_relative_target_path("Files/dimensions/company.csv", "Files/dimensions")

    def test_normalize_relative_path(self):
        self.assertEqual(_normalize_relative_path(r"\foo\\bar/baz.txt"), "foo/bar/baz.txt")

    def test_default_target_path_is_relative_to_target_root(self):
        self.assertEqual(
            _default_target_path("incoming\\file.xlsx", "Files/Uploads/Templates"),
            "incoming/file.xlsx",
        )

    def test_format_bytes(self):
        self.assertEqual(_format_bytes(512), "512 B")
        self.assertEqual(_format_bytes(2048), "2.0 KB")

    def test_format_duration(self):
        self.assertEqual(_format_duration(65), "01:05")
        self.assertEqual(_format_duration(3665), "1:01:05")

    def test_list_local_files_for_folder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "nested").mkdir()
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "nested" / "b.txt").write_text("b", encoding="utf-8")
            self.assertEqual(_list_local_files(str(root)), ["a.txt", "nested/b.txt"])

    def test_require_existing_local_root_has_clean_message(self):
        missing = Path(tempfile.gettempdir()) / "fabric-uploader-cli-missing-path"
        with self.assertRaises(RuntimeError) as ctx:
            _require_existing_local_root(str(missing))
        self.assertIn("Local source path was not found:", str(ctx.exception))
        self.assertIn("relative to the config file", str(ctx.exception))

    def test_relative_local_paths_resolve_from_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_dir = Path(tmp_dir) / "shared-config"
            source_dir = config_dir / "incoming"
            source_dir.mkdir(parents=True)
            (source_dir / "a.txt").write_text("a", encoding="utf-8")
            config = {"_config_dir": str(config_dir)}
            self.assertEqual(_config_base_dir(config), config_dir)
            self.assertEqual(_list_local_files("incoming", _config_base_dir(config)), ["a.txt"])

    def test_normalize_manual_source_path_accepts_absolute_local_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_dir = Path(tmp_dir) / "shared-config"
            source_dir = config_dir / "incoming"
            source_dir.mkdir(parents=True)
            file_path = source_dir / "nested.txt"
            file_path.write_text("a", encoding="utf-8")
            job = {"source_kind": "local", "source_root": "incoming"}
            config = {"_config_dir": str(config_dir)}
            self.assertEqual(
                _normalize_manual_source_path(job, str(file_path), config),
                "nested.txt",
            )

    def test_upsert_mapping_replaces_existing_source(self):
        job = {
            "mappings": [
                {"source_relative_path": "a.txt", "target_relative_path": "Files/Uploads/a.txt"},
            ]
        }
        updated = _upsert_mapping(job, "a.txt", "Files/Uploads/renamed.txt")
        self.assertEqual(
            updated["mappings"],
            [{"source_relative_path": "a.txt", "target_relative_path": "Files/Uploads/renamed.txt"}],
        )

    def test_remove_mappings(self):
        job = {
            "mappings": [
                {"source_relative_path": "a.txt", "target_relative_path": "x/a.txt"},
                {"source_relative_path": "b.txt", "target_relative_path": "x/b.txt"},
            ]
        }
        updated = _remove_mappings(job, ["b.txt"])
        self.assertEqual(
            updated["mappings"],
            [{"source_relative_path": "a.txt", "target_relative_path": "x/a.txt"}],
        )

    def test_remove_job_list_comprehension_shape(self):
        jobs = [{"name": "A"}, {"name": "B"}]
        remaining = [job for index, job in enumerate(jobs) if index != 0]
        self.assertEqual(remaining, [{"name": "B"}])

    def test_summaries(self):
        self.assertEqual(_mapping_summary({"source_relative_path": "a.txt", "target_relative_path": "x/a.txt"}), "a.txt -> x/a.txt")
        self.assertIn(
            "Workspace A / Lakehouse A",
            _job_summary(
                {
                    "name": "Job A",
                    "enabled": True,
                    "fabric": {
                        "workspace_display_name": "Workspace A",
                        "lakehouse_display_name": "Lakehouse A",
                    },
                    "source_kind": "local",
                    "source_root": "C:/tmp",
                    "mappings": [1, 2],
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
