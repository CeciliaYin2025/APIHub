"""Pydantic schemas for SQL definition CRUD operations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ParamDefSchema(BaseModel):
    type: str = Field("string", pattern=r"^(string|number|date)$")
    required: bool = False
    description: str = ""


class OrderBySchema(BaseModel):
    column: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    direction: Literal["ASC", "DESC"] = "ASC"


class SqlDefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    db_alias: str = Field("default", min_length=1, max_length=64)
    description: str = ""
    sql: str = Field(..., min_length=1)
    permission: str = ""
    params: dict[str, ParamDefSchema] = Field(default_factory=dict)
    order_by: list[OrderBySchema] = Field(default_factory=list)


class SqlDefinitionUpdate(BaseModel):
    description: str | None = None
    sql: str | None = None
    permission: str | None = None
    params: dict[str, ParamDefSchema] | None = None
    order_by: list[OrderBySchema] | None = None


class SqlDefinitionOut(BaseModel):
    id: int
    name: str
    db_alias: str
    description: str
    sql: str
    permission: str
    params: dict[str, ParamDefSchema]
    order_by: list[OrderBySchema] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
