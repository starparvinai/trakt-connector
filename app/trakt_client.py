"""Trakt API client with header injection, 401->refresh->retry, 429 backoff."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from .config import Settings
from .token_store import TokenStore
from .trakt_oauth import ReauthRequired, TraktAPIError, refresh_access_token


class VIPRequired(Exception):
    """Endpoint needs Trakt VIP (HTTP 426). Surface, don't retry."""


class TraktClient:
    def __init__(
        self,
        settings: Settings,
        store: TokenStore,
        http: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.http = http or httpx.Client(timeout=15)

    def _headers(self, access_token: str) -> dict:
        return {
            "trakt-api-key": self.settings.client_id,
            "trakt-api-version": "2",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

    def _is_expired(self, expires_at: str, skew_seconds: int = 300) -> bool:
        from datetime import timedelta

        try:
            exp = datetime.fromisoformat(expires_at)
        except ValueError:
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp - timedelta(seconds=skew_seconds)

    def get_valid_access_token(self, user_id: str) -> str:
        tokens = self.store.get_tokens(user_id)
        if not tokens:
            raise ReauthRequired("No Trakt tokens stored — authorize first.")
        if self._is_expired(tokens["expires_at"]):
            tokens = self._rotate(user_id, tokens["refresh_token"])
        return tokens["access_token"]

    def _rotate(self, user_id: str, refresh_token: str) -> dict:
        """Refresh and atomically replace the stored pair (single-use)."""
        try:
            new = refresh_access_token(self.settings, self.http, refresh_token)
        except ReauthRequired:
            self.store.delete_tokens(user_id)
            raise
        self.store.save_tokens(
            user_id, new["access_token"], new["refresh_token"], new["expires_at"]
        )
        return new

    def request(self, user_id: str, method: str, path: str, **kwargs) -> httpx.Response:
        """Send an authenticated Trakt API request with safe retries."""
        access_token = self.get_valid_access_token(user_id)
        url = f"{self.settings.api_base}{path}"
        resp = self.http.request(
            method, url, headers=self._headers(access_token), **kwargs
        )

        if resp.status_code == 401:
            # Refresh once, then retry exactly once.
            tokens = self.store.get_tokens(user_id)
            if not tokens:
                raise ReauthRequired("Trakt session expired — re-authorize once.")
            try:
                new = self._rotate(user_id, tokens["refresh_token"])
            except ReauthRequired:
                raise
            resp = self.http.request(
                method, url, headers=self._headers(new["access_token"]), **kwargs
            )
            if resp.status_code == 401:
                self.store.delete_tokens(user_id)
                raise ReauthRequired("Trakt rejected the refreshed token — re-authorize.")

        if resp.status_code == 429:
            resp = self._retry_with_backoff(method, url, access_token, kwargs, resp)

        if resp.status_code == 426:
            raise VIPRequired("That needs Trakt VIP.")
        if resp.status_code >= 400:
            raise TraktAPIError(f"Trakt API {resp.status_code}: {resp.text[:300]}")
        return resp

    def _retry_with_backoff(
        self, method: str, url: str, access_token: str, kwargs: dict, first: httpx.Response
    ) -> httpx.Response:
        resp = first
        for _ in range(3):
            if resp.status_code != 429:
                return resp
            wait = resp.headers.get("Retry-After")
            try:
                delay = max(0, int(wait)) if wait else 2
            except ValueError:
                delay = 2
            time.sleep(min(delay, 30))
            resp = self.http.request(
                method, url, headers=self._headers(access_token), **kwargs
            )
        return resp
