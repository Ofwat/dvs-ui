from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from refactor.online_auth import (
    download_sharepoint_item,
    get_group_drive,
    list_groups,
    list_sharepoint_drive_items,
    list_sharepoint_resources,
    resolve_sharepoint_share_url,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only SharePoint smoke test using a folder URL in JSON config."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("sharepoint_readonly_smoke_test.config.json"),
        help="Path to the SharePoint read-only smoke test JSON config file.",
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


def infer_source_from_drive_id(drive_id: str) -> dict[str, Any]:
    drives_ok, drives_payload = list_sharepoint_resources()
    require_ok("list_sharepoint_resources", drives_ok, drives_payload)
    for drive in drives_payload:
        if drive.get("id") == drive_id:
            return {
                "source_type": "drive",
                "target_name": drive.get("name"),
                "target_id": drive.get("id"),
                "web_url": drive.get("webUrl"),
            }

    groups_ok, groups_payload = list_groups()
    require_ok("list_groups", groups_ok, groups_payload)
    for group in groups_payload:
        drive_ok, drive_payload = get_group_drive(group["id"])
        if not drive_ok:
            continue
        if drive_payload.get("id") == drive_id:
            return {
                "source_type": "group",
                "target_name": group.get("name"),
                "target_id": group.get("id"),
                "drive_name": drive_payload.get("name"),
                "web_url": drive_payload.get("webUrl"),
            }

    return {
        "source_type": "unknown",
        "target_name": None,
        "target_id": None,
        "web_url": None,
    }


def main():
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)

    sharepoint = config["sharepoint"]
    folder_url = sharepoint.get("folder_url")
    if not folder_url:
        raise RuntimeError(
            "Config must include 'sharepoint.folder_url'. "
            "The old drive_name/folder_path shape is no longer supported by this smoke test."
        )

    pretty(
        "config",
        {
            "config_path": str(args.config.resolve()),
            "folder_url": folder_url,
            "read_only": True,
            "download_mode": "random_file",
        },
    )

    resolved_ok, resolved_payload = resolve_sharepoint_share_url(folder_url)
    require_ok("resolve_sharepoint_share_url", resolved_ok, resolved_payload)

    parent_reference = resolved_payload.get("parentReference", {})
    drive_id = parent_reference.get("driveId")
    item_id = resolved_payload.get("id")
    if not drive_id or not item_id:
        raise RuntimeError("Resolved SharePoint URL did not return driveId/itemId.")

    source_info = infer_source_from_drive_id(drive_id)
    pretty(
        "resolved_source",
        {
            "source_type": source_info.get("source_type"),
            "target_name": source_info.get("target_name"),
            "target_id": source_info.get("target_id"),
            "drive_id": drive_id,
            "drive_name": source_info.get("drive_name"),
            "web_url": source_info.get("web_url"),
            "folder_name": resolved_payload.get("name"),
            "folder_item_id": item_id,
        },
    )

    children_ok, children_payload = list_sharepoint_drive_items(drive_id, item_id)
    require_ok("list_sharepoint_drive_items", children_ok, children_payload)
    files = [item for item in children_payload if not item.get("isFolder")]
    folders = [item for item in children_payload if item.get("isFolder")]
    pretty(
        "folder_contents",
        {
            "folder_name": resolved_payload.get("name"),
            "folder_item_id": item_id,
            "folder_count": len(folders),
            "file_count": len(files),
            "folders": [
                {
                    "name": item.get("name"),
                    "id": item.get("id"),
                }
                for item in folders
            ],
            "files": [
                {
                    "name": item.get("name"),
                    "id": item.get("id"),
                    "webUrl": item.get("webUrl"),
                }
                for item in files
            ],
        },
    )

    if not files:
        pretty(
            "random_file_download",
            {
                "message": "No files found in the specified folder.",
            },
        )
        return

    selected_file = random.choice(files)
    download_ok, download_payload = download_sharepoint_item(drive_id, selected_file["id"])
    require_ok("download_sharepoint_item", download_ok, download_payload)
    pretty(
        "random_file_download",
        {
            "file_name": selected_file.get("name"),
            "item_id": selected_file.get("id"),
            "createdDateTime": selected_file.get("createdDateTime"),
            "lastModifiedDateTime": selected_file.get("lastModifiedDateTime"),
            "byte_count": len(download_payload),
        },
    )


if __name__ == "__main__":
    main()
