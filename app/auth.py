"""Connector API keys: the HTTP edge's per-user identity.

After OAuth completes we mint a random API key, store only its SHA-256 hash,
and show the raw key once. Every /tools/* call presents it as `X-API-Key`;
we resolve it to the connector user id whose Trakt tokens to use. No more
trusting a caller-supplied `user_id`.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_api_keys (
    key_hash TEXT PRIMARY KEY,
    connector_user_id TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def _hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def mint_api_key(db_path: str, connector_user_id: str) -> str:
    """Create a key for a user. Returns the raw key — show it once, then forget it."""
    raw = "tk_" + secrets.token_urlsafe(32)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO connector_api_keys (key_hash, connector_user_id, created_at)"
            " VALUES (?, ?, ?)",
            (
                _hash(raw),
                connector_user_id,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )
        conn.commit()
    return raw


def resolve_user(db_path: str, raw_key: str) -> str | None:
    """Map a presented API key to its connector user id, or None."""
    if not raw_key:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT connector_user_id FROM connector_api_keys WHERE key_hash = ?",
            (_hash(raw_key),),
        ).fetchone()
    return row[0] if row else None
