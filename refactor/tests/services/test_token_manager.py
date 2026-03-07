import unittest

from refactor.services.auth.token_manager import TokenManager


class TokenManagerTests(unittest.TestCase):
    def test_reuses_cached_token_when_not_stale(self):
        calls = []
        now = 1000.0

        def provider(scope: str):
            calls.append(scope)
            return {"token": f"tok-{len(calls)}", "expires_on": now + 120}

        manager = TokenManager(provider, refresh_margin_seconds=30, time_fn=lambda: now)
        first = manager.get_token("scope-1")
        second = manager.get_token("scope-1")

        self.assertEqual(first["token"], "tok-1")
        self.assertEqual(second["token"], "tok-1")
        self.assertEqual(calls, ["scope-1"])

    def test_refreshes_token_when_stale(self):
        calls = []
        now_holder = {"now": 1000.0}

        def now():
            return now_holder["now"]

        def provider(scope: str):
            calls.append(scope)
            return {"token": f"tok-{len(calls)}", "expires_on": now() + 40}

        manager = TokenManager(provider, refresh_margin_seconds=30, time_fn=now)
        first = manager.get_token("scope-1")

        now_holder["now"] += 11
        second = manager.get_token("scope-1")

        self.assertEqual(first["token"], "tok-1")
        self.assertEqual(second["token"], "tok-2")
        self.assertEqual(calls, ["scope-1", "scope-1"])

    def test_force_refresh_bypasses_cache(self):
        calls = []
        now = 1000.0

        def provider(scope: str):
            calls.append(scope)
            return {"token": f"tok-{len(calls)}", "expires_on": now + 120}

        manager = TokenManager(provider, refresh_margin_seconds=30, time_fn=lambda: now)
        first = manager.get_token("scope-1")
        second = manager.get_token("scope-1", force_refresh=True)

        self.assertEqual(first["token"], "tok-1")
        self.assertEqual(second["token"], "tok-2")
        self.assertEqual(calls, ["scope-1", "scope-1"])


if __name__ == "__main__":
    unittest.main()

