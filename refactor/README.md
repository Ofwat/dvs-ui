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

Current submission service scope (small but robust):
- Append-only submission events (in-memory store for now).
- `create_submission` with duplicate active guard.
- `list_submissions` projection for current state.
- `set_organisation_validation_flags` with idempotency and audit events.

Smoke test the submission API with config:

```bash
python refactor/services/data_validation_api/smoke_test_submission_api.py --config refactor/services/data_validation_api/smoke_test_submission_api.config.json
```

Smoke test Fabric lakehouse access with config:

```bash
python refactor/services/data_validation_api/fabric_lakehouse_smoke_test.py --config refactor/services/data_validation_api/fabric_lakehouse_smoke_test.config.json
```

This script:
- Resolves workspace and lakehouse IDs from configured display names.
- Reads lakehouse table metadata via the Fabric tables API.
- If the lakehouse is schema-enabled and that endpoint is unsupported, it logs a warning and continues.
- Uploads a test file into the configured OneLake `Files/...` directory.
- Lists files in that directory.
- Downloads the uploaded file and verifies content matches.

Smoke test SharePoint access with config (read-only):

```bash
python refactor/services/data_validation_api/sharepoint_readonly_smoke_test.py --config refactor/services/data_validation_api/sharepoint_readonly_smoke_test.config.json
```

This script:
- Resolves a configured SharePoint folder URL through Microsoft Graph.
- Infers whether the folder belongs to a SharePoint drive or a Microsoft 365 group.
- Lists folders and files directly under that folder.
- Picks a random file from that folder, downloads it, and prints created/last-modified timestamps.
- Does not perform any SharePoint write, upload, move, or delete operation.

Next step:
- Add Fabric-backed adapters for event persistence and run orchestration.
