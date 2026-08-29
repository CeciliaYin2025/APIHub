"""GET /api/v1/eo/field-dictionary — the metadata the MCP resource proxies."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import eo as eo_api
from app.models.user import Permission, Role, User
from app.services import eo_field_views
from app.services.auth_service import get_current_user


def _client(user):
    application = FastAPI()
    application.include_router(eo_api.router)
    application.dependency_overrides[get_current_user] = lambda: user
    return TestClient(application)


def _user(*perms):
    role = Role(name="r", permissions=[Permission(resource=r, action=a) for r, a in perms])
    return User(username="u", is_active=True, roles=[role])


def test_returns_every_field_with_both_labels():
    body = _client(_user()).get("/eo/field-dictionary").json()

    assert body["success"] is True
    assert body["views"] == ["mc", "sales"]
    assert body["field_count"] == len(eo_field_views.FIELD_DICTIONARY)

    entry = next(f for f in body["fields"] if f["db_field"] == "OBSOLETE_OH_ALL_AMT_TWD")
    assert entry["mc_label"] == "Obsolete All OH Amt"
    assert entry["sales_label"] == "E&O OH (All) Amt"
    assert entry["mc_only"] is False


def test_scoped_caller_still_sees_the_mc_only_names():
    """A label is not a figure. Hiding the name would leave a scoped caller
    unable to understand why a column from the meeting deck is missing."""
    body = _client(_user(("eo:read:scoped", "read"))).get("/eo/field-dictionary").json()

    names = {f["db_field"] for f in body["fields"]}
    assert "OH_unrecoverable" in names
    assert body["caller_view"] == "sales"
    assert body["caller_view_forced"] is True


def test_unscoped_caller_reports_the_mc_view():
    body = _client(_user(("query:eo_summary", "read"))).get("/eo/field-dictionary").json()

    assert body["caller_view"] == "mc"
    assert body["caller_view_forced"] is False


def test_view_filter_narrows_the_listing():
    body = _client(_user()).get("/eo/field-dictionary", params={"view": "sales"}).json()

    assert body["field_count"] == len(eo_field_views.columns_for_view("sales"))
    assert all(f["mc_only"] is False for f in body["fields"])
    # Every Sales field is nameable except the plumbing ones (CURRENCY_CODE,
    # TWD_CURRENCY_RATE), which neither report prints.
    assert all(f["sales_label"] or f["group"] == "plumbing" for f in body["fields"])


def test_sales_listing_includes_the_currency_plumbing():
    body = _client(_user()).get("/eo/field-dictionary", params={"view": "sales"}).json()

    plumbing = {f["db_field"] for f in body["fields"] if f["group"] == "plumbing"}
    assert plumbing == {"CURRENCY_CODE", "TWD_CURRENCY_RATE"}
    # They must explain themselves — a nameless column with no note is unreadable.
    assert all(f["note"] for f in body["fields"] if f["group"] == "plumbing")


def test_bad_view_filter_is_a_400():
    response = _client(_user()).get("/eo/field-dictionary", params={"view": "finance"})

    assert response.status_code == 400
    assert "Unknown view" in response.json()["detail"]


def test_reports_which_definitions_have_views():
    body = _client(_user()).get("/eo/field-dictionary").json()
    assert body["governed_definitions"] == ["eo_summary"]
