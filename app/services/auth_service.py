"""Authentication service: user lookup, token validation, permission checks."""

import ipaddress
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, OAuth2AuthorizationCodeBearer, OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import API_KEY_PREFIX, hash_api_key, verify_password
from app.models.user import ApiKey, User
from app.services import oauth_rs

# Relative tokenUrl (no leading slash) so Swagger's "Authorize" resolves it
# against the docs page, which includes root_path (e.g. /apihub/docs behind the
# proxy -> /apihub/api/v1/auth/token). An absolute "/api/v1/auth/token" would
# ignore root_path and POST to the un-prefixed URL, which the gateway can't route.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix.lstrip('/')}/auth/token", auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


def build_isap_oauth_scheme():
    """Swagger's "Authorize" entry for logging in with an ISAP account.

    Returns a no-op dependency unless the API actually accepts isap-auth
    tokens: offering the flow while OAUTH_RS_ENABLED is off would walk the user
    through a real login and then reject every request they make with it.

    Both URLs are the browser-facing ones — this flow runs in the reader's
    browser, not on the server, so ISAP_AUTH_INTERNAL_URL would be unreachable.
    """
    if not (settings.oauth_rs_enabled and settings.isap_auth_public_url):
        return lambda: None
    base = settings.isap_auth_public_url.rstrip("/")
    return OAuth2AuthorizationCodeBearer(
        authorizationUrl=f"{base}/api/v1/authorize",
        tokenUrl=f"{base}/api/v1/token",
        scopes={"openid": "Identity", "profile": "Profile", "email": "Email address"},
        auto_error=False,
    )


isap_oauth_scheme = build_isap_oauth_scheme()


@dataclass
class Principal:
    """Resolved caller identity attached to request.state.principal."""

    # "user"      — local password login (JWT minted by POST /auth/token)
    # "api_key"   — service account key (ahk_...)
    # "sso_user"  — isap-auth access token validated by introspection
    #               (app/middleware/auth_context.py), carrying a local User for
    #               RBAC exactly like the other two. Authorization is never
    #               taken from the token's claims.
    type: str
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


# Per-key last recorded "last_used_at" write time (process-local, in-memory).
# Throttles how often resolve_api_key writes + commits to the platform store.
_last_used_cache: dict[int, float] = {}


def _should_record_last_used(key_id: int, now: float) -> bool:
    """Return True (and record `now`) if `key_id` is due for a last_used_at
    write: never recorded, or last recorded more than the throttle window ago."""
    last = _last_used_cache.get(key_id)
    if last is not None and (now - last) < settings.api_key_last_used_throttle_seconds:
        return False
    _last_used_cache[key_id] = now
    return True


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


def _parse_trusted_proxies(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse a comma-separated CIDR/IP list into networks, skipping blanks/invalid entries."""
    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return networks


def _ip_in_networks(ip: str, networks: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def get_client_ip(request) -> str:
    """Resolve the real client IP, trusting X-Forwarded-For only when the
    direct peer is a configured trusted reverse proxy (anti-spoofing).

    Behind an untrusted/unconfigured peer, X-Forwarded-For is attacker-
    controlled and must be ignored — so with no trusted_proxies configured
    (the default), this always returns the direct peer, unchanged from
    pre-existing behavior.
    """
    peer = request.client.host if request.client else "unknown"

    trusted_networks = _parse_trusted_proxies(settings.trusted_proxies)
    if not trusted_networks:
        return peer

    if not _ip_in_networks(peer, trusted_networks):
        # Direct peer isn't a trusted proxy: its X-Forwarded-For is untrusted/spoofable.
        return peer

    xff = request.headers.get("X-Forwarded-For", "")
    if not xff:
        return peer

    hops = [h.strip() for h in xff.split(",") if h.strip()]
    # Walk right-to-left: skip hops that are themselves trusted proxies, the
    # first non-trusted, parseable hop is the real client behind the chain.
    for hop in reversed(hops):
        try:
            addr = ipaddress.ip_address(hop)
        except ValueError:
            continue
        if not any(addr in net for net in trusted_networks):
            return hop
    return peer


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

    if _should_record_last_used(row.id, time.time()):
        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
    return Principal(
        type="api_key",
        user=user,
        key_id=row.id,
        key_name=row.name,
        rate_limit=row.rate_limit_per_minute,
    )


def bearer_token(request) -> str | None:
    """The raw ``Authorization: Bearer`` value, or None if absent.

    Shared with the OAuth branch in AuthContextMiddleware so both agree on what
    counts as a bearer credential.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth[len("Bearer ") :]


def resolve_principal(db, request) -> Principal | None:
    """Resolve caller from X-API-Key or Authorization: Bearer.

    Returns None for no/invalid JWT credentials. Raises ApiKeyError for a
    present-but-invalid API key.
    """
    client_ip = get_client_ip(request)
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return resolve_api_key(db, api_key, client_ip)

    token = bearer_token(request)
    if not token:
        return None
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
    if user.is_service_account:
        # Service accounts authenticate only via API key, never password flow.
        return None
    return user


def get_current_user(
    request: Request,
    _jwt: str | None = Depends(oauth2_scheme),
    _key: str | None = Depends(api_key_scheme),
    _sso: str | None = Depends(isap_oauth_scheme),
) -> User:
    """Return the caller resolved by AuthContextMiddleware.

    The three Depends params are declared only so Swagger shows every auth
    scheme and forwards the credentials; the actual resolution happens in
    AuthContextMiddleware, which sets request.state.principal.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        # With the OAuth Resource Server on, point the client at our RFC 9728
        # metadata instead of a bare "Bearer": that is how an OAuth-aware
        # client discovers which authorization server to send the user to.
        challenge = oauth_rs.challenge_header() if oauth_rs.enabled() else "Bearer"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": challenge},
        )
    return principal.user


def get_current_active_superuser(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser privileges required")
    return current_user


def has_permission(user: User, resource: str, action: str) -> bool:
    """Whether *user* may perform *action* on *resource*.

    The predicate behind ``PermissionChecker``, split out so callers that need
    to *filter* a list (rather than gate a single request) can reuse the exact
    matching rules instead of re-implementing them. Both must stay one
    implementation: a listing that disagreed with the gate would either hide
    definitions the caller can run or advertise ones they cannot.
    """
    if user.is_superuser:
        return True

    for role in user.roles:
        for perm in role.permissions:
            if perm.action != action:
                continue
            if perm.resource == resource:
                return True
            # Trailing-"*" wildcard: the part before "*" is a prefix the
            # requested resource must start with. Covers full-category
            # wildcards ("query:*") and partial prefixes ("query:RD_TC*").
            if perm.resource.endswith("*") and resource.startswith(perm.resource[:-1]):
                return True

    return False


class PermissionChecker:
    """Dependency that checks if the current user has a specific permission.

    Usage:
        @router.post("/query", dependencies=[Depends(PermissionChecker("query:employees", "read"))])
    """

    def __init__(self, resource: str, action: str):
        self.resource = resource
        self.action = action

    def __call__(self, current_user: User = Depends(get_current_user)):
        if has_permission(current_user, self.resource, self.action):
            return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {self.action} on {self.resource}",
        )
