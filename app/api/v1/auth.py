"""Authentication endpoints: login, user/role/permission CRUD."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.security import create_access_token, get_password_hash
from app.db.session import get_db
from app.models.user import Permission, Role, User
from app.schemas.auth import (
    PermissionCreate,
    PermissionOut,
    RoleCreate,
    RoleOut,
    Token,
    UserCreate,
    UserOut,
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
    user = User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name,
        is_superuser=payload.is_superuser,
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
