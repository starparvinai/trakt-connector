"""M2 read tools: normalized, agent-friendly wrappers over Trakt read endpoints.

Design rule: raw Trakt payloads are nested (shows -> seasons -> episodes) and
their shape varies per endpoint. Every tool here returns flat dicts with a
stable shape so an LLM caller never has to guess where the title lives.
"""
from __future__ import annotations

from datetime import date

from .trakt_client import TraktClient

_PAGE_SIZE = 100
_MAX_PAGES = 5


def _ids(ref: dict) -> dict:
    ids = (ref or {}).get("ids", {}) or {}
    return {
        "trakt": ids.get("trakt"),
        "slug": ids.get("slug"),
        "tmdb": ids.get("tmdb"),
        "imdb": ids.get("imdb"),
    }


def _media(ref: dict) -> dict:
    """Flatten a Trakt movie/show/episode object."""
    ref = ref or {}
    return {"title": ref.get("title"), "year": ref.get("year"), "ids": _ids(ref)}


def _unwrap(item: dict) -> dict:
    """Search/history/watchlist items nest the media under its type key."""
    kind = item.get("type")
    flat = _media(item.get(kind, {}) if kind else {})
    flat["type"] = kind
    return flat


def _paged(client: TraktClient, user_id: str, path: str, limit: int) -> list:
    """Fetch pages until `limit` items or the pages run short."""
    items: list = []
    page = 1
    while len(items) < limit and page <= _MAX_PAGES:
        batch = client.request(
            user_id, "GET", path, params={"page": page, "limit": _PAGE_SIZE}
        ).json()
        if not batch:
            break
        items.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        page += 1
    return items[:limit]


# --- Search & discovery -----------------------------------------------------


def search(
    client: TraktClient, user_id: str, query: str, media_type: str = "movie", limit: int = 5
) -> list:
    assert media_type in ("movie", "show", "episode", "person")
    resp = client.request(
        user_id, "GET", f"/search/{media_type}", params={"query": query}
    ).json()
    out = []
    for item in resp[:limit]:
        if media_type == "person":
            person = item.get("person", {}) or {}
            out.append(
                {"type": "person", "name": person.get("name"), "ids": _ids(person)}
            )
        else:
            flat = _unwrap(item)
            flat["overview"] = (item.get(media_type, {}) or {}).get("overview")
            out.append(flat)
    return out


def trending(client: TraktClient, user_id: str, media_type: str = "movies", limit: int = 10) -> list:
    assert media_type in ("movies", "shows")
    resp = client.request(user_id, "GET", f"/{media_type}/trending").json()
    kind = media_type[:-1]  # movies -> movie
    return [
        {**_media(item.get(kind, {})), "watchers": item.get("watchers")}
        for item in resp[:limit]
    ]


def popular(client: TraktClient, user_id: str, media_type: str = "movies", limit: int = 10) -> list:
    assert media_type in ("movies", "shows")
    resp = client.request(user_id, "GET", f"/{media_type}/popular").json()
    return [_media(item) for item in resp[:limit]]


def recommendations(
    client: TraktClient, user_id: str, media_type: str = "movies", limit: int = 10
) -> list:
    assert media_type in ("movies", "shows")
    resp = client.request(user_id, "GET", f"/recommendations/{media_type}").json()
    return [_media(item) for item in resp[:limit]]


# --- Watch history ----------------------------------------------------------


def get_history(
    client: TraktClient,
    user_id: str,
    media_type: str = "movies",
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
) -> list:
    """Watched history, newest first. Dates are YYYY-MM-DD, filtered client-side."""
    assert media_type in ("movies", "episodes")
    out = []
    for item in _paged(client, user_id, f"/sync/history/{media_type}", limit * 2):
        watched_at = item.get("watched_at", "")
        day = watched_at[:10]
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        flat = _unwrap(item)
        if media_type == "episodes":
            ep = item.get("episode", {}) or {}
            flat["episode"] = {
                "season": ep.get("season"),
                "number": ep.get("number"),
                "title": ep.get("title"),
            }
            flat["show"] = _media(item.get("show", {}))
        flat["watched_at"] = watched_at
        out.append(flat)
        if len(out) >= limit:
            break
    return out


# --- Watchlist & collection -------------------------------------------------


def get_watchlist(
    client: TraktClient, user_id: str, media_type: str = "movies", sort: str = "rank"
) -> list:
    assert media_type in ("movies", "shows")
    resp = client.request(
        user_id, "GET", f"/sync/watchlist/{media_type}", params={"sort": sort}
    ).json()
    return [
        {**_unwrap(item), "listed_at": item.get("listed_at")} for item in resp
    ]


def get_collection(client: TraktClient, user_id: str, media_type: str = "movies") -> list:
    assert media_type in ("movies", "shows")
    resp = client.request(user_id, "GET", f"/sync/collection/{media_type}").json()
    return [
        {**_unwrap(item), "collected_at": item.get("collected_at")} for item in resp
    ]


# --- Ratings ----------------------------------------------------------------


def get_ratings(
    client: TraktClient, user_id: str, media_type: str = "movies", rating: int | None = None
) -> list:
    assert media_type in ("movies", "shows", "episodes")
    resp = client.request(user_id, "GET", f"/sync/ratings/{media_type}").json()
    out = []
    for item in resp:
        if rating is not None and item.get("rating") != rating:
            continue
        out.append(
            {**_unwrap(item), "rating": item.get("rating"), "rated_at": item.get("rated_at")}
        )
    return out


# --- Progress, calendar, stats ----------------------------------------------


def next_episode(client: TraktClient, user_id: str, show_id: str) -> dict:
    """The single most useful 'what should I watch' primitive."""
    data = client.request(user_id, "GET", f"/shows/{show_id}/progress/watched").json()
    nxt = data.get("next_episode") or {}
    if not nxt:
        return {"completed": True, "next_episode": None}
    return {
        "completed": False,
        "next_episode": {
            "season": nxt.get("season"),
            "number": nxt.get("number"),
            "title": nxt.get("title"),
            "ids": _ids(nxt),
        },
    }


def upcoming(client: TraktClient, user_id: str, days: int = 7) -> list:
    start = date.today().isoformat()
    resp = client.request(user_id, "GET", f"/calendars/my/shows/{start}/{days}").json()
    out = []
    for item in resp:
        ep = item.get("episode", {}) or {}
        out.append(
            {
                "first_aired": item.get("first_aired"),
                "show": _media(item.get("show", {})),
                "episode": {
                    "season": ep.get("season"),
                    "number": ep.get("number"),
                    "title": ep.get("title"),
                },
            }
        )
    return out


def stats(client: TraktClient, user_id: str) -> dict:
    me = client.request(user_id, "GET", "/users/settings").json()
    username = me["user"]["username"]
    resp = client.request(user_id, "GET", f"/users/{username}/stats")
    if resp.status_code == 204:
        # Fresh account with no activity: Trakt returns 204 No Content.
        return {
            "movies_watched": 0,
            "movies_minutes": 0,
            "shows_watched": 0,
            "episodes_watched": 0,
            "episodes_minutes": 0,
        }
    data = resp.json()
    movies = data.get("movies", {}) or {}
    shows = data.get("shows", {}) or {}
    episodes = data.get("episodes", {}) or {}
    return {
        "movies_watched": movies.get("watched"),
        "movies_minutes": movies.get("minutes"),
        "shows_watched": shows.get("watched"),
        "episodes_watched": episodes.get("watched"),
        "episodes_minutes": episodes.get("minutes"),
    }
