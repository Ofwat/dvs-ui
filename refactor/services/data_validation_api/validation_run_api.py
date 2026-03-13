from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from refactor.services.data_validation_api.submission_service import (
    InMemoryIdempotencyStore,
    JsonFileIdempotencyStore,
    JsonProjectionStore,
    TextBackedIdempotencyStore,
    TextBackedProjectionStore,
    VALIDATION_NOT_VALIDATED,
    VALIDATION_VALIDATED,
)


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    submission_id: str
    process_cd: str
    submission_period_cd: str
    requested_by: str
    requested_ts_utc: str
    state: str
    selected_organisations: list[str]
    selected_templates: list[str]
    selected_asset_keys: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunSummary":
        return cls(
            run_id=str(payload["run_id"]),
            submission_id=str(payload["submission_id"]),
            process_cd=str(payload.get("process_cd") or ""),
            submission_period_cd=str(payload.get("submission_period_cd") or ""),
            requested_by=str(payload["requested_by"]),
            requested_ts_utc=str(payload["requested_ts_utc"]),
            state=str(payload["state"]),
            selected_organisations=[str(item) for item in payload.get("selected_organisations", [])],
            selected_templates=[str(item) for item in payload.get("selected_templates", [])],
            selected_asset_keys=[str(item) for item in payload.get("selected_asset_keys", [])],
        )


@dataclass(frozen=True)
class AssetSourceRef:
    source_type: str
    target_name: str
    root_path: str
    path: str
    folder_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssetHashSnapshot:
    current_hash: str | None
    validated_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssetMetadataSnapshot:
    created: str | None
    modified: str | None
    modified_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssetSnapshot:
    asset_key: str
    asset_type: str
    organisation_cds: list[str]
    source: AssetSourceRef
    hash_snapshot: AssetHashSnapshot
    metadata_snapshot: AssetMetadataSnapshot
    status: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source"] = self.source.to_dict()
        payload["hash_snapshot"] = self.hash_snapshot.to_dict()
        payload["metadata_snapshot"] = self.metadata_snapshot.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AssetSnapshot":
        return cls(
            asset_key=str(payload["asset_key"]),
            asset_type=str(payload["asset_type"]),
            organisation_cds=[str(item).strip().upper() for item in payload.get("organisation_cds", []) if str(item).strip()],
            source=AssetSourceRef(**payload["source"]),
            hash_snapshot=AssetHashSnapshot(**payload["hash_snapshot"]),
            metadata_snapshot=AssetMetadataSnapshot(**payload["metadata_snapshot"]),
            status=str(payload.get("status") or "ready_to_run"),
        )


@dataclass(frozen=True)
class StagedAsset:
    asset_key: str
    path: str
    staged_path: str
    byte_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StagedAsset":
        staged_path = payload.get("staged_path") or payload.get("staging_path")
        return cls(
            asset_key=str(payload["asset_key"]),
            path=str(payload["path"]),
            staged_path=str(staged_path),
            byte_count=int(payload["byte_count"]),
        )


@dataclass(frozen=True)
class PipelineRef:
    pipeline_id: str
    pipeline_run_id: str
    triggered_ts_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PipelineRef":
        return cls(
            pipeline_id=str(payload["pipeline_id"]),
            pipeline_run_id=str(payload["pipeline_run_id"]),
            triggered_ts_utc=str(payload["triggered_ts_utc"]),
        )


@dataclass(frozen=True)
class RunStatusError:
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | str) -> "RunStatusError":
        if isinstance(payload, str):
            return cls(code="PIPELINE_ERROR", message=payload)
        return cls(
            code=str(payload.get("code") or "PIPELINE_ERROR"),
            message=str(payload.get("message") or payload),
        )


@dataclass(frozen=True)
class PlanRunRequest:
    submission_id: str
    requested_organisations: list[str] | None = None
    requested_template_keys: list[str] | None = None
    requested_asset_keys: list[str] | None = None
    force_revalidate: bool = False


@dataclass(frozen=True)
class PlanRunResult:
    submission_id: str
    selected_organisations: list[str]
    selected_templates: list[str]
    selected_asset_keys: list[str]
    asset_plan: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreateRunRequest:
    submission_id: str
    requested_by: str
    idempotency_key: str
    requested_organisations: list[str] | None = None
    requested_template_keys: list[str] | None = None
    requested_asset_keys: list[str] | None = None


@dataclass(frozen=True)
class CreateRunResult:
    run: RunSummary
    asset_snapshots: list[AssetSnapshot]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "asset_snapshots": [item.to_dict() for item in self.asset_snapshots],
        }


@dataclass(frozen=True)
class StageRunInputsRequest:
    run_id: str
    staged_by: str


@dataclass(frozen=True)
class StageRunInputsResult:
    run_id: str
    state: str
    staged_assets: list[StagedAsset]
    errors: list[RunStatusError]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "staged_assets": [item.to_dict() for item in self.staged_assets],
            "errors": [item.to_dict() for item in self.errors],
        }


@dataclass(frozen=True)
class TriggerRunRequest:
    run_id: str
    triggered_by: str


@dataclass(frozen=True)
class TriggerRunResult:
    run_id: str
    state: str
    pipeline: PipelineRef

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "pipeline": self.pipeline.to_dict(),
        }


@dataclass(frozen=True)
class RefreshRunStatusRequest:
    run_id: str


@dataclass(frozen=True)
class RefreshRunStatusResult:
    run_id: str
    state: str
    pipeline_status: str
    latest_error: RunStatusError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "pipeline_status": self.pipeline_status,
            "latest_error": self.latest_error.to_dict() if self.latest_error else None,
        }


@dataclass(frozen=True)
class GetRunRequest:
    run_id: str


@dataclass(frozen=True)
class GetRunResult:
    run: RunSummary
    asset_snapshots: list[AssetSnapshot]
    staged_assets: list[StagedAsset]
    pipeline: PipelineRef | None
    status_history: list[dict[str, Any]]
    result_summary: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "asset_snapshots": [item.to_dict() for item in self.asset_snapshots],
            "staged_assets": [item.to_dict() for item in self.staged_assets],
            "pipeline": self.pipeline.to_dict() if self.pipeline else None,
            "status_history": self.status_history,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class ListRunsRequest:
    submission_id: str | None = None
    state: str | None = None
    requested_by: str | None = None
    page: int = 1
    page_size: int = 25


@dataclass(frozen=True)
class ListRunsResult:
    items: list[RunSummary]
    page: int
    page_size: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
        }


@dataclass(frozen=True)
class FinalizeRunRequest:
    run_id: str
    result_payload: dict[str, Any]
    finalized_by: str
    apply_validation_flags: bool = False


@dataclass(frozen=True)
class FinalizeRunResult:
    run_id: str
    state: str
    result_summary: dict[str, Any]
    submission_update: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunEvent:
    event_id: str
    event_ts_utc: str
    event_type: str
    run_id: str
    submission_id: str
    actor: str
    payload_json: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunEvent":
        return cls(**payload)


class InMemoryRunEventStore:
    def __init__(self):
        self._events: list[RunEvent] = []

    def append(self, event: RunEvent):
        self._events.append(event)

    def list_all(self) -> list[RunEvent]:
        return list(self._events)


class JsonlRunEventStore:
    def __init__(self, events_path: str | Path):
        self._events_path = Path(events_path)
        self._events_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: RunEvent):
        with self._events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=True))
            handle.write("\n")

    def list_all(self) -> list[RunEvent]:
        if not self._events_path.exists():
            return []
        events: list[RunEvent] = []
        with self._events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                events.append(RunEvent.from_dict(json.loads(line)))
        return events


class TextBackedRunEventStore:
    def __init__(
        self,
        read_text: Callable[[], str | None],
        write_text: Callable[[str], None],
    ):
        self._read_text = read_text
        self._write_text = write_text

    def append(self, event: RunEvent):
        line = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=True)
        existing = self._read_text() or ""
        new_text = f"{existing}{line}\n" if existing else f"{line}\n"
        self._write_text(new_text)

    def list_all(self) -> list[RunEvent]:
        raw = self._read_text()
        if not raw:
            return []
        events: list[RunEvent] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            events.append(RunEvent.from_dict(json.loads(line)))
        return events


class ValidationRunApi:
    def __init__(
        self,
        submission_resolver: Callable[[str], dict[str, Any] | None],
        *,
        event_store: InMemoryRunEventStore | JsonlRunEventStore | TextBackedRunEventStore | None = None,
        idempotency_store: InMemoryIdempotencyStore | JsonFileIdempotencyStore | TextBackedIdempotencyStore | None = None,
        runs_projection_path: str | Path | None = None,
        runs_projection_store: JsonProjectionStore | TextBackedProjectionStore | None = None,
        submission_flag_setter: Callable[[str, dict[str, str] | None, dict[str, str] | None, str, str, str | None], Any] | None = None,
        asset_stager: Callable[[AssetSnapshot, str], StagedAsset | dict[str, Any]] | None = None,
        pipeline_trigger: Callable[[dict[str, Any]], PipelineRef | dict[str, Any]] | None = None,
        pipeline_status_resolver: Callable[[PipelineRef, dict[str, Any]], dict[str, Any] | str] | None = None,
        pipeline_id: str = "in-memory-pipeline",
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
    ):
        self._submission_resolver = submission_resolver
        self._events = event_store or InMemoryRunEventStore()
        self._idempotency = idempotency_store or InMemoryIdempotencyStore()
        self._runs_projection_store = runs_projection_store
        if not self._runs_projection_store and runs_projection_path:
            self._runs_projection_store = JsonProjectionStore(runs_projection_path)
        self._submission_flag_setter = submission_flag_setter
        self._asset_stager = asset_stager
        self._pipeline_trigger = pipeline_trigger
        self._pipeline_status_resolver = pipeline_status_resolver
        self._pipeline_id = pipeline_id
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._id_fn = id_fn or (lambda: uuid4().hex)

    def plan_run(self, request: PlanRunRequest) -> PlanRunResult:
        submission = self._require_submission(request.submission_id)
        selected_orgs, selected_templates, asset_rows = self._resolve_scope(
            submission=submission,
            requested_organisations=request.requested_organisations,
            requested_template_keys=request.requested_template_keys,
            requested_asset_keys=request.requested_asset_keys,
        )
        asset_plan = [
            {
                "asset_key": row["asset_key"],
                "status": self._asset_plan_status(row, request.force_revalidate),
            }
            for row in asset_rows
        ]
        return PlanRunResult(
            submission_id=request.submission_id,
            selected_organisations=selected_orgs,
            selected_templates=selected_templates,
            selected_asset_keys=[row["asset_key"] for row in asset_rows],
            asset_plan=asset_plan,
            warnings=[],
        )

    def create_run(self, request: CreateRunRequest) -> CreateRunResult:
        payload = {
            "submission_id": request.submission_id,
            "requested_by": request.requested_by,
            "requested_organisations": request.requested_organisations,
            "requested_template_keys": request.requested_template_keys,
            "requested_asset_keys": request.requested_asset_keys,
        }
        cached = self._get_idempotent("create_run", request.requested_by, request.idempotency_key, payload)
        if cached is not None:
            return self._to_create_run_result(self._require_run_projection(str(cached["run_id"])))

        submission = self._require_submission(request.submission_id)
        selected_orgs, selected_templates, asset_rows = self._resolve_scope(
            submission=submission,
            requested_organisations=request.requested_organisations,
            requested_template_keys=request.requested_template_keys,
            requested_asset_keys=request.requested_asset_keys,
        )
        run_id = self._id_fn()
        now_iso = self._utc_now_iso()
        run = RunSummary(
            run_id=run_id,
            submission_id=request.submission_id,
            process_cd=str(submission["process_cd"]),
            submission_period_cd=str(submission["submission_period_cd"]),
            requested_by=request.requested_by,
            requested_ts_utc=now_iso,
            state="queued",
            selected_organisations=selected_orgs,
            selected_templates=selected_templates,
            selected_asset_keys=[row["asset_key"] for row in asset_rows],
        )
        asset_snapshots = [self._to_asset_snapshot(row) for row in asset_rows]
        self._append_event(
            "run_created",
            run_id=run_id,
            submission_id=request.submission_id,
            actor=request.requested_by,
            payload_json={
                "run": run.to_dict(),
                "asset_snapshots": [item.to_dict() for item in asset_snapshots],
                "status_entry": {
                    "ts_utc": now_iso,
                    "state": "queued",
                    "actor": request.requested_by,
                },
            },
        )
        self._write_projection_file()
        self._store_idempotency(
            "create_run",
            request.requested_by,
            request.idempotency_key,
            payload,
            {"run_id": run_id},
        )
        return self._to_create_run_result(self._require_run_projection(run_id))

    def stage_run_inputs(self, request: StageRunInputsRequest) -> StageRunInputsResult:
        projection = self._require_run_projection(request.run_id)
        staged_assets: list[StagedAsset] = []
        errors: list[RunStatusError] = []
        for snapshot_payload in projection["asset_snapshots"]:
            snapshot = AssetSnapshot.from_dict(snapshot_payload)
            try:
                staged_asset = self._stage_asset(snapshot, request.run_id)
                staged_assets.append(staged_asset)
                self._append_event(
                    "asset_staged",
                    run_id=request.run_id,
                    submission_id=projection["run"]["submission_id"],
                    actor=request.staged_by,
                    payload_json={"staged_asset": staged_asset.to_dict()},
                )
            except Exception as exc:
                error = RunStatusError(code="STAGING_FAILED", message=str(exc))
                errors.append(error)
                self._append_event(
                    "asset_stage_failed",
                    run_id=request.run_id,
                    submission_id=projection["run"]["submission_id"],
                    actor=request.staged_by,
                    payload_json={
                        "asset_key": snapshot.asset_key,
                        "path": snapshot.source.path,
                        "error": error.to_dict(),
                    },
                )
        next_state = "staged" if not errors else ("partially_succeeded" if staged_assets else "failed")
        self._append_state_event(
            event_type="run_staging_completed",
            run_id=request.run_id,
            submission_id=projection["run"]["submission_id"],
            actor=request.staged_by,
            state=next_state,
            payload_json={"errors": [item.to_dict() for item in errors]},
        )
        self._write_projection_file()
        updated = self._require_run_projection(request.run_id)
        return StageRunInputsResult(
            run_id=request.run_id,
            state=updated["run"]["state"],
            staged_assets=[StagedAsset.from_dict(item) for item in updated["staged_assets"]],
            errors=[RunStatusError.from_dict(item) for item in updated["latest_errors"]],
        )

    def trigger_run(self, request: TriggerRunRequest) -> TriggerRunResult:
        projection = self._require_run_projection(request.run_id)
        if projection["run"]["state"] not in {"staged", "partially_succeeded"}:
            raise ValueError(f"Run '{request.run_id}' is not ready to trigger.")
        pipeline = self._trigger_pipeline(projection)
        self._append_state_event(
            event_type="pipeline_triggered",
            run_id=request.run_id,
            submission_id=projection["run"]["submission_id"],
            actor=request.triggered_by,
            state="running",
            payload_json={"pipeline": pipeline.to_dict()},
        )
        self._write_projection_file()
        updated = self._require_run_projection(request.run_id)
        return TriggerRunResult(
            run_id=request.run_id,
            state=updated["run"]["state"],
            pipeline=PipelineRef.from_dict(updated["pipeline"]),
        )

    def refresh_run_status(self, request: RefreshRunStatusRequest) -> RefreshRunStatusResult:
        projection = self._require_run_projection(request.run_id)
        pipeline_payload = projection.get("pipeline")
        if not pipeline_payload:
            latest_error = projection["latest_errors"][-1] if projection["latest_errors"] else None
            return RefreshRunStatusResult(
                run_id=request.run_id,
                state=projection["run"]["state"],
                pipeline_status=projection["run"]["state"],
                latest_error=RunStatusError.from_dict(latest_error) if latest_error else None,
            )

        pipeline = PipelineRef.from_dict(pipeline_payload)
        observed = self._resolve_pipeline_status(pipeline, projection)
        observed_status = str(observed.get("status") or projection["run"]["state"]).strip().lower()
        next_state = self._map_pipeline_status_to_run_state(observed_status, projection["run"]["state"])
        self._append_state_event(
            event_type="status_polled",
            run_id=request.run_id,
            submission_id=projection["run"]["submission_id"],
            actor="system",
            state=next_state,
            payload_json={
                "pipeline_status": observed_status,
                "error": observed.get("error"),
            },
        )
        self._write_projection_file()
        updated = self._require_run_projection(request.run_id)
        latest_error = updated["latest_errors"][-1] if updated["latest_errors"] else None
        return RefreshRunStatusResult(
            run_id=request.run_id,
            state=updated["run"]["state"],
            pipeline_status=observed_status,
            latest_error=RunStatusError.from_dict(latest_error) if latest_error else None,
        )

    def get_run(self, request: GetRunRequest) -> GetRunResult:
        return self._to_get_run_result(self._require_run_projection(request.run_id))

    def list_runs(self, request: ListRunsRequest) -> ListRunsResult:
        items = [
            RunSummary.from_dict(item["run"])
            for item in self._project_runs()
            if (request.submission_id is None or item["run"]["submission_id"] == request.submission_id)
            and (request.state is None or item["run"]["state"] == request.state)
            and (request.requested_by is None or item["run"]["requested_by"] == request.requested_by)
        ]
        start = max(request.page - 1, 0) * request.page_size
        end = start + request.page_size
        return ListRunsResult(items=items[start:end], page=request.page, page_size=request.page_size, total=len(items))

    def finalize_run(self, request: FinalizeRunRequest) -> FinalizeRunResult:
        projection = self._require_run_projection(request.run_id)
        asset_results = request.result_payload.get("asset_results", [])
        passed_asset_keys = sorted(str(item["asset_key"]) for item in asset_results if item.get("status") == "passed")
        failed_asset_keys = sorted(str(item["asset_key"]) for item in asset_results if item.get("status") != "passed")
        state = "succeeded" if not failed_asset_keys else ("partially_succeeded" if passed_asset_keys else "failed")
        result_summary = {
            "passed_asset_keys": passed_asset_keys,
            "failed_asset_keys": failed_asset_keys,
        }
        self._append_state_event(
            event_type="run_finalized",
            run_id=request.run_id,
            submission_id=projection["run"]["submission_id"],
            actor=request.finalized_by,
            state=state,
            payload_json={
                "result_summary": result_summary,
                "result_payload": request.result_payload,
            },
        )

        submission_update = None
        if request.apply_validation_flags and self._submission_flag_setter is not None:
            organisation_changes, template_changes = self._build_submission_flag_changes(
                projection,
                passed_asset_keys=passed_asset_keys,
                failed_asset_keys=failed_asset_keys,
            )
            response = self._submission_flag_setter(
                projection["run"]["submission_id"],
                organisation_changes or None,
                template_changes or None,
                request.finalized_by,
                f"finalize-run-{request.run_id}-{self._hash_payload(result_summary)}",
                f"Finalize validation run {request.run_id}",
            )
            if hasattr(response, "ok") and not getattr(response, "ok"):
                error = getattr(response, "error", None)
                raise ValueError(getattr(error, "message", "Failed to apply validation flags."))
            submission_data = getattr(response, "data", None) if response is not None else None
            if submission_data:
                submission_update = {
                    "submission_id": str(submission_data["submission_id"]),
                    "state": str(submission_data["state"]),
                }

        self._write_projection_file()
        updated = self._require_run_projection(request.run_id)
        return FinalizeRunResult(
            run_id=request.run_id,
            state=updated["run"]["state"],
            result_summary=updated["result_summary"] or result_summary,
            submission_update=submission_update,
        )

    def _require_submission(self, submission_id: str) -> dict[str, Any]:
        submission = self._submission_resolver(submission_id)
        if submission is None:
            raise ValueError(f"Submission '{submission_id}' was not found.")
        return submission

    def _project_runs(self) -> list[dict[str, Any]]:
        by_run: dict[str, dict[str, Any]] = {}
        for event in sorted(self._events.list_all(), key=lambda item: (item.event_ts_utc, item.event_id)):
            payload = event.payload_json or {}
            run = by_run.setdefault(
                event.run_id,
                {
                    "run": None,
                    "asset_snapshots": [],
                    "staged_assets": [],
                    "pipeline": None,
                    "status_history": [],
                    "result_summary": None,
                    "latest_errors": [],
                },
            )
            if event.event_type == "run_created":
                run["run"] = payload["run"]
                run["asset_snapshots"] = payload.get("asset_snapshots", [])
                status_entry = payload.get("status_entry")
                if status_entry:
                    run["status_history"].append(status_entry)
                continue
            if run["run"] is None:
                continue
            if event.event_type == "asset_staged":
                staged_asset = payload["staged_asset"]
                run["staged_assets"] = [item for item in run["staged_assets"] if item["asset_key"] != staged_asset["asset_key"]]
                run["staged_assets"].append(staged_asset)
            elif event.event_type == "asset_stage_failed":
                if payload.get("error"):
                    run["latest_errors"].append(payload["error"])
            elif event.event_type in {"run_staging_completed", "pipeline_triggered", "status_polled", "run_finalized"}:
                run["run"] = {**run["run"], "state": str(payload.get("state") or run["run"]["state"])}
                status_entry = payload.get("status_entry")
                if status_entry:
                    run["status_history"].append(status_entry)
                if event.event_type == "pipeline_triggered":
                    run["pipeline"] = payload.get("pipeline")
                if event.event_type == "run_staging_completed":
                    run["latest_errors"] = payload.get("errors", run["latest_errors"])
                elif payload.get("error"):
                    run["latest_errors"].append(payload["error"])
                if event.event_type == "run_finalized":
                    run["result_summary"] = payload.get("result_summary")

        projected = [item for item in by_run.values() if item["run"] is not None]
        for item in projected:
            item["staged_assets"] = sorted(item["staged_assets"], key=lambda staged: staged["asset_key"])
        projected.sort(key=lambda item: item["run"]["requested_ts_utc"])
        return projected

    def _require_run_projection(self, run_id: str) -> dict[str, Any]:
        for item in self._project_runs():
            if item["run"]["run_id"] == run_id:
                return item
        raise ValueError(f"Run '{run_id}' was not found.")

    def _write_projection_file(self):
        if self._runs_projection_store is None:
            return
        self._runs_projection_store.write(
            {
                "updated_ts_utc": self._utc_now_iso(),
                "items": self._project_runs(),
            }
        )

    def _append_event(
        self,
        event_type: str,
        *,
        run_id: str,
        submission_id: str,
        actor: str,
        payload_json: dict[str, Any] | None = None,
    ):
        self._events.append(
            RunEvent(
                event_id=self._id_fn(),
                event_ts_utc=self._utc_now_iso(),
                event_type=event_type,
                run_id=run_id,
                submission_id=submission_id,
                actor=actor,
                payload_json=payload_json,
            )
        )

    def _append_state_event(
        self,
        *,
        event_type: str,
        run_id: str,
        submission_id: str,
        actor: str,
        state: str,
        payload_json: dict[str, Any] | None = None,
    ):
        payload = dict(payload_json or {})
        payload["state"] = state
        payload["status_entry"] = {
            "ts_utc": self._utc_now_iso(),
            "state": state,
            "actor": actor,
        }
        self._append_event(
            event_type,
            run_id=run_id,
            submission_id=submission_id,
            actor=actor,
            payload_json=payload,
        )

    def _stage_asset(self, snapshot: AssetSnapshot, run_id: str) -> StagedAsset:
        if self._asset_stager is None:
            return StagedAsset(
                asset_key=snapshot.asset_key,
                path=snapshot.source.path,
                staged_path=f"Files/validation-service/runs/{run_id}/{snapshot.asset_key}/{snapshot.source.path}",
                byte_count=len(snapshot.source.path.encode("utf-8")),
            )
        staged = self._asset_stager(snapshot, run_id)
        return staged if isinstance(staged, StagedAsset) else StagedAsset.from_dict(staged)

    def _trigger_pipeline(self, projection: dict[str, Any]) -> PipelineRef:
        if self._pipeline_trigger is None:
            return PipelineRef(
                pipeline_id=self._pipeline_id,
                pipeline_run_id=self._id_fn(),
                triggered_ts_utc=self._utc_now_iso(),
            )
        payload = {
            "run": projection["run"],
            "asset_snapshots": projection["asset_snapshots"],
            "staged_assets": projection["staged_assets"],
            "status_history": projection["status_history"],
        }
        triggered = self._pipeline_trigger(payload)
        return triggered if isinstance(triggered, PipelineRef) else PipelineRef.from_dict(triggered)

    def _resolve_pipeline_status(self, pipeline: PipelineRef, projection: dict[str, Any]) -> dict[str, Any]:
        if self._pipeline_status_resolver is None:
            return {"status": projection["run"]["state"]}
        observed = self._pipeline_status_resolver(pipeline, projection)
        return {"status": observed} if isinstance(observed, str) else observed

    def _to_create_run_result(self, projection: dict[str, Any]) -> CreateRunResult:
        return CreateRunResult(
            run=RunSummary.from_dict(projection["run"]),
            asset_snapshots=[AssetSnapshot.from_dict(item) for item in projection["asset_snapshots"]],
        )

    def _to_get_run_result(self, projection: dict[str, Any]) -> GetRunResult:
        return GetRunResult(
            run=RunSummary.from_dict(projection["run"]),
            asset_snapshots=[AssetSnapshot.from_dict(item) for item in projection["asset_snapshots"]],
            staged_assets=[StagedAsset.from_dict(item) for item in projection["staged_assets"]],
            pipeline=PipelineRef.from_dict(projection["pipeline"]) if projection["pipeline"] else None,
            status_history=projection["status_history"],
            result_summary=projection["result_summary"],
        )

    def _get_idempotent(
        self,
        operation: str,
        actor: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        entry = self._idempotency.get(operation, actor, idempotency_key)
        if not entry:
            return None
        cached_hash, cached_response = entry
        if cached_hash != self._hash_payload(payload):
            raise ValueError("Idempotency key was already used with a different payload.")
        return cached_response.data if hasattr(cached_response, "data") else None

    def _store_idempotency(
        self,
        operation: str,
        actor: str,
        idempotency_key: str,
        payload: dict[str, Any],
        response_payload: dict[str, Any],
    ):
        self._idempotency.set(
            operation,
            actor,
            idempotency_key,
            self._hash_payload(payload),
            _IdempotencyResponse(data=response_payload),
        )

    def _resolve_scope(
        self,
        submission: dict[str, Any],
        requested_organisations: list[str] | None,
        requested_template_keys: list[str] | None,
        requested_asset_keys: list[str] | None,
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        selected_orgs = [item.strip().upper() for item in (requested_organisations or submission["organisations"].keys())]
        selected_templates = [item.strip().upper() for item in (requested_template_keys or [])]
        known_orgs = set(submission["organisations"].keys())
        for org_cd in selected_orgs:
            if org_cd not in known_orgs:
                raise ValueError(f"Organisation '{org_cd}' does not exist in submission.")

        asset_rows: list[dict[str, Any]] = []
        for org_cd in selected_orgs:
            org_payload = submission["organisations"][org_cd]
            for tracked_file in org_payload["file"]["tracked_files"]:
                asset_rows.append(
                    self._build_asset_row(
                        asset_key=f"{org_cd}_FILE::{tracked_file['path']}",
                        asset_type="organisation_input",
                        organisation_cds=[org_cd],
                        source=org_payload["file"],
                        tracked_file=tracked_file,
                    )
                )
            for template_key in org_payload.get("template_keys", []):
                if template_key not in selected_templates:
                    selected_templates.append(template_key)

        known_templates = set(submission.get("templates", {}).keys())
        for template_key in list(selected_templates):
            if template_key not in known_templates:
                raise ValueError(f"Template '{template_key}' does not exist in submission.")
            template_payload = submission["templates"][template_key]
            for tracked_file in template_payload["file"]["tracked_files"]:
                asset_rows.append(
                    self._build_asset_row(
                        asset_key=f"{template_key}::{tracked_file['path']}",
                        asset_type="template",
                        organisation_cds=template_payload.get("applies_to", []),
                        source=template_payload["file"],
                        tracked_file=tracked_file,
                    )
                )

        if requested_asset_keys:
            allowed = {item.strip().upper() for item in requested_asset_keys}
            asset_rows = [row for row in asset_rows if row["asset_key"].upper() in allowed]

        deduped: dict[str, dict[str, Any]] = {}
        for row in asset_rows:
            deduped[row["asset_key"]] = row
        return selected_orgs, selected_templates, list(deduped.values())

    @staticmethod
    def _build_asset_row(
        asset_key: str,
        asset_type: str,
        organisation_cds: list[str],
        source: dict[str, Any],
        tracked_file: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "asset_key": asset_key,
            "asset_type": asset_type,
            "organisation_cds": [item.strip().upper() for item in organisation_cds],
            "source": {
                "source_type": source["source_type"],
                "target_name": source["target_name"],
                "root_path": source["root_path"],
                "path": tracked_file["path"],
                "folder_url": source.get("folder_url"),
            },
            "hash_snapshot": {
                "current_hash": tracked_file.get("current_hash"),
                "validated_hash": tracked_file.get("validated_hash"),
            },
            "metadata_snapshot": {
                "created": tracked_file.get("created"),
                "modified": tracked_file.get("modified"),
                "modified_by": tracked_file.get("modified_by"),
            },
        }

    @staticmethod
    def _asset_plan_status(asset_row: dict[str, Any], force_revalidate: bool) -> str:
        if force_revalidate:
            return "selected_for_revalidation"
        current_hash = asset_row["hash_snapshot"].get("current_hash")
        validated_hash = asset_row["hash_snapshot"].get("validated_hash")
        if not current_hash:
            return "missing"
        if validated_hash and current_hash == validated_hash:
            return "already_validated"
        if validated_hash and current_hash != validated_hash:
            return "changed_since_validation"
        return "not_yet_validated"

    @staticmethod
    def _to_asset_snapshot(asset_row: dict[str, Any]) -> AssetSnapshot:
        return AssetSnapshot(
            asset_key=asset_row["asset_key"],
            asset_type=asset_row["asset_type"],
            organisation_cds=asset_row["organisation_cds"],
            source=AssetSourceRef(**asset_row["source"]),
            hash_snapshot=AssetHashSnapshot(**asset_row["hash_snapshot"]),
            metadata_snapshot=AssetMetadataSnapshot(**asset_row["metadata_snapshot"]),
            status="ready_to_run",
        )

    @staticmethod
    def _map_pipeline_status_to_run_state(pipeline_status: str, current_state: str) -> str:
        if pipeline_status in {"queued", "notstarted"}:
            return "queued"
        if pipeline_status in {"inprogress", "running"}:
            return "running"
        if pipeline_status == "succeeded":
            return "succeeded"
        if pipeline_status in {"failed", "error"}:
            return "failed"
        if pipeline_status == "cancelled":
            return "cancelled"
        return current_state

    @staticmethod
    def _build_submission_flag_changes(
        projection: dict[str, Any],
        *,
        passed_asset_keys: list[str],
        failed_asset_keys: list[str],
    ) -> tuple[dict[str, str], dict[str, str]]:
        passed = set(passed_asset_keys)
        failed = set(failed_asset_keys)
        organisation_assets: dict[str, list[str]] = {}
        template_assets: dict[str, list[str]] = {}
        for asset_payload in projection["asset_snapshots"]:
            asset_key = str(asset_payload["asset_key"])
            if str(asset_payload["asset_type"]) == "organisation_input":
                for org_cd in asset_payload.get("organisation_cds", []):
                    organisation_assets.setdefault(str(org_cd).strip().upper(), []).append(asset_key)
            else:
                template_key = asset_key.split("::", 1)[0].strip().upper()
                template_assets.setdefault(template_key, []).append(asset_key)

        organisation_changes: dict[str, str] = {}
        for org_cd, asset_keys in organisation_assets.items():
            if any(asset_key in failed for asset_key in asset_keys):
                organisation_changes[org_cd] = VALIDATION_NOT_VALIDATED
            elif asset_keys and all(asset_key in passed for asset_key in asset_keys):
                organisation_changes[org_cd] = VALIDATION_VALIDATED

        template_changes: dict[str, str] = {}
        for template_key, asset_keys in template_assets.items():
            if any(asset_key in failed for asset_key in asset_keys):
                template_changes[template_key] = VALIDATION_NOT_VALIDATED
            elif asset_keys and all(asset_key in passed for asset_key in asset_keys):
                template_changes[template_key] = VALIDATION_VALIDATED
        return organisation_changes, template_changes

    @staticmethod
    def _hash_payload(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _utc_now_iso(self) -> str:
        return self._now_fn().astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class _IdempotencyResponse:
    data: dict[str, Any]
    ok: bool = True
    error: None = None
    correlation_id: str = "validation-run-api"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "correlation_id": self.correlation_id,
        }


class InMemoryValidationRunApi(ValidationRunApi):
    """Small in-memory implementation retained for tests and smoke-driving."""

    def __init__(
        self,
        submission_resolver: Callable[[str], dict[str, Any] | None],
        pipeline_id: str = "in-memory-pipeline",
        asset_stager: Callable[[AssetSnapshot, str], StagedAsset | dict[str, Any]] | None = None,
        pipeline_trigger: Callable[[dict[str, Any]], PipelineRef | dict[str, Any]] | None = None,
        pipeline_status_resolver: Callable[[PipelineRef, dict[str, Any]], dict[str, Any] | str] | None = None,
        submission_flag_setter: Callable[[str, dict[str, str] | None, dict[str, str] | None, str, str, str | None], Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
    ):
        super().__init__(
            submission_resolver,
            event_store=InMemoryRunEventStore(),
            idempotency_store=InMemoryIdempotencyStore(),
            submission_flag_setter=submission_flag_setter,
            asset_stager=asset_stager,
            pipeline_trigger=pipeline_trigger,
            pipeline_status_resolver=pipeline_status_resolver,
            pipeline_id=pipeline_id,
            now_fn=now_fn,
            id_fn=id_fn,
        )
