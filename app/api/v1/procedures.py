"""Endpoints for executing Oracle stored procedures and functions.

All endpoints require a ``{db}`` path parameter to select the target database.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.databases import get_db_alias
from app.models.user import User
from app.schemas.query import ProcedureRequest, ProcedureResponse, TaskStatusResponse
from app.services.auth_service import PermissionChecker, get_current_user
from app.tasks.procedure_tasks import async_procedure

router = APIRouter(tags=["Procedures"])


@router.post("/{db}/procedures/execute", response_model=ProcedureResponse)
def execute(
    request: ProcedureRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute an Oracle procedure or function synchronously."""
    resource = f"procedure:{request.name}"
    checker = PermissionChecker(resource, "execute")
    checker(current_user)

    from app.services.procedure_service import execute_procedure

    try:
        result = execute_procedure(
            name=request.name,
            params=request.params,
            out_params=request.out_params,
            is_function=request.is_function,
            db_alias=db,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Procedure execution error: {e}")

    return ProcedureResponse(name=request.name, **result)


@router.post("/{db}/procedures/execute-async", response_model=TaskStatusResponse)
def execute_async(
    request: ProcedureRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute an Oracle procedure or function asynchronously via Celery."""
    resource = f"procedure:{request.name}"
    checker = PermissionChecker(resource, "execute")
    checker(current_user)

    task = async_procedure.delay(
        name=request.name,
        params=request.params,
        out_params=request.out_params,
        is_function=request.is_function,
        db_alias=db,
    )
    return TaskStatusResponse(task_id=task.id, status="PENDING")
