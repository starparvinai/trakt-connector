"""Small security helpers: token encryption and OAuth state values."""
from __future__ import annotations

import secrets

from cryptography.fernet import Fernet, InvalidToken


def encrypt_token(plain: str, key: str) -> str:
    """Encrypt a token for storage. Returns a UTF-8 string."""
    return Fernet(key.encode()).encrypt(plain.encode()).decode()


def decrypt_token(cipher: str, key: str) -> str:
    """Decrypt a stored token. Raises InvalidToken if the key is wrong."""
    return Fernet(key.encode()).decrypt(cipher.encode()).decode()


def generate_state() -> str:
    """CSRF protection value for the OAuth authorize redirect."""
    return secrets.token_urlsafe(32)


__all__ = ["encrypt_token", "decrypt_token", "generate_state", "InvalidToken"]
