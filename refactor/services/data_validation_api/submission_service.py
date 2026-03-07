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
class SharePointSourceRef:
    source_type: str
    target_name: str
    root_path: str
    file_path: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SharePointSourceRef":
        return cls(
            source_type=str(payload["source_type"]).strip().lower(),
            target_name=str(payload["target_name"]).strip(),
            root_path=str(payload["root_path"]).strip(),
            file_path=str(payload["file_path"]).strip(),
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


class ValidationServiceApi:
    def __init__(
        self,
        workspace_id: str,
        submissions_registry_path: str,
        event_store: InMemorySubmissionEventStore | JsonlSubmissionEventStore | None = None,
        idempotency_store: InMemoryIdempotencyStore | JsonFileIdempotencyStore | None = None,
        submissions_projection_path: str | Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
    ):
        self.workspace_id = workspace_id
        self.submissions_registry_path = submissions_registry_path
        self._events = event_store or InMemorySubmissionEventStore()
        self._idempotency = idempotency_store or InMemoryIdempotencyStore()
        self._submissions_projection_path = Path(submissions_projection_path) if submissions_projection_path else None
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

    def create_submission(
        self,
        process_cd: str,
        submission_period_cd: str,
        organisation_sources: dict[str, dict[str, Any] | SharePointSourceRef],
        created_by: str,
        idempotency_key: str,
        allow_duplicate_active: bool = False,
    ) -> ApiResponse:
        payload = {
            "process_cd": process_cd,
            "submission_period_cd": submission_period_cd,
            "organisation_sources": {
                key: value.to_dict() if isinstance(value, SharePointSourceRef) else value
                for key, value in organisation_sources.items()
            },
            "allow_duplicate_active": allow_duplicate_active,
        }
        idempotent = self._handle_idempotency("create_submission", created_by, idempotency_key, payload)
        if idempotent is not None:
            return idempotent

        process_cd = process_cd.strip()
        submission_period_cd = submission_period_cd.strip()
        cleaned_org_sources = {
            key.strip().upper(): self._normalize_source_ref(value)
            for key, value in organisation_sources.items()
        }
        if not process_cd or not submission_period_cd:
            response = self._error("VALIDATION_ERROR", "process_cd and submission_period_cd are required.")
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
            return response
        if not cleaned_org_sources:
            response = self._error("VALIDATION_ERROR", "organisation_sources must not be empty.")
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
            return response
        invalid_sources = [key for key, source in cleaned_org_sources.items() if not self._is_valid_source_ref(source)]
        if invalid_sources:
            response = self._error(
                "VALIDATION_ERROR",
                f"organisation_sources contains invalid source refs for: {', '.join(invalid_sources)}.",
            )
            self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
            return response

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
        for org_cd, source in cleaned_org_sources.items():
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="submission_created",
                submission_id=submission_id,
                process_cd=process_cd,
                submission_period_cd=submission_period_cd,
                organisation_cd=org_cd,
                sharepoint_source=source.to_dict(),
                validation_flag=VALIDATION_NOT_VALIDATED,
                actor=created_by,
            )
            self._events.append(event)

        created = self._get_submission_projection(submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._ok(created)
        self._store_idempotency("create_submission", created_by, idempotency_key, payload, response)
        return response

    def set_organisation_validation_flags(
        self,
        submission_id: str,
        changes: dict[str, str],
        modified_by: str,
        idempotency_key: str,
        reason: str | None = None,
    ) -> ApiResponse:
        payload = {
            "submission_id": submission_id,
            "changes": changes,
            "reason": reason,
        }
        idempotent = self._handle_idempotency(
            "set_organisation_validation_flags", modified_by, idempotency_key, payload
        )
        if idempotent is not None:
            return idempotent

        projection = self._get_submission_projection(submission_id)
        if projection is None:
            response = self._error("SUBMISSION_NOT_FOUND", "Submission not found.")
            self._store_idempotency(
                "set_organisation_validation_flags",
                modified_by,
                idempotency_key,
                payload,
                response,
            )
            return response

        known_orgs = set(projection["organisations"].keys())
        now_iso = self._utc_now_iso()
        for org, flag in changes.items():
            org_cd = org.strip().upper()
            if org_cd not in known_orgs:
                response = self._error(
                    "ORG_NOT_IN_SUBMISSION",
                    f"Organisation '{org_cd}' does not exist in submission.",
                )
                self._store_idempotency(
                    "set_organisation_validation_flags",
                    modified_by,
                    idempotency_key,
                    payload,
                    response,
                )
                return response
            if flag not in {VALIDATION_VALIDATED, VALIDATION_NOT_VALIDATED}:
                response = self._error("VALIDATION_ERROR", f"Unsupported flag '{flag}' for '{org_cd}'.")
                self._store_idempotency(
                    "set_organisation_validation_flags",
                    modified_by,
                    idempotency_key,
                    payload,
                    response,
                )
                return response

        for org, flag in changes.items():
            org_cd = org.strip().upper()
            event = SubmissionEvent(
                event_id=self._id_fn(),
                event_ts_utc=now_iso,
                event_type="org_flag_changed",
                submission_id=submission_id,
                process_cd=projection["process_cd"],
                submission_period_cd=projection["submission_period_cd"],
                organisation_cd=org_cd,
                sharepoint_source=projection["organisations"][org_cd]["sharepoint_source"],
                validation_flag=flag,
                actor=modified_by,
                reason=reason,
            )
            self._events.append(event)

        updated = self._get_submission_projection(submission_id)
        self._write_projection_file(self._project_submissions())
        response = self._ok(updated)
        self._store_idempotency(
            "set_organisation_validation_flags",
            modified_by,
            idempotency_key,
            payload,
            response,
        )
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
                    "organisations": {},
                    "state": "ready",
                }
                by_submission[event.submission_id] = submission

            if event.organisation_cd:
                org_entry = submission["organisations"].get(event.organisation_cd, {})
                if event.sharepoint_source is not None:
                    org_entry["sharepoint_source"] = event.sharepoint_source
                if event.validation_flag is not None:
                    org_entry["validation_flag"] = event.validation_flag
                submission["organisations"][event.organisation_cd] = org_entry

            submission["state"] = self._compute_submission_state(submission)

        return list(by_submission.values())

    @staticmethod
    def _compute_submission_state(submission: dict[str, Any]) -> str:
        flags = [
            org.get("validation_flag", VALIDATION_NOT_VALIDATED)
            for org in submission["organisations"].values()
        ]
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
        if not self._submissions_projection_path:
            return
        self._submissions_projection_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_ts_utc": self._utc_now_iso(),
            "items": items,
        }
        self._submissions_projection_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

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
            file_path=normalized.file_path.strip(),
        )

    @staticmethod
    def _is_valid_source_ref(source: SharePointSourceRef) -> bool:
        return (
            source.source_type in {"drive", "group"}
            and bool(source.target_name)
            and bool(source.root_path)
            and bool(source.file_path)
        )

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
