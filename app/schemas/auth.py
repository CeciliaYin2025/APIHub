"""Pydantic schemas for authentication and user management."""

from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: str | None = None


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str | None = None
    is_superuser: bool = False
    role_ids: list[int] = []


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str | None
    is_active: bool
    is_superuser: bool
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
