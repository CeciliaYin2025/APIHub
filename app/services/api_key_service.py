"""Business logic for issuing, listing, and revoking API keys."""

from datetime import datetime, timezone

from app.core.security import generate_api_key
from app.models.user import ApiKey, User


def create_api_key(
    db,
    *,
    service_account_username: str,
    name: str,
    expires_at: datetime | None = None,
    ip_allowlist: list[str] | None = None,
    rate_limit_per_minute: int | None = None,
    created_by: str | None = None,
) -> tuple[ApiKey, str]:
    """Create a key bound to a service account. Returns (row, plaintext).

    Plaintext is returned once and never stored.
    """
    user = db.query(User).filter(User.username == service_account_username).first()
    if user is None:
        raise ValueError(f"Service account '{service_account_username}' not found")
    if not user.is_service_account:
        raise ValueError(f"User '{service_account_username}' is not a service account")

    plaintext, key_prefix, key_hash = generate_api_key()
    row = ApiKey(
        user_id=user.id,
        name=name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        expires_at=expires_at,
        ip_allowlist=ip_allowlist,
        rate_limit_per_minute=rate_limit_per_minute,
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def list_api_keys(db) -> list[ApiKey]:
    return db.query(ApiKey).all()


def revoke_api_key(db, key_id: int) -> ApiKey:
    row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if row is None:
        raise ValueError("API key not found")
    row.enabled = False
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
