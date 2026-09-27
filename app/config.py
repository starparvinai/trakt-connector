"""Configuration for the Trakt connector auth spine (M1)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    token_encryption_key: str
    authorize_url: str = "https://trakt.tv/oauth/authorize"
    token_url: str = "https://api.trakt.tv/oauth/token"
    api_base: str = "https://api.trakt.tv"
    db_path: str = "./trakt_tokens.db"
    oauth_state_secret: str = ""


def get_settings() -> Settings:
    """Read settings from the environment. Secrets stay out of code and chat."""
    return Settings(
        client_id=os.environ["TRAKT_CLIENT_ID"],
        client_secret=os.environ["TRAKT_CLIENT_SECRET"],
        redirect_uri=os.environ["TRAKT_REDIRECT_URI"],
        token_encryption_key=os.environ["TOKEN_ENCRYPTION_KEY"],
        authorize_url=os.environ.get(
            "TRAKT_AUTHORIZE_URL", "https://trakt.tv/oauth/authorize"
        ),
        token_url=os.environ.get(
            "TRAKT_TOKEN_URL", "https://api.trakt.tv/oauth/token"
        ),
        api_base=os.environ.get("TRAKT_API_BASE", "https://api.trakt.tv"),
        db_path=os.environ.get("TRAKT_TOKEN_DB", "./trakt_tokens.db"),
        oauth_state_secret=os.environ.get("OAUTH_STATE_SECRET", ""),
    )
