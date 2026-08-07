"""Read-only view of the MCP tool catalog.

Reports what the MCP server derives from the registered SQL definitions; it
configures nothing. The MCP server's own settings live in ``MCP_*`` environment
variables in a separate process and are not writable from here.
"""

from fastapi import APIRouter, Depends, Request

from app.middleware.audit import record_audit
from app.models.user import User
from app.services.auth_service import get_current_active_superuser
from app.services.mcp_catalog_service import build_tool_view

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/tools")
def list_mcp_tools(request: Request, _admin: User = Depends(get_current_active_superuser)):
    """List every SQL definition with the MCP tool name it derives.

    Superuser-only: it spans every database alias, so it would otherwise let a
    caller enumerate definitions they hold no ``query:<name>`` permission for.
    """
    record_audit(request, action_type="report", action="mcp_tools")
    return build_tool_view()
