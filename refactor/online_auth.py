from __future__ import annotations

import os
import time
from typing import Any

import requests
from azure.identity import InteractiveBrowserCredential

from env_utils import load_env


load_env()

SHAREPOINT_HOST = os.getenv("SHAREPOINT_HOST", "blah.sharepoint.com")
SITE_PATH = os.getenv("SHAREPOINT_SITE_PATH", "sites/*")
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
SHAREPOINT_SCOPE = "https://graph.microsoft.com/.default"
FABRIC_WORKSPACES_URL = "https://api.fabric.microsoft.com/v1/workspaces"
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
        print(f"[DEBUG] SharePoint drives: status={response.status_code} count={len(drives_payload)}")
        drives = [
            {
                "name": drive.get("name", "Untitled drive"),
                "webUrl": drive.get("webUrl"),
                "id": drive.get("id"),
            }
            for drive in drives_payload
        ]
        return True, drives
    print(f"[DEBUG] SharePoint drives failed: status={response.status_code} body={response.text[:500]}")
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
