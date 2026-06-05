from __future__ import annotations

import unittest
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from components.button_start import build_start_button
from pages.home import build_home_masthead


class ButtonStartTests(unittest.TestCase):
    def test_button_includes_svg_icon(self):
        button = build_start_button()
        payload = button.to_plotly_json()
        children = payload["props"]["children"]

        self.assertEqual(payload["type"], "Link")
        self.assertEqual(children[0].to_plotly_json()["type"], "Span")
        self.assertEqual(children[1].to_plotly_json()["type"], "Svg")
        self.assertEqual(
            children[1].to_plotly_json()["props"]["className"], "govuk-button__start-icon"
        )

    def test_home_masthead_uses_start_button_component(self):
        masthead = build_home_masthead()
        text = str(masthead)
        self.assertIn("govuk-button__start-icon", text)
        self.assertIn("Get started", text)


if __name__ == "__main__":
    unittest.main()
