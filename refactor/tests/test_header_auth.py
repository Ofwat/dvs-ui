from __future__ import annotations

import unittest

from refactor.components.header import build_auth_widget


class HeaderAuthTests(unittest.TestCase):
    def test_signed_out_widget_shows_login(self):
        widget = build_auth_widget()
        payload = widget.to_plotly_json()
        children = payload["props"]["children"]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].to_plotly_json()["props"]["children"], "Log in")

    def test_signed_in_widget_shows_initials_and_logout(self):
        widget = build_auth_widget(
            {
                "signed_in": True,
                "display_name": "Example User",
                "username": "user@example.com",
                "initials": "SZ",
                "status_message": "Signed in.",
                "permission_summary": "Granted: SharePoint, Fabric | Not currently available: OneLake",
            }
        )
        payload = widget.to_plotly_json()
        children = payload["props"]["children"]
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0].to_plotly_json()["props"]["children"], "SZ")
        text_column = children[1].to_plotly_json()["props"]["children"]
        self.assertEqual(text_column[0].to_plotly_json()["props"]["children"], "Example User")
        logout = text_column[1].to_plotly_json()
        self.assertEqual(logout["type"], "A")
        self.assertEqual(logout["props"]["children"], "Log out")


if __name__ == "__main__":
    unittest.main()
