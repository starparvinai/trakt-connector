"""Tool registry + single dispatch surface.

POST /tools/invoke {"tool": "...", "arguments": {...}} keeps the HTTP surface
to one endpoint instead of twenty, and mirrors the shape of MCP's tools/call —
easy to port when the connector grows an MCP front.
"""
from __future__ import annotations

from typing import Callable

from . import read_tools, write_tools
from .trakt_client import TraktClient


class UnknownToolError(Exception):
    pass


_DESCRIPTIONS = {
    "search": "Search Trakt for movies, shows, episodes, or people.",
    "trending": "What people are watching right now.",
    "popular": "Most popular movies/shows by all-time watches.",
    "recommendations": "Personalized recommendations from your Trakt profile.",
    "get_history": "Your watch history, optionally filtered by date.",
    "get_watchlist": "Your watchlist.",
    "get_collection": "Your collection.",
    "get_ratings": "Your ratings, optionally filtered by score.",
    "next_episode": "The next unwatched episode of a show.",
    "upcoming": "Your calendar: episodes airing in the next N days.",
    "stats": "Your lifetime Trakt stats.",
    "log_movie": "Log a movie as watched.",
    "log_episode": "Log an episode as watched.",
    "rate_movie": "Rate a movie 1-10.",
    "rate_show": "Rate a show 1-10.",
    "rate_episode": "Rate an episode 1-10.",
    "add_to_watchlist": "Add a movie/show to your watchlist.",
    "remove_from_watchlist": "Remove from watchlist (needs confirm=True).",
    "remove_movie_from_history": "Remove a movie from history (needs confirm=True).",
    "remove_episode_from_history": "Remove an episode from history (needs confirm=True).",
}

# name -> (callable(client, user_id, **args), is_write)
_REGISTRY: dict[str, tuple[Callable, bool]] = {}


def _tool(name: str, fn: Callable, write: bool = False) -> None:
    _REGISTRY[name] = (fn, write)


_tool("search", read_tools.search)
_tool("trending", read_tools.trending)
_tool("popular", read_tools.popular)
_tool("recommendations", read_tools.recommendations)
_tool("get_history", read_tools.get_history)
_tool("get_watchlist", read_tools.get_watchlist)
_tool("get_collection", read_tools.get_collection)
_tool("get_ratings", read_tools.get_ratings)
_tool("next_episode", read_tools.next_episode)
_tool("upcoming", read_tools.upcoming)
_tool("stats", read_tools.stats)
_tool("log_movie", write_tools.log_movie, write=True)
_tool("log_episode", write_tools.log_episode, write=True)
_tool("rate_movie", write_tools.rate_movie, write=True)
_tool("rate_show", write_tools.rate_show, write=True)
_tool("rate_episode", write_tools.rate_episode, write=True)
_tool("add_to_watchlist", write_tools.add_to_watchlist, write=True)
_tool("remove_from_watchlist", write_tools.remove_from_watchlist, write=True)
_tool("remove_movie_from_history", write_tools.remove_movie_from_history, write=True)
_tool("remove_episode_from_history", write_tools.remove_episode_from_history, write=True)


def list_tools() -> list[dict]:
    return [
        {"name": name, "write": write, "description": _DESCRIPTIONS.get(name, "")}
        for name, (_, write) in sorted(_REGISTRY.items())
    ]


def invoke(client: TraktClient, user_id: str, tool: str, arguments: dict | None) -> dict:
    if tool not in _REGISTRY:
        raise UnknownToolError(f"unknown tool: {tool}")
    fn, _ = _REGISTRY[tool]
    return {"tool": tool, "result": fn(client, user_id, **(arguments or {}))}
