# Fabric Uploader CLI

Interactive terminal UI for uploading local files or SharePoint files into a Fabric lakehouse.

## Run

```bash
python -m refactor.services.fabric_uploader_cli
python -m refactor.services.fabric_uploader_cli --config path/to/config.json
python -m refactor.services.fabric_uploader_cli --action upload
python -m refactor.services.fabric_uploader_cli --action show_config_path
```

On first run without `--config`, the CLI creates `local_config.json` in this folder and lets you define:

- reusable upload jobs
- file mappings for local folders or SharePoint folders
- the Fabric workspace and lakehouse for each job

Each job can then be re-run, edited, enabled, disabled, or removed from the saved config.
The CLI can also show the config file path directly so you can open and edit it yourself.

## Shareable Configs

To share a config between users, keep it in a shared folder and run the CLI with `--config`.

- Job-level Fabric destinations are stored by display name, so each job can target a different workspace and lakehouse.
- Relative local source paths are resolved from the config file's folder, not from the current working directory.
- SharePoint jobs can store multiple file/folder links. File links are added directly; folder links are scanned and you choose which files to map.
- Each job uses `source_root` and `target_root`, and each mapping stores a `target_relative_path` relative to the job's `target_root`.
- Authentication is not stored in the config file.
