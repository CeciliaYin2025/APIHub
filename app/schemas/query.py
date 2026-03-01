"""Pydantic schemas for query, procedure, and write-back endpoints."""

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Execute a named SQL query definition."""
    query_name: str = Field(..., description="Name of the registered SQL definition")
    params: dict[str, Any] = Field(default_factory=dict, description="Bind parameters")
    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=50, ge=1, le=1000, description="Rows per page")


class QueryResponse(BaseModel):
    """Open data style response."""
    success: bool = True
    query_name: str
    total: int
    page: int
    page_size: int
    columns: list[str]
    data: list[dict[str, Any]]


class ProcedureRequest(BaseModel):
    """Execute an Oracle stored procedure or function."""
    name: str = Field(..., description="Fully qualified procedure/function name, e.g. PKG.PROC_NAME")
    params: dict[str, Any] = Field(default_factory=dict, description="Input parameters")
    out_params: list[str] = Field(default_factory=list, description="Names of OUT parameter placeholders")
    is_function: bool = Field(default=False, description="True if calling a function (returns a value)")


class ProcedureResponse(BaseModel):
    success: bool = True
    name: str
    result: Any = None
    out_params: dict[str, Any] = Field(default_factory=dict)


class WriteRequest(BaseModel):
    """Write data back to the database (insert/update/delete)."""
    operation: str = Field(..., pattern="^(insert|update|delete)$", description="DML operation type")
    table: str = Field(..., description="Target table name")
    data: list[dict[str, Any]] = Field(..., description="Rows to process")
    where: dict[str, Any] | None = Field(default=None, description="WHERE clause params (for update/delete)")
    async_mode: bool = Field(default=False, description="If true, enqueue via Celery and return task ID")


class WriteResponse(BaseModel):
    success: bool = True
    operation: str
    affected_rows: int = 0
    task_id: str | None = None
    message: str = ""


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Any = None
