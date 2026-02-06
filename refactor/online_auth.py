from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import requests
from azure.identity import InteractiveBrowserCredential

try:
    from env_utils import load_env
except ImportError:
    import importlib.util
    from pathlib import Path

    candidates = []
    if "__file__" in globals():
        candidates.append(Path(__file__).resolve().parent / "env_utils.py")
    cwd = Path.cwd()
    candidates.append(cwd / "refactor" / "env_utils.py")
    candidates.append(cwd / "env_utils.py")

    _env_path = next((path for path in candidates if path.exists()), None)
    if not _env_path:
        raise
    _spec = importlib.util.spec_from_file_location("env_utils", _env_path)
    if not _spec or not _spec.loader:
        raise
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    load_env = _module.load_env  # type: ignore[attr-defined]


load_env()

SHAREPOINT_HOST = os.getenv("SHAREPOINT_HOST", "blah.sharepoint.com")
SITE_PATH = os.getenv("SHAREPOINT_SITE_PATH", "sites/*")
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
SHAREPOINT_SCOPE = "https://graph.microsoft.com/.default"
ONELAKE_SCOPE = "https://storage.azure.com/.default"
FABRIC_WORKSPACES_URL = "https://api.fabric.microsoft.com/v1/workspaces"
FABRIC_LAKEHOUSES_URL = "https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/lakehouses"
FABRIC_LAKEHOUSE_TABLES_URL = (
    "https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables"
)
ONELAKE_LIST_PATHS_URL = "https://onelake.dfs.fabric.microsoft.com/{filesystem}"
ONELAKE_FILE_URL = "https://onelake.dfs.fabric.microsoft.com/{filesystem}/{path}"
SHAREPOINT_DRIVE_URL = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}:/{SITE_PATH}:/drives"
SHAREPOINT_DRIVE_ITEMS_URL = "https://graph.microsoft.com/v1.0/drives/{drive}/root/children"
SHAREPOINT_DRIVE_ITEM_CHILDREN_URL = "https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/children"
SHAREPOINT_DRIVE_ITEM_CONTENT_URL = "https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/content"
LOCAL_TOKEN_CACHE: dict[str, dict[str, Any]] = {}


def _sharepoint_api_url() -> str:
    return f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}:/{SITE_PATH}"


def _cache_token(scope: str, token: Any) -> dict[str, Any]:
    entry = {
        "token": token.token,
        "expires_on": token.expires_on,
    }
    LOCAL_TOKEN_CACHE[scope] = entry
    return entry


def _cached_token(scope: str) -> dict[str, Any] | None:
    token_data = LOCAL_TOKEN_CACHE.get(scope)
    if not token_data:
        return None
    if token_data.get("expires_on", 0) <= time.time():
        LOCAL_TOKEN_CACHE.pop(scope, None)
        return None
    return token_data


def _cached_headers(scope: str) -> dict[str, str] | None:
    token_data = _cached_token(scope)
    if not token_data:
        return None
    return {"Authorization": f"Bearer {token_data['token']}"}


def get_cached_access_status() -> tuple[bool, str]:
    token_data = _cached_token(SHAREPOINT_SCOPE)
    if not token_data:
        return False, "You are not signed in. Click the button to authenticate."
    expires_in = int(token_data["expires_on"] - time.time())
    return True, f"Signed in. Token expires in {expires_in}s."


def _acquire_token(scope: str, force: bool = False) -> dict[str, Any]:
    token_data = _cached_token(scope)
    if token_data and not force:
        return token_data
    credential = InteractiveBrowserCredential()
    token = credential.get_token(scope)
    return _cache_token(scope, token)


def check_token_access(force: bool = False) -> tuple[bool, Any]:
    token_data = _acquire_token(SHAREPOINT_SCOPE, force=force)
    headers = {"Authorization": f"Bearer {token_data['token']}"}
    response = requests.get(_sharepoint_api_url(), headers=headers)
    if response.ok:
        return True, response.json()
    return False, response.text


def list_fabric_workspaces() -> tuple[bool, list[dict] | str]:
    headers = _cached_headers(FABRIC_SCOPE)
    if not headers:
        _acquire_token(FABRIC_SCOPE, force=True)
        headers = _cached_headers(FABRIC_SCOPE)
        if not headers:
            return False, "Sign in to list Fabric workspaces."
    response = requests.get(FABRIC_WORKSPACES_URL, headers=headers)
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def list_fabric_lakehouses(workspace_id: str) -> tuple[bool, list[dict] | str]:
    headers = _cached_headers(FABRIC_SCOPE)
    if not headers:
        _acquire_token(FABRIC_SCOPE, force=True)
        headers = _cached_headers(FABRIC_SCOPE)
        if not headers:
            return False, "Sign in to list Fabric lakehouses."
    url = FABRIC_LAKEHOUSES_URL.format(workspace_id=workspace_id)
    response = requests.get(url, headers=headers)
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def list_lakehouse_tables(workspace_id: str, lakehouse_id: str) -> tuple[bool, list[dict] | str]:
    headers = _cached_headers(FABRIC_SCOPE)
    if not headers:
        _acquire_token(FABRIC_SCOPE, force=True)
        headers = _cached_headers(FABRIC_SCOPE)
        if not headers:
            return False, "Sign in to list Fabric lakehouse tables."
    url = FABRIC_LAKEHOUSE_TABLES_URL.format(
        workspace_id=workspace_id,
        lakehouse_id=lakehouse_id,
    )
    response = requests.get(url, headers=headers)
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def list_lakehouse_files(
    workspace_id: str, lakehouse_id: str, directory: str = "Files"
) -> tuple[bool, list[dict] | str]:
    headers = _cached_headers(ONELAKE_SCOPE)
    if not headers:
        _acquire_token(ONELAKE_SCOPE, force=True)
        headers = _cached_headers(ONELAKE_SCOPE)
        if not headers:
            return False, "Sign in to list OneLake files."
    headers = {
        **headers,
        "x-ms-version": "2023-08-03",
    }
    url = ONELAKE_LIST_PATHS_URL.format(filesystem=workspace_id)
    params = {
        "resource": "filesystem",
        "recursive": "false",
        "directory": f"{lakehouse_id}/{directory}",
    }
    response = requests.get(url, headers=headers, params=params)
    if response.ok:
        payload = response.json()
        paths = payload.get("paths", [])
        items = []
        prefix = f"{lakehouse_id}/{directory}/"
        for entry in paths:
            name = entry.get("name", "")
            if name.startswith(prefix):
                name = name[len(prefix) :]
            items.append(
                {
                    "name": name,
                    "isDirectory": str(entry.get("isDirectory", "")).lower() == "true",
                    "raw": entry,
                }
            )
        return True, items
    return False, response.text


def upload_lakehouse_file(
    workspace_id: str, lakehouse_id: str, filename: str, content: bytes
) -> tuple[bool, str]:
    headers = _cached_headers(ONELAKE_SCOPE)
    if not headers:
        _acquire_token(ONELAKE_SCOPE, force=True)
        headers = _cached_headers(ONELAKE_SCOPE)
        if not headers:
            return False, "Sign in to upload OneLake files."
    headers = {
        **headers,
        "x-ms-version": "2023-08-03",
    }
    path = f"{lakehouse_id}/Files/Uploads/{filename}"
    url = ONELAKE_FILE_URL.format(filesystem=workspace_id, path=quote(path))
    create = requests.put(url, headers=headers, params={"resource": "file"})
    if not create.ok:
        return False, create.text
    append = requests.patch(
        url,
        headers=headers,
        params={"action": "append", "position": 0},
        data=content,
    )
    if not append.ok:
        return False, append.text
    flush = requests.patch(
        url,
        headers=headers,
        params={"action": "flush", "position": len(content)},
    )
    if not flush.ok:
        return False, flush.text
    return True, f"Uploaded {filename} to lakehouse."


def transfer_sharepoint_file_to_lakehouse(
    drive_id: str,
    item_id: str,
    filename: str,
    workspace_id: str,
    lakehouse_id: str,
) -> tuple[bool, str]:
    success, payload = download_sharepoint_item(drive_id, item_id)
    if not success:
        return False, f"SharePoint download failed: {payload}"
    return upload_lakehouse_file(workspace_id, lakehouse_id, filename, payload)


def list_sharepoint_resources() -> tuple[bool, list[dict[str, str]] | str]:
    headers = _cached_headers(SHAREPOINT_SCOPE)
    if not headers:
        _acquire_token(SHAREPOINT_SCOPE, force=True)
        headers = _cached_headers(SHAREPOINT_SCOPE)
        if not headers:
            return False, "Sign in to list SharePoint resources."
    response = requests.get(SHAREPOINT_DRIVE_URL, headers=headers)
    if response.ok:
        payload = response.json()
        drives_payload = payload.get("value", [])
        drives = [
            {
                "name": drive.get("name", "Untitled drive"),
                "webUrl": drive.get("webUrl"),
                "id": drive.get("id"),
            }
            for drive in drives_payload
        ]
        return True, drives
    return False, response.text


def list_sharepoint_drive_items(
    drive_id: str, parent_item_id: str | None = None
) -> tuple[bool, list[dict[str, str]] | str]:
    headers = _cached_headers(SHAREPOINT_SCOPE)
    if not headers:
        return False, "Sign in to list SharePoint resources."
    if parent_item_id:
        url = SHAREPOINT_DRIVE_ITEM_CHILDREN_URL.format(drive=drive_id, item=parent_item_id)
    else:
        url = SHAREPOINT_DRIVE_ITEMS_URL.format(drive=drive_id)
    response = requests.get(url, headers=headers)
    if response.ok:
        payload = response.json()
        items = [
            {
                "name": item.get("name", "Untitled item"),
                "id": item.get("id"),
                "isFolder": bool(item.get("folder")),
                "webUrl": item.get("webUrl"),
            }
            for item in payload.get("value", [])
        ]
        return True, items
    return False, response.text


def download_sharepoint_item(drive_id: str, item_id: str) -> tuple[bool, bytes | str]:
    headers = _cached_headers(SHAREPOINT_SCOPE)
    if not headers:
        return False, "Sign in to download files."
    url = SHAREPOINT_DRIVE_ITEM_CONTENT_URL.format(drive=drive_id, item=item_id)
    response = requests.get(url, headers=headers)
    if response.ok:
        return True, response.content
    return False, response.text
