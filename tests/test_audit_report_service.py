"""Usage report query building and pure helpers.

No live PostgreSQL is available to `pytest` (tests/e2e is excluded via
norecursedirs), so these tests exercise the parsing/validation helpers
directly and assert on the compiled SQL of the built statements. The
numeric correctness of the aggregation is covered by
tests/e2e/usage-report.spec.js against a real Postgres.
"""

from datetime import date, datetime

import pytest
from sqlalchemy.dialects import postgresql

import app.services.audit_report_service as svc


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect())).replace("\n", " ")


# ── resolve_date_range ───────────────────────────────────────────────────

def test_date_range_covers_whole_end_day():
    ts_from, ts_to = svc.resolve_date_range(date(2026, 7, 1), date(2026, 7, 29))
    assert ts_from == datetime(2026, 7, 1, 0, 0, 0)
    assert ts_to.date() == date(2026, 7, 29)
    assert (ts_to.hour, ts_to.minute, ts_to.second) == (23, 59, 59)


def test_date_range_defaults_to_last_30_days_inclusive():
    ts_from, ts_to = svc.resolve_date_range(None, date(2026, 7, 30))
    assert ts_from.date() == date(2026, 7, 1)  # 30 days including the 30th


def test_date_range_rejects_inverted_range():
    with pytest.raises(ValueError, match="date_from"):
        svc.resolve_date_range(date(2026, 7, 30), date(2026, 7, 1))


# ── parse_group_by ───────────────────────────────────────────────────────

def test_group_by_defaults_to_user_db_api():
    assert svc.parse_group_by(None) == ["user", "db", "api"]


def test_group_by_empty_string_means_totals_only():
    assert svc.parse_group_by("") == []


def test_group_by_dedupes_and_preserves_order():
    assert svc.parse_group_by("day, user ,day") == ["day", "user"]


def test_group_by_rejects_unknown_token():
    with pytest.raises(ValueError, match="ip"):
        svc.parse_group_by("user,ip")


# ── parse_action_types ───────────────────────────────────────────────────

def test_action_types_default_excludes_definition_and_report():
    assert svc.parse_action_types(None) == ["query", "procedure", "write"]


def test_action_types_empty_string_means_no_filter():
    assert svc.parse_action_types("") == []


def test_action_types_rejects_unknown_value():
    with pytest.raises(ValueError, match="login"):
        svc.parse_action_types("query,login")


# ── parse_sort ───────────────────────────────────────────────────────────

def test_sort_defaults_to_calls_descending():
    assert svc.parse_sort(None) == ("calls", True)


def test_sort_ascending_without_minus():
    assert svc.parse_sort("avg_duration_ms") == ("avg_duration_ms", False)


def test_sort_rejects_unknown_column():
    with pytest.raises(ValueError, match="principal"):
        svc.parse_sort("-principal")


# ── build_filters ────────────────────────────────────────────────────────

def test_filters_always_bound_the_time_window():
    clauses = svc.build_filters(datetime(2026, 7, 1), datetime(2026, 7, 29, 23, 59, 59))
    assert len(clauses) == 2


def test_filters_add_one_clause_per_supplied_dimension():
    clauses = svc.build_filters(
        datetime(2026, 7, 1), datetime(2026, 7, 29),
        principal="alice", db_alias="DW1", action="get_orders",
        action_types=["query"], status_code=500, min_duration_ms=100,
    )
    assert len(clauses) == 8


def test_empty_action_types_adds_no_clause():
    clauses = svc.build_filters(datetime(2026, 7, 1), datetime(2026, 7, 2), action_types=[])
    assert len(clauses) == 2


# ── build_usage_statement ────────────────────────────────────────────────

def _stmt(group_by=("user", "db", "api"), sort_column="calls", sort_desc=True, page=1, page_size=50):
    clauses = svc.build_filters(datetime(2026, 7, 1), datetime(2026, 7, 29))
    return svc.build_usage_statement(group_by=list(group_by), clauses=clauses,
                                     sort_column=sort_column, sort_desc=sort_desc,
                                     page=page, page_size=page_size)


def test_statement_groups_by_the_selected_dimensions():
    sql = _sql(_stmt(group_by=("user", "day")))
    assert "GROUP BY api_hub_audit_logs.principal, date(api_hub_audit_logs.ts)" in sql
    # unselected dimensions must not leak into the grouping
    assert "db_alias" not in sql.split("GROUP BY")[1]


def test_statement_splits_success_and_failure_with_filtered_counts():
    sql = _sql(_stmt())
    assert "FILTER (WHERE api_hub_audit_logs.status_code < " in sql
    assert "FILTER (WHERE api_hub_audit_logs.status_code >= " in sql


def test_statement_sorts_and_paginates():
    sql = _sql(_stmt(sort_column="avg_duration_ms", sort_desc=False, page=3, page_size=25))
    assert "ORDER BY avg_duration_ms" in sql
    assert "DESC" not in sql.split("ORDER BY")[1]
    assert "LIMIT" in sql and "OFFSET" in sql


def test_statement_sorts_descending_when_requested():
    sql = _sql(_stmt(sort_column="last_used_at", sort_desc=True))
    assert "ORDER BY last_used_at DESC" in sql


# ── _log_to_dict ─────────────────────────────────────────────────────────

class _Row:
    """Stands in for an AuditLog ORM row."""

    def __init__(self, params):
        self.id = 7
        self.ts = datetime(2026, 7, 30, 10, 12, 33)
        self.principal = "alice"
        self.principal_type = "user"
        self.api_key_name = None
        self.client_ip = "10.1.2.3"
        self.method = "POST"
        self.path = "/api/v1/dw1/queries/execute"
        self.db_alias = "DW1"
        self.action_type = "query"
        self.action = "get_orders"
        self.status_code = 200
        self.duration_ms = 145
        self.result_rows = 12
        self.params = params


def test_small_params_are_inlined():
    item = svc._log_to_dict(_Row({"dept": "IT"}), inline_limit=4096)
    assert item["params"] == {"dept": "IT"}
    assert item["params_omitted"] is False
    assert item["params_bytes"] > 0


def test_oversized_params_are_omitted_with_their_size():
    big = {"rows": [{"v": "x" * 100} for _ in range(200)]}
    item = svc._log_to_dict(_Row(big), inline_limit=4096)
    assert item["params"] is None
    assert item["params_omitted"] is True
    assert item["params_bytes"] > 4096


def test_no_inline_limit_always_returns_full_params():
    big = {"rows": [{"v": "x" * 100} for _ in range(200)]}
    item = svc._log_to_dict(_Row(big), inline_limit=None)
    assert item["params"] == big
    assert item["params_omitted"] is False


def test_null_params_stay_null():
    item = svc._log_to_dict(_Row(None), inline_limit=4096)
    assert item["params"] is None
    assert item["params_omitted"] is False
    assert item["params_bytes"] is None
