# Dimensions Loading GUI Wrapper Design

## Goal

Build a GUI service inside the single Dash app for the dimensions-loading workflow only.

The intended user flow is:
1. User opens Services.
2. User selects `Dimension Loader`.
3. User chooses environment: `dev` or `prod`.
4. User gets actions similar to the CLI:
   - Refresh files to be loaded from SharePoint
   - List dimension files to be loaded from SharePoint
   - Sync dimensions Fabric with SharePoint

The wrapper should make it easier to:
- select the target environment
- inspect SharePoint source files
- review the target Fabric destination
- run the sync with less terminal interaction
- show progress and failures clearly

## Intended Scope

This is a wrapper for the existing CLI logic, not a rewrite of the upload logic.

Initial scope assumption:
- dimensions jobs only
- SharePoint sources only
- the service scans a service-owned config file outside the app folder for dimensions jobs
- the GUI lives inside the single existing Dash app, under Services
- environment selection is derived from job name prefixes: `[DEV]` and `[PROD]`
- wrapper focuses on the jobs whose target paths live under `Files/dimensions/...`

## Config Location

Recommended config path:
- `~/.dvs-ui/services/dimensions-loader/config.json`

Why this is better than a repo-local `local_config.json`:
- it keeps service state out of the application folder
- it matches the existing auth storage pattern already used by the app
- it is user-specific, predictable, and easy to document

If the config file is missing, the service should not fail silently. It should show a clear message such as:

> No dimensions loader config found at `~/.dvs-ui/services/dimensions-loader/config.json`.
> Create the config file or open the service settings to point the app at a different config.

## Environment Requirements

The service also depends on values from `.env`.

Required values for the dimensions flow:
- `SHAREPOINT_HOST`
- `SHAREPOINT_SITE_PATH`

If these are missing, the GUI should show a clear setup message instead of failing with a stack trace. Example:

> Dimensions Loader is not configured yet.
> Missing environment values: `SHAREPOINT_HOST`, `SHAREPOINT_SITE_PATH`.
> Add them to `.env` and reload the app.

Useful defaults already exist in the current app for some values, but the GUI should still validate the runtime environment and explain what is missing when the dimensions service cannot start.

## Suggested UX Shape

1. Landing screen lists only dimensions jobs.
2. Each job card shows:
   - job name
   - enabled/disabled state
   - workspace and lakehouse
   - source type
   - target root
   - number of configured mappings
3. Selecting an environment opens a detail panel with:
   - source links
   - mapping list
   - target preview
   - validation issues
   - run button
4. Running a job shows:
   - upload progress per file
   - success/failure per mapping
   - final summary

## Open Questions

Please answer these before implementation:

1. Is the wrapper only for jobs whose `target_root` starts with `Files/dimensions/`, or do you want a broader “dimensions-related” filter?
2. Should the GUI let you edit the dimensions job config, or should it be read-only plus a run button?
3. Do you want the wrapper to run the existing CLI workflow directly, or should it call the underlying upload functions and present native progress in the GUI?
4. Should the wrapper support both SharePoint-sourced dimensions jobs and any local-source variants, or only SharePoint?
5. Should the UI include a job validation step before upload, or just show errors when upload starts?
6. Do you want the GUI to keep using `local_config.json`, or should it support loading a separate shared config file?
7. Should disabled jobs be hidden, greyed out, or visible but non-runnable?
8. What is the preferred UI style for this wrapper: plain utility screen, GOV.UK-styled page, or something more dashboard-like?
9. Should upload progress be per file only, or do you want a more detailed per-step log?
10. Do you want a “dry run” mode for dimensions jobs that validates paths and mappings without uploading anything?

## Implementation Notes

- Reuse the existing config parsing and upload functions as much as possible.
- Keep the GUI focused on the dimensions use case so it stays simple.
- Avoid expanding the wrapper into a full replacement for the terminal CLI unless that becomes a requirement.
- If the GUI lives inside the existing Dash app, keep the dimensions UI isolated from the broader dashboard pages.

## Working Assumption Until You Reply

Assume:
- dimensions jobs are the only supported job type in the wrapper
- SharePoint is the only supported source type
- the wrapper will use a config file concept similar to `local_config.json`, but with a better name/location for the service
- the wrapper will validate and update its own service-specific config as part of the workflow
- config editing is not in scope for the first version, but the design should leave room for it later
- the GUI will expose a run button, validation summary, and progress log
