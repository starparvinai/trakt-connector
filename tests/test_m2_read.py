"""M2 tests: read tools normalize Trakt's nested payloads into flat schemas."""
from __future__ import annotations

import httpx
from cryptography.fernet import Fernet

from app import read_tools
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


def _client_for(routes: dict, tmp_path) -> TraktClient:
    s = _settings(db_path=str(tmp_path / "t.db"))
    store = TokenStore(s.db_path, s.token_encryption_key)
    store.save_tokens("u1", "access", "refresh", _future_iso())

    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        assert body is not None, f"unexpected path {request.url.path}"
        return httpx.Response(200, json=body() if callable(body) else body)

    return TraktClient(s, store, httpx.Client(transport=httpx.MockTransport(handler)))


def test_search_flattens_nested_movie(tmp_path):
    client = _client_for(
        {
            "/search/movie": [
                {
                    "type": "movie",
                    "score": 99.1,
                    "movie": {
                        "title": "Dune",
                        "year": 2021,
                        "ids": {"trakt": 1, "slug": "dune-2021", "tmdb": 2, "imdb": "tt3"},
                        "overview": "Spice.",
                    },
                }
            ]
        },
        tmp_path,
    )
    out = read_tools.search(client, "u1", "dune")
    assert out == [
        {
            "title": "Dune",
            "year": 2021,
            "ids": {"trakt": 1, "slug": "dune-2021", "tmdb": 2, "imdb": "tt3"},
            "type": "movie",
            "overview": "Spice.",
        }
    ]


def test_history_filters_by_date_and_unwraps_episodes(tmp_path):
    client = _client_for(
        {
            "/sync/history/episodes": [
                {
                    "watched_at": "2026-09-20T10:00:00Z",
                    "type": "episode",
                    "episode": {
                        "season": 1,
                        "number": 2,
                        "title": "Pilot",
                        "ids": {"trakt": 9, "slug": None, "tmdb": None, "imdb": None},
                    },
                    "show": {
                        "title": "Severance",
                        "year": 2022,
                        "ids": {"trakt": 8, "slug": "severance", "tmdb": 7, "imdb": "tt6"},
                    },
                },
                {
                    "watched_at": "2026-09-26T10:00:00Z",
                    "type": "episode",
                    "episode": {"season": 1, "number": 3, "title": "Next", "ids": {}},
                    "show": {"title": "Severance", "year": 2022, "ids": {}},
                },
            ]
        },
        tmp_path,
    )
    out = read_tools.get_history(
        client, "u1", "episodes", start_date="2026-09-25", end_date="2026-09-27"
    )
    assert len(out) == 1
    assert out[0]["show"]["title"] == "Severance"
    assert out[0]["episode"] == {"season": 1, "number": 3, "title": "Next"}


def test_next_episode_returns_flat_next(tmp_path):
    client = _client_for(
        {
            "/shows/breaking-bad/progress/watched": {
                "aired": 62,
                "completed": 10,
                "next_episode": {
                    "season": 2,
                    "number": 1,
                    "title": "Seven Thirty-Seven",
                    "ids": {"trakt": 5, "slug": None, "tmdb": None, "imdb": None},
                },
            }
        },
        tmp_path,
    )
    out = read_tools.next_episode(client, "u1", "breaking-bad")
    assert out["completed"] is False
    assert out["next_episode"]["season"] == 2
    assert out["next_episode"]["title"] == "Seven Thirty-Seven"


def test_stats_resolves_username_then_summarizes(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/users/settings":
            return httpx.Response(200, json={"user": {"username": "parvin"}})
        return httpx.Response(
            200,
            json={
                "movies": {"watched": 120, "minutes": 15000},
                "shows": {"watched": 30},
                "episodes": {"watched": 400, "minutes": 18000},
            },
        )

    s = _settings(db_path=str(tmp_path / "t.db"))
    store = TokenStore(s.db_path, s.token_encryption_key)
    store.save_tokens("u1", "access", "refresh", _future_iso())
    client = TraktClient(s, store, httpx.Client(transport=httpx.MockTransport(handler)))

    out = read_tools.stats(client, "u1")
    assert seen == ["/users/settings", "/users/parvin/stats"]
    assert out["movies_watched"] == 120
    assert out["episodes_minutes"] == 18000


def test_watchlist_keeps_listed_at(tmp_path):
    client = _client_for(
        {
            "/sync/watchlist/movies": [
                {
                    "rank": 1,
                    "listed_at": "2026-09-01T00:00:00Z",
                    "type": "movie",
                    "movie": {
                        "title": "Dune: Part Two",
                        "year": 2024,
                        "ids": {"trakt": 11, "slug": "dune-part-two-2024", "tmdb": 12, "imdb": "tt13"},
                    },
                }
            ]
        },
        tmp_path,
    )
    out = read_tools.get_watchlist(client, "u1", "movies")
    assert out[0]["title"] == "Dune: Part Two"
    assert out[0]["listed_at"] == "2026-09-01T00:00:00Z"
