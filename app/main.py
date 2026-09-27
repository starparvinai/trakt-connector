"""FastAPI app: OAuth + API-key-authenticated tool invocation (M4 hardening).

M4 changes vs M1:
- OAuth state is a signed, expiring token (app/state.py), not an in-memory dict.
- /oauth/callback mints a connector API key (shown once); /tools/* requires it
  as `X-API-Key`. No more trusting a caller-supplied `user_id`.
- POST /tools/invoke dispatches any registered tool by name (mirrors MCP tools/call).
"""
from __future__ import annotations

import uuid

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import tools_registry
from .auth import mint_api_key, resolve_user
from .config import get_settings
from .state import issue, make_serializer, redeem
from .token_store import TokenStore
from .trakt_client import TraktClient
from .trakt_oauth import (
    ReauthRequired,
    TraktAPIError,
    build_authorize_url,
    exchange_code_for_tokens,
)
from .tools_registry import UnknownToolError

app = FastAPI(title="Trakt Connector")


def _deps():
    settings = get_settings()
    store = TokenStore(settings.db_path, settings.token_encryption_key)
    return settings, store


def _state_secret(settings) -> str:
    # Production should set a dedicated OAUTH_STATE_SECRET; the encryption key
    # is a safe fallback for the prototype.
    return settings.oauth_state_secret or settings.token_encryption_key


@app.get("/health")
def health() -> dict:
    return {"ok": True, "milestone": "M4-hardening"}


@app.get("/oauth/start")
def oauth_start() -> RedirectResponse:
    settings, _ = _deps()
    state = issue(make_serializer(_state_secret(settings)), nonce=uuid.uuid4().hex)
    return RedirectResponse(build_authorize_url(settings, state), status_code=302)


@app.get("/oauth/callback")
def oauth_callback(code: str, state: str) -> dict:
    settings, store = _deps()
    try:
        redeem(make_serializer(_state_secret(settings)), state, max_age_seconds=600)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        with httpx.Client(timeout=15) as client:
            tokens = exchange_code_for_tokens(settings, client, code)
    except ReauthRequired as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TraktAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # Never return Trakt tokens in the response; store them encrypted server-side.
    connector_user_id = "u_" + uuid.uuid4().hex
    store.save_tokens(
        connector_user_id,
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["expires_at"],
    )
    api_key = mint_api_key(settings.db_path, connector_user_id)
    return {
        "ok": True,
        "connector_user_id": connector_user_id,
        "api_key": api_key,
        "usage": "Shown once. Pass it as the X-API-Key header on /tools/list and /tools/invoke.",
    }


def _authed_user(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    settings, _ = _deps()
    user_id = resolve_user(settings.db_path, x_api_key or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key.")
    return user_id


@app.get("/tools/list")
def tools_list(user_id: str = Depends(_authed_user)) -> dict:
    return {"tools": tools_registry.list_tools()}


class InvokeBody(BaseModel):
    tool: str
    arguments: dict = {}


@app.post("/tools/invoke")
def tools_invoke(body: InvokeBody, user_id: str = Depends(_authed_user)) -> dict:
    settings, store = _deps()
    client = TraktClient(settings, store)
    try:
        return tools_registry.invoke(client, user_id, body.tool, body.arguments)
    except UnknownToolError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"Bad arguments: {e}")
    except ReauthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except TraktAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
