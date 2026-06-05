from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from components.radios import build_radio_group
from pages.dimension_loader_service import build_dimension_loader_content


class RadioGroupTests(unittest.TestCase):
    def test_radio_group_renders_fieldset_and_options(self):
        component = build_radio_group(
            legend="Choose environment",
            radio_id="dimension-loader-environment",
            options=[
                {"label": "dev", "value": "dev"},
                {"label": "prod", "value": "prod"},
            ],
            value="dev",
        )
        fieldset = component.children[0]
        radios = fieldset.children[1]
        self.assertEqual(component.className, "govuk-form-group")
        self.assertEqual(fieldset.className, "govuk-fieldset")
        self.assertEqual(radios.className, "govuk-radios app-radio-group")
        self.assertEqual(radios.inputClassName, "govuk-radios__input")
        self.assertEqual(
            radios.labelClassName,
            "govuk-label app-radio-group__label",
        )
        self.assertFalse(radios.inline)
        self.assertEqual(radios.id, "dimension-loader-environment")
        self.assertEqual(radios.value, "dev")
        self.assertEqual([option["value"] for option in radios.options], ["dev", "prod"])

    def test_dimension_loader_page_uses_shared_radio_group(self):
        content = build_dimension_loader_content()
        radios = content.children[0].children[3].children[0].children[1]
        self.assertEqual(radios.id, "dimension-loader-environment")
        self.assertEqual(radios.className, "govuk-radios app-radio-group")
        self.assertEqual(radios.inputClassName, "govuk-radios__input")
        self.assertEqual(
            radios.labelClassName,
            "govuk-label app-radio-group__label",
        )
        self.assertFalse(radios.inline)


if __name__ == "__main__":
    unittest.main()
