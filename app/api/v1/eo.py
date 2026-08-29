"""E&O metadata endpoints — the field dictionary behind the MC / Sales views.

Serves `app/services/eo_field_views.py` over HTTP so two consumers can share one
source of truth: the MCP server (which exposes it as the `eo://data-dictionary`
resource and is forbidden from importing `app/`), and anything else that needs
to turn a display name a user typed back into a database column.

This is metadata, not data: no warehouse is touched and no amounts are returned.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.middleware.audit import record_audit
from app.models.user import User
from app.services import eo_field_views
from app.services.auth_service import get_current_user

router = APIRouter(tags=["E&O"])


@router.get("/eo/field-dictionary")
def field_dictionary(
    request: Request,
    view: str | None = Query(
        default=None,
        description="Return only the fields visible in this view ('mc' or 'sales'). Omit for all fields.",
    ),
    current_user: User = Depends(get_current_user),
):
    """The db_field × MC label × Sales label mapping.

    The same column carries two display names — MC says "Obsolete All OH Amt",
    the Sales workbook says "E&O OH (All) Amt" — and users quote whichever they
    were shown. `fields[].mc_label` / `.sales_label` let a caller resolve either
    back to `db_field`.

    **Every field is listed regardless of the caller's own view**, including the
    MC-only ones. A label is not a figure: knowing that MC has a column called
    "Obsolete OPO Actual Loss Amt" discloses no amount, while hiding the name
    would stop a scoped caller understanding why a column they were shown in a
    meeting is absent from their data. What the caller may actually *read* is
    `caller_view`, which is enforced on `/queries/execute`, not here.
    """
    record_audit(request, "definition", "eo_field_dictionary", params={"view": view})

    try:
        fields = eo_field_views.dictionary_payload(view)
        effective, downgraded = eo_field_views.effective_view(current_user, None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "success": True,
        "views": list(eo_field_views.VIEWS),
        "default_view": eo_field_views.DEFAULT_VIEW,
        "caller_view": effective,
        # True when the caller's permissions pin them to a view rather than
        # letting them choose (i.e. they hold eo:read:scoped).
        "caller_view_forced": downgraded,
        "governed_definitions": sorted(eo_field_views.GOVERNED_DEFINITIONS),
        "field_count": len(fields),
        "fields": fields,
    }
