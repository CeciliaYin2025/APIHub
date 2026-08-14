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
    # Free-text category. Empty is allowed and groups under "(ungrouped)" in the
    # Admin UI; it is a label only and carries no authorization meaning.
    group_name: str = Field("", max_length=100)
    description: str = ""
    # What the query returns — usually the column list. Surfaced to LLMs in the
    # MCP tool description, so it is worth writing even though nothing enforces it.
    result_description: str = ""
    sql: str = Field(..., min_length=1)
    permission: str = ""
    # False takes the definition out of service without deleting it: it drops out
    # of the definition list and therefore out of the MCP catalog, and execution
    # refuses it. Defaults true — disabling is always explicit.
    enabled: bool = True
    params: dict[str, ParamDefSchema] = Field(default_factory=dict)
    order_by: list[OrderBySchema] = Field(default_factory=list)


class SqlDefinitionUpdate(BaseModel):
    group_name: str | None = Field(None, max_length=100)
    description: str | None = None
    result_description: str | None = None
    sql: str | None = None
    permission: str | None = None
    enabled: bool | None = None
    params: dict[str, ParamDefSchema] | None = None
    order_by: list[OrderBySchema] | None = None


class SqlDefinitionOut(BaseModel):
    id: int
    name: str
    db_alias: str
    group_name: str = ""
    description: str
    result_description: str = ""
    sql: str
    permission: str
    enabled: bool = True
    params: dict[str, ParamDefSchema]
    order_by: list[OrderBySchema] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
