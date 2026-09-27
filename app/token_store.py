"""Encrypted per-user token store with atomic single-use refresh rotation."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .security import decrypt_token, encrypt_token


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TokenStore:
    """SQLite-backed store. Tokens are encrypted at rest with Fernet."""

    def __init__(self, db_path: str, encryption_key: str) -> None:
        self.db_path = db_path
        self.key = encryption_key
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    user_id TEXT PRIMARY KEY,
                    access_token_enc TEXT NOT NULL,
                    refresh_token_enc TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save_tokens(
        self, user_id: str, access_token: str, refresh_token: str, expires_at: str
    ) -> None:
        """Atomically replace the stored pair (single-use refresh semantics)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tokens (user_id, access_token_enc, refresh_token_enc, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    access_token_enc=excluded.access_token_enc,
                    refresh_token_enc=excluded.refresh_token_enc,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    encrypt_token(access_token, self.key),
                    encrypt_token(refresh_token, self.key),
                    expires_at,
                    _utcnow_iso(),
                ),
            )
            conn.commit()

    def get_tokens(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT access_token_enc, refresh_token_enc, expires_at FROM tokens WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "access_token": decrypt_token(row[0], self.key),
            "refresh_token": decrypt_token(row[1], self.key),
            "expires_at": row[2],
        }

    def delete_tokens(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
            conn.commit()
