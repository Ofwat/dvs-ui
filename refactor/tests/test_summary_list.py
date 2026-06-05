from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from components.summary_list import build_summary_card, build_summary_list, build_summary_list_row


class SummaryListTests(unittest.TestCase):
    def test_build_summary_list_row_without_actions(self):
        row = build_summary_list_row("Name", "Sarah Philips")
        self.assertEqual(row.className, "govuk-summary-list__row")
        self.assertEqual(row.children[0].className, "govuk-summary-list__key")
        self.assertEqual(row.children[1].className, "govuk-summary-list__value")

    def test_build_summary_list_with_actions(self):
        summary = build_summary_list(
            rows=[
                {
                    "key": "Date of birth",
                    "value": "5 January 1978",
                    "actions": [
                        {
                            "label": "Change",
                            "href": "/change-dob",
                            "visually_hidden_text": "date of birth",
                        }
                    ],
                }
            ]
        )
        self.assertEqual(summary.className, "govuk-summary-list")
        row = summary.children[0]
        self.assertEqual(row.children[2].className, "govuk-summary-list__actions")
        action = row.children[2].children[0]
        self.assertEqual(action.href, "/change-dob")
        self.assertEqual(action.className, "govuk-link")
        self.assertEqual(action.target, "_blank")
        self.assertEqual(action.rel, "noopener noreferrer")

    def test_build_summary_card_with_actions(self):
        card = build_summary_card(
            "Lead tenant",
            rows=[
                {
                    "key": "Age",
                    "value": "38",
                }
            ],
            actions=[
                {
                    "label": "Edit",
                    "href": "/edit",
                    "visually_hidden_text": "lead tenant",
                }
            ],
        )
        self.assertEqual(card.className, "govuk-summary-card")
        title_wrapper = card.children[0]
        self.assertEqual(title_wrapper.className, "govuk-summary-card__title-wrapper")
        self.assertEqual(title_wrapper.children[0].className, "govuk-summary-card__title")
        self.assertEqual(title_wrapper.children[1].className, "govuk-summary-card__actions")


if __name__ == "__main__":
    unittest.main()
