from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable


@dataclass(frozen=True)
class TokenEntry:
    token: str
    expires_on: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "expires_on": self.expires_on,
        }


class TokenManager:
    """In-memory token manager with auto-refresh before expiry."""

    def __init__(
        self,
        token_provider: Callable[[str], Any],
        refresh_margin_seconds: int = 60,
        time_fn: Callable[[], float] = time.time,
    ):
        self._token_provider = token_provider
        self._refresh_margin_seconds = max(0, refresh_margin_seconds)
        self._time_fn = time_fn
        self._cache: dict[str, TokenEntry] = {}

    def get_token(self, scope: str, force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh:
            cached = self._cache.get(scope)
            if cached and not self._is_stale(cached):
                return cached.to_dict()

        token_obj = self._token_provider(scope)
        entry = self._coerce_entry(token_obj)
        self._cache[scope] = entry
        return entry.to_dict()

    def get_headers(self, scope: str, force_refresh: bool = False) -> dict[str, str]:
        token_data = self.get_token(scope, force_refresh=force_refresh)
        return {"Authorization": f"Bearer {token_data['token']}"}

    def get_cached_token(self, scope: str) -> dict[str, Any] | None:
        cached = self._cache.get(scope)
        if not cached:
            return None
        if self._is_stale(cached):
            self._cache.pop(scope, None)
            return None
        return cached.to_dict()

    def invalidate(self, scope: str):
        self._cache.pop(scope, None)

    def clear_cache(self):
        self._cache.clear()

    def _is_stale(self, entry: TokenEntry) -> bool:
        return entry.expires_on <= (self._time_fn() + self._refresh_margin_seconds)

    @staticmethod
    def _coerce_entry(token_obj: Any) -> TokenEntry:
        token_value = getattr(token_obj, "token", None)
        expires_on = getattr(token_obj, "expires_on", None)
        if token_value is None and isinstance(token_obj, dict):
            token_value = token_obj.get("token")
            expires_on = token_obj.get("expires_on")
        if token_value is None or expires_on is None:
            raise ValueError("token_provider must return token and expires_on")
        return TokenEntry(token=str(token_value), expires_on=float(expires_on))
