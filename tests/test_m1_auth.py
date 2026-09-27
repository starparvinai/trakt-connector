"""M1 tests: encrypted store, single-use refresh rotation, 401 retry, invalid_grant."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.token_store import TokenStore
from app.trakt_client import ReauthRequired, TraktClient
from app.trakt_oauth import refresh_access_token


def _settings(**over) -> Settings:
    base = dict(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:8000/oauth/callback",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    base.update(over)
    return Settings(**base)


def _future_iso(days: int = 6) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def test_token_store_roundtrip_and_encryption(tmp_path):
    s = _settings(db_path=str(tmp_path / "t.db"))
    store = TokenStore(s.db_path, s.token_encryption_key)
    store.save_tokens("u1", "access-1", "refresh-1", _future_iso())

    # Ciphertext at rest: raw DB must not contain plaintext tokens.
    raw = (tmp_path / "t.db").read_bytes()
    assert b"access-1" not in raw and b"refresh-1" not in raw

    got = store.get_tokens("u1")
    assert got["access_token"] == "access-1"
    assert got["refresh_token"] == "refresh-1"

    store.delete_tokens("u1")
    assert store.get_tokens("u1") is None


def _token_transport(new_access: str, new_refresh: str, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": new_access,
                "refresh_token": new_refresh,
                "expires_in": 7 * 24 * 3600,
            },
        )

    return httpx.MockTransport(handler)


def test_refresh_rotation_replaces_pair_atomically(tmp_path):
    s = _settings(db_path=str(tmp_path / "t.db"))
    store = TokenStore(s.db_path, s.token_encryption_key)
    store.save_tokens("u1", "old-access", "old-refresh", _past_iso())

    calls: list = []
    http = httpx.Client(transport=_token_transport("new-access", "new-refresh", calls))
    new = refresh_access_token(s, http, "old-refresh")

    # Single-use semantics: caller must persist the NEW pair, old refresh is dead.
    store.save_tokens("u1", new["access_token"], new["refresh_token"], new["expires_at"])
    got = store.get_tokens("u1")
    assert got["access_token"] == "new-access"
    assert got["refresh_token"] == "new-refresh"
    assert calls[0]["grant_type"] == "refresh_token"
    assert calls[0]["refresh_token"] == "old-refresh"


def test_401_triggers_single_refresh_then_retry(tmp_path):
    s = _settings(db_path=str(tmp_path / "t.db"))
    store = TokenStore(s.db_path, s.token_encryption_key)
    store.save_tokens("u1", "stale-access", "good-refresh", _future_iso())

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            seen.append("refresh")
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "expires_in": 7 * 24 * 3600,
                },
            )
        seen.append(("api", request.headers.get("authorization")))
        if request.headers.get("authorization") == "Bearer stale-access":
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"ok": True})

    client = TraktClient(s, store, httpx.Client(transport=httpx.MockTransport(handler)))
    resp = client.request("u1", "GET", "/sync/history/movies")
    assert resp.status_code == 200
    assert seen.count("refresh") == 1  # exactly one refresh, then one retry
    got = store.get_tokens("u1")
    assert got["access_token"] == "fresh-access"
    assert got["refresh_token"] == "fresh-refresh"


def test_invalid_grant_deletes_tokens_and_raises_reauth(tmp_path):
    s = _settings(db_path=str(tmp_path / "t.db"))
    store = TokenStore(s.db_path, s.token_encryption_key)
    store.save_tokens("u1", "dead-access", "dead-refresh", _past_iso())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "session not found"}
        )

    client = TraktClient(s, store, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ReauthRequired):
        client.get_valid_access_token("u1")
    assert store.get_tokens("u1") is None  # dead tokens are purged
