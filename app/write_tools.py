"""M3 write tools: log watches, ratings, and watchlist mutations.

Safety rule: destructive operations (removing history or watchlist entries)
never execute on the first call. They return a preview dict and require
confirm=True on a second call. This is the two-step pattern LLM agents use
for irreversible actions: the model shows the preview, the user approves,
the model re-calls with confirm=True.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .trakt_client import TraktClient

_WATCHLIST_TYPES = ("movies", "shows")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ids_for(media_id: str) -> dict:
    """Accept a Trakt numeric ID or slug; Trakt resolves either."""
    media_id = str(media_id).strip()
    if media_id.isdigit():
        return {"trakt": int(media_id)}
    return {"slug": media_id}


def _preview(action: str, target: dict) -> dict:
    return {
        "status": "needs_confirmation",
        "action": action,
        "target": target,
        "hint": "Re-run with confirm=True to execute.",
    }


def _episode_trakt_id(
    client: TraktClient, user_id: str, show_id: str, season: int, number: int
) -> int:
    """Resolve S/E numbers to the episode's Trakt ID via the season listing."""
    episodes = client.request(
        user_id, "GET", f"/shows/{show_id}/seasons/{season}"
    ).json()
    for ep in episodes:
        if ep.get("number") == number:
            return ep["ids"]["trakt"]
    raise ValueError(f"episode S{season}E{number} not found for show {show_id!r}")


# --- History ---------------------------------------------------------------


def log_movie(
    client: TraktClient, user_id: str, media_id: str, watched_at: str | None = None
) -> dict:
    watched_at = watched_at or _now_iso()
    resp = client.request(
        user_id,
        "POST",
        "/sync/history",
        json={"movies": [{"watched_at": watched_at, "ids": _ids_for(media_id)}]},
    ).json()
    added = (resp.get("added") or {}).get("movies", 0)
    return {
        "status": "logged",
        "movie": str(media_id),
        "watched_at": watched_at,
        "added": added,
    }


def log_episode(
    client: TraktClient,
    user_id: str,
    show_id: str,
    season: int,
    number: int,
    watched_at: str | None = None,
) -> dict:
    watched_at = watched_at or _now_iso()
    episode_id = _episode_trakt_id(client, user_id, show_id, season, number)
    resp = client.request(
        user_id,
        "POST",
        "/sync/history",
        json={"episodes": [{"watched_at": watched_at, "ids": {"trakt": episode_id}}]},
    ).json()
    added = (resp.get("added") or {}).get("episodes", 0)
    return {
        "status": "logged",
        "show": str(show_id),
        "season": season,
        "number": number,
        "watched_at": watched_at,
        "added": added,
    }


def remove_movie_from_history(
    client: TraktClient, user_id: str, media_id: str, confirm: bool = False
) -> dict:
    target = {"type": "movie", "id": str(media_id)}
    if not confirm:
        return _preview("remove_movie_from_history", target)
    resp = client.request(
        user_id, "POST", "/sync/history/remove", json={"movies": [{"ids": _ids_for(media_id)}]}
    ).json()
    deleted = (resp.get("deleted") or {}).get("movies", 0)
    return {"status": "removed", "deleted": deleted, **target}


def remove_episode_from_history(
    client: TraktClient,
    user_id: str,
    show_id: str,
    season: int,
    number: int,
    confirm: bool = False,
) -> dict:
    target = {"type": "episode", "show": str(show_id), "season": season, "number": number}
    if not confirm:
        return _preview("remove_episode_from_history", target)
    episode_id = _episode_trakt_id(client, user_id, show_id, season, number)
    resp = client.request(
        user_id,
        "POST",
        "/sync/history/remove",
        json={"episodes": [{"ids": {"trakt": episode_id}}]},
    ).json()
    deleted = (resp.get("deleted") or {}).get("episodes", 0)
    return {"status": "removed", "deleted": deleted, **target}


# --- Ratings ---------------------------------------------------------------


def _rate(
    client: TraktClient, user_id: str, key: str, ids: dict, rating: int
) -> dict:
    assert 1 <= rating <= 10, "rating must be 1-10"
    resp = client.request(
        user_id,
        "POST",
        "/sync/ratings",
        json={key: [{"rating": rating, "rated_at": _now_iso(), "ids": ids}]},
    ).json()
    added = (resp.get("added") or {}).get(key, 0)
    return {"status": "rated", "type": key, "rating": rating, "added": added}


def rate_movie(client: TraktClient, user_id: str, media_id: str, rating: int) -> dict:
    return _rate(client, user_id, "movies", _ids_for(media_id), rating)


def rate_show(client: TraktClient, user_id: str, media_id: str, rating: int) -> dict:
    return _rate(client, user_id, "shows", _ids_for(media_id), rating)


def rate_episode(
    client: TraktClient, user_id: str, show_id: str, season: int, number: int, rating: int
) -> dict:
    episode_id = _episode_trakt_id(client, user_id, show_id, season, number)
    return _rate(client, user_id, "episodes", {"trakt": episode_id}, rating)


# --- Watchlist --------------------------------------------------------------


def add_to_watchlist(
    client: TraktClient, user_id: str, media_type: str, media_id: str
) -> dict:
    assert media_type in _WATCHLIST_TYPES
    resp = client.request(
        user_id,
        "POST",
        "/sync/watchlist",
        json={media_type: [{"ids": _ids_for(media_id)}]},
    ).json()
    added = (resp.get("added") or {}).get(media_type, 0)
    existing = (resp.get("existing") or {}).get(media_type, 0)
    return {
        "status": "added_to_watchlist",
        "type": media_type,
        "id": str(media_id),
        "added": added,
        "already_present": existing,
    }


def remove_from_watchlist(
    client: TraktClient, user_id: str, media_type: str, media_id: str, confirm: bool = False
) -> dict:
    assert media_type in _WATCHLIST_TYPES
    target = {"type": media_type, "id": str(media_id)}
    if not confirm:
        return _preview("remove_from_watchlist", target)
    resp = client.request(
        user_id,
        "POST",
        "/sync/watchlist/remove",
        json={media_type: [{"ids": _ids_for(media_id)}]},
    ).json()
    deleted = (resp.get("deleted") or {}).get(media_type, 0)
    return {"status": "removed_from_watchlist", "deleted": deleted, **target}
