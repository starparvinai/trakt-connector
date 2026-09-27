"""Verify M1 end-to-end: use stored tokens to call an authenticated endpoint."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.token_store import TokenStore
from app.trakt_client import TraktClient


def main() -> None:
    settings = get_settings()
    store = TokenStore(settings.db_path, settings.token_encryption_key)
    client = TraktClient(settings, store)
    user_id = os.environ.get("TRAKT_USER_ID", "parvin")
    resp = client.request(user_id, "GET", "/users/settings")
    user = resp.json().get("user", {})
    print(f"Authenticated as Trakt user: {user.get('username')}")


if __name__ == "__main__":
    main()
