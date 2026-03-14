from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


VALIDATION_VALIDATED = "validated"
VALIDATION_NOT_VALIDATED = "not_validated"
TERMINAL_STATES = {"validated", "failed"}
SUBMISSION_EDIT_EVENT_TYPES = {
    "submission_created",
    "submission_template_upserted",
    "org_flag_changed",
    "template_flag_changed",
    "sharepoint_hashes_refreshed",
    "template_hashes_refreshed",
    "submission_note_updated",
    "submission_org_removed",
    "submission_org_upserted",
    "submission_template_removed",
}


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApiResponse:
    ok: bool
    data: dict[str, Any] | list[dict[str, Any]] | None
    error: ApiError | None
    correlation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class TrackedFileRef:
    path: str | None
    watch: bool = False
    watch_search_term: str | None = None
    current_hash: str | None = None
    validated_hash: str | None = None
    size_bytes: int | None = None
    created: str | None = None
    modified: str | None = None
    modified_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | str) -> "TrackedFileRef":
        if isinstance(payload, str):
            return cls(path=payload.strip())
        return cls(
            path=str(payload["path"]).strip() if payload.get("path") else None,
            watch=bool(payload.get("watch", False)),
            watch_search_term=str(payload["watch_search_term"]).strip() if payload.get("watch_search_term") else None,
            current_hash=str(payload["current_hash"]).strip() if payload.get("current_hash") else None,
            validated_hash=str(payload["validated_hash"]).strip() if payload.get("validated_hash") else None,
            size_bytes=int(payload["size_bytes"]) if payload.get("size_bytes") is not None else None,
            created=str(payload["created"]).strip() if payload.get("created") else None,
            modified=str(payload["modified"]).strip() if payload.get("modified") else None,
            modified_by=str(payload["modified_by"]).strip() if payload.get("modified_by") else None,
        )


@dataclass(frozen=True)
class SharePointSourceRef:
    source_type: str
    target_name: str
    root_path: str
    tracked_files: list[TrackedFileRef]
    folder_url: str | None = None
    drive_id: str | None = None
    linked_item_id: str | None = None
    linked_item_name: str | None = None
    linked_item_is_folder: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "target_name": self.target_name,
            "root_path": self.root_path,
            "tracked_files": [item.to_dict() for item in self.tracked_files],
            "folder_url": self.folder_url,
            "drive_id": self.drive_id,
            "linked_item_id": self.linked_item_id,
            "linked_item_name": self.linked_item_name,
            "linked_item_is_folder": self.linked_item_is_folder,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SharePointSourceRef":
        return cls(
            source_type=str(payload["source_type"]).strip().lower(),
            target_name=str(payload["target_name"]).strip(),
            root_path=str(payload["root_path"]).strip(),
            tracked_files=[
                TrackedFileRef.from_dict(item)
                for item in payload.get("tracked_files", [])
                if (
                    isinstance(item, dict)
                    and (str(item.get("path", "")).strip() or bool(item.get("watch")))
                ) or (isinstance(item, str) and item.strip())
            ],
            folder_url=str(payload["folder_url"]).strip() if payload.get("folder_url") else None,
            drive_id=str(payload["drive_id"]).strip() if payload.get("drive_id") else None,
            linked_item_id=str(payload["linked_item_id"]).strip() if payload.get("linked_item_id") else None,
            linked_item_name=str(payload["linked_item_name"]).strip() if payload.get("linked_item_name") else None,
            linked_item_is_folder=bool(payload["linked_item_is_folder"]) if payload.get("linked_item_is_folder") is not None else None,
        )


@dataclass(frozen=True)
class OrganisationSubmissionRef:
    sharepoint_source: SharePointSourceRef
    template_keys: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sharepoint_source": self.sharepoint_source.to_dict(),
            "template_keys": self.template_keys,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | SharePointSourceRef) -> "OrganisationSubmissionRef":
        if isinstance(payload, OrganisationSubmissionRef):
            return payload
        if isinstance(payload, SharePointSourceRef):
            return cls(sharepoint_source=payload, template_keys=[])
        if "sharepoint_source" not in payload:
            return cls(
                sharepoint_source=SharePointSourceRef.from_dict(payload),
                template_keys=[],
            )
        return cls(
            sharepoint_source=SharePointSourceRef.from_dict(payload["sharepoint_source"]),
            template_keys=[str(item).strip().upper() for item in payload.get("template_keys", []) if str(item).strip()],
        )


@dataclass(frozen=True)
class TemplateSubmissionRef:
    assignment_mode: str
    applies_to: list[str]
    sharepoint_source: SharePointSourceRef

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_mode": self.assignment_mode,
            "applies_to": self.applies_to,
            "sharepoint_source": self.sharepoint_source.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TemplateSubmissionRef":
        if isinstance(payload, TemplateSubmissionRef):
            return payload
        return cls(
            assignment_mode=str(payload.get("assignment_mode", "shared")).strip().lower(),
            applies_to=[str(item).strip().upper() for item in payload.get("applies_to", []) if str(item).strip()],
            sharepoint_source=SharePointSourceRef.from_dict(payload["sharepoint_source"]),
        )


@dataclass(frozen=True)
class SubmissionEvent:
    event_id: str
    event_ts_utc: str
    event_type: str
    submission_id: str
    process_cd: str
    submission_period_cd: str
    organisation_cd: str | None
    sharepoint_source: dict[str, Any] | None
    validation_flag: str | None
    actor: str
    reason: str | None = None
    payload_json: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SubmissionEvent":
        return cls(**payload)


class InMemorySubmissionEventStore:
    def __init__(self):
        self._events: list[SubmissionEvent] = []

    def append(self, event: SubmissionEvent):
        self._events.append(event)

    def list_all(self) -> list[SubmissionEvent]:
        return list(self._events)


class JsonlSubmissionEventStore:
    def __init__(self, events_path: str | Path):
        self._events_path = Path(events_path)
        self._events_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._events_path

    def append(self, event: SubmissionEvent):
        with self._events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=True))
            handle.write("\n")

    def list_all(self) -> list[SubmissionEvent]:
        if not self._events_path.exists():
            return []
        events: list[SubmissionEvent] = []
        with self._events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                events.append(SubmissionEvent.from_dict(json.loads(line)))
        return events


class TextBackedSubmissionEventStore:
    def __init__(
        self,
        read_text: Callable[[], str | None],
        write_text: Callable[[str], None],
    ):
        self._read_text = read_text
        self._write_text = write_text

    def append(self, event: SubmissionEvent):
        line = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=True)
        existing = self._read_text() or ""
        new_text = f"{existing}{line}\n" if existing else f"{line}\n"
        self._write_text(new_text)

    def list_all(self) -> list[SubmissionEvent]:
        raw = self._read_text()
        if not raw:
            return []
        events: list[SubmissionEvent] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            events.append(SubmissionEvent.from_dict(json.loads(line)))
        return events


class InMemoryIdempotencyStore:
    def __init__(self):
        self._entries: dict[tuple[str, str, str], tuple[str, ApiResponse]] = {}

    def get(self, operation: str, actor: str, key: str) -> tuple[str, ApiResponse] | None:
        return self._entries.get((operation, actor, key))

    def set(self, operation: str, actor: str, key: str, request_hash: str, response: ApiResponse):
        self._entries[(operation, actor, key)] = (request_hash, response)


class JsonFileIdempotencyStore:
    def __init__(self, store_path: str | Path):
        self._store_path = Path(store_path)
        self._store_path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, operation: str, actor: str, key: str) -> tuple[str, ApiResponse] | None:
        entries = self._load()
        payload = entries.get(self._entry_key(operation, actor, key))
        if not payload:
            return None
        return payload["request_hash"], ApiResponse(
            ok=payload["response"]["ok"],
            data=payload["response"]["data"],
            error=ApiError(**payload["response"]["error"]) if payload["response"]["error"] else None,
            correlation_id=payload["response"]["correlation_id"],
        )

    def set(self, operation: str, actor: str, key: str, request_hash: str, response: ApiResponse):
        entries = self._load()
        entries[self._entry_key(operation, actor, key)] = {
            "request_hash": request_hash,
            "response": response.to_dict(),
        }
        self._store_path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _entry_key(operation: str, actor: str, key: str) -> str:
        return f"{operation}:{actor}:{key}"

    def _load(self) -> dict[str, Any]:
        if not self._store_path.exists():
            return {}
        return json.loads(self._store_path.read_text(encoding="utf-8"))


class TextBackedIdempotencyStore:
    def __init__(
        self,
        read_text: Callable[[], str | None],
        write_text: Callable[[str], None],
    ):
        self._read_text = read_text
        self._write_text = write_text

    def get(self, operation: str, actor: str, key: str) -> tuple[str, ApiResponse] | None:
        entries = self._load()
        payload = entries.get(JsonFileIdempotencyStore._entry_key(operation, actor, key))
        if not payload:
            return None
        return payload["request_hash"], ApiResponse(
            ok=payload["response"]["ok"],
            data=payload["response"]["data"],
            error=ApiError(**payload["response"]["error"]) if payload["response"]["error"] else None,
            correlation_id=payload["response"]["correlation_id"],
        )

    def set(self, operation: str, actor: str, key: str, request_hash: str, response: ApiResponse):
        entries = self._load()
        entries[JsonFileIdempotencyStore._entry_key(operation, actor, key)] = {
            "request_hash": request_hash,
            "response": response.to_dict(),
        }
        self._write_text(json.dumps(entries, indent=2, sort_keys=True))

    def _load(self) -> dict[str, Any]:
        raw = self._read_text()
        if not raw:
            return {}
        return json.loads(raw)


class JsonProjectionStore:
    def __init__(self, projection_path: str | Path):
        self._projection_path = Path(projection_path)

    def write(self, payload: dict[str, Any]):
        self._projection_path.parent.mkdir(parents=True, exist_ok=True)
        self._projection_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class TextBackedProjectionStore:
    def __init__(self, write_text: Callable[[str], None]):
        self._write_text = write_text

    def write(self, payload: dict[str, Any]):
        self._write_text(json.dumps(payload, indent=2, sort_keys=True))


class ValidationServiceApi:
    def __init__(
        self,
        workspace_id: str,
        submissions_registry_path: str,
        event_store: InMemorySubmissionEventStore | JsonlSubmissionEventStore | TextBackedSubmissionEventStore | None = None,
        idempotency_store: InMemoryIdempotencyStore | JsonFileIdempotencyStore | TextBackedIdempotencyStore | None = None,
        submissions_projection_path: str | Path | None = None,
        submissions_projection_store: JsonProjectionStore | TextBackedProjectionStore | None = None,
        sharepoint_hash_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        run_file_stager: Callable[[dict[str, Any], dict[str, Any], str, str], dict[str, Any]] | None = None,
        projection_metadata_provider: Callable[[], dict[str, Any]] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
    ):
        self.workspace_id = workspace_id
        self.submissions_registry_path = submissions_registry_path
        self._events = event_store or InMemorySubmissionEventStore()
        self._idempotency = idempotency_store or InMemoryIdempotencyStore()
        self._submissions_projection_store = submissions_projection_store
        self._sharepoint_hash_resolver = sharepoint_hash_resolver
        self._run_file_stager = run_file_stager
        self._projection_metadata_provider = projection_metadata_provider
        if not self._submissions_projection_store and submissions_projection_path:
            self._submissions_projection_store = JsonProjectionStore(submissions_projection_path)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._id_fn = id_fn or (lambda: uuid4().hex)

    def list_submissions(
        self,
        process_cd: str | None = None,
        submission_period_cd: str | None = None,
        state: str | None = None,
        created_by: str | None = None,
    ) -> ApiResponse:
        items = self._project_submissions()
        filtered = []
        for item in items:
            if process_cd and item["process_cd"] != process_cd:
                continue
            if submission_period_cd and item["submission_period_cd"] != submission_period_cd:
                continue
            if state and item["state"] != state:
                continue
            if created_by and item["created_by"] != created_by:
                continue
            filtered.append(item)
        self._write_projection_file(items)
        return self._ok(filtered)

    def list_runs(
        self,
        submission_id: str | None = None,
        state: str | None = None,
        started_by: str | None = None,
    ) -> ApiResponse:
        items = self._project_runs()
        filtered = []
        for item in items:
            if submission_id and item["submission_id"] != submission_id:
                continue
            if state and item["state"] != state:
                continue
            if started_by and item["started_by"] != started_by:
                continue
            filtered.append(item)
        return self._ok(filtered)

    def list_submission_history(
        self,
        submission_id: str,
        actor: str | None = None,
        include_run_events: bool = False,
    ) -> ApiResponse:
        projection = self._get_submission_projection(submission_id)
        if projection is None:
            return self._error("SUBMISSION_NOT_FOUND", "Submission not found.")

        items = []
        for event in sorted(self._events.list_all(), key=lambda item: (item.event_ts_utc, item.event_id)):
            if event.submission_id != submission_id:
                continue
            if actor and event.actor != actor:
                continue
            if not include_run_events and event.event_type.startswith("validation_run_"):
                continue
            items.append(self._submission_history_entry(event))
        return self._ok(items)

    def list_edit_history(
        self,
        submission_id: str,
        actor: str | None = None,
    ) -> ApiResponse:
        projection = self._get_submission_projection(submission_id)
        if projection is None:
            return self._error("SUBMISSION_NOT_FOUND", "Submission not found.")

        items = []
        for event in sorted(self._events.list_all(), key=lambda item: (item.event_ts_utc, item.event_id)):
            if event.submission_id != submission_id:
                continue
            if actor and event.actor != actor:
                continue
            if event.event_type not in SUBMISSION_EDIT_EVENT_TYPES:
                continue
            items.append(self._submission_history_entry(event))
        return self._ok(items)

    def create_submission(
        self,
        process_cd: str,
        submission_period_cd: str,
        organisations: dict[str, dict[str, Any] | OrganisationSubmissionRef | SharePointSourceRef],
        templates: dict[str, dict[str, Any] | TemplateSubmissionRef] | None,
        created_by: str,
        idempotency_key: str,
        note: str | None = None,
        allow_duplicate_active: bool = False,
    ) -> ApiResponse:
        payload = {
            "process_cd": process_cd,
            "submission_period_cd": submission_period_cd,
            "organisations": {
                key: (
                    value.to_dict()
                    if isinstance(value, (OrganisationSubmissionRef, SharePointSourceRef))
                    else value
                )
                for key, value in organisations.items()
            },
            "templates": {
                key: value.to_dict() if isinstance(value, TemplateSubmissionRef) else value
                for key, value in (templates or {}).items()
            },
            "note": note,
            "allow_duplicate_active": allow_duplicate_active,
        }
        idempotent = self._handle_idempotency("create_submission", created_by, idempotency_key, payload)
        if idempotent is not None:
            return idempotent

        process_cd = process_cd.strip()
        submission_period_cd = submission_period_cd.strip()
        cleaned_organisations = {
            key.strip().upper(): self._normalize_organisation_ref(value)
            for key, value in organisations.items()
        }
        cleaned_templates = {
            key.strip().upper(): self._normalize_template_ref(value)
            for key, value in (templates or {}).items()
        }
        if not process_cd or not submission_period_cd:
            response = self._error("VALIDATION_ERROR", "process_cd and submission_period_cd are required.")
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
            return response
        if not cleaned_organisations:
            response = self._error("VALIDATION_ERROR", "organisations must not be empty.")
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
            return response
        invalid_orgs = [key for key, item in cleaned_organisations.items() if not self._is_valid_organisation_ref(item)]
        if invalid_orgs:
            response = self._error(
                "VALIDATION_ERROR",
                f"organisations contains invalid source refs for: {', '.join(invalid_orgs)}.",
            )
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
            return response
        invalid_templates = [key for key, item in cleaned_templates.items() if not self._is_valid_template_ref(item)]
        if invalid_templates:
            response = self._error(
                "VALIDATION_ERROR",
                f"templates contains invalid source refs for: {', '.join(invalid_templates)}.",
            )
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
            return response
        template_validation_error = self._validate_template_links(cleaned_organisations, cleaned_templates)
        if template_validation_error is not None:
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, template_validation_error)
            return template_validation_error

        if not allow_duplicate_active:
            for item in self._project_submissions():
                if (
                    item["process_cd"] == process_cd
                    and item["submission_period_cd"] == submission_period_cd
                    and item["state"] not in TERMINAL_STATES
                ):
                    response = self._error(
                        "DUPLICATE_ACTIVE_SUBMISSION",
                        "Active submission already exists for process_cd/submission_period_cd.",
                        details={"submission_id": item["submission_id"]},
                    )
                    self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
                    return response

        submission_id = self._id_fn()
        now_iso = self._utc_now_iso()
        for org_cd, org_ref in cleaned_organisations.items():
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="submission_created",
                submission_id=submission_id,
                process_cd=process_cd,
                submission_period_cd=submission_period_cd,
                organisation_cd=org_cd,
                sharepoint_source=org_ref.sharepoint_source.to_dict(),
                validation_flag=VALIDATION_NOT_VALIDATED,
                actor=created_by,
                payload_json={
                    **({"note": note.strip()} if note and note.strip() else {}),
                    "template_keys": org_ref.template_keys,
                }
                or None,
            )
            self._events.append(event)

        for template_key, template_ref in cleaned_templates.items():
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="submission_template_upserted",
                submission_id=submission_id,
                process_cd=process_cd,
                submission_period_cd=submission_period_cd,
                organisation_cd=None,
                sharepoint_source=template_ref.sharepoint_source.to_dict(),
                validation_flag=VALIDATION_NOT_VALIDATED,
                actor=created_by,
                payload_json={
                    "template_key": template_key,
                    "assignment_mode": template_ref.assignment_mode,
                    "applies_to": template_ref.applies_to,
                },
            )
            self._events.append(event)

        created = self._get_submission_projection(submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._ok(created)
        self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
        return response

    def set_validation_flags(
        self,
        submission_id: str,
        organisation_changes: dict[str, str] | None,
        template_changes: dict[str, str] | None,
        modified_by: str,
        idempotency_key: str,
        reason: str | None = None,
    ) -> ApiResponse:
        payload = {
            "submission_id": submission_id,
            "organisation_changes": organisation_changes or {},
            "template_changes": template_changes or {},
            "reason": reason,
        }
        idempotent = self._handle_idempotency(
            "set_validation_flags", modified_by, idempotency_key, payload
        )
        if idempotent is not None:
            return idempotent

        projection = self._get_submission_projection(submission_id)
        if projection is None:
            response = self._error("SUBMISSION_NOT_FOUND", "Submission not found.")
            self._store_idempotency(
                "set_validation_flags",
                modified_by,
                idempotency_key,
                payload,
                response,
            )
            return response

        known_orgs = set(projection["organisations"].keys())
        known_templates = set(projection.get("templates", {}).keys())
        now_iso = self._utc_now_iso()
        for org, flag in (organisation_changes or {}).items():
            org_cd = org.strip().upper()
            if org_cd not in known_orgs:
                response = self._error(
                    "ORG_NOT_IN_SUBMISSION",
                    f"Organisation '{org_cd}' does not exist in submission.",
                )
                self._store_idempotency(
                    "set_validation_flags",
                    modified_by,
                    idempotency_key,
                    payload,
                    response,
                )
                return response
            if flag not in {VALIDATION_VALIDATED, VALIDATION_NOT_VALIDATED}:
                response = self._error("VALIDATION_ERROR", f"Unsupported flag '{flag}' for '{org_cd}'.")
                self._store_idempotency(
                    "set_validation_flags",
                    modified_by,
                    idempotency_key,
                    payload,
                    response,
                )
                return response

        for template_key, flag in (template_changes or {}).items():
            normalized_template_key = template_key.strip().upper()
            if normalized_template_key not in known_templates:
                response = self._error(
                    "TEMPLATE_NOT_IN_SUBMISSION",
                    f"Template '{normalized_template_key}' does not exist in submission.",
                )
                self._store_idempotency(
                    "set_validation_flags",
                    modified_by,
                    idempotency_key,
                    payload,
                    response,
                )
                return response
            if flag not in {VALIDATION_VALIDATED, VALIDATION_NOT_VALIDATED}:
                response = self._error("VALIDATION_ERROR", f"Unsupported flag '{flag}' for '{normalized_template_key}'.")
                self._store_idempotency(
                    "set_validation_flags",
                    modified_by,
                    idempotency_key,
                    payload,
                    response,
                )
                return response

        for org, flag in (organisation_changes or {}).items():
            org_cd = org.strip().upper()
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="org_flag_changed",
                submission_id=submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=org_cd,
                sharepoint_source=self._with_validation_hashes(
                    projection["organisations"][org_cd]["file"],
                    flag,
                ),
                validation_flag=flag,
                actor=modified_by,
                reason=reason,
            )
            self._events.append(event)

        for template_key, flag in (template_changes or {}).items():
            normalized_template_key = template_key.strip().upper()
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="template_flag_changed",
                submission_id=submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=None,
                sharepoint_source=self._with_validation_hashes(
                    projection["templates"][normalized_template_key]["file"],
                    flag,
                ),
                validation_flag=flag,
                actor=modified_by,
                reason=reason,
                payload_json={"template_key": normalized_template_key},
            )
            self._events.append(event)

        updated = self._get_submission_projection(submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._ok(updated)
        self._store_idempotency(
            "set_validation_flags",
            modified_by,
            idempotency_key,
            payload,
            response,
        )
        return response

    def set_organisation_validation_flags(
        self,
        submission_id: str,
        changes: dict[str, str],
        modified_by: str,
        idempotency_key: str,
        reason: str | None = None,
    ) -> ApiResponse:
        return self.set_validation_flags(
            submission_id=submission_id,
            organisation_changes=changes,
            template_changes=None,
            modified_by=modified_by,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def set_template_validation_flags(
        self,
        submission_id: str,
        changes: dict[str, str],
        modified_by: str,
        idempotency_key: str,
        reason: str | None = None,
    ) -> ApiResponse:
        return self.set_validation_flags(
            submission_id=submission_id,
            organisation_changes=None,
            template_changes=changes,
            modified_by=modified_by,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def refresh_submission_hashes(
        self,
        submission_id: str,
        refreshed_by: str,
        idempotency_key: str,
    ) -> ApiResponse:
        payload = {"submission_id": submission_id}
        idempotent = self._handle_idempotency(
            "refresh_submission_hashes",
            refreshed_by,
            idempotency_key,
            payload,
        )
        if idempotent is not None:
            return idempotent

        if self._sharepoint_hash_resolver is None:
            response = self._error(
                "SHAREPOINT_HASH_RESOLVER_NOT_CONFIGURED",
                "SharePoint hash resolver is not configured for this service instance.",
            )
            self._store_idempotency("refresh_submission_hashes", refreshed_by, idempotency_key, payload, response)
            return response

        projection = self._get_submission_projection(submission_id)
        if projection is None:
            response = self._error("SUBMISSION_NOT_FOUND", "Submission not found.")
            self._store_idempotency("refresh_submission_hashes", refreshed_by, idempotency_key, payload, response)
            return response

        now_iso = self._utc_now_iso()
        for org_cd, org_payload in projection["organisations"].items():
            refreshed_source = self._sharepoint_hash_resolver(org_payload["file"])
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="sharepoint_hashes_refreshed",
                submission_id=submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=org_cd,
                sharepoint_source=refreshed_source,
                validation_flag=org_payload.get("company_validation_flag"),
                actor=refreshed_by,
                payload_json={"template_keys": org_payload.get("template_keys", [])},
            )
            self._events.append(event)

        for template_key, template_payload in projection.get("templates", {}).items():
            refreshed_source = self._sharepoint_hash_resolver(template_payload["file"])
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="template_hashes_refreshed",
                submission_id=submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=None,
                sharepoint_source=refreshed_source,
                validation_flag=template_payload.get("validation_flag"),
                actor=refreshed_by,
                payload_json={"template_key": template_key},
            )
            self._events.append(event)

        updated = self._get_submission_projection(submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._ok(updated)
        self._store_idempotency("refresh_submission_hashes", refreshed_by, idempotency_key, payload, response)
        return response

    def edit_submission(
        self,
        submission_id: str,
        modified_by: str,
        idempotency_key: str,
        organisation_upserts: dict[str, dict[str, Any] | OrganisationSubmissionRef | SharePointSourceRef] | None = None,
        organisation_removals: list[str] | None = None,
        template_upserts: dict[str, dict[str, Any] | TemplateSubmissionRef] | None = None,
        template_removals: list[str] | None = None,
        note: str | None = None,
        reason: str | None = None,
    ) -> ApiResponse:
        payload = {
            "submission_id": submission_id,
            "organisation_upserts": {
                key: (
                    value.to_dict()
                    if isinstance(value, (OrganisationSubmissionRef, SharePointSourceRef))
                    else value
                )
                for key, value in (organisation_upserts or {}).items()
            },
            "organisation_removals": organisation_removals or [],
            "template_upserts": {
                key: value.to_dict() if isinstance(value, TemplateSubmissionRef) else value
                for key, value in (template_upserts or {}).items()
            },
            "template_removals": template_removals or [],
            "note": note,
            "reason": reason,
        }
        idempotent = self._handle_idempotency("edit_submission", modified_by, idempotency_key, payload)
        if idempotent is not None:
            return idempotent

        projection = self._get_submission_projection(submission_id)
        if projection is None:
            response = self._error("SUBMISSION_NOT_FOUND", "Submission not found.")
            self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
            return response

        current_orgs = projection["organisations"]
        current_templates = projection.get("templates", {})
        removals = [item.strip().upper() for item in (organisation_removals or []) if item and item.strip()]
        unknown_removals = [org_cd for org_cd in removals if org_cd not in current_orgs]
        if unknown_removals:
            response = self._error(
                "ORG_NOT_IN_SUBMISSION",
                f"Organisation(s) not found in submission: {', '.join(unknown_removals)}.",
            )
            self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
            return response

        normalized_upserts = {
            key.strip().upper(): self._normalize_organisation_ref(value)
            for key, value in (organisation_upserts or {}).items()
        }
        invalid_sources = [key for key, source in normalized_upserts.items() if not self._is_valid_organisation_ref(source)]
        if invalid_sources:
            response = self._error(
                "VALIDATION_ERROR",
                f"organisation_upserts contains invalid refs for: {', '.join(invalid_sources)}.",
            )
            self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
            return response

        template_removal_keys = [item.strip().upper() for item in (template_removals or []) if item and item.strip()]
        unknown_template_removals = [key for key in template_removal_keys if key not in current_templates]
        if unknown_template_removals:
            response = self._error(
                "TEMPLATE_NOT_IN_SUBMISSION",
                f"Template(s) not found in submission: {', '.join(unknown_template_removals)}.",
            )
            self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
            return response

        normalized_template_upserts = {
            key.strip().upper(): self._normalize_template_ref(value)
            for key, value in (template_upserts or {}).items()
        }
        invalid_templates = [key for key, source in normalized_template_upserts.items() if not self._is_valid_template_ref(source)]
        if invalid_templates:
            response = self._error(
                "VALIDATION_ERROR",
                f"template_upserts contains invalid refs for: {', '.join(invalid_templates)}.",
            )
            self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
            return response

        template_validation_error = self._validate_template_links(
            {**{k: self._organisation_ref_from_projection(v) for k, v in current_orgs.items()}, **normalized_upserts},
            {**{k: self._template_ref_from_projection(v) for k, v in current_templates.items()}, **normalized_template_upserts},
            removed_template_keys=template_removal_keys,
        )
        if template_validation_error is not None:
            self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, template_validation_error)
            return template_validation_error

        for org_cd in removals:
            if org_cd in normalized_upserts:
                response = self._error(
                    "VALIDATION_ERROR",
                    f"Organisation '{org_cd}' cannot be removed and upserted in the same edit.",
                )
                self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
                return response
        for template_key in template_removal_keys:
            if template_key in normalized_template_upserts:
                response = self._error(
                    "VALIDATION_ERROR",
                    f"Template '{template_key}' cannot be removed and upserted in the same edit.",
                )
                self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
                return response

        now_iso = self._utc_now_iso()
        if note is not None:
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_note_updated",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=None,
                    validation_flag=None,
                    actor=modified_by,
                    reason=reason,
                    payload_json={"note": note.strip() if note.strip() else None},
                )
            )

        for org_cd in removals:
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_org_removed",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=org_cd,
                    sharepoint_source=None,
                    validation_flag=None,
                    actor=modified_by,
                    reason=reason,
                )
            )

        for org_cd, source in normalized_upserts.items():
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_org_upserted",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=org_cd,
                    sharepoint_source=source.sharepoint_source.to_dict(),
                    validation_flag=VALIDATION_NOT_VALIDATED,
                    actor=modified_by,
                    reason=reason,
                    payload_json={"template_keys": source.template_keys},
                )
            )

        for template_key in template_removal_keys:
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_template_removed",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=None,
                    validation_flag=None,
                    actor=modified_by,
                    reason=reason,
                    payload_json={"template_key": template_key},
                )
            )

        for template_key, template_ref in normalized_template_upserts.items():
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_template_upserted",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=template_ref.sharepoint_source.to_dict(),
                    validation_flag=VALIDATION_NOT_VALIDATED,
                    actor=modified_by,
                    reason=reason,
                    payload_json={
                        "template_key": template_key,
                        "assignment_mode": template_ref.assignment_mode,
                        "applies_to": template_ref.applies_to,
                    },
                )
            )

        updated = self._get_submission_projection(submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._ok(updated)
        self._store_idempotency("edit_submission", modified_by, idempotency_key, payload, response)
        return response

    def start_validation_run(
        self,
        submission_id: str,
        requested_org_files: dict[str, list[str] | None] | None,
        started_by: str,
        idempotency_key: str,
        refresh_hashes: bool = True,
    ) -> ApiResponse:
        payload = {
            "submission_id": submission_id,
            "requested_org_files": requested_org_files,
            "refresh_hashes": refresh_hashes,
        }
        idempotent = self._handle_idempotency("start_validation_run", started_by, idempotency_key, payload)
        if idempotent is not None:
            return idempotent

        if self._run_file_stager is None:
            response = self._error(
                "RUN_FILE_STAGER_NOT_CONFIGURED",
                "Run file stager is not configured for this service instance.",
            )
            self._store_idempotency("start_validation_run", started_by, idempotency_key, payload, response)
            return response

        projection = self._get_submission_projection(submission_id)
        if projection is None:
            response = self._error("SUBMISSION_NOT_FOUND", "Submission not found.")
            self._store_idempotency("start_validation_run", started_by, idempotency_key, payload, response)
            return response

        if refresh_hashes:
            projection = self._refresh_submission_projection(submission_id, started_by)

        normalized_request = self._normalize_requested_org_files(
            requested_org_files,
            projection["organisations"],
        )
        if isinstance(normalized_request, ApiResponse):
            self._store_idempotency("start_validation_run", started_by, idempotency_key, payload, normalized_request)
            return normalized_request

        run_id = self._id_fn()
        now_iso = self._utc_now_iso()
        self._events.append(
            SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="validation_run_started",
                submission_id=submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=None,
                sharepoint_source=None,
                validation_flag=None,
                actor=started_by,
                payload_json={
                    "run_id": run_id,
                    "requested_org_files": normalized_request,
                },
            )
        )

        for org_cd, requested_files in normalized_request.items():
            source_payload = projection["organisations"][org_cd]["file"]
            source = SharePointSourceRef.from_dict(source_payload)
            tracked_by_path: dict[str, dict[str, Any]] = {
                item.path: {**item.to_dict(), "asset_kind": "organisation", "asset_key": org_cd}
                for item in source.tracked_files
            }
            for template_key in projection["organisations"][org_cd].get("template_keys", []):
                template_payload = projection.get("templates", {}).get(template_key)
                if not template_payload:
                    continue
                template_source = SharePointSourceRef.from_dict(template_payload["file"])
                for item in template_source.tracked_files:
                    tracked_by_path[f"TEMPLATE::{template_key}::{item.path}"] = {
                        **item.to_dict(),
                        "path": item.path,
                        "asset_kind": "template",
                        "asset_key": template_key,
                        "source_payload": template_payload["file"],
                    }
            selected_paths = requested_files or list(tracked_by_path.keys())
            missing_requested = [path for path in selected_paths if path not in tracked_by_path]
            if missing_requested:
                self._append_run_failure_event(
                    submission_id=submission_id,
                    projection=projection,
                    run_id=run_id,
                    org_cd=org_cd,
                    actor=started_by,
                    error_code="TRACKED_FILE_NOT_IN_SUBMISSION",
                    message=f"Tracked files not found in submission source: {', '.join(missing_requested)}.",
                    selected_files=[{"path": path} for path in selected_paths],
                )
                continue

            selected_files = [tracked_by_path[path] for path in selected_paths]
            unavailable_files = [item["path"] for item in selected_files if not item.get("current_hash")]
            if unavailable_files:
                self._append_run_failure_event(
                    submission_id=submission_id,
                    projection=projection,
                    run_id=run_id,
                    org_cd=org_cd,
                    actor=started_by,
                    error_code="TRACKED_FILE_MISSING",
                    message=f"Tracked files are missing in SharePoint: {', '.join(unavailable_files)}.",
                    selected_files=selected_files,
                )
                continue

            staged_files: list[dict[str, Any]] = []
            staging_error: dict[str, Any] | None = None
            for tracked_file in selected_files:
                try:
                    source_for_file = tracked_file.get("source_payload") or source_payload
                    staged_files.append(self._run_file_stager(source_for_file, tracked_file, run_id, org_cd))
                except Exception as exc:
                    staging_error = {
                        "code": "FILE_STAGING_FAILED",
                        "message": str(exc),
                    }
                    break

            if staging_error:
                self._append_run_failure_event(
                    submission_id=submission_id,
                    projection=projection,
                    run_id=run_id,
                    org_cd=org_cd,
                    actor=started_by,
                    error_code=staging_error["code"],
                    message=staging_error["message"],
                    selected_files=selected_files,
                )
                continue

            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="validation_run_org_staged",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=org_cd,
                    sharepoint_source=None,
                    validation_flag=None,
                    actor=started_by,
                    payload_json={
                        "run_id": run_id,
                        "files": staged_files,
                    },
                )
            )

        run_projection = self._get_run_projection(run_id)
        self._write_projection_file(self._project_submissions())
        response = self._ok(run_projection)
        self._store_idempotency("start_validation_run", started_by, idempotency_key, payload, response)
        return response

    def _project_submissions(self) -> list[dict[str, Any]]:
        by_submission: dict[str, dict[str, Any]] = {}
        for event in sorted(self._events.list_all(), key=lambda e: (e.event_ts_utc, e.event_id)):
            submission = by_submission.get(event.submission_id)
            if not submission:
                submission = {
                    "submission_id": event.submission_id,
                    "process_cd": event.process_cd,
                    "submission_period_cd": event.submission_period_cd,
                    "created_by": event.actor,
                    "created_ts_utc": event.event_ts_utc,
                    "last_modified_by": event.actor,
                    "last_modified_ts_utc": event.event_ts_utc,
                    "note": None,
                    "organisations": {},
                    "templates": {},
                    "state": "ready",
                }
                by_submission[event.submission_id] = submission

            if event.event_type.startswith("validation_run_"):
                continue

            submission["last_modified_by"] = event.actor
            submission["last_modified_ts_utc"] = event.event_ts_utc

            if event.payload_json and "note" in event.payload_json:
                submission["note"] = event.payload_json.get("note")

            if event.event_type in {"submission_template_upserted", "template_flag_changed", "template_hashes_refreshed"}:
                template_key = str((event.payload_json or {}).get("template_key", "")).strip().upper()
                if template_key:
                    template_entry = submission["templates"].get(template_key, {})
                    if event.sharepoint_source is not None:
                        template_entry["file"] = event.sharepoint_source
                    if event.validation_flag is not None:
                        template_entry["validation_flag"] = event.validation_flag
                    template_entry["assignment_mode"] = str((event.payload_json or {}).get("assignment_mode") or template_entry.get("assignment_mode") or "shared")
                    template_entry["applies_to"] = [
                        str(item).strip().upper()
                        for item in ((event.payload_json or {}).get("applies_to") or template_entry.get("applies_to") or [])
                        if str(item).strip()
                    ]
                    template_entry["template_key"] = template_key
                    submission["templates"][template_key] = template_entry
            elif event.event_type == "submission_template_removed":
                template_key = str((event.payload_json or {}).get("template_key", "")).strip().upper()
                if template_key:
                    submission["templates"].pop(template_key, None)
                    for org_entry in submission["organisations"].values():
                        org_entry["template_keys"] = [key for key in org_entry.get("template_keys", []) if key != template_key]

            if event.organisation_cd:
                org_entry = submission["organisations"].get(event.organisation_cd, {})
                if event.event_type == "submission_org_removed":
                    submission["organisations"].pop(event.organisation_cd, None)
                    submission["state"] = self._compute_submission_state(submission)
                    continue
                if event.sharepoint_source is not None:
                    org_entry["file"] = event.sharepoint_source
                if event.validation_flag is not None:
                    org_entry["company_validation_flag"] = event.validation_flag
                if event.payload_json and "template_keys" in event.payload_json:
                    org_entry["template_keys"] = [
                        str(item).strip().upper()
                        for item in event.payload_json.get("template_keys", [])
                        if str(item).strip()
                    ]
                org_entry["validation_flag"] = self._compute_organisation_state(
                    org_entry,
                    submission["templates"],
                )
                submission["organisations"][event.organisation_cd] = org_entry
            else:
                for org_entry in submission["organisations"].values():
                    org_entry["validation_flag"] = self._compute_organisation_state(
                        org_entry,
                        submission["templates"],
                    )

            submission["state"] = self._compute_submission_state(submission)

        return list(by_submission.values())

    def _project_runs(self) -> list[dict[str, Any]]:
        by_run: dict[str, dict[str, Any]] = {}
        for event in sorted(self._events.list_all(), key=lambda e: (e.event_ts_utc, e.event_id)):
            if not event.event_type.startswith("validation_run_"):
                continue
            payload = event.payload_json or {}
            run_id = payload.get("run_id")
            if not run_id:
                continue
            run = by_run.get(run_id)
            if not run:
                run = {
                    "run_id": run_id,
                    "submission_id": event.submission_id,
                    "process_cd": event.process_cd,
                    "submission_period_cd": event.submission_period_cd,
                    "started_by": event.actor,
                    "started_ts_utc": event.event_ts_utc,
                    "requested_org_files": payload.get("requested_org_files", {}),
                    "organisations": {},
                    "state": "queued",
                }
                by_run[run_id] = run

            if event.event_type == "validation_run_started":
                continue

            org_cd = event.organisation_cd
            if not org_cd:
                continue
            org_entry = run["organisations"].get(org_cd, {})
            if event.event_type == "validation_run_org_staged":
                org_entry["status"] = "staged"
                org_entry["files"] = payload.get("files", [])
            elif event.event_type == "validation_run_org_failed":
                org_entry["status"] = "failed"
                org_entry["files"] = payload.get("files", [])
                org_entry["error"] = payload.get("error")
            run["organisations"][org_cd] = org_entry
            run["state"] = self._compute_run_state(run)

        return list(by_run.values())

    @staticmethod
    def _compute_submission_state(submission: dict[str, Any]) -> str:
        flags = [
            org.get("validation_flag", VALIDATION_NOT_VALIDATED)
            for org in submission["organisations"].values()
        ]
        flags.extend(
            template.get("validation_flag", VALIDATION_NOT_VALIDATED)
            for template in submission.get("templates", {}).values()
        )
        if not flags:
            return "ready"
        if all(flag == VALIDATION_VALIDATED for flag in flags):
            return VALIDATION_VALIDATED
        if any(flag == VALIDATION_VALIDATED for flag in flags):
            return "partially_validated"
        return "ready"

    @staticmethod
    def _compute_organisation_state(org_entry: dict[str, Any], templates: dict[str, Any]) -> str:
        company_flag = org_entry.get("company_validation_flag", VALIDATION_NOT_VALIDATED)
        template_flags = [
            templates[key].get("validation_flag", VALIDATION_NOT_VALIDATED)
            for key in org_entry.get("template_keys", [])
            if key in templates
        ]
        flags = [company_flag, *template_flags]
        if all(flag == VALIDATION_VALIDATED for flag in flags):
            return VALIDATION_VALIDATED
        if any(flag == VALIDATION_VALIDATED for flag in flags):
            return "partially_validated"
        return VALIDATION_NOT_VALIDATED

    def _get_submission_projection(self, submission_id: str) -> dict[str, Any] | None:
        for item in self._project_submissions():
            if item["submission_id"] == submission_id:
                return item
        return None

    def _get_run_projection(self, run_id: str) -> dict[str, Any] | None:
        for item in self._project_runs():
            if item["run_id"] == run_id:
                return item
        return None

    def _utc_now_iso(self) -> str:
        return self._now_fn().astimezone(timezone.utc).isoformat()

    def _write_projection_file(self, items: list[dict[str, Any]]):
        if not self._submissions_projection_store:
            return
        payload = {
            "updated_ts_utc": self._utc_now_iso(),
            "items": items,
        }
        if self._projection_metadata_provider is not None:
            payload["storage_metadata"] = self._projection_metadata_provider()
        self._submissions_projection_store.write(payload)

    def _refresh_submission_projection(self, submission_id: str, actor: str) -> dict[str, Any]:
        projection = self._get_submission_projection(submission_id)
        if projection is None:
            raise ValueError("Submission not found.")
        if self._sharepoint_hash_resolver is None:
            return projection
        now_iso = self._utc_now_iso()
        for org_cd, org_payload in projection["organisations"].items():
            refreshed_source = self._sharepoint_hash_resolver(org_payload["file"])
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="sharepoint_hashes_refreshed",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=org_cd,
                    sharepoint_source=refreshed_source,
                    validation_flag=org_payload.get("company_validation_flag"),
                    actor=actor,
                    payload_json={"template_keys": org_payload.get("template_keys", [])},
                )
            )
        for template_key, template_payload in projection.get("templates", {}).items():
            refreshed_source = self._sharepoint_hash_resolver(template_payload["file"])
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="template_hashes_refreshed",
                    submission_id=submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=refreshed_source,
                    validation_flag=template_payload.get("validation_flag"),
                    actor=actor,
                    payload_json={"template_key": template_key},
                )
            )
        return self._get_submission_projection(submission_id) or projection

    def _append_run_failure_event(
        self,
        submission_id: str,
        projection: dict[str, Any],
        run_id: str,
        org_cd: str,
        actor: str,
        error_code: str,
        message: str,
        selected_files: list[dict[str, Any]],
    ):
        self._events.append(
            SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=self._utc_now_iso(),
                event_type="validation_run_org_failed",
                submission_id=submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=org_cd,
                sharepoint_source=None,
                validation_flag=None,
                actor=actor,
                payload_json={
                    "run_id": run_id,
                    "files": selected_files,
                    "error": {
                        "code": error_code,
                        "message": message,
                    },
                },
            )
        )

    @staticmethod
    def _normalize_requested_org_files(
        requested_org_files: dict[str, list[str] | None] | None,
        organisations: dict[str, Any],
    ) -> dict[str, list[str] | None] | ApiResponse:
        if requested_org_files is None:
            return {org_cd: None for org_cd in organisations}

        normalized: dict[str, list[str] | None] = {}
        known_orgs = set(organisations.keys())
        for raw_org, raw_files in requested_org_files.items():
            org_cd = raw_org.strip().upper()
            if org_cd not in known_orgs:
                return ApiResponse(
                    ok=False,
                    data=None,
                    error=ApiError(
                        code="ORG_NOT_IN_SUBMISSION",
                        message=f"Organisation '{org_cd}' does not exist in submission.",
                    ),
                    correlation_id=uuid4().hex,
                )
            if raw_files is None:
                normalized[org_cd] = None
            else:
                normalized[org_cd] = [str(item).strip() for item in raw_files if str(item).strip()]
        return normalized

    @staticmethod
    def _compute_run_state(run: dict[str, Any]) -> str:
        statuses = [org.get("status") for org in run["organisations"].values() if org.get("status")]
        if not statuses:
            return "queued"
        if all(status == "staged" for status in statuses):
            return "staged"
        if any(status == "staged" for status in statuses):
            return "partially_failed"
        return "failed"

    @staticmethod
    def _normalize_source_ref(source: dict[str, Any] | SharePointSourceRef) -> SharePointSourceRef:
        if isinstance(source, SharePointSourceRef):
            normalized = source
        else:
            normalized = SharePointSourceRef.from_dict(source)
        return SharePointSourceRef(
            source_type=normalized.source_type.strip().lower(),
            target_name=normalized.target_name.strip(),
            root_path=normalized.root_path.strip(),
            tracked_files=[
                TrackedFileRef(
                    path=item.path.strip() if item.path else None,
                    watch=bool(item.watch),
                    watch_search_term=item.watch_search_term.strip() if item.watch_search_term else None,
                    current_hash=item.current_hash.strip() if item.current_hash else None,
                    validated_hash=item.validated_hash.strip() if item.validated_hash else None,
                    size_bytes=item.size_bytes,
                    created=item.created.strip() if item.created else None,
                    modified=item.modified.strip() if item.modified else None,
                    modified_by=item.modified_by.strip() if item.modified_by else None,
                )
                for item in normalized.tracked_files
                if (item.path and item.path.strip()) or item.watch
            ],
            folder_url=normalized.folder_url.strip() if normalized.folder_url else None,
            drive_id=normalized.drive_id.strip() if normalized.drive_id else None,
            linked_item_id=normalized.linked_item_id.strip() if normalized.linked_item_id else None,
            linked_item_name=normalized.linked_item_name.strip() if normalized.linked_item_name else None,
            linked_item_is_folder=normalized.linked_item_is_folder,
        )

    @staticmethod
    def _is_valid_source_ref(source: SharePointSourceRef) -> bool:
        return (
            source.source_type in {"drive", "group"}
            and bool(source.target_name)
            and bool(source.root_path)
            and bool(source.tracked_files)
            and all(
                (item.path and item.path.strip()) or (item.watch and item.watch_search_term and item.watch_search_term.strip())
                for item in source.tracked_files
            )
        )

    @classmethod
    def _normalize_organisation_ref(
        cls,
        source: dict[str, Any] | OrganisationSubmissionRef | SharePointSourceRef,
    ) -> OrganisationSubmissionRef:
        normalized = OrganisationSubmissionRef.from_dict(source)
        return OrganisationSubmissionRef(
            sharepoint_source=cls._normalize_source_ref(normalized.sharepoint_source),
            template_keys=[item.strip().upper() for item in normalized.template_keys if item.strip()],
        )

    @classmethod
    def _normalize_template_ref(
        cls,
        template: dict[str, Any] | TemplateSubmissionRef,
    ) -> TemplateSubmissionRef:
        normalized = TemplateSubmissionRef.from_dict(template if isinstance(template, dict) else template.to_dict())
        return TemplateSubmissionRef(
            assignment_mode=normalized.assignment_mode.strip().lower(),
            applies_to=[item.strip().upper() for item in normalized.applies_to if item.strip()],
            sharepoint_source=cls._normalize_source_ref(normalized.sharepoint_source),
        )

    @classmethod
    def _is_valid_organisation_ref(cls, source: OrganisationSubmissionRef) -> bool:
        return cls._is_valid_source_ref(source.sharepoint_source)

    @classmethod
    def _is_valid_template_ref(cls, template: TemplateSubmissionRef) -> bool:
        return (
            template.assignment_mode in {"shared", "per_organisation"}
            and bool(template.applies_to)
            and cls._is_valid_source_ref(template.sharepoint_source)
        )

    @staticmethod
    def _template_ref_from_projection(payload: dict[str, Any]) -> TemplateSubmissionRef:
        return TemplateSubmissionRef(
            assignment_mode=str(payload.get("assignment_mode") or "shared").strip().lower(),
            applies_to=[str(item).strip().upper() for item in payload.get("applies_to", []) if str(item).strip()],
            sharepoint_source=SharePointSourceRef.from_dict(payload["file"]),
        )

    @staticmethod
    def _organisation_ref_from_projection(payload: dict[str, Any]) -> OrganisationSubmissionRef:
        return OrganisationSubmissionRef(
            sharepoint_source=SharePointSourceRef.from_dict(payload["file"]),
            template_keys=[str(item).strip().upper() for item in payload.get("template_keys", []) if str(item).strip()],
        )

    def _validate_template_links(
        self,
        organisations: dict[str, OrganisationSubmissionRef],
        templates: dict[str, TemplateSubmissionRef],
        removed_template_keys: list[str] | None = None,
    ) -> ApiResponse | None:
        removed = set(removed_template_keys or [])
        available_templates = set(templates.keys()) - removed
        known_orgs = set(organisations.keys())
        for org_cd, org_ref in organisations.items():
            unknown_template_keys = [key for key in org_ref.template_keys if key not in available_templates]
            if unknown_template_keys:
                return self._error(
                    "VALIDATION_ERROR",
                    f"Organisation '{org_cd}' references unknown template(s): {', '.join(unknown_template_keys)}.",
                )
        for template_key, template_ref in templates.items():
            unknown_orgs = [org_cd for org_cd in template_ref.applies_to if org_cd not in known_orgs]
            if unknown_orgs:
                return self._error(
                    "VALIDATION_ERROR",
                    f"Template '{template_key}' references unknown organisation(s): {', '.join(unknown_orgs)}.",
                )
        return None

    @staticmethod
    def _with_validation_hashes(source_payload: dict[str, Any], validation_flag: str) -> dict[str, Any]:
        source = SharePointSourceRef.from_dict(source_payload)
        tracked_files = [
            TrackedFileRef(
                path=item.path,
                watch=item.watch,
                watch_search_term=item.watch_search_term,
                current_hash=item.current_hash,
                validated_hash=item.current_hash if validation_flag == VALIDATION_VALIDATED else None,
                size_bytes=item.size_bytes,
                created=item.created,
                modified=item.modified,
                modified_by=item.modified_by,
            )
            for item in source.tracked_files
        ]
        return SharePointSourceRef(
            source_type=source.source_type,
            target_name=source.target_name,
            root_path=source.root_path,
            tracked_files=tracked_files,
            folder_url=source.folder_url,
        ).to_dict()

    @staticmethod
    def _hash_payload(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _handle_idempotency(
        self,
        operation: str,
        actor: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> ApiResponse | None:
        payload_hash = self._hash_payload(payload)
        entry = self._idempotency.get(operation, actor, idempotency_key)
        if not entry:
            return None
        cached_hash, cached_response = entry
        if cached_hash != payload_hash:
            return self._error(
                "IDEMPOTENCY_CONFLICT",
                "Idempotency key was already used with a different payload.",
            )
        return cached_response

    def _store_idempotency(
        self,
        operation: str,
        actor: str,
        idempotency_key: str,
        payload: dict[str, Any],
        response: ApiResponse,
    ):
        self._idempotency.set(operation, actor, idempotency_key, self._hash_payload(payload), response)

    def _ok(self, data: dict[str, Any] | list[dict[str, Any]]) -> ApiResponse:
        return ApiResponse(ok=True, data=data, error=None, correlation_id=self._id_fn())

    def _error(self, code: str, message: str, details: dict[str, Any] | None = None) -> ApiResponse:
        return ApiResponse(
            ok=False,
            data=None,
            error=ApiError(code=code, message=message, details=details),
            correlation_id=self._id_fn(),
        )

    @staticmethod
    def _submission_history_entry(event: SubmissionEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "event_ts_utc": event.event_ts_utc,
            "event_type": event.event_type,
            "submission_id": event.submission_id,
            "process_cd": event.process_cd,
            "submission_period_cd": event.submission_period_cd,
            "organisation_cd": event.organisation_cd,
            "actor": event.actor,
            "reason": event.reason,
            "validation_flag": event.validation_flag,
            "summary": ValidationServiceApi._summarize_submission_event(event),
            "payload": event.payload_json,
            "sharepoint_source": event.sharepoint_source,
        }

    @staticmethod
    def _summarize_submission_event(event: SubmissionEvent) -> str:
        payload = event.payload_json or {}
        if event.event_type == "submission_created":
            return "Submission created."
        if event.event_type == "submission_note_updated":
            return "Submission note updated."
        if event.event_type == "submission_org_removed":
            return f"Organisation '{event.organisation_cd}' removed from submission."
        if event.event_type == "submission_org_upserted":
            return f"Organisation '{event.organisation_cd}' added or updated in submission."
        if event.event_type == "submission_template_removed":
            template_key = str(payload.get("template_key", "")).strip().upper()
            return f"Template '{template_key}' removed from submission."
        if event.event_type == "submission_template_upserted":
            template_key = str(payload.get("template_key", "")).strip().upper()
            return f"Template '{template_key}' added or updated in submission."
        if event.event_type == "org_flag_changed":
            return f"Organisation '{event.organisation_cd}' validation flag set to '{event.validation_flag}'."
        if event.event_type == "template_flag_changed":
            template_key = str(payload.get("template_key", "")).strip().upper()
            return f"Template '{template_key}' validation flag set to '{event.validation_flag}'."
        if event.event_type == "sharepoint_hashes_refreshed":
            return f"Organisation '{event.organisation_cd}' SharePoint hashes refreshed."
        if event.event_type == "template_hashes_refreshed":
            template_key = str(payload.get("template_key", "")).strip().upper()
            return f"Template '{template_key}' SharePoint hashes refreshed."
        if event.event_type == "validation_run_started":
            run_id = str(payload.get("run_id", "")).strip()
            return f"Legacy validation run '{run_id}' started."
        if event.event_type == "validation_run_org_staged":
            run_id = str(payload.get("run_id", "")).strip()
            return f"Legacy validation run '{run_id}' staged organisation '{event.organisation_cd}'."
        if event.event_type == "validation_run_org_failed":
            run_id = str(payload.get("run_id", "")).strip()
            return f"Legacy validation run '{run_id}' failed for organisation '{event.organisation_cd}'."
        return event.event_type
