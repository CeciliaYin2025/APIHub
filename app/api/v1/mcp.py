"""Read-only view of the MCP tool catalog.

Reports what the MCP server derives from the registered SQL definitions; it
configures nothing. The MCP server's own settings live in ``MCP_*`` environment
variables in a separate process and are not writable from here.
"""

from fastapi import APIRouter, Depends, Request

from app.middleware.audit import record_audit
from app.models.user import User
from app.services.auth_service import get_current_active_superuser, get_current_user
from app.services.mcp_catalog_service import build_tool_view
from app.services.mcp_catalog_version import catalog_version

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/tools")
def list_mcp_tools(request: Request, _admin: User = Depends(get_current_active_superuser)):
    """List every SQL definition with the MCP tool name it derives.

    Superuser-only: it spans every database alias, so it would otherwise let a
    caller enumerate definitions they hold no ``query:<name>`` permission for.
    """
    record_audit(request, action_type="report", action="mcp_tools")
    return build_tool_view()


@router.get("/catalog-version")
def get_catalog_version(_user: User = Depends(get_current_user)):
    """An opaque hash that changes whenever the derived MCP tool catalog would.

    Lets the MCP server check its cached tool list for staleness with one small
    request instead of refetching every definition on every alias.

    Any authenticated caller, unlike ``/tools`` — under OAuth the MCP server
    proxies as whoever is calling, so a superuser gate would make this unusable
    for exactly the process it exists for. It is safe to open: the response is a
    single digest, naming no definition and revealing no count.

    Deliberately unaudited. It is polled on a timer, so recording it would bury
    the audit trail in noise while telling nobody anything.
    """
    return {"version": catalog_version()}
