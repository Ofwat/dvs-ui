from __future__ import annotations

import base64
import platform
import os
import socket
import subprocess
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import parse_qs
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from azure.identity import InteractiveBrowserCredential
from azure.identity._internal import within_dac, wrap_exceptions
try:
    from azure.core.exceptions import ClientAuthenticationError
except ImportError:
    ClientAuthenticationError = Exception  # type: ignore[assignment]
try:
    from azure.identity import AuthenticationRecord, TokenCachePersistenceOptions
except ImportError:
    AuthenticationRecord = None  # type: ignore[assignment]
    TokenCachePersistenceOptions = None  # type: ignore[assignment]
try:
    from services.auth import TokenManager
except ImportError:
    from refactor.services.auth import TokenManager

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
FABRIC_PIPELINES_URL = "https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/dataPipelines"
FABRIC_LAKEHOUSES_URL = "https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/lakehouses"
FABRIC_LAKEHOUSE_TABLES_URL = (
    "https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables"
)
FABRIC_ITEM_JOB_INSTANCES_URL = (
    "https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items/{item_id}/jobs/instances"
)
FABRIC_ITEM_JOB_INSTANCE_URL = (
    "https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items/{item_id}/jobs/instances/{job_instance_id}"
)
ONELAKE_LIST_PATHS_URL = "https://onelake.dfs.fabric.microsoft.com/{filesystem}"
ONELAKE_FILE_URL = "https://onelake.dfs.fabric.microsoft.com/{filesystem}/{path}"
SHAREPOINT_DRIVE_URL = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}:/{SITE_PATH}:/drives"
SHAREPOINT_DRIVE_ITEMS_URL = "https://graph.microsoft.com/v1.0/drives/{drive}/root/children"
SHAREPOINT_DRIVE_ITEM_CHILDREN_URL = "https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/children"
SHAREPOINT_DRIVE_ITEM_CONTENT_URL = "https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/content"
GROUPS_URL = "https://graph.microsoft.com/v1.0/groups"
GROUP_DRIVE_URL = "https://graph.microsoft.com/v1.0/groups/{group_id}/drive"
DRIVE_URL = "https://graph.microsoft.com/v1.0/drives/{drive_id}"
SHAREPOINT_SHARE_ITEM_URL = "https://graph.microsoft.com/v1.0/shares/{share_id}/driveItem"
ME_URL = "https://graph.microsoft.com/v1.0/me"
TOKEN_CACHE_NAME = os.getenv("AZURE_TOKEN_CACHE_NAME", "dvs-ui-token-cache")
AUTH_RECORD_PATH = Path.home() / ".dvs-ui" / "azure-auth-record.json"


def _initiate_auth_code_flow(app: Any, scopes: list[str], redirect_uri: str, *, claims: str | None, login_hint: str | None):
    return app.initiate_auth_code_flow(
        scopes,
        redirect_uri=redirect_uri,
        prompt="select_account",
        claims_challenge=claims,
        login_hint=login_hint,
        response_mode="form_post",
    )


def _open_browser(url: str) -> bool:
    opened = webbrowser.open(url)
    if not opened:
        uname = platform.uname()
        system = uname[0].lower()
        release = uname[2].lower()
        if "microsoft" in release and system == "linux":
            kwargs = {"timeout": 5}

            try:
                exit_code = subprocess.call(
                    ["powershell.exe", "-NoProfile", "-Command", 'Start-Process "{}"'.format(url)], **kwargs
                )
                opened = exit_code == 0
            except Exception:  # pylint:disable=broad-except
                # powershell.exe isn't available, or the subprocess timed out
                pass
    return opened


class _FormPostInteractiveBrowserCredential(InteractiveBrowserCredential):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("_server_class", _FormPostAuthCodeRedirectServer)
        super().__init__(**kwargs)

    @wrap_exceptions
    def _request_token(self, *scopes: str, **kwargs) -> dict:
        server = None
        redirect_uri = ""
        if self._parsed_url:
            try:
                redirect_uri = "http://{}:{}".format(self._parsed_url.hostname, self._parsed_url.port)
                server = self._server_class(self._parsed_url.hostname, self._parsed_url.port, timeout=self._timeout)
            except socket.error as ex:
                raise ClientAuthenticationError(message="Couldn't start an HTTP server on " + redirect_uri) from ex
        else:
            for port in range(8400, 9000):
                try:
                    server = self._server_class("localhost", port, timeout=self._timeout)
                    redirect_uri = "http://localhost:{}".format(port)
                    break
                except socket.error:
                    continue

        if not server:
            raise ClientAuthenticationError(message="Couldn't start an HTTP server on localhost")

        scopes = list(scopes)
        claims = kwargs.get("claims")
        app = self._get_app(**kwargs)
        flow = _initiate_auth_code_flow(
            app,
            scopes,
            redirect_uri,
            claims=claims,
            login_hint=self._login_hint,
        )
        if "auth_uri" not in flow:
            raise ClientAuthenticationError("Failed to begin authentication flow")

        if not _open_browser(flow["auth_uri"]):
            raise ClientAuthenticationError(message="Failed to open a browser")

        response = server.wait_for_redirect()
        if not response:
            if within_dac.get():
                raise ClientAuthenticationError(
                    message="Timed out after waiting {} seconds for the user to authenticate".format(self._timeout)
                )
            raise ClientAuthenticationError(
                message="Timed out after waiting {} seconds for the user to authenticate".format(self._timeout)
            )

        return app.acquire_token_by_auth_code_flow(flow, response, scopes=scopes, claims_challenge=claims)


class _FormPostAuthCodeRedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("/favicon.ico"):
            self.send_response(204)
            self.end_headers()
            return

        query = self.path.split("?", 1)[-1]
        parsed = parse_qs(query, keep_blank_values=True)
        self.server.query_params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in parsed.items()}

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"Authentication complete. You can close this window.")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = parse_qs(body, keep_blank_values=True)
        self.server.query_params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in parsed.items()}

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"Authentication complete. You can close this window.")

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        pass


class _FormPostAuthCodeRedirectServer(HTTPServer):
    query_params: dict[str, Any] = {}

    def __init__(self, hostname: str, port: int, timeout: int) -> None:
        HTTPServer.__init__(self, (hostname, port), _FormPostAuthCodeRedirectHandler)
        self.timeout = timeout

    def wait_for_redirect(self) -> dict[str, Any]:
        while not self.query_params:
            try:
                self.handle_request()
            except (IOError, ValueError):
                break

        self.server_close()
        return self.query_params

    def handle_timeout(self):
        self.server_close()


def _load_auth_record() -> Any | None:
    if not AuthenticationRecord or not AUTH_RECORD_PATH.exists():
        return None
    return AuthenticationRecord.deserialize(AUTH_RECORD_PATH.read_text(encoding="utf-8"))


def _save_auth_record(record: Any):
    AUTH_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_RECORD_PATH.write_text(record.serialize(), encoding="utf-8")


def _clear_auth_record():
    try:
        AUTH_RECORD_PATH.unlink(missing_ok=True)
    except TypeError:
        if AUTH_RECORD_PATH.exists():
            AUTH_RECORD_PATH.unlink()


def clear_authentication_state():
    global _CREDENTIAL
    _clear_auth_record()
    TOKEN_MANAGER.clear_cache()
    _CREDENTIAL = None


def _build_credential(ignore_saved_auth: bool = False) -> InteractiveBrowserCredential:
    kwargs: dict[str, Any] = {}
    if TokenCachePersistenceOptions:
        kwargs["cache_persistence_options"] = TokenCachePersistenceOptions(
            name=TOKEN_CACHE_NAME,
            allow_unencrypted_storage=True,
        )
    auth_record = None if ignore_saved_auth else _load_auth_record()
    if auth_record is not None:
        kwargs["authentication_record"] = auth_record
    tenant_id = os.getenv("AZURE_TENANT_ID")
    if tenant_id:
        kwargs["tenant_id"] = tenant_id
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        kwargs["client_id"] = client_id
    return _FormPostInteractiveBrowserCredential(**kwargs)


_CREDENTIAL: InteractiveBrowserCredential | None = None


def _get_credential(force_rebuild: bool = False, ignore_saved_auth: bool = False) -> InteractiveBrowserCredential:
    global _CREDENTIAL
    if _CREDENTIAL is None or force_rebuild:
        _CREDENTIAL = _build_credential(ignore_saved_auth=ignore_saved_auth)
    return _CREDENTIAL


def _acquire_token(scope: str):
    credential = _get_credential()
    try:
        return credential.get_token(scope)
    except ClientAuthenticationError:
        _clear_auth_record()

    credential = _get_credential(force_rebuild=True, ignore_saved_auth=True)
    record = credential.authenticate(scopes=[scope])
    _save_auth_record(record)
    credential = _get_credential(force_rebuild=True, ignore_saved_auth=False)
    return credential.get_token(scope)


TOKEN_MANAGER = TokenManager(
    token_provider=_acquire_token,
    refresh_margin_seconds=int(os.getenv("TOKEN_REFRESH_MARGIN_SECONDS", "60")),
)


def _sharepoint_api_url() -> str:
    return f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}:/{SITE_PATH}"


def _cached_token(scope: str) -> dict[str, Any] | None:
    return TOKEN_MANAGER.get_cached_token(scope)


def _format_identity_name(username: str) -> str:
    local_part = username.split("@", 1)[0].strip()
    if not local_part:
        return username
    words = [part for part in re.split(r"[._\-\s]+", local_part) if part]
    if not words:
        return local_part
    return " ".join(word.capitalize() for word in words)


def _build_initials(source: str) -> str:
    cleaned = source.split("@", 1)[0].strip()
    parts = [part for part in re.split(r"[._\-\s]+", cleaned) if part]
    if not parts:
        return (cleaned[:2] or "?").upper()
    initials = "".join(part[0] for part in parts[:2]).upper()
    return initials or "?"


def get_current_auth_identity() -> dict[str, Any]:
    record = _load_auth_record()
    if not record:
        return {
            "signed_in": False,
            "display_name": None,
            "username": None,
            "initials": None,
            "status_message": "Not signed in.",
        }

    username = getattr(record, "username", None) or ""
    display_name = _format_identity_name(username) if username else "Signed in"
    token_data = _cached_token(SHAREPOINT_SCOPE)
    if token_data:
        expires_in = max(0, int(token_data["expires_on"] - time.time()))
        status_message = f"Signed in. Token expires in {expires_in}s."
    else:
        status_message = "Signed in."
    return {
        "signed_in": True,
        "display_name": display_name,
        "username": username or None,
        "initials": _build_initials(username or display_name),
        "status_message": status_message,
    }


def get_cached_access_status() -> tuple[bool, str]:
    token_data = _cached_token(SHAREPOINT_SCOPE)
    if not token_data:
        return False, "You are not signed in. Click the button to authenticate."
    expires_in = int(token_data["expires_on"] - time.time())
    return True, f"Signed in. Token expires in {expires_in}s."


def _request_with_auto_refresh(method: str, url: str, scope: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    auth_headers = TOKEN_MANAGER.get_headers(scope)
    merged_headers = {**auth_headers, **headers}
    response = requests.request(method, url, headers=merged_headers, **kwargs)
    if response.status_code != 401:
        return response

    auth_headers = TOKEN_MANAGER.get_headers(scope, force_refresh=True)
    merged_headers = {**auth_headers, **headers}
    return requests.request(method, url, headers=merged_headers, **kwargs)


def _ensure_authenticated(scope: str):
    if not AuthenticationRecord:
        return
    if _load_auth_record() is not None:
        return
    credential = _get_credential(force_rebuild=True, ignore_saved_auth=True)
    record = credential.authenticate(scopes=[scope])
    _save_auth_record(record)


def check_token_access(force: bool = False) -> tuple[bool, Any]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    if force:
        TOKEN_MANAGER.get_token(SHAREPOINT_SCOPE, force_refresh=True)
    response = _request_with_auto_refresh("GET", _sharepoint_api_url(), SHAREPOINT_SCOPE)
    if response.ok:
        return True, response.json()
    return False, response.text


def list_fabric_workspaces() -> tuple[bool, list[dict] | str]:
    _ensure_authenticated(FABRIC_SCOPE)
    response = _request_with_auto_refresh("GET", FABRIC_WORKSPACES_URL, FABRIC_SCOPE)
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def _response_json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        if response.text:
            return response.text
        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
        }


def _normalize_pipeline_parameters(parameters: dict[str, Any] | None) -> dict[str, Any] | None:
    if not parameters:
        return None
    normalized: dict[str, Any] = {}
    for key, value in parameters.items():
        normalized[key] = value
    return normalized


def list_fabric_pipelines(workspace_id: str) -> tuple[bool, list[dict] | str]:
    _ensure_authenticated(FABRIC_SCOPE)
    url = FABRIC_PIPELINES_URL.format(workspace_id=workspace_id)
    response = _request_with_auto_refresh("GET", url, FABRIC_SCOPE)
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def trigger_fabric_pipeline(
    workspace_id: str,
    pipeline_id: str,
    parameters: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any] | str]:
    _ensure_authenticated(FABRIC_SCOPE)
    url = FABRIC_ITEM_JOB_INSTANCES_URL.format(
        workspace_id=workspace_id,
        item_id=pipeline_id,
    )
    body: dict[str, Any] = {}
    normalized_parameters = _normalize_pipeline_parameters(parameters)
    if normalized_parameters:
        body["executionData"] = {"parameters": normalized_parameters}
    response = _request_with_auto_refresh(
        "POST",
        url,
        FABRIC_SCOPE,
        params={"jobType": "Pipeline"},
        json=body or None,
    )
    if response.ok:
        payload = _response_json_or_text(response)
        if isinstance(payload, dict):
            return True, payload
        return True, {"message": payload}
    return False, response.text


def list_fabric_pipeline_runs(
    workspace_id: str,
    pipeline_id: str,
) -> tuple[bool, list[dict] | str]:
    _ensure_authenticated(FABRIC_SCOPE)
    url = FABRIC_ITEM_JOB_INSTANCES_URL.format(
        workspace_id=workspace_id,
        item_id=pipeline_id,
    )
    response = _request_with_auto_refresh(
        "GET",
        url,
        FABRIC_SCOPE,
        params={"jobType": "Pipeline"},
    )
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def list_running_fabric_pipeline_runs(
    workspace_id: str,
    pipeline_id: str,
) -> tuple[bool, list[dict] | str]:
    success, payload = list_fabric_pipeline_runs(workspace_id, pipeline_id)
    if not success:
        return False, payload

    active_statuses = {"notstarted", "queued", "inprogress", "running"}
    active_runs = [
        item
        for item in payload
        if str(item.get("status") or item.get("state") or "").strip().lower() in active_statuses
    ]
    return True, active_runs


def get_fabric_pipeline_run(
    workspace_id: str,
    pipeline_id: str,
    job_instance_id: str,
) -> tuple[bool, dict[str, Any] | str]:
    _ensure_authenticated(FABRIC_SCOPE)
    url = FABRIC_ITEM_JOB_INSTANCE_URL.format(
        workspace_id=workspace_id,
        item_id=pipeline_id,
        job_instance_id=job_instance_id,
    )
    response = _request_with_auto_refresh(
        "GET",
        url,
        FABRIC_SCOPE,
        params={"jobType": "Pipeline"},
    )
    if response.ok:
        payload = _response_json_or_text(response)
        if isinstance(payload, dict):
            return True, payload
        return True, {"message": payload}
    return False, response.text


def list_fabric_lakehouses(workspace_id: str) -> tuple[bool, list[dict] | str]:
    _ensure_authenticated(FABRIC_SCOPE)
    url = FABRIC_LAKEHOUSES_URL.format(workspace_id=workspace_id)
    response = _request_with_auto_refresh("GET", url, FABRIC_SCOPE)
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def list_lakehouse_tables(workspace_id: str, lakehouse_id: str) -> tuple[bool, list[dict] | str]:
    _ensure_authenticated(FABRIC_SCOPE)
    url = FABRIC_LAKEHOUSE_TABLES_URL.format(
        workspace_id=workspace_id,
        lakehouse_id=lakehouse_id,
    )
    response = _request_with_auto_refresh("GET", url, FABRIC_SCOPE)
    if response.ok:
        payload = response.json()
        return True, payload.get("value", [])
    return False, response.text


def list_lakehouse_files(
    workspace_id: str, lakehouse_id: str, directory: str = "Files"
) -> tuple[bool, list[dict] | str]:
    _ensure_authenticated(ONELAKE_SCOPE)
    headers = {"x-ms-version": "2023-08-03"}
    url = ONELAKE_LIST_PATHS_URL.format(filesystem=workspace_id)
    params = {
        "resource": "filesystem",
        "recursive": "false",
        "directory": f"{lakehouse_id}/{directory}",
    }
    response = _request_with_auto_refresh(
        "GET",
        url,
        ONELAKE_SCOPE,
        headers=headers,
        params=params,
    )
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
    workspace_id: str,
    lakehouse_id: str,
    filename: str,
    content: bytes,
    directory: str = "Files/Uploads",
) -> tuple[bool, str]:
    _ensure_authenticated(ONELAKE_SCOPE)
    headers = {"x-ms-version": "2023-08-03"}
    cleaned_directory = directory.strip("/")
    path = f"{lakehouse_id}/{cleaned_directory}/{filename}"
    return _write_lakehouse_file_bytes(workspace_id, path, content, headers)


def _write_lakehouse_file_bytes(
    workspace_id: str,
    lakehouse_path: str,
    content: bytes,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    effective_headers = {"x-ms-version": "2023-08-03"}
    if headers:
        effective_headers.update(headers)
    url = ONELAKE_FILE_URL.format(filesystem=workspace_id, path=quote(lakehouse_path))
    create = _request_with_auto_refresh(
        "PUT",
        url,
        ONELAKE_SCOPE,
        headers=effective_headers,
        params={"resource": "file"},
    )
    if not create.ok:
        return False, create.text
    append = _request_with_auto_refresh(
        "PATCH",
        url,
        ONELAKE_SCOPE,
        headers=effective_headers,
        params={"action": "append", "position": 0},
        data=content,
    )
    if not append.ok:
        return False, append.text
    flush = _request_with_auto_refresh(
        "PATCH",
        url,
        ONELAKE_SCOPE,
        headers=effective_headers,
        params={"action": "flush", "position": len(content)},
    )
    if not flush.ok:
        return False, flush.text
    return True, "Uploaded file to lakehouse."


def write_lakehouse_file(
    workspace_id: str,
    lakehouse_id: str,
    relative_path: str,
    content: bytes,
) -> tuple[bool, str]:
    _ensure_authenticated(ONELAKE_SCOPE)
    cleaned_path = relative_path.strip("/")
    lakehouse_path = f"{lakehouse_id}/{cleaned_path}"
    return _write_lakehouse_file_bytes(workspace_id, lakehouse_path, content)


def download_lakehouse_file(
    workspace_id: str,
    lakehouse_id: str,
    relative_path: str,
) -> tuple[bool, bytes | str]:
    _ensure_authenticated(ONELAKE_SCOPE)
    headers = {"x-ms-version": "2023-08-03"}
    cleaned_path = relative_path.strip("/")
    path = f"{lakehouse_id}/{cleaned_path}"
    url = ONELAKE_FILE_URL.format(filesystem=workspace_id, path=quote(path))
    response = _request_with_auto_refresh(
        "GET",
        url,
        ONELAKE_SCOPE,
        headers=headers,
    )
    if response.ok:
        return True, response.content
    return False, response.text


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
    _ensure_authenticated(SHAREPOINT_SCOPE)
    response = _request_with_auto_refresh("GET", SHAREPOINT_DRIVE_URL, SHAREPOINT_SCOPE)
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


def list_groups() -> tuple[bool, list[dict[str, str]] | str]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    response = _request_with_auto_refresh(
        "GET",
        GROUPS_URL,
        SHAREPOINT_SCOPE,
        params={"$select": "id,displayName"},
    )
    if response.ok:
        payload = response.json()
        groups = [
            {
                "name": group.get("displayName", "Untitled group"),
                "id": group.get("id"),
            }
            for group in payload.get("value", [])
        ]
        return True, groups
    return False, response.text


def get_current_user() -> tuple[bool, dict[str, Any] | str]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    response = _request_with_auto_refresh(
        "GET",
        ME_URL,
        SHAREPOINT_SCOPE,
        params={"$select": "id,displayName,mail,userPrincipalName"},
    )
    if response.ok:
        payload = response.json()
        return True, {
            "id": payload.get("id"),
            "displayName": payload.get("displayName"),
            "mail": payload.get("mail"),
            "userPrincipalName": payload.get("userPrincipalName"),
        }
    return False, response.text


def get_group_drive(group_id: str) -> tuple[bool, dict[str, str] | str]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    url = GROUP_DRIVE_URL.format(group_id=group_id)
    response = _request_with_auto_refresh("GET", url, SHAREPOINT_SCOPE)
    if response.ok:
        payload = response.json()
        return True, {
            "name": payload.get("name", "Untitled drive"),
            "id": payload.get("id"),
            "webUrl": payload.get("webUrl"),
        }
    return False, response.text


def get_drive(drive_id: str) -> tuple[bool, dict[str, Any] | str]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    url = DRIVE_URL.format(drive_id=drive_id)
    response = _request_with_auto_refresh("GET", url, SHAREPOINT_SCOPE)
    if response.ok:
        return True, response.json()
    return False, response.text


def resolve_sharepoint_share_url(share_url: str) -> tuple[bool, dict[str, Any] | str]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    encoded = base64.urlsafe_b64encode(share_url.encode("utf-8")).decode("utf-8").rstrip("=")
    share_id = f"u!{encoded}"
    url = SHAREPOINT_SHARE_ITEM_URL.format(share_id=share_id)
    response = _request_with_auto_refresh("GET", url, SHAREPOINT_SCOPE)
    if response.ok:
        return True, response.json()
    return False, response.text


def list_sharepoint_drive_items(
    drive_id: str, parent_item_id: str | None = None
) -> tuple[bool, list[dict[str, str]] | str]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    if parent_item_id:
        url = SHAREPOINT_DRIVE_ITEM_CHILDREN_URL.format(drive=drive_id, item=parent_item_id)
    else:
        url = SHAREPOINT_DRIVE_ITEMS_URL.format(drive=drive_id)
    response = _request_with_auto_refresh("GET", url, SHAREPOINT_SCOPE)
    if response.ok:
        payload = response.json()
        items = [
            {
                "name": item.get("name", "Untitled item"),
                "id": item.get("id"),
                "isFolder": bool(item.get("folder")),
                "webUrl": item.get("webUrl"),
                "createdDateTime": item.get("createdDateTime"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
                "lastModifiedBy": (
                    item.get("lastModifiedBy", {}).get("user", {}).get("displayName")
                    or item.get("lastModifiedBy", {}).get("application", {}).get("displayName")
                ),
            }
            for item in payload.get("value", [])
        ]
        return True, items
    return False, response.text


def download_sharepoint_item(drive_id: str, item_id: str) -> tuple[bool, bytes | str]:
    _ensure_authenticated(SHAREPOINT_SCOPE)
    url = SHAREPOINT_DRIVE_ITEM_CONTENT_URL.format(drive=drive_id, item=item_id)
    response = _request_with_auto_refresh("GET", url, SHAREPOINT_SCOPE)
    if response.ok:
        return True, response.content
    return False, response.text
