"""JWT token creation and password hashing utilities."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

API_KEY_PREFIX = "ahk_"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, key_prefix, key_hash) for a fresh API key.

    Plaintext is shown to the caller once; only key_prefix (display) and
    key_hash (SHA-256 hex, for lookup) are persisted.
    """
    plaintext = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, plaintext[:16], hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    """SHA-256 hex of an API key plaintext (high-entropy → no bcrypt needed)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
