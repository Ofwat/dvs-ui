from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


VALIDATION_VALIDATED = "validated"
VALIDATION_NOT_VALIDATED = "not_validated"


class _UnsetNoteValue:
    pass


UNSET_NOTE = _UnsetNoteValue()
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
class OperationError:
    code: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ListSubmissionsRequest:
    process_cd: str | None = None
    submission_period_cd: str | None = None
    state: str | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class CreateSubmissionRequest:
    process_cd: str
    submission_period_cd: str
    organisations: dict[str, dict[str, Any] | "OrganisationSubmissionRef" | "SharePointSourceRef"]
    templates: dict[str, dict[str, Any] | "TemplateSubmissionRef"] | None
    created_by: str
    idempotency_key: str
    note: str | None = None
    allow_duplicate_active: bool = False


@dataclass(frozen=True)
class EditSubmissionRequest:
    submission_id: str
    modified_by: str
    idempotency_key: str
    organisation_upserts: dict[str, dict[str, Any] | "OrganisationSubmissionRef" | "SharePointSourceRef"] | None = None
    organisation_removals: list[str] | None = None
    template_upserts: dict[str, dict[str, Any] | "TemplateSubmissionRef"] | None = None
    template_removals: list[str] | None = None
    organisation_validation_changes: dict[str, str] | None = None
    template_validation_changes: dict[str, str] | None = None
    note: str | None | _UnsetNoteValue = UNSET_NOTE
    reason: str | None = None


@dataclass(frozen=True)
class RefreshSubmissionRequest:
    submission_id: str
    refreshed_by: str
    idempotency_key: str


@dataclass(frozen=True)
class SetValidationFlagsRequest:
    submission_id: str
    organisation_changes: dict[str, str] | None
    template_changes: dict[str, str] | None
    modified_by: str
    idempotency_key: str
    reason: str | None = None
    validation_run_id: str | None = None


@dataclass(frozen=True)
class ListSubmissionHistoryRequest:
    submission_id: str
    actor: str | None = None
    include_run_events: bool = False


@dataclass(frozen=True)
class ListEditHistoryRequest:
    submission_id: str
    actor: str | None = None


@dataclass(frozen=True)
class ListSubmissionsResult:
    ok: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    error: OperationError | None = None

    @property
    def data(self) -> list[dict[str, Any]]:
        return self.items

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "items": self.items,
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class SubmissionMutationResult:
    ok: bool
    submission: dict[str, Any] | None = None
    error: OperationError | None = None

    @property
    def data(self) -> dict[str, Any] | None:
        return self.submission

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "submission": self.submission,
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class ListSubmissionHistoryResult:
    ok: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    error: OperationError | None = None

    @property
    def data(self) -> list[dict[str, Any]]:
        return self.items

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "items": self.items,
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class ListEditHistoryResult:
    ok: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    error: OperationError | None = None

    @property
    def data(self) -> list[dict[str, Any]]:
        return self.items

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "items": self.items,
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class TrackedFileRef:
    path: str | None
    watch: bool = False
    watch_search_term: str | None = None
    current_hash: str | None = None
    validated_hash: str | None = None
    validation_run_id: str | None = None
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
            validation_run_id=str(payload["validation_run_id"]).strip() if payload.get("validation_run_id") else None,
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
        self._entries: dict[tuple[str, str, str], tuple[str, Any]] = {}

    def get(self, operation: str, actor: str, key: str) -> tuple[str, Any] | None:
        return self._entries.get((operation, actor, key))

    def set(self, operation: str, actor: str, key: str, request_hash: str, response: Any):
        self._entries[(operation, actor, key)] = (request_hash, response)


class JsonFileIdempotencyStore:
    def __init__(self, store_path: str | Path):
        self._store_path = Path(store_path)
        self._store_path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, operation: str, actor: str, key: str) -> tuple[str, Any] | None:
        entries = self._load()
        payload = entries.get(self._entry_key(operation, actor, key))
        if not payload:
            return None
        return payload["request_hash"], payload["response"]

    def set(self, operation: str, actor: str, key: str, request_hash: str, response: Any):
        entries = self._load()
        entries[self._entry_key(operation, actor, key)] = {
            "request_hash": request_hash,
            "response": self._serialize_response(response),
        }
        self._store_path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _entry_key(operation: str, actor: str, key: str) -> str:
        return f"{operation}:{actor}:{key}"

    def _load(self) -> dict[str, Any]:
        if not self._store_path.exists():
            return {}
        return json.loads(self._store_path.read_text(encoding="utf-8"))

    @staticmethod
    def _serialize_response(response: Any) -> dict[str, Any]:
        if hasattr(response, "to_dict"):
            return {
                "type": type(response).__name__,
                "payload": response.to_dict(),
            }
        return {
            "type": "raw",
            "payload": response,
        }


class TextBackedIdempotencyStore:
    def __init__(
        self,
        read_text: Callable[[], str | None],
        write_text: Callable[[str], None],
    ):
        self._read_text = read_text
        self._write_text = write_text

    def get(self, operation: str, actor: str, key: str) -> tuple[str, Any] | None:
        entries = self._load()
        payload = entries.get(JsonFileIdempotencyStore._entry_key(operation, actor, key))
        if not payload:
            return None
        return payload["request_hash"], payload["response"]

    def set(self, operation: str, actor: str, key: str, request_hash: str, response: Any):
        entries = self._load()
        entries[JsonFileIdempotencyStore._entry_key(operation, actor, key)] = {
            "request_hash": request_hash,
            "response": JsonFileIdempotencyStore._serialize_response(response),
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

    @staticmethod
    def _deserialize_cached_response(payload: Any) -> Any:
        if not isinstance(payload, dict) or "type" not in payload or "payload" not in payload:
            return payload
        response_type = str(payload["type"])
        data = payload["payload"]
        if response_type == "ListSubmissionsResult":
            return ListSubmissionsResult(
                ok=bool(data["ok"]),
                items=list(data.get("items", [])),
                error=OperationError(**data["error"]) if data.get("error") else None,
            )
        if response_type in {"SubmissionMutationResult", "CreateSubmissionResult", "EditSubmissionResult", "RefreshSubmissionResult", "SetValidationFlagsResult"}:
            return SubmissionMutationResult(
                ok=bool(data["ok"]),
                submission=data.get("submission"),
                error=OperationError(**data["error"]) if data.get("error") else None,
            )
        if response_type == "ListSubmissionHistoryResult":
            return ListSubmissionHistoryResult(
                ok=bool(data["ok"]),
                items=list(data.get("items", [])),
                error=OperationError(**data["error"]) if data.get("error") else None,
            )
        if response_type == "ListEditHistoryResult":
            return ListEditHistoryResult(
                ok=bool(data["ok"]),
                items=list(data.get("items", [])),
                error=OperationError(**data["error"]) if data.get("error") else None,
            )
        return data

    @staticmethod
    def _list_submissions_result(
        *,
        items: list[dict[str, Any]] | None = None,
        error: OperationError | None = None,
    ) -> ListSubmissionsResult:
        return ListSubmissionsResult(ok=error is None, items=items or [], error=error)

    @staticmethod
    def _submission_mutation_result(
        *,
        submission: dict[str, Any] | None = None,
        error: OperationError | None = None,
    ) -> SubmissionMutationResult:
        return SubmissionMutationResult(ok=error is None, submission=submission, error=error)

    @staticmethod
    def _submission_history_result(
        *,
        items: list[dict[str, Any]] | None = None,
        error: OperationError | None = None,
    ) -> ListSubmissionHistoryResult:
        return ListSubmissionHistoryResult(ok=error is None, items=items or [], error=error)

    @staticmethod
    def _edit_history_result(
        *,
        items: list[dict[str, Any]] | None = None,
        error: OperationError | None = None,
    ) -> ListEditHistoryResult:
        return ListEditHistoryResult(ok=error is None, items=items or [], error=error)

    def list_submissions(
        self,
        request: ListSubmissionsRequest,
    ) -> ListSubmissionsResult:
        items = self._project_submissions()
        filtered = []
        for item in items:
            if request.process_cd and item["process_cd"] != request.process_cd:
                continue
            if request.submission_period_cd and item["submission_period_cd"] != request.submission_period_cd:
                continue
            if request.state and item["state"] != request.state:
                continue
            if request.created_by and item["created_by"] != request.created_by:
                continue
            filtered.append(item)
        filtered.sort(key=lambda item: (str(item.get("created_ts_utc") or ""), str(item.get("submission_id") or "")), reverse=True)
        self._write_projection_file(items)
        return self._list_submissions_result(items=filtered)

    def list_submission_history(
        self,
        request: ListSubmissionHistoryRequest,
    ) -> ListSubmissionHistoryResult:
        projection = self._get_submission_projection(request.submission_id)
        if projection is None:
            return self._submission_history_result(error=OperationError("SUBMISSION_NOT_FOUND", "Submission not found."))

        items = []
        for event in sorted(self._events.list_all(), key=lambda item: (item.event_ts_utc, item.event_id), reverse=True):
            if event.submission_id != request.submission_id:
                continue
            if request.actor and event.actor != request.actor:
                continue
            if not request.include_run_events and event.event_type.startswith("validation_run_"):
                continue
            items.append(self._submission_history_entry(event))
        return self._submission_history_result(items=items)

    def list_edit_history(
        self,
        request: ListEditHistoryRequest,
    ) -> ListEditHistoryResult:
        projection = self._get_submission_projection(request.submission_id)
        if projection is None:
            return self._edit_history_result(error=OperationError("SUBMISSION_NOT_FOUND", "Submission not found."))

        items = []
        for event in sorted(self._events.list_all(), key=lambda item: (item.event_ts_utc, item.event_id), reverse=True):
            if event.submission_id != request.submission_id:
                continue
            if request.actor and event.actor != request.actor:
                continue
            if event.event_type not in SUBMISSION_EDIT_EVENT_TYPES:
                continue
            items.append(self._submission_history_entry(event))
        return self._edit_history_result(items=items)

    def create_submission(
        self,
        request: CreateSubmissionRequest,
    ) -> SubmissionMutationResult:
        payload = {
            "process_cd": request.process_cd,
            "submission_period_cd": request.submission_period_cd,
            "organisations": {
                key: (
                    value.to_dict()
                    if isinstance(value, (OrganisationSubmissionRef, SharePointSourceRef))
                    else value
                )
                for key, value in request.organisations.items()
            },
            "templates": {
                key: value.to_dict() if isinstance(value, TemplateSubmissionRef) else value
                for key, value in (request.templates or {}).items()
            },
            "note": request.note,
            "allow_duplicate_active": request.allow_duplicate_active,
        }
        idempotent = self._handle_idempotency("create_submission", request.created_by, request.idempotency_key, payload)
        if idempotent is not None:
            return idempotent

        process_cd = request.process_cd.strip()
        submission_period_cd = request.submission_period_cd.strip()
        cleaned_organisations = {
            key.strip().upper(): self._normalize_organisation_ref(value)
            for key, value in request.organisations.items()
        }
        cleaned_templates = {
            key.strip().upper(): self._normalize_template_ref(value)
            for key, value in (request.templates or {}).items()
        }
        if not process_cd or not submission_period_cd:
            response = self._submission_mutation_result(error=OperationError("VALIDATION_ERROR", "process_cd and submission_period_cd are required."))
            self._store_idempotency("create_submission", request.created_by, request.idempotency_key, payload, response)
            return response
        if not cleaned_organisations:
            response = self._submission_mutation_result(error=OperationError("VALIDATION_ERROR", "organisations must not be empty."))
            self._store_idempotency("create_submission", request.created_by, request.idempotency_key, payload, response)
            return response
        invalid_orgs = [key for key, item in cleaned_organisations.items() if not self._is_valid_organisation_ref(item)]
        if invalid_orgs:
            response = self._submission_mutation_result(
                error=OperationError("VALIDATION_ERROR", f"organisations contains invalid source refs for: {', '.join(invalid_orgs)}.")
            )
            self._store_idempotency("create_submission", request.created_by, request.idempotency_key, payload, response)
            return response
        invalid_templates = [key for key, item in cleaned_templates.items() if not self._is_valid_template_ref(item)]
        if invalid_templates:
            response = self._submission_mutation_result(
                error=OperationError("VALIDATION_ERROR", f"templates contains invalid source refs for: {', '.join(invalid_templates)}.")
            )
            self._store_idempotency("create_submission", request.created_by, request.idempotency_key, payload, response)
            return response
        template_validation_error = self._validate_template_links(cleaned_organisations, cleaned_templates)
        if template_validation_error is not None:
            self._store_idempotency("create_submission", request.created_by, request.idempotency_key, payload, template_validation_error)
            return template_validation_error

        if not request.allow_duplicate_active:
            for item in self._project_submissions():
                if (
                    item["process_cd"] == process_cd
                    and item["submission_period_cd"] == submission_period_cd
                    and item["state"] not in TERMINAL_STATES
                ):
                    response = self._submission_mutation_result(
                        error=OperationError(
                            "DUPLICATE_ACTIVE_SUBMISSION",
                            "Active submission already exists for process_cd/submission_period_cd.",
                            details={"submission_id": item["submission_id"]},
                        )
                    )
                    self._store_idempotency("create_submission", request.created_by, request.idempotency_key, payload, response)
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
                actor=request.created_by,
                payload_json={
                    **({"note": request.note.strip()} if request.note and request.note.strip() else {}),
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
                actor=request.created_by,
                payload_json={
                    "template_key": template_key,
                    "assignment_mode": template_ref.assignment_mode,
                    "applies_to": template_ref.applies_to,
                },
            )
            self._events.append(event)

        created = self._get_submission_projection(submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._submission_mutation_result(submission=created)
        self._store_idempotency("create_submission", request.created_by, request.idempotency_key, payload, response)
        return response

    def set_validation_flags(
        self,
        request: SetValidationFlagsRequest,
    ) -> SubmissionMutationResult:
        payload = {
            "submission_id": request.submission_id,
            "organisation_changes": request.organisation_changes or {},
            "template_changes": request.template_changes or {},
            "reason": request.reason,
            "validation_run_id": request.validation_run_id,
        }
        idempotent = self._handle_idempotency(
            "set_validation_flags", request.modified_by, request.idempotency_key, payload
        )
        if idempotent is not None:
            return idempotent

        projection = self._get_submission_projection(request.submission_id)
        if projection is None:
            response = self._submission_mutation_result(error=OperationError("SUBMISSION_NOT_FOUND", "Submission not found."))
            self._store_idempotency(
                "set_validation_flags",
                request.modified_by,
                request.idempotency_key,
                payload,
                response,
            )
            return response

        known_orgs = set(projection["organisations"].keys())
        known_templates = set(projection.get("templates", {}).keys())
        now_iso = self._utc_now_iso()
        for org, flag in (request.organisation_changes or {}).items():
            org_cd = org.strip().upper()
            if org_cd not in known_orgs:
                response = self._submission_mutation_result(
                    error=OperationError("ORG_NOT_IN_SUBMISSION", f"Organisation '{org_cd}' does not exist in submission.")
                )
                self._store_idempotency(
                    "set_validation_flags",
                    request.modified_by,
                    request.idempotency_key,
                    payload,
                    response,
                )
                return response
            if flag not in {VALIDATION_VALIDATED, VALIDATION_NOT_VALIDATED}:
                response = self._submission_mutation_result(
                    error=OperationError("VALIDATION_ERROR", f"Unsupported flag '{flag}' for '{org_cd}'.")
                )
                self._store_idempotency(
                    "set_validation_flags",
                    request.modified_by,
                    request.idempotency_key,
                    payload,
                    response,
                )
                return response

        for template_key, flag in (request.template_changes or {}).items():
            normalized_template_key = template_key.strip().upper()
            if normalized_template_key not in known_templates:
                response = self._submission_mutation_result(
                    error=OperationError("TEMPLATE_NOT_IN_SUBMISSION", f"Template '{normalized_template_key}' does not exist in submission.")
                )
                self._store_idempotency(
                    "set_validation_flags",
                    request.modified_by,
                    request.idempotency_key,
                    payload,
                    response,
                )
                return response
            if flag not in {VALIDATION_VALIDATED, VALIDATION_NOT_VALIDATED}:
                response = self._submission_mutation_result(
                    error=OperationError("VALIDATION_ERROR", f"Unsupported flag '{flag}' for '{normalized_template_key}'.")
                )
                self._store_idempotency(
                    "set_validation_flags",
                    request.modified_by,
                    request.idempotency_key,
                    payload,
                    response,
                )
                return response

        for org, flag in (request.organisation_changes or {}).items():
            org_cd = org.strip().upper()
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="org_flag_changed",
                submission_id=request.submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=org_cd,
                sharepoint_source=self._with_validation_hashes(
                    projection["organisations"][org_cd]["file"],
                    flag,
                    validation_run_id=request.validation_run_id,
                ),
                validation_flag=flag,
                actor=request.modified_by,
                reason=request.reason,
            )
            self._events.append(event)

        for template_key, flag in (request.template_changes or {}).items():
            normalized_template_key = template_key.strip().upper()
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="template_flag_changed",
                submission_id=request.submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=None,
                sharepoint_source=self._with_validation_hashes(
                    projection["templates"][normalized_template_key]["file"],
                    flag,
                    validation_run_id=request.validation_run_id,
                ),
                validation_flag=flag,
                actor=request.modified_by,
                reason=request.reason,
                payload_json={"template_key": normalized_template_key},
            )
            self._events.append(event)

        updated = self._get_submission_projection(request.submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._submission_mutation_result(submission=updated)
        self._store_idempotency(
            "set_validation_flags",
            request.modified_by,
            request.idempotency_key,
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
        validation_run_id: str | None = None,
    ) -> SubmissionMutationResult:
        return self.set_validation_flags(
            SetValidationFlagsRequest(
                submission_id=submission_id,
                organisation_changes=changes,
                template_changes=None,
                modified_by=modified_by,
                idempotency_key=idempotency_key,
                reason=reason,
                validation_run_id=validation_run_id,
            )
        )

    def set_template_validation_flags(
        self,
        submission_id: str,
        changes: dict[str, str],
        modified_by: str,
        idempotency_key: str,
        reason: str | None = None,
        validation_run_id: str | None = None,
    ) -> SubmissionMutationResult:
        return self.set_validation_flags(
            SetValidationFlagsRequest(
                submission_id=submission_id,
                organisation_changes=None,
                template_changes=changes,
                modified_by=modified_by,
                idempotency_key=idempotency_key,
                reason=reason,
                validation_run_id=validation_run_id,
            )
        )

    def refresh_submission_hashes(
        self,
        request: RefreshSubmissionRequest,
    ) -> SubmissionMutationResult:
        payload = {"submission_id": request.submission_id}
        idempotent = self._handle_idempotency(
            "refresh_submission_hashes",
            request.refreshed_by,
            request.idempotency_key,
            payload,
        )
        if idempotent is not None:
            return idempotent

        if self._sharepoint_hash_resolver is None:
            response = self._submission_mutation_result(
                error=OperationError(
                    "SHAREPOINT_HASH_RESOLVER_NOT_CONFIGURED",
                    "SharePoint hash resolver is not configured for this service instance.",
                )
            )
            self._store_idempotency("refresh_submission_hashes", request.refreshed_by, request.idempotency_key, payload, response)
            return response

        projection = self._get_submission_projection(request.submission_id)
        if projection is None:
            response = self._submission_mutation_result(error=OperationError("SUBMISSION_NOT_FOUND", "Submission not found."))
            self._store_idempotency("refresh_submission_hashes", request.refreshed_by, request.idempotency_key, payload, response)
            return response

        now_iso = self._utc_now_iso()
        for org_cd, org_payload in projection["organisations"].items():
            refreshed_source = self._sharepoint_hash_resolver(org_payload["file"])
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="sharepoint_hashes_refreshed",
                submission_id=request.submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=org_cd,
                sharepoint_source=refreshed_source,
                validation_flag=org_payload.get("validation_flag"),
                actor=request.refreshed_by,
                payload_json={"template_keys": org_payload.get("template_keys", [])},
            )
            self._events.append(event)

        for template_key, template_payload in projection.get("templates", {}).items():
            refreshed_source = self._sharepoint_hash_resolver(template_payload["file"])
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="template_hashes_refreshed",
                submission_id=request.submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=None,
                sharepoint_source=refreshed_source,
                validation_flag=template_payload.get("validation_flag"),
                actor=request.refreshed_by,
                payload_json={"template_key": template_key},
            )
            self._events.append(event)

        updated = self._get_submission_projection(request.submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._submission_mutation_result(submission=updated)
        self._store_idempotency("refresh_submission_hashes", request.refreshed_by, request.idempotency_key, payload, response)
        return response

    def edit_submission(
        self,
        request: EditSubmissionRequest,
    ) -> SubmissionMutationResult:
        payload = {
            "submission_id": request.submission_id,
            "organisation_upserts": {
                key: (
                    value.to_dict()
                    if isinstance(value, (OrganisationSubmissionRef, SharePointSourceRef))
                    else value
                )
                for key, value in (request.organisation_upserts or {}).items()
            },
            "organisation_removals": request.organisation_removals or [],
            "template_upserts": {
                key: value.to_dict() if isinstance(value, TemplateSubmissionRef) else value
                for key, value in (request.template_upserts or {}).items()
            },
            "template_removals": request.template_removals or [],
            "organisation_validation_changes": request.organisation_validation_changes or {},
            "template_validation_changes": request.template_validation_changes or {},
            "note": None if request.note is UNSET_NOTE else request.note,
            "reason": request.reason,
        }
        idempotent = self._handle_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload)
        if idempotent is not None:
            return idempotent

        projection = self._get_submission_projection(request.submission_id)
        if projection is None:
            response = self._submission_mutation_result(error=OperationError("SUBMISSION_NOT_FOUND", "Submission not found."))
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response

        current_orgs = projection["organisations"]
        current_templates = projection.get("templates", {})
        removals = [item.strip().upper() for item in (request.organisation_removals or []) if item and item.strip()]
        unknown_removals = [org_cd for org_cd in removals if org_cd not in current_orgs]
        if unknown_removals:
            response = self._submission_mutation_result(
                error=OperationError("ORG_NOT_IN_SUBMISSION", f"Organisation(s) not found in submission: {', '.join(unknown_removals)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response

        normalized_upserts = {
            key.strip().upper(): self._reset_organisation_tracking_metadata(self._normalize_organisation_ref(value))
            for key, value in (request.organisation_upserts or {}).items()
        }
        invalid_sources = [key for key, source in normalized_upserts.items() if not self._is_valid_organisation_ref(source)]
        if invalid_sources:
            response = self._submission_mutation_result(
                error=OperationError("VALIDATION_ERROR", f"organisation_upserts contains invalid refs for: {', '.join(invalid_sources)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response

        template_removal_keys = [item.strip().upper() for item in (request.template_removals or []) if item and item.strip()]
        unknown_template_removals = [key for key in template_removal_keys if key not in current_templates]
        if unknown_template_removals:
            response = self._submission_mutation_result(
                error=OperationError("TEMPLATE_NOT_IN_SUBMISSION", f"Template(s) not found in submission: {', '.join(unknown_template_removals)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response

        normalized_template_upserts = {
            key.strip().upper(): self._reset_template_tracking_metadata(self._normalize_template_ref(value))
            for key, value in (request.template_upserts or {}).items()
        }
        invalid_templates = [key for key, source in normalized_template_upserts.items() if not self._is_valid_template_ref(source)]
        if invalid_templates:
            response = self._submission_mutation_result(
                error=OperationError("VALIDATION_ERROR", f"template_upserts contains invalid refs for: {', '.join(invalid_templates)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response

        template_validation_error = self._validate_template_links(
            {**{k: self._organisation_ref_from_projection(v) for k, v in current_orgs.items()}, **normalized_upserts},
            {**{k: self._template_ref_from_projection(v) for k, v in current_templates.items()}, **normalized_template_upserts},
            removed_template_keys=template_removal_keys,
        )
        if template_validation_error is not None:
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, template_validation_error)
            return template_validation_error

        for org_cd in removals:
            if org_cd in normalized_upserts:
                response = self._submission_mutation_result(
                    error=OperationError("VALIDATION_ERROR", f"Organisation '{org_cd}' cannot be removed and upserted in the same edit.")
                )
                self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
                return response
        for template_key in template_removal_keys:
            if template_key in normalized_template_upserts:
                response = self._submission_mutation_result(
                    error=OperationError("VALIDATION_ERROR", f"Template '{template_key}' cannot be removed and upserted in the same edit.")
                )
                self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
                return response

        normalized_org_validation_changes = {
            key.strip().upper(): str(flag).strip().lower()
            for key, flag in (request.organisation_validation_changes or {}).items()
            if key and str(key).strip()
        }
        normalized_template_validation_changes = {
            key.strip().upper(): str(flag).strip().lower()
            for key, flag in (request.template_validation_changes or {}).items()
            if key and str(key).strip()
        }
        effective_orgs = (set(current_orgs.keys()) - set(removals)) | set(normalized_upserts.keys())
        effective_templates = (set(current_templates.keys()) - set(template_removal_keys)) | set(normalized_template_upserts.keys())
        unknown_org_flag_changes = [org_cd for org_cd in normalized_org_validation_changes if org_cd not in effective_orgs]
        if unknown_org_flag_changes:
            response = self._submission_mutation_result(
                error=OperationError("ORG_NOT_IN_SUBMISSION", f"Organisation(s) not found in submission: {', '.join(unknown_org_flag_changes)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response
        unknown_template_flag_changes = [template_key for template_key in normalized_template_validation_changes if template_key not in effective_templates]
        if unknown_template_flag_changes:
            response = self._submission_mutation_result(
                error=OperationError("TEMPLATE_NOT_IN_SUBMISSION", f"Template(s) not found in submission: {', '.join(unknown_template_flag_changes)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response
        invalid_org_flags = [
            org_cd for org_cd, flag in normalized_org_validation_changes.items()
            if flag not in {VALIDATION_VALIDATED, VALIDATION_NOT_VALIDATED}
        ]
        if invalid_org_flags:
            response = self._submission_mutation_result(
                error=OperationError("VALIDATION_ERROR", f"Unsupported organisation validation flag update for: {', '.join(invalid_org_flags)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response
        invalid_template_flags = [
            template_key for template_key, flag in normalized_template_validation_changes.items()
            if flag not in {VALIDATION_VALIDATED, VALIDATION_NOT_VALIDATED}
        ]
        if invalid_template_flags:
            response = self._submission_mutation_result(
                error=OperationError("VALIDATION_ERROR", f"Unsupported template validation flag update for: {', '.join(invalid_template_flags)}.")
            )
            self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
            return response

        now_iso = self._utc_now_iso()
        if request.note is not UNSET_NOTE:
            normalized_note = request.note.strip() if isinstance(request.note, str) else None
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_note_updated",
                    submission_id=request.submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=None,
                    validation_flag=None,
                    actor=request.modified_by,
                    reason=request.reason,
                    payload_json={"note": normalized_note if normalized_note else None},
                )
            )

        for org_cd in removals:
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_org_removed",
                    submission_id=request.submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=org_cd,
                    sharepoint_source=None,
                    validation_flag=None,
                    actor=request.modified_by,
                    reason=request.reason,
                )
            )

        for org_cd, source in normalized_upserts.items():
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_org_upserted",
                    submission_id=request.submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=org_cd,
                    sharepoint_source=source.sharepoint_source.to_dict(),
                    validation_flag=VALIDATION_NOT_VALIDATED,
                    actor=request.modified_by,
                    reason=request.reason,
                    payload_json={"template_keys": source.template_keys},
                )
            )

        for template_key in template_removal_keys:
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_template_removed",
                    submission_id=request.submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=None,
                    validation_flag=None,
                    actor=request.modified_by,
                    reason=request.reason,
                    payload_json={"template_key": template_key},
                )
            )

        for template_key, template_ref in normalized_template_upserts.items():
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="submission_template_upserted",
                    submission_id=request.submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=template_ref.sharepoint_source.to_dict(),
                    validation_flag=VALIDATION_NOT_VALIDATED,
                    actor=request.modified_by,
                    reason=request.reason,
                    payload_json={
                        "template_key": template_key,
                        "assignment_mode": template_ref.assignment_mode,
                        "applies_to": template_ref.applies_to,
                    },
                )
            )

        org_sources_for_flags = {
            org_cd: normalized_upserts[org_cd].sharepoint_source.to_dict()
            if org_cd in normalized_upserts
            else current_orgs[org_cd]["file"]
            for org_cd in normalized_org_validation_changes
        }
        for org_cd, flag in normalized_org_validation_changes.items():
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="org_flag_changed",
                    submission_id=request.submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=org_cd,
                    sharepoint_source=self._with_validation_hashes(
                        org_sources_for_flags[org_cd],
                        flag,
                    ),
                    validation_flag=flag,
                    actor=request.modified_by,
                    reason=request.reason,
                )
            )

        template_sources_for_flags = {
            template_key: normalized_template_upserts[template_key].sharepoint_source.to_dict()
            if template_key in normalized_template_upserts
            else current_templates[template_key]["file"]
            for template_key in normalized_template_validation_changes
        }
        for template_key, flag in normalized_template_validation_changes.items():
            self._events.append(
                SubmissionEvent(
                    event_id=self._id_fn(),
                    event_ts_utc=now_iso,
                    event_type="template_flag_changed",
                    submission_id=request.submission_id,
                    process_cd=projection["process_cd"],
                    submission_period_cd=projection["submission_period_cd"],
                    organisation_cd=None,
                    sharepoint_source=self._with_validation_hashes(
                        template_sources_for_flags[template_key],
                        flag,
                    ),
                    validation_flag=flag,
                    actor=request.modified_by,
                    reason=request.reason,
                    payload_json={"template_key": template_key},
                )
            )

        updated = self._get_submission_projection(request.submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._submission_mutation_result(submission=updated)
        self._store_idempotency("edit_submission", request.modified_by, request.idempotency_key, payload, response)
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
                    org_entry["validation_flag"] = event.validation_flag
                if event.payload_json and "template_keys" in event.payload_json:
                    org_entry["template_keys"] = [
                        str(item).strip().upper()
                        for item in event.payload_json.get("template_keys", [])
                        if str(item).strip()
                    ]
                submission["organisations"][event.organisation_cd] = org_entry

            submission["state"] = self._compute_submission_state(submission)

        return list(by_submission.values())

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

    def _get_submission_projection(self, submission_id: str) -> dict[str, Any] | None:
        for item in self._project_submissions():
            if item["submission_id"] == submission_id:
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
                    validation_run_id=item.validation_run_id.strip() if item.validation_run_id else None,
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
    def _reset_source_tracking_metadata(source: SharePointSourceRef) -> SharePointSourceRef:
        return SharePointSourceRef(
            source_type=source.source_type,
            target_name=source.target_name,
            root_path=source.root_path,
            tracked_files=[
                TrackedFileRef(
                    path=item.path,
                    watch=item.watch,
                    watch_search_term=item.watch_search_term,
                    current_hash=None,
                    validated_hash=None,
                    validation_run_id=None,
                    size_bytes=None,
                    created=None,
                    modified=None,
                    modified_by=None,
                )
                for item in source.tracked_files
            ],
            folder_url=source.folder_url,
            drive_id=source.drive_id,
            linked_item_id=source.linked_item_id,
            linked_item_name=source.linked_item_name,
            linked_item_is_folder=source.linked_item_is_folder,
        )

    @classmethod
    def _reset_organisation_tracking_metadata(cls, source: OrganisationSubmissionRef) -> OrganisationSubmissionRef:
        return OrganisationSubmissionRef(
            sharepoint_source=cls._reset_source_tracking_metadata(source.sharepoint_source),
            template_keys=list(source.template_keys),
        )

    @classmethod
    def _reset_template_tracking_metadata(cls, template: TemplateSubmissionRef) -> TemplateSubmissionRef:
        return TemplateSubmissionRef(
            assignment_mode=template.assignment_mode,
            applies_to=list(template.applies_to),
            sharepoint_source=cls._reset_source_tracking_metadata(template.sharepoint_source),
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
    ) -> SubmissionMutationResult | None:
        removed = set(removed_template_keys or [])
        available_templates = set(templates.keys()) - removed
        known_orgs = set(organisations.keys())
        for org_cd, org_ref in organisations.items():
            unknown_template_keys = [key for key in org_ref.template_keys if key not in available_templates]
            if unknown_template_keys:
                return self._submission_mutation_result(
                    error=OperationError(
                        "VALIDATION_ERROR",
                        f"Organisation '{org_cd}' references unknown template(s): {', '.join(unknown_template_keys)}.",
                    )
                )
        for template_key, template_ref in templates.items():
            unknown_orgs = [org_cd for org_cd in template_ref.applies_to if org_cd not in known_orgs]
            if unknown_orgs:
                return self._submission_mutation_result(
                    error=OperationError(
                        "VALIDATION_ERROR",
                        f"Template '{template_key}' references unknown organisation(s): {', '.join(unknown_orgs)}.",
                    )
                )
        return None

    @staticmethod
    def _with_validation_hashes(
        source_payload: dict[str, Any],
        validation_flag: str,
        *,
        validation_run_id: str | None = None,
    ) -> dict[str, Any]:
        source = SharePointSourceRef.from_dict(source_payload)
        tracked_files = [
            TrackedFileRef(
                path=item.path,
                watch=item.watch,
                watch_search_term=item.watch_search_term,
                current_hash=item.current_hash,
                validated_hash=item.current_hash if validation_flag == VALIDATION_VALIDATED else None,
                validation_run_id=validation_run_id if validation_flag == VALIDATION_VALIDATED else None,
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
    ) -> Any | None:
        payload_hash = self._hash_payload(payload)
        entry = self._idempotency.get(operation, actor, idempotency_key)
        if not entry:
            return None
        cached_hash, cached_response = entry
        if cached_hash != payload_hash:
            return self._submission_mutation_result(
                error=OperationError("IDEMPOTENCY_CONFLICT", "Idempotency key was already used with a different payload.")
            )
        return self._deserialize_cached_response(cached_response)

    def _store_idempotency(
        self,
        operation: str,
        actor: str,
        idempotency_key: str,
        payload: dict[str, Any],
        response: Any,
    ):
        self._idempotency.set(operation, actor, idempotency_key, self._hash_payload(payload), response)

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
