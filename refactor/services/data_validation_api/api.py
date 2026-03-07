from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import csv
from typing import Any

COMPONENT_NAME = "data-validation-api"


@dataclass(frozen=True)
class ValidationIssue:
    row_number: int
    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationResult:
    component: str
    valid: bool
    total_rows: int
    issue_count: int
    issues: list[ValidationIssue]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = [issue.to_dict() for issue in self.issues]
        return payload


def validate_csv_rows(csv_path: str | Path, required_fields: list[str]) -> ValidationResult:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    issues: list[ValidationIssue] = []
    total_rows = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing_columns = [field for field in required_fields if field not in headers]
        for field in missing_columns:
            issues.append(
                ValidationIssue(
                    row_number=0,
                    field=field,
                    code="missing_column",
                    message=f"Required column '{field}' is missing from input CSV.",
                )
            )

        for index, row in enumerate(reader, start=1):
            total_rows += 1
            for field in required_fields:
                if field not in row:
                    continue
                value = row.get(field)
                if value is None or str(value).strip() == "":
                    issues.append(
                        ValidationIssue(
                            row_number=index,
                            field=field,
                            code="missing_value",
                            message=f"Field '{field}' is required.",
                        )
                    )

    return ValidationResult(
        component=COMPONENT_NAME,
        valid=len(issues) == 0,
        total_rows=total_rows,
        issue_count=len(issues),
        issues=issues,
    )

