"""Authentication service: user lookup, token validation, permission checks."""

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import API_KEY_PREFIX, hash_api_key, verify_password
from app.db.session import get_db
from app.models.user import ApiKey, User

# Relative tokenUrl (no leading slash) so Swagger's "Authorize" resolves it
# against the docs page, which includes root_path (e.g. /apihub/docs behind the
# proxy -> /apihub/api/v1/auth/token). An absolute "/api/v1/auth/token" would
# ignore root_path and POST to the un-prefixed URL, which the gateway can't route.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix.lstrip('/')}/auth/token")


@dataclass
class Principal:
    """Resolved caller identity attached to request.state.principal."""

    type: str  # "user" | "api_key"
    user: User
    key_id: int | None = None
    key_name: str | None = None
    rate_limit: int | None = None


class ApiKeyError(Exception):
    """Raised on a present-but-invalid API key (short-circuits the request)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _ip_allowed(client_ip: str, allowlist: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in allowlist:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def resolve_api_key(db, raw_key: str, client_ip: str) -> Principal:
    """Validate an API key and return a Principal, or raise ApiKeyError."""
    row = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(raw_key)).first()
    if row is None:
        raise ApiKeyError(401, "Could not validate credentials")
    if not row.enabled:
        raise ApiKeyError(401, "API key is disabled")
    if row.expires_at is not None:
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise ApiKeyError(401, "API key has expired")
    if row.ip_allowlist and not _ip_allowed(client_ip, row.ip_allowlist):
        raise ApiKeyError(403, "Source IP not allowed")
    user = row.user
    if user is None or not user.is_active:
        raise ApiKeyError(401, "Could not validate credentials")

    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return Principal(
        type="api_key",
        user=user,
        key_id=row.id,
        key_name=row.name,
        rate_limit=row.rate_limit_per_minute,
    )


def resolve_principal(db, request) -> Principal | None:
    """Resolve caller from X-API-Key or Authorization: Bearer.

    Returns None for no/invalid JWT credentials. Raises ApiKeyError for a
    present-but-invalid API key.
    """
    client_ip = request.client.host if request.client else "unknown"
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return resolve_api_key(db, api_key, client_ip)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer ") :]
    if token.startswith(API_KEY_PREFIX):
        return resolve_api_key(db, token, client_ip)

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username = payload.get("sub")
    except JWTError:
        return None
    if not username:
        return None
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        return None
    return Principal(type="user", user=user, rate_limit=None)


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        # Deactivated accounts must not receive a token. Return None so the
        # login endpoint replies with the same generic 401 (no account-state leak).
        return None
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def get_current_active_superuser(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser privileges required")
    return current_user


class PermissionChecker:
    """Dependency that checks if the current user has a specific permission.

    Usage:
        @router.post("/query", dependencies=[Depends(PermissionChecker("query:employees", "read"))])
    """

    def __init__(self, resource: str, action: str):
        self.resource = resource
        self.action = action

    def __call__(self, current_user: User = Depends(get_current_user)):
        if current_user.is_superuser:
            return current_user

        for role in current_user.roles:
            for perm in role.permissions:
                if perm.action != self.action:
                    continue
                if perm.resource == self.resource:
                    return current_user
                # Trailing-"*" wildcard: the part before "*" is a prefix the
                # requested resource must start with. Covers full-category
                # wildcards ("query:*") and partial prefixes ("query:RD_TC*").
                if perm.resource.endswith("*") and self.resource.startswith(perm.resource[:-1]):
                    return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {self.action} on {self.resource}",
        )
