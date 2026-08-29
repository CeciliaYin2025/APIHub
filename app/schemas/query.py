"""Pydantic schemas for query, procedure, and write-back endpoints."""

from typing import Any

from pydantic import BaseModel, Field, model_serializer


class QueryRequest(BaseModel):
    """Execute a named SQL query definition.

    Fetch modes (controlled by ``fetch_mode``):

    - **page** (default): paginated results, use ``page`` + ``page_size``.
    - **all**: return every row (no pagination). Use with caution on large datasets.
    - **batch**: stream results in a fixed-size batch, use ``batch_size`` + ``batch_offset``.
    - **top_n**: return the first N rows, use ``top_n``.
    - **one**: return exactly one row (or 404).
    - **count**: return only the matching row count (no data), via ``total``.
    """

    query_name: str = Field(..., description="Name of the registered SQL definition")
    params: dict[str, Any] = Field(default_factory=dict, description="Bind parameters")
    fetch_mode: str = Field(
        default="page",
        pattern="^(page|all|batch|top_n|one|count)$",
        description="Fetch mode: page, all, batch, top_n, one, count",
    )
    # page mode
    page: int = Field(default=1, ge=1, description="Page number (fetch_mode=page)")
    page_size: int = Field(default=50, ge=1, le=1000, description="Rows per page (fetch_mode=page)")
    # batch mode
    batch_size: int = Field(default=500, ge=1, le=10000, description="Batch size (fetch_mode=batch)")
    batch_offset: int = Field(default=0, ge=0, description="Starting row offset (fetch_mode=batch)")
    # top_n mode
    top_n: int = Field(default=10, ge=1, le=1000, description="Number of rows to return (fetch_mode=top_n)")
    # Audience view — only meaningful for definitions with an audience-specific
    # column set (today: eo_summary). See app/services/eo_field_views.py.
    view: str | None = Field(
        default=None,
        pattern="^(mc|sales)$",
        description=(
            "Audience view for definitions that have one: 'mc' (default, full report) or "
            "'sales' (the narrower column set sent to Sales). Ignored by every other definition. "
            "A caller holding eo:read:scoped is served 'sales' whatever they ask for."
        ),
    )


class QueryResponse(BaseModel):
    """Open data style response."""

    success: bool = True
    query_name: str
    fetch_mode: str = "page"
    total: int
    page: int | None = None
    page_size: int | None = None
    batch_offset: int | None = None
    batch_size: int | None = None
    has_more: bool | None = None
    columns: list[str]
    data: list[dict[str, Any]]
    # Only populated for definitions with an audience view. `view` is what was
    # actually applied, which is not always what was asked for: `view_downgraded`
    # marks a request narrowed by the caller's permissions, so the narrowing is
    # reported rather than silent.
    view: str | None = None
    view_downgraded: bool | None = None
    masked_columns: list[str] | None = None

    @model_serializer(mode="wrap")
    def _drop_unused_view_keys(self, handler):
        """Omit the three view keys entirely when the definition has no view.

        Most definitions have no audience view, and emitting `"view": null` on
        every response would change the documented shape of every existing
        endpoint — including the count-mode contract that clients assert on.
        They stay in the OpenAPI schema (they are real fields); they just do not
        appear on the wire unless they mean something. Blanket
        `response_model_exclude_none` would not do: `page`, `page_size` and
        `has_more` are legitimately null per fetch mode and must stay.
        """
        data = handler(self)
        if self.view is None:
            for key in ("view", "view_downgraded", "masked_columns"):
                data.pop(key, None)
        return data


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


class BatchOperation(BaseModel):
    """A single operation within a batch transaction."""

    operation: str = Field(..., pattern="^(insert|update|delete)$")
    data: list[dict[str, Any]] = Field(default_factory=list, description="Rows to process")
    where: dict[str, Any] | None = Field(default=None, description="WHERE clause params (for update/delete)")


class BatchWriteRequest(BaseModel):
    """Execute multiple DML operations within a single database transaction.

    All operations target the same table. If any operation fails, the entire
    transaction is rolled back (atomicity).
    """

    table: str = Field(..., description="Target table name")
    operations: list[BatchOperation] = Field(..., min_length=1, description="Ordered list of DML operations")
    async_mode: bool = Field(default=False, description="If true, enqueue via Celery")


class BatchWriteResponse(BaseModel):
    success: bool = True
    table: str
    total_affected: int = 0
    operation_count: int = 0
    committed: bool = False
    results: list[dict[str, Any]] = []
    task_id: str | None = None
    message: str = ""


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Any = None
