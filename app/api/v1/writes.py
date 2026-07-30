"""Endpoints for writing data back to Oracle (INSERT/UPDATE/DELETE).

Write operations require a ``{db}`` path parameter.
Task status lookup is database-independent (Celery task ID is global).
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.databases import get_db_alias
from app.middleware.audit import record_audit
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
    request: Request,
    payload: WriteRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute a write operation (sync or async) against the specified database."""
    record_audit(request, "write", payload.table, params={
        "operation": payload.operation, "data": payload.data,
        "where": payload.where, "async_mode": payload.async_mode,
    })

    resource = f"table:{payload.table}"
    checker = PermissionChecker(resource, "write")
    checker(current_user)

    if payload.async_mode:
        task = async_write.delay(
            operation=payload.operation,
            table=payload.table,
            data=payload.data,
            where=payload.where,
            db_alias=db,
        )
        return WriteResponse(
            operation=payload.operation,
            task_id=task.id,
            message="Task enqueued for async processing",
        )

    try:
        affected = execute_write(
            operation=payload.operation,
            table=payload.table,
            data=payload.data,
            where=payload.where,
            db_alias=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Write operation error: {e}")

    record_audit(request, result_rows=affected)

    return WriteResponse(
        operation=payload.operation,
        affected_rows=affected,
        message=f"{payload.operation.upper()} completed successfully",
    )


@router.post("/{db}/writes/batch", response_model=BatchWriteResponse)
def batch_execute(
    request: Request,
    payload: BatchWriteRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute multiple DML operations within a single database transaction.

    All operations target the same table on the specified database.
    If ANY operation fails, the entire transaction is **rolled back**.
    """
    resource = f"table:{payload.table}"
    checker = PermissionChecker(resource, "write")
    checker(current_user)

    ops = [op.model_dump() for op in payload.operations]
    record_audit(request, "write", payload.table, params={"operations": ops, "async_mode": payload.async_mode})

    if payload.async_mode:
        from app.tasks.write_tasks import async_batch_write

        task = async_batch_write.delay(table=payload.table, operations=ops, db_alias=db)
        return BatchWriteResponse(
            table=payload.table,
            task_id=task.id,
            message="Batch transaction enqueued for async processing",
        )

    try:
        result = execute_batch_write(table=payload.table, operations=ops, db_alias=db)
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

    record_audit(request, result_rows=result["total_affected"])

    return BatchWriteResponse(
        table=payload.table,
        total_affected=result["total_affected"],
        operation_count=result["operation_count"],
        committed=result["committed"],
        results=result["results"],
        message="Batch transaction committed successfully",
    )
