"""Query endpoints — read tables/views as open data via registered SQL definitions.

SQL definitions are scoped by database alias. CRUD endpoints are under
``/{db}/queries/definitions``.  Query execution is ``/{db}/queries/execute``.
"""

from datetime import date

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.v1.databases import get_db_alias
from app.core.config import settings
from app.db.session import get_db
from app.middleware.audit import record_audit
from app.models.sql_definition import SqlDefinitionModel
from app.models.user import User
from app.schemas.query import QueryRequest, QueryResponse
from app.schemas.sql_definition import SqlDefinitionCreate, SqlDefinitionOut, SqlDefinitionUpdate
from app.services.auth_service import PermissionChecker, get_current_user, has_permission
from app.services.query_service import execute_query
from app.services.sql_registry import sql_registry

router = APIRouter(tags=["Queries"])


# ── helpers ──────────────────────────────────────────────────────────────


def _require_superuser(user: User):
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser privileges required")


def _row_to_out(row: SqlDefinitionModel) -> dict:
    """Convert ORM row to a dict matching SqlDefinitionOut."""
    return {
        "id": row.id,
        "name": row.name,
        "db_alias": row.db_alias or "default",
        "group_name": row.group_name or "",
        "description": row.description or "",
        "result_description": row.result_description or "",
        "sql": row.sql,
        "permission": row.permission,
        "params": row.params or {},
        "order_by": row.order_by or [],
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


# ── SQL definition management (scoped by database) ───────────────────────
# NOTE: Static paths (reload, import-yaml) MUST come before {name} param path.


@router.get("/{db}/queries/definitions")
def list_definitions(
    db: str = Depends(get_db_alias),
    permitted_only: bool = False,
    current_user: User = Depends(get_current_user),
):
    """List SQL definitions for a specific database.

    ``permitted_only=true`` drops the definitions the caller could not actually
    execute (same rule as the ``PermissionChecker`` on ``/execute``). It is
    opt-in because the Admin UI's management view must show every definition to
    the superuser who maintains them; callers that *hand the list onward* —
    the MCP tool catalog, the Query Console picker — should set it, or they
    advertise names, descriptions and parameters the caller has no right to.
    """
    definitions = sql_registry.list_all(db_alias=db)
    if permitted_only:
        definitions = [d for d in definitions if has_permission(current_user, d.permission, "read")]
    return [
        {
            "name": d.name,
            "db_alias": d.db_alias,
            "group_name": d.group_name,
            "description": d.description,
            "result_description": d.result_description,
            "permission": d.permission,
            "params": {
                k: {"type": v.type, "required": v.required, "description": v.description} for k, v in d.params.items()
            },
        }
        for d in definitions
    ]


@router.post("/{db}/queries/definitions/reload")
def reload_definitions(
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Hot-reload SQL definitions from DB (or YAML fallback). Superuser only."""
    _require_superuser(current_user)
    sql_registry.reload()
    count = len(sql_registry.list_all(db_alias=db))
    return {"message": "SQL definitions reloaded", "count": count}


@router.post("/{db}/queries/definitions/import-yaml")
def import_yaml_definitions(
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Import YAML definitions into the database for this db_alias. Existing names are skipped. Superuser only."""
    _require_superuser(current_user)

    from pathlib import Path

    base = Path(settings.sql_definitions_dir)
    if not base.exists():
        raise HTTPException(status_code=404, detail=f"YAML directory not found: {base}")

    imported = 0
    skipped = 0
    errors = []

    for path in sorted(base.glob("*.y*ml")):
        try:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)

            name = raw["name"]
            file_db_alias = raw.get("db_alias", "default")

            # Only import definitions that match this database
            if file_db_alias != db:
                continue

            existing = (
                pgdb.query(SqlDefinitionModel)
                .filter(SqlDefinitionModel.name == name, SqlDefinitionModel.db_alias == db)
                .first()
            )
            if existing:
                skipped += 1
                continue

            params = {}
            for pname, pdef in (raw.get("params") or {}).items():
                params[pname] = {
                    "type": pdef.get("type", "string"),
                    "required": pdef.get("required", False),
                    "description": pdef.get("description", ""),
                }

            row = SqlDefinitionModel(
                name=name,
                db_alias=db,
                group_name=raw.get("group_name", ""),
                description=raw.get("description", ""),
                result_description=raw.get("result_description", ""),
                sql=raw["sql"].strip(),
                permission=raw.get("permission", f"query:{name}"),
                params=params,
                order_by=raw.get("order_by") or [],
            )
            pgdb.add(row)
            imported += 1
        except Exception as e:
            errors.append(f"{path.name}: {e}")

    pgdb.commit()
    sql_registry.reload_from_db()

    return {
        "message": "YAML import completed",
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


@router.get("/{db}/queries/definitions/export-yaml")
def export_definitions_yaml(
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Export all SQL definitions for this database as a downloadable YAML file. Superuser only."""
    _require_superuser(current_user)
    rows = (
        pgdb.query(SqlDefinitionModel).filter(SqlDefinitionModel.db_alias == db).order_by(SqlDefinitionModel.name).all()
    )

    defs_list = []
    for row in rows:
        entry = {
            "name": row.name,
            "group_name": row.group_name or "",
            "description": row.description or "",
            "result_description": row.result_description or "",
            "sql": row.sql,
            "permission": row.permission,
            "params": row.params or {},
            "order_by": row.order_by or [],
        }
        defs_list.append(entry)

    content = yaml.dump(defs_list, allow_unicode=True, default_flow_style=False, sort_keys=False)
    filename = f"sql_definitions_{db}_{date.today().isoformat()}.yaml"

    return Response(
        content=content,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{db}/queries/definitions/import-yaml-file")
async def import_yaml_file(
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    """Import SQL definitions from an uploaded YAML file. Existing names are skipped. Superuser only."""
    _require_superuser(current_user)

    content = await file.read()
    try:
        raw_list = yaml.safe_load(content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    if not isinstance(raw_list, list):
        raw_list = [raw_list]

    imported, skipped, errors = 0, 0, []
    for i, raw in enumerate(raw_list):
        try:
            name = raw["name"]
            existing = (
                pgdb.query(SqlDefinitionModel)
                .filter(SqlDefinitionModel.name == name, SqlDefinitionModel.db_alias == db)
                .first()
            )
            if existing:
                skipped += 1
                continue

            params = {}
            for pname, pdef in (raw.get("params") or {}).items():
                params[pname] = {
                    "type": pdef.get("type", "string"),
                    "required": pdef.get("required", False),
                    "description": pdef.get("description", ""),
                }

            row = SqlDefinitionModel(
                name=name,
                db_alias=db,
                group_name=raw.get("group_name", ""),
                description=raw.get("description", ""),
                result_description=raw.get("result_description", ""),
                sql=raw["sql"].strip(),
                permission=raw.get("permission", f"query:{name}"),
                params=params,
                order_by=raw.get("order_by") or [],
            )
            pgdb.add(row)
            imported += 1
        except Exception as e:
            errors.append(f"Item {i}: {e}")

    pgdb.commit()
    sql_registry.reload_from_db()
    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.post("/{db}/queries/definitions", response_model=SqlDefinitionOut, status_code=201)
def create_definition(
    request: Request,
    body: SqlDefinitionCreate,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Create a new SQL definition for this database. Superuser only."""
    _require_superuser(current_user)
    record_audit(request, "definition", body.name, params=body.model_dump(mode="json"))

    existing = (
        pgdb.query(SqlDefinitionModel)
        .filter(SqlDefinitionModel.name == body.name, SqlDefinitionModel.db_alias == db)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Definition '{body.name}' already exists for database '{db}'")

    permission = body.permission or f"query:{body.name}"
    params = {k: v.model_dump() for k, v in body.params.items()} if body.params else {}
    order_by = [o.model_dump() for o in body.order_by] if body.order_by else []

    row = SqlDefinitionModel(
        name=body.name,
        db_alias=db,
        group_name=body.group_name,
        description=body.description,
        result_description=body.result_description,
        sql=body.sql.strip(),
        permission=permission,
        params=params,
        order_by=order_by,
    )
    pgdb.add(row)
    pgdb.commit()
    pgdb.refresh(row)
    sql_registry.reload_from_db()
    return _row_to_out(row)


@router.get("/{db}/queries/definitions/{name}", response_model=SqlDefinitionOut)
def get_definition(
    name: str,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Get a single SQL definition with full details. Superuser only."""
    _require_superuser(current_user)

    row = (
        pgdb.query(SqlDefinitionModel)
        .filter(SqlDefinitionModel.name == name, SqlDefinitionModel.db_alias == db)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found for database '{db}'")
    return _row_to_out(row)


@router.put("/{db}/queries/definitions/{name}", response_model=SqlDefinitionOut)
def update_definition(
    request: Request,
    name: str,
    body: SqlDefinitionUpdate,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Update an existing SQL definition. Superuser only."""
    _require_superuser(current_user)
    record_audit(request, "definition", name, params=body.model_dump(mode="json", exclude_none=True))

    row = (
        pgdb.query(SqlDefinitionModel)
        .filter(SqlDefinitionModel.name == name, SqlDefinitionModel.db_alias == db)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found for database '{db}'")

    if body.group_name is not None:
        row.group_name = body.group_name
    if body.result_description is not None:
        row.result_description = body.result_description
    if body.description is not None:
        row.description = body.description
    if body.sql is not None:
        row.sql = body.sql.strip()
    if body.permission is not None:
        row.permission = body.permission
    if body.params is not None:
        row.params = {k: v.model_dump() for k, v in body.params.items()}
    if body.order_by is not None:
        row.order_by = [o.model_dump() for o in body.order_by]

    pgdb.commit()
    pgdb.refresh(row)
    sql_registry.reload_from_db()
    return _row_to_out(row)


@router.delete("/{db}/queries/definitions/{name}")
def delete_definition(
    request: Request,
    name: str,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
    pgdb: Session = Depends(get_db),
):
    """Delete a SQL definition. Superuser only."""
    _require_superuser(current_user)
    record_audit(request, "definition", name)

    row = (
        pgdb.query(SqlDefinitionModel)
        .filter(SqlDefinitionModel.name == name, SqlDefinitionModel.db_alias == db)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found for database '{db}'")

    pgdb.delete(row)
    pgdb.commit()
    sql_registry.reload_from_db()
    return {"message": f"Definition '{name}' deleted from database '{db}'"}


# ── Query execution (database-specific) ──────────────────────────────────


@router.post("/{db}/queries/execute", response_model=QueryResponse)
def execute(
    request: Request,
    payload: QueryRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute a registered SQL query against the specified database.

    Supports multiple fetch modes:
    - **page**: paginated (default). Use ``page`` + ``page_size``.
    - **all**: return all rows.
    - **batch**: fixed-size batch with offset. Use ``batch_size`` + ``batch_offset``.
    - **top_n**: first N rows. Use ``top_n``.
    - **one**: exactly one row (404 if none).
    - **count**: matching row count only (``total``), no data returned.
    """
    record_audit(request, "query", payload.query_name,
                 params={"params": payload.params, "fetch_mode": payload.fetch_mode})
    definition = sql_registry.get(payload.query_name, db_alias=db)
    if not definition:
        raise HTTPException(
            status_code=404, detail=f"Query definition '{payload.query_name}' not found for database '{db}'"
        )

    # Check permission
    checker = PermissionChecker(definition.permission, "read")
    checker(current_user)

    try:
        result = execute_query(
            definition,
            payload.params,
            payload.fetch_mode,
            page=payload.page,
            page_size=payload.page_size,
            batch_size=payload.batch_size,
            batch_offset=payload.batch_offset,
            top_n=payload.top_n,
            db_alias=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query execution error: {e}")

    # count mode returns no rows, so the matching total is the meaningful
    # volume figure for the report.
    record_audit(request, result_rows=result["total"] if payload.fetch_mode == "count" else len(result["data"]))

    return QueryResponse(
        query_name=payload.query_name,
        fetch_mode=payload.fetch_mode,
        total=result["total"],
        page=result.get("page"),
        page_size=result.get("page_size"),
        batch_offset=result.get("batch_offset"),
        batch_size=result.get("batch_size"),
        has_more=result.get("has_more"),
        columns=result["columns"],
        data=result["data"],
    )
