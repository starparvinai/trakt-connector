"""Tiny CLI to exercise the M2 read tools against a real Trakt account.

Usage (from the project root, with .env sourced):
    .venv/bin/python scripts/trakt_cli.py search "dune"
    .venv/bin/python scripts/trakt_cli.py history --type movies --limit 5
    .venv/bin/python scripts/trakt_cli.py watchlist --type shows
    .venv/bin/python scripts/trakt_cli.py next breaking-bad
    .venv/bin/python scripts/trakt_cli.py stats
    .venv/bin/python scripts/trakt_cli.py recommendations --type movies
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import read_tools, write_tools
from app.config import get_settings
from app.token_store import TokenStore
from app.trakt_client import TraktClient


def _client() -> tuple[TraktClient, str]:
    settings = get_settings()
    client = TraktClient(settings, TokenStore(settings.db_path, settings.token_encryption_key))
    return client, os.environ.get("TRAKT_USER_ID", "parvin")


def main() -> None:
    p = argparse.ArgumentParser(description="Trakt connector M2 read tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--type", default="movie", choices=["movie", "show", "episode", "person"])

    sp = sub.add_parser("history")
    sp.add_argument("--type", default="movies", choices=["movies", "episodes"])
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--start", default=None, help="YYYY-MM-DD")
    sp.add_argument("--end", default=None, help="YYYY-MM-DD")

    sp = sub.add_parser("watchlist")
    sp.add_argument("--type", default="movies", choices=["movies", "shows"])

    sp = sub.add_parser("next")
    sp.add_argument("show", help="Trakt ID or slug, e.g. breaking-bad")

    sub.add_parser("stats")

    sp = sub.add_parser("recommendations")
    sp.add_argument("--type", default="movies", choices=["movies", "shows"])

    # --- M3 write commands ---
    sp = sub.add_parser("log-movie")
    sp.add_argument("id", help="Trakt slug or numeric ID")
    sp.add_argument("--watched-at", default=None)

    sp = sub.add_parser("log-episode")
    sp.add_argument("show")
    sp.add_argument("season", type=int)
    sp.add_argument("number", type=int)

    sp = sub.add_parser("rate-movie")
    sp.add_argument("id")
    sp.add_argument("rating", type=int)

    sp = sub.add_parser("rate-show")
    sp.add_argument("id")
    sp.add_argument("rating", type=int)

    sp = sub.add_parser("rate-episode")
    sp.add_argument("show")
    sp.add_argument("season", type=int)
    sp.add_argument("number", type=int)
    sp.add_argument("rating", type=int)

    sp = sub.add_parser("watchlist-add")
    sp.add_argument("type", choices=["movies", "shows"])
    sp.add_argument("id")

    sp = sub.add_parser("watchlist-remove")
    sp.add_argument("type", choices=["movies", "shows"])
    sp.add_argument("id")
    sp.add_argument("--confirm", action="store_true")

    sp = sub.add_parser("history-remove-movie")
    sp.add_argument("id")
    sp.add_argument("--confirm", action="store_true")

    sp = sub.add_parser("history-remove-episode")
    sp.add_argument("show")
    sp.add_argument("season", type=int)
    sp.add_argument("number", type=int)
    sp.add_argument("--confirm", action="store_true")

    args = p.parse_args()
    client, user_id = _client()

    if args.cmd == "search":
        out = read_tools.search(client, user_id, args.query, args.type)
    elif args.cmd == "history":
        out = read_tools.get_history(client, user_id, args.type, args.start, args.end, args.limit)
    elif args.cmd == "watchlist":
        out = read_tools.get_watchlist(client, user_id, args.type)
    elif args.cmd == "next":
        out = read_tools.next_episode(client, user_id, args.show)
    elif args.cmd == "stats":
        out = read_tools.stats(client, user_id)
    elif args.cmd == "recommendations":
        out = read_tools.recommendations(client, user_id, args.type)
    elif args.cmd == "log-movie":
        out = write_tools.log_movie(client, user_id, args.id, args.watched_at)
    elif args.cmd == "log-episode":
        out = write_tools.log_episode(client, user_id, args.show, args.season, args.number)
    elif args.cmd == "rate-movie":
        out = write_tools.rate_movie(client, user_id, args.id, args.rating)
    elif args.cmd == "rate-show":
        out = write_tools.rate_show(client, user_id, args.id, args.rating)
    elif args.cmd == "rate-episode":
        out = write_tools.rate_episode(client, user_id, args.show, args.season, args.number, args.rating)
    elif args.cmd == "watchlist-add":
        out = write_tools.add_to_watchlist(client, user_id, args.type, args.id)
    elif args.cmd == "watchlist-remove":
        out = write_tools.remove_from_watchlist(client, user_id, args.type, args.id, args.confirm)
    elif args.cmd == "history-remove-movie":
        out = write_tools.remove_movie_from_history(client, user_id, args.id, args.confirm)
    elif args.cmd == "history-remove-episode":
        out = write_tools.remove_episode_from_history(
            client, user_id, args.show, args.season, args.number, args.confirm
        )

    print(json.dumps(out, indent=2)[:6000])


if __name__ == "__main__":
    main()
