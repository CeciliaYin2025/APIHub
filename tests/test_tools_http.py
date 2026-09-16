"""HTTP tests for the flat per-definition tool endpoints (app/api/v1/tools.py).

These exist for LLM platforms that import tools from OpenAPI (Dify). Two things
matter: the document must be importable the way Dify reads it — one operation
per definition, every argument a top-level body property — and the call path
must enforce the same boundary as the MCP tools, since a model is the caller.
Execution itself is faked at ``queries.execute_query``; the real router,
permission check and view projection run.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import databases as databases_api
from app.api.v1 import queries as queries_api
from app.api.v1 import tools as tools_api
from app.core.config import settings
from app.models.user import Permission, Role, User
from app.services.auth_service import get_current_user
from app.services.sql_registry import ParamDef, SqlDefinition


def _definitions():
    return {
        "eo_summary": SqlDefinition(
            name="eo_summary",
            description="E&O summary.",
            result_description="73 columns.",
            sql="",
            permission="query:eo_summary",
            db_alias="DW",
            group_name="eo",
            params={
                "frozen_date": ParamDef(name="frozen_date", type="date", required=True, description="Frozen date."),
                "ou": ParamDef(name="ou", description="Site filter."),
            },
        ),
        "eo_detail": SqlDefinition(
            name="eo_detail",
            description="",
            sql="",
            permission="query:eo_detail",
            db_alias="DW",
            group_name="eo",
            params={"frozen_date": ParamDef(name="frozen_date", type="date", required=True)},
        ),
        "hr_emps": SqlDefinition(
            name="hr_emps", description="", sql="", permission="query:hr_emps", db_alias="DW", group_name="hr"
        ),
        "paged_thing": SqlDefinition(
            name="paged_thing",
            description="",
            sql="",
            permission="query:paged_thing",
            db_alias="DW",
            params={"page": ParamDef(name="page", type="number")},
        ),
    }


COLUMNS = ["OU", "OBSOLETE_OH_ALL_AMT_TWD", "OH_unrecoverable"]
ROW = {"OU": "SP", "OBSOLETE_OH_ALL_AMT_TWD": 10, "OH_unrecoverable": 3}


class _Registry:
    def __init__(self):
        self.defs = _definitions()

    def get(self, name, db_alias=None):
        return self.defs.get(name)

    def list_all(self, db_alias=None, include_disabled=False):
        return list(self.defs.values())


def _client(monkeypatch, user):
    calls = []

    def fake_execute_query(definition, params, fetch_mode, **kwargs):
        calls.append({"name": definition.name, "params": params, "fetch_mode": fetch_mode, **kwargs})
        return {"columns": list(COLUMNS), "data": [dict(ROW)], "total": 1, "page": 1, "page_size": 50}

    registry = _Registry()
    monkeypatch.setattr(tools_api, "sql_registry", registry)
    monkeypatch.setattr(queries_api, "sql_registry", registry)
    monkeypatch.setattr(queries_api, "execute_query", fake_execute_query)
    monkeypatch.setattr(queries_api, "record_audit", lambda *a, **k: None)
    monkeypatch.setattr(settings, "apihub_public_url", "https://hub.example/apihub")

    application = FastAPI()
    application.include_router(tools_api.router)
    application.dependency_overrides[get_current_user] = lambda: user
    application.dependency_overrides[databases_api.get_db_alias] = lambda: "DW"
    return TestClient(application), calls


def _user(*resources, superuser=False):
    role = Role(name="r", permissions=[Permission(resource=r, action="read") for r in resources])
    return User(username="u", is_active=True, is_superuser=superuser, roles=[role])


EO_READER = _user("query:eo_*")


# ── the OpenAPI document ─────────────────────────────────────────────────────


def _doc(monkeypatch, user=EO_READER, **query):
    client, _ = _client(monkeypatch, user)
    resp = client.get("/DW/tools/openapi.json", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_lists_only_definitions_the_caller_may_run(monkeypatch):
    doc = _doc(monkeypatch)
    assert sorted(doc["paths"]) == ["/DW/tools/eo_detail", "/DW/tools/eo_summary"]


def test_group_narrows_the_document(monkeypatch):
    doc = _doc(monkeypatch, _user(superuser=True), group="hr")
    assert list(doc["paths"]) == ["/DW/tools/hr_emps"]


def test_server_url_is_the_public_api_base(monkeypatch):
    assert _doc(monkeypatch)["servers"] == [{"url": "https://hub.example/apihub/api/v1"}]


def test_operation_ids_are_the_mcp_tool_names(monkeypatch):
    # The E&O skills refer to tools as query_dw_eo_*; keeping the names means the
    # same prompt works on Dify and over MCP.
    op = _doc(monkeypatch)["paths"]["/DW/tools/eo_summary"]["post"]
    assert op["operationId"] == "query_dw_eo_summary"
    assert op["description"] == "[DW · eo] E&O summary.\nReturns: 73 columns."


def test_arguments_are_flat_top_level_properties(monkeypatch):
    # Dify reads only the top-level properties of a request body; anything
    # nested (the /queries/execute `params` object) cannot be filled in.
    schema = _doc(monkeypatch)["paths"]["/DW/tools/eo_summary"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    props = schema["properties"]
    assert set(props) == {"frozen_date", "ou", "fetch_mode", "view", "columns", "order_by", "page", "page_size"}
    assert schema["required"] == ["frozen_date"]
    assert props["frozen_date"]["format"] == "date"
    assert "YYYY-MM-DD" in props["frozen_date"]["description"]
    assert props["fetch_mode"]["enum"] == ["page", "count", "one"]
    assert props["page_size"]["maximum"] == settings.tool_page_size_max
    assert "default" not in props["view"], "a default would turn every call into an explicit view request"


def test_columns_is_offered_as_an_optional_array(monkeypatch):
    # The point of the parameter: a page of 89-column rows costs ~258 KB, so a
    # model that can ask for five columns keeps the answer inside its window.
    schema = _doc(monkeypatch)["paths"]["/DW/tools/eo_detail"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    prop = schema["properties"]["columns"]

    assert prop["type"] == "array"
    assert prop["items"] == {"type": "string"}
    assert "columns" not in schema.get("required", [])


def test_view_is_offered_only_where_a_definition_has_one(monkeypatch):
    props = _doc(monkeypatch)["paths"]["/DW/tools/eo_detail"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["properties"]
    assert "view" not in props


def test_paging_knobs_step_aside_for_a_declared_bind(monkeypatch):
    doc = _doc(monkeypatch, _user(superuser=True))
    props = doc["paths"]["/DW/tools/paged_thing"]["post"]["requestBody"]["content"]["application/json"]["schema"][
        "properties"
    ]
    assert props["page"]["type"] == "number"
    assert {"_page", "page_size"} <= set(props)


# ── calling a tool ───────────────────────────────────────────────────────────


def test_flat_body_becomes_bind_params(monkeypatch):
    client, calls = _client(monkeypatch, EO_READER)
    resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "page": 2, "page_size": 20})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == [ROW]
    assert calls == [
        {
            "name": "eo_detail",
            "params": {"frozen_date": "2026-08-01"},
            "fetch_mode": "page",
            "page": 2,
            "page_size": 20,
            "batch_size": 500,
            "batch_offset": 0,
            "top_n": 10,
            "db_alias": "DW",
            # Empty, not absent: the knob is always passed through, and an empty
            # list means "keep the definition's own ordering".
            "order_by": [],
        }
    ]


def test_order_by_is_forwarded_to_the_query(monkeypatch):
    client, calls = _client(monkeypatch, EO_READER)
    resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "order_by": ["-OU"]})

    assert resp.status_code == 200
    assert calls[0]["order_by"] == [{"column": "OU", "direction": "DESC"}]


def test_order_by_is_listed_in_the_schema_as_an_array(monkeypatch):
    schema = _doc(monkeypatch)["paths"]["/DW/tools/eo_detail"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    prop = schema["properties"]["order_by"]

    assert prop["type"] == "array"
    assert prop["items"] == {"type": "string"}
    assert "compare_date" in prop["description"], "the description must name the workaround it replaces"


def test_columns_projects_the_response(monkeypatch):
    client, _ = _client(monkeypatch, EO_READER)
    body = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "columns": ["OU"]}).json()

    assert body["columns"] == ["OU"]
    assert body["data"] == [{"OU": "SP"}]


def test_columns_accepts_a_comma_separated_string(monkeypatch):
    # Models send "A, B" for an array often enough that rejecting it would spend
    # a tool call on formatting instead of on the question.
    client, _ = _client(monkeypatch, EO_READER)
    body = client.post(
        "/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "columns": "OU, OH_unrecoverable"}
    ).json()

    assert body["columns"] == ["OU", "OH_unrecoverable"]


def test_unknown_column_is_refused(monkeypatch):
    client, _ = _client(monkeypatch, EO_READER)
    resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "columns": ["NOPE"]})

    assert resp.status_code == 400
    assert "NOPE" in resp.json()["detail"]


def test_columns_is_listed_among_the_accepted_arguments(monkeypatch):
    client, _ = _client(monkeypatch, EO_READER)
    resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "bogus": 1})

    assert resp.status_code == 400
    assert "columns" in resp.json()["detail"]


def test_page_size_is_capped(monkeypatch):
    client, calls = _client(monkeypatch, EO_READER)
    client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "page_size": 1000})
    assert calls[0]["page_size"] == settings.tool_page_size_max


def test_bulk_fetch_modes_are_refused(monkeypatch):
    client, calls = _client(monkeypatch, EO_READER)
    for mode in ("all", "batch"):
        resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "fetch_mode": mode})
        assert resp.status_code == 400
    assert calls == []


def test_undeclared_argument_is_refused_with_the_accepted_list(monkeypatch):
    client, calls = _client(monkeypatch, EO_READER)
    resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "site": "SP"})
    assert resp.status_code == 400
    assert "site" in resp.json()["detail"] and "frozen_date" in resp.json()["detail"]
    assert calls == []


def test_null_arguments_are_treated_as_omitted(monkeypatch):
    client, calls = _client(monkeypatch, EO_READER)
    resp = client.post("/DW/tools/eo_summary", json={"frozen_date": "2026-08-01", "ou": None, "page": None})
    assert resp.status_code == 200
    assert calls[0]["params"] == {"frozen_date": "2026-08-01"}
    assert calls[0]["page"] == 1


def test_non_integer_page_is_a_400(monkeypatch):
    client, _ = _client(monkeypatch, EO_READER)
    resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "page": "two"})
    assert resp.status_code == 400


def test_sales_view_is_applied_on_the_governed_definition(monkeypatch):
    client, _ = _client(monkeypatch, EO_READER)
    body = client.post("/DW/tools/eo_summary", json={"frozen_date": "2026-08-01", "view": "sales"}).json()
    assert body["view"] == "sales"
    assert "OH_unrecoverable" not in body["columns"]


def test_view_is_dropped_on_a_definition_without_one(monkeypatch):
    client, calls = _client(monkeypatch, EO_READER)
    body = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01", "view": "sales"}).json()
    assert "view" not in calls[0]["params"]
    assert body["columns"] == COLUMNS


def test_caller_without_permission_gets_403_and_no_parameter_list(monkeypatch):
    client, calls = _client(monkeypatch, _user("query:hr_*"))
    resp = client.post("/DW/tools/eo_detail", json={"bogus": 1})
    assert resp.status_code == 403
    assert "frozen_date" not in resp.text
    assert calls == []


def test_unknown_definition_is_a_404(monkeypatch):
    client, _ = _client(monkeypatch, EO_READER)
    assert client.post("/DW/tools/nope", json={}).status_code == 404


def test_disabled_definition_says_disabled(monkeypatch):
    client, _ = _client(monkeypatch, EO_READER)
    tools_api.sql_registry.defs["eo_detail"].enabled = False
    resp = client.post("/DW/tools/eo_detail", json={"frozen_date": "2026-08-01"})
    assert resp.status_code == 404
    assert "disabled" in resp.json()["detail"]
