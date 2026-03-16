# Current API Contract

This document describes the transport-agnostic Python application API implemented under `refactor.services.data_validation_api`.

The target contract is reusable from:
- GUI
- CLI
- automation scripts

It is not an HTTP contract and not a GUI-specific facade.

## Layers

### Stable application APIs

- `ValidationServiceApi` in [submission_service.py](submission_service.py)
- `ValidationRunApi` in [validation_run_api.py](validation_run_api.py)

### Internal workflow helpers

- `workflows.py` in [workflows.py](workflows.py)

These helpers are intentionally transport-agnostic and are used to keep CLI/example code thin. They are internal reusable orchestration helpers, not the primary stable contract.

### Client adapters

The scripts under [examples](examples) are example clients of the stable APIs and workflow helpers. They should not be treated as the source of truth for domain behavior.

## Behavioral Rules

### Canonical run states

Active states:
- `queued`
- `staged`
- `running`

Terminal states:
- `succeeded`
- `failed`
- `cancelled`
- `partially_succeeded`

External aliases are normalized internally. Public callers should only rely on the canonical states above.

### Explicit validation rule

Successful pipeline completion does not automatically mark submission files as validated.

Tracked file fields:
- `validated_hash`
- `validation_run_id`

remain `null` until validation is applied explicitly via:
- `ValidationServiceApi.set_validation_flags(...)`
- `ValidationRunApi.finalize_run(..., apply_validation_flags=True)`

### Watched files

Tracked file entries always carry:
- `watch`
- `path`
- `watch_search_term` when relevant

Rules:
- `refresh_submission_hashes(...)` is responsible for recomputing SharePoint file metadata and resolving watched files when they appear.
- run methods operate on the current stored submission state
- unresolved watched files must not silently pass run staging

## Stable Application APIs

## `ValidationServiceApi`

Uses typed request/result dataclasses.

Stable result rule:
- every stable result includes `ok` and `error`
- recoverable failures are returned in `error`, not raised as raw public exceptions

Common public error shape:
- `OperationError`
  - `code: str`
  - `message: str`
  - `details: dict[str, Any] | None`

### `list_submissions(request: ListSubmissionsRequest) -> ListSubmissionsResult`

Filters current submission projections.

Parameters:
- `process_cd: str | None = None`
- `submission_period_cd: str | None = None`
- `state: str | None = None`
- `created_by: str | None = None`

### `create_submission(request: CreateSubmissionRequest) -> SubmissionMutationResult`

Creates a new submission.

Parameters:
- `process_cd: str`
- `submission_period_cd: str`
- `organisations: dict[str, dict | OrganisationSubmissionRef | SharePointSourceRef]`
- `templates: dict[str, dict | TemplateSubmissionRef] | None`
- `created_by: str`
- `idempotency_key: str`
- `note: str | None = None`
- `allow_duplicate_active: bool = False`

### `edit_submission(request: EditSubmissionRequest) -> SubmissionMutationResult`

Updates an existing submission.

Editable fields:
- note
- organisation upserts/removals
- template upserts/removals
- organisation validation flags
- template validation flags

Identity fields are not editable:
- `process_cd`
- `submission_period_cd`

### `refresh_submission_hashes(request: RefreshSubmissionRequest) -> SubmissionMutationResult`

Refreshes SharePoint-backed file metadata for a submission.

Responsibilities:
- recompute `current_hash`
- recompute `size_bytes`
- refresh `created`, `modified`, `modified_by`
- resolve watched files when they appear

Requires:
- `sharepoint_hash_resolver`

### `set_validation_flags(request: SetValidationFlagsRequest) -> SubmissionMutationResult`

Applies explicit validation flags to organisations and/or templates.

Supported flags:
- `validated`
- `not_validated`

### `set_organisation_validation_flags(...) -> SubmissionMutationResult`

Convenience wrapper for organisation-only explicit validation changes.

### `set_template_validation_flags(...) -> SubmissionMutationResult`

Convenience wrapper for template-only explicit validation changes.

### `list_submission_history(request: ListSubmissionHistoryRequest) -> ListSubmissionHistoryResult`

Lists normalized submission events for a single submission.

### `list_edit_history(request: ListEditHistoryRequest) -> ListEditHistoryResult`

Lists submission edit events only for a single submission.

## `ValidationRunApi`

Uses typed request/result dataclasses with the same stable envelope:
- `ok: bool`
- `error: OperationError | None`

### Planning

#### `plan_run(request: PlanRunRequest) -> PlanRunResult`

Resolves run scope and selected assets before run creation.

### Run lifecycle

#### `create_run(request: CreateRunRequest) -> CreateRunResult`

Creates and persists a validation run plus asset snapshots.

#### `stage_run_inputs(request: StageRunInputsRequest) -> StageRunInputsResult`

Stages run inputs to the configured backing store.

Requires:
- `asset_stager`

#### `trigger_run(request: TriggerRunRequest) -> TriggerRunResult`

Triggers the external pipeline.

Requires:
- `pipeline_trigger`

`TriggerRunResult` also exposes persisted external metadata such as:
- pipeline config file paths
- latest known external status metadata when available

#### `refresh_run_status(request: RefreshRunStatusRequest) -> RefreshRunStatusResult`

Refreshes one active run from the external pipeline status.

If success is observed externally, the run is auto-finalized for run-history purposes only. Submission validation metadata is not updated automatically.

Requires:
- `pipeline_status_resolver` for live external status

`RefreshRunStatusResult` includes:
- canonical run state
- observed pipeline status
- normalized latest external error
- external metadata including:
  - `pipeline_config_filepaths`
  - `latest_external_status`
  - `last_status_sync_ts_utc`
  - `latest_error`

#### `refresh_running_runs(request: RefreshRunningRunsRequest) -> RefreshRunningRunsResult`

Refreshes all active runs matching the optional filters.

Requires:
- `pipeline_status_resolver` for live external status

#### `poll_run(request: PollRunRequest) -> PollRunResult`

Polls one run until a terminal state or timeout.

Requires:
- `pipeline_status_resolver` for live external status

#### `finalize_run(request: FinalizeRunRequest) -> FinalizeRunResult`

Stores final results and optionally applies explicit validation flags back to the submission.

When `apply_validation_flags=True`, this is the mechanism that populates:
- `validated_hash`
- `validation_run_id`

### Queries

#### `get_run(request: GetRunRequest) -> GetRunResult`

Returns the full persisted run view.

This includes first-class persisted external metadata:
- `pipeline.pipeline_run_id`
- `external_metadata.pipeline_config_filepaths`
- `external_metadata.latest_external_status`
- `external_metadata.last_status_sync_ts_utc`
- `external_metadata.latest_error`

#### `list_running_runs(request: ListRunningRunsRequest) -> ListRunningRunsResult`

Lists only active runs.

#### `list_runs(request: ListRunsRequest) -> ListRunsResult`

Lists persisted runs with paging and simple filters.

#### `list_run_history(request: ListRunHistoryRequest) -> ListRunHistoryResult`

Lists persisted runs with richer history detail than `list_runs(...)`.

Each history entry includes the same persisted external metadata summary used by `get_run(...)`.

## Removed APIs

The old submission-owned run entrypoints were removed from the package surface.

Use instead:
- `ValidationRunApi.list_runs(...)`
- `ValidationRunApi.plan_run(...)`
- `ValidationRunApi.create_run(...)`
- `ValidationRunApi.stage_run_inputs(...)`
- `ValidationRunApi.trigger_run(...)`
- or reusable helpers in `workflows.py`

## Internal Workflow Helpers

The internal orchestration layer currently includes helpers such as:
- `create_submission_from_discovery(...)`
- `edit_submission_workflow(...)`
- `run_submission_workflow(...)`
- `refresh_active_runs_workflow(...)`
- `resolve_sharepoint_folder_listing(...)`
- `find_sharepoint_match_by_search_term(...)`

These are reusable from GUI/CLI code, but they are internal orchestration helpers rather than the main stable service contract.

## Examples

The example scripts are client adapters over the stable APIs and workflow helpers:
- [create_submission.py](examples/create_submission.py)
- [edit_submission.py](examples/edit_submission.py)
- [run_submission.py](examples/run_submission.py)
- [get_run.py](examples/get_run.py)
- [list_runs.py](examples/list_runs.py)
- [list_running_runs.py](examples/list_running_runs.py)
- [refresh_run_status.py](examples/refresh_run_status.py)
- [refresh_running_runs.py](examples/refresh_running_runs.py)
- [list_run_history.py](examples/list_run_history.py)

They are intentionally useful operational clients, but they are not part of the stable API contract.
