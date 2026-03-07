import tempfile
import unittest
from pathlib import Path

from refactor.services.data_validation_api.api import validate_csv_rows


class DataValidationApiTests(unittest.TestCase):
    def _write_csv(self, content: str) -> Path:
        tmp_dir = Path(tempfile.mkdtemp(prefix="data-validation-api-"))
        csv_path = tmp_dir / "input.csv"
        csv_path.write_text(content, encoding="utf-8")
        return csv_path

    def test_validate_csv_rows_returns_valid_result_when_required_fields_exist(self):
        path = self._write_csv("name,email\nAlice,alice@example.com\n")

        result = validate_csv_rows(path, ["name", "email"])

        self.assertTrue(result.valid)
        self.assertEqual(result.total_rows, 1)
        self.assertEqual(result.issue_count, 0)
        self.assertEqual(result.issues, [])

    def test_validate_csv_rows_reports_missing_column(self):
        path = self._write_csv("name\nAlice\n")

        result = validate_csv_rows(path, ["name", "email"])

        self.assertFalse(result.valid)
        self.assertEqual(result.issue_count, 1)
        issue = result.issues[0]
        self.assertEqual(issue.code, "missing_column")
        self.assertEqual(issue.field, "email")
        self.assertEqual(issue.row_number, 0)

    def test_validate_csv_rows_reports_missing_required_value(self):
        path = self._write_csv("name,email\nAlice,\n")

        result = validate_csv_rows(path, ["name", "email"])

        self.assertFalse(result.valid)
        self.assertEqual(result.issue_count, 1)
        issue = result.issues[0]
        self.assertEqual(issue.code, "missing_value")
        self.assertEqual(issue.field, "email")
        self.assertEqual(issue.row_number, 1)


if __name__ == "__main__":
    unittest.main()

