"""Per-request audit: one structured log line + one api_hub_audit_logs row.

Audit writes are best-effort: any failure is logged and swallowed so it can
never affect the response.
"""

import json
import logging
import time

from fastapi import Request, Response
from starlette.background import BackgroundTask, BackgroundTasks
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog
from app.services.auth_service import get_client_ip

logger = logging.getLogger("app.audit")

# Paths that should not generate audit rows (health checks, token issuance,
# admin static assets). Matched by suffix / prefix against the full path.
_SKIP_SUFFIXES = ("/health", "/auth/token")


def _should_skip(path: str) -> bool:
    # Audit only real API calls; exclude the Admin SPA/static, root, health,
    # and token issuance. Anchored on the API prefix so business routes that
    # merely contain "admin" in a segment are still audited.
    if settings.api_prefix not in path:
        return True
    return any(path.endswith(s) for s in _SKIP_SUFFIXES)


def record_audit(request: Request, action_type: str | None = None, action: str | None = None,
                 params=None, result_rows: int | None = None) -> None:
    """Stash usage-report detail on request.state for AuditMiddleware.

    The middleware runs after call_next, by which point the request body is
    already consumed — routers are the only place the parsed payload is
    available, so they hand it over here (the same pattern the pre-existing
    audit_action / audit_db_alias assignments use).

    Safe to call more than once per request: only non-None arguments are
    applied, so a router can record the action and params up front and add
    result_rows once the response has been built.
    """
    if action_type is not None:
        request.state.audit_action_type = action_type
    if action is not None:
        request.state.audit_action = action
    if params is not None:
        request.state.audit_params = params
    if result_rows is not None:
        request.state.audit_result_rows = result_rows


def _safe_params(value):
    """Render recorded params as something JSONB can store.

    Audit writes are best-effort and must never surface, so a payload json
    cannot encode degrades to its repr rather than raising out of
    build_audit_row.
    """
    if value is None:
        return None
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return {"_unserializable": repr(value)}
    return value


def build_audit_row(request: Request, response: Response, duration_ms: int) -> dict:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        p_type, p_name, key_id, key_name = "anonymous", "anonymous", None, None
    else:
        p_type = principal.type
        p_name = principal.user.username
        key_id = principal.key_id
        key_name = principal.key_name
    return {
        "principal_type": p_type,
        "principal": p_name,
        "api_key_id": key_id,
        "api_key_name": key_name,
        "client_ip": get_client_ip(request),
        "method": request.method,
        "path": request.url.path,
        "db_alias": getattr(request.state, "audit_db_alias", None),
        "action": getattr(request.state, "audit_action", None),
        "action_type": getattr(request.state, "audit_action_type", None),
        "params": _safe_params(getattr(request.state, "audit_params", None)),
        "result_rows": getattr(request.state, "audit_result_rows", None),
        "status_code": response.status_code,
        "duration_ms": duration_ms,
    }


def write_audit(row: dict) -> None:
    """Persist one audit row. Best-effort: any failure is logged and swallowed
    so it can never surface — including when run as a background task after
    the response has already been sent to the client."""
    db = SessionLocal()
    try:
        db.add(AuditLog(**row))
        db.commit()
    except Exception:  # noqa: BLE001 — audit writes are best-effort
        logger.exception("audit write failed")
    finally:
        db.close()


def _safe_write_audit(row: dict) -> None:
    """BackgroundTask target: an extra swallow layer around write_audit so a
    background failure (including one from a monkeypatched/replaced
    write_audit) can never surface to the client — the response has already
    been sent by the time this runs."""
    try:
        write_audit(row)
    except Exception:  # noqa: BLE001 — audit writes are best-effort
        logger.exception("audit background task failed")


def _chain_background(existing, task: BackgroundTask):
    """Attach `task` without clobbering a background task a route may have set."""
    if existing is None:
        return task
    tasks = BackgroundTasks()
    tasks.add_task(existing)
    tasks.add_task(task)
    return tasks


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.time()
        response = await call_next(request)
        if _should_skip(request.url.path):
            return response
        try:
            row = build_audit_row(request, response, int((time.time() - start) * 1000))
            logger.info(
                "[AUDIT] principal=%s type=%s key=%s ip=%s %s %s db=%s action=%s status=%s dur=%sms",
                row["principal"],
                row["principal_type"],
                row["api_key_name"],
                row["client_ip"],
                row["method"],
                row["path"],
                row["db_alias"],
                row["action"],
                row["status_code"],
                row["duration_ms"],
            )
            response.background = _chain_background(response.background, BackgroundTask(_safe_write_audit, row))
        except Exception:  # noqa: BLE001 — audit must never break the request
            logger.exception("audit row build failed")
        return response
