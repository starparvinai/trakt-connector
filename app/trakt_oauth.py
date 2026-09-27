"""Trakt OAuth2 Authorization Code flow helpers (M1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .config import Settings


class ReauthRequired(Exception):
    """Tokens are dead (invalid_grant / session not found). User must re-authorize."""


class TraktAPIError(Exception):
    """Trakt API returned an error we don't auto-recover from."""


def build_authorize_url(settings: Settings, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "state": state,
    }
    return f"{settings.authorize_url}?{urlencode(params)}"


def _expires_at(seconds: int) -> str:
    # Trakt access tokens live ~7 days; compute from the response when present.
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def exchange_code_for_tokens(
    settings: Settings, client: httpx.Client, code: str
) -> dict:
    resp = client.post(
        settings.token_url,
        json={
            "code": code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        _raise_for_oauth_error(resp)
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": _expires_at(int(data.get("expires_in", 7 * 24 * 3600))),
    }


def refresh_access_token(
    settings: Settings, client: httpx.Client, refresh_token: str
) -> dict:
    """Single-use refresh: returns a NEW pair; the old refresh token is dead."""
    resp = client.post(
        settings.token_url,
        json={
            "refresh_token": refresh_token,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        _raise_for_oauth_error(resp)
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": _expires_at(int(data.get("expires_in", 7 * 24 * 3600))),
    }


def _raise_for_oauth_error(resp: httpx.Response) -> None:
    try:
        body = resp.json()
    except Exception:
        body = {}
    error = body.get("error", "")
    if error == "invalid_grant" or "session not found" in str(
        body.get("error_description", "")
    ):
        raise ReauthRequired(f"Trakt OAuth failed: {error} — re-authorize once.")
    raise TraktAPIError(f"Trakt OAuth error {resp.status_code}: {body}")
