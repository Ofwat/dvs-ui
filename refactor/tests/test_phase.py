from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from components.phase import build_phase


class PhaseBannerTests(unittest.TestCase):
    def test_phase_banner_uses_standard_structure(self):
        banner = build_phase()
        payload = banner.to_plotly_json()
        self.assertEqual(payload["type"], "Div")
        self.assertIn("govuk-phase-banner", payload["props"]["className"])

        content = payload["props"]["children"][0].to_plotly_json()
        self.assertEqual(content["type"], "P")
        self.assertEqual(content["props"]["children"][0].to_plotly_json()["props"]["children"], "Alpha")


if __name__ == "__main__":
    unittest.main()
