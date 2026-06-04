from __future__ import annotations

import unittest
from types import SimpleNamespace
import threading
from unittest.mock import patch
from urllib import request as urllib_request
from urllib.parse import urlencode

from azure.core.exceptions import ClientAuthenticationError

from refactor import online_auth


class _FakeApp:
    def __init__(self):
        self.last_kwargs = None

    def initiate_auth_code_flow(self, scopes, **kwargs):
        self.last_kwargs = {"scopes": scopes, **kwargs}
        return {"auth_uri": "https://example.test/auth"}


class OnlineAuthTests(unittest.TestCase):
    def test_initiate_auth_code_flow_uses_form_post(self):
        app = _FakeApp()

        flow = online_auth._initiate_auth_code_flow(  # noqa: SLF001
            app,
            ["scope-a"],
            "http://localhost:8400",
            claims=None,
            login_hint="alice@example.com",
        )

        self.assertEqual(flow["auth_uri"], "https://example.test/auth")
        self.assertEqual(app.last_kwargs["response_mode"], "form_post")
        self.assertEqual(app.last_kwargs["prompt"], "select_account")
        self.assertEqual(app.last_kwargs["login_hint"], "alice@example.com")

    @patch("refactor.online_auth._clear_auth_record")
    @patch("refactor.online_auth._get_credential")
    def test_acquire_token_does_not_retry_unexpected_errors(self, get_credential, clear_auth_record):
        class _BoomCredential:
            def get_token(self, scope):
                raise ValueError("boom")

        get_credential.return_value = _BoomCredential()

        with self.assertRaises(ValueError):
            online_auth._acquire_token("scope-a")  # noqa: SLF001

        clear_auth_record.assert_not_called()

    @patch("refactor.online_auth._save_auth_record")
    @patch("refactor.online_auth._get_credential")
    def test_acquire_token_reauthenticates_after_client_auth_failure(self, get_credential, save_auth_record):
        class _FirstCredential:
            def get_token(self, scope):
                raise ClientAuthenticationError(message="expired")

        class _SecondCredential:
            def authenticate(self, scopes):
                self.scopes = scopes
                return SimpleNamespace(
                    serialize=lambda: "{}",
                )

        class _ThirdCredential:
            def get_token(self, scope):
                return SimpleNamespace(token="tok-1", expires_on=1234.0)

        get_credential.side_effect = [_FirstCredential(), _SecondCredential(), _ThirdCredential()]

        with patch("refactor.online_auth._clear_auth_record") as clear_auth_record:
            token = online_auth._acquire_token("scope-a")  # noqa: SLF001

        self.assertEqual(token.token, "tok-1")
        clear_auth_record.assert_called_once()
        save_auth_record.assert_called_once()

    def test_form_post_redirect_server_accepts_post_callback(self):
        server = online_auth._FormPostAuthCodeRedirectServer("localhost", 0, timeout=5)  # noqa: SLF001
        port = server.server_port
        result_holder = {}

        def wait_for_redirect():
            result_holder["response"] = server.wait_for_redirect()

        thread = threading.Thread(target=wait_for_redirect, daemon=True)
        thread.start()

        body = urlencode({"code": "abc123", "state": "xyz"}).encode("utf-8")
        urllib_request.urlopen(
            f"http://localhost:{port}/callback",
            data=body,
            timeout=5,
        ).read()

        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result_holder["response"], {"code": "abc123", "state": "xyz"})

    @patch("refactor.online_auth._cached_token")
    @patch("refactor.online_auth._load_auth_record")
    def test_get_current_auth_identity_uses_cached_record(self, load_auth_record, cached_token):
        load_auth_record.return_value = SimpleNamespace(username="user@example.com")
        cached_token.return_value = {"token": "tok", "expires_on": 2000.0}

        identity = online_auth.get_current_auth_identity()

        self.assertTrue(identity["signed_in"])
        self.assertEqual(identity["display_name"], "User Example")
        self.assertEqual(identity["username"], "user@example.com")
        self.assertEqual(identity["initials"], "UE")
        self.assertIn("Signed in", identity["status_message"])

    @patch("refactor.online_auth._clear_auth_record")
    @patch("refactor.online_auth.TOKEN_MANAGER")
    def test_clear_authentication_state_clears_tokens_and_record(self, token_manager, clear_auth_record):
        online_auth._CREDENTIAL = object()  # noqa: SLF001

        online_auth.clear_authentication_state()

        clear_auth_record.assert_called_once()
        token_manager.clear_cache.assert_called_once()
        self.assertIsNone(online_auth._CREDENTIAL)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
