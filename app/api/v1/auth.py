"""Authentication endpoints: login, user/role/permission CRUD."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.security import create_access_token, get_password_hash
from app.db.session import get_db
from app.models.user import Permission, Role, User
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
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
from app.services.api_key_service import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.services.auth_service import authenticate_user, get_current_active_superuser, get_current_user

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
    user = User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name,
        is_superuser=payload.is_superuser,
        is_service_account=payload.is_service_account,
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
    # Enforce service-account / superuser mutual exclusion on the resulting state.
    new_is_service = payload.is_service_account if payload.is_service_account is not None else user.is_service_account
    new_is_superuser = payload.is_superuser if payload.is_superuser is not None else user.is_superuser
    if new_is_service and new_is_superuser:
        raise HTTPException(status_code=400, detail="A service account cannot be a superuser")
    if payload.is_service_account is not None:
        user.is_service_account = payload.is_service_account
    if payload.is_superuser is not None:
        user.is_superuser = payload.is_superuser
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
