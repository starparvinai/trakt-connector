"""Signed, expiring OAuth state tokens.

Replaces the old in-memory `_state_to_user` dict, which died on restart and
broke with more than one worker. State is now self-contained: payload +
timestamp + HMAC, so any worker can redeem it and forged/expired tokens fail.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SALT = "trakt-connector-oauth-state"


def make_serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def issue(serializer: URLSafeTimedSerializer, **payload: str) -> str:
    return serializer.dumps(payload)


def redeem(
    serializer: URLSafeTimedSerializer, token: str, max_age_seconds: int = 600
) -> dict:
    """Return the payload, or raise ValueError on forgery/expiry."""
    try:
        return serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired) as exc:
        raise ValueError(f"invalid or expired OAuth state: {exc}") from exc
