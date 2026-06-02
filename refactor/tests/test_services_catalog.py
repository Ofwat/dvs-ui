from __future__ import annotations

import unittest
import sys
from pathlib import Path

REFACTOR_DIR = Path(__file__).resolve().parents[1]
if str(REFACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(REFACTOR_DIR))

from pages import get_navigation_pages, get_page_by_path


class ServicesCatalogTests(unittest.TestCase):
    def test_services_navigation_includes_dimension_loader(self):
        pages = get_navigation_pages()
        self.assertIn({"label": "Services", "path": "/services"}, pages)

    def test_dimension_loader_route_exists(self):
        page = get_page_by_path("/services/dimension-loader")
        self.assertEqual(page["label"], "Dimension Loader")
        self.assertEqual(page["path"], "/services/dimension-loader")


if __name__ == "__main__":
    unittest.main()
