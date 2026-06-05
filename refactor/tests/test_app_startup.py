from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


class AppStartupTests(unittest.TestCase):
    def test_app_import_does_not_load_example_dqchecks(self):
        refactor_dir = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(refactor_dir))
        try:
            sys.modules.pop("example_dqchecks", None)
            module = importlib.import_module("app")
            self.assertIsNotNone(module)
            self.assertNotIn("example_dqchecks", sys.modules)
        finally:
            sys.path.remove(str(refactor_dir))
            sys.modules.pop("app", None)

    def test_app_layout_includes_footer(self):
        refactor_dir = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(refactor_dir))
        try:
            sys.modules.pop("app", None)
            module = importlib.import_module("app")
            layout_text = str(module.app.layout)
            self.assertIn("govuk-footer", layout_text)
            self.assertIn("Crown copyright", layout_text)
        finally:
            sys.path.remove(str(refactor_dir))
            sys.modules.pop("app", None)


if __name__ == "__main__":
    unittest.main()
