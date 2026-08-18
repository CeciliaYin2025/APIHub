"""Authentication endpoints: login, user/role/permission CRUD."""

import ipaddress
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.db.session import get_db
from app.models.user import ApiKey, Permission, Role, User
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, ApiKeyUpdate
from app.schemas.auth import (
    PasswordReset,
    PermissionCreate,
    PermissionOut,
    PermissionUpdate,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    Token,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.schemas.sso_token import SsoTokenRequest, SsoTokenResponse
from app.services.api_key_service import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.services.auth_service import authenticate_user, get_current_active_superuser, get_current_user
from app.services.sso_service import (
    SCOPE,
    SsoExchangeError,
    build_pkce_pair,
    exchange_pkce_code,
    fetch_userinfo,
    refresh_tokens,
    resolve_sso_user,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Token ────────────────────────────────────────────────────────────────────


@router.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user.username})
    return Token(access_token=access_token)


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


# ── Users (superuser only) ──────────────────────────────────────────────────


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    if payload.is_service_account and payload.is_superuser:
        raise HTTPException(status_code=400, detail="A service account cannot be a superuser")
    if payload.is_service_account and payload.auth_source == "sso":
        # A service account authenticates with an API key and nothing else, so
        # "provisioned against isap-auth" says nothing about it.
        raise HTTPException(status_code=400, detail="A service account cannot use SSO")
    if payload.auth_source == "local" and not payload.password:
        raise HTTPException(status_code=400, detail="A local account requires a password")
    user = User(
        username=payload.username,
        # SSO accounts get a valid-but-unguessable hash rather than an empty
        # one, matching sso_service: verify("") would raise instead of failing.
        hashed_password=get_password_hash(payload.password or secrets.token_urlsafe(32)),
        full_name=payload.full_name,
        is_superuser=payload.is_superuser,
        is_service_account=payload.is_service_account,
        auth_source=payload.auth_source,
    )
    if payload.role_ids:
        roles = db.query(Role).filter(Role.id.in_(payload.role_ids)).all()
        user.roles = roles
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _admin: User = Depends(get_current_active_superuser)):
    return db.query(User).all()


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_active_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_active is not None:
        if not payload.is_active and user.id == admin.id:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        user.is_active = payload.is_active
    # Enforce the exclusions on the RESULTING state, not on what was sent.
    new_is_service = payload.is_service_account if payload.is_service_account is not None else user.is_service_account
    new_is_superuser = payload.is_superuser if payload.is_superuser is not None else user.is_superuser
    new_auth_source = payload.auth_source if payload.auth_source is not None else user.auth_source
    if new_is_service and new_is_superuser:
        raise HTTPException(status_code=400, detail="A service account cannot be a superuser")
    if new_is_service and new_auth_source == "sso":
        raise HTTPException(status_code=400, detail="A service account cannot use SSO")
    if payload.is_service_account is not None:
        user.is_service_account = payload.is_service_account
    if payload.is_superuser is not None:
        user.is_superuser = payload.is_superuser
    if payload.auth_source is not None:
        # Switching sso -> local leaves the account with the unguessable hash it
        # was created with, so it still cannot sign in until an admin sets a
        # password. Deliberate: the alternative is minting a password nobody
        # asked for.
        user.auth_source = payload.auth_source
    if payload.role_ids is not None:
        roles = db.query(Role).filter(Role.id.in_(payload.role_ids)).all()
        user.roles = roles
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_active_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user.is_active = False
    db.commit()


@router.patch("/users/{user_id}/password", status_code=204)
def reset_user_password(
    user_id: int,
    payload: PasswordReset,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.auth_source == "sso":
        # Refused rather than silently stored: password login is blocked for
        # these accounts, so accepting one would leave the admin believing they
        # had handed out working credentials.
        raise HTTPException(status_code=400, detail="Cannot set a password on an SSO account")
    user.hashed_password = get_password_hash(payload.new_password)
    db.commit()


# ── Roles (superuser only) ──────────────────────────────────────────────────


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    role = Role(name=payload.name, description=payload.description)
    if payload.permission_ids:
        perms = db.query(Permission).filter(Permission.id.in_(payload.permission_ids)).all()
        role.permissions = perms
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _admin: User = Depends(get_current_active_superuser)):
    return db.query(Role).all()


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    if payload.permission_ids is not None:
        perms = db.query(Permission).filter(Permission.id.in_(payload.permission_ids)).all()
        role.permissions = perms
    db.commit()
    db.refresh(role)
    return role


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.users:
        raise HTTPException(status_code=409, detail="Cannot delete role that is still assigned to users")
    db.delete(role)
    db.commit()


# ── Permissions (superuser only) ────────────────────────────────────────────


@router.post("/permissions", response_model=PermissionOut, status_code=201)
def create_permission(
    payload: PermissionCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    perm = Permission(resource=payload.resource, action=payload.action, description=payload.description)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(db: Session = Depends(get_db), _admin: User = Depends(get_current_active_superuser)):
    return db.query(Permission).all()


@router.put("/permissions/{permission_id}", response_model=PermissionOut)
def update_permission(
    permission_id: int,
    payload: PermissionUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    perm = db.query(Permission).filter(Permission.id == permission_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    if payload.resource is not None:
        perm.resource = payload.resource
    if payload.action is not None:
        perm.action = payload.action
    if payload.description is not None:
        perm.description = payload.description
    db.commit()
    db.refresh(perm)
    return perm


@router.delete("/permissions/{permission_id}", status_code=204)
def delete_permission(
    permission_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    perm = db.query(Permission).filter(Permission.id == permission_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    if perm.roles:
        raise HTTPException(status_code=409, detail="Cannot delete permission that is still assigned to roles")
    db.delete(perm)
    db.commit()


# ── API Keys (superuser only) ───────────────────────────────────────────────


def _key_to_out(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "service_account": row.user.username if row.user else "",
        "key_prefix": row.key_prefix,
        "enabled": row.enabled,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "ip_allowlist": row.ip_allowlist,
        "rate_limit_per_minute": row.rate_limit_per_minute,
    }


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key_endpoint(
    payload: ApiKeyCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_active_superuser),
):
    try:
        row, plaintext = create_api_key(
            db,
            service_account_username=payload.service_account_username,
            name=payload.name,
            expires_at=payload.expires_at,
            ip_allowlist=payload.ip_allowlist,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            created_by=admin.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**_key_to_out(row), "api_key": plaintext}


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys_endpoint(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    return [_key_to_out(r) for r in list_api_keys(db)]


@router.patch("/api-keys/{key_id}", response_model=ApiKeyOut)
def update_api_key_endpoint(
    key_id: int,
    payload: ApiKeyUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    """Change an existing key's restrictions (expiry, IP allowlist, rate limit).

    Without this the only way to tighten a key already in use was to edit the
    row by hand. Omitted fields are left alone; an explicit null clears the
    restriction.
    """
    row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="API key is revoked; issue a new key instead")

    fields = payload.model_fields_set
    if "ip_allowlist" in fields and payload.ip_allowlist is not None:
        for entry in payload.ip_allowlist:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid IP or CIDR: {entry!r}")
        row.ip_allowlist = payload.ip_allowlist
    elif "ip_allowlist" in fields:
        row.ip_allowlist = None

    if "expires_at" in fields:
        row.expires_at = payload.expires_at
    if "rate_limit_per_minute" in fields:
        row.rate_limit_per_minute = payload.rate_limit_per_minute

    db.commit()
    db.refresh(row)
    return _key_to_out(row)


@router.delete("/api-keys/{key_id}", status_code=200)
def revoke_api_key_endpoint(
    key_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    try:
        revoke_api_key(db, key_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": f"API key {key_id} revoked"}


# ── MCP identity bridge (internal only) ─────────────────────────────────────


@router.post("/sso-token", response_model=SsoTokenResponse, include_in_schema=False)
def sso_token_bridge(
    payload: SsoTokenRequest,
    x_bridge_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Identity bridge for the MCP OAuth Resource Server (internal only).

    The MCP RS calls this AFTER validating a Bearer token via isap-auth
    introspection; the claims here are therefore already verified. Guarded by
    a shared secret; never route this path through the public gateway.
    """
    if not settings.mcp_bridge_secret:
        raise HTTPException(status_code=404, detail="Not found")
    if not secrets.compare_digest(x_bridge_secret, settings.mcp_bridge_secret):
        raise HTTPException(status_code=403, detail="Invalid bridge secret")

    user = resolve_sso_user(db, payload.username, payload.email or "")

    token = create_access_token(data={"sub": user.username})
    return SsoTokenResponse(access_token=token)


# ── Admin UI SSO (isap-auth, OAuth 2.1 authorization code + PKCE) ───────────

SSO_STATE_COOKIE = "apihub_sso_state"
SSO_VERIFIER_COOKIE = "apihub_sso_verifier"
SSO_REFRESH_COOKIE = "apihub_sso_refresh"


def _require_sso_enabled() -> None:
    """404 rather than 403 when SSO is off — the route should not exist."""
    if not settings.admin_sso_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _sso_callback_url() -> str:
    """Absolute callback URL; must match a line in ISAP_REDIRECT_URIS exactly."""
    return f"{settings.apihub_public_url.rstrip('/')}{settings.api_prefix}/auth/sso/callback"


def _refresh_cookie_path() -> str:
    """Scope the refresh cookie to the SSO routes.

    It is a credential for the user's whole ISAP session, so it must not ride
    along on every ordinary API call the way a "/" cookie would.
    """
    return f"{settings.root_path}{settings.api_prefix}/auth/sso"


def _set_refresh_cookie(response, refresh_token: str) -> None:
    """Store the refresh token where only this server can read it.

    SameSite=Strict, not Lax: a cross-site request that carried this cookie
    would consume the rotation, and the legitimate tab's next refresh would
    then look like a replay to isap-auth — which revokes the entire token
    family. Strict keeps that off the table without a CSRF token.
    """
    response.set_cookie(
        SSO_REFRESH_COOKIE,
        refresh_token,
        max_age=settings.sso_refresh_max_age_seconds,
        httponly=True,
        samesite="strict",
        secure=not settings.debug,
        path=_refresh_cookie_path(),
    )


def _admin_redirect(fragment: str) -> RedirectResponse:
    """Hand the result back to the SPA in the URL fragment.

    A fragment is never sent to the server, so the JWT stays out of nginx
    access logs and Referer headers. The SPA strips it via history.replaceState
    as soon as it has read it.
    """
    response = RedirectResponse(url=f"{settings.root_path}/admin#{fragment}", status_code=302)
    response.delete_cookie(SSO_STATE_COOKIE, path="/")
    response.delete_cookie(SSO_VERIFIER_COOKIE, path="/")
    return response


@router.get("/sso/login", include_in_schema=False)
def sso_login():
    """Start the SSO flow: bounce the browser to isap-auth's /authorize."""
    _require_sso_enabled()
    state = secrets.token_urlsafe(32)
    verifier, challenge = build_pkce_pair()
    params = urlencode(
        {
            "client_id": settings.isap_app_id,
            "response_type": "code",
            "redirect_uri": _sso_callback_url(),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": SCOPE,
        }
    )
    response = RedirectResponse(
        url=f"{settings.isap_auth_public_url.rstrip('/')}/api/v1/authorize?{params}",
        status_code=302,
    )
    # Login-CSRF guard: without it an attacker can feed a victim a callback URL
    # carrying the attacker's own code and silently log them into the attacker's
    # account. isap-auth echoes `state` back unchanged, so we can compare.
    response.set_cookie(
        SSO_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/",
    )
    # The PKCE verifier never leaves this server: the browser only ever carries
    # it back inside an httpOnly cookie it cannot read.
    response.set_cookie(
        SSO_VERIFIER_COOKIE,
        verifier,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/",
    )
    return response


@router.get("/sso/callback", include_in_schema=False)
def sso_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Redeem the auth code, map to a local user, hand a JWT to the SPA.

    Every failure path redirects with a `#sso_error=` fragment rather than
    returning JSON — this endpoint is reached by a browser navigation, so the
    Admin UI login page is the only sensible place to surface an error.
    """
    _require_sso_enabled()

    expected = request.cookies.get(SSO_STATE_COOKIE, "")
    if not expected or not state or not secrets.compare_digest(state, expected):
        return _admin_redirect("sso_error=state_mismatch")
    if not code:
        return _admin_redirect("sso_error=missing_code")
    verifier = request.cookies.get(SSO_VERIFIER_COOKIE, "")
    if not verifier:
        # Same class of failure as a bad state: the flow cannot be tied back to
        # the browser that started it, so the code must not be redeemed.
        return _admin_redirect("sso_error=state_mismatch")

    try:
        tokens = exchange_pkce_code(code, verifier, _sso_callback_url())
        info = fetch_userinfo(tokens["access_token"])
    except SsoExchangeError as e:
        return _admin_redirect(f"sso_error={e.reason}")

    try:
        user = resolve_sso_user(db, info["username"], info.get("email") or "")
    except HTTPException as e:
        reason = "not_provisioned" if "provisioned" in str(e.detail) else "not_eligible"
        return _admin_redirect(f"sso_error={reason}")

    access_token = create_access_token(data={"sub": user.username})
    response = _admin_redirect(f"sso_token={access_token}")
    if tokens.get("refresh_token"):
        _set_refresh_cookie(response, tokens["refresh_token"])
    return response


@router.post("/sso/refresh", response_model=Token, include_in_schema=False)
def sso_refresh(request: Request, db: Session = Depends(get_db)):
    """Mint a fresh API Hub JWT from the stored isap-auth refresh token.

    Without this the Admin UI session ends when the local JWT expires
    (ACCESS_TOKEN_EXPIRE_MINUTES) and the user runs the whole browser login
    again. The refresh token itself never reaches the SPA.

    Failure clears the cookie: a spent or revoked token will not become valid
    on retry, and leaving it in place would make every future refresh replay it
    — which isap-auth treats as theft and answers by revoking the family.
    """
    _require_sso_enabled()

    stored = request.cookies.get(SSO_REFRESH_COOKIE, "")
    if not stored:
        raise HTTPException(status_code=401, detail="No SSO session")

    try:
        tokens = refresh_tokens(stored)
        info = fetch_userinfo(tokens["access_token"])
        user = resolve_sso_user(db, info["username"], info.get("email") or "")
    except (SsoExchangeError, HTTPException):
        response = JSONResponse(status_code=401, content={"detail": "SSO session expired"})
        response.delete_cookie(SSO_REFRESH_COOKIE, path=_refresh_cookie_path())
        return response

    response = JSONResponse(content={"access_token": create_access_token(data={"sub": user.username})})
    # Rotation: isap-auth invalidates the presented token, so the replacement
    # must be stored even when the rest of this request succeeded trivially.
    if tokens.get("refresh_token"):
        _set_refresh_cookie(response, tokens["refresh_token"])
    return response


@router.post("/sso/logout", include_in_schema=False)
def sso_logout():
    """Drop the refresh cookie so the next visit starts a fresh login.

    The SPA clears its own JWT locally; without this the browser would still
    hold a usable refresh token afterwards.
    """
    response = JSONResponse(content={"message": "Signed out"})
    response.delete_cookie(SSO_REFRESH_COOKIE, path=_refresh_cookie_path())
    return response
