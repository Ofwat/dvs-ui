# Data Validation API Plan and Requirements (MS Fabric)

## 1. Goal
Build a backend-first `data-validation-api` service that manages validation submissions in Microsoft Fabric, executes validation runs per submission, and provides auditable state/history.

The service has two stages:

1. Setup of a validation submission.
2. Running and tracking individual validation runs within a submission.

## 2. Scope

### In scope (v1)
- Read submission/runs from append-only event stores in Fabric and return current projected state.
- Create a new submission with metadata and organisation file mappings.
- Start a validation run for selected organisations in an existing submission.
- Track Fabric pipeline run status/errors for launched runs.
- Update validation flags and submission details using append-only history rows (audit trail).

### Out of scope (v1)
- GUI implementation details.
- Complex workflow orchestration beyond one configured pipeline.
- Real-time push notifications.

## 3. High-Level Design

## 3.1 Service object
Create a class like `ValidationServiceApi` with constructor-injected Fabric/SharePoint config.

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
- Submission identity fields:
  - `process_cd`
  - `submission_period_cd`

## 4.3 SharePoint source reference
Canonical shape:
- `source_type`: `drive | group`
- `target_name`: SharePoint drive name or Microsoft 365 group name
- `root_path`: root folder path within that source
- `file_path`: file path relative to the root path

## 4.2 Submission lifecycle state
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
- `validated -> in_progress` when a new run starts for changed paths/organisations.

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
  - per-organisation status and file path
  - created metadata
  - current state summary

## 5.2 `create_submission(...)`
Creates a new submission.

Inputs:
- `process_cd: str`
- `submission_period_cd: str`
- `organisation_sources: dict[str, sharepoint_source_ref]`
- `created_by: str`
- `idempotency_key: str` (required)

Behavior:
- Validate required fields and non-empty organisation map.
- Generate `submission_id`.
- Write append-only creation event(s) with:
  - creator username
  - created timestamp (UTC ISO-8601)
  - initial org flag `not_validated`
  - SharePoint source reference per organisation
  - submission state initialized (recommended `ready`)
- Enforce duplicate policy:
  - v1 default: reject create if an active submission exists for `(process_cd, submission_period_cd)` where state is not terminal (`validated` or `failed`) unless `allow_duplicate_active=true`.

Outputs:
- Created submission summary including `submission_id`.

## 5.3 `start_validation_run(...)`
Starts validation for a subset of organisations within a submission.

Inputs:
- `submission_id: str`
- `organisation_sources_subset: dict[str, sharepoint_source_ref]`
- `requested_by: str`
- `path_override_mode: bool` (default `false`)
- `idempotency_key: str` (required)

Behavior:
- Validate submission exists.
- If `path_override_mode=false`, reject any organisation not already present on submission.
- If `path_override_mode=true`, treat provided source refs as temporary run overrides only (no submission mutation); mutation must go through `update_submission`.
- Copy each selected source file from SharePoint into configured Fabric temp folder.
- Build pipeline payload:
  - `process_cd`
  - staged Fabric file paths
  - organisation list
  - submission context (`submission_id`, `submission_period_cd`)
- Trigger configured Fabric pipeline.
- Record run event(s) with `run_id`, pipeline run reference, and staged file metadata.

Outputs:
- `run_id`
- pipeline run identifier
- staged files map
- initial run status (`queued`/`started`)

## 5.4 `get_run_status(...)`
Gets latest status for a run and crash details if failed.

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
- error details if failed (code/message/stage if available)

## 5.5 `update_submission(...)`
Updates submission details via append-only events.

Inputs (partial update):
- `submission_id: str`
- `modified_by: str`
- `idempotency_key: str` (required)
- optional:
  - add/remove organisations
  - change SharePoint source refs for organisation(s)
  - submission metadata updates where allowed

Behavior:
- No in-place edits; add update events.
- Keep before/after values in event payload for audit.

Outputs:
- Updated projected submission state.

## 5.6 `set_organisation_validation_flags(...)`
Changes per-organisation validated/not_validated flags with audit.

Inputs:
- `submission_id: str`
- `changes: dict[str, str]` (`organisation_cd -> validated|not_validated`)
- `modified_by: str`
- optional `reason: str`
- `idempotency_key: str` (required)

Behavior:
- Append one event per changed organisation with actor/timestamp/reason.
- Recompute submission aggregate state.

Outputs:
- Updated per-org flags + submission aggregate state.

## 6. Data Schemas (logical)

## 6.1 `submission_events.jsonl` schema
- `event_id`
- `event_ts_utc`
- `event_type` (`submission_created`, `org_flag_changed`, `org_path_changed`, `org_added`, `org_removed`, `submission_updated`)
- `submission_id`
- `process_cd`
- `submission_period_cd`
- `organisation_cd` (nullable depending on event type)
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
- `event_type` (`run_requested`, `files_staged`, `pipeline_triggered`, `status_polled`, `run_failed`, `run_succeeded`)
- `run_id`
- `submission_id`
- `pipeline_run_id`
- `organisation_cd` (nullable)
- `fabric_staged_path` (nullable)
- `status` (nullable)
- `error_code` (nullable)
- `error_message` (nullable)
- `actor`
- `payload_json` (optional)

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

## 6.4 Idempotency store
- `idempotency_key`
- `operation` (`create_submission`, `start_validation_run`, `update_submission`, `set_organisation_validation_flags`)
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
- Reject creation if any organisation code duplicated or path empty.
- Reject run start if submission not found.
- Reject run start if subset contains unknown organisation unless explicit override mode is enabled.
- Treat all timestamps as UTC.
- Normalize organisation codes and process/submission codes (trim + consistent casing).

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
- `set_organisation_validation_flags` audit row creation.
- `update_submission` add/remove organisation and path changes.
- State aggregation (`validated`, `partially_validated`, etc).

Integration tests (mocked Fabric/SharePoint clients):
- File staging from SharePoint to Fabric temp.
- Pipeline trigger payload correctness.
- Pipeline status polling and error mapping.
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
6. Implement `start_validation_run` + run event tracking.
7. Implement `get_run_status`.
8. Implement `update_submission` and `set_organisation_validation_flags`.
9. Add projection/query view functions for GUI consumption.
10. Add comprehensive tests, then wire into GUI callbacks.

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
