from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from refactor.online_auth import (
        download_lakehouse_file,
        list_fabric_workspaces,
        list_fabric_lakehouses,
        list_lakehouse_files,
        list_lakehouse_tables,
        upload_lakehouse_file,
    )
except ModuleNotFoundError:
    from refactor.online_auth import (
        download_lakehouse_file,
        list_fabric_workspaces,
        list_fabric_lakehouses,
        list_lakehouse_files,
        list_lakehouse_tables,
        upload_lakehouse_file,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke test Fabric lakehouse table access and file roundtrip using JSON config."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("fabric_lakehouse_smoke_test.config.json"),
        help="Path to the Fabric lakehouse smoke test JSON config file.",
    )
    return parser


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def pretty(label: str, payload: dict[str, Any]):
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2))


def require_ok(action: str, success: bool, payload: Any):
    if not success:
        raise RuntimeError(f"{action} failed: {payload}")


def parse_error_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"message": payload}
    return {"message": str(payload)}


def find_item_by_display_name(items: list[dict[str, Any]], display_name: str, label: str) -> dict[str, Any]:
    matches = [item for item in items if item.get("displayName") == display_name]
    if not matches:
        raise RuntimeError(f"{label} with displayName '{display_name}' was not found.")
    if len(matches) > 1:
        raise RuntimeError(f"{label} with displayName '{display_name}' is not unique.")
    return matches[0]


def main():
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)

    fabric = config["fabric"]
    workspace_display_name = fabric["workspace_display_name"]
    lakehouse_display_name = fabric["lakehouse_display_name"]
    pretty(
        "config",
        {
            "config_path": str(args.config.resolve()),
            "workspace_display_name": workspace_display_name,
            "lakehouse_display_name": lakehouse_display_name,
        },
    )
    if workspace_display_name == "Your Fabric Workspace" or lakehouse_display_name == "Your Lakehouse":
        raise RuntimeError(
            "Config still contains placeholder values. Update workspace_display_name and lakehouse_display_name."
        )

    workspaces_ok, workspaces_payload = list_fabric_workspaces()
    require_ok("list_fabric_workspaces", workspaces_ok, workspaces_payload)
    workspace = find_item_by_display_name(workspaces_payload, workspace_display_name, "Workspace")
    workspace_id = workspace["id"]

    lakehouses_ok, lakehouses_payload = list_fabric_lakehouses(workspace_id)
    require_ok("list_fabric_lakehouses", lakehouses_ok, lakehouses_payload)
    lakehouse = find_item_by_display_name(lakehouses_payload, lakehouse_display_name, "Lakehouse")
    lakehouse_id = lakehouse["id"]

    pretty(
        "fabric_target",
        {
            "workspace_display_name": workspace_display_name,
            "workspace_id": workspace_id,
            "lakehouse_display_name": lakehouse_display_name,
            "lakehouse_id": lakehouse_id,
            "lakehouses_found_in_workspace": len(lakehouses_payload),
        },
    )

    table_cfg = config.get("table_probe", {})
    tables_ok, tables_payload = list_lakehouse_tables(workspace_id, lakehouse_id)
    if tables_ok:
        table_names = [item.get("name") or item.get("displayName") or item.get("id") for item in tables_payload]
        table_result = {
            "lakehouse_id": lakehouse_id,
            "table_count": len(tables_payload),
            "table_names": table_names,
        }
        expected_table = table_cfg.get("expected_table_name")
        if expected_table:
            table_result["expected_table_name"] = expected_table
            table_result["expected_table_present"] = expected_table in table_names
        pretty("table_probe", table_result)
    else:
        error_payload = parse_error_payload(tables_payload)
        if error_payload.get("errorCode") == "UnsupportedOperationForSchemasEnabledLakehouse":
            pretty(
                "table_probe_warning",
                {
                    "lakehouse_id": lakehouse_id,
                    "warning": "Table listing is not supported for schema-enabled lakehouses via this endpoint.",
                    "details": error_payload,
                },
            )
        else:
            require_ok("list_lakehouse_tables", tables_ok, tables_payload)

    file_cfg = config["file_roundtrip"]
    directory = file_cfg.get("directory", "Files/Uploads")
    filename = file_cfg["filename"]
    content_text = file_cfg["content"]
    content_bytes = content_text.encode("utf-8")

    upload_ok, upload_payload = upload_lakehouse_file(
        workspace_id=workspace_id,
        lakehouse_id=lakehouse_id,
        filename=filename,
        content=content_bytes,
        directory=directory,
    )
    require_ok("upload_lakehouse_file", upload_ok, upload_payload)
    pretty(
        "file_upload",
        {
            "directory": directory,
            "filename": filename,
            "message": upload_payload,
        },
    )

    files_ok, files_payload = list_lakehouse_files(
        workspace_id=workspace_id,
        lakehouse_id=lakehouse_id,
        directory=directory,
    )
    require_ok("list_lakehouse_files", files_ok, files_payload)
    file_names = [item.get("name") for item in files_payload]
    pretty(
        "file_listing",
        {
            "directory": directory,
            "file_count": len(files_payload),
            "file_names": file_names,
            "uploaded_file_present": filename in file_names,
        },
    )

    relative_path = f"{directory.strip('/')}/{filename}"
    download_ok, download_payload = download_lakehouse_file(
        workspace_id=workspace_id,
        lakehouse_id=lakehouse_id,
        relative_path=relative_path,
    )
    require_ok("download_lakehouse_file", download_ok, download_payload)
    downloaded_text = download_payload.decode("utf-8")
    pretty(
        "file_download",
        {
            "relative_path": relative_path,
            "byte_count": len(download_payload),
            "content_matches": downloaded_text == content_text,
            "content_preview": downloaded_text[:200],
        },
    )


if __name__ == "__main__":
    main()
