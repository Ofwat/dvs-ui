# Current Public API

This document describes the public-facing Python service API that is implemented today under `refactor.services.data_validation_api`.

It is intentionally a "what exists now" document, not a target-state design doc.

## Main Service Objects

The current backend surface is split across two main classes:

- `ValidationServiceApi` in [submission_service.py](submission_service.py)
- `ValidationRunApi` in [validation_run_api.py](validation_run_api.py)

`ValidationServiceApi` owns submission lifecycle and submission-level validation flags.

`ValidationRunApi` owns validation run lifecycle, staging, triggering, polling, and finalization.

## `ValidationServiceApi`

### `list_submissions(...) -> ApiResponse`

Filters current submission projections.

Parameters:
- `process_cd: str | None = None`
- `submission_period_cd: str | None = None`
- `state: str | None = None`
- `created_by: str | None = None`

Returns:
- `ApiResponse.data` containing a list of submission projections.

### `create_submission(...) -> ApiResponse`

Creates a new submission with organisation files and optional template files.

Parameters:
- `process_cd: str`
- `submission_period_cd: str`
- `organisations: dict[str, dict | OrganisationSubmissionRef | SharePointSourceRef]`
- `templates: dict[str, dict | TemplateSubmissionRef] | None`
- `created_by: str`
- `idempotency_key: str`
- `note: str | None = None`
- `allow_duplicate_active: bool = False`

Returns:
- `ApiResponse.data` containing the created submission projection.

### `edit_submission(...) -> ApiResponse`

Updates an existing submission by upserting/removing organisations or templates, and optionally updating the note.

Parameters:
- `submission_id: str`
- `modified_by: str`
- `idempotency_key: str`
- `organisation_upserts: dict[str, dict | OrganisationSubmissionRef | SharePointSourceRef] | None = None`
- `organisation_removals: list[str] | None = None`
- `template_upserts: dict[str, dict | TemplateSubmissionRef] | None = None`
- `template_removals: list[str] | None = None`
- `note: str | None = None`
- `reason: str | None = None`

Returns:
- `ApiResponse.data` containing the updated submission projection.

### `refresh_submission_hashes(...) -> ApiResponse`

Refreshes SharePoint hashes and metadata for all organisations and templates in a submission.

Parameters:
- `submission_id: str`
- `refreshed_by: str`
- `idempotency_key: str`

Returns:
- `ApiResponse.data` containing the refreshed submission projection.

Requires:
- `sharepoint_hash_resolver` configured on the service instance.

### `set_validation_flags(...) -> ApiResponse`

Sets validation flags for organisations and/or templates in a submission.

Parameters:
- `submission_id: str`
- `organisation_changes: dict[str, str] | None`
- `template_changes: dict[str, str] | None`
- `modified_by: str`
- `idempotency_key: str`
- `reason: str | None = None`

Returns:
- `ApiResponse.data` containing the updated submission projection.

Supported flags today:
- `validated`
- `not_validated`

### `set_organisation_validation_flags(...) -> ApiResponse`

Convenience wrapper over `set_validation_flags(...)` for organisation-only updates.

Parameters:
- `submission_id: str`
- `changes: dict[str, str]`
- `modified_by: str`
- `idempotency_key: str`
- `reason: str | None = None`

### `set_template_validation_flags(...) -> ApiResponse`

Convenience wrapper over `set_validation_flags(...)` for template-only updates.

Parameters:
- `submission_id: str`
- `changes: dict[str, str]`
- `modified_by: str`
- `idempotency_key: str`
- `reason: str | None = None`

### `list_submission_history(...) -> ApiResponse`

Lists submission events for a single submission.

Parameters:
- `submission_id: str`
- `actor: str | None = None`
- `include_run_events: bool = False`

Returns:
- `ApiResponse.data` containing normalized event entries with timestamps, actor, summary, payload, and source details.

### `list_edit_history(...) -> ApiResponse`

Lists submission edit events only for a single submission.

Parameters:
- `submission_id: str`
- `actor: str | None = None`

Returns:
- `ApiResponse.data` containing normalized edit-history entries.

### `list_runs(...) -> ApiResponse`

Legacy run projection listing exposed from the submission service.

Parameters:
- `submission_id: str | None = None`
- `state: str | None = None`
- `started_by: str | None = None`

Returns:
- `ApiResponse.data` containing legacy run projections built from submission events.

Status:
- Still implemented.
- Superseded by `ValidationRunApi.list_runs(...)` for new run handling.

### `start_validation_run(...) -> ApiResponse`

Legacy run-start method on the submission service.

Parameters:
- `submission_id: str`
- `requested_org_files: dict[str, list[str] | None] | None`
- `started_by: str`
- `idempotency_key: str`
- `refresh_hashes: bool = True`

Returns:
- `ApiResponse.data` containing the legacy run projection.

Status:
- Still implemented for backward compatibility.
- New work should prefer `ValidationRunApi`.

## `ValidationRunApi`

The run API uses typed request/result dataclasses rather than `ApiResponse`.

## Planning

### `plan_run(request: PlanRunRequest) -> PlanRunResult`

Resolves the run scope before creation.

`PlanRunRequest` fields:
- `submission_id: str`
- `requested_organisations: list[str] | None = None`
- `requested_template_keys: list[str] | None = None`
- `requested_asset_keys: list[str] | None = None`
- `force_revalidate: bool = False`

`PlanRunResult` fields:
- `submission_id`
- `selected_organisations`
- `selected_templates`
- `selected_asset_keys`
- `asset_plan`
- `warnings`

## Run Lifecycle

### `create_run(request: CreateRunRequest) -> CreateRunResult`

Creates a persisted validation run and captures asset snapshots.

`CreateRunRequest` fields:
- `submission_id: str`
- `requested_by: str`
- `idempotency_key: str`
- `requested_organisations: list[str] | None = None`
- `requested_template_keys: list[str] | None = None`
- `requested_asset_keys: list[str] | None = None`

`CreateRunResult` fields:
- `run: RunSummary`
- `asset_snapshots: list[AssetSnapshot]`

### `stage_run_inputs(request: StageRunInputsRequest) -> StageRunInputsResult`

Stages the selected assets to a run-specific destination such as Fabric/OneLake.

`StageRunInputsRequest` fields:
- `run_id: str`
- `staged_by: str`

`StageRunInputsResult` fields:
- `run_id`
- `state`
- `staged_assets`
- `errors`

Requires:
- `asset_stager` configured on the API instance.

### `trigger_run(request: TriggerRunRequest) -> TriggerRunResult`

Triggers the external validation pipeline for a staged run.

`TriggerRunRequest` fields:
- `run_id: str`
- `triggered_by: str`

`TriggerRunResult` fields:
- `run_id`
- `state`
- `pipeline: PipelineRef`

Requires:
- `pipeline_trigger` configured on the API instance.

### `refresh_run_status(request: RefreshRunStatusRequest) -> RefreshRunStatusResult`

Polls the external pipeline status and updates the run state.

`RefreshRunStatusRequest` fields:
- `run_id: str`

`RefreshRunStatusResult` fields:
- `run_id`
- `state`
- `pipeline_status`
- `latest_error`

Requires:
- `pipeline_status_resolver` configured on the API instance if external status polling is needed.

### `finalize_run(request: FinalizeRunRequest) -> FinalizeRunResult`

Stores final result payloads and can optionally apply validation flags back to the submission.

`FinalizeRunRequest` fields:
- `run_id: str`
- `result_payload: dict[str, Any]`
- `finalized_by: str`
- `apply_validation_flags: bool = False`

`FinalizeRunResult` fields:
- `run_id`
- `state`
- `result_summary`
- `submission_update`

Requires:
- `submission_flag_setter` configured on the API instance if `apply_validation_flags=True`.

## Run Queries

### `get_run(request: GetRunRequest) -> GetRunResult`

Returns the full persisted run view.

`GetRunRequest` fields:
- `run_id: str`

`GetRunResult` fields:
- `run: RunSummary`
- `asset_snapshots: list[AssetSnapshot]`
- `staged_assets: list[StagedAsset]`
- `pipeline: PipelineRef | None`
- `status_history: list[dict[str, Any]]`
- `result_summary: dict[str, Any] | None`

### `list_runs(request: ListRunsRequest) -> ListRunsResult`

Lists persisted validation runs with paging.

`ListRunsRequest` fields:
- `submission_id: str | None = None`
- `state: str | None = None`
- `requested_by: str | None = None`
- `page: int = 1`
- `page_size: int = 25`

`ListRunsResult` fields:
- `items: list[RunSummary]`
- `page`
- `page_size`
- `total`

### `list_run_history(request: ListRunHistoryRequest) -> ListRunHistoryResult`

Lists persisted runs with richer history detail than `list_runs(...)`.

`ListRunHistoryRequest` fields:
- `submission_id: str | None = None`
- `state: str | None = None`
- `requested_by: str | None = None`
- `page: int = 1`
- `page_size: int = 25`

`ListRunHistoryResult` fields:
- `items: list[RunHistoryEntry]`
- `page`
- `page_size`
- `total`

`RunHistoryEntry` fields:
- `run: RunSummary`
- `pipeline: PipelineRef | None`
- `latest_error: RunStatusError | None`
- `status_history: list[dict[str, Any]]`
- `result_summary: dict[str, Any] | None`

## Core Public Data Types

These types are part of the current public Python surface and are exported from the package:

- `ApiResponse`
- `ApiError`
- `SharePointSourceRef`
- `OrganisationSubmissionRef`
- `TemplateSubmissionRef`
- `TrackedFileRef`
- `PlanRunRequest`
- `PlanRunResult`
- `CreateRunRequest`
- `CreateRunResult`
- `StageRunInputsRequest`
- `StageRunInputsResult`
- `TriggerRunRequest`
- `TriggerRunResult`
- `RefreshRunStatusRequest`
- `RefreshRunStatusResult`
- `GetRunRequest`
- `GetRunResult`
- `ListRunHistoryRequest`
- `ListRunHistoryResult`
- `ListRunsRequest`
- `ListRunsResult`
- `FinalizeRunRequest`
- `FinalizeRunResult`
- `RunSummary`
- `AssetSnapshot`
- `StagedAsset`
- `PipelineRef`
- `RunStatusError`
- `RunHistoryEntry`

## Notes On Current Shape

- `ValidationServiceApi` still exposes some legacy run methods.
- `ValidationRunApi` is the main path for new validation-run work.
- Some higher-level orchestration still lives in the smoke script rather than fully in the service layer.
- This document covers the callable service surface that exists now, not the full desired future API.
