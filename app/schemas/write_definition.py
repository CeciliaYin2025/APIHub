"""Pydantic schemas for named write definitions and their execution."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.services.named_write_service import validate_definition_shape

IDENTIFIER = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"


class WriteColumnSchema(BaseModel):
    name: str = Field(..., pattern=IDENTIFIER)
    type: Literal["string", "number", "date", "datetime"] = "string"
    required: bool = False
    description: str = ""


class WriteDefinitionCreate(BaseModel):
    name: str = Field(..., pattern=IDENTIFIER)
    group_name: str = Field("", max_length=100)
    description: str = ""
    table_name: str = Field(..., min_length=1, max_length=128)
    mode: Literal["insert", "replace"]
    columns: list[WriteColumnSchema] = Field(..., min_length=1)
    key_columns: list[str] = Field(default_factory=list)
    max_rows: int = Field(10000, ge=1, le=100000)
    permission: str = ""
    enabled: bool = True

    @model_validator(mode="after")
    def _shape(self):
        validate_definition_shape(self.mode, [c.model_dump() for c in self.columns], self.key_columns)
        return self


class WriteDefinitionUpdate(BaseModel):
    """Partial update. The merged result is re-validated by the router."""

    group_name: str | None = Field(None, max_length=100)
    description: str | None = None
    table_name: str | None = Field(None, min_length=1, max_length=128)
    mode: Literal["insert", "replace"] | None = None
    columns: list[WriteColumnSchema] | None = Field(None, min_length=1)
    key_columns: list[str] | None = None
    max_rows: int | None = Field(None, ge=1, le=100000)
    permission: str | None = None
    enabled: bool | None = None


class WriteDefinitionOut(BaseModel):
    id: int
    name: str
    db_alias: str
    group_name: str
    description: str
    table_name: str
    mode: str
    columns: list[dict[str, Any]]
    key_columns: list[str]
    max_rows: int
    permission: str
    enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NamedWriteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    key: dict[str, Any] | None = Field(
        default=None, description="replace mode: the key_columns values that bound the delete"
    )
    rows: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = Field(default=False, description="Run in a transaction, report counts, then roll back")


class NamedWriteResponse(BaseModel):
    success: bool = True
    name: str
    mode: str
    deleted_rows: int
    inserted_rows: int
    committed: bool
    dry_run: bool
