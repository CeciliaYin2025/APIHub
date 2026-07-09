"""Per-request audit: one structured log line + one api_hub_audit_logs row.

Audit writes are best-effort: any failure is logged and swallowed so it can
never affect the response.
"""

import logging
import time

from fastapi import Request, Response
from starlette.background import BackgroundTask, BackgroundTasks
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog

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
        "client_ip": request.client.host if request.client else "unknown",
        "method": request.method,
        "path": request.url.path,
        "db_alias": getattr(request.state, "audit_db_alias", None),
        "action": getattr(request.state, "audit_action", None),
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
