"""Endpoints for writing data back to Oracle (INSERT/UPDATE/DELETE).

Write operations require a ``{db}`` path parameter.
Task status lookup is database-independent (Celery task ID is global).
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.databases import get_db_alias
from app.models.user import User
from app.schemas.query import (
    BatchWriteRequest,
    BatchWriteResponse,
    TaskStatusResponse,
    WriteRequest,
    WriteResponse,
)
from app.services.auth_service import PermissionChecker, get_current_user
from app.services.write_service import BatchWriteError, execute_batch_write, execute_write
from app.tasks.write_tasks import async_write

router = APIRouter(tags=["Writes"])


# ── Task status (database-independent) ───────────────────────────────────


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task_status(task_id: str, _current_user: User = Depends(get_current_user)):
    """Check the status of an async write or procedure task."""
    from app.tasks.celery_app import celery_app

    result = celery_app.AsyncResult(task_id)
    return TaskStatusResponse(
        task_id=task_id,
        status=result.status,
        result=result.result if result.ready() else None,
    )


# ── Write operations (database-specific) ─────────────────────────────────


@router.post("/{db}/writes/execute", response_model=WriteResponse)
def execute(
    request: WriteRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute a write operation (sync or async) against the specified database."""
    resource = f"table:{request.table}"
    checker = PermissionChecker(resource, "write")
    checker(current_user)

    if request.async_mode:
        task = async_write.delay(
            operation=request.operation,
            table=request.table,
            data=request.data,
            where=request.where,
            db_alias=db,
        )
        return WriteResponse(
            operation=request.operation,
            task_id=task.id,
            message="Task enqueued for async processing",
        )

    try:
        affected = execute_write(
            operation=request.operation,
            table=request.table,
            data=request.data,
            where=request.where,
            db_alias=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Write operation error: {e}")

    return WriteResponse(
        operation=request.operation,
        affected_rows=affected,
        message=f"{request.operation.upper()} completed successfully",
    )


@router.post("/{db}/writes/batch", response_model=BatchWriteResponse)
def batch_execute(
    request: BatchWriteRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute multiple DML operations within a single database transaction.

    All operations target the same table on the specified database.
    If ANY operation fails, the entire transaction is **rolled back**.
    """
    resource = f"table:{request.table}"
    checker = PermissionChecker(resource, "write")
    checker(current_user)

    ops = [op.model_dump() for op in request.operations]

    if request.async_mode:
        from app.tasks.write_tasks import async_batch_write

        task = async_batch_write.delay(table=request.table, operations=ops, db_alias=db)
        return BatchWriteResponse(
            table=request.table,
            task_id=task.id,
            message="Batch transaction enqueued for async processing",
        )

    try:
        result = execute_batch_write(table=request.table, operations=ops, db_alias=db)
    except BatchWriteError as e:
        raise HTTPException(status_code=400, detail={
            "message": str(e),
            "failed_index": e.failed_index,
            "committed": e.committed,
            "completed_operations": e.results,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch write error: {e}")

    return BatchWriteResponse(
        table=request.table,
        total_affected=result["total_affected"],
        operation_count=result["operation_count"],
        committed=result["committed"],
        results=result["results"],
        message="Batch transaction committed successfully",
    )
