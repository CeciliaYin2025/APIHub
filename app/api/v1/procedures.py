"""Endpoints for executing Oracle stored procedures and functions."""

from fastapi import APIRouter, Depends, HTTPException

from app.models.user import User
from app.schemas.query import ProcedureRequest, ProcedureResponse, TaskStatusResponse
from app.services.auth_service import PermissionChecker, get_current_user
from app.tasks.procedure_tasks import async_procedure

router = APIRouter(prefix="/procedures", tags=["Procedures"])


@router.post("/execute", response_model=ProcedureResponse)
def execute(request: ProcedureRequest, current_user: User = Depends(get_current_user)):
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
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Procedure execution error: {e}")

    return ProcedureResponse(name=request.name, **result)


@router.post("/execute-async", response_model=TaskStatusResponse)
def execute_async(request: ProcedureRequest, current_user: User = Depends(get_current_user)):
    """Execute an Oracle procedure or function asynchronously via Celery."""
    resource = f"procedure:{request.name}"
    checker = PermissionChecker(resource, "execute")
    checker(current_user)

    task = async_procedure.delay(
        name=request.name,
        params=request.params,
        out_params=request.out_params,
        is_function=request.is_function,
    )
    return TaskStatusResponse(task_id=task.id, status="PENDING")
