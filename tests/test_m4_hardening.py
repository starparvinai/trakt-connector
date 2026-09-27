"""M4 tests: signed state, refresh locking, 429/426 handling, API-key gate, invoke."""
from __future__ import annotations

import threading
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import tools_registry
from app.auth import mint_api_key, resolve_user
from app.config import Settings, get_settings
from app.state import issue, make_serializer, redeem
from app.token_store import TokenStore
from app.tools_registry import UnknownToolError
from app.trakt_client import TraktClient, VIPRequired


def _settings(**over) -> Settings:
    base = dict(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:8000/oauth/callback",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    base.update(over)
    return Settings(**base)


def _iso(delta_days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).isoformat()


def _client(handler, tmp_path, expired: bool = False) -> TraktClient:
    s = _settings(db_path=str(tmp_path / "t.db"))
    store = TokenStore(s.db_path, s.token_encryption_key)
    store.save_tokens("u1", "a1", "r1", _iso(-1 if expired else 7))
    return TraktClient(s, store, httpx.Client(transport=httpx.MockTransport(handler)))


# --- Signed state -----------------------------------------------------------


def test_state_roundtrip_tamper_and_wrong_secret():
    s = make_serializer("s3cr3t")
    tok = issue(s, nonce="abc")
    assert redeem(s, tok)["nonce"] == "abc"
    with pytest.raises(ValueError):
        redeem(s, tok + "tampered")
    with pytest.raises(ValueError):
        redeem(make_serializer("other-secret"), tok)


# --- 429 / 426 ---------------------------------------------------------------


def test_429_backoff_retries_then_succeeds(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json=[])

    client = _client(handler, tmp_path)
    resp = client.request("u1", "GET", "/sync/history/movies")
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_426_raises_vip_required(tmp_path):
    client = _client(lambda r: httpx.Response(426, json={}), tmp_path)
    with pytest.raises(VIPRequired):
        client.request("u1", "GET", "/sync/history/movies")


# --- Refresh locking ---------------------------------------------------------


def test_concurrent_refresh_happens_once(tmp_path):
    token_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            token_calls["n"] += 1
            time.sleep(0.1)  # force the threads to overlap
            return httpx.Response(
                200,
                json={"access_token": "a2", "refresh_token": "r2", "expires_in": 3600},
            )
        return httpx.Response(200, json=[])

    client = _client(handler, tmp_path, expired=True)
    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(client.get_valid_access_token("u1")))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert token_calls["n"] == 1, "single-use refresh must fire exactly once"
    assert results == ["a2", "a2"]


# --- API keys -----------------------------------------------------------------


def test_api_key_mint_resolve_and_reject(tmp_path):
    db = str(tmp_path / "k.db")
    key = mint_api_key(db, "u_abc")
    assert key.startswith("tk_")
    assert resolve_user(db, key) == "u_abc"
    assert resolve_user(db, "tk_bogus") is None
    assert resolve_user(db, "") is None


def test_registry_rejects_unknown_tool(tmp_path):
    client = _client(lambda r: httpx.Response(200, json=[]), tmp_path)
    with pytest.raises(UnknownToolError):
        tools_registry.invoke(client, "u1", "nope", {})


# --- HTTP edge -----------------------------------------------------------------


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "api.db")
    monkeypatch.setenv("TRAKT_CLIENT_ID", "cid")
    monkeypatch.setenv("TRAKT_CLIENT_SECRET", "csec")
    monkeypatch.setenv("TRAKT_REDIRECT_URI", "http://localhost:8000/oauth/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TRAKT_TOKEN_DB", db)
    # httpx honors proxy env vars; strip them so ambient config (e.g. odd
    # no_proxy entries) can't break client construction in tests.
    for var in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    return db


def test_oauth_start_redirects_with_signed_state(env):
    from app import main as main_mod

    c = TestClient(main_mod.app)
    r = c.get("/oauth/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "trakt.tv/oauth/authorize" in loc and "state=" in loc


def test_oauth_callback_issues_api_key(env, monkeypatch):
    from app import main as main_mod

    settings = get_settings()
    secret = settings.oauth_state_secret or settings.token_encryption_key
    state = issue(make_serializer(secret), nonce="n1")
    monkeypatch.setattr(
        main_mod,
        "exchange_code_for_tokens",
        lambda settings, client, code: {
            "access_token": "a",
            "refresh_token": "r",
            "expires_at": _iso(7),
        },
    )
    c = TestClient(main_mod.app)
    r = c.get("/oauth/callback", params={"code": "c", "state": state})
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"].startswith("tk_")
    assert resolve_user(env, body["api_key"]) == body["connector_user_id"]

    r2 = c.get("/oauth/callback", params={"code": "c", "state": state + "x"})
    assert r2.status_code == 400


def test_invoke_requires_key_and_dispatches(env):
    from app import main as main_mod

    c = TestClient(main_mod.app)
    assert (
        c.post("/tools/invoke", json={"tool": "stats", "arguments": {}}).status_code
        == 401
    )
    assert c.get("/tools/list").status_code == 401

    key = mint_api_key(env, "u1")
    saved = tools_registry._REGISTRY["stats"]
    tools_registry._REGISTRY["stats"] = (lambda client, user_id: {"stub": True}, False)
    try:
        r = c.post(
            "/tools/invoke",
            json={"tool": "stats", "arguments": {}},
            headers={"X-API-Key": key},
        )
        assert r.status_code == 200
        assert r.json() == {"tool": "stats", "result": {"stub": True}}

        r = c.post(
            "/tools/invoke",
            json={"tool": "nope", "arguments": {}},
            headers={"X-API-Key": key},
        )
        assert r.status_code == 404

        r = c.get("/tools/list", headers={"X-API-Key": key})
        assert r.status_code == 200
        assert any(t["name"] == "search" for t in r.json()["tools"])
    finally:
        tools_registry._REGISTRY["stats"] = saved
