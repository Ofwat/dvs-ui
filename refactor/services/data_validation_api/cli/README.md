# Data Validation CLI

Interactive terminal UI for the data validation service.

## Run

```bash
python -m refactor.services.data_validation_api.cli
python -m refactor.services.data_validation_api.cli --config path/to/config.json
python -m refactor.services.data_validation_api.cli --action run --config path/to/config.json
```

On first run without `--config`, the CLI asks for the service and pipeline settings and saves them to `local_config.json` in this folder. Later runs reuse that file and let you choose `Edit saved config` from the main menu.

## Features

- arrow-key menus for choosing actions and existing records
- pasteable text fields for codes, notes, and SharePoint links
- saved local config for workspace, lakehouse, pipeline, and storage paths
- drill-down view for submissions, companies, templates, files, and linked validation runs
- flows for creating, editing, and running submissions

## Dependency

This CLI uses `questionary` for terminal prompts.
