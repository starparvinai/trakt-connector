"""M3 tests: write tools post correct bodies and gate destructive ops."""
from __future__ import annotations

import httpx
from cryptography.fernet import Fernet

from app import write_tools
from app.config import Settings
from app.token_store import TokenStore
from app.trakt_client import TraktClient


def _settings(**over) -> Settings:
    base = dict(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:8000/oauth/callback",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    base.update(over)
    return Settings(**base)


def _future_iso() -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()


class _Recorder:
    """Mock transport that records every request and serves canned bodies."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content.decode()) if request.content else {}
        self.calls.append((request.method, request.url.path, body))
        canned = self.routes.get(request.url.path)
        assert canned is not None, f"unexpected path {request.url.path}"
        return httpx.Response(200, json=canned() if callable(canned) else canned)

    def client_for(self, tmp_path) -> TraktClient:
        s = _settings(db_path=str(tmp_path / "t.db"))
        store = TokenStore(s.db_path, s.token_encryption_key)
        store.save_tokens("u1", "access", "refresh", _future_iso())
        return TraktClient(s, store, httpx.Client(transport=httpx.MockTransport(self.handler)))


def test_log_movie_posts_ids_and_watched_at(tmp_path):
    rec = _Recorder({"/sync/history": {"added": {"movies": 1}, "not_found": {}}})
    client = rec.client_for(tmp_path)
    out = write_tools.log_movie(client, "u1", "dune-2021", watched_at="2026-09-26T20:00:00Z")
    assert out["status"] == "logged" and out["added"] == 1
    method, path, body = rec.calls[0]
    assert (method, path) == ("POST", "/sync/history")
    assert body["movies"][0]["ids"] == {"slug": "dune-2021"}
    assert body["movies"][0]["watched_at"] == "2026-09-26T20:00:00Z"


def test_log_episode_resolves_episode_id(tmp_path):
    rec = _Recorder(
        {
            "/shows/severance/seasons/1": [
                {"number": 1, "ids": {"trakt": 101}},
                {"number": 2, "ids": {"trakt": 102}},
            ],
            "/sync/history": {"added": {"episodes": 1}},
        }
    )
    client = rec.client_for(tmp_path)
    out = write_tools.log_episode(client, "u1", "severance", 1, 2)
    assert out["status"] == "logged" and out["number"] == 2
    post = [c for c in rec.calls if c[1] == "/sync/history"][0]
    assert post[2]["episodes"][0]["ids"] == {"trakt": 102}


def test_rate_movie_sends_rating(tmp_path):
    rec = _Recorder({"/sync/ratings": {"added": {"movies": 1}}})
    client = rec.client_for(tmp_path)
    out = write_tools.rate_movie(client, "u1", "12345", 9)
    assert out == {"status": "rated", "type": "movies", "rating": 9, "added": 1}
    _, _, body = rec.calls[0]
    assert body["movies"][0]["rating"] == 9
    assert body["movies"][0]["ids"] == {"trakt": 12345}


def test_remove_from_watchlist_needs_confirmation(tmp_path):
    rec = _Recorder({"/sync/watchlist/remove": {"deleted": {"movies": 1}}})
    client = rec.client_for(tmp_path)
    preview = write_tools.remove_from_watchlist(client, "u1", "movies", "dune-2021")
    assert preview["status"] == "needs_confirmation"
    assert preview["action"] == "remove_from_watchlist"
    assert rec.calls == [], "must not POST before confirmation"

    done = write_tools.remove_from_watchlist(client, "u1", "movies", "dune-2021", confirm=True)
    assert done["status"] == "removed_from_watchlist" and done["deleted"] == 1
    method, path, body = rec.calls[0]
    assert (method, path) == ("POST", "/sync/watchlist/remove")
    assert body["movies"][0]["ids"] == {"slug": "dune-2021"}


def test_remove_movie_from_history_gate(tmp_path):
    rec = _Recorder({"/sync/history/remove": {"deleted": {"movies": 1}}})
    client = rec.client_for(tmp_path)
    preview = write_tools.remove_movie_from_history(client, "u1", "dune-2021")
    assert preview["status"] == "needs_confirmation"
    assert rec.calls == []
    done = write_tools.remove_movie_from_history(client, "u1", "dune-2021", confirm=True)
    assert done["status"] == "removed" and done["deleted"] == 1
