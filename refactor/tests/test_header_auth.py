from __future__ import annotations

import unittest

from refactor.components.header import build_auth_widget


class HeaderAuthTests(unittest.TestCase):
    def test_signed_out_widget_shows_login(self):
        widget = build_auth_widget()
        text = str(widget)
        self.assertIn("Log in", text)
        self.assertIn("Not signed in", text)

    def test_signed_in_widget_shows_initials_and_logout(self):
        widget = build_auth_widget(
            {
                "signed_in": True,
                "display_name": "Example User",
                "username": "user@example.com",
                "initials": "SZ",
                "status_message": "Signed in.",
            }
        )
        text = str(widget)
        self.assertIn("SZ", text)
        self.assertIn("Log out", text)


if __name__ == "__main__":
    unittest.main()
