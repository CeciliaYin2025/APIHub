"""Pydantic schemas for authentication and user management."""

from typing import Literal

from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: str | None = None


class UserCreate(BaseModel):
    username: str
    # Optional so an SSO account can be pre-provisioned without inventing a
    # password nobody will ever use. Required for auth_source="local", which the
    # endpoint enforces (a passwordless local account could never sign in).
    password: str | None = None
    full_name: str | None = None
    is_superuser: bool = False
    is_service_account: bool = False
    auth_source: Literal["local", "sso"] = "local"
    role_ids: list[int] = []


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str | None
    is_active: bool
    is_superuser: bool
    is_service_account: bool = False
    auth_source: str = "local"
    roles: list["RoleOut"] = []

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permission_ids: list[int] = []


class RoleOut(BaseModel):
    id: int
    name: str
    description: str | None
    permissions: list["PermissionOut"] = []

    model_config = {"from_attributes": True}


class PermissionCreate(BaseModel):
    resource: str
    action: str
    description: str | None = None


class PermissionOut(BaseModel):
    id: int
    resource: str
    action: str
    description: str | None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None
    is_service_account: bool | None = None
    auth_source: Literal["local", "sso"] | None = None
    role_ids: list[int] | None = None


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[int] | None = None


class PermissionUpdate(BaseModel):
    resource: str | None = None
    action: str | None = None
    description: str | None = None


class PasswordReset(BaseModel):
    new_password: str
