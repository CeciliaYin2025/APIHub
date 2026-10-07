"""Named write definitions — CRUD (superuser) and execution (``write:<name>``).

Definitions are scoped by database alias like SQL definitions, but there is no
in-memory registry: writes are low-volume, so each call reads its definition
from the platform store and CRUD needs no reload.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.v1.databases import get_db_alias
from app.db.pool_adapter import PoolExhaustedError
from app.db.session import get_db
from app.middleware.audit import record_audit
from app.models.user import User
from app.models.write_definition import WriteDefinitionModel
from app.schemas.write_definition import (
    NamedWriteRequest,
    NamedWriteResponse,
    WriteDefinitionCreate,
    WriteDefinitionOut,
    WriteDefinitionUpdate,
)
from app.services.auth_service import PermissionChecker, get_current_user
from app.services.named_write_service import (
    NamedWriteError,
    NamedWriteExecutionError,
    check_against_table,
    execute_named_write,
    validate_definition_shape,
)

router = APIRouter(tags=["Write Definitions"])


# ── helpers ──────────────────────────────────────────────────────────────


def _require_superuser(user: User):
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser privileges required")


def _row_to_out(row: WriteDefinitionModel) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "db_alias": row.db_alias,
        "group_name": row.group_name or "",
        "description": row.description or "",
        "table_name": row.table_name,
        "mode": row.mode,
        "columns": row.columns or [],
        "key_columns": row.key_columns or [],
        "max_rows": row.max_rows if row.max_rows is not None else 10000,
        "permission": row.permission,
        "enabled": True if row.enabled is None else bool(row.enabled),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _get_row(pgdb: Session, name: str, db: str) -> WriteDefinitionModel:
    row = (
        pgdb.query(WriteDefinitionModel)
        .filter(WriteDefinitionModel.name == name, WriteDefinitionModel.db_alias == db)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Write definition '{name}' not found for database '{db}'")
    return row


def _check_table_or_400(row: WriteDefinitionModel) -> None:
    try:
        missing = check_against_table(row)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PoolExhaustedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not describe table '{row.table_name}': {e}")
    if missing:
        raise HTTPException(
            status_code=400, detail=f"Columns not found in table '{row.table_name}': {', '.join(missing)}"
        )


# ── Definition management (superuser) ────────────────────────────────────


@router.get("/{db}/writes/definitions", response_model=list[WriteDefinitionOut])
def list_definitions(
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """List this database's write definitions, disabled ones included. Superuser only."""
    _require_superuser(current_user)
    rows = pgdb.query(WriteDefinitionModel).filter(WriteDefinitionModel.db_alias == db).all()
    return [_row_to_out(r) for r in rows]


@router.get("/{db}/writes/definitions/{name}", response_model=WriteDefinitionOut)
def get_definition(
    name: str,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    _require_superuser(current_user)
    return _row_to_out(_get_row(pgdb, name, db))


@router.post("/{db}/writes/definitions", response_model=WriteDefinitionOut, status_code=201)
def create_definition(
    request: Request,
    body: WriteDefinitionCreate,
    skip_table_check: bool = False,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Create a write definition. Superuser only.

    The declared table and columns are checked against the real table through
    this alias unless ``skip_table_check`` (for a table that does not exist yet).
    """
    _require_superuser(current_user)
    record_audit(request, "definition", body.name, params=body.model_dump(mode="json"))

    exists = (
        pgdb.query(WriteDefinitionModel)
        .filter(WriteDefinitionModel.name == body.name, WriteDefinitionModel.db_alias == db)
        .first()
    )
    if exists:
        raise HTTPException(status_code=409, detail=f"Write definition '{body.name}' already exists for '{db}'")

    row = WriteDefinitionModel(
        name=body.name,
        db_alias=db,
        group_name=body.group_name,
        description=body.description,
        table_name=body.table_name.strip(),
        mode=body.mode,
        columns=[c.model_dump() for c in body.columns],
        key_columns=list(body.key_columns),
        max_rows=body.max_rows,
        permission=body.permission or f"write:{body.name}",
        enabled=body.enabled,
    )
    if not skip_table_check:
        _check_table_or_400(row)
    pgdb.add(row)
    pgdb.commit()
    pgdb.refresh(row)
    return _row_to_out(row)


@router.put("/{db}/writes/definitions/{name}", response_model=WriteDefinitionOut)
def update_definition(
    request: Request,
    name: str,
    body: WriteDefinitionUpdate,
    skip_table_check: bool = False,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Update a write definition (name and alias are fixed). Superuser only."""
    _require_superuser(current_user)
    record_audit(request, "definition", name, params=body.model_dump(mode="json", exclude_none=True))
    row = _get_row(pgdb, name, db)

    mode = body.mode if body.mode is not None else row.mode
    columns = [c.model_dump() for c in body.columns] if body.columns is not None else row.columns
    key_columns = list(body.key_columns) if body.key_columns is not None else (row.key_columns or [])
    try:
        validate_definition_shape(mode, columns, key_columns)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    schema_changed = body.table_name is not None or body.columns is not None
    row.mode, row.columns, row.key_columns = mode, columns, key_columns
    if body.table_name is not None:
        row.table_name = body.table_name.strip()
    for field in ("group_name", "description", "max_rows", "permission", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)

    if schema_changed and not skip_table_check:
        _check_table_or_400(row)
    pgdb.commit()
    pgdb.refresh(row)
    return _row_to_out(row)


@router.delete("/{db}/writes/definitions/{name}")
def delete_definition(
    request: Request,
    name: str,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    _require_superuser(current_user)
    record_audit(request, "definition", name)
    row = _get_row(pgdb, name, db)
    pgdb.delete(row)
    pgdb.commit()
    return {"message": f"Write definition '{name}' deleted"}


@router.post("/{db}/writes/definitions/{name}/validate")
def validate_definition(
    name: str,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Re-check the declared table and columns against the real table."""
    _require_superuser(current_user)
    _check_table_or_400(_get_row(pgdb, name, db))
    return {"valid": True}


# ── Execution ────────────────────────────────────────────────────────────


@router.post("/{db}/writes/named/execute", response_model=NamedWriteResponse)
def execute(
    request: Request,
    payload: NamedWriteRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Run a named write: one transaction, delete bounded by the definition's key."""
    params = {"key": payload.key, "rows": payload.rows, "dry_run": payload.dry_run}
    record_audit(request, "write", payload.name, params=params)

    row = _get_row(pgdb, payload.name, db)
    if not row.enabled:
        raise HTTPException(status_code=409, detail=f"Write definition '{payload.name}' is disabled")
    PermissionChecker(row.permission, "write")(current_user)

    try:
        result = execute_named_write(row, payload.key, payload.rows, dry_run=payload.dry_run)
    except NamedWriteError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PoolExhaustedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except NamedWriteExecutionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Database rejected the write during {e.phase}; rolled back",
                "error": str(e),
                "phase": e.phase,
                "committed": False,
            },
        )

    # Separate counts in the audit row: batch writes only record a total, which
    # left "how many did it delete" to Oracle flashback.
    record_audit(
        request, params={**params, "result": result}, result_rows=result["deleted_rows"] + result["inserted_rows"]
    )
    return NamedWriteResponse(name=row.name, mode=row.mode, **result)
