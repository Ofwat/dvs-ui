from __future__ import annotations

import argparse
import json
from pathlib import Path

from refactor.services.data_validation_api.api import validate_csv_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-validation-api",
        description="Run the data-validation-api subsystem against a CSV file.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to an input CSV file.",
    )
    parser.add_argument(
        "--required-field",
        dest="required_fields",
        action="append",
        default=[],
        help="Required field name. Repeat flag for multiple fields.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = validate_csv_rows(args.input, args.required_fields)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

