# Refactor workspace

This directory will house the refactor of `examples/plotlydash-basic`. Follow the existing AGENTS instructions: promote clean structure, reuse components, keep diffs minimal, and add tests/coverage as soon as logic changes. The current work-in-progress files (from the base example) remain in `examples/plotlydash-basic` so the refactor can start from a clean slate here. Any design notes, new layout files, or prototype modules should live in this folder to keep refactor work isolated.

## Next steps
1. Decide what portions of the current dashboard to carry into the refactor (layout, navigation, dqchecks wiring, assets).
2. Sketch the desired new architecture (componentized layouts, shared utility modules, test harness).
3. Slowly reimplement the dashboard within this folder, referencing the original code rather than modifying it directly.

## data-validation-api scaffold

This repo now includes a backend-first scaffold for the `data-validation-api` subsystem:

- API: `refactor/services/data_validation_api/api.py`
- CLI runner: `refactor/services/data_validation_api/runner.py`
- Tests: `refactor/tests/services/test_data_validation_api.py`
- Submission service core: `refactor/services/data_validation_api/submission_service.py`
- Submission tests: `refactor/tests/services/test_submission_service.py`

Run it from repo root:

```bash
python -m refactor.services.data_validation_api.runner --input path/to/file.csv --required-field name --required-field email
```

Run tests:

```bash
python -m unittest refactor.tests.services.test_data_validation_api
python -m unittest refactor.tests.services.test_submission_service
```

Current submission service scope:
- Append-only submission and run events with in-memory, local file-backed, and Fabric-backed store options.
- `create_submission` with duplicate active guard.
- `list_submissions` projection for current state.
- `set_organisation_validation_flags` with idempotency and audit events.
- Dedicated validation run API with append-only run events, projections, staging, trigger, status refresh, and finalize support.

Smoke test create/edit submission then trigger a validation run with config:

```bash
python refactor/services/data_validation_api/smoke_test_submission_and_run_api.py --config refactor/services/data_validation_api/smoke_test_submission_api.config.json
```

This script:
- creates or reuses a submission
- edits the submission entry
- stages selected files to local disk or Fabric depending on `storage_mode`
- triggers a validation run and prints the resulting run detail
- persists submission/run state to local files or Fabric paths depending on `storage_mode`

Next step:
- Expand Fabric pipeline payload/status mapping and add broader integration coverage.
