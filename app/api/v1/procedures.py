"""Endpoints for executing Oracle stored procedures and functions.

All endpoints require a ``{db}`` path parameter to select the target database.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.databases import get_db_alias
from app.middleware.audit import record_audit
from app.models.user import User
from app.schemas.query import ProcedureRequest, ProcedureResponse, TaskStatusResponse
from app.services.auth_service import PermissionChecker, get_current_user
from app.tasks.procedure_tasks import async_procedure

router = APIRouter(tags=["Procedures"])


def _audit_params(payload: ProcedureRequest) -> dict:
    return {"params": payload.params, "out_params": payload.out_params, "is_function": payload.is_function}


@router.post("/{db}/procedures/execute", response_model=ProcedureResponse)
def execute(
    request: Request,
    payload: ProcedureRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute an Oracle procedure or function synchronously."""
    record_audit(request, "procedure", payload.name, params=_audit_params(payload))

    resource = f"procedure:{payload.name}"
    checker = PermissionChecker(resource, "execute")
    checker(current_user)

    from app.services.procedure_service import execute_procedure

    try:
        result = execute_procedure(
            name=payload.name,
            params=payload.params,
            out_params=payload.out_params,
            is_function=payload.is_function,
            db_alias=db,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Procedure execution error: {e}")

    return ProcedureResponse(name=payload.name, **result)


@router.post("/{db}/procedures/execute-async", response_model=TaskStatusResponse)
def execute_async(
    request: Request,
    payload: ProcedureRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute an Oracle procedure or function asynchronously via Celery."""
    record_audit(request, "procedure", payload.name, params=_audit_params(payload))

    resource = f"procedure:{payload.name}"
    checker = PermissionChecker(resource, "execute")
    checker(current_user)

    task = async_procedure.delay(
        name=payload.name,
        params=payload.params,
        out_params=payload.out_params,
        is_function=payload.is_function,
        db_alias=db,
    )
    return TaskStatusResponse(task_id=task.id, status="PENDING")
