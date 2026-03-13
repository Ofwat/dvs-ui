# Data Validation API Plan and Requirements (MS Fabric)

## 1. Goal
Build a backend-first `data-validation-api` service that manages validation submissions in Microsoft Fabric, executes validation runs per submission, and provides auditable state/history.

The service has two stages:

1. Setup of a validation submission.
2. Running and tracking individual validation runs within a submission.

## 2. Scope

### In scope (v1)
- Read submission/runs from append-only event stores in Fabric and return current projected state.
- Create a new submission with metadata, organisation participation, and tracked validation assets.
- Start a validation run for selected organisations in an existing submission.
- Track Fabric pipeline run status/errors for launched runs.
- Update validation flags and submission details using append-only history rows (audit trail).

### Out of scope (v1)
- GUI implementation details.
- Complex workflow orchestration beyond one configured pipeline.
- Real-time push notifications.

## 3. High-Level Design

## 3.1 Service objects
Split the backend into two interfaces:

1. `SubmissionServiceApi`
- owns submission structure and submission state
- create/edit/list submissions
- refresh hashes
- apply validation flags

2. `ValidationRunApi`
- owns validation execution lifecycle
- plan/create/stage/trigger/poll/finalize runs
- reads submission state but does not own submission editing

Example constructor parameters:
- `workspace_id: str`
- `lakehouse_id: str`
- `submissions_events_path: str` (Fabric `Files/...` path to submission events JSONL)
- `runs_events_path: str` (Fabric `Files/...` path to run events JSONL)
- `submissions_projection_path: str` (Fabric `Files/...` path to current submissions JSON)
- `runs_projection_path: str` (Fabric `Files/...` path to current runs JSON)
- `fabric_temp_folder: str` (staging folder in Fabric)
- `pipeline_id: str` (or pipeline name)
- `sharepoint_site_config: dict`
- `token_provider/auth_manager` (existing token manager service)

## 3.2 Storage model
Use append-only records for auditability.

Recommended logical data layers:

1. Event store files (`JSONL` in `Files/...`)
- `Files/validation-service/events/submission_events.jsonl`
- `Files/validation-service/events/run_events.jsonl`
- These are append-only and are the source of truth.

2. Read projections (`JSON` in `Files/...`)
- `Files/validation-service/projections/submissions_current.json`
- `Files/validation-service/projections/runs_current.json`
- Optional per-submission snapshots under `Files/validation-service/projections/submissions/<submission_id>.json`
- Built from events for fast API/UI reads.

3. Immutable archive (OneLake/ADLS path)
- Periodic export/snapshot of event data to immutable storage.
- Used for long-term audit/compliance and disaster recovery.

This preserves full history and supports audits without in-place mutation.

Storage decision for v1:
- Canonical source of truth is append-only `JSONL` event files in `Files/...`.
- GUI/API reads should use `JSON` projections derived from events.
- The UI must not reconstruct state from raw event logs.
- Use controlled retention and archival process to keep long-term history.

Retention/operations policy for v1:
- No hard-delete from event files in normal operation.
- Event logs are append-only; projections are replaceable/rebuildable.
- Archive job must run before any retention-compacting maintenance.
- Archive payload must include full event rows plus export timestamp/checksum.

## 4. Domain Model

## 4.1 IDs and keys
- `submission_id`: unique stable ID (UUIDv7 recommended).
- `run_id`: unique ID for each pipeline launch request.
- `organisation_cd`: organisation key.
- `sharepoint_source_ref`: structured source reference supporting both SharePoint drives and groups.
- `asset_id`: unique ID for a tracked submission asset.
- `asset_key`: stable logical key within a submission.
- Submission identity fields:
  - `process_cd`
  - `submission_period_cd`

## 4.2 Submission structure
Treat a submission as two separate concepts:

1. Participants
- Organisations/companies that belong to the submission.
- These carry business identity and derived validation state.

2. Assets
- Files that are tracked and validated as part of the submission.
- Assets are first-class records. They must not be stored only under organisations.
- Asset types for v1:
  - `organisation_input`
  - `template`

This supports:
- one-company-one-template
- one-template-many-companies
- one template shared by the full submission

Recommended current-state projection:
- `submission`
  - submission metadata
  - `organisations: dict[organisation_cd, organisation_projection]`
  - `assets: dict[asset_key, asset_projection]`

Organisation projection:
- `organisation_cd`
- `validation_flag`
- `asset_keys: list[str]`

Asset projection:
- `asset_id`
- `asset_key`
- `asset_type: organisation_input | template`
- `label`
- `sharepoint_source`
- `applies_to`
  - `all_organisations: bool`
  - `organisation_cds: list[str]`
- `validation_flag`
- `current_hash`
- `validated_hash`
- `created`
- `modified`
- `modified_by`

Design rule:
- organisation membership and file assets evolve independently.
- a shared template is represented once and referenced by multiple organisations.

## 4.3 SharePoint source reference
Canonical shape:
- `source_type`: `drive | group`
- `target_name`: SharePoint drive name or Microsoft 365 group name
- `root_path`: root folder path within that source
- `tracked_files`: one or more file paths relative to the root path

## 4.4 Validation scope rules
Validation is per asset, with derived organisation and submission state.

Rules:
- Every tracked asset has its own `validation_flag`.
- `organisation_input` assets usually apply to a single organisation.
- `template` assets may apply to one organisation, multiple organisations, or all organisations.
- An organisation is considered `validated` when all assets that apply to it are `validated`.
- A submission is considered `validated` when all required assets in the submission are `validated`.
- If a shared template changes, all organisations that depend on it become effectively `not_validated` until that template is revalidated.

## 4.5 Submission lifecycle state
Submission-level state:
- `ready`
- `in_progress`
- `partially_validated`
- `validated`
- `failed`

Organisation-level validation flag:
- `not_validated`
- `validated`

State transition rules:
- `ready -> in_progress` when a run is started.
- `in_progress -> failed` if latest run fails and no successful run supersedes it.
- `in_progress -> partially_validated` if run succeeds but at least one organisation remains `not_validated`.
- `in_progress -> validated` if run succeeds and all organisations are `validated`.
- `partially_validated -> in_progress` when a new run starts.
- `failed -> in_progress` when a new run starts.
- `validated -> in_progress` when a new run starts for changed assets/organisations.

## 4.6 Validation run lifecycle state
Run-level state:
- `planned`
- `queued`
- `staging`
- `staged`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `partially_succeeded`

Rules:
- A run always operates on a frozen snapshot of asset hashes captured at run creation time.
- SharePoint changes after run creation must not change the meaning of that run.
- `planned -> queued` when the run record is created and accepted for execution.
- `queued -> staging` when file copy into Fabric begins.
- `staging -> staged` when all selected assets are copied successfully.
- `staging -> failed` if required staging fails and no retry succeeds.
- `staged -> running` when Fabric pipeline/notebook execution starts.
- `running -> succeeded` when execution completes successfully for all required assets.
- `running -> partially_succeeded` when execution completes but only part of the selected scope succeeded.
- `running -> failed` when execution crashes or returns a failure state.
- `running -> cancelled` when the run is cancelled externally or by user action.

## 5. Public API (v1)

All public methods return a common envelope:
- `ok: bool`
- `data: object | null`
- `error: { code: str, message: str, details?: object } | null`
- `correlation_id: str`

## 5.1 `list_submissions()`
Reads Fabric submission registry and returns existing submissions with computed current state.

Inputs:
- Optional filters (`process_cd`, `submission_period_cd`, `state`, `created_by`).

Outputs:
- List of current submission projections:
  - `submission_id`
  - `process_cd`
  - `submission_period_cd`
  - per-organisation status
  - tracked asset summary
  - created metadata
  - current state summary

## 5.2 `create_submission(...)`
Creates a new submission.

Inputs:
- `process_cd: str`
- `submission_period_cd: str`
- `organisations: list[str] | dict[str, organisation_submission_ref]`
- `templates: dict[str, template_submission_ref]`
- `created_by: str`
- `idempotency_key: str` (required)

Behavior:
- Validate required fields, non-empty organisation set, and valid template map.
- Generate `submission_id`.
- Write append-only creation event(s) with:
  - creator username
  - created timestamp (UTC ISO-8601)
  - participant organisations
  - tracked templates
  - initial template flag `not_validated`
  - submission state initialized (recommended `ready`)
- Enforce duplicate policy:
  - v1 default: reject create if an active submission exists for `(process_cd, submission_period_cd)` where state is not terminal (`validated` or `failed`) unless `allow_duplicate_active=true`.

Outputs:
- Created submission summary including `submission_id`.

## 5.3 `ValidationRunApi`
Validation runs must be exposed through a dedicated interface rather than folded into submission editing methods.

### 5.3.0 Shared run object shapes
Canonical run summary:
```json
{
  "run_id": "run_01",
  "submission_id": "sub_01",
  "requested_by": "user@example.com",
  "requested_ts_utc": "2026-03-09T12:00:00+00:00",
  "state": "queued",
  "selected_organisations": ["ORG001"],
  "selected_templates": ["TPL_MAIN"],
  "selected_asset_keys": ["ORG001_FILE", "TPL_MAIN"]
}
```

Canonical asset snapshot:
```json
{
  "asset_key": "TPL_MAIN",
  "asset_type": "template",
  "organisation_cds": ["ORG001"],
  "source": {
    "source_type": "drive",
    "target_name": "Documents",
    "root_path": "Shared Documents/Validation/Templates",
    "path": "main.xlsx"
  },
  "hash_snapshot": {
    "current_hash": "abc123",
    "validated_hash": null
  },
  "metadata_snapshot": {
    "created": "2026-03-01T10:00:00+00:00",
    "modified": "2026-03-05T11:00:00+00:00",
    "modified_by": "user@example.com"
  },
  "status": "ready_to_run"
}
```

Canonical staged asset:
```json
{
  "asset_key": "TPL_MAIN",
  "path": "main.xlsx",
  "staged_path": "Files/validation-service/runs/run_01/ORG001/templates/main.xlsx",
  "byte_count": 1024
}
```

### 5.3.1 `plan_run(...)`
Builds an execution plan without writing state.

Inputs:
- `submission_id: str`
- `requested_organisations: list[str] | null`
- `requested_template_keys: list[str] | null`
- `requested_asset_keys: list[str] | null`
- `force_revalidate: bool` (default `false`)

Behavior:
- Validate submission exists.
- Resolve the effective asset set.
- Include shared templates that apply to selected organisations.
- Determine for each selected asset:
  - current hash
  - validated hash
  - already validated / changed since validation / missing
- Return warnings for invalid or incomplete scope.

Outputs:
- effective organisation list
- effective template list
- effective asset list
- per-asset validation readiness summary
- plan warnings

Request shape:
```json
{
  "submission_id": "sub_01",
  "requested_organisations": ["ORG001"],
  "requested_template_keys": null,
  "requested_asset_keys": null,
  "force_revalidate": false
}
```

Response `data` shape:
```json
{
  "submission_id": "sub_01",
  "selected_organisations": ["ORG001"],
  "selected_templates": ["TPL_MAIN"],
  "selected_asset_keys": ["ORG001_FILE", "TPL_MAIN"],
  "asset_plan": [
    {
      "asset_key": "ORG001_FILE",
      "status": "changed_since_validation"
    },
    {
      "asset_key": "TPL_MAIN",
      "status": "already_validated"
    }
  ],
  "warnings": []
}
```

### 5.3.2 `create_run(...)`
Creates a run record and freezes the execution snapshot.

Inputs:
- `submission_id: str`
- `requested_organisations: list[str] | null`
- `requested_template_keys: list[str] | null`
- `requested_asset_keys: list[str] | null`
- `requested_by: str`
- `idempotency_key: str`

Behavior:
- Validate submission exists.
- Resolve the effective asset set.
- Capture frozen snapshot metadata for each selected asset:
  - source path
  - current hash at run creation
  - SharePoint timestamps / modifier where available
- Persist run creation event(s).
- Initialize run state as `planned` or `queued`.

Outputs:
- `run_id`
- frozen asset snapshot
- initial run state

Request shape:
```json
{
  "submission_id": "sub_01",
  "requested_organisations": ["ORG001"],
  "requested_template_keys": null,
  "requested_asset_keys": null,
  "requested_by": "user@example.com",
  "idempotency_key": "create-run-01"
}
```

Response `data` shape:
```json
{
  "run": {
    "run_id": "run_01",
    "submission_id": "sub_01",
    "requested_by": "user@example.com",
    "requested_ts_utc": "2026-03-09T12:00:00+00:00",
    "state": "queued",
    "selected_organisations": ["ORG001"],
    "selected_templates": ["TPL_MAIN"],
    "selected_asset_keys": ["ORG001_FILE", "TPL_MAIN"]
  },
  "asset_snapshots": []
}
```

### 5.3.3 `stage_run_inputs(...)`
Copies selected source files into Fabric staging.

Inputs:
- `run_id: str`
- `staged_by: str`

Behavior:
- Read frozen asset snapshot for the run.
- Copy each selected file from SharePoint into configured Fabric temp folder.
- Persist staged file metadata:
  - staged path
  - byte count
  - asset key
- Mark per-asset staging success/failure.

Outputs:
- `run_id`
- staged assets
- staging errors if any
- updated run state

Request shape:
```json
{
  "run_id": "run_01",
  "staged_by": "user@example.com"
}
```

Response `data` shape:
```json
{
  "run_id": "run_01",
  "state": "staged",
  "staged_assets": [],
  "errors": []
}
```

### 5.3.4 `trigger_run(...)`
Triggers the configured Fabric pipeline/notebook for a staged run.

Inputs:
- `run_id: str`
- `triggered_by: str`

Behavior:
- Validate run exists and is in `staged` or retryable state.
- Build pipeline payload from frozen run snapshot and staged paths.
- Trigger Fabric pipeline.
- Persist pipeline reference:
  - pipeline id/name
  - pipeline run id
  - trigger timestamp

Outputs:
- `run_id`
- pipeline run identifier
- updated run state

Request shape:
```json
{
  "run_id": "run_01",
  "triggered_by": "user@example.com"
}
```

Response `data` shape:
```json
{
  "run_id": "run_01",
  "state": "running",
  "pipeline": {
    "pipeline_id": "pipeline_01",
    "pipeline_run_id": "fabric_run_01",
    "triggered_ts_utc": "2026-03-09T12:05:00+00:00"
  }
}
```

### 5.3.5 `refresh_run_status(...)`
Refreshes latest execution status for a run.

Inputs:
- `run_id: str`

Behavior:
- Resolve pipeline run reference from `run_events`.
- Query Fabric pipeline run status.
- Persist a status observation event.

Outputs:
- `run_id`
- pipeline status (`queued|in_progress|succeeded|failed|cancelled`)
- timestamps
- error details if failed

Request shape:
```json
{
  "run_id": "run_01"
}
```

Response `data` shape:
```json
{
  "run_id": "run_01",
  "state": "running",
  "pipeline": {
    "pipeline_run_id": "fabric_run_01",
    "status": "in_progress"
  },
  "latest_error": null
}
```

### 5.3.6 `get_run(...)`
Gets the current run detail view.

Inputs:
- `run_id: str`

Outputs:
- full run detail:
  - requested scope
  - frozen asset snapshot
  - staged assets
  - pipeline reference
  - current state
  - latest errors

Request shape:
```json
{
  "run_id": "run_01"
}
```

Response `data` shape:
```json
{
  "run": {},
  "asset_snapshots": [],
  "staged_assets": [],
  "pipeline": {},
  "status_history": [],
  "result_summary": null
}
```

### 5.3.7 `list_runs(...)`
Lists run summaries.

Inputs:
- optional `submission_id`
- optional `state`
- optional `requested_by`

Outputs:
- paginable list of run summaries

Request shape:
```json
{
  "submission_id": "sub_01",
  "state": null,
  "requested_by": null,
  "page": 1,
  "page_size": 25
}
```

Response `data` shape:
```json
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "total": 1
}
```

### 5.3.8 `finalize_run(...)`
Applies execution results to run state and optionally to submission validation state.

Inputs:
- `run_id: str`
- `result_payload: object`
- `finalized_by: str`
- `apply_validation_flags: bool` (default `false`)

Behavior:
- Persist normalized result summary for the run.
- Mark assets/organisations as passed or failed for that run snapshot.
- If `apply_validation_flags=true`, update submission/template/company validation flags based on the run result.

Outputs:
- finalized run detail
- affected submission state summary if flags are applied

Request shape:
```json
{
  "run_id": "run_01",
  "result_payload": {
    "asset_results": [
      {
        "asset_key": "ORG001_FILE",
        "status": "passed"
      },
      {
        "asset_key": "TPL_MAIN",
        "status": "passed"
      }
    ]
  },
  "finalized_by": "user@example.com",
  "apply_validation_flags": true
}
```

Response `data` shape:
```json
{
  "run_id": "run_01",
  "state": "succeeded",
  "result_summary": {
    "passed_asset_keys": ["ORG001_FILE", "TPL_MAIN"],
    "failed_asset_keys": []
  },
  "submission_update": {
    "submission_id": "sub_01",
    "state": "validated"
  }
}
```

## 5.5 `update_submission(...)`
Updates submission details via append-only events.

Inputs (partial update):
- `submission_id: str`
- `modified_by: str`
- `idempotency_key: str` (required)
- optional:
  - add/remove organisations
  - add/remove/update assets
  - change asset applicability to organisations
  - submission metadata updates where allowed

Behavior:
- No in-place edits; add update events.
- Keep before/after values in event payload for audit.
- Any updated asset resets that asset `validation_flag` to `not_validated`.
- If a shared template asset changes, all dependent organisations must be recomputed as not validated until the template is revalidated.

Outputs:
- Updated projected submission state.

## 5.6 `set_validation_flags(...)`
Changes per-asset and/or per-organisation validated/not_validated flags with audit.

Inputs:
- `submission_id: str`
- `organisation_changes: dict[str, str]` (optional, `organisation_cd -> validated|not_validated`)
- `asset_changes: dict[str, str]` (optional, `asset_key -> validated|not_validated`)
- `modified_by: str`
- optional `reason: str`
- `idempotency_key: str` (required)

Behavior:
- Append one event per changed entity with actor/timestamp/reason.
- Asset validation updates should update `validated_hash` and all derived states.
- Recompute submission aggregate state.

Outputs:
- Updated per-org flags, per-asset flags, and submission aggregate state.

## 6. Data Schemas (logical)

## 6.1 `submission_events.jsonl` schema
- `event_id`
- `event_ts_utc`
- `event_type` (`submission_created`, `organisation_updated`, `asset_updated`, `asset_flag_changed`, `org_flag_changed`, `submission_updated`)
- `submission_id`
- `process_cd`
- `submission_period_cd`
- `organisation_cd` (nullable depending on event type)
- `asset_key` (nullable depending on event type)
- `sharepoint_source` (nullable structured object)
- `validation_flag` (nullable)
- `submission_state` (nullable snapshot/derived helper)
- `actor`
- `reason` (nullable)
- `payload_json` (optional details)

Format:
- One JSON object per line.
- UTF-8 encoded.
- Append-only; never rewrite prior lines except for explicit repair tooling.

## 6.2 `run_events.jsonl` schema
- `event_id`
- `event_ts_utc`
- `event_type` (`run_planned`, `run_created`, `asset_staged`, `pipeline_triggered`, `status_polled`, `run_finalized`, `run_failed`, `run_succeeded`)
- `run_id`
- `submission_id`
- `pipeline_run_id`
- `organisation_cd` (nullable)
- `template_key` (nullable)
- `asset_key` (nullable)
- `fabric_staged_path` (nullable)
- `status` (nullable)
- `error_code` (nullable)
- `error_message` (nullable)
- `actor`
- `payload_json` (optional)

Recommended `payload_json` content by event type:
- `run_created`
  - requested scope
  - frozen asset snapshots
- `asset_staged`
  - staged path
  - byte count
  - snapshot hash
- `pipeline_triggered`
  - pipeline id/name
  - pipeline parameters
- `status_polled`
  - observed external status
  - raw status payload if needed
- `run_finalized`
  - normalized result summary
  - per-asset / per-organisation outcomes

Format:
- One JSON object per line.
- UTF-8 encoded.
- Append-only; never rewrite prior lines except for explicit repair tooling.

## 6.3 Projection file schemas
- `submissions_current.json`
  - JSON object containing `updated_ts_utc` and `items: [...]`
- `runs_current.json`
  - JSON object containing `updated_ts_utc` and `items: [...]`
- Optional per-submission snapshots
  - One JSON document per submission for detail views
  - Must include both participant organisations and tracked assets

## 6.4 Idempotency store
- `idempotency_key`
- `operation` (`create_submission`, `create_run`, `stage_run_inputs`, `trigger_run`, `refresh_run_status`, `finalize_run`, `update_submission`, `set_validation_flags`)
- `actor`
- `request_hash`
- `response_payload`
- `created_ts_utc`
- `expires_ts_utc`

Behavior:
- Same `idempotency_key + operation + actor` with identical `request_hash` returns the original response.
- Same key with different payload returns conflict error.

v1 storage choice:
- In-memory for local testing.
- File-backed JSON store in `Files/validation-service/system/idempotency.json` for deployed v1 if needed.

## 7. Validation and Error Rules
- Reject creation if `process_cd` or `submission_period_cd` missing.
- Reject creation if any organisation code duplicated.
- Reject creation if any asset key duplicated or source path empty.
- Reject creation if an asset references unknown organisations.
- Reject creation if a template asset has no applicability (`all_organisations=false` and empty `organisation_cds`).
- Reject run start if submission not found.
- Reject run start if subset contains unknown organisation or unknown asset unless explicit override mode is enabled.
- Treat all timestamps as UTC.
- Normalize organisation codes and process/submission codes (trim + consistent casing).
- Normalize asset keys using a stable casing/slug policy.

Standard error codes:
- `VALIDATION_ERROR`
- `SUBMISSION_NOT_FOUND`
- `ORG_NOT_IN_SUBMISSION`
- `DUPLICATE_ACTIVE_SUBMISSION`
- `IDEMPOTENCY_CONFLICT`
- `SHAREPOINT_COPY_FAILED`
- `PIPELINE_TRIGGER_FAILED`
- `PIPELINE_STATUS_QUERY_FAILED`
- `FABRIC_WRITE_FAILED`

## 8. Concurrency and Consistency
- Use optimistic concurrency for projection rebuilds.
- Writes are append-only; projection rebuild must be deterministic by `event_ts_utc` then `event_id`.
- `event_id` must be globally unique and monotonic-sortable (UUIDv7 recommended).
- Event append and projection rewrite must be serialized per logical store to avoid partial writes.
- `create_submission` uniqueness guard:
  - default policy: reject duplicate active submissions for the same `(process_cd, submission_period_cd)`.
  - config flag `allow_duplicate_active_submissions` can relax this.
- Projection rebuild must be idempotent and replay-safe from raw events.

## 9. Security and Auth
- Use shared token manager service for token acquisition/refresh.
- Required scopes:
  - Microsoft Graph (SharePoint read)
  - Fabric APIs (workspace/pipeline operations)
  - OneLake storage APIs (staging writes)
- Do not log secrets/tokens.
- Record actor identity for every mutating action.

## 10. Observability
- Structured logs for API actions:
  - `action`, `submission_id`, `run_id`, `pipeline_run_id`, `actor`, `result`.
- Include correlation ID per request path.
- Persist error summaries in `run_events`.
- Track API latency metrics by method and outcome.
- Track counters for retries, token refreshes, and failed external calls.

## 10.1 GUI-oriented query contracts
Provide projection/query helpers so GUI does not reconstruct state from raw events:
- `list_submissions_view(filters, page, page_size, sort)`:
  - returns paginated summaries.
- `get_submission_detail_view(submission_id)`:
  - returns full submission with organisation rows and latest run summary.
- `list_submission_runs_view(submission_id, page, page_size)`:
  - returns run history.

Pagination defaults:
- `page=1`, `page_size=25`, max `page_size=200`.

GUI contract note:
- GUI reads projections through API methods, not by reading `JSONL` directly.

## 11. Test Plan

Unit tests:
- Submission projection from append-only events.
- `create_submission` validation and generated metadata.
- `set_validation_flags` audit row creation.
- `update_submission` add/remove organisation and asset changes.
- State aggregation (`validated`, `partially_validated`, etc).
- Shared-template applicability and derived organisation state.
- One-template-many-organisations projection behavior.

Integration tests (mocked Fabric/SharePoint clients):
- Run planning correctness for selected organisations/templates/assets.
- Frozen hash snapshot correctness at run creation time.
- File staging from SharePoint to Fabric temp.
- Pipeline trigger payload correctness.
- Pipeline status polling and error mapping.
- Run finalization and validation-flag application behavior.
- Projection rebuild correctness from event streams.
- Archive export job consistency (row counts/checksum or equivalent).
- JSONL append/read roundtrip.
- Projection JSON rewrite correctness after multiple events.

Failure-path tests:
- Expired token auto-refresh.
- SharePoint copy failure.
- Pipeline trigger failure.
- Pipeline failed status with surfaced error.

## 12. Implementation Phases
1. Define core models and `JSONL`/`JSON` file schemas.
2. Implement Fabric file adapters and token-aware request wrapper.
3. Implement JSONL append + projection rebuild utilities.
4. Implement `list_submissions` and projection builder.
5. Implement `create_submission`.
6. Implement `ValidationRunApi.plan_run` and `ValidationRunApi.create_run`.
7. Implement `ValidationRunApi.stage_run_inputs` and run event tracking.
8. Implement `ValidationRunApi.trigger_run` and `ValidationRunApi.refresh_run_status`.
9. Implement `ValidationRunApi.finalize_run`.
10. Implement `update_submission` and `set_validation_flags`.
11. Add projection/query view functions for GUI consumption.
12. Add comprehensive tests, then wire into GUI callbacks.

## 13. Operations Runbook (v1 defaults)

Audit retention defaults:
- Event files (`submission_events.jsonl`, `run_events.jsonl`) operational retention: `180 days` minimum.
- Immutable archive retention: `7 years` (or org compliance requirement, whichever is higher).
- Idempotency table retention: `30 days`.

Archive cadence:
- Incremental archive export every `6 hours`.
- Daily reconciliation job verifies archive completeness for prior day.

Maintenance guardrails:
- File compaction/rotation allowed only after successful archive export and verification for affected window.
- Minimum local event retention threshold: `>= 180 days` before archival-only storage.
- No in-place rewrite of historical event files in normal operations.

Verification and integrity:
- Each archive batch records:
  - source table
  - event time window
  - row count
  - checksum/hash
  - export timestamp
- Daily reconciliation compares source counts/checksums with archive manifests.
- Any mismatch raises an operational alert and blocks retention-compacting maintenance.

Monitoring and alerting:
- Alerts on:
  - archive job failure
  - reconciliation mismatch
  - projection rebuild failure
  - pipeline status polling failures above threshold
- Suggested thresholds:
  - archive/reconciliation failures: alert immediately
  - external call failures: alert if error rate > `5%` over `15 min`

Recovery procedures:
1. Stop retention/compaction jobs and cleanup schedulers.
2. Identify missing/corrupt interval from reconciliation logs.
3. Restore from immutable archive for that interval.
4. Rebuild projections from restored events.
5. Re-run reconciliation and reopen maintenance jobs only after clean verification.

## 14. Fixed Decisions for v1
- Source of truth: append-only `JSONL` event files in `Files/validation-service/events/`.
- Read model: `JSON` projection files in `Files/validation-service/projections/`.
- Long-term history: immutable archive pipeline plus controlled retention.
- Pipeline selection: single configured pipeline ID for v1.
- Duplicate submission policy: reject duplicate active submissions by default.
- All mutating APIs require idempotency keys.
- GUI should consume projection/view APIs, not raw events.

## 15. Future Extensions (v2+)
- Multiple pipelines selected by `process_cd` or tenant.
- Batch status polling worker for long-running runs.
- Table-backed or Delta-backed projections if query scale demands them.
- Webhook/event-driven notifications for run state changes.
