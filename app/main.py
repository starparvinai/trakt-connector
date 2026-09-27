"""FastAPI app: OAuth start/callback for the Trakt connector (M1 prototype)."""
from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

from .config import get_settings
from .security import generate_state
from .token_store import TokenStore
from .trakt_oauth import (
    ReauthRequired,
    TraktAPIError,
    build_authorize_url,
    exchange_code_for_tokens,
)

app = FastAPI(title="Trakt Connector — M1 Auth Spine")

# Prototype only: map OAuth state -> user_id in memory.
# Production should use signed cookies or a server-side session store.
_state_to_user: dict[str, str] = {}


def _deps():
    settings = get_settings()
    store = TokenStore(settings.db_path, settings.token_encryption_key)
    return settings, store


@app.get("/health")
def health() -> dict:
    return {"ok": True, "milestone": "M1-auth-spine"}


@app.get("/oauth/start")
def oauth_start(user_id: str = Query(..., min_length=1)) -> RedirectResponse:
    settings, _ = _deps()
    state = generate_state()
    _state_to_user[state] = user_id
    return RedirectResponse(build_authorize_url(settings, state), status_code=302)


@app.get("/oauth/callback")
def oauth_callback(code: str = Query(...), state: str = Query(...)) -> dict:
    settings, store = _deps()
    user_id = _state_to_user.pop(state, None)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    try:
        with httpx.Client(timeout=15) as client:
            tokens = exchange_code_for_tokens(settings, client, code)
    except ReauthRequired as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TraktAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # Never return tokens in the response; store them encrypted server-side.
    store.save_tokens(
        user_id, tokens["access_token"], tokens["refresh_token"], tokens["expires_at"]
    )
    return {"ok": True, "user_id": user_id}
